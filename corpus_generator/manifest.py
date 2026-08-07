"""
manifest.py — the ground-truth answer key.

One row per (document, person, element) planting. A single document can
plant elements for many people (multi-person docs). "edge_case" tags let
you filter the manifest down to just the adversarial cases when scoring.
"""

import json

CATEGORIES = [
    "full_name", "dob", "ssn", "dl_number", "passport_number",
    "financial_account", "card_number", "medical", "login_credentials",
    "home_address", "phone", "email",
]

class Manifest:
    def __init__(self):
        self.rows = []
        self.documents = []  # doc-level metadata (type, quarantine reason, etc.)

    def add_document(self, doc_id, filename, file_type, notes=None, quarantine_reason=None):
        self.documents.append({
            "doc_id": doc_id,
            "filename": filename,
            "file_type": file_type,
            "notes": notes,
            "quarantine_reason": quarantine_reason,
        })

    def plant(self, doc_id, person_id, category, value, edge_case=None, is_partial=False):
        assert category in CATEGORIES, f"unknown category {category}"
        self.rows.append({
            "doc_id": doc_id,
            "person_id": person_id,
            "category": category,
            "value": value,
            "edge_case": edge_case,
            "is_partial": is_partial,
        })

    def plant_false_positive(self, doc_id, looks_like, actual_value, note):
        """Log a deliberate distractor so scoring can penalize false-positive flags."""
        self.rows.append({
            "doc_id": doc_id,
            "person_id": None,
            "category": "FALSE_POSITIVE_TRAP",
            "value": actual_value,
            "edge_case": f"false_positive:{looks_like}",
            "note": note,
        })

    def save(self, path_rows="output/manifest/manifest.json", path_docs="output/manifest/documents.json"):
        with open(path_rows, "w") as f:
            json.dump(self.rows, f, indent=2)
        with open(path_docs, "w") as f:
            json.dump(self.documents, f, indent=2)
        print(f"Manifest: {len(self.rows)} planted-element rows across {len(self.documents)} documents")
