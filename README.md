# Loopp
[Live demo](https://loopp-support.pages.dev)

Loopp is a fictional customer-support demo with streaming text chat and native
voice. Both modes share a LangGraph agent, conversation history, tools, and
policy retrieval. Refunds create mock receipts; no money moves. Email lookup
is demo identity resolution, not real authentication.

## Architecture

React and TypeScript provide the UI. FastAPI streams chat over SSE and bridges
browser audio to Gemini Live over WebSockets. Gemini 3.5 Flash-Lite drives
LangGraph; PostgreSQL stores sessions, invoices, receipts, and tickets, with
pgvector for policy retrieval. API routes, orchestration, and tools are separate.
Gemini 3.5 Flash-Lite replaced 2.5 Flash-Lite because 2.5 was unavailable to this API project.

## Retrieval and trade-offs

The corpus has 15 versioned documents and 30 chunks. Section-aware chunking
preserves headings and paragraphs, targets roughly 180–300 estimated tokens,
and overlaps complete sentences. Character-based estimates avoid a tokenizer
dependency; some chunks exceed the target to avoid small fragments. Content
hashes let unchanged chunks reuse their 768-dimensional Gemini embeddings.

Search filters active versions, effective dates, and subscription tier, then
ranks four matches using exact cosine search. The corpus is too small to need
an approximate index. The threshold stays **0.68** and measures relevance,
not answer correctness. A second, bounded yes/no model call must also verify
that the retrieved passages address the actual Loopp request. A negative, malformed,
or unavailable verdict returns the existing escalation decision without creating
a ticket. The check cannot promote a below-threshold result or authorize refunds.
Cited policy sentences are revalidated against sources;
this restricts paraphrasing. Invoice facts come from SQL, not vector search.
API cost reporting is a text-only estimate; missing costs remain unavailable.

## Local setup

Requires Python 3.11+, Node 20+, Docker Compose, and access to the configured
Gemini text, Live, and embedding models. From the repository root:

```sh
cp .env.example .env
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

Set `GEMINI_API_KEY` and matching database passwords in `.env`; keep it local.

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
Open http://127.0.0.1:5173. Microphone capture requires localhost or HTTPS.
Deployment commands and backup details are in [deploy/README.md](deploy/README.md).

## Try this

1. Start a new conversation: “I'm sam.patel@example.test. Check my account.”
2. Ask: “Which invoices are eligible for a mock refund?”
3. Name an invoice from the returned list, then click **Confirm invoice and amount**.
4. Ask about the Business monthly refund policy and inspect its citations.
5. Switch to Voice to continue the same conversation; allow microphone access.

`refresh_demo.py` adds today's paid demo invoices for Sam and Alex (`alex.morgan@example.test`).
IDs are `INV-D<YYYYMMDD>-1` and `-2`. Runs are insert-only and idempotent;
existing invoices and receipts are never reset. The six-hour maintenance job
runs it. Eligibility still uses current account state and refund rules.
Refund confirmation remains a separate server endpoint called by the UI button.
Spoken agreement does not issue a refund.

## Rate limits and failure handling

Production defaults limit new sessions to 10/minute and 100/day per network peer,
plus 200/minute and 5,000/day globally. Valid browser sessions are reused.
Agent requests are limited to 12/minute per session. Provider calls attempt up
to three requests within a 45-second budget; longer Retry-After instructions
return a retry-later state. The graph budget is 150 seconds and voice sessions
have a 30-minute cap. Each API worker allows one voice socket per session and
three globally by default. Voice releases audio on page exit or after two minutes
in a hidden tab. Reconnects preserve history. Refund transactions validate
ownership, eligibility, expiration, and exact confirmation; unique persisted
receipts make refunds idempotent. Mutations are not automatically replayed.

## Guardrail evaluation

From `backend/`, run `../.venv/bin/python eval_guardrail.py` after seeding policies.
It sends the original 10 in-scope and 10 out-of-scope queries plus 10 fixed
held-out out-of-scope queries through `app.policy.search` with real embeddings.
It reports retrieval-only and verified decisions on the same passages, each
group's before/after pass rates, and similarity extrema. The second check uses
Gemini 3.5 Flash-Lite with a ten-second timeout and no automatic retries. Provider
failures fail closed but do not count as semantic evaluation successes. In-scope
passes require GROUNDED; out-of-scope passes require rejection. This conservative
check can reject valid questions. A second frozen set adds 15 in-scope
paraphrases and 15 challenging out-of-scope queries; run only those with
`eval_guardrail.py --new-only`. Cases are never resampled after measurement.
Timeouts, provider 429s, malformed output, and detected verdict-override
instructions fail closed with “I couldn't verify that right now” and an
escalation offer. Only attempted provider calls emit model-completed trace
events with measured latency and available provider-reported tokens.
This is a small retrieval check, not a measure of answer correctness. Record
measured results below; no scores are prefilled.

| Group | Queries | Before | After |
| --- | --- | --- | --- |
| Original in scope | 10 | | |
| Original out of scope | 10 | | |
| Held-out out of scope | 10 | | |
| V2 in scope | 15 | | |
| V2 out of scope | 15 | | |

## Tests

The focused suite contains 42 tests, including 19 database checks.
From `backend/`: `RUN_DB_TESTS=1 ../.venv/bin/python -m unittest discover -s tests -v`.
Database tests create and remove a disposable database; the account needs that privilege.
Use `RUN_DB_TESTS=0` for non-DB checks without a running database.
From `frontend/`: `npm run build`.
Physical microphone capture, playback, mute, and interruption require a manual browser check;
automated checks do not establish that those behaviors work.
