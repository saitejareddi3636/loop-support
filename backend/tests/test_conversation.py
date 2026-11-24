"""Tool-free model replies are kept unless they state facts the backend cannot support."""

import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

OLD_GENERIC = "I'd be happy to help. What would you like me to check"
CONTEXT = {
    "account": {"customer_id": "cus_3001", "name": "Sam Patel", "tier": "business"},
    "references": {"selected_invoice_id": "INV-3002"},
    "refund_receipts": [
        {
            "receipt_id": "mock_e37bceb21e1f",
            "invoice_id": "INV-3002",
            "amount_cents": 6900,
            "currency": "USD",
        }
    ],
}
HISTORY = [
    HumanMessage("Tell me about invoice 3002"),
    AIMessage("Invoice INV-3002 is for $69.00 and is eligible for a mock refund."),
    AIMessage("Mock refund issued for INV-3002: $69.00 USD. Receipt: mock_e37bceb21e1f."),
]


class ScriptedModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen = []

    def bind_tools(self, tools):
        return self

    async def astream(self, messages):
        self.seen.append(messages)
        yield AIMessageChunk(content=self.replies[min(len(self.seen), len(self.replies)) - 1])


async def final_answer(model, query, context=CONTEXT, history=HISTORY):
    from app.agent.graph import build_graph

    with patch("app.agent.graph.ChatGoogleGenerativeAI", return_value=model):
        events = [
            e
            async for e in build_graph("fake", "not-persisted", "trace").astream(
                {
                    "messages": [*history, HumanMessage(query)],
                    "results": [],
                    "context": context,
                },
                stream_mode="custom",
            )
        ]
    return next(e["answer"] for e in events if e["type"] == "final_answer"), events


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_followups_about_earlier_turns_keep_the_model_answer(self):
        cases = {
            "What invoice did you refund earlier?": "Earlier we refunded INV-3002 for $69.00 USD. Your receipt is mock_e37bceb21e1f.",
            "What invoice are we discussing?": "We're discussing INV-3002, the $69.00 Business plan adjustment, which is already refunded.",
            "Yes I did confirm the invoice and amount.": "Thanks for confirming. The receipt for INV-3002 is mock_e37bceb21e1f. Anything else?",
            "So": "Did you want to look at another invoice, or ask about a policy?",
        }
        for query, reply in cases.items():
            with self.subTest(query=query):
                model = ScriptedModel(reply)
                answer, _ = await final_answer(model, query)
                self.assertEqual(answer, reply)
                self.assertNotIn(OLD_GENERIC, answer)
                self.assertEqual(len(model.seen), 1)

    async def test_receipts_from_backend_context_reach_the_model(self):
        model = ScriptedModel("We refunded INV-3002.")
        await final_answer(model, "Which invoice did we refund?", history=[])
        prompt = "\n".join(str(m.content) for m in model.seen[0])
        self.assertIn("mock_e37bceb21e1f", prompt)
        self.assertIn("refund_receipts", prompt)

    async def test_out_of_scope_request_is_declined_in_the_models_words(self):
        cases = {
            "Does Loopp cover a refund for a restaurant meal?": "I can only help with Loopp accounts, invoices, and policies, so I can't cover a restaurant meal.",
            "Can you book a flight to Denver?": "I can't book travel, but I can check your Loopp invoices or support policies.",
            "Does Loopp cover a refund for my restaurant meal?": "That isn't a Loopp purchase, so I can't help with it. I can check your Loopp invoices.",
        }
        for query, reply in cases.items():
            with self.subTest(query=query):
                answer, events = await final_answer(ScriptedModel(reply), query)
                self.assertEqual(answer, reply)
                self.assertFalse(any(e["type"] == "tool_started" for e in events))

    async def test_unsupported_policy_numbers_trigger_one_revision(self):
        model = ScriptedModel(
            "Business refunds are allowed within 30 days, up to $750.",
            "Which plan's refund policy would you like me to look up?",
        )
        answer, events = await final_answer(model, "What is the Business refund window?")
        self.assertEqual(answer, "Which plan's refund policy would you like me to look up?")
        self.assertEqual(len(model.seen), 2)
        revision = next(e for e in events if e["type"] == "answer_revision")
        self.assertIn("$750.00", revision["unsupported"])
        self.assertIn("30 day", revision["unsupported"])
        self.assertIn("Backend validation", str(model.seen[1][-1].content))

    async def test_persistent_fabrication_falls_back_without_the_old_generic_reply(self):
        from app.agent.graph import FACT_CHECK_FALLBACK

        model = ScriptedModel("INV-9999 was refunded for $12.00 under receipt mock_abcdef123456.")
        answer, _ = await final_answer(model, "What else did you refund?")
        self.assertEqual(answer, FACT_CHECK_FALLBACK)
        self.assertEqual(len(model.seen), 2)
        self.assertNotIn(OLD_GENERIC, answer)


