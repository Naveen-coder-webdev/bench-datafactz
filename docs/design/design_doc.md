# Solution Design Document — Breach Analytics at Scale
DataFactZ AI Engineering Internship · Use Case 3 (Weeks 3–4)

---

## 1. Stack Justification

### Orchestration
**Chosen: hand-rolled Python orchestration (stage scripts + SQLite/Postgres),
not LangGraph or the Claude Agent SDK.**

- *Rejected — LangGraph*: gives graph-based state management and built-in
  retries, but the bulk of this system (ingestion, parsing, extraction,
  resolution) is a **deterministic pipeline**, not a graph of LLM-driven
  decisions. Adopting a graph framework for stages that are plain function
  calls adds an abstraction layer (state schemas, node wiring) without
  buying anything — the four stages built today (`ingestion.py`,
  `parsing.py`, `extraction.py`, `entity_resolution.py`) are simpler as
  linear scripts with a shared DB as the handoff point.
- *Rejected — Claude Agent SDK for the whole system*: same reasoning —
  wrapping every stage as an "agent" would blur the pipeline-vs-agent
  boundary the brief explicitly grades (§5). The SDK is the right fit
  specifically for the four agentic components (orchestrator, exception
  investigator, adjudicator, QA auditor), not the bulk path.
- **Why hand-rolled wins here**: each stage is independently testable
  (`python3 stage.py db.sqlite` — exactly how each was verified today),
  the DB is the single source of truth so any stage can be re-run
  idempotently, and the four agents can be added as thin callers against
  the same DB without needing the rest of the pipeline to know about them.

### Models per tier
**Chosen: 3-tier routing — deterministic (free) → cheap model (DeepSeek-class)
→ stronger model (Claude Sonnet-class), invoked only on escalation.**

- *Rejected — single strong model for all extraction*: measured today,
  100% of structured PII (SSN, DOB, phone, email, card numbers) was caught
  by regex/checksum detectors at zero marginal cost per document. Routing
  every document through an LLM would multiply cost with no accuracy gain
  on the ~85% of documents where deterministic detection already works —
  see `docs/cost/cost_report.md` for the measured 0% LLM-tier hit rate on
  this corpus.
- *Rejected — cheap model only, no escalation tier*: the entity-resolution
  shared-name trap (two different people, one name) needs real reasoning
  over corroborating identifiers, not just pattern completion. A cheap
  model without escalation would likely follow the deterministic
  resolver's mistakes rather than catch them — exactly the kind of
  case the adjudicator agent built today (`agents/adjudicator/adjudicator.py`)
  is designed to catch (it correctly identified two internally-contradictory
  SSNs in one cluster and recommended a split).
- **Why tiered wins**: this is the design the brief's cost section (§6)
  explicitly asks for, and it's the only configuration where cost scales
  with *ambiguity*, not corpus size.

### Infrastructure
**Chosen: SQLite for local dev/demo, with a clean migration path to Azure
Database for PostgreSQL for production.**

- *Rejected — Postgres from day one*: for a one-week build/demo cycle,
  standing up and maintaining a Postgres instance (even via Docker Compose)
  adds setup friction with no benefit until the system needs concurrent
  writers or true production scale. SQLite's schema (`db/schema.sql`) uses
  only standard SQL (no SQLite-specific extensions), so migrating is a
  `pg_dump`-equivalent exercise, not a rewrite.
- *Rejected — a document database (e.g. Cosmos DB / MongoDB)*: the brief
  explicitly requires "a real relational schema... with migrations and an
  ERD" (§7) — the domain (documents → extractions → persons →
  identity_links → flags → review_decisions) is inherently relational,
  with foreign-key integrity being exactly what makes the exposure table's
  evidence trail defensible.
- **Why SQLite→Postgres wins**: fast local iteration today, zero schema
  rewrite required to move to Azure Database for PostgreSQL (or AWS RDS)
  when concurrency demands it at the 100K-document scale (§7 below).

---

## 2. Architecture Overview

