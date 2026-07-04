"""
04_load_disease_associations.py

Loads real disease-association data from Disbiome
(https://disbiome.ugent.be) into microbe_disease, for organisms
already present in our `organism` table.

Disbiome's /experiment endpoint returns ~11,000 raw experiment-level
rows (one row per organism/disease/publication/detection-method
combination) -- not pre-aggregated. This script:
  1. Fetches all experiments
  2. Matches each row's organism_name against our organism +
     organism_synonym tables (exact match only -- no fuzzy matching yet,
     so most rows naming organisms outside our 15-organism seed list
     will not match, by design at this stage)
  3. Creates disease/study records as needed
  4. Aggregates matched rows per (organism, disease) pair to compute
     replication_count, contradiction_count, and evidence_grade,
     rather than inserting one row per raw experiment

Known limitations (real ones, not hidden):
  - Disease IDs are stored as "MEDDRA:<id>" placeholders, not real
    MONDO IDs. Disbiome doesn't expose a MONDO mapping; that crosswalk
    is separate follow-up work.
  - Study IDs are "DISBIOME_PUB:<id>" -- Disbiome's own internal
    publication ID, not a PMID.
  - Evidence grading is a simple first-pass heuristic (majority
    direction = the call, minority = contradictions), not a
    rigorous meta-analysis.

Usage:
    pip install requests
    python scripts/04_load_disease_associations.py
"""

import sqlite3
from collections import defaultdict
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"

DISBIOME_EXPERIMENT_URL = "https://disbiome.ugent.be:8080/experiment"

DIRECTION_MAP = {
    "Elevated": "increased",
    "Reduced": "decreased",
    "Unaffected": "no_change",
}


def fetch_experiments():
    resp = requests.get(DISBIOME_EXPERIMENT_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()


def build_organism_lookup(conn):
    """Map organism name (lowercased) -> ncbi_taxid, using both the
    organism table and organism_synonym table, so a Disbiome row
    naming an organism by an old/synonym name still matches."""
    lookup = {}
    for taxid, name in conn.execute("SELECT ncbi_taxid, name FROM organism"):
        lookup[name.lower()] = taxid
    for taxid, syn in conn.execute("SELECT ncbi_taxid, synonym_name FROM organism_synonym"):
        lookup.setdefault(syn.lower(), taxid)
    return lookup


def ensure_disease(conn, meddra_id, disease_name):
    disease_id = f"MEDDRA:{int(meddra_id)}"
    conn.execute(
        "INSERT OR IGNORE INTO disease (mondo_id, name, category) VALUES (?, ?, ?)",
        (disease_id, disease_name, "unmapped -- MedDRA only, no MONDO crosswalk yet"),
    )
    return disease_id


def ensure_study(conn, publication_id, method_name):
    study_id = f"DISBIOME_PUB:{int(publication_id)}"
    conn.execute(
        "INSERT OR IGNORE INTO study (study_id, study_type, detection_method) VALUES (?, ?, ?)",
        (study_id, "observational", method_name),
    )
    return study_id


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    print("Fetching experiments from Disbiome ...")
    experiments = fetch_experiments()
    print(f"  {len(experiments)} raw experiment rows received")

    organism_lookup = build_organism_lookup(conn)
    print(f"  matching against {len(organism_lookup)} known organism names/synonyms")

    grouped = defaultdict(list)
    matched, unmatched_names = 0, set()

    for exp in experiments:
        organism_name = (exp.get("organism_name") or "").strip()
        taxid = organism_lookup.get(organism_name.lower())
        if not taxid:
            unmatched_names.add(organism_name)
            continue

        if exp.get("qualitative_outcome") not in DIRECTION_MAP:
            continue
        if not exp.get("meddra_id"):
            continue

        matched += 1
        grouped[(taxid, exp["meddra_id"])].append(exp)

    print(
        f"  {matched} rows matched a known organism; "
        f"{len(unmatched_names)} distinct organism names in Disbiome were not in our table"
    )

    inserted = 0
    for (taxid, meddra_id), rows in grouped.items():
        first = rows[0]
        disease_id = ensure_disease(conn, meddra_id, first["disease_name"])

        directions = [DIRECTION_MAP[r["qualitative_outcome"]] for r in rows]
        majority_direction = max(set(directions), key=directions.count)
        replication_count = directions.count(majority_direction)
        contradiction_count = len(directions) - replication_count

        if contradiction_count > 0:
            grade = "D"
        elif replication_count >= 2:
            grade = "A"
        else:
            grade = "B"

        study_id = ensure_study(conn, first["publication_id"], first.get("method_name"))

        conn.execute(
            """
            INSERT INTO microbe_disease
                (ncbi_taxid, mondo_id, direction, study_id, evidence_grade,
                 replication_count, contradiction_count, source_db, last_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, date('now'))
            """,
            (
                taxid, disease_id, majority_direction, study_id, grade,
                replication_count, contradiction_count, "Disbiome",
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()

    print(
        f"\nDone. Inserted {inserted} aggregated disease associations "
        f"across {len(grouped)} (organism, disease) pairs."
    )
    if unmatched_names:
        sample = sorted(unmatched_names)[:10]
        print(f"\nSample of unmatched organism names (first 10 of {len(unmatched_names)}):")
        for n in sample:
            print(f"  - {n}")


if __name__ == "__main__":
    main()
