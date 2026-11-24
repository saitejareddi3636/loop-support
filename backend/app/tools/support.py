"""Session-bound support tools. Identity and financial decisions belong to the backend."""

from langchain_core.tools import tool
from app.db import connect
from app.policy import search
from app.data import account_by_customer, invoice_by_id
from app.refunds import create_ticket, stage_refund, eligibility, existing_refund
from app.storage import (
    set_verified_customer,
    verified_customer,
    update_context,
    conversation_context,
    get_chat,
    user_invoice_ids,
)
from app.tools.customer import verify_customer as lookup_customer


def invoice_state(chat_id, invoice, db):
    prior = existing_refund(db, chat_id, invoice["invoice_id"])
    if prior:
        return {
            **invoice,
            "refund_status": "already_refunded",
            "receipt_id": prior["receipt_id"],
            "eligible": False,
            "eligibility_reason": "A mock refund receipt already exists.",
        }
    check = eligibility(chat_id, invoice["invoice_id"], db, lock=False)
    return {
        **invoice,
        "refund_status": "ineligible" if "error" in check else "eligible",
        "eligible": "error" not in check,
        "eligibility_reason": check.get("error", "Passed the current backend refund checks."),
    }


def owned_invoices(chat_id):
    customer_id = verified_customer(chat_id)
    if not customer_id:
        return {
            "error": "What is the email on your fictional demo account?",
            "outcome": "needs_information",
        }
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM invoices WHERE customer_id=%s ORDER BY date DESC,invoice_id",
            (customer_id,),
        ).fetchall()
        items = [invoice_state(chat_id, row, db) for row in rows]
    eligible = [i["invoice_id"] for i in items if i["eligible"]]
    references = {
        "listed_invoice_ids": [i["invoice_id"] for i in items],
        "eligible_invoice_ids": eligible,
        "selected_invoice_id": eligible[0] if len(eligible) == 1 else None,
    }
    if len(eligible) > 1:
        references["user_selected_invoice_id"] = None
    update_context(chat_id, references)
    return {
        "customer_id": customer_id,
        "invoices": items,
        "eligible_count": len(eligible),
        "already_refunded_count": sum(i["refund_status"] == "already_refunded" for i in items),
    }


def tools_for_chat(chat_id: str):
    def resolve(customer_id=None):
        bound = verified_customer(chat_id)
        if not bound:
            return None, {
                "error": "What is the email on your fictional demo account?",
                "outcome": "needs_information",
            }
        if customer_id and customer_id != bound:
            return None, {
                "error": "That customer ID does not match this verified conversation. Omit the customer ID to use the current account.",
                "outcome": "needs_information",
            }
        return bound, None

    @tool
    def verify_customer(email: str) -> dict:
        """Resolve the customer's fictional email once and retain it for this conversation. Never guess an email."""
        result = lookup_customer.invoke({"email": email})
        if result.get("verified"):
            set_verified_customer(chat_id, result["customer_id"])
        return result

    @tool
    def get_account(customer_id: str | None = None) -> dict:
        """Get the conversation's verified account, tier, subscription and contract. Omit customer_id; the backend resolves it."""
        bound, error = resolve(customer_id)
        return error or account_by_customer(bound) or {"error": "The account could not be found."}

    @tool
    def get_invoice(invoice_id: str, customer_id: str | None = None) -> dict:
        """Get a known owned invoice and its actual refund eligibility. Omit customer_id. Use list_invoices if the invoice ID is unknown; never guess IDs."""
        bound, error = resolve(customer_id)
        if error:
            return error
        with connect() as db:
            invoice = invoice_by_id(invoice_id, db)
            if not invoice or invoice["customer_id"] != bound:
                return {
                    "error": "Invoice not found for this account. Use list_invoices to inspect your own invoices.",
                    "outcome": "needs_information",
                }
            result = invoice_state(chat_id, invoice, db)
        update_context(chat_id, {"selected_invoice_id": invoice["invoice_id"]})
        return result

    @tool
    def list_invoices() -> dict:
        """List the verified account's real invoices and backend refund eligibility. Use for 'Do I have any eligible refunds?' or when no invoice ID was supplied. Does not stage or issue refunds."""
        return owned_invoices(chat_id)

    @tool
    def search_policy(query: str) -> dict:
        """Retrieve applicable support documentation. Give a complete question; general policy terms do not establish personal invoice eligibility."""
        customer_id = verified_customer(chat_id)
        account = account_by_customer(customer_id) if customer_id else None
        if account and query.strip().lower() in ("refund", "refunds", "refund policy"):
            query = f"Refund eligibility policy for {account['tier']} monthly subscription invoices"
        return search(query, account["tier"] if account else None)

    @tool
    def issue_mock_refund(invoice_id: str | None = None) -> dict:
        """Stage a requested invoice refund. Omit invoice_id only for a previously selected invoice or one uniquely eligible invoice. Never guess. A separate confirmation click still issues the receipt."""
        # A model-supplied argument or a model's invoice lookup is not a user choice.
        messages = get_chat(chat_id)["messages"]
        latest = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        ids = user_invoice_ids(latest)
        selected = next(iter(ids)) if len(ids) == 1 else None
        if not ids:
            selected = conversation_context(chat_id)["references"].get("user_selected_invoice_id")
        if selected:
            return stage_refund(chat_id, selected)
        listing = owned_invoices(chat_id)
        if "error" in listing:
            return listing
        eligible = [i for i in listing["invoices"] if i["eligible"]]
        if not eligible:
            return {"status": "no_eligible_invoices", **listing}
        if len(eligible) > 1 or len(ids) > 1:
            return {
                "status": "invoice_choice_required",
                "invoices": eligible,
                "message": "Which invoice would you like me to check for a mock refund?",
            }
        return stage_refund(chat_id, eligible[0]["invoice_id"])

    @tool
    def create_escalation_ticket(category: str, reason: str) -> dict:
        """Create a persisted support ticket after the customer authorizes human assistance for a clear issue."""
        return create_ticket(chat_id, category, reason)

    return [
        verify_customer,
        get_account,
        get_invoice,
        list_invoices,
        search_policy,
        issue_mock_refund,
        create_escalation_ticket,
    ]
