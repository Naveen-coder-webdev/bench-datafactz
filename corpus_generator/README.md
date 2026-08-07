# Breach Corpus Generator — DataFactZ Use Case 3

Reproducible synthetic breach-dump generator with a ground-truth manifest.
**Every identity and document is synthetic** (Faker-generated, seeded for
reproducibility). No real PII anywhere in this corpus.

## Run it

```bash
pip install faker python-docx reportlab openpyxl Pillow pypdf
python3 generate.py
```

Outputs:
- `output/corpus/` — the document dump (520 files) — this is what your
  ingestion pipeline points at.
- `output/manifest/manifest.json` — the answer key: one row per
  (document, person, PII category) planting. Score your pipeline against
  this for recall/precision.
- `output/manifest/documents.json` — per-document metadata: file type,
  and quarantine reason for the deliberately broken files.
- `output/manifest/people_pool.json` — the underlying 168-person identity
  pool, including which records are deliberate shared-name collisions.

## What's in the corpus

| Requirement (brief §3) | How it's satisfied |
|---|---|
| 500+ documents | 520 generated |
| 150+ unique individuals | 168 (160 base + 8 forced into shared-name pairs) |
| 6+ file types | docx, pdf (digital), pdf (scanned/OCR-only), xlsx, csv, eml (w/ attachments), txt, html, png |
| PII in prose/tables/signatures/scans, not neatly labeled | Narrative letter/incident/chat/email templates; tabular exports; scanned fax image |
| Name variants (nickname/maiden/initials/misspelling) | `people.py::_attach_variants`, tagged `edge_case="name_variant"` in manifest |
| Shared name, different people | 8 forced collision pairs, `is_name_collision_with` in people_pool.json |
| Partial identifiers (SSN last-4, split across docs) | CSV exports plant last-4 only (`edge_case="partial_identifier_last4"`); full SSN for the same person planted separately elsewhere in the corpus |
| Multi-person documents | XLSX exports (one with 80 people), spreadsheet screenshots |
| False-positive traps | Order numbers shaped like SSNs, `TEST USER` placeholder rows — logged as `FALSE_POSITIVE_TRAP` rows, not real person plants |
| Problem files | 8 zero-byte, 8 corrupt, 8 wrong-extension (PNG saved as `.docx`), 8 password-protected PDFs |

## Manifest schema

```json
{
  "doc_id": "DOC_0299",
  "person_id": "P0088",
  "category": "ssn",
  "value": "881-68-8603",
  "edge_case": "requires_ocr",
  "is_partial": false
}
```

`category` is one of: `full_name, dob, ssn, dl_number, passport_number,
financial_account, card_number, medical, login_credentials, home_address,
phone, email`. Rows with `category == "FALSE_POSITIVE_TRAP"` have
`person_id: null` — they exist to test that your extractor does NOT flag
them, not to be matched.

## Scoring your pipeline against this

- **Person-level recall/precision**: for each `person_id` in the manifest,
  check whether your exposure table produced a matching row.
- **Per-category flag accuracy**: for each (person, category) pair, check
  whether your table's flag for that category is set correctly.
- **Entity resolution correctness**: verify that `is_name_collision_with`
  pairs in `people_pool.json` were NOT merged, and that everyone with
  `edge_case: "name_variant"` planted elements WAS correctly linked to
  their single true `person_id`.
- **False-positive rate**: count how many `FALSE_POSITIVE_TRAP` rows your
  system incorrectly turned into a flagged person/exposure.

## Extending it

- Bump `N_PEOPLE`, `N_SHARED_NAME_PAIRS`, or `TARGET_DOCS` in `generate.py`.
- Add a new file type by writing a `plant_*` function in `planters.py`
  following the existing pattern (write file, call `manifest.plant(...)`
  for every element you embedded) and wiring it into `generate.py`.
- Multilingual stretch goal: add locale-specific Faker providers and a
  `language` field per document.
