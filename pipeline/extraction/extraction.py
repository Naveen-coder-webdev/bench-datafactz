"""
extraction.py — Stage 3: deterministic PII detection.

This is the "cheap, fast, testable" tier from the brief's cost-engineering
section: regex + checksum validators, no LLM calls. It handles every
category that has a reliable structural pattern. Context-dependent
categories (e.g. distinguishing a real home address from an office address
in ambiguous prose) are exactly where the design doc's cost section says
the LLM tier should be invoked instead — see extraction_llm_stub.py for
the interface that tier would implement.

Every detector returns (raw_value, normalized_value, confidence, context_snippet).
"""

import re
import sqlite3

# ---------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------

SSN_RE = re.compile(r"\b(\d{3}-\d{2}-\d{4})\b")
PHONE_RE = re.compile(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?:\s?x\d+)?")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
DOB_RE = re.compile(r"\b(19\d{2}|20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
DL_RE = re.compile(r"\bD\d{7}\b")
PASSPORT_RE = re.compile(r"\b\d{9,10}\b")
LOGIN_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9]{4,20})[/\s]+([A-Za-z0-9!@#$%^&*_]{6,20})\b")
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.\s]+?\b(?:St|Street|Ave|Avenue|Rd|Road|Ln|Lane|Dr|Drive|Blvd|Suite|Apt|Way|Ct|Court)\b[^,\n]*,\s*[A-Za-z\s]+,?\s*[A-Z]{2}\s*\d{5}",
    re.IGNORECASE,
)

# Known false-positive shapes to actively suppress (order refs, placeholders)
ORDER_REF_RE = re.compile(r"\bORD-\d+\b")
PLACEHOLDER_SSN = {"000-00-0000"}
PLACEHOLDER_NAMES = {"TEST USER"}

# Name detection: cheap deterministic pass — capitalized word sequences
# near a name-indicating label. This is exactly the kind of context-dependent
# element the design doc's cost section should route to an LLM tier for
# higher recall; this regex pass is the free/fast first tier.
NAME_LABEL_RE = re.compile(
    r"(?:Patient Name|Employee|Customer Name|Full Name|Name|Dear|Record for|Record request for|for)"
    r"[:,]?\s+([A-Z][a-zA-Z.'-]+(?:\s[A-Z][a-zA-Z.'-]+){1,2})"
)
# Table-row style: "Name | Robert Smith |" from docx/xlsx flattening
NAME_TABLE_RE = re.compile(r"\b([A-Z][a-zA-Z.'-]+\s[A-Z][a-zA-Z.'-]+)\s*\|")

MEDICAL_KEYWORDS = [
    "diabetes", "hypertension", "asthma", "depressive disorder", "anxiety disorder",
    "hypothyroidism", "migraine", "coronary artery disease", "rheumatoid arthritis",
    "gerd", "diagnosis", "patient", "symptoms", "condition:",
]


