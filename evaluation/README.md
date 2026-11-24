# Loopp agent evaluation

Independent, measured checks against the **live** Gemini text model and the full agent pipeline (`run_turn` → LangGraph → tools → compose). The runner does not mock the model.

Use an isolated PostgreSQL database so evaluation does not disturb a local UI session.

## What it measures

Each scenario is a short multi-turn conversation for a seeded fictional customer. After the final turn the runner records:

- assistant answer
- tools started / completed / succeeded
- pass/fail against programmatic expectations (required/forbidden substrings and tools, refund staging vs issuance, ticket presence, negative refund-offer checks)
- wall-clock latency
- provider-reported text token usage and the app’s text-only cost estimate when present

Expectations check **outcomes**, not canned wording.

**Not covered here:** Gemini Live audio, microphone, speaker playback, mute, or spoken interruption. Those require manual browser validation (see main README).

## Suites

| Suite | Source | Purpose |
| --- | --- | --- |
| Primary | `scenarios` in `scenarios.json` | Broad coverage of account, billing, policy, ownership, multi-intent, memory, safeguards |
| Held-out | `held_out` | Reserved scenarios for final validation |
| Unseen | `unseen` | Conversational generalization and negative asserts (unsolicited refunds, abandoned intents) |

## Database

Point `LOOPP_EVAL_DATABASE_URL` (or `DATABASE_URL` for one-off setup) at a dedicated database, then migrate and seed:

```sh
# example against local Compose Postgres on 55432
createdb -h 127.0.0.1 -p 55432 -U loopp loopp_eval   # or equivalent

cd backend
DATABASE_URL=postgresql://loopp:YOUR_PASSWORD@127.0.0.1:55432/loopp_eval ../.venv/bin/python migrate.py
DATABASE_URL=postgresql://loopp:YOUR_PASSWORD@127.0.0.1:55432/loopp_eval ../.venv/bin/python seed_data.py
DATABASE_URL=postgresql://loopp:YOUR_PASSWORD@127.0.0.1:55432/loopp_eval ../.venv/bin/python seed_policies.py
DATABASE_URL=postgresql://loopp:YOUR_PASSWORD@127.0.0.1:55432/loopp_eval ../.venv/bin/python refresh_demo.py
```

The runner defaults to `LOOPP_EVAL_DATABASE_URL` when set (see `run_eval.py`).

## Run

From the repository root, with `GEMINI_API_KEY` in `.env`:

```sh
.venv/bin/python evaluation/run_eval.py
.venv/bin/python evaluation/run_eval.py --only-failed evaluation/results/latest.json
.venv/bin/python evaluation/run_eval.py --held-out
.venv/bin/python evaluation/run_eval.py --unseen
```

Results land under `evaluation/results/` as timestamped JSON plus `*-latest.json` / `*.md` summaries. Refunds are confirmed only when a scenario sets `confirm_refund: true`.

## Interpreting failures

- **tool selection** — wrong tool, or eligibility dump for a non-refund ask
- **facts** — missing invoice/amount/tier that tools or context returned
- **safeguards** — receipt without confirmation, cross-customer access, unsolicited refund pitch
- **scope** — out-of-scope ask answered as if Loopp policy applied
- **memory / multi-intent** — prior ticket/invoice forgotten, or a secondary ask dropped

Fix architecture, re-run `--only-failed`, then held-out / unseen. Do not tune scenarios to a single model wording.

## Measured results

Isolated eval DB, live `gemini-3.5-flash-lite`, no model mocks. Figures below are from the saved final reports (2026-11-24).

| Suite | Cases | Pass | Pass rate | Avg latency | Text-only cost sum | Canonical artifact |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Primary | 80 | 80 | 100% | ~1970 ms | ~$0.092 | `results/primary-final.json` / `latest.json` |
| Held-out | 12 | 12 | 100% | ~3123 ms | ~$0.021 | `results/held_out-latest.json` |
| Unseen | 8 | 8 | 100% | ~5622 ms | ~$0.016 | `results/unseen-latest.json` |

**Progress evidence retained (not deleted):**

| Artifact | Result | Why kept |
| --- | --- | --- |
| `primary-20261124T053115Z.json` | 69/80 | Early baseline after conversation refactor |
| `primary-20261124T054928Z.json` | 78/80 | Mid-pass before compose/scorer fixes |
| `held_out-20261124T055335Z.json` | 11/12 | Held-out ownership phrasing miss |
| `unseen-20261124T055405Z.json` | 7/8 | Unseen suspension follow-up miss |
| `rerun_failed-latest.json` | 2/2 | Confirmation that the last primary failures cleared |

Backend unit/regression suite (separate from this runner): **74 tests OK** including DB tests. See main README.

### Remaining limitations

- Live/voice microphone, interruption, and mute are out of scope for this runner.
- Text-only cost estimates exclude Live audio and embeddings.
- Model wording varies; assertions check outcomes, not scripts.
- A few social greetings remain scripted in `social_reply`.
- Off-domain asks rephrased into Loopp policy language can still stress the relevance verifier.
