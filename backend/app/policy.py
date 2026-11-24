"""Section-aware Gemini embeddings, exact pgvector retrieval and evidence decisions."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from google import genai
from google.genai import types
from app.config import configured_api_key
from app.db import connect as db
from app.resilience import sync_lookup
from app.policy_relevance import assess_relevance

POLICIES_PATH = Path(__file__).resolve().parents[1] / "policies.json"
EMBED_MODEL = "gemini-embedding-001"
DIMENSIONS = 768
MIN_SIMILARITY = 0.68


def token_estimate(text):
    # Character estimate, not provider-reported token usage.
    return (len(text) + 3) // 4


def document_chunks(doc):
    sections = re.split(r"(?m)^## ", doc["body"])
    units = []
    for section in sections:
        if not section.strip():
            continue
        heading, _, body = section.partition("\n")
        for paragraph in re.split(r"\n\s*\n", body.strip()):
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            part = ""
            for sentence in sentences:
                if token_estimate(part + " " + sentence) > 260 and part:
                    units.append((heading, part.strip()))
                    part = ""
                part += " " + sentence
            if part.strip():
                units.append((heading, part.strip()))
    groups = []
    current = []

    def render(items):
        return "\n\n".join(f"## {h}\n{b}" for h, b in items)

    for unit in units:
        if current and token_estimate(render(current + [unit])) > 300:
            groups.append(current)
            current = []
        current.append(unit)
    if current:
        if (
            groups
            and token_estimate(render(current)) < 120
            and token_estimate(render(groups[-1] + current)) <= 360
        ):
            groups[-1] += current
        else:
            groups.append(current)
    result = []
    previous = ""
    for group in groups:
        content = render(group)
        # Sentence overlap around 30–50 estimated tokens, bounded to the prior section.
        if previous:
            sentences = re.split(r"(?<=[.!?])\s+", previous.split("\n")[-1])
            overlap = ""
            for sentence in reversed(sentences):
                if token_estimate(sentence + " " + overlap) > 50:
                    break
                overlap = sentence + " " + overlap
                if token_estimate(overlap) >= 30:
                    break
            if overlap.strip():
                content = "Previous section context: " + overlap.strip() + "\n\n" + content
        previous = render(group)
        section = " / ".join(dict.fromkeys(h for h, _ in group))
        digest = hashlib.sha256(
            (EMBED_MODEL + "\n" + doc["title"] + "\n" + content).encode()
        ).hexdigest()
        result.append(
            dict(
                chunk_id=f"{doc['document_id']}:v{doc['version']}:c{digest[:12]}",
                section=section,
                content=content,
                content_hash=digest,
            )
        )
    return result


def embed(text, task_type):
    def request(remaining):
        client = genai.Client(
            api_key=configured_api_key(),
            http_options=types.HttpOptions(
                timeout=max(1, min(20000, int(remaining * 1000))),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        try:
            return (
                client.models.embed_content(
                    model=EMBED_MODEL,
                    contents=text,
                    config=types.EmbedContentConfig(
                        task_type=task_type, output_dimensionality=DIMENSIONS
                    ),
                )
                .embeddings[0]
                .values
            )
        finally:
            client.close()

    return sync_lookup(request)


def seed():
    docs = json.loads(POLICIES_PATH.read_text())
    prepared = []
    updated = 0
    with db() as connection:
        existing = {
            row["chunk_id"]: row
            for row in connection.execute("SELECT chunk_id,content_hash FROM knowledge_chunks")
        }
    for doc in docs:
        for chunk in document_chunks(doc):
            vector = None
            if (
                chunk["chunk_id"] not in existing
                or existing[chunk["chunk_id"]]["content_hash"] != chunk["content_hash"]
            ):
                vector = json.dumps(
                    embed(doc["title"] + "\n" + chunk["content"], "RETRIEVAL_DOCUMENT")
                )
                updated += 1
            prepared.append((doc, chunk, vector))
    # Publish the entire new corpus atomically only after every required embedding succeeds.
    with db() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(681202)")
        for d, c, vector in prepared:
            values = (
                c["chunk_id"],
                d["document_id"],
                d["title"],
                d["document_type"],
                d["version"],
                d["tiers"],
                d["effective_date"],
                d["expiration_date"],
                d["active"],
                c["section"],
                d["source_reference"],
                c["content"],
                c["content_hash"],
                EMBED_MODEL,
            )
            if vector:
                connection.execute(
                    """INSERT INTO knowledge_chunks VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                    ON CONFLICT(chunk_id) DO UPDATE SET embedding=EXCLUDED.embedding,content=EXCLUDED.content,content_hash=EXCLUDED.content_hash""",
                    (*values, vector),
                )
            connection.execute(
                "UPDATE knowledge_chunks SET active=%s,tiers=%s,effective_date=%s,expiration_date=%s,title=%s,section=%s,source_reference=%s WHERE chunk_id=%s",
                (
                    d["active"],
                    d["tiers"],
                    d["effective_date"],
                    d["expiration_date"],
                    d["title"],
                    c["section"],
                    d["source_reference"],
                    c["chunk_id"],
                ),
            )
        connection.execute(
            "DELETE FROM knowledge_chunks WHERE NOT(chunk_id=ANY(%s))",
            ([c["chunk_id"] for _, c, _ in prepared],),
        )
        for tier, days, limit, manual, document in [
            ("Free", 0, 0, True, "refunds"),
            ("Pro", 30, 10000, False, "pro-refunds"),
            ("Business", 14, 50000, False, "business-billing"),
            ("Enterprise", 0, 0, True, "enterprise-contracts"),
        ]:
            connection.execute(
                """INSERT INTO refund_rules VALUES (%s,2,TRUE,'2026-11-01',NULL,%s,%s,ARRAY['monthly'],%s,%s)
                ON CONFLICT(tier,version) DO UPDATE SET window_days=EXCLUDED.window_days,max_amount_cents=EXCLUDED.max_amount_cents,manual_review=EXCLUDED.manual_review,billing_cycles=EXCLUDED.billing_cycles""",
                (tier, days, limit, manual, document),
            )
    return {"documents": len(docs), "chunks": len(prepared), "embeddings_updated": updated}


def decision(status, reason, tier=None, sources=None, top=None):
    sources = sources or []
    return {
        "retrieval_status": status,
        "decision": status,
        "low_confidence": status != "GROUNDED",
        "top_similarity": top,
        "threshold": MIN_SIMILARITY,
        "applicable_tier": tier,
        "sources": sources,
        "documents_retrieved": sorted(set(s["document_id"] for s in sources)),
        "active_policy_versions": {s["document_id"]: s["version"] for s in sources},
        "evidence_sufficient": status == "GROUNDED",
        "verification_reason": reason,
    }


def search(query, tier=None):
    if len(query.strip().split()) < 3:
        return decision(
            "NEEDS_CLARIFICATION", "Please describe the policy or issue you want help with.", tier
        )
    tier_sensitive = bool(
        re.search(r"\b(refund|refunds|contract|contracts|entitlement)\b", query, re.I)
    )
    if not tier and tier_sensitive:
        return decision(
            "NEEDS_CLARIFICATION",
            "Please provide your fictional account email so the applicable tier can be checked.",
            tier,
        )
    vector = json.dumps(embed(query, "RETRIEVAL_QUERY"))
    with db() as connection:
        rows = connection.execute(
            """SELECT *,1-(embedding <=> %s::vector) AS similarity FROM knowledge_chunks
            WHERE active AND effective_date<=CURRENT_DATE AND (expiration_date IS NULL OR expiration_date>=CURRENT_DATE)
            AND ('all'=ANY(tiers) OR %s=ANY(tiers)) ORDER BY embedding <=> %s::vector LIMIT 4""",
            (vector, tier, vector),
        ).fetchall()
    sources = [
        {
            k: row[k]
            for k in (
                "chunk_id",
                "document_id",
                "title",
                "document_type",
                "version",
                "tiers",
                "effective_date",
                "expiration_date",
                "section",
                "source_reference",
                "content",
                "similarity",
            )
        }
        for row in rows
        if row["similarity"] >= MIN_SIMILARITY
    ]
    for s in sources:
        s["citation"] = s["chunk_id"]
        s["excerpt"] = s.pop("content")
        s["similarity"] = round(float(s["similarity"]), 3)
        s["effective_date"] = str(s["effective_date"])
        s["expiration_date"] = str(s["expiration_date"]) if s["expiration_date"] else None
    top = round(float(rows[0]["similarity"]), 3) if rows else None
    if not sources:
        return decision(
            "ESCALATION_REQUIRED",
            "No active, applicable passage met the retrieval threshold. I can offer a human support ticket.",
            tier,
            top=top,
        )
    versions = {}
    for s in sources:
        versions.setdefault(s["document_id"], set()).add(s["version"])
    if any(len(v) > 1 for v in versions.values()):
        return decision(
            "ESCALATION_REQUIRED",
            "Conflicting active document versions require review.",
            tier,
            top=top,
        )
    verification = assess_relevance(query, tier, sources)
    if verification.get("verdict") != "YES":
        reason = (
            "The retrieved passages do not address this request within Loopp support. Please clarify the Loopp policy or issue you need help with."
            if verification.get("verdict") == "NO"
            else "The additional policy relevance check is unavailable. I cannot ground this answer right now; please try again later."
        )
        result = decision("ESCALATION_REQUIRED", reason, tier, top=top)
    else:
        result = decision(
            "GROUNDED",
            "Retrieved current, applicable passages above the threshold and verified their relevance to this request. Policy output is restricted to these source excerpts; relevance does not establish answer correctness or refund eligibility.",
            tier,
            sources,
            top,
        )
    result["retrieval_decision"] = "GROUNDED"
    result["relevance_verification"] = verification
    return result


def validate_evidence(result):
    if result["decision"] != "GROUNDED":
        return False
    if result.get("relevance_verification", {"verdict": "YES"}).get("verdict") != "YES":
        return False
    for s in result["sources"]:
        if s["effective_date"] > date.today().isoformat() or (
            s["expiration_date"] and s["expiration_date"] < date.today().isoformat()
        ):
            return False
        if "all" not in s["tiers"] and result["applicable_tier"] not in s["tiers"]:
            return False
        with db() as connection:
            row = connection.execute(
                "SELECT content,active,version,tiers,effective_date,expiration_date FROM knowledge_chunks WHERE chunk_id=%s",
                (s["citation"],),
            ).fetchone()
            versions = connection.execute(
                "SELECT count(DISTINCT version) AS n FROM knowledge_chunks WHERE document_id=%s AND active AND effective_date<=CURRENT_DATE AND (expiration_date IS NULL OR expiration_date>=CURRENT_DATE)",
                (s["document_id"],),
            ).fetchone()["n"]
        if versions != 1:
            return False
        if (
            not row
            or not row["active"]
            or row["content"] != s["excerpt"]
            or row["version"] != s["version"]
        ):
            return False
        if row["effective_date"] > date.today() or (
            row["expiration_date"] and row["expiration_date"] < date.today()
        ):
            return False
        if "all" not in row["tiers"] and result["applicable_tier"] not in row["tiers"]:
            return False
    return bool(result["sources"])


def selected_passages(result, query="", limit=3):
    """Select complete source sentences; never generate or paraphrase a policy claim."""
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "what",
        "are",
        "can",
        "please",
        "from",
        "this",
        "that",
        "my",
        "your",
        "about",
        "current",
        "policy",
        "rules",
        "subscription",
        "invoice",
        "invoices",
    }
    terms = {
        word.rstrip("s")
        for word in re.findall(r"[a-z]+", query.lower())
        if len(word) > 2 and word not in stopwords
    }
    candidates = []
    for rank, source in enumerate(result["sources"]):
        specific = "all" not in source["tiers"] and result.get("applicable_tier") in source["tiers"]
        for paragraph in source["excerpt"].split("\n"):
            if not paragraph.strip() or paragraph.startswith(("##", "Previous section context:")):
                continue
            for position, sentence in enumerate(re.split(r"(?<=[.!?])\s+", paragraph.strip())):
                mentioned_tiers = set(re.findall(r"\b(Free|Pro|Business|Enterprise)\b", sentence))
                if (
                    mentioned_tiers
                    and result.get("applicable_tier")
                    and result["applicable_tier"] not in mentioned_tiers
                ):
                    continue
                words = {word.rstrip("s") for word in re.findall(r"[a-z]+", sentence.lower())}
                score = (
                    3 * len(terms & words) + (8 if specific else 0) - rank * 0.05 - position * 0.01
                )
                if re.search(r"refund", query, re.I) and "refund" in words:
                    score += 4
                candidates.append((score, sentence, source))
    chosen = []
    seen = set()
    for _, sentence, source in sorted(candidates, key=lambda item: item[0], reverse=True):
        if sentence in seen:
            continue
        seen.add(sentence)
        chosen.append((sentence, source))
        if len(chosen) >= limit:
            break
    return chosen


def grounded_answer(result, query="", max_sentences=3):
    if result["decision"] != "GROUNDED":
        return result["verification_reason"] + (
            " Would you like a human support ticket?"
            if result["decision"] == "ESCALATION_REQUIRED"
            and "Would you like" not in result["verification_reason"]
            else ""
        )
    if not validate_evidence(result):
        return "I cannot verify the current policy evidence. Would you like a human support ticket?"
    selected = selected_passages(result, query, max_sentences)
    if not selected:
        return "I cannot find a supporting policy passage. Would you like a human support ticket?"
    # Each displayed sentence remains verbatim and cites the actual retrieved chunk.
    return "\n\n".join(f"{sentence} [{source['citation']}]" for sentence, source in selected)


def active_refund_rule(tier, connection):
    return connection.execute(
        """SELECT * FROM refund_rules WHERE tier=%s AND active AND effective_date<=CURRENT_DATE
        AND (expiration_date IS NULL OR expiration_date>=CURRENT_DATE) ORDER BY version DESC LIMIT 1 FOR SHARE""",
        (tier,),
    ).fetchone()