```
┌─────────────┐   ┌──────────┐   ┌────────────┐   ┌───────────────────┐   ┌────────────────┐
│  Ingestion  │──▶│ Parsing  │──▶│ Extraction │──▶│ Entity Resolution  │──▶│ Exposure Table  │
│  & Triage   │   │ (+ OCR)  │   │ (regex/    │   │ (deterministic +   │   │ (denormalized,  │
│             │   │          │   │  structured)│   │  adjudicator agent)│   │  evidence-linked)│
└─────────────┘   └──────────┘   └────────────┘   └────────┬───────────┘   └────────────────┘
      │                                                     │
      ▼                                                     ▼
 quarantine ◀── Exception Investigator agent      Adjudicator agent (escalated
 queue           (retries/escalates)               shared-name / no-corroboration cases)

                     All stages write to a single relational DB.
                     API (FastAPI) and Dashboard read from it.
                     QA Auditor agent independently re-samples completed results.
```

See `docs/diagrams/architecture.mmd` for the Mermaid source.

## 3. Database ERD

```
documents (doc_id PK) ──1:N── extractions (extraction_id PK, doc_id FK)
                                     │
                                     │ N:1
                                     ▼
                              identity_links (link_id PK, extraction_id FK, person_id FK)
                                     │
                                     │ N:1
                                     ▼
                                 persons (person_id PK) ──1:N── flags (flag_id PK, person_id FK)
                                     │
                                     └──1:N── review_decisions (decision_id PK, subject_id)

agent_runs (run_id PK, subject_id) — independent trace table, not FK-constrained
                                      to allow logging runs against any subject type
```

Full DDL: `db/schema.sql`.

## 4. Pipeline vs. Agent Boundary — and why

**Pipeline (deterministic, built and run today):**
- Ingestion/triage — file classification is a lookup + magic-byte check, no judgment required
- Parsing — routing to the right parser is deterministic (file type → parser); the "should I OCR this?" decision is a *measurable* threshold (extracted text length), not a judgment call
- PII extraction — regex/checksum detectors need no reasoning; structured tabular extraction is a column-mapping operation
- Entity resolution's *first pass* — blocking by name + accepting/rejecting merges based on identifier corroboration is rule application, not judgment

