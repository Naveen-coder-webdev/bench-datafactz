"""
entity_resolution.py — Stage 4: link every extraction to a unique
canonical person.

Strategy (deterministic tier — the bulk path):
  1. Build "documents" as resolution units: each source document's
     extractions are grouped together (co-occurrence signal — elements
     in the same doc likely describe the same 1-2 people).
  2. Within a document, pick the strongest identity anchor available:
     SSN > email > phone > name. SSN/email/phone are near-unique;
     name alone is NOT (that's the shared-name trap).
  3. Blocking: group documents by normalized name first (cheap), then
     within a name-block, split into distinct persons using DOB/SSN as
     the disambiguator — this is what correctly keeps two different
     "Garrett Wallace"s apart.
  4. Merge across name variants: nickname map + initials + fuzzy match
     (Levenshtein-ish via difflib) get merged INTO the same person only
     when a corroborating identifier (DOB, SSN-fragment, email, phone)
     also lines up — name similarity alone is not sufficient to merge.
  5. Anything ambiguous (same name, no corroborating identifier to split
     OR merge confidently) is escalated to the adjudicator agent
     (agents/adjudicator/adjudicator.py) rather than guessed here.
"""

import re
import sqlite3
import difflib

NICKNAMES = {
    "Robert": "Bob", "William": "Bill", "Richard": "Rick", "James": "Jim",
    "Elizabeth": "Liz", "Margaret": "Peggy", "Katherine": "Kate",
    "Michael": "Mike", "Christopher": "Chris", "Jennifer": "Jen",
    "Patricia": "Pat", "Charles": "Chuck", "Anthony": "Tony",
    "Deborah": "Debbie", "Susan": "Sue", "Kenneth": "Ken",
    "Timothy": "Tim", "Rebecca": "Becky", "Joseph": "Joe",
    "Barbara": "Barb",
}
NICKNAME_TO_FORMAL = {v: k for k, v in NICKNAMES.items()}


def normalize_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    parts = name.split()
    normed = []
    for p in parts:
        p_clean = p.rstrip(".")
        if p_clean in NICKNAME_TO_FORMAL:
            p_clean = NICKNAME_TO_FORMAL[p_clean]
        normed.append(p_clean.lower())
    return " ".join(normed)


def name_last_token(name: str) -> str:
    return name.strip().split()[-1].lower() if name.strip() else ""


def is_initial_form(name: str) -> bool:
    """'D. Johnson' or 'Danielle J.' style."""
    return bool(re.match(r"^[A-Z]\.\s\w+$", name.strip()) or re.match(r"^\w+\s[A-Z]\.$", name.strip()))


def names_plausibly_same(a: str, b: str) -> bool:
    na, nb = normalize_name(a), normalize_name(b)
    if na == nb:
        return True
    if is_initial_form(a) or is_initial_form(b):
        # match on last name + first-initial
        a_last, b_last = name_last_token(a), name_last_token(b)
        if a_last == b_last:
            a_first_letter = a.strip()[0].lower()
            b_first_letter = b.strip()[0].lower()
            return a_first_letter == b_first_letter
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    return ratio > 0.85  # catches misspellings like 'Dainelle Johnson'


class Cluster:
    """A working identity cluster during resolution — becomes one `persons` row."""
    _next_id = 1

    def __init__(self, name, dob=None, ssn=None, email=None, phone=None):
        self.id = f"RES_{Cluster._next_id:04d}"
        Cluster._next_id += 1
        self.names = {name}
        self.dob = dob
        self.ssn = ssn
        self.emails = {email} if email else set()
        self.phones = {phone} if phone else set()
        self.doc_ids = set()
        self.unit_keys = set()  # row_group (if tabular) or doc_id — for precise co-occurrence linking
        self.extraction_ids = []
        self.needs_review = False

    def strong_match(self, dob, ssn, email, phone) -> bool:
        if ssn and self.ssn and ssn == self.ssn:
            return True
        if dob and self.dob and dob == self.dob:
            return True
        if email and email in self.emails:
            return True
        if phone and phone in self.phones:
            return True
        return False

    def conflicting(self, dob, ssn) -> bool:
        """A DIFFERENT DOB or SSN present is a strong signal these are NOT the same person —
        this is the mechanism that keeps shared-name pairs split."""
        if ssn and self.ssn and ssn != self.ssn:
            return True
        if dob and self.dob and dob != self.dob:
            return True
        return False

    def absorb(self, name, dob, ssn, email, phone, doc_id, extraction_id=None, unit_key=None):
        self.names.add(name)
        if dob and not self.dob:
            self.dob = dob
        if ssn and not self.ssn:
            self.ssn = ssn
        if email:
            self.emails.add(email)
        if phone:
            self.phones.add(phone)
        self.doc_ids.add(doc_id)
        if unit_key:
            self.unit_keys.add(unit_key)
        if extraction_id:
            self.extraction_ids.append(extraction_id)

    def best_name(self) -> str:
        # prefer the longest, most complete-looking name (not an initials form)
        full_names = [n for n in self.names if not is_initial_form(n)]
        pool = full_names or list(self.names)
        return max(pool, key=len)


