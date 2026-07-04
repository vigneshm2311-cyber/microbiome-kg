"""
02_smoke_test.py

Inserts one realistic, hand-built example end to end (the same
F. prausnitzii / Crohn's disease example from the schema doc) and
queries it back out via a join — just to prove the database actually
works before we connect any real data sources to it.

Usage:
    python scripts/02_smoke_test.py
"""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"


def run_smoke_test() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # --- 1. Backbone records -------------------------------------------------
    cur.execute(
        "INSERT INTO organism (ncbi_taxid, name, rank, is_cultured, source_db, last_verified) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("853", "Faecalibacterium prausnitzii", "species", True, "NCBI Taxonomy", "2026-06-24"),
    )

    cur.execute(
        "INSERT INTO disease (mondo_id, name, category) VALUES (?, ?, ?)",
        ("MONDO:0005011", "Crohn disease", "autoimmune/inflammatory"),
    )

    cur.execute(
        "INSERT INTO body_site (uberon_id, name) VALUES (?, ?)",
        ("UBERON:0001155", "colon"),
    )

    cur.execute(
        "INSERT INTO study (study_id, study_type, sample_size, population, detection_method, publication_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("PMID:12345678", "observational", 45, "adult human, Crohn's vs healthy", "16S", "2014-03-01"),
    )

    # --- 2. Edge: the disease association, evidence-graded -------------------
    cur.execute(
        "INSERT INTO microbe_disease "
        "(ncbi_taxid, mondo_id, direction, study_id, evidence_grade, replication_count, contradiction_count, source_db) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("853", "MONDO:0005011", "decreased", "PMID:12345678", "A", 3, 0, "Disbiome"),
    )

    # --- 3. Edge: the normal-flora data point (healthy colon baseline) -------
    cur.execute(
        "INSERT INTO microbe_bodysite "
        "(ncbi_taxid, uberon_id, cohort_health_status, prevalence, abundance, flora_classification, source_db) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("853", "UBERON:0001155", "healthy", 0.92, 0.045, "core", "MGnify"),
    )

    conn.commit()

    # --- 4. Read it back via a join, the way a real query would ---------------
    print("Disease associations for Faecalibacterium prausnitzii:\n")
    rows = cur.execute(
        """
        SELECT o.name, d.name, md.direction, md.evidence_grade,
               md.replication_count, md.contradiction_count, s.study_type, s.sample_size
        FROM microbe_disease md
        JOIN organism o ON o.ncbi_taxid = md.ncbi_taxid
        JOIN disease d  ON d.mondo_id   = md.mondo_id
        JOIN study s    ON s.study_id   = md.study_id
        WHERE o.ncbi_taxid = ?
        """,
        ("853",),
    ).fetchall()

    for r in rows:
        print(
            f"  {r[0]} — {r[1]}: {r[2]} (grade {r[3]}, "
            f"{r[4]} replications, {r[5]} contradictions; "
            f"{r[6]} study, n={r[7]})"
        )

    print("\nNormal flora status at body site:\n")
    rows = cur.execute(
        """
        SELECT o.name, b.name, mb.cohort_health_status, mb.prevalence,
               mb.abundance, mb.flora_classification
        FROM microbe_bodysite mb
        JOIN organism o   ON o.ncbi_taxid = mb.ncbi_taxid
        JOIN body_site b  ON b.uberon_id  = mb.uberon_id
        WHERE o.ncbi_taxid = ?
        """,
        ("853",),
    ).fetchall()

    for r in rows:
        print(
            f"  {r[0]} in {r[1]} ({r[2]} cohort): "
            f"prevalence={r[3]*100:.0f}%, abundance={r[4]*100:.1f}%, "
            f"classification={r[5]}"
        )

    conn.close()


if __name__ == "__main__":
    run_smoke_test()
