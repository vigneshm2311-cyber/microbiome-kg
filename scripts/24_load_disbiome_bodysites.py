"""
24_load_disbiome_bodysites.py

Populates body_site and microbe_bodysite using REAL Disbiome
experiment data -- the same /experiment endpoint already used since
script 04, but extracting fields never used before: organism_ncbi_id
(a direct, exact NCBI taxid -- no fuzzy name-matching needed, unlike
every other body-site source this project) and location_id, resolved
via Disbiome's own /location endpoint into a genuine 3-level
anatomical hierarchy (149 real locations, e.g. "Gastrointestinal >
Colon > Mucosa").

Unlike BacSPaD (a clinically-biased pathogen database), Disbiome
curates case-control studies comparing disease vs healthy cohorts --
genuinely different, complementary signal. What this measures is
honestly narrower than true population prevalence, though: most
experiment records don't include subject-level counts (subject_value/
control_value are often null), so we can't compute a real "% of
people who carry this organism" figure. What study_count actually
means: how many independent published experiments detected this
organism via sampling at this specific anatomical site. A real,
citable signal, just not the same claim as population prevalence.

agg_name (Disbiome's own full breadcrumb path) is used as the
body_site name rather than the bare leaf name, since many leaf names
repeat across branches (nine separate "Mucosa" entries exist across
nine different organ systems) and would otherwise collide.

No Uberon IDs are invented -- locations are stored under Disbiome's
own location_id (e.g. "DISBIOME:10"), the same honest-gap pattern
used for BacSPaD's categories and for diseases with no MONDO mapping.

Safe to re-run: existing Disbiome-sourced rows are cleared before
reinserting, so running this twice won't double-count anything.

Usage:
    pip install requests
    python scripts/24_load_disbiome_bodysites.py
"""

import sqlite3
from collections import defaultdict
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
SOURCE_LABEL = "Disbiome (case-control study sampling)"


def ensure_columns(conn):
    mb_cols = {row[1] for row in conn.execute("PRAGMA table_info(microbe_bodysite)")}
    if "study_count" not in mb_cols:
        conn.execute("ALTER TABLE microbe_bodysite ADD COLUMN study_count INTEGER")
    conn.commit()


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    ensure_columns(conn)

    conn.execute("DELETE FROM microbe_bodysite WHERE source_db = ?", (SOURCE_LABEL,))
    conn.commit()

    print("Fetching real location hierarchy from Disbiome ...")
    loc_resp = requests.get("https://disbiome.ugent.be:8080/location", timeout=30)
    loc_resp.raise_for_status()
    locations = loc_resp.json()
    print(f"  {len(locations)} locations received")

    for loc in locations:
        conn.execute(
            "INSERT OR IGNORE INTO body_site (uberon_id, name, source_db) VALUES (?, ?, ?)",
            (f"DISBIOME:{loc['location_id']}", loc["agg_name"], "Disbiome (case-control study locations)"),
        )
    conn.commit()

    print("Fetching experiment data ...")
    exp_resp = requests.get("https://disbiome.ugent.be:8080/experiment", timeout=60)
    exp_resp.raise_for_status()
    experiments = exp_resp.json()
    print(f"  {len(experiments)} experiment records received")

    known_taxids = {row[0] for row in conn.execute("SELECT ncbi_taxid FROM organism")}

    pair_counts = defaultdict(int)
    for exp in experiments:
        taxid = exp.get("organism_ncbi_id")
        location_id = exp.get("location_id")
        if taxid is None or location_id is None:
            continue
        taxid_str = str(taxid)
        if taxid_str not in known_taxids:
            continue
        pair_counts[(taxid_str, location_id)] += 1

    matched_organisms = set()
    for (taxid, location_id), count in pair_counts.items():
        site_id = f"DISBIOME:{location_id}"
        conn.execute(
            "INSERT INTO microbe_bodysite (ncbi_taxid, uberon_id, study_count, source_db) VALUES (?, ?, ?, ?)",
            (taxid, site_id, count, SOURCE_LABEL),
        )
        matched_organisms.add(taxid)

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(
        f"\nDone. {len(pair_counts)} (organism, location) pairs loaded, "
        f"covering {len(matched_organisms)} distinct organisms already in our table."
    )
    print(
        f"{len(known_taxids) - len(matched_organisms)} organisms in our table have no Disbiome body-site data "
        f"(never sampled at a specific location in Disbiome's curated experiments)."
    )
    if issues:
        print(f"WARNING: {len(issues)} foreign key issues:")
        for i in issues[:10]:
            print(f"  {i}")
    else:
        print("Foreign key integrity check passed.")


if __name__ == "__main__":
    main()
