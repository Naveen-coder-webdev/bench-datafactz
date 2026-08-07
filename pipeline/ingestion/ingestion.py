"""
ingestion.py — Stage 1: inventory the corpus, classify by real content
(not just extension), and quarantine anything that can't be safely parsed.

Nothing is silently dropped: every file ends up either 'pending' (ready
for parsing) or 'quarantined' (with a reason logged to the DB).
"""

import os
import sqlite3
import filetype  # detects real file type from magic bytes, not extension

EXT_TO_TYPE = {
    ".docx": "docx", ".pdf": "pdf", ".xlsx": "xlsx", ".csv": "csv",
    ".eml": "eml", ".txt": "txt", ".html": "html", ".png": "png",
}


def classify_file(path: str) -> tuple[str, str | None]:
    """Returns (file_type, quarantine_reason). file_type is best-guess;
    quarantine_reason is None if the file is fine to route to parsing."""
    size = os.path.getsize(path)
    if size == 0:
        return "unknown", "Zero-byte file"

    ext = os.path.splitext(path)[1].lower()
    declared_type = EXT_TO_TYPE.get(ext, "unknown")

    # Detect actual content type from magic bytes
    kind = filetype.guess(path)
    real_type = kind.extension if kind else None

    # EML/CSV/TXT/HTML are plain-text-ish and won't be caught by filetype
    # (which only detects binary signatures), so don't flag those as mismatched.
    if declared_type in ("eml", "csv", "txt", "html") and real_type is None:
        # confirm it's actually readable as text
        try:
            with open(path, "r", errors="strict") as f:
                f.read(2048)
            return declared_type, None
        except (UnicodeDecodeError, Exception):
            return declared_type, "Declared as text-based but unreadable as text — possible corruption"

    if real_type is not None:
        # e.g. a PNG saved as .docx: real_type='png', ext='.docx'
        ext_bare = ext.lstrip(".")
        if real_type != ext_bare and not (real_type == "zip" and ext_bare == "docx"):
            # docx/xlsx are zip containers, so zip-vs-docx/xlsx is expected, not a mismatch
            return declared_type, f"File extension '{ext}' does not match actual content (detected: {real_type})"

    if declared_type == "pdf":
        try:
            with open(path, "rb") as f:
                header = f.read(5)
            if header != b"%PDF-":
                return "pdf", "Corrupt/unreadable file structure (invalid PDF header)"
        except Exception as e:
            return "pdf", f"Could not read file: {e}"

        # Check for password protection
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            if reader.is_encrypted:
                return "pdf", "Password-protected — cannot parse without credential"
        except Exception as e:
            return "pdf", f"Corrupt/unreadable file structure: {e}"

    return declared_type, None


def ingest_corpus(corpus_dir: str, db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    files = sorted(os.listdir(corpus_dir))
    counts = {"pending": 0, "quarantined": 0}

    for fname in files:
        path = os.path.join(corpus_dir, fname)
        if not os.path.isfile(path):
            continue
        doc_id = os.path.splitext(fname)[0]
        file_type, reason = classify_file(path)

        status = "quarantined" if reason else "pending"
        counts[status] += 1

        cur.execute(
            """INSERT OR REPLACE INTO documents (doc_id, filename, file_type, status, quarantine_reason)
               VALUES (?, ?, ?, ?, ?)""",
            (doc_id, fname, file_type, status, reason),
        )

    conn.commit()
    conn.close()
    print(f"Ingested {len(files)} files: {counts['pending']} pending parsing, {counts['quarantined']} quarantined")
    return counts


if __name__ == "__main__":
    import sys
    corpus_dir = sys.argv[1] if len(sys.argv) > 1 else "../corpus_generator/output/corpus"
    db_path = sys.argv[2] if len(sys.argv) > 2 else "../db/breach.db"
    ingest_corpus(corpus_dir, db_path)
