# Breach Analytics at Scale — DataFactZ Capstone (Use Case 3)

Agentic document intelligence & entity resolution for breach-notification
analysis. Ingests a heterogeneous, messy document corpus and produces a
defensible, evidence-backed exposure table: who is affected, and what of
theirs was exposed.

See the brief: `docs/design/brief.md` (paste the original assignment here
for reference).

## Repo layout

```
corpus_generator/   Synthetic breach-dump generator + ground-truth manifest
                     (done — see corpus_generator/README.md)

pipeline/            Deterministic bulk-processing path
  ingestion/           inventory, classify, route, quarantine
  parsing/             text extraction + OCR across all file types
  extraction/           PII detection (regex/checksum detectors + LLM)
  entity_resolution/   linking elements to unique individuals

agents/               Agentic layer — judgment/iteration only, not the bulk path
  orchestrator/         plans the processing campaign, tracks progress, adapts
  exception_investigator/  retries/escalates quarantined + ambiguous files
  adjudicator/           merge/split/escalate decisions on uncertain matches
  qa_auditor/            independent sampling + re-verification of results

api/                  Versioned REST API (documents, extractions, persons,
                       identity links, flags, review decisions)

db/
  migrations/          schema migrations (documents, extractions, persons,
                       identity_links, flags, review_decisions)

ui/                    Dashboard: processing status, exposure table w/ filters,
                       person detail + evidence drill-down, review queue,
                       run cost, run traces

docs/
  design/               problem statement, design doc, stack justification
  diagrams/             architecture diagram
  cost/                 cost model, 100K/1M extrapolation, cost/accuracy curve

scripts/               scoring script (pipeline output vs. corpus_generator
                       manifest), dev/setup scripts

tests/                 unit + integration tests
```

## Status

- [x] Corpus generator + manifest (Section 3) — 520 docs, 168 people
- [x] Ingestion & triage — 32/520 correctly quarantined
- [x] Multi-format parsing (incl. OCR) — verified against known scanned doc
- [x] PII extraction (deterministic + structured tabular) — 5,600+ elements
- [x] Entity resolution (deterministic tier) — 220 clusters, ~30% person-level recall (see error analysis)
- [x] Exposure table + API + DB schema
- [x] Entity-resolution adjudicator agent — 52 real, explained decisions
- [ ] Orchestrator, exception investigator, QA auditor agents
- [ ] Human review queue write actions wired into UI
- [x] Accuracy measurement vs. manifest (`scripts/score.py`, `scripts/score_report.json`)
- [x] Cost engineering report (`docs/cost/cost_report.md`)
- [x] Dashboard UI (`ui/dashboard_template.html`)
- [x] Design doc, architecture diagram, stack justification (`docs/design/`, `docs/diagrams/`)
- [ ] Presentation deck

## Running the pipeline end to end

```bash
# 1. Generate the corpus (see corpus_generator/README.md)
cd corpus_generator && python3 generate.py && cd ..

# 2. Create the DB
python3 -c "import sqlite3; c=sqlite3.connect('db/breach.db'); c.executescript(open('db/schema.sql').read())"

# 3. Run the pipeline stages in order
python3 pipeline/ingestion/ingestion.py corpus_generator/output/corpus db/breach.db
python3 pipeline/parsing/parsing.py corpus_generator/output/corpus db/breach.db
python3 pipeline/extraction/extraction.py db/breach.db
python3 pipeline/extraction/structured_extraction.py corpus_generator/output/corpus db/breach.db
python3 pipeline/entity_resolution/entity_resolution.py db/breach.db
python3 agents/adjudicator/adjudicator.py db/breach.db
python3 pipeline/exposure_table.py db/breach.db

# 4. Score against the manifest
python3 scripts/score.py db/breach.db corpus_generator/output/manifest

# 5. Run the API + dashboard together
cd api && BREACH_DB=../db/breach.db uvicorn main:app --reload --port 8000
# then open http://localhost:8000 — the dashboard is served from the same
# process and calls the API live (no separate static snapshot anymore)
```

See `docs/design/design_doc.md` for the full architecture, stack
justification, ERD, pipeline-vs-agent reasoning, and accuracy error
analysis. See `docs/cost/cost_report.md` for the cost model.
