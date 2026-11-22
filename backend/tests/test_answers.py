"""Small presentation regression checks; facts remain owned by backend tools."""
import unittest
from app.agent.answers import refund_reply
from app.policy import selected_passages

class AnswerTests(unittest.TestCase):
    def test_refund_leads_with_returned_invoice_and_amount(self):
        result={'status':'confirmation_required','invoice_id':'INV-EXAMPLE','amount_cents':12345,'currency':'USD'}
        reply=refund_reply(result,'Exact retrieved policy sentence. [retrieved-source]')
        self.assertTrue(reply.startswith('**INV-EXAMPLE · $123.45 USD**'))
        self.assertIn('No refund has been issued',reply)
        self.assertLess(reply.index('Confirm the invoice'),reply.index('Exact retrieved'))

    def test_failed_check_never_claims_eligibility(self):
        reply=refund_reply({'error':'Invoice belongs to another customer.'})
        self.assertNotIn('passed',reply)
        self.assertNotIn('Confirm',reply)

    def test_refund_evidence_prefers_actual_tier_rule_and_keeps_exact_text(self):
        general='Free accounts have no automatic paid subscription refund entitlement.'
        rule='A paid Business monthly invoice can receive one full mock refund within fourteen calendar days.'
        result={'applicable_tier':'Business','sources':[
            {'tiers':['all'],'excerpt':'## Scope\n'+general,'citation':'general'},
            {'tiers':['Business'],'excerpt':'## Refunds\n'+rule,'citation':'business'}]}
        selected=selected_passages(result,'Please refund my invoice',1)
        self.assertEqual(selected[0][0],rule)
        self.assertEqual(selected[0][1]['citation'],'business')

    def test_existing_receipt_is_reported_without_restaging(self):
        reply=refund_reply({'error':'This invoice already has a mock refund receipt.','receipt_id':'mock_existing'},'Unneeded policy basis')
        self.assertIn('mock_existing',reply)
        self.assertNotIn('Unneeded',reply)
        self.assertNotIn('Confirm',reply)

    def test_structured_existing_receipt_is_a_successful_lookup(self):
        reply=refund_reply({'status':'already_refunded','receipt_id':'mock_saved','invoice_id':'INV-SAVED','amount_cents':6900,'currency':'USD'},'Unneeded policy basis')
        self.assertIn('INV-SAVED already has a $69.00 USD mock refund',reply)
        self.assertIn('mock_saved',reply)
        self.assertNotIn('Unneeded',reply)
        self.assertNotIn('Confirm',reply)

    def test_greeting_and_name_do_not_require_policy_evidence(self):
        from app.agent.conversation import social_reply
        greeting=social_reply('Hey, can you hear me?',[],{})
        self.assertIn('what should I call you',greeting)
        self.assertNotIn('policy evidence',greeting)
        reply=social_reply('Sam',[{'role':'assistant','content':greeting}],{})
        self.assertIn('Nice to meet you, Sam',reply)
        self.assertIn('email',reply)

    def test_no_eligible_invoice_reply_does_not_claim_personal_entitlement(self):
        from app.agent.answers import invoice_list_reply
        result={'invoices':[{'invoice_id':'INV-A','eligible':False,'refund_status':'already_refunded'},{'invoice_id':'INV-B','eligible':False,'refund_status':'ineligible','eligibility_reason':'Only paid invoices are eligible.'}]}
        reply=invoice_list_reply(result)
        self.assertIn('None currently qualify',reply)
        self.assertIn('INV-A already have mock refund receipts',reply)
        self.assertIn('INV-B',reply)
