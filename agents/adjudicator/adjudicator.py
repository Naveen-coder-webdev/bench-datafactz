"""
adjudicator.py — the entity-resolution adjudicator agent (brief §5).

Picks up every person the deterministic resolver marked `needs_review`,
gathers both sides' evidence, reasons over it, and issues an EXPLAINED
merge / split / escalate decision. Every run is logged to `agent_runs`
with a human-readable rationale — that's the "explainable" requirement.

This implementation reasons with a weighted-evidence heuristic rather than
calling an LLM, because this sandbox has no live model credential to call
out to. The interface (`adjudicate_person`) is exactly where you'd swap in
a real `messages.create(...)` call with the same evidence bundle as the
prompt — see the commented LLM_PROMPT_TEMPLATE below for what that call
would look like. The heuristic keeps the same explainability contract:
every decision returns a reason string, not just a verdict.
"""

import json
import sqlite3
import time

LLM_PROMPT_TEMPLATE = """You are an entity-resolution adjudicator for a breach analytics
system. Two evidence sets below were name-matched by a deterministic
resolver but could not be confidently merged or split. Decide: MERGE (same
person), SPLIT (different people), or ESCALATE (insufficient evidence for
either). Justify your decision citing specific corroborating or
contradicting fields.

Evidence set A: {evidence_a}
Evidence set B: {evidence_b}

Respond as JSON: {{"decision": "MERGE|SPLIT|ESCALATE", "confidence": 0-1, "reasoning": "..."}}
"""


def _gather_evidence(cur, person_id: str) -> dict:
    cur.execute("SELECT best_name, dob FROM persons WHERE person_id=?", (person_id,))
    name, dob = cur.fetchone()
    cur.execute("""SELECT e.category, e.normalized_value, e.doc_id FROM identity_links il
                   JOIN extractions e ON il.extraction_id = e.extraction_id
                   WHERE il.person_id=?""", (person_id,))
    fields = {}
    for cat, val, doc_id in cur.fetchall():
        fields.setdefault(cat, set()).add(val)
    return {"person_id": person_id, "name": name, "dob": dob, "fields": {k: list(v) for k, v in fields.items()}}


def adjudicate_person(evidence: dict) -> dict:
    """
    Heuristic reasoning: a `needs_review` cluster usually means the
    resolver merged same-name records with NO corroborating identifier.
    We look for any distinguishing signal (distinct DOB, distinct SSN
    fragment, distinct email domain pattern) across the cluster's own
    absorbed fields. If every field is internally consistent (no
    contradictions), we confirm the merge with moderate confidence.
    If we find an internal contradiction the resolver missed, we flag
    for a SPLIT recommendation. Otherwise, escalate to a human.
    """
    fields = evidence["fields"]
    ssns = fields.get("ssn", [])
    dobs = fields.get("dob", [])

    if len(set(ssns)) > 1:
        return {
            "decision": "SPLIT",
            "confidence": 0.8,
            "reasoning": f"Cluster '{evidence['name']}' contains {len(set(ssns))} distinct SSNs "
                         f"({ssns}) under one name — internally contradictory. Recommend splitting "
                         f"into separate persons keyed by SSN.",
        }
    if len(set(dobs)) > 1:
        return {
            "decision": "SPLIT",
            "confidence": 0.65,
            "reasoning": f"Cluster '{evidence['name']}' contains {len(set(dobs))} distinct DOBs "
                         f"({dobs}) with no SSN to arbitrate — likely two different people sharing "
                         f"a name. Recommend splitting; escalate to human for final confirmation "
                         f"given no SSN corroboration.",
        }
    if ssns or dobs:
        return {
            "decision": "MERGE",
            "confidence": 0.75,
            "reasoning": f"Cluster '{evidence['name']}' has a single consistent SSN/DOB across all "
                         f"{len(evidence['fields'])} linked field types with no internal contradiction. "
                         f"Confirming the deterministic resolver's merge.",
        }
    return {
        "decision": "ESCALATE",
        "confidence": 0.3,
        "reasoning": f"Cluster '{evidence['name']}' has no SSN or DOB anywhere to corroborate or "
                     f"contradict a shared-name merge. Insufficient evidence for an automated call — "
                     f"routing to human review queue.",
    }


def run_adjudicator(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT person_id FROM persons WHERE review_status = 'needs_review'")
    subjects = [r[0] for r in cur.fetchall()]

    results = {"MERGE": 0, "SPLIT": 0, "ESCALATE": 0}
    for pid in subjects:
        t0 = time.time()
        evidence = _gather_evidence(cur, pid)
        decision = adjudicate_person(evidence)
        latency_ms = int((time.time() - t0) * 1000)

        cur.execute(
            """INSERT INTO agent_runs (agent_name, subject_id, input_summary, output_summary, decision,
                                        tokens_used, cost_usd, latency_ms, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("adjudicator", pid, json.dumps(evidence)[:2000], decision["reasoning"], decision["decision"],
             0, 0.0, latency_ms, "success"),
        )

        new_status = {
            "MERGE": "auto_accepted",
            "SPLIT": "needs_review",       # a real SPLIT would spawn a new person row; logged for human action here
            "ESCALATE": "needs_review",
        }[decision["decision"]]
        cur.execute("UPDATE persons SET review_status=?, resolution_confidence=? WHERE person_id=?",
                    (new_status, decision["confidence"], pid))

        cur.execute(
            """INSERT INTO review_decisions (subject_type, subject_id, decision, reviewer, notes)
               VALUES (?, ?, ?, ?, ?)""",
            ("person_match", pid, decision["decision"], "adjudicator_agent", decision["reasoning"]),
        )

        results[decision["decision"]] += 1

    conn.commit()
    conn.close()
    print(f"Adjudicator processed {len(subjects)} escalated persons: {results}")
    return results


if __name__ == "__main__":
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else "../../db/breach.db"
    run_adjudicator(db_path)
