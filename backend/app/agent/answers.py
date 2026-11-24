"""Compose customer-facing answers from model drafts and backend tool facts."""

import json
import re

from app.identifiers import invoice_ids

MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
POLICY_TERM = re.compile(
    r"(?i)\b(\d+)\s*(?:calendar\s+|business\s+)?(day|hour|week|month|year|%|percent)"
)
RECEIPT = re.compile(r"\bmock_[0-9a-f]{6,}\b")
TICKET = re.compile(r"\bTKT[- ]?[0-9A-F]+\b", re.IGNORECASE)
FACT_CHECK_FALLBACK = "I can't verify that from your account records or Loopp's support policies yet. Tell me which invoice or policy you'd like me to check, and I'll look it up."


def money_cents(text):
    cents = {round(float(v.replace(",", "")) * 100) for v in MONEY.findall(text)}
    cents |= {int(v) for v in re.findall(r'"amount_cents":\s*(\d+)', text)}
    return cents


def policy_terms(text):
    return {
        (int(n), "%" if unit.lower() == "percent" else unit.lower())
        for n, unit in POLICY_TERM.findall(text)
    }


def unsupported_facts(answer, support):
    """Facts in a reply must already exist in history, tool output or backend context."""
    issues = [i for i in invoice_ids(answer) if i not in support and i not in invoice_ids(support)]
    issues += [r for r in RECEIPT.findall(answer) if r not in support]
    known_cents = money_cents(support)
    issues += [f"${c / 100:.2f}" for c in money_cents(answer) if c not in known_cents]
    known_terms = policy_terms(support)
    issues += [f"{n} {u}" for n, u in policy_terms(answer) if (n, u) not in known_terms]
    return issues


def refund_reply(result, policy_basis=""):
    if result.get("status") == "no_eligible_invoices":
        return invoice_list_reply(result)
    if result.get("status") == "invoice_choice_required":
        choices = ", ".join(
            f"Option {number}: {i['invoice_id']} (${i['amount_cents'] / 100:.2f} {i['currency']})"
            for number, i in enumerate(result["invoices"], 1)
        )
        return f"Which invoice would you like to proceed with: {choices}? Choose a card on screen, or say 'the first one' or 'the second one'."
    if result.get("status") == "already_refunded":
        return f"{result['invoice_id']} already has a ${result['amount_cents'] / 100:.2f} {result['currency']} mock refund. Receipt: `{result['receipt_id']}`."
    if result.get("status") == "confirmation_required":
        amount = result["amount_cents"] / 100
        lead = f"**{result['invoice_id']} · ${amount:.2f} {result['currency']}** passed the mock refund checks."
        action = "I've opened the confirmation card on your screen. Click 'Confirm invoice and amount' there to issue the mock receipt. No refund has been issued; spoken agreement cannot confirm it."
        answer = lead + "\n\n" + action
    else:
        answer = result.get(
            "error", "I could not stage a mock refund. Please check the invoice details."
        )
        if result.get("receipt_id"):
            return answer + " Receipt: `" + result["receipt_id"] + "`."
    if policy_basis:
        answer += "\n\n" + policy_basis
    return answer


def invoice_list_reply(result):
    if result.get("error"):
        return result["error"]
    items = result.get("invoices", [])
    if not items:
        return "I could not find any invoices on your account."
    eligible = [i for i in items if i.get("eligible")]
    if eligible:
        rows = "; ".join(
            f"Option {number}: {i['invoice_id']} (${i['amount_cents'] / 100:.2f} {i['currency']})"
            for number, i in enumerate(eligible, 1)
        )
        return (
            f"I checked your invoices. Eligible for a mock refund: {rows}. Which invoice would you like to proceed with? Choose a card, or say the first or second one."
            if len(eligible) > 1
            else f"I checked your invoices. {rows} passed the mock refund checks. Would you like me to prepare its confirmation?"
        )
    refunded = [i["invoice_id"] for i in items if i.get("refund_status") == "already_refunded"]
    remaining = [
        f"{i['invoice_id']}: {i.get('eligibility_reason', 'not eligible')}"
        for i in items
        if i.get("refund_status") != "already_refunded"
    ]
    answer = "I checked your invoices. None currently qualify for another automatic mock refund."
    if refunded:
        answer += " " + ", ".join(refunded) + " already have mock refund receipts."
    if remaining:
        answer += " " + " ".join(remaining[:3])
    return answer


