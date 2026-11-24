"""Dynamic LangGraph loop with bounded recovery and deterministic policy output."""

import asyncio
import json
import operator
import os
import re
import time
from typing import Annotated
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_chunk_to_message,
)
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph

from app.agent.answers import compose_turn_answer, unsupported_facts
from app.data import account_by_customer
from app.identifiers import (
    partial_invoice_reference,
    selected_invoice_ids,
    supplied_email,
)
from app.metrics import PRICING_SOURCE, text_cost
from app.policy import decision, grounded_answer, validate_evidence
from app.resilience import RetryLater, classify, retry_delay
from app.storage import conversation_context, offer_escalation, user_invoice_ids, verified_customer
from app.tools.support import tools_for_chat

SYSTEM_PROMPT = """You are Loopp, a warm, concise general customer support assistant for a fictional demo—not a refund-only bot.
Greet people naturally. Ask for name and account email when needed, one step at a time. A greeting never requires policy evidence.
The backend conversation context already has the verified account, invoice references, refund_receipts, and open_tickets. Do not ask for the email again when an account is present. Never invent customer, invoice, receipt, or ticket IDs.
When account tier, subscription status, billing cycle, or contract dates are already present in that context, you may answer those fields directly. Call get_account when the context account is missing or the customer asks you to re-check the live record.
Questions about a locked, suspended, restricted, or held account should lead with the context (or get_account) status, tier, and restriction labels only. Do not infer that an overdue, disputed, unpaid, or other invoice status caused the restriction unless a retrieved policy or tool result explicitly says so. Do not pivot to refunds.
If identity was just verified, continue the customer's earlier unresolved concern from history immediately. Do not reset with a generic “how can I help” when that concern is already known.
When the customer asks about a completed mock refund or receipt, prefer the persisted refund_receipts in context over restaging or guessing.
Demo tickets record a request only; never promise that a human will follow up or that a live team is reviewing the ticket.

Choose tools by the customer's actual intent (capabilities are separate):
- verify_customer — identity only.
- get_account — account/tier/subscription/contract status, including suspension and billing holds.
- get_invoice — a named invoice's amount, date, status, or description.
- list_invoices — recent charges or invoice inventory for review. Report amounts, dates, and status. Do not turn an ordinary billing review into a refund offer.
- search_policy — plan rules, troubleshooting, contracts, cancellation, login, disputes. Pass a complete question.
- issue_mock_refund — only when the customer clearly asks to refund, prepare, stage, or check personal refund eligibility.
- create_escalation_ticket — only with clear authorization after explaining the issue. “What should I do next” or “without refunding” is not ticket authorization; explain options and ask first.

Never volunteer refund eligibility, refund options, confirmation cards, or “would you like a refund?” after verification or ordinary account/billing questions. A general policy never proves personal eligibility.
One turn can contain several asks. Complete each authorized action and answer the remaining questions in the same reply. If an open ticket already exists in context, reuse its ticket_id; do not offer to open another unless the customer asks. Questions about contact timing or ticket status should use that open ticket; do not answer them with unrelated policy excerpts.
When the customer does ask to proceed with a refund, issue_mock_refund stages a known invoice; if several qualify, ask which one. The separate confirmation button is always required. Spoken yes cannot issue a receipt.
Scope: Loopp accounts, invoices, mock refunds, policies, and tickets only. Decline unrelated requests briefly without calling search_policy, and without repeating the same sentence.
Answer from tool results, history, and backend context in natural concise language. Preserve important IDs and amounts. Never state policy numbers, invoice amounts, or receipts from memory."""

FACT_CHECK_FALLBACK = "I can't verify that from your account records or Loopp's support policies yet. Tell me which invoice or policy you'd like me to check, and I'll look it up."


class AgentState(MessagesState):
    results: Annotated[list[dict], operator.add]
    context: dict


def text_content(message):
    c = message.content
    return c if isinstance(c, str) else "".join(p.get("text", "") for p in c if isinstance(p, dict))


def support_text(state: AgentState, messages):
    return "\n".join(
        [json.dumps(state.get("context", {}), default=str)]
        + [text_content(m) for m in messages if not isinstance(m, SystemMessage)]
    )