**Agents (only where the brief's four required roles genuinely need reasoning):**
- **Orchestrator** — plans/adapts the processing campaign (not yet built; scoped for week 2 continuation)
- **Exception investigator** — decides *which* alternative strategy to try on a quarantined file (retry OCR? different parser? escalate?) — this is a judgment call across options, not a lookup
- **Entity-resolution adjudicator** — *built and run today* (52 real decisions, each with a written rationale). This is the correct agent boundary: the deterministic resolver already tried rule-based disambiguation and explicitly flagged what it couldn't decide (`needs_review`); the agent's job is to reason over that specific evidence bundle, not to re-do extraction or parsing.
- **QA auditor** — independently re-verifies flags against source passages (not yet built; scoped for continuation)

**Why this boundary, concretely**: today's adjudicator run demonstrates the
principle directly — of 52 escalated persons, 27 had an internal
identifier contradiction (2 different SSNs, or 2 different DOBs) that the
*deterministic* resolver's rules correctly detected as ambiguous but
couldn't resolve alone. The agent's reasoning (comparing evidence sets,
weighing which contradiction is decisive) is exactly the "judgment and
iteration" the brief says agents should own — and nothing more.

## 5. Security

- **No real PII anywhere** — corpus is 100% synthetic (Faker-generated),
  verified in `corpus_generator/README.md`.
- **Evidence-based flags only** — every exposure flag stores `doc_refs`
  (the exact source documents), so nothing in the exposure table is
  unsupported by traceable evidence — directly satisfying the brief's
  "defensibility is the bar" requirement (§2).
- **Least-privilege DB access** (production): API should use a read-scoped
  DB role for `GET` endpoints and a separate write-scoped role for the
  review-decision endpoints, not implemented in this local SQLite demo but
  straightforward on the Postgres migration path.
- **PII-at-rest**: extracted values (SSNs, card numbers) are currently
  stored in plaintext in `extractions.raw_value` for demo purposes; a
  production deployment should encrypt this column at rest (e.g. Postgres
  `pgcrypto` or application-layer encryption) since the extraction DB
  itself becomes a second copy of the sensitive data.

## 6. Scalability — what changes at 100K documents

| Component | At 520 docs (today) | At 100K docs |
|---|---|---|
| Ingestion/parsing | Single-threaded, ~seconds | Needs a worker queue (e.g. Azure Service Bus / Celery) to parallelize OCR, which is the dominant compute cost |
| DB | SQLite, single file | Migrate to Postgres (schema already portable) for concurrent writers |
| Entity resolution | In-memory clustering (Python objects) | Needs blocking-key-indexed batch processing — the current in-memory `Cluster` list approach (`entity_resolution.py`) is O(n·clusters) for name-plausibility checks and won't scale past roughly 10K persons without an indexed blocking step (e.g. block by normalized last name + DOB-year in SQL before doing in-memory fuzzy matching) |
| Adjudicator agent | 52 synchronous calls | Needs batching/async calls with a hard budget (brief §5's "hard budgets on steps/tokens/spend") and human approval gates before bulk merge actions |
| API | Single FastAPI process | Add pagination cursors (already present in `/api/v1/persons`) at every list endpoint; add caching for `/stats` |

## 7. Accuracy Report (measured today)

Full numbers: `scripts/score_report.json`, generated by `scripts/score.py`
against the corpus generator's manifest.

| Metric | Value |
|---|---|
| True people in corpus | 168 |
| Resolved person clusters | 220 |
| Clusters mapped to a true person (via SSN/DOB) | 73 |
| Person-level recall | ~30% |
| Shared-name-trap: pairs correctly kept split | Partial — see error analysis below |

### Error analysis (the brief explicitly wants this, not just the number)

1. **Root cause found and partially fixed today**: the first entity-resolution
   pass flattened multi-person spreadsheets to raw text before extraction,
   which paired row 1's name with row 40's SSN. Fixed by adding row-aware
   structured extraction (`pipeline/extraction/structured_extraction.py`)
   for xlsx/csv — this alone cut spurious cluster count from 333 to 220
   (true count: 168).
2. **Remaining, diagnosed gap — partial identifier linkage**: the brief's
   required edge case ("an SSN in one document, the matching name only in
   another; last-4-digits references") is *planted correctly* in the
   corpus (CSV exports carry last-4 SSN only), but the current resolver
   only treats **full, exact-match** SSN/DOB as a corroborating identifier.
   A person whose full SSN appears in one document and only last-4 in
   another currently resolves into two separate clusters instead of one —
   directly explaining most of the recall gap. **Fix for week 2**: add
   last-4 as a weak corroborating signal (not a strong merge trigger alone)
   in `Cluster.strong_match`.
3. **Name-detection coverage gap**: the label-anchored name regex (`Name:`,
   `Dear`, etc.) misses one of the three narrative letter templates in the
   corpus generator, which has no such anchor phrase. This is precisely the
   category the design's Tier 1 (cheap LLM) routing is intended for —
   confirmed by today's build, not assumed.
4. **Shared-name trap**: correctly detected as a resolver-level signal (the
   adjudicator's SPLIT decisions cite exact SSN/DOB contradictions), but
   full end-to-end verification against all 8 planted collision pairs is
   blocked on the same partial-identifier gap above, since some collision
   pairs' documents didn't share a strong identifier with the base record.

## 8. What's built vs. what remains

Built and verified working today (see chat history / repo commits):
ingestion & triage, multi-format parsing incl. OCR, deterministic + structured
PII extraction, entity resolution (deterministic tier), exposure table
generation + CSV/XLSX export, the entity-resolution adjudicator agent with
real explained decisions, a versioned REST API, and a dashboard UI.

Remaining for continued build: orchestrator agent, exception investigator
agent, QA auditor agent, human-review-queue write actions wired into the UI,
last-4 partial-identifier linkage fix, and full agent-hygiene features
(budgets, approval gates, forced-failure demo).
