import unittest
from app.identifiers import supplied_email, invoice_ids, selected_invoice_ids
from app.agent.conversation import social_reply
from app.agent.answers import refund_reply, invoice_list_reply


class IdentifierTests(unittest.TestCase):
    def test_actual_spoken_invoice_variants(self):
        for query in [
            "Yes, invoice 3002.",
            "INV 3002",
            "INV3002",
            "invoice three zero zero two",
            "invoice 3 0 0 2",
        ]:
            with self.subTest(query=query):
                self.assertEqual(invoice_ids(query), {"INV-3002"})
        self.assertEqual(invoice_ids("INV-D20261124-1"), {"INV-D20261124-1"})
        self.assertEqual(invoice_ids("INV-3002 and INV-3005"), {"INV-3002", "INV-3005"})

    def test_short_choices_use_only_the_listed_order(self):
        refs = {"eligible_invoice_ids": ["INV-D20261124-1", "INV-3002"]}
        for query in [
            "the second one",
            "option two",
            "option 2",
            "2",
            "yes, the second one please",
        ]:
            with self.subTest(query=query):
                self.assertEqual(selected_invoice_ids(query, refs), {"INV-3002"})
        self.assertEqual(selected_invoice_ids("3002", refs), {"INV-3002"})
        for query in [
            "invoice D2026",
            "first explain the policy",
            "69 dollars",
            "third",
            "the first one and second one",
        ]:
            with self.subTest(query=query):
                self.assertEqual(selected_invoice_ids(query, refs), set())
        self.assertEqual(selected_invoice_ids("the first one", {}), set())

    def test_email_spelling_preserves_exact_letters(self):
        for query in [
            "It's sam.patel@example.test.",
            "sam dot patel at example dot test",
            "s a m dot p a t e l at e x a m p l e dot t e s t",
        ]:
            with self.subTest(query=query):
                self.assertEqual(supplied_email(query), "sam.patel@example.test")
        self.assertEqual(supplied_email("sam.patil@example.test"), "sam.patil@example.test")
        self.assertIsNone(supplied_email("I'm Sam. Sam Patil"))
        self.assertIsNone(supplied_email("sam.patel@example.test or alex.morgan@example.test"))

    def test_repeated_name_leads_to_email_spelling_not_lookup(self):
        greeting = social_reply("Can you hear me?", [], {})
        first = social_reply("I'm Sam. Sam Patil", [{"role": "assistant", "content": greeting}], {})
        self.assertIn("email", first)
        repeated = social_reply("I'm Sam. Sam Patil", [{"role": "assistant", "content": first}], {})
        self.assertIn("spell", repeated)

    def test_refund_copy_points_to_the_screen_button(self):
        result = {
            "status": "confirmation_required",
            "invoice_id": "INV-3002",
            "amount_cents": 6900,
            "currency": "USD",
        }
        reply = refund_reply(result)
        self.assertIn("Click 'Confirm invoice and amount'", reply)
        self.assertIn("screen", reply)
        self.assertIn("spoken agreement cannot confirm", reply)
