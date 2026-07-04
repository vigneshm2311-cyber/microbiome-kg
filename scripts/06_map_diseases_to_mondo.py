"""
06_map_diseases_to_mondo.py

Closes a documented Tier-2 gap: our `disease` table currently stores
MEDDRA:<id> placeholders (from Disbiome) instead of real MONDO IDs,
because Disbiome itself doesn't expose a MONDO mapping.

MONDO publishes its MedDRA cross-references as separate SSSOM mapping
files, by confidence tier:
  - mondo_exactmatch_meddra.sssom.tsv   (curated 1:1 equivalence -- tiny, ~3 rows)
  - mondo_closematch_meddra.sssom.tsv   (close but not exact -- ~1480 rows)
  - mondo_hasdbxref_meddra.sssom.tsv    (general cross-reference -- ~1500 rows)

This script downloads those three files directly (small, no need to
clone the full ~100MB ontology), builds a MedDRA-code -> MONDO-id map
prioritized by confidence, then migrates every MEDDRA:<id> placeholder
disease that has a match:
  - Updates disease.mondo_id to the real MONDO id, recording the
    original meddra_id and match confidence rather than discarding them
  - Updates every microbe_disease row referencing the old placeholder
    so the foreign key stays consistent
  - If two different placeholders turn out to map to the *same* real
    MONDO disease, merges them (re-points the second placeholder's
    microbe_disease rows, then deletes the now-empty duplicate) rather
    than creating a duplicate disease row

Foreign keys are temporarily disabled during the migration (since
we're deliberately changing primary key values that are referenced
elsewhere) and a PRAGMA foreign_key_check is run before finishing to
confirm nothing was left inconsistent.

Known limitation: MedDRA is not one of the handful of sources MONDO
guarantees precise 1:1 equivalence for (that list is Orphanet, OMIM,
DOID, EFO) -- these mappings are good, real, and usable, but not
philosophically airtight the way those four are. Not every disease
will find a match; that's expected, not a bug.

Usage:
    pip install requests
    python scripts/06_map_diseases_to_mondo.py
"""

import csv
import sqlite3
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"

MAPPINGS_BASE = "https://raw.githubusercontent.com/monarch-initiative/mondo/master/src/ontology/mappings"

# Checked in this order -- first match wins, so higher confidence is
# never overwritten by a lower-confidence match found later.
MAPPING_FILES = [
    ("exact", "mondo_exactmatch_meddra.sssom.tsv"),
    ("close", "mondo_closematch_meddra.sssom.tsv"),
    ("xref", "mondo_hasdbxref_meddra.sssom.tsv"),
]


def fetch_sssom_rows(filename: str) -> list:
    resp = requests.get(f"{MAPPINGS_BASE}/{filename}", timeout=60)
    resp.raise_for_status()
    # SSSOM files start with several "# key: value" metadata lines
    # before the real TSV header -- strip those first.
    lines = [line for line in resp.text.splitlines() if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def build_meddra_to_mondo_map() -> dict:
    mapping = {}
    for confidence, filename in MAPPING_FILES:
        rows = fetch_sssom_rows(filename)
        added = 0
        for row in rows:
            object_id = row.get("object_id", "")
            if not object_id.startswith("MedDRA:"):
                continue
            meddra_code = object_id.split(":", 1)[1]
            if meddra_code not in mapping:
                mapping[meddra_code] = (row["subject_id"], confidence)
                added += 1
        print(f"  {filename}: {len(rows)} rows, {added} new ({confidence} confidence)")
    return mapping


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(disease)")}
    if "meddra_id" not in cols:
        conn.execute("ALTER TABLE disease ADD COLUMN meddra_id TEXT")
    if "mondo_mapping_confidence" not in cols:
        conn.execute("ALTER TABLE disease ADD COLUMN mondo_mapping_confidence TEXT")
    conn.commit()


def migrate(conn: sqlite3.Connection, mapping: dict):
    ensure_columns(conn)
    conn.execute("PRAGMA foreign_keys = OFF")

    placeholders = conn.execute(
        "SELECT mondo_id FROM disease WHERE mondo_id LIKE 'MEDDRA:%'"
    ).fetchall()

    mapped, merged, unmapped = 0, 0, 0

    for (placeholder_id,) in placeholders:
        meddra_code = placeholder_id.split(":", 1)[1]
        match = mapping.get(meddra_code)
        if not match:
            unmapped += 1
            continue

        real_mondo_id, confidence = match
        already_exists = conn.execute(
            "SELECT 1 FROM disease WHERE mondo_id = ?", (real_mondo_id,)
        ).fetchone()

        if already_exists:
            # Two different Disbiome MedDRA placeholders map to the
            # same real MONDO concept -- merge rather than duplicate.
            conn.execute(
                "UPDATE microbe_disease SET mondo_id = ? WHERE mondo_id = ?",
                (real_mondo_id, placeholder_id),
            )
            conn.execute("DELETE FROM disease WHERE mondo_id = ?", (placeholder_id,))
            merged += 1
        else:
            conn.execute(
                """
                UPDATE disease
                SET mondo_id = ?, meddra_id = ?, mondo_mapping_confidence = ?, category = NULL
                WHERE mondo_id = ?
                """,
                (real_mondo_id, meddra_code, confidence, placeholder_id),
            )
            conn.execute(
                "UPDATE microbe_disease SET mondo_id = ? WHERE mondo_id = ?",
                (real_mondo_id, placeholder_id),
            )
            mapped += 1

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    return mapped, merged, unmapped, issues


def main():
    print("Fetching MONDO-MedDRA mapping files ...")
    mapping = build_meddra_to_mondo_map()
    print(f"\nTotal distinct MedDRA codes with a MONDO mapping available: {len(mapping)}")

    conn = sqlite3.connect(DB_PATH)
    mapped, merged, unmapped, issues = migrate(conn, mapping)
    conn.close()

    print(
        f"\nDone. {mapped} diseases migrated to real MONDO IDs, "
        f"{merged} merged into an already-migrated disease (duplicate MONDO target), "
        f"{unmapped} remain as MEDDRA:<id> placeholders (no MONDO mapping found)."
    )
    if issues:
        print(f"\nWARNING: {len(issues)} foreign key issues detected after migration:")
        for issue in issues[:10]:
            print(f"  {issue}")
    else:
        print("Foreign key integrity check passed -- no orphaned references.")


if __name__ == "__main__":
    main()
