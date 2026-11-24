"""Authoring source for the fictional Loopp demonstration knowledge base."""

import json
from pathlib import Path

# Each document has distinct, authored sections; no synthetic paragraph repetition.
DOCS = [
    (
        "refunds",
        "General refund eligibility",
        "policy",
        ["all"],
        [
            (
                "Scope and prerequisites",
                """Loopp uses mock billing records for this demonstration. A subscription refund creates a persisted mock receipt and never transfers money. Before discussing an invoice, resolve the customer by their fictional account email and check invoice ownership. A customer may receive details only for invoices belonging to their verified demo account. Knowing another invoice identifier does not grant permission. Request missing identifiers without guessing an account or exposing another customer's name, balance, or subscription.""",
            ),
            (
                "Decision and confirmation",
                """Automatic refunds require a paid invoice, an eligible subscription and contract, and the tier-specific rules effective on the request date. Unpaid, overdue, cancelled, disputed, and already refunded invoices cannot receive an automatic mock refund. Suspended or expired subscriptions and expired or terminated contracts require human review. An active or cancelled subscription can qualify if its other conditions pass. The refund window uses calendar days from the invoice date. Future-dated invoices are ineligible. A unique confirmation binds one customer, invoice, full amount, policy version, and conversation for ten minutes. Only the confirmation button executes the refund; spoken agreement cannot replace it.""",
            ),
            (
                "Exceptions and review",
                """Use the Pro, Business, or Enterprise document for the applicable window and limit. Free accounts have no automatic paid subscription refund entitlement. An invoice can have only one mock receipt, including after reconnect or repeated confirmation. A denied automatic refund does not imply a decision about statutory rights or an external payment provider. Offer human review when the customer requests an exception, and obtain authorization before opening a ticket.""",
            ),
        ],
    ),
    (
        "pro-refunds",
        "Pro subscription refund rules",
        "policy",
        ["Pro"],
        [
            (
                "Eligible monthly invoices",
                """Pro monthly subscriptions allow a full mock refund within thirty calendar days of the invoice date, up to one hundred US dollars per invoice. The invoice must be paid and owned by the verified customer. The subscription must be active or cancelled, with an active contract whose end date has not passed. Cancellation alone does not invalidate a recent qualifying monthly invoice. The tool checks these conditions from current structured records rather than inferring them from the wording of a request.""",
            ),
            (
                "Annual billing and exclusions",
                """Pro annual invoices are excluded from automatic refunds in this demonstration. Refer annual billing exceptions for human review after obtaining consent. Suspended, trial, and expired subscriptions do not pass the automatic refund check. Disputed invoices must remain in the dispute process; do not issue a parallel mock refund. A partial refund, credit, currency conversion, or transfer to another invoice is unsupported. Explain the available full-invoice action and ask for clarification if the requested amount differs from the paid invoice.""",
            ),
            (
                "Customer confirmation",
                """After eligibility passes, display the exact invoice identifier and USD amount in the confirmation card. Tell the customer that no receipt exists until they click that card. Confirmation expires after ten minutes and is checked again against current data. If the amount, customer, or applicable policy changed, stage a new check rather than modifying the token. Repeated clicks with the same valid confirmation return the same persisted receipt.""",
            ),
        ],
    ),
    (
        "business-billing",
        "Business subscription billing rules",
        "policy",
        ["Business"],
        [
            (
                "Monthly billing and ownership",
                """Business accounts use a shared workspace subscription with one billing owner. This demo resolves that owner through the verified customer account. Workspace members cannot use another account email to retrieve invoices. Monthly billing records include the subscription, account, contract, invoice date, payment status, and total. Seat allocation and tax calculations are outside the available structured records. Do not reconstruct a missing calculation from the total or promise that an estimate is an invoice correction.""",
            ),
            (
                "Refunds and annual commitments",
                """A paid Business monthly invoice can receive one full mock refund within fourteen calendar days, with a maximum of five hundred US dollars. The subscription must be active or cancelled and its contract active and unexpired. Annual commitments require human review and cannot use the automatic refund action. A suspended account, terminated agreement, overdue invoice, or disputed payment also requires resolution before automatic eligibility can be considered. The backend owns this decision and rechecks it when the confirmation button is clicked.""",
            ),
            (
                "Billing assistance",
                """For a suspected duplicate charge, collect the two invoice identifiers and inspect their actual records. Similar amounts alone do not establish a duplicate. For a purchase order, consolidated invoice, or negotiated billing arrangement, offer a billing ticket rather than inventing contractual terms. State the evidence that is available and the details that are missing. The agent cannot edit invoices or account restrictions through a refund request.""",
            ),
        ],
    ),
    (
        "enterprise-contracts",
        "Enterprise contract and refund terms",
        "policy",
        ["Enterprise"],
        [
            (
                "Contract authority",
                """Enterprise subscriptions are governed by their recorded agreement. Account tools expose contract status and end date, but do not contain signed commercial schedules, service credits, or negotiated cancellation clauses. An active contract establishes only that the account is currently covered. It does not establish a right to a specific credit or refund. Expired and terminated contracts prevent automated financial actions. Do not treat a previous invoice description as proof that an agreement renewed.""",
            ),
            (
                "Mandatory manual review",
                """All Enterprise refund requests require human contract review, even when an invoice is paid and recent. The automatic mock refund tool must decline staging for this tier. Collect the owned invoice identifier, the customer's stated issue, and the requested outcome. Explain that the available knowledge cannot determine negotiated refund entitlements. Offer an escalation ticket, wait for authorization, and attach the relevant account and invoice references without attaching unrelated customers' information.""",
            ),
            (
                "Permitted assistance",
                """The support agent can retrieve owned invoice facts, show the current general guidance, and create an authorized review ticket. It cannot change contract dates, waive commitments, alter payment terms, or represent a service credit as approved. Enterprise billing cycles may be monthly or annual; neither cycle removes manual review. If the customer asks when money will arrive, explain that this demonstration has no payment processing integration and cannot predict a transfer date.""",
            ),
        ],
    ),
    (
        "cancellation",
        "Subscription cancellation",
        "guide",
        ["all"],
        [
            (
                "Clarify the request",
                """Distinguish stopping a future renewal, deleting an account, and refunding an existing invoice. These are different requests with different evidence requirements. Ask which subscription or invoice is involved if the customer gives no identifier. This demo has read-only subscription tools and cannot directly cancel or delete an account. Explain that limitation and offer an authorized support ticket for the requested change. Do not claim that describing cancellation steps has changed account state.""",
            ),
            (
                "Access and billing",
                """Cancellation ordinarily prevents a future renewal after it is processed by the appropriate billing team; the exact access end date must come from the account record or a human confirmation. Do not invent an access date when the record does not expose one. Already generated invoices keep their recorded payment status until a supported action changes it. A cancelled subscription can still have an invoice eligible under Pro or Business monthly rules, provided the contract and remaining conditions pass.""",
            ),
            (
                "Special cases",
                """Enterprise cancellation requires contract review. Trial cancellation, account deletion, data export, and organization ownership transfers also require a separate authorized workflow outside this demo. If a customer says cancel everything, ask what scope they intend before offering a ticket. A cancellation request is not consent to refund an invoice and does not authorize a support ticket by itself. Confirm the chosen next step and preserve its context in the conversation.""",
            ),
        ],
    ),
    (
        "billing-disputes",
        "Billing disputes and failed payments",
        "guide",
        ["all"],
        [
            (
                "Identify the payment problem",
                """Ask for an owned invoice identifier and whether the customer sees an unpaid balance, a bank charge, or a formal dispute. Retrieve its actual status before suggesting an action. A bank notification can precede an invoice update, and the agent cannot inspect the bank's ledger. Never ask for a card number, card security code, bank login, or one-time authentication code. The fictional demo email and invoice identifier are sufficient to locate supported records.""",
            ),
            (
                "Status handling",
                """An unpaid or overdue invoice has no completed payment eligible for a refund. Explain the recorded status and offer billing assistance when the customer reports conflicting evidence. A disputed invoice must remain in its review process and cannot receive an automatic mock refund. A cancelled invoice should not be paid through a separate link supplied in chat. For a refunded invoice, report the recorded status and avoid creating another receipt. Do not infer the date a bank will post funds.""",
            ),
            (
                "Failed payment troubleshooting",
                """For a payment failure, suggest checking the billing details through the customer's trusted account portal and contacting their bank for a decline explanation. This agent cannot retry a charge or update a payment method. Persistent failures, a disputed duplicate charge, or disagreement between records and customer evidence can be referred to human billing support after authorization. Include the invoice identifiers and the reported discrepancy; exclude payment credentials from ticket text.""",
            ),
        ],
    ),
    (
        "plan-changes",
        "Plan upgrades and downgrades",
        "guide",
        ["all"],
        [
            (
                "Plan selection",
                """Loopp's fictional subscription tiers are Free, Pro, Business, and Enterprise. Retrieve the customer's current tier and billing cycle before discussing an account change. The account tool returns the current recorded subscription; an older invoice may describe a legacy plan. Use that distinction when explaining a mismatch. This demonstration does not contain a complete pricing catalog and cannot quote unrecorded prices or negotiated enterprise discounts.""",
            ),
            (
                "Timing and adjustments",
                """An upgrade, downgrade, and cancellation have different effects. Ask which tier the customer wants and whether they are asking about a past charge or a future change. No tool in this demo changes a subscription, calculates proration, or schedules a renewal. A paid plan-adjustment invoice can be inspected as an invoice, but its description is not sufficient evidence to calculate a new adjustment. Do not promise credits, seat prices, or a future charge from the current invoice amount.""",
            ),
            (
                "Review workflow",
                """For an authorized plan-change ticket, preserve the current tier, requested tier, billing cycle, and desired effective date as a customer request rather than an approved outcome. Expired or terminated contracts and suspended accounts need review before changes. Enterprise changes require the billing or contract team. The support agent may explain these limitations, retrieve applicable guidance, and offer a ticket; it must not report the plan as changed until a supported backend action records that change.""",
            ),
        ],
    ),
    (
        "trials",
        "Trial and account activation",
        "guide",
        ["all"],
        [
            (
                "Trial state",
                """A trial subscription is a distinct account status. Retrieve the account before assuming the customer has an active paid subscription. The demo does not issue invoices for card authorizations or promise a particular trial length. A trial account cannot use the automatic paid-subscription refund workflow merely because the user reports seeing a bank hold. Ask for an owned invoice if they believe a payment was completed, and use its actual status as the source of truth.""",
            ),
            (
                "Activation checks",
                """If access appears inactive, ask whether the customer can sign in and which workspace they expect to access. Check the recorded subscription status and restrictions. Suggest signing out and back into the trusted account portal when the account is active but the interface appears stale. Do not ask the customer to share an activation link, password, authentication cookie, or email verification code. This demo's email lookup resolves fictional records and does not verify a real person's identity.""",
            ),
            (
                "Escalation boundaries",
                """An expired trial, unexpected paid invoice, or unresolved activation issue may require human support. Clarify the symptom and account first so the ticket is actionable. Trial extension, email ownership verification, workspace transfer, and conversion to a paid plan are unavailable agent actions. Offer human assistance and create a ticket only after the customer requests it or explicitly accepts an offered escalation. Do not silently convert a trial or remove account restrictions.""",
            ),
        ],
    ),
    (
        "invoice-corrections",
        "Invoice corrections",
        "guide",
        ["all"],
        [
            (
                "Inspect the record",
                """For an invoice correction, first identify the owned invoice and the specific incorrect field. Supported records show identifier, date, amount, currency, payment status, description, and linked account information. A typo in a customer request does not prove the invoice is wrong. Ask the customer to describe the expected correction without requesting sensitive tax documents in the chat. Do not disclose another customer's invoice while comparing records.""",
            ),
            (
                "Changes requiring review",
                """The agent cannot edit a legal billing name, tax number, address, currency, or historical invoice amount. Those corrections require a billing team review. A refund receipt does not rewrite the original invoice and must not be used as a substitute for a correction. When multiple invoices have similar descriptions, ask which one needs review instead of selecting the newest automatically. An unpaid invoice is still a billing record, even though it cannot receive a paid-invoice refund.""",
            ),
            (
                "Ticket contents",
                """With customer authorization, create a correction ticket containing the invoice identifier, current field as exposed by the tools, requested field change, and reason. Label unverified desired values as customer supplied. If the field is absent from the structured data, say so rather than inventing its current value. The ticket status begins open; do not promise a revised invoice, turnaround time, or tax treatment that the knowledge base does not establish.""",
            ),
        ],
    ),
    (
        "suspension",
        "Account suspension and reactivation",
        "guide",
        ["all"],
        [
            (
                "Account restrictions",
                """Suspended accounts retain their records but cannot use automatic financial actions in this demonstration. Retrieve the subscription status and restriction labels to explain what is recorded. A restriction label is a support signal, not a complete investigation result. Avoid attributing fraud, misconduct, or a specific unpaid debt unless the structured evidence explicitly supports it. An expired subscription and a suspended subscription are separate states that may need different human review.""",
            ),
            (
                "Reactivation requests",
                """Ask whether the customer needs access restored, a billing balance explained, or a terminated contract reviewed. Read-only account tools cannot clear restrictions or reactivate a subscription. A paid invoice by itself does not establish that all restrictions have been resolved. Expired or terminated contracts require contract review before reactivation can be promised. Do not suggest creating a second account to evade restrictions or using another customer's identity.""",
            ),
            (
                "Safe assistance",
                """Offer an authorized support ticket with the current account status, restriction labels, owned invoice references, and the customer's requested outcome. Exclude unrelated account data. If the issue is unclear, ask a clarifying question before offering a specific action. Tickets record requests for review and do not imply approval. Refund checks must still apply payment status, tier rules, contract validity, date limits, and invoice-specific confirmation after any later status change.""",
            ),
        ],
    ),
    (
        "login",
        "Login and authentication troubleshooting",
        "troubleshooting",
        ["all"],
        [
            (
                "Basic sign-in checks",
                """For sign-in problems, ask whether the customer sees an error, cannot receive an email, or reaches the wrong workspace. Suggest checking the account email spelling, using the trusted Loopp sign-in page, and trying a private browser window to isolate stale cookies. If the private window works, clear only the site's stored sign-in data and try again. Explain that doing so may sign the customer out. Do not recommend disabling browser security controls.""",
            ),
            (
                "Email and multi-factor issues",
                """For a missing sign-in email, suggest checking spam folders and mail filtering, then requesting one new link through the trusted sign-in page. Avoid repeated rapid requests. Never request a password, recovery code, authentication token, or one-time code in conversation. This agent's fictional email lookup cannot reset credentials, prove mailbox ownership, or bypass multi-factor authentication. A customer who has lost both their primary sign-in method and recovery method needs the account recovery team.""",
            ),
            (
                "When checks fail",
                """Collect the error text without secrets, approximate occurrence time, browser type, and whether another trusted device has the same issue. Do not ask for a screenshot containing credentials. Offer a human support ticket if the customer wants further help. A general login question can be answered without selecting a subscription tier. Tier-specific billing entitlements still require a resolved demo account. Repeated failures do not authorize an automatic refund or removal of restrictions.""",
            ),
        ],
    ),
    (
        "usage",
        "Subscription usage limits",
        "guide",
        ["all"],
        [
            (
                "Available evidence",
                """The account tool identifies tier, status, and billing cycle, but the demonstration has no live usage meter or complete quota catalog. Do not invent remaining credits, seat counts, voice minutes, storage capacity, or reset times. Explain which account facts are available and ask what limit message the customer encountered. An invoice total does not establish a usage allowance, and a plan name does not prove that a specific feature is enabled.""",
            ),
            (
                "Troubleshooting a limit message",
                """Suggest refreshing the trusted account portal and checking that the correct workspace is selected. Ask whether the message appears for one task or every task. A temporary connection error can resemble a limit problem; preserve the actual error text without guessing a root cause. The agent cannot increase quota, reset usage, remove a cap, or change subscription tier. Do not advise making extra accounts or bypassing a restriction.""",
            ),
            (
                "Support review",
                """If the displayed allowance conflicts with the customer's agreement, offer an authorized review ticket with the tier, account identifier, error text, and requested clarification. Enterprise allowances can depend on a signed schedule unavailable to this agent. For other tiers, avoid quoting numerical limits absent from the retrieved documents. A limit question should receive a clear explanation of the evidence gap and useful troubleshooting steps, rather than a fabricated quota or an unsolicited ticket.""",
            ),
        ],
    ),
    (
        "escalation",
        "Human escalation procedures",
        "policy",
        ["all"],
        [
            (
                "Escalation decision",
                """Offer human assistance when applicable knowledge is unavailable, evidence conflicts, an action requires contract review, or a permanent tool failure prevents resolution. Insufficient evidence is a reason to explain the limitation; it is not automatic permission to create a ticket. If a request is ambiguous, ask a useful clarifying question first. A low similarity score measures retrieval relevance and must never be presented as a probability that an answer is correct.""",
            ),
            (
                "Consent and identity",
                """Create a ticket when a verified fictional customer explicitly requests human support or accepts an offer for the current issue. A negative response must stop the workflow. A bare yes is sufficient only when the immediately preceding assistant message offered a ticket and the issue is already clear. If no account is resolved, ask for the demo account email. Never transfer another session's history or unrelated customer's invoice into the ticket.""",
            ),
            (
                "Explain the next step",
                """Give the persisted ticket identifier and open status after creation succeeds. Include the limitation, customer request, related owned invoices, and policy evidence references. Do not promise a response time, refund approval, or actual contact from a human in this demonstration. Repeated requests for the same open issue return the existing ticket. A new issue should use a new conversation so its context and authorization remain distinct.""",
            ),
        ],
    ),
    (
        "tickets",
        "Support ticket handling",
        "guide",
        ["all"],
        [
            (
                "Ticket record",
                """A support ticket records the resolved customer identifier, conversation, category, reason, evidence references, and status. This demo stores tickets in PostgreSQL and initially marks them open. A ticket is a durable request for human review; it does not send an email or trigger a staffed support queue. Explain that boundary when a customer asks who will call or when a reply will arrive. Never invent an assigned agent or estimated resolution time.""",
            ),
            (
                "Context and duplicates",
                """Keep a concise issue description, the customer-authorized outcome, and relevant references from the conversation. Label customer reports separately from verified records. The backend allows one open ticket per conversation and returns its existing identifier for repeated requests, even when the model paraphrases the reason. Use a fresh conversation for a different issue. A reconnect must not produce additional tickets for the same open conversation.""",
            ),
            (
                "Corrections and privacy",
                """If the customer notices an incorrect ticket description, explain that this demo has no ticket-editing tool and preserve their correction in the conversation for review. Avoid including credentials, payment secrets, or unrelated histories. If ticket creation fails, report that no ticket confirmation is available and offer a retry after the issue is resolved. A displayed optimistic loading state does not prove persistence; only the successful backend result establishes a ticket identifier.""",
            ),
        ],
    ),
    (
        "refunds-archive",
        "Archived subscription refund policy",
        "policy",
        ["all"],
        [
            (
                "Historical terms",
                """This archived fictional policy version applied before November 2026 and allowed a seven-calendar-day window for certain paid subscription refunds. It is retained only to demonstrate versioned storage and exclusion from current retrieval. It is superseded by the active general and tier-specific policy documents. Its historical window must not be substituted for current Pro, Business, or Enterprise rules. No current action should cite this document as an active entitlement.""",
            ),
            (
                "Version transitions",
                """A historical invoice date does not automatically select an archived policy in this demonstration. Current requests use rules active and effective on the request date. The structured refund service enforces those rules independently of generated language. Historical legal interpretation, retroactive exceptions, and negotiated commitments require human review. The agent must not infer a grandfathered benefit or promise a historical exception from this document's presence in the database.""",
            ),
            (
                "Retention and audit",
                """Archived chunks retain document identifiers, headings, versions, dates, source references, and embeddings so maintainers can inspect the knowledge history. Normal support search filters them out before ranking. They do not count as current evidence, even when their wording is semantically similar to a refund question. If only archived evidence exists, the correct result is insufficient current evidence and an offer of human assistance after clarification. Retaining an embedding does not authorize using it in an answer.""",
            ),
        ],
    ),
]


def build():
    docs = []
    for ident, title, kind, tiers, sections in DOCS:
        archived = ident == "refunds-archive"
        docs.append(
            dict(
                document_id=ident,
                policy_id=ident,
                title=title,
                document_type=kind,
                version=1 if archived else 2,
                tiers=tiers,
                effective_date="2025-01-01" if archived else "2026-11-01",
                expiration_date="2026-10-31" if archived else None,
                active=not archived,
                source_reference=f"loopp-demo://support/{ident}",
                body="\n\n".join(f"## {heading}\n{text}" for heading, text in sections),
            )
        )
    Path(__file__).with_name("policies.json").write_text(json.dumps(docs, indent=2) + "\n")
    print(len(docs), "documents;", sum(len(d["body"].split()) for d in docs), "words")


if __name__ == "__main__":
    build()
