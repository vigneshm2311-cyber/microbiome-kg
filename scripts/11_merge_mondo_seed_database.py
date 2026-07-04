"""
11_merge_mondo_seed_database.py

Merges a separately-built database (broader NCBI taxonomy coverage,
disease backbone built directly from MONDO) into our schema, then
re-aggregates its raw Disbiome association data using OUR
evidence-grading logic -- combining its wider ontology coverage with
the replication/contradiction/grade rigor that file doesn't have.

The source file uses a different schema (organism/disease/association,
no study table, no evidence aggregation -- 9,877 raw rows cover only
7,700 distinct organism-disease pairs, meaning the same pair often
appears many times unaggregated). This script:

  1. Merges its `organism` table into ours (same NCBI taxid scheme --
     a direct INSERT OR IGNORE, no name matching needed)
  2. Merges its `disease` table into ours (same MONDO id scheme,
     carrying over its meddra xref where present)
  3. Re-aggregates its `association.raw` column (the original Disbiome
     experiment JSON, already cached there) into our microbe_disease
     table using the same replication/contradiction/grade logic as
     04_load_disease_associations.py -- re-deriving direction from
     the raw qualitative_outcome field rather than trusting that
     file's own pre-labeled `direction` column, for consistency with
     how the rest of this project grades evidence
  4. Skips any (organism, disease) pair we already have a graded
     association for -- this adds new coverage, it doesn't re-grade
     work 04_load_disease_associations.py already did
  5. Creates bare DISBIOME_PUB:<id> study placeholders for any new
     publication IDs this introduces -- re-run
     09_map_studies_to_pmid.py and 10_score_study_quality.py
     afterward to enrich them the same way every other study in this
     project was enriched

Usage:
    Copy the uploaded file to data/raw/mondo_seed.db first, then:
    python scripts/11_merge_mondo_seed_database.py
"""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
SOURCE_PATH = PROJECT_ROOT / "data" / "raw" / "mondo_seed.db"

DIRECTION_MAP = {
    "Elevated": "increased",
    "Reduced": "decreased",
    "Unaffected": "no_change",
}


def main():
    if not SOURCE_PATH.exists():
        raise SystemExit(f"Expected the uploaded file at {SOURCE_PATH} -- copy it there first.")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(f"ATTACH DATABASE '{SOURCE_PATH}' AS src")

    # --- 1. Merge organisms (same NCBI taxid scheme) ------------------------
    before = conn.execute("SELECT COUNT(*) FROM organism").fetchone()[0]
    conn.execute(
        """
        INSERT OR IGNORE INTO organism (ncbi_taxid, name, rank, source_db, last_verified)
        SELECT CAST(taxid AS TEXT), scientific_name, rank, 'mondo_seed_merge', date('now')
        FROM src.organism
        """
    )
    after = conn.execute("SELECT COUNT(*) FROM organism").fetchone()[0]
    print(f"Organisms: {before} -> {after} ({after - before} new)")

    # --- 2. Merge diseases (same MONDO id scheme) ---------------------------
    before = conn.execute("SELECT COUNT(*) FROM disease").fetchone()[0]
    conn.execute(
        """
        INSERT OR IGNORE INTO disease (mondo_id, name, meddra_id, mondo_mapping_confidence)
        SELECT mondo_id, label,
               CASE WHEN xref_meddra LIKE 'MedDRA:%' THEN substr(xref_meddra, 8) ELSE NULL END,
               CASE WHEN xref_meddra LIKE 'MedDRA:%' THEN 'xref' ELSE NULL END
        FROM src.disease
        """
    )
    after = conn.execute("SELECT COUNT(*) FROM disease").fetchone()[0]
    print(f"Diseases: {before} -> {after} ({after - before} new)")

    # --- 3. Re-aggregate raw associations with OUR evidence logic ----------
    rows = conn.execute("SELECT taxid, mondo_id, raw FROM src.association").fetchall()
    print(f"\nRe-aggregating {len(rows)} raw association rows ...")

    grouped = defaultdict(list)
    for taxid, mondo_id, raw in rows:
        if not taxid or not mondo_id:
            continue
        taxid = str(taxid)
        exp = json.loads(raw)
        if exp.get("qualitative_outcome") not in DIRECTION_MAP:
            continue
        grouped[(taxid, mondo_id)].append(exp)

    inserted, skipped_existing = 0, 0
    for (taxid, mondo_id), exps in grouped.items():
        existing = conn.execute(
            "SELECT 1 FROM microbe_disease WHERE ncbi_taxid = ? AND mondo_id = ?",
            (taxid, mondo_id),
        ).fetchone()
        if existing:
            skipped_existing += 1
            continue

        directions = [DIRECTION_MAP[e["qualitative_outcome"]] for e in exps]
        majority = max(set(directions), key=directions.count)
        replication = directions.count(majority)
        contradiction = len(directions) - replication
        grade = "D" if contradiction > 0 else ("A" if replication >= 2 else "B")

        first = exps[0]
        pub_id = first.get("publication_id")
        study_id = f"DISBIOME_PUB:{pub_id}" if pub_id else None
        if study_id:
            conn.execute(
                "INSERT OR IGNORE INTO study (study_id, study_type, detection_method) VALUES (?, ?, ?)",
                (study_id, "observational", first.get("method_name")),
            )

        conn.execute(
            """
            INSERT INTO microbe_disease
                (ncbi_taxid, mondo_id, direction, study_id, evidence_grade,
                 replication_count, contradiction_count, source_db, last_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, date('now'))
            """,
            (
                taxid, mondo_id, majority, study_id, grade,
                replication, contradiction, "Disbiome (via mondo_seed merge)",
            ),
        )
        inserted += 1

    conn.commit()
    conn.execute("DETACH DATABASE src")
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(
        f"\nDone. {inserted} new evidence-graded associations added, "
        f"{skipped_existing} (organism, disease) pairs already had a graded "
        f"association from our own earlier load and were left untouched."
    )
    print(
        "\nNext: re-run 09_map_studies_to_pmid.py and 10_score_study_quality.py "
        "to enrich the new DISBIOME_PUB study placeholders this just created."
    )
    if issues:
        print(f"WARNING: {len(issues)} foreign key issues detected:")
        for issue in issues[:10]:
            print(f"  {issue}")
    else:
        print("Foreign key integrity check passed.")


if __name__ == "__main__":
    main()
