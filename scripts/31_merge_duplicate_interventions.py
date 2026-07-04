"""
31_merge_duplicate_interventions.py

Fixes a real gap found via direct inspection: two intervention rows
can refer to the literal same real-world substance but be loaded
under two different ID schemes by two different sources -- found via
"Azithromycin" existing as both Prestw-1234 (Maier et al. 2018, drug)
and CHEBI:2955 (BugSigDB, chemical).

Usage:
    python scripts/31_merge_duplicate_interventions.py
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
        SELECT LOWER(name) AS norm, GROUP_CONCAT(intervention_id, '||') AS ids
        FROM intervention
        GROUP BY LOWER(name)
        HAVING COUNT(*) > 1
        """
    ).fetchall()

    print(f"Found {len(groups)} duplicated intervention name(s)\n")

    merged_names = 0
    for norm, ids_str in groups:
        ids = ids_str.split("||")
        survivor = ids[0]
        duplicates = ids[1:]

        for dup_id in duplicates:
            conn.execute(
                "UPDATE microbe_intervention SET intervention_id = ? WHERE intervention_id = ?",
                (survivor, dup_id),
            )
            conn.execute("DELETE FROM intervention WHERE intervention_id = ?", (dup_id,))
            merged_names += 1
            print(f"  [merge] '{norm}': {dup_id} -> {survivor}")

    conn.commit()

    dupe_pairs = conn.execute(
        """
        SELECT ncbi_taxid, intervention_id, COUNT(*) c
        FROM microbe_intervention
        GROUP BY ncbi_taxid, intervention_id
        HAVING c > 1
        """
    ).fetchall()

    merged_pairs = 0
    for taxid, intervention_id, count in dupe_pairs:
        rows = conn.execute(
            """
            SELECT id, effect_direction, adjusted_pvalue, evidence_level, source_db
            FROM microbe_intervention
            WHERE ncbi_taxid = ? AND intervention_id = ?
            """,
            (taxid, intervention_id),
        ).fetchall()
        survivor_row = next((r for r in rows if r[2] is not None), rows[0])
        sources = sorted({r[4] for r in rows if r[4]})
        conn.execute(
            "UPDATE microbe_intervention SET source_db = ? WHERE id = ?",
            (" + ".join(sources), survivor_row[0]),
        )
        for r in rows:
            if r[0] != survivor_row[0]:
                conn.execute("DELETE FROM microbe_intervention WHERE id = ?", (r[0],))
        merged_pairs += 1
        print(f"  [combine duplicate pair] taxid={taxid} intervention={intervention_id}: {count} rows -> 1, sources: {sources}")

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(f"\nDone. {merged_names} duplicate intervention definition(s) merged, {merged_pairs} resulting duplicate evidence pair(s) combined.")
    print("Foreign key check:", "passed" if not issues else issues)


if __name__ == "__main__":
    main()