def policy_support(evidence):
    sources = evidence.get("sources") or []
    return "\n".join(
        f"{s.get('excerpt', '')} {s.get('citation', '')} {s.get('text', '')}" for s in sources
    )


def results_support(results):
    return "\n".join(json.dumps(r.get("result", {}), default=str) for r in results)


def wants_refund_choice(latest):
    """True only when the customer clearly asks to refund or check personal eligibility."""
    return bool(
        re.search(
            r"(?i)(?:"
            r"(?:which|what|any).{0,40}\b(?:refund|eligible|eligibility)\b|"
            r"\b(?:eligible|eligibility|refundable)\b.{0,40}\binvoice|"
            r"\b(?:go ahead|process|prepare|stage|iss+ue|issue|proceed|give).{0,40}\brefund\b|"
            r"\brefund\b.{0,40}\b(?:it|that|this|invoice|me|my|an?|the)\b|"
            r"\b(?:mock\s+)?refund\b.{0,20}\b(?:for|on)\b|"
            r"\b(?:the|a|my)\s+(?:mock\s+)?refund\b"
            r")",
            latest,
        )
    )


UNSOLICITED_REFUND = re.compile(
    r"(?i)(?:eligible for a mock refund|would you like me to (?:prepare|stage|process|go ahead with).{0,40}refund|"
    r"would you like (?:a |me to (?:prepare a )?|to )?(?:mock )?refund|passed the mock refund checks|"
    r"prepare its confirmation)"
)

ACCOUNT_STATUS_ASK = re.compile(
    r"(?i)\b(?:locked|lock(?:ed)? out|suspend|suspended|suspension|billing hold|restriction|"
    r"what happened|why (?:is|was)|account status|what should i do|next step)\b"
)
RESTRICTION_CAUSE_INFER = re.compile(
    r"(?i)(?:^|[.!?]\s*)([^.!?\n]*(?:because|due to|caused by|as a result of|from (?:an?|your|the))"
    r"[^.!?\n]*(?:overdue|unpaid|past[- ]due|disputed|outstanding|invoice|balance|missed payment)"
    r"[^.!?\n]*[.!?]?\s*)"
)


def ticket_recorded_note(ticket_id, *, created=False):
    """Demo tickets are persisted records only — never promise staffed follow-up."""
    if created:
        return (
            f"Ticket {ticket_id} is open. This demo ticket records your request; "
            "it does not notify a live support team or promise follow-up."
        )
    return (
        f"Your open support ticket {ticket_id} is recorded in this demo as open. "
        "It does not notify a live support team or promise follow-up."
    )


def _account_from_support(support):
    """Pull tier/status from backend context or get_account tool JSON in the support blob."""
    if not support:
        return None
    tier = status = restrictions = None
    for match in re.finditer(
        r'"tier":\s*"([^"]+)".{0,240}?"status":\s*"([^"]+)"', support, re.DOTALL
    ):
        tier, status = match.group(1), match.group(2)
    if not tier:
        for match in re.finditer(
            r'"status":\s*"([^"]+)".{0,240}?"tier":\s*"([^"]+)"', support, re.DOTALL
        ):
            status, tier = match.group(1), match.group(2)
    restr = re.search(r'"restrictions":\s*\[(.*?)\]', support, re.DOTALL)
    if restr:
        restrictions = re.findall(r'"([^"]+)"', restr.group(1))
    if not (tier or status):
        return None
    return {"tier": tier, "status": status, "restrictions": restrictions or []}


