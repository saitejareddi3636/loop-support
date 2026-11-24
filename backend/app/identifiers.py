"""Normalize user-provided identifiers without guessing an account or invoice."""

import re

EMAIL = r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}"
DIGITS = dict(zip("zero one two three four five six seven eight nine".split(), "0123456789"))
ORDINALS = {
    word: index
    for index, word in enumerate(
        "first second third fourth fifth sixth seventh eighth ninth tenth".split(), 1
    )
}


def supplied_email(text):
    text = text.lower().replace("’", "'")
    literal = set(re.findall(EMAIL, text))
    if literal:
        return next(iter(literal)) if len(literal) == 1 else None
    if not re.search(r"\bat\b|@", text):
        return None
    text = re.split(r"(?:my email(?: address)?(?: is)?|email address is|it's|it is)\s*", text)[-1]
    text = re.sub(r"^(?:i am|i'm)\s+", "", text)
    text = re.sub(r"\s+(?:please|thanks)[.!?]*$", "", text)
    for word, symbol in [
        ("at", "@"),
        ("dot", "."),
        ("period", "."),
        ("underscore", "_"),
        ("hyphen", "-"),
        ("dash", "-"),
        ("plus", "+"),
    ]:
        text = re.sub(r"\b" + word + r"\b", symbol, text)
    candidate = re.sub(r"\s+", "", text).strip(" .!?")
    return candidate if re.fullmatch(EMAIL, candidate) else None


def invoice_ids(text):
    # Spoken digit names are accepted only after an explicit invoice/INV prefix.
    text = re.sub(r"(?i)\b(?:" + "|".join(DIGITS) + r")\b", lambda m: DIGITS[m[0].lower()], text)
    ids = set()
    for match in re.finditer(
        r"(?i)\b(?:invoice|inv)[\s:#-]*(D\s*\d{8}\s*[- ]\s*\d+|\d(?:[ \t]*\d)*)\b", text
    ):
        value = re.sub(r"\s+", "", match[1]).upper()
        ids.add("INV-" + value)
    return ids


def selected_invoice_ids(text, references):
    explicit = invoice_ids(text)
    if explicit:
        return explicit
    choices = references.get("eligible_invoice_ids", [])
    clean = text.lower().strip(" .!?")
    clean = re.sub(r"^(?:yes|yeah|okay|ok)[, ]+", "", clean)
    if clean.isdigit() and "INV-" + clean in choices:
        return {"INV-" + clean}
    clean = re.sub(
        r"^(?:(?:please )?(?:refund|choose|select|use|review|prepare)|i want|i would like)\s+",
        "",
        clean,
    )
    clean = re.sub(r"^(?:the\s+)?(?:option\s+)?", "", clean)
    clean = re.sub(r"(?:\s+(?:one|invoice|option|please))+$", "", clean).strip()
    number = (
        ORDINALS.get(clean)
        or {"one": 1, "two": 2, "three": 3}.get(clean)
        or (int(clean) if clean.isdigit() else None)
    )
    if number and number <= len(choices):
        return {choices[number - 1]}
    return set()


def partial_invoice_reference(text):
    return bool(re.search(r"(?i)\b(?:invoice|inv)\s*[-:]?\s*D\d", text)) and not invoice_ids(text)