class FactSupportTests(unittest.TestCase):
    def test_spoken_invoice_and_cent_amounts_count_as_support(self):
        from app.agent.answers import unsupported_facts

        support = (
            'User: invoice 3002\n{"amount_cents": 6900}\nPolicy: refunds within 14 calendar days'
        )
        self.assertEqual(
            unsupported_facts("INV-3002 was $69.00 and the window is 14 days.", support), []
        )
        self.assertEqual(
            unsupported_facts("INV-3005 was $89.00 within 30 days.", support),
            ["INV-3005", "$89.00", "30 day"],
        )


class CompositionTests(unittest.TestCase):
    def test_account_inquiry_keeps_model_answer_instead_of_eligibility_dump(self):
        from app.agent.answers import compose_turn_answer

        listing = {
            "name": "list_invoices",
            "result": {
                "invoices": [
                    {
                        "invoice_id": "INV-1002",
                        "amount_cents": 2900,
                        "currency": "USD",
                        "description": "Loopp Pro plan adjustment",
                        "eligible": True,
                    },
                    {
                        "invoice_id": "INV-1003",
                        "amount_cents": 1900,
                        "currency": "USD",
                        "eligible": True,
                    },
                ]
            },
        }
        draft = (
            "I checked your Pro account. Recent charges include INV-1002 for $29.00 "
            "(plan adjustment) and INV-1003 for $19.00. Which charge should we review first?"
        )
        answer = compose_turn_answer(
            draft,
            [listing],
            latest="Yeah, I think I've been charged overcharged twice recently. Can you look into my account?",
        )
        self.assertEqual(answer, draft)
        self.assertNotIn("Eligible for a mock refund", answer)

    def test_unsolicited_refund_pitch_is_stripped_from_billing_review(self):
        from app.agent.answers import compose_turn_answer

        listing = {
            "name": "list_invoices",
            "result": {
                "invoices": [
                    {
                        "invoice_id": "INV-1003",
                        "amount_cents": 1900,
                        "currency": "USD",
                        "status": "paid",
                        "date": "2026-11-15",
                        "eligible": True,
                    }
                ]
            },
        }
        draft = (
            "INV-1003 is $19.00 for a plan adjustment. "
            "Would you like me to prepare a mock refund for it?"
        )
        answer = compose_turn_answer(
            draft,
            [listing],
            latest="Can you look into my recent charges?",
        )
        self.assertIn("INV-1003", answer)
        self.assertNotIn("Would you like me to prepare a mock refund", answer)
        self.assertNotIn("Eligible for a mock refund", answer)

    def test_failed_policy_does_not_erase_invoice_review(self):
        from app.agent.answers import compose_turn_answer

        results = [
            {
                "name": "list_invoices",
                "result": {
                    "invoices": [
                        {
                            "invoice_id": "INV-2001",
                            "amount_cents": 1200,
                            "currency": "USD",
                            "status": "paid",
                            "date": "2026-11-04",
                            "description": "Loopp Starter monthly subscription (legacy plan)",
                        }
                    ]
                },
            },
            {
                "name": "search_policy",
                "result": {
                    "decision": "ESCALATION_REQUIRED",
                    "verification_reason": "No active, applicable passage met the retrieval threshold.",
                },
            },
        ]
        answer = compose_turn_answer(
            "No active, applicable passage met the retrieval threshold.",
            results,
            evidence=results[1]["result"],
            latest="I'm paying for starter but my account looks free. What's going on?",
        )
        self.assertIn("INV-2001", answer)
        self.assertIn("12", answer)
        self.assertNotIn("Eligible for a mock refund", answer)

    def test_followup_keeps_account_status_from_context(self):
        from app.agent.answers import compose_turn_answer

        support = json.dumps(
            {
                "account": {
                    "name": "Avery Reed",
                    "tier": "Free",
                    "status": "suspended",
                    "restrictions": ["billing_hold"],
                }
            }
        )
        answer = compose_turn_answer(
            "I've noted that you don't want a refund! Would you like me to create an escalation ticket?",
            [],
            support=support,
            latest="Okay — so what should I do next without refunding anything?",
        )
        self.assertIn("suspended", answer.lower())
        self.assertIn("Free", answer)
        self.assertIn("billing hold", answer.lower())

    def test_refund_request_still_uses_choice_template_when_draft_fails(self):
        from app.agent.answers import compose_turn_answer

        listing = {
            "name": "list_invoices",
            "result": {
                "invoices": [
                    {
                        "invoice_id": "INV-1002",
                        "amount_cents": 2900,
                        "currency": "USD",
                        "eligible": True,
                    },
                    {
                        "invoice_id": "INV-1003",
                        "amount_cents": 1900,
                        "currency": "USD",
                        "eligible": True,
                    },
                ]
            },
        }
        answer = compose_turn_answer(
            "Sure, I'll refund INV-9999 for $12.00.",
            [listing],
            latest="Yeah, can you go ahead and refund that?",
        )
        self.assertIn("Which invoice would you like to proceed with", answer)
        self.assertIn("INV-1002", answer)

    def test_multi_intent_keeps_question_answer_and_appends_ticket(self):
        from app.agent.answers import compose_turn_answer

        draft = (
            "Our tools show contract status and end dates, but not negotiated cancellation clauses, "
            "so I can't quote a penalty amount from policy alone."
        )
        evidence = {
            "decision": "GROUNDED",
            "sources": [
                {
                    "excerpt": "Enterprise subscriptions are governed by their recorded agreement.",
                    "citation": "enterprise-contracts:v2:c886a52784be7",
                }
            ],
        }
        results = [
            {
                "name": "search_policy",
                "result": evidence,
            },
            {
                "name": "create_escalation_ticket",
                "result": {"ticket_id": "TKT-72A2B756", "status": "open"},
            },
        ]
        answer = compose_turn_answer(
            draft,
            results,
            evidence=evidence,
            latest="Yes, and what is my negotiated cancellation penalty?",
            policy_text="Enterprise subscriptions are governed by their recorded agreement. [enterprise-contracts:v2:c886a52784be7]",
        )
        self.assertIn("negotiated cancellation", answer)
        self.assertIn("TKT-72A2B756", answer)
        self.assertNotIn("saved for human review", answer)
        self.assertNotIn("already with the team", answer)

    def test_refund_staging_still_uses_confirmation_template(self):
        from app.agent.answers import compose_turn_answer

        answer = compose_turn_answer(
            "All set, I refunded it.",
            [
                {
                    "name": "issue_mock_refund",
                    "result": {
                        "status": "confirmation_required",
                        "invoice_id": "INV-1002",
                        "amount_cents": 2900,
                        "currency": "USD",
                        "token": "tok",
                    },
                }
            ],
            latest="Please refund INV-1002.",
        )
        self.assertIn("Confirm invoice and amount", answer)
        self.assertIn("INV-1002", answer)
        self.assertNotIn("I refunded it", answer)

    def test_unsupported_contact_window_is_not_kept(self):
        from app.agent.answers import FACT_CHECK_FALLBACK, compose_turn_answer

        answer = compose_turn_answer(
            "Our team typically follows up within 24 business hours.",
            [],
            support='{"open_tickets":[]}',
            latest="When will someone contact me?",
        )
        self.assertEqual(answer, FACT_CHECK_FALLBACK)

    def test_failed_policy_mentions_existing_open_ticket(self):
        from app.agent.answers import compose_turn_answer

        answer = compose_turn_answer(
            "I can switch your plan.",
            [{"name": "search_policy", "result": {"decision": "ESCALATION_REQUIRED"}}],
            evidence={
                "decision": "ESCALATION_REQUIRED",
                "verification_reason": "No active, applicable passage met the retrieval threshold.",
            },
            support='{"open_tickets":[{"ticket_id":"TKT-9F2D7C31","status":"open"}]}',
            latest="Can you switch me back to starter?",
        )
        self.assertIn("No active, applicable passage", answer)
        self.assertIn("TKT-9F2D7C31", answer)
        self.assertNotIn("I can switch your plan", answer)
        self.assertNotIn("already with the team", answer)
        self.assertIn("does not notify a live support team", answer)

    def test_contact_followup_does_not_use_unrelated_policy_excerpt(self):
        from app.agent.answers import compose_turn_answer

        evidence = {
            "decision": "GROUNDED",
            "sources": [
                {
                    "excerpt": "If the customer asks when money will arrive, explain that this demonstration has no payment processing.",
                    "citation": "enterprise-contracts:v2:c8552f053ac81",
                }
            ],
        }
        answer = compose_turn_answer(
            "Someone will email you within 24 business hours.",
            [{"name": "search_policy", "result": evidence}],
            evidence=evidence,
            support='{"open_tickets":[{"ticket_id":"TKT-B1F5A6E5","status":"open"}]}',
            latest="When exactly will someone contact me?",
            policy_text="If the customer asks when money will arrive, explain that this demonstration has no payment processing. [enterprise-contracts:v2:c8552f053ac81]",
        )
        self.assertIn("TKT-B1F5A6E5", answer)
        self.assertNotIn("money will arrive", answer)
        self.assertNotIn("24 business hours", answer)
        self.assertNotIn("already with the team", answer)
        self.assertIn("does not notify a live support team", answer)

    def test_does_not_infer_restriction_cause_from_invoice_status(self):
        from app.agent.answers import compose_turn_answer

        support = json.dumps(
            {
                "account": {
                    "tier": "Free",
                    "status": "suspended",
                    "restrictions": ["billing_hold"],
                }
            }
        )
        answer = compose_turn_answer(
            "Your account is suspended because of an overdue unpaid invoice balance.",
            [],
            support=support,
            latest="Why is my account locked?",
        )
        self.assertIn("suspended", answer.lower())
        self.assertNotIn("because of an overdue", answer.lower())
        self.assertNotIn("unpaid invoice", answer.lower())

    def test_failed_policy_preserves_account_from_context(self):
        from app.agent.answers import compose_turn_answer

        support = json.dumps(
            {
                "account": {
                    "name": "Avery Reed",
                    "tier": "Free",
                    "status": "suspended",
                    "restrictions": ["billing_hold"],
                }
            }
        )
        answer = compose_turn_answer(
            "No active, applicable passage met the retrieval threshold.",
            [
                {
                    "name": "search_policy",
                    "result": {
                        "decision": "ESCALATION_REQUIRED",
                        "verification_reason": "No active, applicable passage met the retrieval threshold.",
                    },
                }
            ],
            evidence={
                "decision": "ESCALATION_REQUIRED",
                "verification_reason": "No active, applicable passage met the retrieval threshold.",
            },
            support=support,
            latest="Why is my account suspended and what should I do next?",
        )
        self.assertIn("suspended", answer.lower())
        self.assertIn("Free", answer)
        self.assertIn("billing hold", answer.lower())


if __name__ == "__main__":
    unittest.main()