def prepare_model_input(state: AgentState, writer):
    results = state.get("results", [])
    policies = [
        r["result"] for r in results if r["name"] == "search_policy" and "decision" in r["result"]
    ]
    latest = next((text_content(m) for m in reversed(state["messages"]) if m.type == "human"), "")
    policy_intent = bool(
        re.search(
            r"polic(?:y|ies)|refund|contract|cancel|login|activation|trial|limit|upgrade|downgrade|suspend|reactivat|correction|dispute|payment fail",
            latest,
            re.IGNORECASE,
        )
    )
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        AIMessage(
            content="Backend conversation context: "
            + json.dumps(state.get("context", {}), default=str)
        ),
        *state["messages"],
    ]
    if any(r["name"] == "verify_customer" and r["result"].get("verified") for r in results):
        messages.append(
            HumanMessage(
                content=(
                    "Backend note, not from the customer: identity was just verified. "
                    "Continue their earlier unresolved request from the conversation history. "
                    "Do not ask a generic how-can-I-help unless no prior concern exists, "
                    "and do not offer refunds unless they asked for one."
                )
            )
        )
    writer(
        {
            "type": "prompt_trace",
            "node": "agent",
            "messages": [
                {
                    "role": m.type,
                    "content": text_content(m),
                    "tool_calls": getattr(m, "tool_calls", []),
                }
                for m in messages
            ],
        }
    )
    return results, policies, latest, policy_intent, messages


def required_invoice_call(state: AgentState, results, latest, delivered, writer, draft=""):
    # A refund action, or a reply about the customer's own refund eligibility, requires fresh
    # owned invoice evidence. A scope decline or a policy answer does not.
    claims_eligibility = bool(
        re.search(
            r"eligib|qualif|refundable|can (?:get|receive|have) (?:a |the )?(?:full |mock )?refund",
            draft,
            re.IGNORECASE,
        )
    )
    personal_refund = bool(
        re.search(r"refund", latest, re.IGNORECASE)
        and (
            claims_eligibility
            or re.search(r"\b(go ahead|give|is+ue|prepare|stage|process|proceed)\b", latest, re.IGNORECASE)
        )
        and not any(r["name"] == "search_policy" for r in results)
    )
    invoice_checked = any(
        r["name"] == "issue_mock_refund"
        or (r["name"] in ("list_invoices", "get_invoice") and not r["result"].get("error"))
        for r in results
    )
    if personal_refund and not invoice_checked and state.get("context", {}).get("account"):
        if delivered:
            writer({"type": "answer_reset"})
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "list_invoices",
                            "args": {},
                            "id": "required_" + uuid4().hex,
                        }
                    ],
                )
            ]
        }
    requested_action = bool(
        re.search(
            r"go ahead|\bis+ue\b.{0,30}refund|give.{0,30}refund|(?:prepare|stage).{0,60}\brefund\b|refund\s+(?:it|that|this|INV-)",
            latest,
            re.IGNORECASE,
        )
    )
    has_stage = any(r["name"] == "issue_mock_refund" for r in results)
    listed = next(
        (r["result"] for r in reversed(results) if r["name"] == "list_invoices"),
        None,
    )
    eligible = [item for item in (listed or {}).get("invoices", []) if item.get("eligible")]
    if not listed:
        invoice = next(
            (
                r["result"]
                for r in reversed(results)
                if r["name"] == "get_invoice" and not r["result"].get("error")
            ),
            None,
        )
        requested_ids = user_invoice_ids(latest)
        eligible = (
            [invoice]
            if invoice
            and invoice.get("eligible")
            and (not requested_ids or invoice["invoice_id"] in requested_ids)
            else []
        )
    if requested_action and not has_stage and len(eligible) == 1:
        if delivered:
            writer({"type": "answer_reset"})
        return {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "issue_mock_refund",
                            "args": {"invoice_id": eligible[0]["invoice_id"]},
                            "id": "requested_" + uuid4().hex,
                        }
                    ],
                )
            ]
        }
    return None


