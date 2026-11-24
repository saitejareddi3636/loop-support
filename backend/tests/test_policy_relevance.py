"""Mocked relevance decisions: no network or database required."""

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.policy import MIN_SIMILARITY, search, validate_evidence
from app.policy_relevance import assess_relevance


def row(similarity=0.75, version=2):
    return dict(
        chunk_id=f"billing:v{version}",
        document_id="billing",
        title="Loopp billing",
        document_type="policy",
        version=version,
        tiers=["Business"],
        effective_date=date(2026, 11, 1),
        expiration_date=None,
        section="Refunds",
        source_reference="fixture",
        similarity=similarity,
        content="Paid Loopp Business monthly invoices have a refund window.",
    )


class PolicyRelevanceTests(unittest.TestCase):
    def retrieve(self, rows, verdict):
        database = MagicMock()
        database.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = (
            rows
        )
        with (
            patch("app.policy.db", database),
            patch("app.policy.embed", return_value=[1.0]),
            patch("app.policy.assess_relevance", return_value=verdict) as verify,
        ):
            result = search("Does Loopp cover a refund for my restaurant bill?", "Business")
        return result, verify

    def test_yes_only_retains_existing_evidence_and_threshold(self):
        result, verify = self.retrieve([row()], {"verdict": "YES"})
        self.assertEqual(result["decision"], "GROUNDED")
        self.assertEqual(result["retrieval_decision"], "GROUNDED")
        self.assertEqual(result["threshold"], MIN_SIMILARITY)
        self.assertEqual(MIN_SIMILARITY, 0.68)
        self.assertEqual(result["sources"][0]["excerpt"], row()["content"])
        self.assertEqual(result["active_policy_versions"], {"billing": 2})
        self.assertEqual(
            verify.call_args.args[:2],
            ("Does Loopp cover a refund for my restaurant bill?", "Business"),
        )

    def test_no_discards_high_similarity_evidence(self):
        result, _ = self.retrieve([row(0.95)], {"verdict": "NO"})
        self.assertEqual(result["decision"], "ESCALATION_REQUIRED")
        self.assertEqual(result["retrieval_decision"], "GROUNDED")
        self.assertEqual(result["sources"], [])
        self.assertFalse(result["evidence_sufficient"])
        self.assertEqual(result["top_similarity"], 0.95)
        self.assertFalse(validate_evidence(result))
        # Changing only the decision cannot bypass the negative verification.
        result["decision"] = "GROUNDED"
        self.assertFalse(validate_evidence(result))

    def test_unavailable_or_unknown_verdict_fails_closed(self):
        for verdict in ({"verdict": "UNAVAILABLE"}, {"verdict": "maybe"}, {}):
            with self.subTest(verdict=verdict):
                result, _ = self.retrieve([row()], verdict)
                self.assertEqual(result["decision"], "ESCALATION_REQUIRED")
                self.assertFalse(result["sources"])
                self.assertIn("unavailable", result["verification_reason"])

    def test_low_similarity_conflicts_and_clarifications_never_reach_verifier(self):
        for rows in ([row(0.67)], [row(version=1), row(version=2)]):
            result, verify = self.retrieve(rows, {"verdict": "YES"})
            self.assertEqual(result["decision"], "ESCALATION_REQUIRED")
            verify.assert_not_called()
        with patch("app.policy.embed") as embed, patch("app.policy.assess_relevance") as verify:
            self.assertEqual(search("hello")["decision"], "NEEDS_CLARIFICATION")
            self.assertEqual(
                search("What is the refund policy?")["decision"], "NEEDS_CLARIFICATION"
            )
            embed.assert_not_called()
            verify.assert_not_called()

    def test_model_output_is_strict_and_call_is_read_only_and_bounded(self):
        for text, expected in [
            (' {"answer":"YES"}', "YES"),
            ('{"answer":"NO"}', "NO"),
            ('{"answer":true}', "UNAVAILABLE"),
            ('{"answer":"YES","issue_refund":true}', "UNAVAILABLE"),
            ("YES", "UNAVAILABLE"),
            ("", "UNAVAILABLE"),
        ]:
            with self.subTest(text=text), patch("app.policy_relevance.genai.Client") as client:
                client.return_value.models.generate_content.return_value = SimpleNamespace(
                    text=text, usage_metadata=None
                )
                result = assess_relevance(
                    "What is the refund window?",
                    "Business",
                    [{"title": "Billing", "excerpt": "Source text"}],
                )
                self.assertEqual(result["verdict"], expected)
                self.assertIsNone(result["usage"])
                self.assertIsNone(result["estimated_cost_usd"])
                options = client.call_args.kwargs["http_options"]
                self.assertEqual(options.timeout, 10000)
                self.assertEqual(options.retry_options.attempts, 1)
                config = client.return_value.models.generate_content.call_args.kwargs["config"]
                self.assertIsNone(config.tools)
                client.return_value.close.assert_called_once()

    def test_model_error_fails_closed_and_does_not_replay(self):
        with patch("app.policy_relevance.genai.Client") as client:
            client.return_value.models.generate_content.side_effect = TimeoutError()
            result = assess_relevance(
                "Refund my restaurant bill",
                "Business",
                [{"title": "Billing", "excerpt": "Source text"}],
            )
            self.assertEqual(result["verdict"], "UNAVAILABLE")
            client.return_value.models.generate_content.assert_called_once()
            client.return_value.close.assert_called_once()
