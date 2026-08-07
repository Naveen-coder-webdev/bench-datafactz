-- schema.sql — relational schema for the breach analytics platform
-- SQLite for local dev/demo; column types map cleanly to Postgres if you
-- move to Azure Database for PostgreSQL later (see docs/design/stack_justification.md).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,           -- docx, pdf_digital, pdf_scanned, xlsx, csv, eml, txt, html, png
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, parsed, quarantined, failed
    quarantine_reason TEXT,
    raw_text TEXT,                     -- extracted text (post OCR if needed)
    parse_method TEXT,                 -- e.g. 'pdftotext', 'tesseract_ocr', 'docx_native'
    ingested_at TEXT DEFAULT CURRENT_TIMESTAMP,
    parsed_at TEXT
);

CREATE TABLE IF NOT EXISTS extractions (
    extraction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id),
    category TEXT NOT NULL,            -- ssn, dob, phone, email, card_number, home_address, medical, login_credentials, full_name, dl_number, passport_number
    raw_value TEXT NOT NULL,
    normalized_value TEXT,
    detector TEXT NOT NULL,            -- which detector found it: regex_ssn, regex_email, luhn_card, name_ner, etc.
    confidence REAL NOT NULL,          -- 0-1
    context_snippet TEXT,              -- surrounding text for evidence drill-down
    is_partial INTEGER DEFAULT 0,      -- e.g. last-4 SSN only
    row_group TEXT,                    -- for multi-person tabular docs: groups elements from the same row (e.g. doc_id + row index). NULL = whole-document is one group.
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS persons (
    person_id TEXT PRIMARY KEY,        -- system-assigned canonical ID (RES_0001...)
    best_name TEXT NOT NULL,
    dob TEXT,
    resolution_confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'auto_accepted',  -- auto_accepted, human_reviewed, needs_review
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS identity_links (
    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES persons(person_id),
    extraction_id INTEGER NOT NULL REFERENCES extractions(extraction_id),
    match_method TEXT NOT NULL,        -- exact_name, alias_match, ssn_match, adjudicator_agent
    match_confidence REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS flags (
    flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES persons(person_id),
    category TEXT NOT NULL,
    is_exposed INTEGER NOT NULL DEFAULT 0,
    confidence REAL,
    doc_refs TEXT,                     -- JSON array of doc_ids as evidence
    review_status TEXT DEFAULT 'auto_accepted',
    UNIQUE(person_id, category)
);

CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL,        -- 'extraction', 'person_match', 'flag'
    subject_id TEXT NOT NULL,
    decision TEXT NOT NULL,            -- 'accept', 'reject', 'merge', 'split'
    reviewer TEXT,
    notes TEXT,
    decided_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,          -- orchestrator, exception_investigator, adjudicator, qa_auditor
    subject_id TEXT,
    input_summary TEXT,
    output_summary TEXT,
    decision TEXT,
    tokens_used INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    latency_ms INTEGER,
    status TEXT,                       -- success, escalated, failed
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_extractions_doc ON extractions(doc_id);
CREATE INDEX IF NOT EXISTS idx_extractions_category ON extractions(category);
CREATE INDEX IF NOT EXISTS idx_links_person ON identity_links(person_id);
CREATE INDEX IF NOT EXISTS idx_flags_person ON flags(person_id);