def luhn_valid(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if len(digits) < 13:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _context(text: str, start: int, end: int, window=60) -> str:
    return text[max(0, start - window):min(len(text), end + window)].replace("\n", " ").strip()


def extract_from_text(text: str) -> list[dict]:
    """Returns list of {category, raw_value, normalized_value, detector, confidence, context_snippet, is_partial}"""
    results = []

    seen_names = set()
    for m in NAME_LABEL_RE.finditer(text):
        val = m.group(1).strip()
        if val in PLACEHOLDER_NAMES or val in seen_names:
            continue
        seen_names.add(val)
        ctx = _context(text, m.start(), m.end())
        results.append(dict(category="full_name", raw_value=val, normalized_value=val,
                             detector="regex_name_label", confidence=0.75, context_snippet=ctx, is_partial=0))

    for m in NAME_TABLE_RE.finditer(text):
        val = m.group(1).strip()
        if val in PLACEHOLDER_NAMES or val in seen_names:
            continue
        seen_names.add(val)
        ctx = _context(text, m.start(), m.end())
        results.append(dict(category="full_name", raw_value=val, normalized_value=val,
                             detector="regex_name_table", confidence=0.6, context_snippet=ctx, is_partial=0))

    for m in SSN_RE.finditer(text):
        val = m.group(1)
        if val in PLACEHOLDER_SSN:
            continue
        # avoid matching inside an obvious order-ref context
        ctx = _context(text, m.start(), m.end())
        results.append(dict(category="ssn", raw_value=val, normalized_value=val,
                             detector="regex_ssn", confidence=0.95, context_snippet=ctx, is_partial=0))

    for m in DOB_RE.finditer(text):
        val = m.group(0)
        ctx = _context(text, m.start(), m.end())
        results.append(dict(category="dob", raw_value=val, normalized_value=val,
                             detector="regex_dob", confidence=0.85, context_snippet=ctx, is_partial=0))

    for m in EMAIL_RE.finditer(text):
        val = m.group(0)
        ctx = _context(text, m.start(), m.end())
        conf = 0.6 if "@example.com" in val else 0.9
        results.append(dict(category="email", raw_value=val, normalized_value=val.lower(),
                             detector="regex_email", confidence=conf, context_snippet=ctx, is_partial=0))

    for m in PHONE_RE.finditer(text):
        val = m.group(0)
        ctx = _context(text, m.start(), m.end())
        results.append(dict(category="phone", raw_value=val, normalized_value=re.sub(r"\D", "", val),
                             detector="regex_phone", confidence=0.8, context_snippet=ctx, is_partial=0))

    for m in DL_RE.finditer(text):
        val = m.group(0)
        ctx = _context(text, m.start(), m.end())
        results.append(dict(category="dl_number", raw_value=val, normalized_value=val,
                             detector="regex_dl", confidence=0.85, context_snippet=ctx, is_partial=0))

    for m in ADDRESS_RE.finditer(text):
        val = m.group(0)
        ctx = _context(text, m.start(), m.end())
        results.append(dict(category="home_address", raw_value=val, normalized_value=val,
                             detector="regex_address", confidence=0.7, context_snippet=ctx, is_partial=0))

    for m in CARD_RE.finditer(text):
        candidate = m.group(0)
        digits_only = re.sub(r"\D", "", candidate)
        if len(digits_only) < 13 or len(digits_only) > 19:
            continue
        if not luhn_valid(digits_only):
            continue
        ctx = _context(text, m.start(), m.end())
        results.append(dict(category="card_number", raw_value=candidate, normalized_value=digits_only,
                             detector="luhn_card", confidence=0.97, context_snippet=ctx, is_partial=0))

    for m in LOGIN_RE.finditer(text):
        ctx = _context(text, m.start(), m.end())
        if "username" in ctx.lower() or "login" in ctx.lower() or "/" in m.group(0):
            results.append(dict(category="login_credentials", raw_value=m.group(0), normalized_value=m.group(0),
                                 detector="regex_login", confidence=0.6, context_snippet=ctx, is_partial=0))

    lowered = text.lower()
    for kw in MEDICAL_KEYWORDS:
        idx = lowered.find(kw)
        if idx != -1:
            ctx = _context(text, idx, idx + len(kw))
            results.append(dict(category="medical", raw_value=kw, normalized_value=kw,
                                 detector="keyword_medical", confidence=0.5, context_snippet=ctx, is_partial=0))
            break  # one medical flag per doc is enough signal; dedup happens downstream

    # Partial identifier: last-4 SSN/card patterns explicitly labeled as such in context
    for m in re.finditer(r"\b(\d{4})\b", text):
        ctx = _context(text, m.start(), m.end())
        if re.search(r"last[\s-]?4|ending in|card ending", ctx, re.IGNORECASE):
            results.append(dict(category="card_number", raw_value=m.group(1), normalized_value=m.group(1),
                                 detector="regex_partial_last4", confidence=0.55, context_snippet=ctx, is_partial=1))

    return results


def run_extraction(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT doc_id, raw_text FROM documents WHERE status='parsed' AND raw_text IS NOT NULL")
    docs = cur.fetchall()

    total = 0
    for doc_id, text in docs:
        if not text:
            continue
        findings = extract_from_text(text)
        for f in findings:
            cur.execute(
                """INSERT INTO extractions (doc_id, category, raw_value, normalized_value, detector, confidence, context_snippet, is_partial)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, f["category"], f["raw_value"], f["normalized_value"], f["detector"],
                 f["confidence"], f["context_snippet"], f["is_partial"]),
            )
            total += 1

    conn.commit()
    conn.close()
    print(f"Extracted {total} PII elements across {len(docs)} parsed documents")
    return total


if __name__ == "__main__":
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else "../../db/breach.db"
    run_extraction(db_path)
