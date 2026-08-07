"""
structured_extraction.py — row-aware extraction for tabular multi-person
documents (xlsx, csv).

Why this exists: flattening a spreadsheet to raw text and running the same
regex pass as narrative documents loses which name goes with which SSN/DOB
in a MULTI-person file — row 1's name can get paired with row 40's SSN.
For anything with real rows and columns, read it as rows and columns.

Column-name -> category mapping is heuristic (case-insensitive substring
match) rather than hardcoded to this corpus's exact headers, so it holds up
against real-world exports with slightly different column names.
"""

import csv
import re
import sqlite3
import openpyxl

COLUMN_HINTS = [
    (re.compile(r"name", re.I), "full_name"),
    (re.compile(r"dob|date.?of.?birth", re.I), "dob"),
    (re.compile(r"ssn|social.?security", re.I), "ssn"),
    (re.compile(r"phone", re.I), "phone"),
    (re.compile(r"e-?mail", re.I), "email"),
    (re.compile(r"address", re.I), "home_address"),
    (re.compile(r"card", re.I), "card_number"),
    (re.compile(r"medical|condition|diagnosis", re.I), "medical"),
    (re.compile(r"employee.?id", re.I), "_employee_id"),  # not a PII category we flag, kept for context only
]

PLACEHOLDER_NAMES = {"TEST USER"}
FALSE_POSITIVE_ID_PREFIXES = ("ORD-",)


def _map_headers(headers: list[str]) -> dict[int, str]:
    mapping = {}
    for i, h in enumerate(headers or []):
        h = (h or "").strip()
        for pattern, category in COLUMN_HINTS:
            if pattern.search(h):
                mapping[i] = category
                break
    return mapping


def extract_xlsx_rows(path: str, doc_id: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        headers = [str(c) if c is not None else "" for c in rows[0]]
        col_map = _map_headers(headers)
        for r_idx, row in enumerate(rows[1:], start=1):
            row_group = f"{doc_id}_r{r_idx}"
            row_vals = [str(c) if c is not None else "" for c in row]
            name_val = None
            for col_idx, category in col_map.items():
                if col_idx >= len(row_vals):
                    continue
                val = row_vals[col_idx].strip()
                if not val or category == "_employee_id":
                    continue
                if category == "full_name":
                    name_val = val
                    if val.upper() in PLACEHOLDER_NAMES:
                        break  # skip whole placeholder row
                out.append(dict(row_group=row_group, category=category, raw_value=val,
                                 normalized_value=val, detector="structured_column", confidence=0.9,
                                 context_snippet=f"row {r_idx}: {' | '.join(row_vals)}"[:200], is_partial=0))
            # drop rows that were flagged placeholder (no clean way to un-append; filter post-hoc)
        # filter out placeholder rows fully
        out = [o for o in out if not any(v.upper() in PLACEHOLDER_NAMES for v in [o["raw_value"]] if o["category"] == "full_name")]
    return out


def extract_csv_rows(path: str, doc_id: str) -> list[dict]:
    out = []
    with open(path, newline="", errors="replace") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return out
    headers = rows[0]
    col_map = _map_headers(headers)
    for r_idx, row in enumerate(rows[1:], start=1):
        row_group = f"{doc_id}_r{r_idx}"
        for col_idx, category in col_map.items():
            if col_idx >= len(row) or category == "_employee_id":
                continue
            val = row[col_idx].strip()
            if not val:
                continue
            # CSV in this corpus plants SSN as last-4 only -> mark partial
            is_partial = 1 if category == "ssn" and len(val) == 4 and val.isdigit() else 0
            out.append(dict(row_group=row_group, category=category, raw_value=val,
                             normalized_value=val, detector="structured_column", confidence=0.9,
                             context_snippet=f"row {r_idx}: {','.join(row)}"[:200], is_partial=is_partial))
    return out


def run_structured_extraction(corpus_dir: str, db_path: str):
    import os
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT doc_id, filename, file_type FROM documents WHERE status='parsed' AND file_type IN ('xlsx','csv')")
    docs = cur.fetchall()

    total = 0
    for doc_id, filename, file_type in docs:
        path = os.path.join(corpus_dir, filename)
        try:
            rows = extract_xlsx_rows(path, doc_id) if file_type == "xlsx" else extract_csv_rows(path, doc_id)
        except Exception as e:
            print(f"  structured extraction failed for {doc_id}: {e}")
            continue
        for r in rows:
            cur.execute(
                """INSERT INTO extractions (doc_id, category, raw_value, normalized_value, detector, confidence, context_snippet, is_partial, row_group)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, r["category"], r["raw_value"], r["normalized_value"], r["detector"],
                 r["confidence"], r["context_snippet"], r["is_partial"], r["row_group"]),
            )
            total += 1

    # Remove the old blob-regex extractions for these same docs (they mis-paired rows)
    doc_ids = [d[0] for d in docs]
    if doc_ids:
        cur.execute(
            f"DELETE FROM extractions WHERE doc_id IN ({','.join('?'*len(doc_ids))}) AND detector != 'structured_column'",
            doc_ids,
        )

    conn.commit()
    conn.close()
    print(f"Structured (row-aware) extraction: {total} elements from {len(docs)} tabular documents")
    return total


if __name__ == "__main__":
    import sys
    corpus_dir = sys.argv[1] if len(sys.argv) > 1 else "../../corpus_generator/output/corpus"
    db_path = sys.argv[2] if len(sys.argv) > 2 else "../../db/breach.db"
    run_structured_extraction(corpus_dir, db_path)
