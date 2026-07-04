"""
07_fuzzy_match_diseases_to_mondo.py

Backup pass for diseases 06_map_diseases_to_mondo.py couldn't resolve
by exact MedDRA-code lookup. The Crohn's disease case we found by
hand is the model: Disbiome's MedDRA code for "Crohn's Disease" isn't
one of the ~1,460 codes MONDO has curated a crosswalk for -- but
MONDO almost certainly has an entry literally labeled "Crohn disease"
sitting right there in the same mapping files, just associated with
a *different* MedDRA code than the one Disbiome happens to use.

This pass builds a name index from the same SSSOM files
06_map_diseases_to_mondo.py already used (no new download needed),
and matches remaining MEDDRA:<id> placeholder disease names against
MONDO disease labels after *light* normalization only:
  - lowercase
  - strip possessive 's  ("Crohn's disease" -> "crohn disease")
  - strip remaining punctuation
  - collapse whitespace

Deliberately NOT using true fuzzy/edit-distance matching. Disease
names are a place where aggressive fuzzy matching is actively
dangerous -- "type 1 diabetes" and "type 2 diabetes" are one
character apart and are very much not the same disease. Light
normalization only catches formatting differences, not semantic
near-misses, which is the right tradeoff when identity matters.

Every match is printed for visual spot-checking before you trust it,
the same way the Tannerella/Forsythia mismatch was caught by reading
the output, not by blind automation.

This also proactively relaxes the mondo_mapping_confidence CHECK
constraint (it doesn't allow a "name_match" value yet) -- same root
cause as the rank-column crashes earlier, fixed before it bites this
time rather than after.

Usage:
    pip install requests
    python scripts/07_fuzzy_match_diseases_to_mondo.py
"""

import csv
import re
import sqlite3
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"

MAPPINGS_BASE = "https://raw.githubusercontent.com/monarch-initiative/mondo/master/src/ontology/mappings"
MAPPING_FILES = [
    "mondo_exactmatch_meddra.sssom.tsv",
    "mondo_closematch_meddra.sssom.tsv",
    "mondo_hasdbxref_meddra.sssom.tsv",
]


def normalize(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"'s\b", "", name)        # possessive: "crohn's" -> "crohn"
    name = re.sub(r"[^a-z0-9 ]", " ", name)  # remaining punctuation -> space
    name = re.sub(r"\s+", " ", name).strip()
    return name


def fetch_sssom_rows(filename: str) -> list:
    resp = requests.get(f"{MAPPINGS_BASE}/{filename}", timeout=60)
    resp.raise_for_status()
    lines = [line for line in resp.text.splitlines() if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def build_name_index() -> dict:
    """normalized MONDO label -> mondo_id, reusing the same mapping
    files 06_map_diseases_to_mondo.py already pulled."""
    index = {}
    for filename in MAPPING_FILES:
        for row in fetch_sssom_rows(filename):
            label, mondo_id = row.get("subject_label"), row.get("subject_id")
            if label and mondo_id:
                index.setdefault(normalize(label), mondo_id)
    return index


def relax_confidence_constraint(conn: sqlite3.Connection) -> None:
    """Same root cause as the rank-column crashes: a CHECK constraint
    on a small enum that turned out not to be exhaustive. Fixing it
    proactively this time via SQLite's standard recreate-table dance,
    since CHECK constraints can't be altered in place."""
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='disease'"
    ).fetchone()[0]
    if "CHECK" not in sql:
        return  # already relaxed -- safe to re-run this script

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """
        CREATE TABLE disease_new (
            mondo_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT,
            meddra_id TEXT,
            mondo_mapping_confidence TEXT
        )
        """
    )
    conn.execute("INSERT INTO disease_new SELECT * FROM disease")
    conn.execute("DROP TABLE disease")
    conn.execute("ALTER TABLE disease_new RENAME TO disease")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def main():
    print("Building MONDO name index from existing mapping files ...")
    name_index = build_name_index()
    print(f"  {len(name_index)} distinct normalized MONDO labels available to match against\n")

    conn = sqlite3.connect(DB_PATH)
    relax_confidence_constraint(conn)
    conn.execute("PRAGMA foreign_keys = OFF")

    placeholders = conn.execute(
        "SELECT mondo_id, name FROM disease WHERE mondo_id LIKE 'MEDDRA:%'"
    ).fetchall()

    matched, merged, unmatched = 0, 0, 0

    for placeholder_id, disease_name in placeholders:
        real_mondo_id = name_index.get(normalize(disease_name))
        if not real_mondo_id:
            unmatched += 1
            continue

        existing = conn.execute(
            "SELECT name FROM disease WHERE mondo_id = ?", (real_mondo_id,)
        ).fetchone()

        if existing:
            print(f"  [merge] '{disease_name}' ({placeholder_id}) -> {real_mondo_id} ('{existing[0]}')")
            conn.execute(
                "UPDATE microbe_disease SET mondo_id = ? WHERE mondo_id = ?",
                (real_mondo_id, placeholder_id),
            )
            conn.execute("DELETE FROM disease WHERE mondo_id = ?", (placeholder_id,))
            merged += 1
        else:
            print(f"  [match] '{disease_name}' ({placeholder_id}) -> {real_mondo_id}")
            meddra_code = placeholder_id.split(":", 1)[1]
            conn.execute(
                """
                UPDATE disease
                SET mondo_id = ?, meddra_id = ?, mondo_mapping_confidence = ?, category = NULL
                WHERE mondo_id = ?
                """,
                (real_mondo_id, meddra_code, "name_match", placeholder_id),
            )
            conn.execute(
                "UPDATE microbe_disease SET mondo_id = ? WHERE mondo_id = ?",
                (real_mondo_id, placeholder_id),
            )
            matched += 1

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(
        f"\nDone. {matched} newly matched by name, {merged} merged into an "
        f"already-mapped disease, {unmatched} still unmapped after both passes."
    )
    if issues:
        print(f"WARNING: {len(issues)} foreign key issues detected:")
        for issue in issues[:10]:
            print(f"  {issue}")
    else:
        print("Foreign key integrity check passed.")


if __name__ == "__main__":
    main()
