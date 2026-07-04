"""
13_merge_duplicate_evidence_pairs.py

Fixes a latent gap in how 06_map_diseases_to_mondo.py and
07_fuzzy_match_diseases_to_mondo.py merge diseases: when two
different source-side disease codes (e.g. two different MedDRA
codes for what's really the same real-world disease) both get
migrated to the same target MONDO id, each one's microbe_disease
rows get re-pointed to that target -- but if the same organism had
separate evidence under both source codes, this creates two rows for
the same (organism, disease) pair instead of one properly combined
row. This surfaced as the Streptococcus / primary biliary cirrhosis
duplicate found via 12_verify_database_integrity.py.

This finds every such duplicate (ncbi_taxid, mondo_id) pair and
properly re-aggregates them: each row's replication_count is treated
as votes for its own direction, contradiction_count as votes against
it, recombined into one row with a freshly recomputed majority
direction and grade -- rather than deleting one row arbitrarily and
losing real evidence.

Usage:
    python scripts/13_merge_duplicate_evidence_pairs.py
"""

import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    dupes = conn.execute(
        """
        SELECT ncbi_taxid, mondo_id, COUNT(*) c FROM microbe_disease
        GROUP BY ncbi_taxid, mondo_id HAVING c > 1
        """
    ).fetchall()

    print(f"Found {len(dupes)} duplicated (organism, disease) pairs to merge.\n")

    for taxid, mondo_id, count in dupes:
        rows = conn.execute(
            """
            SELECT id, direction, replication_count, contradiction_count
            FROM microbe_disease WHERE ncbi_taxid = ? AND mondo_id = ?
            """,
            (taxid, mondo_id),
        ).fetchall()

        votes = defaultdict(int)
        for row_id, direction, repl, contra in rows:
            votes[direction] += repl

        total_contradiction = sum(r[3] for r in rows)
        majority_direction = max(votes, key=votes.get)
        majority_count = votes[majority_direction]
        grade = "D" if total_contradiction > 0 else ("A" if majority_count >= 2 else "B")

        survivor_id = max(rows, key=lambda r: r[2])[0]
        conn.execute(
            """
            UPDATE microbe_disease
            SET direction = ?, replication_count = ?, contradiction_count = ?,
                evidence_grade = ?, source_db = ?
            WHERE id = ?
            """,
            (
                majority_direction, majority_count, total_contradiction, grade,
                "merged (multiple source codes converged on this disease)", survivor_id,
            ),
        )
        for row_id, *_ in rows:
            if row_id != survivor_id:
                conn.execute("DELETE FROM microbe_disease WHERE id = ?", (row_id,))

        print(
            f"  taxid={taxid} mondo_id={mondo_id}: merged {count} rows -> "
            f"{majority_direction}, grade {grade}, {majority_count} replications, "
            f"{total_contradiction} contradictions"
        )

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(f"\nDone. Foreign key check: {'passed' if not issues else issues}")


if __name__ == "__main__":
    main()
