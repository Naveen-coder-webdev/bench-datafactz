"""
generate.py — builds the full synthetic breach corpus + manifest.

Run: python3 generate.py

Produces:
  output/corpus/*             the document dump (what your pipeline ingests)
  output/manifest/manifest.json     every planted element (the answer key)
  output/manifest/documents.json    per-document metadata
  output/manifest/people_pool.json  the underlying identity pool
"""

import os
import random
import shutil

from people import build_person_pool
from manifest import Manifest
import planters as pl

random.seed(7)

N_PEOPLE = 160
N_SHARED_NAME_PAIRS = 8
TARGET_DOCS = 520


def reset_output():
    if os.path.exists("output/corpus"):
        shutil.rmtree("output/corpus")
    os.makedirs("output/corpus", exist_ok=True)
    os.makedirs("output/manifest", exist_ok=True)


def main():
    reset_output()
    people = build_person_pool(N_PEOPLE, N_SHARED_NAME_PAIRS)
    manifest = Manifest()
    doc_counter = 1

    def next_id():
        nonlocal doc_counter
        did = f"DOC_{doc_counter:04d}"
        doc_counter += 1
        return did

    # ---- 1) One-person narrative documents across text-bearing formats ----
    # Every person gets at least one doc; many get several (different formats)
    # so entity resolution has to link across documents, not just within one.
    single_doc_planters = [
        ("docx", pl.plant_docx),
        ("pdf_digital", pl.plant_digital_pdf),
        ("txt", pl.plant_txt),
        ("html", pl.plant_html),
    ]

    for person in people:
        n_docs_for_person = random.choice([1, 1, 2, 2, 3])  # most people appear 1-3x
        chosen = random.sample(single_doc_planters, k=min(n_docs_for_person, len(single_doc_planters)))
        for fmt, fn in chosen:
            edge_case = "name_variant" if (person["aliases"] and random.random() < 0.3) else None
            fn(next_id(), person, manifest, edge_case=edge_case)

    # ---- 2) Scanned PDFs (OCR-required) for a subset ----
    scan_subjects = random.sample(people, k=60)
    for person in scan_subjects:
        pl.plant_scanned_pdf(next_id(), person, manifest)

    # ---- 3) Screenshot-of-spreadsheet problem files ----
    for _ in range(6):
        subset = random.sample(people, k=random.randint(4, 9))
        pl.plant_screenshot(next_id(), subset, manifest)

    # ---- 4) Multi-person XLSX exports (incl. one big 80-person dump) ----
    pl.plant_xlsx(next_id(), random.sample(people, k=80), manifest, include_fp_traps=True)
    for _ in range(5):
        subset = random.sample(people, k=random.randint(10, 25))
        edge = "name_variant" if random.random() < 0.4 else None
        pl.plant_xlsx(next_id(), subset, manifest, edge_case=edge, include_fp_traps=(random.random() < 0.5))

    # ---- 5) CSV exports with partial identifiers (last-4 SSN only) ----
    for _ in range(6):
        subset = random.sample(people, k=random.randint(15, 30))
        pl.plant_csv(next_id(), subset, manifest)

    # ---- 6) EML with attachments ----
    for person in random.sample(people, k=90):
        attach = None
        attach_name = None
        if random.random() < 0.4:
            attach = f"Attached record for {person['full_name']}, DOB {person['dob']}, phone {person['phone']}.".encode()
            attach_name = "record_export.txt"
        edge_case = "name_variant" if (person["aliases"] and random.random() < 0.3) else None
        pl.plant_eml(next_id(), person, manifest, edge_case=edge_case,
                     attach_doc_bytes=attach, attach_name=attach_name)

    # ---- 7) Shared-name collision pairs: force them into the SAME small
    #          set of documents so resolution must actively split them ----
    collision_people = [p for p in people if p["is_name_collision_with"]]
    for p in collision_people:
        pl.plant_docx(next_id(), p, manifest, edge_case="shared_name_trap")
        pl.plant_eml(next_id(), p, manifest, edge_case="shared_name_trap")

    # ---- 8) Problem files: corrupt, zero-byte, wrong extension, password ----
    for _ in range(8):
        pl.plant_zero_byte(next_id(), manifest)
    for _ in range(8):
        pl.plant_corrupt(next_id(), manifest)
    for person in random.sample(people, k=8):
        pl.plant_wrong_extension(next_id(), person, manifest)
    for person in random.sample(people, k=8):
        pl.plant_password_protected(next_id(), person, manifest)

    # ---- 9) Top up to target doc count with more single-person variety ----
    while doc_counter - 1 < TARGET_DOCS:
        person = random.choice(people)
        fmt, fn = random.choice(single_doc_planters)
        edge_case = "name_variant" if (person["aliases"] and random.random() < 0.3) else None
        fn(next_id(), person, manifest, edge_case=edge_case)

    manifest.save()

    n_docs = doc_counter - 1
    n_people_total = len(people)
    print(f"\nCorpus generation complete.")
    print(f"  Documents: {n_docs}")
    print(f"  People:    {n_people_total} ({N_SHARED_NAME_PAIRS} shared-name collision pairs)")
    print(f"  Output:    output/corpus/  +  output/manifest/")


if __name__ == "__main__":
    main()
