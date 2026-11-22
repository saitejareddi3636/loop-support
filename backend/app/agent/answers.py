"""Concise presentation of backend action results, shared by voice and chat."""


def refund_reply(result, policy_basis=''):
    if result.get('status')=='no_eligible_invoices':return invoice_list_reply(result)
    if result.get('status')=='invoice_choice_required':
        choices=', '.join(f"{i['invoice_id']} (${i['amount_cents']/100:.2f} {i['currency']})" for i in result['invoices'])
        return f'Which invoice would you like to proceed with: {choices}?'
    if result.get('status')=='already_refunded':
        return f"{result['invoice_id']} already has a ${result['amount_cents']/100:.2f} {result['currency']} mock refund. Receipt: `{result['receipt_id']}`."
    if result.get('status')=='confirmation_required':
        amount=result['amount_cents']/100
        lead=f"**{result['invoice_id']} · ${amount:.2f} {result['currency']}** passed the mock refund checks."
        action='Confirm the invoice and amount below to issue the mock receipt. No refund has been issued.'
        answer=lead+'\n\n'+action
    else:
        answer=result.get('error','I could not stage a mock refund. Please check the invoice details.')
        if result.get('receipt_id'):
            return answer+' Receipt: `'+result['receipt_id']+'`.'
    if policy_basis:
        answer+='\n\n'+policy_basis
    return answer


def invoice_list_reply(result):
    if result.get('error'):return result['error']
    items=result.get('invoices',[])
    if not items:return 'I could not find any invoices on your account.'
    eligible=[i for i in items if i.get('eligible')]
    if eligible:
        rows='; '.join(f"{i['invoice_id']} (${i['amount_cents']/100:.2f} {i['currency']})" for i in eligible)
        return f"I checked your invoices. Eligible for a mock refund: {rows}. Which invoice would you like to proceed with?" if len(eligible)>1 else f"I checked your invoices. {rows} passed the mock refund checks. Would you like me to prepare its confirmation?"
    refunded=[i['invoice_id'] for i in items if i.get('refund_status')=='already_refunded']
    remaining=[f"{i['invoice_id']}: {i.get('eligibility_reason','not eligible')}" for i in items if i.get('refund_status')!='already_refunded']
    answer='I checked your invoices. None currently qualify for another automatic mock refund.'
    if refunded:answer+=' '+', '.join(refunded)+' already have mock refund receipts.'
    if remaining:answer+=' '+' '.join(remaining[:3])
    return answer
