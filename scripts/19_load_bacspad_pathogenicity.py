"""
19_load_bacspad_pathogenicity.py

Populates body_site and microbe_bodysite using real data from BacSPaD
(Zenodo 13235447) -- 6,039 bacterial genomes from BV-BRC with a
pathogenicity_label (HP/NHP) and isolation_source_category on most
rows.

Two fields from the BacSPaD documentation looked like an exact match
for "commensal vs pathogen at this site" -- biotic_relationship and
body_sample_site -- but real inspection showed they're populated on
well under 2% of rows, and biotic_relationship is partially polluted
with values that don't match its own documented vocabulary (e.g.
"Gastrointestinal" appearing as a biotic_relationship value, clearly
misplaced body-site data). Neither is reliable enough to build on.

What IS reliable: pathogenicity_label (99.2% populated) and
isolation_source_category (99.0% populated, 13 sensible anatomical
buckets). This script uses those two as the real signal.

Important interpretive caveat, reflected directly in the column
names below rather than glossed over: BacSPaD is sourced from BV-BRC,
a CLINICAL genomics database. Even at sites that are naturally
colonized by harmless commensals (skin, gut, oral cavity), genomes in
this dataset were disproportionately sequenced BECAUSE they came from
documented infections. So "pathogenic_isolate_count" here means
"isolates from this site that were clinically classified as
pathogenic in this dataset" -- a real and useful signal, but it is
NOT the same claim as "this fraction of the organism's natural
population at this site is pathogenic." The column names say exactly
that, deliberately, rather than reusing the existing
flora_classification field (which implies general healthy-population
ecology -- a different, unsupported claim here).

No Uberon IDs are invented for the body-site categories -- "Gastro-
intestinal", "Respiratory Tract", etc. are stored as BacSPaD's own
category text with uberon_id left NULL, the same honest-gap pattern
already used for diseases with no MONDO mapping. The catch-all
"Other" category is excluded entirely, since it carries no real
site information.

Usage:
    pip install pandas
    Place Genomes_labeled.csv at data/raw/bacspad/Genomes_labeled.csv
    python scripts/19_load_bacspad_pathogenicity.py
"""

import sqlite3
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
CSV_PATH = PROJECT_ROOT / "data" / "raw" / "bacspad" / "Genomes_labeled.csv"


def ensure_columns(conn: sqlite3.Connection) -> None:
    bs_cols = {row[1] for row in conn.execute("PRAGMA table_info(body_site)")}
    if "source_db" not in bs_cols:
        conn.execute("ALTER TABLE body_site ADD COLUMN source_db TEXT")

    mb_cols = {row[1] for row in conn.execute("PRAGMA table_info(microbe_bodysite)")}
    for col, coltype in [
        ("clinical_isolate_count", "INTEGER"),
        ("pathogenic_isolate_count", "INTEGER"),
        ("pathogenic_fraction", "REAL"),
        ("source_db", "TEXT"),
    ]:
        if col not in mb_cols:
            conn.execute(f"ALTER TABLE microbe_bodysite ADD COLUMN {col} {coltype}")
    conn.commit()


def find_taxid(pairs: list, species_name: str):
    target = species_name.lower()
    for stored, taxid in pairs:
        if stored == target:
            return taxid
    for stored, taxid in pairs:
        if stored.startswith(target + " "):
            return taxid
    return None


def build_organism_lookup(conn) -> list:
    pairs = []
    for taxid, name in conn.execute("SELECT ncbi_taxid, name FROM organism"):
        pairs.append((name.lower(), taxid))
    for taxid, syn in conn.execute("SELECT ncbi_taxid, synonym_name FROM organism_synonym"):
        pairs.append((syn.lower(), taxid))
    return pairs


def main():
    if not CSV_PATH.exists():
        raise SystemExit(f"Expected {CSV_PATH} -- copy Genomes_labeled.csv there first.")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    ensure_columns(conn)

    df = pd.read_csv(CSV_PATH, low_memory=False)
    df = df[df["isolation_source_category"].notna() & (df["isolation_source_category"] != "")]
    df = df[df["isolation_source_category"] != "Other"]
    df = df[df["species"].notna()]

    organism_lookup = build_organism_lookup(conn)

    categories = sorted(df["isolation_source_category"].unique())
    for category in categories:
        conn.execute(
            "INSERT OR IGNORE INTO body_site (uberon_id, name, source_db) VALUES (?, ?, ?)",
            (f"BACSPAD:{category.replace(' ', '_').upper()}", category, "BacSPaD category (no formal Uberon mapping)"),
        )
    conn.commit()

    grouped = df.groupby(["species", "isolation_source_category"]).agg(
        total=("pathogenicity_label", "count"),
        pathogenic=("pathogenicity_label", lambda s: (s == "HP").sum()),
    ).reset_index()

    matched, unmatched_species = 0, set()
    for _, row in grouped.iterrows():
        taxid = find_taxid(organism_lookup, row["species"])
        if not taxid:
            unmatched_species.add(row["species"])
            continue

        site_id = f"BACSPAD:{row['isolation_source_category'].replace(' ', '_').upper()}"
        fraction = row["pathogenic"] / row["total"]
        conn.execute(
            """
            INSERT INTO microbe_bodysite
                (ncbi_taxid, uberon_id, clinical_isolate_count, pathogenic_isolate_count,
                 pathogenic_fraction, source_db)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (taxid, site_id, int(row["total"]), int(row["pathogenic"]), fraction,
             "BacSPaD (BV-BRC clinical isolates)"),
        )
        matched += 1

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(f"Loaded {len(categories)} body-site categories: {categories}")
    print(f"\nMatched {matched} (organism, site) pairs across {len(grouped) - len(unmatched_species)} species.")
    print(f"{len(unmatched_species)} species in BacSPaD had no match in our organism table.")
    if unmatched_species:
        print("  Sample unmatched:", sorted(unmatched_species)[:15])
    if issues:
        print(f"WARNING: {len(issues)} foreign key issues:")
        for i in issues[:10]:
            print(f"  {i}")
    else:
        print("Foreign key integrity check passed.")


if __name__ == "__main__":
    main()
