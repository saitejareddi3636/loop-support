#!/usr/bin/env python3
"""Run independent Loopp agent scenarios against the live Gemini model."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

EVAL_DB = os.getenv(
    "LOOPP_EVAL_DATABASE_URL",
    "postgresql://loopp:cursortest@127.0.0.1:55499/loopp_eval",
)
os.environ["DATABASE_URL"] = EVAL_DB
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("RELEVANCE_VERIFIER", "on")

from app.agent.runner import run_turn  # noqa: E402
from app.config import configured_api_key  # noqa: E402
from app.db import connect  # noqa: E402
from app.storage import (  # noqa: E402
    create_chat,
    new_session,
    session_id,
    set_verified_customer,
    get_chat,
)


def load_scenarios(path: Path) -> dict:
    return json.loads(path.read_text())


def customer_id_for_email(email: str | None) -> str | None:
    if not email:
        return None
    with connect() as db:
        row = db.execute("SELECT customer_id FROM customers WHERE email=%s", (email,)).fetchone()
    return row["customer_id"] if row else None


def normalize(text: str) -> str:
    return text.lower().replace("’", "'").replace("`", "")


def includes_any(text: str, options: list[str] | None) -> bool:
    if not options:
        return True
    lower = normalize(text)
    return any(normalize(option) in lower for option in options)


def includes_none(text: str, options: list[str] | None) -> bool:
    if not options:
        return True
    lower = normalize(text)
    return all(normalize(option) not in lower for option in options)


async def run_turn_collect(chat_id: str, text: str, api_key: str) -> dict:
    started = time.perf_counter()
    answer = ""
    tools_started: list[str] = []
    tools_completed: list[str] = []
    tools_succeeded: list[str] = []
    usage = []
    costs = []
    errors = []
    events = []
    async for event in run_turn(chat_id, text, api_key):
        events.append(event)
        kind = event.get("type")
        if kind == "tool_started":
            tools_started.append(event.get("name", ""))
        elif kind in ("tool_completed", "tool_failed", "tool_blocked"):
            tools_completed.append(event.get("name", ""))
            # Consent blocks and hard failures are not successful capability use.
            if kind == "tool_completed" and not (event.get("result") or {}).get("error"):
                tools_succeeded.append(event.get("name", ""))
        elif kind == "model_completed":
            if event.get("usage"):
                usage.append(event["usage"])
            if event.get("estimated_cost_usd") is not None:
                costs.append(event["estimated_cost_usd"])
        elif kind == "complete":
            answer = event.get("answer") or ""
        elif kind in ("error", "agent_error"):
            errors.append(event.get("message") or kind)
            if not answer:
                answer = event.get("message") or ""
    return {
        "answer": answer,
        "tools_started": tools_started,
        "tools_completed": tools_completed,
        "tools_succeeded": tools_succeeded,
        "usage": usage,
        "costs": costs,
        "errors": errors,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "event_types": [e.get("type") for e in events],
    }


def score(result: dict, expect: dict, chat_id: str) -> tuple[bool, list[str]]:
    reasons = []
    answer = result["answer"] or ""
    tools = set(result["tools_completed"] or result["tools_started"])
    # Negative tool asserts use successful completions only (blocked consent is not success).
    succeeded = set(result.get("tools_succeeded") or [])
    chat = get_chat(chat_id) or {}
    # Outcome checks may look across the whole assistant history for memory cases.
    history = "\n".join(
        m["content"] for m in (chat.get("messages") or []) if m.get("role") == "assistant"
    )
    corpus = answer + "\n" + history

    if expect.get("tools_any") and not any(t in tools for t in expect["tools_any"]):
        reasons.append(f"missing tools_any={expect['tools_any']}; got={sorted(tools)}")
    if expect.get("tools_none"):
        bad = [t for t in expect["tools_none"] if t in succeeded]
        if bad:
            reasons.append(f"forbidden tools present: {bad}")
    if expect.get("must_include_any") and not includes_any(corpus, expect["must_include_any"]):
        reasons.append(f"missing must_include_any={expect['must_include_any']}")
    if expect.get("must_not_include_any") and not includes_none(
        corpus, expect["must_not_include_any"]
    ):
        hit = [x for x in expect["must_not_include_any"] if normalize(x) in normalize(corpus)]
        reasons.append(f"forbidden text present: {hit}")

    receipts = chat.get("refund_receipts") or []
    if expect.get("no_receipt") and receipts:
        reasons.append(f"unexpected receipts: {[r.get('receipt_id') for r in receipts]}")
    if expect.get("refund_pending"):
        with connect() as db:
            pending = db.execute(
                "SELECT invoice_id FROM refund_pending WHERE chat_id=%s AND expires_at>now()",
                (chat_id,),
            ).fetchone()
        if not pending:
            reasons.append("expected refund_pending row")
    if expect.get("expect_ticket"):
        if "TKT" not in answer.upper() and not any(
            "TKT" in (r.get("content") or "").upper()
            for r in chat.get("messages") or []
            if r.get("role") == "assistant"
        ):
            reasons.append("expected ticket reference")
    if result["errors"] and not answer:
        reasons.append(f"turn errors: {result['errors']}")
    return not reasons, reasons


async def run_scenario(scenario: dict, api_key: str) -> dict:
    owner = session_id(new_session())
    chat = create_chat(owner)
    chat_id = chat["id"]
    email = scenario.get("email")
    customer_id = customer_id_for_email(email)
    turns_out = []
    final = {
        "answer": "",
        "tools_started": [],
        "tools_completed": [],
        "usage": [],
        "costs": [],
        "errors": [],
        "latency_ms": 0,
    }

    # Establish identity without spending a scored model turn when email is known.
    if customer_id:
        set_verified_customer(chat_id, customer_id)

    for text in scenario["turns"]:
        # If the scenario itself is testing identity, do not pre-verify.
        if scenario.get("email") is None and "@" in text:
            pass
        turn = await run_turn_collect(chat_id, text, api_key)
        turns_out.append({"user": text, **turn})
        final = turn
        # accumulate tools across the conversation for multi-turn expects
        final = {
            **turn,
            "tools_started": [t for tr in turns_out for t in tr["tools_started"]],
            "tools_completed": [t for tr in turns_out for t in tr["tools_completed"]],
            "tools_succeeded": [t for tr in turns_out for t in tr.get("tools_succeeded", [])],
            "usage": [u for tr in turns_out for u in tr["usage"]],
            "costs": [c for tr in turns_out for c in tr["costs"]],
            "latency_ms": sum(tr["latency_ms"] for tr in turns_out),
        }

    if scenario.get("confirm_refund"):
        with connect() as db:
            pending = db.execute(
                """SELECT token,invoice_id,amount_cents FROM refund_pending
                   WHERE chat_id=%s AND expires_at>now() ORDER BY expires_at DESC LIMIT 1""",
                (chat_id,),
            ).fetchone()
        if pending:
            from app.refunds import confirm_refund

            confirm_refund(
                chat_id, pending["token"], pending["invoice_id"], pending["amount_cents"]
            )

    passed, reasons = score(final, scenario.get("expect") or {}, chat_id)
    tokens_in = 0
    tokens_out = 0
    for usage in final["usage"]:
        if isinstance(usage, dict):
            tokens_in += int(usage.get("input_tokens") or usage.get("prompt_token_count") or 0)
            tokens_out += int(usage.get("output_tokens") or usage.get("candidates_token_count") or 0)
    return {
        "id": scenario["id"],
        "category": scenario.get("category"),
        "email": email,
        "pass": passed,
        "reasons": reasons,
        "answer": final["answer"],
        "tools": final["tools_completed"],
        "latency_ms": final["latency_ms"],
        "input_tokens": tokens_in or None,
        "output_tokens": tokens_out or None,
        "estimated_cost_usd": round(sum(final["costs"]), 6) if final["costs"] else None,
        "turns": [
            {
                "user": t["user"],
                "answer": t["answer"],
                "tools": t["tools_completed"],
                "latency_ms": t["latency_ms"],
            }
            for t in turns_out
        ],
    }


def summarize(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    by_category: dict[str, dict] = {}
    for r in results:
        bucket = by_category.setdefault(r.get("category") or "unknown", {"pass": 0, "fail": 0})
        bucket["pass" if r["pass"] else "fail"] += 1
    costs = [r["estimated_cost_usd"] for r in results if r.get("estimated_cost_usd") is not None]
    latencies = [r["latency_ms"] for r in results]
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0,
        "by_category": by_category,
        "latency_ms_avg": round(sum(latencies) / len(latencies)) if latencies else None,
        "estimated_cost_usd_sum": round(sum(costs), 6) if costs else None,
        "failed_ids": [r["id"] for r in results if not r["pass"]],
    }


def render_markdown(summary: dict, results: list[dict], title: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"- Total: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Pass rate: {summary['pass_rate']:.1%}",
        f"- Avg latency: {summary['latency_ms_avg']} ms",
        f"- Text-only estimated cost sum: {summary['estimated_cost_usd_sum']}",
        "",
        "## By category",
        "",
    ]
    for category, counts in sorted(summary["by_category"].items()):
        lines.append(f"- {category}: {counts['pass']} pass / {counts['fail']} fail")
    lines += ["", "## Failures", ""]
    failures = [r for r in results if not r["pass"]]
    if not failures:
        lines.append("None.")
    for r in failures:
        lines.append(f"### {r['id']}")
        lines.append(f"- reasons: {r['reasons']}")
        lines.append(f"- tools: {r['tools']}")
        lines.append(f"- answer: {r['answer'][:500]}")
        lines.append("")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenarios",
        default=str(ROOT / "evaluation" / "scenarios.json"),
    )
    parser.add_argument("--held-out", action="store_true")
    parser.add_argument("--unseen", action="store_true")
    parser.add_argument("--only-failed", help="Path to prior results JSON")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--ids", nargs="*", default=[])
    args = parser.parse_args()

    api_key = configured_api_key()
    if not api_key:
        print("GEMINI_API_KEY is not configured", file=sys.stderr)
        return 2

    with connect() as db:
        n = db.execute("SELECT count(*) AS n FROM customers").fetchone()["n"]
        if n < 20:
            print(f"Eval DB looks empty ({n} customers). Seed loopp_eval first.", file=sys.stderr)
            return 2

    payload = load_scenarios(Path(args.scenarios))
    if args.held_out:
        scenarios = payload["held_out"]
        label = "held_out"
    elif args.unseen:
        scenarios = payload.get("unseen") or []
        label = "unseen"
    else:
        scenarios = payload["scenarios"]
        label = "primary"
    if args.only_failed:
        prior = json.loads(Path(args.only_failed).read_text())
        failed_ids = set(prior.get("summary", {}).get("failed_ids") or [])
        all_scenarios = (
            payload["scenarios"] + payload.get("held_out", []) + payload.get("unseen", [])
        )
        scenarios = [s for s in all_scenarios if s["id"] in failed_ids]
        label = "rerun_failed"
    if args.ids:
        wanted = set(args.ids)
        all_scenarios = (
            payload["scenarios"] + payload.get("held_out", []) + payload.get("unseen", [])
        )
        scenarios = [s for s in all_scenarios if s["id"] in wanted]
        label = "selected"
    if args.limit:
        scenarios = scenarios[: args.limit]
    print(f"Running {len(scenarios)} {label} scenarios on {EVAL_DB}")
    results = []
    for index, scenario in enumerate(scenarios, 1):
        print(f"[{index}/{len(scenarios)}] {scenario['id']} ...", flush=True)
        try:
            result = await run_scenario(scenario, api_key)
        except Exception as exc:  # noqa: BLE001 - record and continue the suite
            result = {
                "id": scenario["id"],
                "category": scenario.get("category"),
                "email": scenario.get("email"),
                "pass": False,
                "reasons": [f"exception: {exc}"],
                "answer": "",
                "tools": [],
                "latency_ms": None,
                "input_tokens": None,
                "output_tokens": None,
                "estimated_cost_usd": None,
                "turns": [],
            }
        results.append(result)
        status = "PASS" if result["pass"] else "FAIL"
        print(f"  {status} ({result.get('latency_ms')} ms) {result.get('reasons') or ''}")

    summary = summarize(results)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "evaluation" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "generated_at": stamp,
        "database": re.sub(r":[^:@/]+@", ":***@", EVAL_DB),
        "suite": label,
        "model": os.getenv("GEMINI_TEXT_MODEL"),
        "summary": summary,
        "results": results,
    }
    json_path = out_dir / f"{label}-{stamp}.json"
    latest_name = {
        "held_out": "held_out-latest",
        "unseen": "unseen-latest",
        "primary": "latest",
    }.get(label, f"{label}-latest")
    latest_json = out_dir / f"{latest_name}.json"
    latest_md = out_dir / f"{latest_name}.md"
    json_path.write_text(json.dumps(doc, indent=2))
    latest_json.write_text(json.dumps(doc, indent=2))
    latest_md.write_text(render_markdown(summary, results, f"Loopp evaluation ({label})"))
    print(json.dumps(summary, indent=2))
    print(f"Wrote {json_path}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
