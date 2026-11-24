"""Small manual retrieval evaluation. Uses real app.policy.search embeddings."""

from app.policy import search, MIN_SIMILARITY

IN_SCOPE = (
    ("Pro", "What is the refund window for a Pro monthly subscription?"),
    ("Business", "What amount limit applies to Business monthly refunds?"),
    ("Free", "Can Free accounts receive automatic subscription refunds?"),
    ("Enterprise", "Do Enterprise contract refunds need human review?"),
    ("Pro", "What happens to refunds for an annual Pro subscription?"),
    ("Business", "How should I report a suspected duplicate invoice charge?"),
    ("Pro", "How do I troubleshoot problems logging into my account?"),
    ("Business", "What should I check when account activation fails?"),
    ("Pro", "What information is needed to create a support ticket?"),
    ("Business", "Can the agent change my subscription usage limits?"),
)
OUT_OF_SCOPE = (
    ("Pro", "Can you refund my airline ticket after a flight cancellation?"),
    ("Business", "Does Loopp cover a refund for my restaurant bill?"),
    ("Pro", "Can you approve a refund for a broken laptop from another retailer?"),
    ("Business", "What travel insurance policy covers my lost luggage?"),
    ("Pro", "Which medication should I take for a persistent headache?"),
    ("Business", "How do I refinance a mortgage on my house?"),
    ("Pro", "Who won the most recent football championship?"),
    ("Business", "What will the weather be tomorrow in Chicago?"),
    ("Pro", "How do I bake a sourdough loaf at home?"),
    ("Business", "Write a Python algorithm to solve a Sudoku puzzle."),
)


# Fixed before the first verification run; do not tune the gate against these results.
HELD_OUT_OF_SCOPE = (
    ("Business", "Can I get a refund for my restaurant meal?"),
    ("Pro", "Why did Netflix bill me twice this month?"),
    ("Business", "Explain the late payment fee on my Amazon invoice."),
    ("Pro", "Can you cancel my Spotify Premium subscription and refund the charge?"),
    ("Business", "Can my landlord legally keep my security deposit?"),
    ("Pro", "Draft a legal defense against this parking fine."),
    ("Business", "Is this chest pain a heart attack or indigestion?"),
    ("Pro", "Should I double my prescribed antibiotic dose today?"),
    ("Business", "Hello there, how are you feeling today?"),
    ("Pro", "Tell me a funny joke about penguins."),
)


def main():
    errors = 0
    extrema = {}
    for label, queries, expected in [
        ("original-in-scope", IN_SCOPE, True),
        ("original-out-of-scope", OUT_OF_SCOPE, False),
        ("held-out-out-of-scope", HELD_OUT_OF_SCOPE, False),
    ]:
        before = after = 0
        similarities = []
        print(f"\n{label}: expected {'GROUNDED' if expected else 'not GROUNDED'}", flush=True)
        for number, (tier, query) in enumerate(queries, 1):
            try:
                # Both decisions use exactly the same retrieved vectors and passages.
                result = search(query, tier)
                baseline = result.get("retrieval_decision", result["decision"])
                verdict = result.get("relevance_verification", {}).get("verdict", "NOT_RUN")
                before_ok = (baseline == "GROUNDED") == expected
                # A failed verifier is a closed decision, but not a measured semantic success.
                after_ok = (
                    (result["decision"] == "GROUNDED") == expected
                ) and verdict != "UNAVAILABLE"
                before += before_ok
                after += after_ok
                similarity = result["top_similarity"]
                if similarity is not None:
                    similarities.append(similarity)
                if verdict == "UNAVAILABLE":
                    errors += 1
                print(
                    f"{number:02d} | similarity={similarity if similarity is not None else 'unavailable'} | "
                    f"threshold={result['threshold']} | before={baseline} ({'PASS' if before_ok else 'FAIL'}) | "
                    f"after={result['decision']} ({'PASS' if after_ok else 'FAIL'}) | relevance={verdict} | tier={tier} | {query}",
                    flush=True,
                )
            except Exception as error:
                errors += 1
                print(
                    f"{number:02d} ERROR | similarity=unavailable | threshold={MIN_SIMILARITY} | decision=ERROR ({type(error).__name__}) | {query}",
                    flush=True,
                )
        print(
            f"{label} before pass rate: {before}/{len(queries)} ({100 * before / len(queries):.1f}%)",
            flush=True,
        )
        print(
            f"{label} after pass rate: {after}/{len(queries)} ({100 * after / len(queries):.1f}%)",
            flush=True,
        )
        extrema[label] = similarities
    for name, operation, label in [
        ("Max original out-of-scope", max, "original-out-of-scope"),
        ("Min original in-scope", min, "original-in-scope"),
        ("Max held-out out-of-scope", max, "held-out-out-of-scope"),
    ]:
        values = extrema[label]
        print(f"{name} similarity: {operation(values) if values else 'unavailable'}", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
