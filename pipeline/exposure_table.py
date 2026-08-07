"""
exposure_table.py — builds the denormalized exposure table (brief Section 2):
one row per resolved person, with a flag per exposure category, evidence
doc references, and confidence/review status.
"""

import json
import sqlite3

CATEGORIES = [
    "ssn", "dob", "dl_number", "passport_number", "financial_account",
    "card_number", "medical", "login_credentials", "home_address", "phone", "email",
]


def build_flags(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM flags")

    cur.execute("""SELECT il.person_id, e.category, e.confidence, e.doc_id
                   FROM identity_links il JOIN extractions e ON il.extraction_id = e.extraction_id
                   WHERE e.category != 'full_name'""")
    rows = cur.fetchall()

    agg = {}  # (person_id, category) -> {confidences: [], docs: set()}
    for pid, cat, conf, doc_id in rows:
        key = (pid, cat)
        agg.setdefault(key, {"confidences": [], "docs": set()})
        agg[key]["confidences"].append(conf)
        agg[key]["docs"].add(doc_id)

    for (pid, cat), data in agg.items():
        avg_conf = sum(data["confidences"]) / len(data["confidences"])
        review_status = "auto_accepted" if avg_conf >= 0.75 else "needs_review"
        cur.execute(
            """INSERT INTO flags (person_id, category, is_exposed, confidence, doc_refs, review_status)
               VALUES (?, ?, 1, ?, ?, ?)""",
            (pid, cat, round(avg_conf, 3), json.dumps(sorted(data["docs"])), review_status),
        )

    conn.commit()
    conn.close()
    print(f"Built {len(agg)} exposure flags across resolved persons")


def export_exposure_table(db_path: str, out_csv: str, out_xlsx: str):
    import csv as csvmod
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT person_id, best_name, dob, resolution_confidence, review_status FROM persons")
    persons = cur.fetchall()

    cur.execute("SELECT person_id, category, is_exposed, confidence, doc_refs, review_status FROM flags")
    flags_by_person = {}
    for pid, cat, exposed, conf, refs, rstatus in cur.fetchall():
        flags_by_person.setdefault(pid, {})[cat] = {
            "exposed": bool(exposed), "confidence": conf, "doc_refs": json.loads(refs), "review_status": rstatus,
        }

    cur.execute("""SELECT il.person_id, COUNT(DISTINCT e.doc_id) FROM identity_links il
                   JOIN extractions e ON il.extraction_id = e.extraction_id GROUP BY il.person_id""")
    doc_counts = dict(cur.fetchall())

    header = ["person_id", "best_name", "dob", "doc_count", "resolution_confidence", "review_status"] + \
             [f"exposed_{c}" for c in CATEGORIES] + [f"evidence_{c}" for c in CATEGORIES]

    rows_out = []
    for pid, name, dob, res_conf, rstatus in persons:
        flags = flags_by_person.get(pid, {})
        row = [pid, name, dob or "", doc_counts.get(pid, 0), res_conf, rstatus]
        for c in CATEGORIES:
            row.append(1 if flags.get(c, {}).get("exposed") else 0)
        for c in CATEGORIES:
            refs = flags.get(c, {}).get("doc_refs", [])
            row.append(";".join(refs))
        rows_out.append(row)

    with open(out_csv, "w", newline="") as f:
        w = csvmod.writer(f)
        w.writerow(header)
        w.writerows(rows_out)

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Exposure Table"
    ws.append(header)
    for row in rows_out:
        ws.append(row)
    wb.save(out_xlsx)

    conn.close()
    print(f"Exported exposure table: {len(rows_out)} persons -> {out_csv}, {out_xlsx}")


if __name__ == "__main__":
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else "../db/breach.db"
    build_flags(db_path)
    export_exposure_table(db_path, "exposure_table.csv", "exposure_table.xlsx")
