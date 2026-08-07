"""
score.py — Stage: accuracy measurement vs. the corpus generator's manifest.

Since the pipeline assigns its own person_ids (RES_0001...) that don't
match the manifest's P#### ids, we first build a mapping by matching on
strong identifiers (SSN is close to unique in this corpus; DOB as backup),
then compute:
  - Person-level recall/precision (did every true person get a resolved
    cluster, and does every resolved cluster correspond to exactly one
    true person?)
  - Per-category flag accuracy
  - Shared-name-trap check: were the 8 deliberate collision pairs kept split?
"""

import json
import sqlite3
from collections import defaultdict


def load_manifest(manifest_dir: str):
    with open(f"{manifest_dir}/manifest.json") as f:
        rows = json.load(f)
    with open(f"{manifest_dir}/people_pool.json") as f:
        people = json.load(f)
    return rows, people


def score(db_path: str, manifest_dir: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    manifest_rows, people = load_manifest(manifest_dir)
    people_by_id = {p["person_id"]: p for p in people}
    true_ssn_to_pid = {p["ssn"]: p["person_id"] for p in people}
    true_dob_to_pids = defaultdict(list)
    for p in people:
        true_dob_to_pids[p["dob"]].append(p["person_id"])

    # ---- Build resolved-cluster -> true-person mapping via SSN (primary), DOB (fallback) ----
    cur.execute("SELECT person_id, dob FROM persons")
    resolved = cur.fetchall()

    cur.execute("""SELECT il.person_id, e.category, e.normalized_value
                   FROM identity_links il JOIN extractions e ON il.extraction_id = e.extraction_id""")
    links = cur.fetchall()

    resolved_ssns = defaultdict(set)
    for pid, cat, val in links:
        if cat == "ssn":
            resolved_ssns[pid].add(val)

    res_to_true = {}
    for res_pid, dob in resolved:
        ssns = resolved_ssns.get(res_pid, set())
        matched_true = None
        for s in ssns:
            if s in true_ssn_to_pid:
                matched_true = true_ssn_to_pid[s]
                break
        if not matched_true and dob and dob in true_dob_to_pids and len(true_dob_to_pids[dob]) == 1:
            matched_true = true_dob_to_pids[dob][0]
        if matched_true:
            res_to_true[res_pid] = matched_true

    # ---- Person-level recall/precision ----
    true_person_ids = set(people_by_id.keys())
    matched_true_ids = set(res_to_true.values())
    recall = len(matched_true_ids) / len(true_person_ids)

    mapped_clusters = len(res_to_true)
    total_clusters = len(resolved)
    precision = mapped_clusters / total_clusters if total_clusters else 0

    # true people matched by MORE THAN ONE resolved cluster = over-splitting instances
    true_to_res = defaultdict(list)
    for res_pid, true_pid in res_to_true.items():
        true_to_res[true_pid].append(res_pid)
    oversplit = {k: v for k, v in true_to_res.items() if len(v) > 1}

    # ---- Shared-name-trap check ----
    collision_pairs = [p for p in people if p["is_name_collision_with"]]
    trap_results = []
    for p in collision_pairs:
        other_id = p["is_name_collision_with"]
        p_cluster = next((r for r, t in res_to_true.items() if t == p["person_id"]), None)
        other_cluster = next((r for r, t in res_to_true.items() if t == other_id), None)
        kept_split = (p_cluster is not None and other_cluster is not None and p_cluster != other_cluster)
        trap_results.append({
            "pair": (p["person_id"], other_id),
            "names": p["full_name"],
            "kept_split": kept_split,
            "resolved_clusters": (p_cluster, other_cluster),
        })

    # ---- Per-category flag accuracy (recall only, on matched persons) ----
    true_flags = defaultdict(set)  # (true_pid, category) present in manifest
    for r in manifest_rows:
        if r.get("person_id") and r["category"] != "FALSE_POSITIVE_TRAP":
            true_flags[r["person_id"]].add(r["category"])

    resolved_flags = defaultdict(set)
    for pid, cat, val in links:
        true_pid = res_to_true.get(pid)
        if true_pid:
            resolved_flags[true_pid].add(cat)

    categories = sorted({c for cats in true_flags.values() for c in cats})
    cat_recall = {}
    for cat in categories:
        true_people_with_cat = {pid for pid, cats in true_flags.items() if cat in cats}
        if not true_people_with_cat:
            continue
        caught = {pid for pid in true_people_with_cat if cat in resolved_flags.get(pid, set())}
        cat_recall[cat] = len(caught) / len(true_people_with_cat)

    # ---- Report ----
    report = {
        "person_level": {
            "true_people": len(true_person_ids),
            "resolved_clusters": total_clusters,
            "clusters_mapped_to_a_true_person": mapped_clusters,
            "true_people_found_recall": round(recall, 3),
            "cluster_mapping_precision": round(precision, 3),
            "oversplit_true_people_count": len(oversplit),
            "oversplit_examples": dict(list(oversplit.items())[:5]),
        },
        "shared_name_trap": trap_results,
        "per_category_recall": {k: round(v, 3) for k, v in cat_recall.items()},
    }

    with open("score_report.json", "w") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else "../../db/breach.db"
    manifest_dir = sys.argv[2] if len(sys.argv) > 2 else "../../corpus_generator/output/manifest"
    score(db_path, manifest_dir)