def _strip_restriction_cause_inference(answer, latest=""):
    """Drop claims that invoice payment status caused a restriction without evidence."""
    if not answer:
        return answer
    if not (
        ACCOUNT_STATUS_ASK.search(latest or "")
        or re.search(r"(?i)\bsuspend|billing hold|restriction|locked\b", answer)
    ):
        return answer
    cleaned = RESTRICTION_CAUSE_INFER.sub(" ", answer)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip(" \n")
    # If the whole reply was an unsupported causal claim, drop it so grounding can restate facts.
    return cleaned


def _ensure_account_grounding(answer, support, latest):
    """Keep account status visible on lock/suspension follow-ups instead of drifting to refunds."""
    answer = _strip_restriction_cause_inference(answer, latest)
    if not ACCOUNT_STATUS_ASK.search(latest or ""):
        return answer
    account = _account_from_support(support)
    if not account:
        return answer
    lower = (answer or "").lower()
    bits = []
    if account.get("status") and account["status"].lower() not in lower:
        bits.append(f"status {account['status']}")
    if account.get("tier") and account["tier"].lower() not in lower:
        bits.append(f"tier {account['tier']}")
    for label in account.get("restrictions") or []:
        pretty = label.replace("_", " ")
        if pretty.lower() not in lower and label.lower() not in lower:
            bits.append(pretty)
    if not bits:
        return answer
    lead = "Your account currently shows " + ", ".join(bits) + "."
    return f"{lead} {answer}".strip() if answer else lead


def _factual_billing_summary(accounts, invoices, listings):
    parts = []
    if accounts:
        account = accounts[-1]
        tier = account.get("tier") or account.get("subscription", {}).get("tier")
        status = account.get("status") or account.get("subscription", {}).get("status")
        name = account.get("name")
        bit = "I checked your account"
        if name:
            bit += f" for {name}"
        details = ", ".join(
            x for x in [tier and f"tier {tier}", status and f"status {status}"] if x
        )
        if details:
            bit += f" ({details})"
        parts.append(bit + ".")
    source = None
    if listings:
        source = listings[-1].get("invoices") or []
    elif invoices:
        source = []
        for item in invoices:
            if item.get("invoices"):
                source.extend(item["invoices"])
            elif item.get("invoice_id"):
                source.append(item)
    if source:
        rows = []
        for item in source[:6]:
            amount = item.get("amount_cents")
            money = f"${amount / 100:.2f}" if isinstance(amount, int) else ""
            rows.append(
                f"{item.get('invoice_id')} {money} {item.get('status') or ''} "
                f"{item.get('date') or ''}".strip()
            )
        if rows:
            parts.append("Recent invoices: " + "; ".join(rows) + ".")
    parts.append("Tell me which charge or detail you want to review next.")
    return " ".join(parts)


