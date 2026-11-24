"""Small manual retrieval evaluation. Uses real app.policy.search embeddings."""
from app.policy import search, MIN_SIMILARITY

IN_SCOPE = (
    ('Pro', 'What is the refund window for a Pro monthly subscription?'),
    ('Business', 'What amount limit applies to Business monthly refunds?'),
    ('Free', 'Can Free accounts receive automatic subscription refunds?'),
    ('Enterprise', 'Do Enterprise contract refunds need human review?'),
    ('Pro', 'What happens to refunds for an annual Pro subscription?'),
    ('Business', 'How should I report a suspected duplicate invoice charge?'),
    ('Pro', 'How do I troubleshoot problems logging into my account?'),
    ('Business', 'What should I check when account activation fails?'),
    ('Pro', 'What information is needed to create a support ticket?'),
    ('Business', 'Can the agent change my subscription usage limits?'),
)
OUT_OF_SCOPE = (
    ('Pro', 'Can you refund my airline ticket after a flight cancellation?'),
    ('Business', 'Does Loopp cover a refund for my restaurant bill?'),
    ('Pro', 'Can you approve a refund for a broken laptop from another retailer?'),
    ('Business', 'What travel insurance policy covers my lost luggage?'),
    ('Pro', 'Which medication should I take for a persistent headache?'),
    ('Business', 'How do I refinance a mortgage on my house?'),
    ('Pro', 'Who won the most recent football championship?'),
    ('Business', 'What will the weather be tomorrow in Chicago?'),
    ('Pro', 'How do I bake a sourdough loaf at home?'),
    ('Business', 'Write a Python algorithm to solve a Sudoku puzzle.'),
)


def main():
    errors = 0
    for label, queries, expected in [('in-scope', IN_SCOPE, True), ('out-of-scope', OUT_OF_SCOPE, False)]:
        passed = 0
        print(f"\n{label}: expected {'GROUNDED' if expected else 'not GROUNDED'}", flush=True)
        for number, (tier, query) in enumerate(queries, 1):
            try:
                result = search(query, tier)
                ok = (result['decision'] == 'GROUNDED') == expected
                passed += ok
                similarity = result['top_similarity']
                print(f"{number:02d} {'PASS' if ok else 'FAIL'} | similarity={similarity if similarity is not None else 'unavailable'} | threshold={result['threshold']} | decision={result['decision']} | tier={tier} | {query}", flush=True)
            except Exception as error:
                errors += 1
                print(f"{number:02d} ERROR | similarity=unavailable | threshold={MIN_SIMILARITY} | decision=ERROR ({type(error).__name__}) | {query}", flush=True)
        print(f"{label} pass rate: {passed}/{len(queries)} ({100*passed/len(queries):.1f}%)", flush=True)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