def build_graph(api_key, chat_id, trace_id):
    tools = tools_for_chat(chat_id)
    by_name = {t.name: t for t in tools}
    simulated = False
    model = ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash-lite"),
        google_api_key=api_key,
        timeout=40,
        max_retries=0,
    ).bind_tools(tools)

    async def call_model(state: AgentState):
        writer = get_stream_writer()
        results, policies, latest, policy_intent, messages = prepare_model_input(state, writer)
        references = state.get("context", {}).get("references", {})
        if not results:
            selected = selected_invoice_ids(latest, references)
            if references.get("awaiting_refund_invoice_choice") and partial_invoice_reference(
                latest
            ):
                return {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "list_invoices",
                                    "args": {},
                                    "id": "clarify_" + uuid4().hex,
                                }
                            ],
                        )
                    ]
                }
            if references.get("awaiting_refund_invoice_choice") and len(selected) == 1:
                return {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "issue_mock_refund",
                                    "args": {"invoice_id": next(iter(selected))},
                                    "id": "selected_" + uuid4().hex,
                                }
                            ],
                        )
                    ]
                }
            if not state.get("context", {}).get("account") and supplied_email(latest):
                return {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "verify_customer",
                                    "args": {"email": supplied_email(latest)},
                                    "id": "identity_" + uuid4().hex,
                                }
                            ],
                        )
                    ]
                }
        deadline = time.monotonic() + float(os.getenv("MODEL_REQUEST_BUDGET_SECONDS", "45"))
        attempt = 0
        revised = False
        support = support_text(state, messages)
        while True:
            started = time.perf_counter()
            aggregate = None
            delivered = False
            try:
                async with asyncio.timeout(max(0.01, deadline - time.monotonic())):
                    async for chunk in model.astream(messages):
                        aggregate = chunk if aggregate is None else aggregate + chunk
                        text = text_content(chunk)
                        if (
                            text
                            and results
                            and not policies
                            and not policy_intent
                            and not any("error" in r["result"] for r in results)
                        ):
                            delivered = True
                            writer({"type": "delta", "text": text, "stream_origin": "model"})
                if aggregate is None:
                    raise RuntimeError("Empty provider response")
                reply = message_chunk_to_message(aggregate)
                usage = getattr(reply, "usage_metadata", None)
                cost = text_cost(os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash-lite"), usage)
                writer(
                    {
                        "type": "model_completed",
                        "duration_ms": round((time.perf_counter() - started) * 1000),
                        "usage": usage,
                        "estimated_cost_usd": cost,
                        "cost_scope": "text_only_estimate",
                        "pricing_source": PRICING_SOURCE
                        if os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash-lite")
                        == "gemini-3.5-flash-lite"
                        else None,
                        "model": os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash-lite"),
                    }
                )
                if reply.tool_calls:
                    if delivered:
                        writer({"type": "answer_reset"})
                else:
                    answer = text_content(reply)
                    required = required_invoice_call(
                        state, results, latest, delivered, writer, answer
                    )
                    if required is not None:
                        return required
                    evidence = None
                    policy_text = ""
                    if policies:
                        evidence = policies[-1]
                        customer = await asyncio.to_thread(verified_customer, chat_id)
                        account = (
                            await asyncio.to_thread(account_by_customer, customer)
                            if customer
                            else None
                        )
                        tier_changed = bool(
                            account
                            and evidence.get("applicable_tier") not in (None, account["tier"])
                        )
                        if tier_changed:
                            evidence = decision(
                                "ESCALATION_REQUIRED",
                                "The account tier changed while retrieving policy evidence. Please retry the lookup.",
                                account["tier"],
                            )
                        if evidence["decision"] == "GROUNDED" and not await asyncio.to_thread(
                            validate_evidence, evidence
                        ):
                            evidence = decision(
                                "ESCALATION_REQUIRED",
                                "The retrieved evidence changed, conflicts, or is no longer applicable.",
                                evidence.get("applicable_tier"),
                                top=evidence.get("top_similarity"),
                            )
                            writer({"type": "grounding", "result": evidence})
                            await asyncio.to_thread(
                                offer_escalation, chat_id, evidence["verification_reason"]
                            )
                            writer(
                                {
                                    "type": "escalation_offered",
                                    "message": "Would you like a human support ticket?",
                                }
                            )
                        refunds = [r["result"] for r in results if r["name"] == "issue_mock_refund"]
                        policy_text = await asyncio.to_thread(
                            grounded_answer, evidence, latest, 1 if refunds else 3
                        )
                    elif not results:
                        issues = unsupported_facts(answer, support)
                        if issues and not revised:
                            revised = True
                            writer({"type": "answer_revision", "unsupported": issues})
                            messages = [
                                *messages,
                                AIMessage(content=answer),
                                HumanMessage(
                                    content="Backend validation, not from the customer: your draft stated "
                                    + ", ".join(issues)
                                    + ", which is not in the conversation, backend context or any tool result. Call the appropriate tool to verify it, or reply without it."
                                ),
                            ]
                            continue
                        if issues or not answer.strip():
                            answer = FACT_CHECK_FALLBACK
                    if results or evidence is not None:
                        answer = compose_turn_answer(
                            answer,
                            results,
                            evidence=evidence,
                            support=support,
                            latest=latest,
                            policy_text=policy_text,
                        )
                    if not answer.strip():
                        answer = FACT_CHECK_FALLBACK
                    writer({"type": "final_answer", "answer": answer})
                return {"messages": [reply]}
            except Exception as error:
                category, retry_after, retryable = classify(error)
                if delivered or not retryable:
                    raise
                if attempt == 2:
                    raise RetryLater(category, retry_after) from error
                delay = retry_delay(error, attempt, deadline - time.monotonic())
                writer(
                    {
                        "type": "retry_scheduled",
                        "attempt": attempt + 1,
                        "delay_ms": round(delay * 1000),
                        "category": category,
                        "message": "The provider is temporarily unavailable; retrying before any output or action.",
                    }
                )
                await asyncio.sleep(delay)
                attempt += 1

    async def call_tools(state: AgentState):
        nonlocal simulated
        writer = get_stream_writer()
        output = []
        results = []
        for call in state["messages"][-1].tool_calls:
            name = call["name"]
            started = time.perf_counter()
            tool_id = call["id"]
            writer({"type": "tool_started", "name": name, "tool_id": tool_id})
            try:
                if (
                    os.getenv("APP_ENV", "local") == "local"
                    and os.getenv("DEMO_FAIL_TOOL") == name
                    and not simulated
                ):
                    simulated = True
                    raise RuntimeError("Controlled local-only tool failure")
                if any(
                    r["name"] == name and r["result"].get("error_category")
                    for r in state.get("results", [])
                ):
                    raise RuntimeError("Permanent tool failure already reported this turn")
                async with asyncio.timeout(65):
                    result = await by_name[name].ainvoke(call["args"])
                result = json.loads(json.dumps(result, default=str))
            except Exception as error:
                category, _, _ = classify(error)
                result = {
                    "error": error.user_message
                    if isinstance(error, RetryLater)
                    else "The tool is unavailable. I can offer human assistance.",
                    "error_category": category,
                    "outcome": "error",
                }
                if isinstance(error, RetryLater):
                    result.update(retry_later=True, retry_after_seconds=error.retry_after_seconds)
            verification = result.get("relevance_verification") if name == "search_policy" else None
            if verification:
                writer(
                    {
                        "type": "model_completed"
                        if verification.get("attempted", True)
                        else "policy_verification",
                        "node": "policy_relevance",
                        **verification,
                    }
                )
            result.setdefault(
                "outcome",
                "error"
                if result.get("error_category")
                else "blocked"
                if result.get("error")
                else "success",
            )
            failed = result["outcome"] == "error" or bool(result.get("error_category"))
            blocked = bool(result.get("error")) and not failed
            writer(
                {
                    "type": "tool_failed"
                    if failed
                    else "tool_blocked"
                    if blocked
                    else "tool_completed",
                    "name": name,
                    "tool_id": tool_id,
                    "result": result,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                }
            )
            if result.get("decision"):
                writer({"type": "grounding", "result": result})
            if (
                (result.get("error_category") and not result.get("retry_later"))
                or result.get("decision") == "ESCALATION_REQUIRED"
                or (name == "issue_mock_refund" and blocked and "review" in result.get("error", ""))
            ):
                reason = result.get("verification_reason") or result.get("error")
                escalation = await asyncio.to_thread(
                    offer_escalation, chat_id, reason, result.get("sources", [])
                )
                writer(
                    {
                        "type": "escalation_offered",
                        "message": "Would you like a human support ticket?",
                        "result": escalation,
                    }
                )
            results.append({"name": name, "result": result})
            output.append(ToolMessage(content=json.dumps(result), tool_call_id=tool_id, name=name))
        return {
            "messages": output,
            "results": results,
            "context": await asyncio.to_thread(conversation_context, chat_id),
        }

    def route(state: AgentState):
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    graph = StateGraph(AgentState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", call_tools)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()
