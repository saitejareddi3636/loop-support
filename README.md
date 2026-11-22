# Loopp

Loopp is a customer-support demo with text chat and live voice.
Both interfaces use the same LangGraph workflow, conversation history,
policy retrieval, and backend tools. Customer records and policies are fictional.
Refunds create mock receipts; no money moves. Email lookup is not authentication.

## Architecture

- React and TypeScript provide chat, voice controls, transcripts, and tool activity.
- FastAPI streams chat over SSE and bridges browser audio to Gemini Live over WebSockets.
- Gemini Live handles native speech; Gemini 3.5 Flash-Lite drives the text agent.
- LangGraph selects account, invoice, policy, refund, and escalation tools.
- PostgreSQL stores application state and billing records; pgvector stores policies.

API routes, agent orchestration, tools, and retrieval are separate backend modules.
Voice and text share a session. Refund ownership, eligibility, confirmation, and
idempotency are enforced by backend code, independently of model output.

## Retrieval and trade-offs

The knowledge base contains 15 versioned support documents. The section-aware
splitter preserves headings and paragraph boundaries, targets roughly 180–300
estimated tokens, and overlaps complete sentences where practical. Token sizes
use a character-based estimate rather than a model tokenizer; some chunks fall
outside the target range to avoid tiny fragments. Stable content hashes let
unchanged chunks reuse their embeddings.

Gemini embeddings use 768 dimensions. Search filters by active version, effective
and expiration dates, and subscription tier before ranking the top four matches
by cosine similarity. Results below 0.68 are rejected. This threshold measures
retrieval relevance, not the probability that an answer is correct.

The corpus has 30 chunks, so retrieval uses an exact vector scan rather than an
approximate index. Policy answers select cited source sentences and revalidate
their text and metadata. This limits unsupported paraphrasing at the cost of
less flexible answers. Account and invoice facts come from database queries,
not vector search. Reported API costs cover text calls only; missing audio and
embedding costs are shown as unavailable.

## Local setup

Requirements: Python 3.11+, Node 20+, Docker Compose, and a Gemini API key with
access to the configured text, embedding, and Live models.

From the repository root:

```sh
cp .env.example .env
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
```

Edit `.env`: set `GEMINI_API_KEY` and replace the database password in both
`POSTGRES_PASSWORD` and `DATABASE_URL`. Keep this file local.

Start the database and backend:

```sh
docker compose up -d db
cd backend
../.venv/bin/python migrate.py
../.venv/bin/python seed_data.py
../.venv/bin/python seed_policies.py
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, from the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. Allow microphone access for voice; use localhost or HTTPS.
Try `sam.patel@example.test` and ask which invoices qualify for a mock refund.
The deterministic seed dates are fixed; eligibility uses the current server date.

Run the focused checks from `backend/` with
`RUN_DB_TESTS=1 ../.venv/bin/python -m unittest discover -s tests -v`,
and build from `frontend/` with `npm run build`. Database tests create and remove
a disposable database using the configured PostgreSQL account. Real microphone,
mute, and interruption behavior require a manual browser check.
