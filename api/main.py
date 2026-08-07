"""
main.py — versioned REST API over the breach analytics DB.

Run: uvicorn main:app --reload --port 8000
Then: http://localhost:8000/docs for interactive OpenAPI docs.
"""

import json
import sqlite3
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = os.environ.get("BREACH_DB", "../db/breach.db")

app = FastAPI(title="Breach Analytics API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/api/v1/stats")
def stats():
    conn = db()
    cur = conn.cursor()
    out = {}
    cur.execute("SELECT status, COUNT(*) c FROM documents GROUP BY status")
    out["documents_by_status"] = {r["status"]: r["c"] for r in cur.fetchall()}
    cur.execute("SELECT file_type, COUNT(*) c FROM documents GROUP BY file_type")
    out["documents_by_type"] = {r["file_type"]: r["c"] for r in cur.fetchall()}
    cur.execute("SELECT COUNT(*) c FROM persons")
    out["total_persons"] = cur.fetchone()["c"]
    cur.execute("SELECT review_status, COUNT(*) c FROM persons GROUP BY review_status")
    out["persons_by_review_status"] = {r["review_status"]: r["c"] for r in cur.fetchall()}
    cur.execute("SELECT COUNT(*) c FROM extractions")
    out["total_extractions"] = cur.fetchone()["c"]
    cur.execute("SELECT SUM(cost_usd) c, COUNT(*) n FROM agent_runs")
    row = cur.fetchone()
    out["agent_run_cost_usd"] = row["c"] or 0
    out["agent_run_count"] = row["n"] or 0
    conn.close()
    return out


@app.get("/api/v1/persons")
def list_persons(review_status: str | None = None, category: str | None = None, limit: int = 500, offset: int = 0):
    conn = db()
    cur = conn.cursor()
    q = "SELECT * FROM persons WHERE 1=1"
    params = []
    if review_status:
        q += " AND review_status = ?"
        params.append(review_status)
    if category:
        q += """ AND person_id IN (SELECT person_id FROM flags WHERE category = ? AND is_exposed = 1)"""
        params.append(category)
    q += " LIMIT ? OFFSET ?"
    params += [limit, offset]
    cur.execute(q, params)
    persons = [dict(r) for r in cur.fetchall()]

    for p in persons:
        cur.execute("SELECT category, is_exposed, confidence, doc_refs FROM flags WHERE person_id=?", (p["person_id"],))
        p["flags"] = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"count": len(persons), "results": persons}


@app.get("/api/v1/persons/{person_id}")
def get_person(person_id: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM persons WHERE person_id=?", (person_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "person not found")
    person = dict(row)

    cur.execute("SELECT category, is_exposed, confidence, doc_refs, review_status FROM flags WHERE person_id=?", (person_id,))
    person["flags"] = [dict(r) for r in cur.fetchall()]

    cur.execute("""SELECT e.extraction_id, e.doc_id, e.category, e.raw_value, e.confidence, e.context_snippet, il.match_method
                   FROM identity_links il JOIN extractions e ON il.extraction_id=e.extraction_id WHERE il.person_id=?""", (person_id,))
    person["evidence"] = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT decision, reviewer, notes, decided_at FROM review_decisions WHERE subject_id=?", (person_id,))
    person["review_history"] = [dict(r) for r in cur.fetchall()]
    conn.close()
    return person


@app.get("/api/v1/documents")
def list_documents(status: str | None = None, limit: int = 1000, offset: int = 0):
    conn = db()
    cur = conn.cursor()
    q = "SELECT doc_id, filename, file_type, status, quarantine_reason, parse_method FROM documents WHERE 1=1"
    params = []
    if status:
        q += " AND status = ?"
        params.append(status)
    q += " LIMIT ? OFFSET ?"
    params += [limit, offset]
    cur.execute(q, params)
    docs = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"count": len(docs), "results": docs}


@app.get("/api/v1/review-queue")
def review_queue():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM persons WHERE review_status='needs_review'")
    persons = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"count": len(persons), "results": persons}


@app.post("/api/v1/review-queue/{person_id}/decide")
def decide(person_id: str, decision: str, reviewer: str = "human_reviewer", notes: str = ""):
    if decision not in ("accept", "reject", "merge", "split"):
        raise HTTPException(400, "invalid decision")
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT INTO review_decisions (subject_type, subject_id, decision, reviewer, notes) VALUES (?,?,?,?,?)",
                ("person_match", person_id, decision, reviewer, notes))
    if decision in ("accept", "merge"):
        cur.execute("UPDATE persons SET review_status='human_reviewed' WHERE person_id=?", (person_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/v1/agent-runs")
def agent_runs(agent_name: str | None = None, subject_id: str | None = None, limit: int = 500):
    conn = db()
    cur = conn.cursor()
    q = "SELECT * FROM agent_runs WHERE 1=1"
    params = []
    if agent_name:
        q += " AND agent_name = ?"
        params.append(agent_name)
    if subject_id:
        q += " AND subject_id = ?"
        params.append(subject_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cur.execute(q, params)
    runs = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"count": len(runs), "results": runs}


# ---- Serve the dashboard UI from the same process (avoids CORS entirely) ----
import os
from fastapi.staticfiles import StaticFiles

_ui_dir = os.path.join(os.path.dirname(__file__), "..", "ui")
if os.path.isdir(_ui_dir):
    app.mount("/", StaticFiles(directory=_ui_dir, html=True), name="ui")
