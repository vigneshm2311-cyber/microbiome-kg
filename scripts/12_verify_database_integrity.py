"""
12_verify_database_integrity.py

A comprehensive integrity check across the whole database. Checks
things the schema doesn't enforce at the SQL level (duplicate
evidence rows across separate aggregation passes, orphaned
references, type regressions) plus targeted spot-checks on specific
things this project got wrong before and fixed.

Usage:
    python scripts/12_verify_database_integrity.py
"""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"


def check(label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))
    return passed


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    all_passed = True

    print("=== Table row counts ===")
    for table in [
        "organism", "organism_synonym", "disease", "body_site", "phenotype",
        "metabolite", "intervention", "study", "microbe_disease",
        "microbe_bodysite", "microbe_metabolite", "microbe_intervention",
    ]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count}")

    print("\n=== Integrity checks ===")

    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    all_passed &= check(
        "Foreign key integrity", len(issues) == 0, f"{len(issues)} violations" if issues else ""
    )
    for i in issues[:10]:
        print(f"    {i}")

    dupes = conn.execute(
        """
        SELECT ncbi_taxid, mondo_id, COUNT(*) c FROM microbe_disease
        GROUP BY ncbi_taxid, mondo_id HAVING c > 1
        """
    ).fetchall()
    all_passed &= check(
        "No duplicate (organism, disease) pairs in microbe_disease",
        len(dupes) == 0,
        f"{len(dupes)} duplicated pairs" if dupes else "",
    )
    for d in dupes[:10]:
        print(f"    taxid={d[0]} mondo_id={d[1]} appears {d[2]} times")

    null_grades = conn.execute(
        "SELECT COUNT(*) FROM microbe_disease WHERE evidence_grade IS NULL"
    ).fetchone()[0]
    all_passed &= check(
        "No microbe_disease rows missing evidence_grade", null_grades == 0,
        f"{null_grades} rows" if null_grades else "",
    )

    total = conn.execute("SELECT COUNT(*) FROM microbe_disease").fetchone()[0]
    graded = conn.execute(
        "SELECT COUNT(*) FROM microbe_disease WHERE evidence_grade IN ('A','B','C','D')"
    ).fetchone()[0]
    all_passed &= check(
        "Evidence grade distribution sums to total row count",
        total == graded, f"{graded} graded vs {total} total",
    )

    col_type = next(r[2] for r in conn.execute("PRAGMA table_info(study)") if r[1] == "quality_score")
    all_passed &= check(
        "study.quality_score column type is REAL, not TEXT",
        col_type.upper() == "REAL", f"actual type: {col_type}",
    )

    fixture = conn.execute("SELECT 1 FROM study WHERE study_id = 'PMID:12345678'").fetchone()
    all_passed &= check(
        "Smoke-test fixture (fake PMID:12345678) was not reintroduced", fixture is None
    )

    synonym = conn.execute(
        "SELECT 1 FROM organism_synonym WHERE ncbi_taxid = '1598' "
        "AND synonym_name LIKE 'Lactobacillus reuteri%'"
    ).fetchone()
    all_passed &= check(
        "Limosilactobacillus reuteri retains its 'Lactobacillus reuteri' synonym",
        synonym is not None,
    )

    bad_names = conn.execute(
        "SELECT COUNT(*) FROM organism WHERE name IS NULL OR TRIM(name) = ''"
    ).fetchone()[0]
    all_passed &= check(
        "No organisms with missing names", bad_names == 0, f"{bad_names} rows" if bad_names else ""
    )

    name_dupes = conn.execute(
        "SELECT LOWER(name) AS norm, GROUP_CONCAT(name, ' / ') AS variants, COUNT(*) c "
        "FROM disease GROUP BY LOWER(name) HAVING c > 1"
    ).fetchall()
    all_passed &= check(
        "No disease name shared by multiple MONDO IDs (case-insensitive)",
        len(name_dupes) == 0, f"{len(name_dupes)} duplicated names" if name_dupes else "",
    )
    for norm, variants, c in name_dupes[:10]:
        print(f"    '{variants}' appears under {c} different mondo_ids")

    orphans = conn.execute(
        """
        SELECT COUNT(*) FROM study s
        WHERE s.study_id LIKE 'DISBIOME_PUB:%'
        AND NOT EXISTS (SELECT 1 FROM microbe_disease md WHERE md.study_id = s.study_id)
        AND NOT EXISTS (SELECT 1 FROM microbe_metabolite mm WHERE mm.study_id = s.study_id)
        AND NOT EXISTS (SELECT 1 FROM microbe_intervention mi WHERE mi.study_id = s.study_id)
        """
    ).fetchone()[0]
    all_passed &= check(
        "No orphaned DISBIOME_PUB study placeholders", orphans == 0,
        f"{orphans} orphaned rows" if orphans else "",
    )

    conn.close()
    print("\n" + ("ALL CHECKS PASSED" if all_passed else "SOME CHECKS FAILED -- see above"))


if __name__ == "__main__":
    main()