def compose_turn_answer(draft, results, evidence=None, support="", latest="", policy_text=""):
    """Keep a grounded model draft. Use templates only for hard financial and failure cases."""
    failures = [r for r in results if r["result"].get("error_category")]
    if failures:
        deferred = next((r["result"] for r in failures if r["result"].get("retry_later")), None)
        return (
            deferred["error"]
            if deferred
            else "A support tool is unavailable, so I cannot verify that answer or complete the request. Would you like a human support ticket?"
        )

    identity_errors = [
        r["result"]
        for r in results
        if r["name"] == "verify_customer" and r["result"].get("needs_email")
    ]
    if identity_errors:
        return identity_errors[-1]["error"]

    refunds = [r["result"] for r in results if r["name"] == "issue_mock_refund"]
    if refunds:
        basis = policy_text if evidence and evidence.get("decision") == "GROUNDED" else ""
        return refund_reply(refunds[-1], basis)

    if evidence is not None and evidence.get("decision") != "GROUNDED":
        reason = evidence.get("verification_reason") or policy_text
        existing = TICKET.findall(support) + [
            r["result"]["ticket_id"]
            for r in results
            if r["name"] == "create_escalation_ticket" and r["result"].get("ticket_id")
        ]
        ticket_note = f" {ticket_recorded_note(existing[0])}" if existing else ""
        accounts = [r["result"] for r in results if r["name"] == "get_account"]
        ctx_account = _account_from_support(support)
        if ctx_account and not accounts:
            accounts = [ctx_account]
        listings = [r["result"] for r in results if r["name"] == "list_invoices"]
        invoices = [
            r["result"]
            for r in results
            if r["name"] == "get_invoice" and not r["result"].get("error")
        ]
        if (accounts or listings or invoices) and not wants_refund_choice(latest):
            summary = _factual_billing_summary(accounts, invoices, listings)
            if reason:
                summary += (
                    " I could not verify an additional policy passage for that follow-up. "
                    "Would you like a human support ticket?"
                )
            return _ensure_account_grounding(summary + ticket_note, support, latest)
        return _ensure_account_grounding((reason or "") + ticket_note, support, latest)

    support_blob = "\n".join(
        part for part in (support, results_support(results), policy_support(evidence or {})) if part
    )
    answer = (draft or "").strip()
    listings = [r["result"] for r in results if r["name"] == "list_invoices"]
    if listings and wants_refund_choice(latest):
        eligible_ids = {
            i["invoice_id"] for i in listings[-1].get("invoices", []) if i.get("eligible")
        }
        if (
            not answer
            or unsupported_facts(answer, support_blob)
            or not any(invoice_id in answer for invoice_id in eligible_ids)
        ):
            return invoice_list_reply(listings[-1])

    if answer and not unsupported_facts(answer, support_blob):
        if not wants_refund_choice(latest) and UNSOLICITED_REFUND.search(answer):
            cleaned = UNSOLICITED_REFUND.sub("", answer)
            cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip(" \n")
            if cleaned and not UNSOLICITED_REFUND.search(cleaned):
                answer = cleaned
            else:
                # Keep factual billing content from tools; drop refund-pitch drafts.
                accounts = [r["result"] for r in results if r["name"] == "get_account"]
                invoices = [
                    r["result"]
                    for r in results
                    if r["name"] in ("get_invoice", "list_invoices")
                    and not r["result"].get("error")
                ]
                if invoices or accounts:
                    answer = _factual_billing_summary(accounts, invoices, listings)
        tickets = [
            r["result"]
            for r in results
            if r["name"] == "create_escalation_ticket" and r["result"].get("ticket_id")
        ]
        if tickets and tickets[-1]["ticket_id"] not in answer:
            answer += "\n\n" + ticket_recorded_note(tickets[-1]["ticket_id"], created=True)
        return _ensure_account_grounding(answer, support_blob, latest)

    if evidence is not None and evidence.get("decision") == "GROUNDED" and policy_text:
        open_ids = TICKET.findall(support) + [
            r["result"]["ticket_id"]
            for r in results
            if r["name"] == "create_escalation_ticket" and r["result"].get("ticket_id")
        ]
        if open_ids and re.search(
            r"(?i)\b(when|contact|reach|follow[- ]?up|reference|ticket)\b", latest
        ):
            return (
                "I don't have a specific contact-time SLA in the available Loopp knowledge. "
                + ticket_recorded_note(open_ids[0])
            )
        answer = policy_text
        tickets = [
            r["result"]
            for r in results
            if r["name"] == "create_escalation_ticket" and r["result"].get("ticket_id")
        ]
        if tickets:
            ticket = tickets[-1]
            if ticket["ticket_id"] not in answer:
                answer += "\n\n" + ticket_recorded_note(ticket["ticket_id"], created=True)
        return answer

    tickets = [
        r["result"]
        for r in results
        if r["name"] == "create_escalation_ticket" and r["result"].get("ticket_id")
    ]
    if tickets:
        ticket = tickets[-1]
        if answer and not unsupported_facts(answer, support_blob):
            if ticket["ticket_id"] not in answer:
                answer += "\n\n" + ticket_recorded_note(ticket["ticket_id"], created=True)
            return _ensure_account_grounding(answer, support_blob, latest)
        return ticket_recorded_note(ticket["ticket_id"], created=True)

    if answer and unsupported_facts(answer, support_blob):
        return FACT_CHECK_FALLBACK
    return _ensure_account_grounding(answer or FACT_CHECK_FALLBACK, support_blob, latest)
