"""
17_merge_case_insensitive_disease_duplicates.py

Fixes a real gap in scripts 06/07: the MONDO crosswalk matching was
never case-insensitive, so disease names that are identical except
for capitalization (e.g. "Chronic kidney disease" from Disbiome's
raw MEDDRA placeholder vs "chronic kidney disease" from the real
MONDO mapping) were never recognized as the same disease. Found via
direct inspection: 101 case-insensitive duplicate pairs, 202 rows
total -- a much bigger gap than the single Crohn's disease case
found earlier this project.

For each pair where exactly one side has a real MONDO id and the
other is a MEDDRA: placeholder, this re-points all microbe_disease
rows from the placeholder to the real MONDO disease, then removes
the placeholder. After re-pointing, the same duplicate-evidence-pair
risk applies as before (an organism might already have evidence
under the real MONDO id AND under the placeholder) -- so this should
be followed by re-running 13_merge_duplicate_evidence_pairs.py.

Pairs where BOTH sides already have a real MONDO id (e.g. "Pulmonary
arterial hypertension" appearing under two different MONDO IDs) are
NOT touched -- that's the same category of genuine ontology
ambiguity found earlier (AA amyloidosis, Budd-Chiari syndrome,
Kostmann syndrome), where a shared label isn't sufficient evidence
of true equivalence without a confirming identifier.

Usage:
    python scripts/17_merge_case_insensitive_disease_duplicates.py
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
        meddra_ids = [i for i in ids if not i.startswith("MONDO:")]

        if len(mondo_ids) != 1 or len(meddra_ids) == 0:
            print(f"  [skip -- ontology ambiguity, not touched] '{norm}': {ids}")
            skipped_ambiguous += 1
            continue

        real_id = mondo_ids[0]
        for placeholder_id in meddra_ids:
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
        f"{skipped_ambiguous} groups left alone (genuine ontology ambiguity, not a crosswalk miss)."
    )
    print("Foreign key check:", "passed" if not issues else issues)
    print(
        "\nIMPORTANT: re-run 13_merge_duplicate_evidence_pairs.py next -- this merge can "
        "recreate duplicate (organism, disease) evidence rows, the same way the mondo_seed "
        "merge did earlier this project."
    )


if __name__ == "__main__":
    main()
