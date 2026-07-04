"""
26_merge_efo_and_case_duplicates.py

Extends script 17's case-insensitive merge logic to also catch a new
gap surfaced by the BugSigDB load: EFO-disease-term rows stored
as-is (never crosswalked to MONDO) sitting right next to a
pre-existing MONDO or MEDDRA entry for the literal same disease.

Same safety logic as script 17: only merges a group when exactly ONE
member already has a real MONDO id and the rest are non-MONDO
placeholders (MEDDRA: or EFO:). Groups where TWO OR MORE members are
already real, distinct MONDO ids are explicitly left untouched.

Usage:
    python scripts/26_merge_efo_and_case_duplicates.py
"""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    groups = conn.execute(
        """
        SELECT LOWER(name) AS norm, GROUP_CONCAT(mondo_id, '||') AS ids
        FROM disease
        GROUP BY LOWER(name)
        HAVING COUNT(*) > 1
        """
    ).fetchall()

    merged, skipped_ambiguous = 0, 0
    for norm, ids_str in groups:
        ids = ids_str.split("||")
        mondo_ids = [i for i in ids if i.startswith("MONDO:")]
        placeholder_ids = [i for i in ids if not i.startswith("MONDO:")]

        if len(mondo_ids) != 1 or len(placeholder_ids) == 0:
            print(f"  [skip -- ontology ambiguity, not touched] '{norm}': {ids}")
            skipped_ambiguous += 1
            continue

        real_id = mondo_ids[0]
        for placeholder_id in placeholder_ids:
            conn.execute(
                "UPDATE microbe_disease SET mondo_id = ? WHERE mondo_id = ?",
                (real_id, placeholder_id),
            )
            conn.execute("DELETE FROM disease WHERE mondo_id = ?", (placeholder_id,))
            merged += 1
            print(f"  [merge] '{norm}': {placeholder_id} -> {real_id}")

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(
        f"\nDone. {merged} placeholder diseases merged into their real MONDO equivalent, "
        f"{skipped_ambiguous} groups left alone (genuine ontology ambiguity)."
    )
    print("Foreign key check:", "passed" if not issues else issues)
    print(
        "\nIMPORTANT: re-run 13_merge_duplicate_evidence_pairs.py next -- this merge can "
        "recreate duplicate (organism, disease) evidence rows."
    )


if __name__ == "__main__":
    main()
