"""Small conversational turns need no policy search or additional model call."""

import re


def social_reply(query, history, context):
    text = query.strip().lower().replace("’", "'")
    account = context.get("account")
    greeting = re.fullmatch(
        r"(?:(?:hey|hi|hello|hi there)[,! .]*)?(?:(?:can|could) you hear me|are you there|how are you(?: doing)?|is this working)?[?!. ]*",
        text,
    )
    if greeting and text:
        if account:
            return f"Hi {account['name'].split()[0]}! I'm here. How can I help you today?"
        return "Hi! I'm here, and I'm Loopp. How are you today, and what should I call you?"
    if re.search(r"what (?:can you do|questions (?:can|do) you answer)|how can you help", text):
        return "I can check your account and invoices, look up support policies, check mock refund eligibility, and create a support ticket with your permission. What would you like help with?"
    previous = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
    if (
        not account
        and re.search(r"what (?:should i call you|is your name)|your name", previous, re.I)
        and "@" not in query
    ):
        name = re.sub(r"(?i)^(?:my name is|i am|i'm|it's|this is)\s+", "", query.strip()).strip(
            " .!"
        )
        name = re.split(r"[.!?]", name)[0].strip()
        if (
            re.fullmatch(r"[A-Za-z][A-Za-z '-]{0,40}", name)
            and 1 <= len(name.split()) <= 3
            and not re.search(r"refund|invoice|policy|account|fine|good", name, re.I)
        ):
            return f"Nice to meet you, {name}. What's the email on your fictional demo account?"
    if not account and re.fullmatch(
        r"(?i)(?:i am|i'm|my name is) [A-Za-z .'-]{1,60}", query.strip()
    ):
        return "Thanks. Please spell your complete email address, including dots and the domain, or type it in the email box on screen."
    return None
