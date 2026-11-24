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


# V2: authored once before this set's first measurement. Never select or replace
# cases based on their resulting similarity or verdict. High similarity is a
# hypothesis, not a guarantee; the evaluator reports misses explicitly.
HELD_OUT_IN_SCOPE_V2 = (
    ("Pro", "How many calendar days do I have to return a paid Loopp monthly charge?"),
    ("Business", "What's the ceiling for an automated refund on my monthly Loopp bill?"),
    ("Free", "Does a free Loopp membership have any paid-subscription money-back entitlement?"),
    ("Enterprise", "Who reviews a reimbursement request tied to my Loopp enterprise agreement?"),
    ("Business", "Can an annual Loopp commitment be refunded automatically?"),
    ("Pro", "What should I send support if the same Loopp payment appears twice?"),
    ("Business", "How should I troubleshoot being unable to sign into Loopp?"),
    ("Free", "My Loopp activation link failed; what checks should I make?"),
    ("Enterprise", "What details should accompany a Loopp human-support escalation?"),
    ("Pro", "Will the support agent raise the usage allowance on my Loopp plan?"),
    ("Business", "What does Loopp do with a disputed subscription charge?"),
    ("Pro", "What is the process for ending my Loopp subscription?"),
    ("Business", "When does a Loopp plan upgrade change my billing?"),
    ("Free", "How do Loopp trials and account activation work?"),
    ("Enterprise", "Which agreement controls billing exceptions for an enterprise Loopp account?"),
)
HELD_OUT_OUT_OF_SCOPE_V2 = (
    (
        "Business",
        "Does Loopp's Business monthly refund policy cover a refund for my restaurant meal?",
    ),
    (
        "Pro",
        "Can Loopp verify my paid monthly invoice and apply its refund window to my Netflix subscription?",
    ),
    (
        "Business",
        "Under Loopp's billing and refund eligibility policy, can I reclaim my Amazon Prime subscription invoice?",
    ),
    (
        "Pro",
        "Does Loopp's monthly subscription cancellation policy let me refund my gym membership invoice?",
    ),
    (
        "Business",
        "Can Loopp's support agent approve a full mock refund for my paid airline ticket invoice?",
    ),
    ("Pro", "Do Loopp's paid-invoice refund rules cover the disputed bill for my hotel stay?"),
    (
        "Business",
        "Can Loopp's account and billing support determine whether my tax refund is legally owed?",
    ),
    (
        "Enterprise",
        "Does Loopp's enterprise contract refund policy say I can sue my landlord over a deposit invoice?",
    ),
    (
        "Pro",
        "According to Loopp's billing policy, what medical treatment should I choose for chest pain?",
    ),
    (
        "Business",
        "Can Loopp's monthly refund limit tell me which antibiotic dose is medically safe?",
    ),
    (
        "Enterprise",
        "Using Loopp's subscription billing policy, quote Intercom's current enterprise pricing and discounts.",
    ),
    (
        "Business",
        "Compare Zendesk's current subscription invoice prices with Salesforce support plans under Loopp's billing rules.",
    ),
    ("Pro", "Can Loopp's paid monthly invoice refund policy reimburse my Spotify Premium charge?"),
    (
        "Business",
        "Do Loopp's Business refund eligibility and cancellation terms cover my electricity provider's bill?",
    ),
    (
        "Pro",
        "Does Loopp's subscription refund policy determine whether my insurance company must pay my medical invoice?",
    ),
)


def main(new_only=False):
    errors = 0
    extrema = {}
    old_sets = [
        ("original-in-scope", IN_SCOPE, True),
        ("original-out-of-scope", OUT_OF_SCOPE, False),
        ("held-out-out-of-scope", HELD_OUT_OF_SCOPE, False),
    ]
    new_sets = [
        ("v2-in-scope", HELD_OUT_IN_SCOPE_V2, True),
        ("v2-out-of-scope", HELD_OUT_OUT_OF_SCOPE_V2, False),
    ]
    for label, queries, expected in new_sets if new_only else old_sets + new_sets:
        before = after = 0
        verdict_counts = {"YES": 0, "NO": 0, "UNAVAILABLE": 0, "NOT_RUN": 0, "DISABLED": 0}
        similarities = []
        print(f"\n{label}: expected {'GROUNDED' if expected else 'not GROUNDED'}", flush=True)
        for number, (tier, query) in enumerate(queries, 1):
            try:
                # Both decisions use exactly the same retrieved vectors and passages.
                result = search(query, tier)
                baseline = result.get("retrieval_decision", result["decision"])
                verification = result.get("relevance_verification", {})
                verdict = verification.get("verdict", "NOT_RUN")
                verdict_counts[verdict] += 1
                usage = verification.get("usage") or {}
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
                    f"after={result['decision']} ({'PASS' if after_ok else 'FAIL'}) | relevance={verdict} | latency_ms={verification.get('duration_ms', 'unavailable')} | tokens_in={usage.get('input_tokens', 'unavailable')} | tokens_out={usage.get('output_tokens', 'unavailable')} | tier={tier} | {query}",
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
        print(f"{label} verifier counts: {verdict_counts}", flush=True)
        print(
            f"{label} above threshold: {sum(v >= MIN_SIMILARITY for v in similarities)}/{len(queries)}",
            flush=True,
        )
        extrema[label] = similarities
    for name, operation, label in [
        ("Max original out-of-scope", max, "original-out-of-scope"),
        ("Min original in-scope", min, "original-in-scope"),
        ("Max held-out out-of-scope", max, "held-out-out-of-scope"),
        ("Min V2 in-scope", min, "v2-in-scope"),
        ("Max V2 out-of-scope", max, "v2-out-of-scope"),
    ]:
        if label not in extrema:
            continue
        values = extrema[label]
        print(f"{name} similarity: {operation(values) if values else 'unavailable'}", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--new-only", action="store_true", help="Measure only the frozen V2 15+15 holdout"
    )
    raise SystemExit(main(parser.parse_args().new_only))
