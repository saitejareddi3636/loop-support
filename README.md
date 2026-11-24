# Loopp

A fictional customer-support agent with streaming chat, live voice, policy retrieval, and mock refunds.

Live URL: [https://loopp-support.pages.dev](https://loopp-support.pages.dev)

Demo account: sam.patel@example.test

<!-- TODO: Loom link -->
<!-- TODO: screenshot -->

## Try this

1. Start a conversation: “I'm sam.patel@example.test. List my eligible refunds.”
2. Choose an invoice from the returned list: “Prepare a mock refund for [invoice ID].”
3. Check its invoice and amount, then click **Confirm invoice and amount**. Spoken agreement does not issue a refund.
4. Ask: “What is the Business monthly refund policy?” Inspect the policy citations.
5. Ask an out-of-scope question: “Does Loopp cover a refund for my restaurant meal?”
6. Switch to Voice, allow microphone access, ask a question, and interrupt while the agent speaks.

## Architecture

```mermaid
flowchart LR
    B[Browser] --> P[Cloudflare Pages Function]
    P -->|Gateway secret| C[Caddy]
    C --> A[FastAPI]
    A --> L[LangGraph]
    L --> T[Backend tools]
    L --> V[PostgreSQL / pgvector]
    L --> G[Gemini text and embeddings]
    T --> V
    B <-->|Voice WebSocket via Pages and Caddy| A
    A <-->|Native audio WebSocket| GL[Gemini Live]
```

React/TypeScript renders chat and voice. FastAPI streams text over SSE and bridges
native Gemini Live audio. Both modes share persisted conversations and LangGraph
tools. The gateway secret stays in Pages/Oracle configuration, outside the browser.

Policy documents use section-aware chunks targeting roughly 180–300 estimated
tokens with sentence overlap. Content hashes reuse unchanged 768-dimensional
embeddings. Retrieval filters active versions, dates, and tier before ranking
four matches; policy citations are revalidated against stored source text.

## Guardrail evaluation

These tables record real embedding and verifier calls with `RELEVANCE_VERIFIER=on`.
“Correct” means accepting an in-scope question or rejecting an out-of-scope one;
it does not measure answer correctness. Counts are shown without rounding.
The original set was rerun after the ticket-information prompt clarification.

Original 20-query set:

| Group | Queries | Retrieval-only correct | Verified correct |
| --- | ---: | ---: | ---: |
| In scope | 10 | 10/10 | 10/10 |
| Out of scope | 10 | 9/10 | 10/10 |

Frozen 30-query set, written before measurement and not tuned afterwards:

| Group | Queries | Retrieval-only correct | Verified correct |
| --- | ---: | ---: | ---: |
| In-scope paraphrases | 15 | 15/15 | 13/15 |
| Challenging out-of-scope | 15 | 0/15 | 15/15 |

All 30 frozen queries passed the **0.68** retrieval threshold. The maximum
out-of-scope similarity was **0.789**, above the minimum in-scope similarity of
**0.690**. The scores overlap: raising a similarity cutoff alone cannot separate
these sets without rejecting valid questions. The original set's extrema were
0.743 out of scope and 0.699 in scope.

Two known false rejections remain: the **Pro monthly refund-window** paraphrase
(0.743) and the **subscription-ending process** question (0.707). The verifier is
conservative and adds a model call; the held-out results were not used to tune it.

The verifier runs only after retrieval passes. Timeout, 429, malformed output,
or detected verdict-override instructions fail closed with:
“I couldn't verify that right now. Would you like a human support ticket?”
An escalation offer does not create a ticket without consent.

Set **`RELEVANCE_VERIFIER=off`** to disable the additional relevance model.
This deliberately restores retrieval-only decisions, including their false
positives. The 0.68 threshold, applicability/citation checks, session isolation,
and all backend refund/ticket protections remain enabled. The default is `on`.

## Rate limits and failure handling

- New sessions: 10/minute and 100/day per network peer; 200/minute and 5,000/day globally in production. Existing valid browser sessions are reused.
- Agent requests: 12/minute per session in production; excess requests return 429.
- Text and embedding lookups retry provider 429s, timeouts, and transient upstream failures up to three attempts within a 45-second budget. Valid `Retry-After` instructions are respected; excessive waits return a retry-later state. State-changing tools are not automatically replayed.
- Voice: 30-minute cap, one socket per session and three globally per API worker by default. Page exit or two minutes in a hidden tab releases voice resources.
- Refunds: backend ownership, eligibility, expiration, invoice/amount confirmation, and persisted idempotency checks apply independently of model output.
- Relevance verification: one bounded ten-second call, no automatic retry; failures return the verification-unavailable message above and offer escalation.

## Local setup

Requires Python 3.11+, Node 20+, Docker Compose, and access to the configured Gemini models.

```sh
cp .env.example .env
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

Set the Gemini key and matching database passwords in your local `.env`, then:

```sh
docker compose up -d db
cd backend
../.venv/bin/python migrate.py
../.venv/bin/python seed_data.py
../.venv/bin/python seed_policies.py
../.venv/bin/python refresh_demo.py
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal: `cd frontend && npm ci && npm run dev`.
Open http://127.0.0.1:5173. Operations and the key-rotation warning are in
[deploy/README.md](deploy/README.md); keep secrets, certificates, and backups outside Git.

## Tests

The full backend suite contains **46 tests**: 27 non-DB checks and 19 PostgreSQL
checks. It covers session isolation/quotas, tool recovery, ticket consent,
refund ownership/eligibility/confirmation/idempotency, citation applicability,
verifier on/off modes and failures, trace fields, voice socket limits, and
insert-only daily demo invoices. DB tests create a disposable database; the
configured account needs permission to create/drop it.

From `backend/`:

```sh
RUN_DB_TESTS=1 ../.venv/bin/python -m unittest discover -s tests -v
RUN_DB_TESTS=0 DATABASE_URL=postgresql://unused:unused@127.0.0.1:1/no_database ../.venv/bin/python -m unittest discover -s tests -v
RELEVANCE_VERIFIER=on ../.venv/bin/python eval_guardrail.py
RELEVANCE_VERIFIER=on ../.venv/bin/python eval_guardrail.py --new-only
```

From the repository root:

```sh
(cd frontend && npm run build)
./deploy/smoke_test.sh https://loopp-support.pages.dev
```

The smoke script checks readiness, session cookies, chat creation, and unauthenticated
401 rejection. It makes no agent or refund requests. Automated checks do not
establish physical microphone, speaker playback, mute, or interruption behavior;
those require a manual browser check.

## Model note

Gemini 2.5 Flash-Lite is unavailable to new projects, so the app uses Gemini 3.5 Flash-Lite.
Gemini Live remains the native speech integration.

## Trade-offs and known limits

- Records and financial actions are fictional. Demo email lookup is not authentication, and mock receipts do not transfer money.
- Displayed cost is a text-only estimate. Missing Live audio and embedding costs remain unavailable; a complete session total is not fabricated.
- Spoken refund confirmation is intentionally unsupported. Only the UI button calling the separate server-side endpoint can issue a mock receipt.
- PostgreSQL performs an exact vector scan over 30 chunks from 15 versioned documents. This keeps retrieval simple for a small corpus; larger corpora would need a different indexing trade-off.
