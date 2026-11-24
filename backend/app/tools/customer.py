"""Read-only fictional data tools backed by PostgreSQL."""

from langchain_core.tools import tool
from app.data import customer_by_email, account_by_customer, invoice_by_id


@tool
def verify_customer(email: str) -> dict:
    """Resolve a fictional customer email. This is demo lookup, not real authentication."""
    row = customer_by_email(email)
    return (
        {
            "verified": True,
            "customer_id": row["customer_id"],
            "name": row["name"],
            "mock_identity": True,
        }
        if row
        else {"verified": False, "error": "No fictional customer found for this email."}
    )


@tool
def get_account(customer_id: str) -> dict:
    """Retrieve actual tier, subscription, contract, and account restrictions."""
    return account_by_customer(customer_id) or {"error": "Account not found."}


@tool
def get_invoice(customer_id: str, invoice_id: str) -> dict:
    """Retrieve a structured invoice with an ownership check."""
    row = invoice_by_id(invoice_id)
    return (
        row
        if row and row["customer_id"] == customer_id
        else {"error": "Invoice not found for this customer."}
    )