def resolve_entities(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""SELECT extraction_id, doc_id, category, normalized_value, row_group
                   FROM extractions ORDER BY doc_id""")
    rows = cur.fetchall()

    # Group extractions by resolution unit: row_group if the extraction came
    # from a row-aware tabular parse (multi-person doc), else the whole
    # document (single-person narrative doc). This is what keeps an 80-person
    # spreadsheet from having every row's identifiers smeared together.
    by_doc = {}
    for extraction_id, doc_id, category, value, row_group in rows:
        key = row_group if row_group else doc_id
        by_doc.setdefault(key, []).append((extraction_id, category, value))

    clusters: list[Cluster] = []
    escalations = []  # ambiguous cases sent to the adjudicator

    for unit_key, items in by_doc.items():
        # unit_key is either a row_group ("DOC_0123_r7") or a bare doc_id.
        # The real document a link belongs to is always the part before "_r".
        doc_id = unit_key.split("_r")[0] if "_r" in unit_key else unit_key
        names = [(eid, v) for eid, c, v in items if c == "full_name"]
        dob = next((v for _, c, v in items if c == "dob"), None)
        ssn = next((v for _, c, v in items if c == "ssn"), None)
        email = next((v for _, c, v in items if c == "email"), None)
        phone = next((v for _, c, v in items if c == "phone"), None)

        if not names:
            continue  # no identity anchor in this doc — extractions stay unlinked for now

        for eid, name in names:
            # 1. find clusters whose name is plausibly the same
            candidates = [c for c in clusters if any(names_plausibly_same(name, n) for n in c.names)]

            if not candidates:
                c = Cluster(name, dob, ssn, email, phone)
                c.absorb(name, dob, ssn, email, phone, doc_id, eid, unit_key)
                clusters.append(c)
                continue

            # 2. among name-plausible candidates, prefer one with a corroborating
            #    identifier match; reject ones with a CONFLICTING identifier
            #    (this is the shared-name-trap split logic)
            strong = [c for c in candidates if c.strong_match(dob, ssn, email, phone)]
            conflicted = [c for c in candidates if c.conflicting(dob, ssn)]

            if strong:
                strong[0].absorb(name, dob, ssn, email, phone, doc_id, eid, unit_key)
            elif len(candidates) == 1 and not conflicted and not (dob or ssn or email or phone):
                # same name, zero identifiers anywhere to corroborate or contradict —
                # ambiguous. Escalate rather than silently merge.
                candidates[0].absorb(name, dob, ssn, email, phone, doc_id, eid, unit_key)
                candidates[0].needs_review = True
                escalations.append({"doc_id": doc_id, "name": name, "reason": "name match with no corroborating identifier"})
            elif conflicted and len(candidates) == len(conflicted):
                # every name-plausible cluster conflicts on DOB/SSN -> genuinely a
                # different person under a shared name. Start a new cluster.
                c = Cluster(name, dob, ssn, email, phone)
                c.absorb(name, dob, ssn, email, phone, doc_id, eid, unit_key)
                clusters.append(c)
            else:
                # mixed signal -> escalate to adjudicator, attach to best guess for now
                best_guess = candidates[0]
                best_guess.absorb(name, dob, ssn, email, phone, doc_id, eid, unit_key)
                best_guess.needs_review = True
                escalations.append({"doc_id": doc_id, "name": name, "reason": "mixed/ambiguous identifier signal"})

    # Persist persons + identity_links
    cur.execute("DELETE FROM identity_links")
    cur.execute("DELETE FROM persons")
    for c in clusters:
        review_status = "needs_review" if c.needs_review else "auto_accepted"
        cur.execute(
            "INSERT INTO persons (person_id, best_name, dob, resolution_confidence, review_status) VALUES (?, ?, ?, ?, ?)",
            (c.id, c.best_name(), c.dob, 0.6 if c.needs_review else 0.9, review_status),
        )
        for eid in c.extraction_ids:
            cur.execute(
                "INSERT INTO identity_links (person_id, extraction_id, match_method, match_confidence) VALUES (?, ?, ?, ?)",
                (c.id, eid, "deterministic_resolution", 0.6 if c.needs_review else 0.9),
            )

    # Also link the non-name extractions (ssn/dob/etc not attached above) via
    # co-occurrence, at the SAME granularity resolution used (row_group when
    # the extraction is row-tagged, else whole document). Row-aware first,
    # falling back to doc-level only for extractions with no row_group.
    cur.execute("""SELECT e.extraction_id, e.doc_id, e.row_group FROM extractions e
                   LEFT JOIN identity_links il ON e.extraction_id = il.extraction_id
                   WHERE il.link_id IS NULL""")
    unlinked = cur.fetchall()

    unit_to_person = {}
    doc_to_person = {}
    for c in clusters:
        for uk in c.unit_keys:
            unit_to_person.setdefault(uk, c.id)
        for d in c.doc_ids:
            doc_to_person.setdefault(d, c.id)  # fallback for non-tabular docs only

    linked_unlinked = 0
    for eid, doc_id, row_group in unlinked:
        pid = unit_to_person.get(row_group) if row_group else None
        if not pid and not row_group:
            pid = doc_to_person.get(doc_id)
        if pid:
            cur.execute(
                "INSERT INTO identity_links (person_id, extraction_id, match_method, match_confidence) VALUES (?, ?, ?, ?)",
                (pid, eid, "row_cooccurrence" if row_group else "doc_cooccurrence", 0.85 if row_group else 0.7),
            )
            linked_unlinked += 1

    conn.commit()
    conn.close()

    print(f"Resolved {len(clusters)} persons from {len(by_doc)} documents")
    print(f"  {sum(1 for c in clusters if c.needs_review)} persons flagged needs_review")
    print(f"  {len(escalations)} escalation events logged for the adjudicator")
    print(f"  {linked_unlinked} additional non-name extractions linked via document co-occurrence")
    return clusters, escalations


if __name__ == "__main__":
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else "../../db/breach.db"
    resolve_entities(db_path)
