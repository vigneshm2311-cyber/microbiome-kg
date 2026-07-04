"""
05_expand_organisms_from_disbiome.py

Instead of guessing which organisms to add next, this derives the
priority list directly from data we already have: it re-fetches
Disbiome's experiments, counts how often each *unmatched* organism
name occurs, and resolves the top N most frequent ones via NCBI --
the same lookup logic as 03_load_taxonomy.py.

The logic: an organism named in 50 Disbiome experiments is worth
adding before one named in 1 -- resolving it immediately unlocks
however many disease associations were waiting on it. That's a
data-driven priority order, not a hand-picked list.

After this runs, re-run 04_load_disease_associations.py to actually
load the disease associations now unlocked for these newly-added
organisms -- this script only touches the organism table.

Note: several of these names will be genus-level or informal cluster
names (e.g. "Clostridia cluster I") rather than clean species names.
Some won't resolve via NCBI at all and will be skipped -- that's
expected, not a bug, since Disbiome's curation includes names at
every taxonomic resolution.

Usage:
    pip install requests
    python scripts/05_expand_organisms_from_disbiome.py [top_n]
    (top_n defaults to 100)
"""

import sys
import sqlite3
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"

DISBIOME_EXPERIMENT_URL = "https://disbiome.ugent.be:8080/experiment"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_DELAY = 0.4

# Known Disbiome data-quality issues: a search term gets corrected to
# the real organism name before hitting NCBI. The first entry here is
# a confirmed case -- "Tanerella forsythia" is a one-letter typo for
# "Tannerella forsythia" (a real periodontal pathogen); searched
# unrestricted, it instead matched the plant genus Forsythia.
KNOWN_NAME_CORRECTIONS = {
    "Tanerella forsythia": "Tannerella forsythia",
}


# --- NCBI lookup helpers (same logic as 03_load_taxonomy.py) --------------

def search_taxid(name: str) -> str | None:
    resp = requests.get(
        f"{EUTILS_BASE}/esearch.fcgi",
        params={"db": "taxonomy", "term": name, "retmode": "json"},
        timeout=15,
    )
    resp.raise_for_status()
    id_list = resp.json().get("esearchresult", {}).get("idlist", [])
    return id_list[0] if id_list else None


def fetch_taxon_record(taxid: str) -> dict:
    resp = requests.get(
        f"{EUTILS_BASE}/efetch.fcgi",
        params={"db": "taxonomy", "id": taxid, "rettype": "xml"},
        timeout=15,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    taxon = root.find("Taxon")
    if taxon is None:
        raise ValueError(f"no <Taxon> element in efetch response for taxid {taxid}")

    record = {
        "taxid": taxon.findtext("TaxId"),
        "name": taxon.findtext("ScientificName"),
        "rank": (taxon.findtext("Rank") or "no rank").lower(),
        "parent_taxid": taxon.findtext("ParentTaxId"),
        "synonyms": [],
    }
    other_names = taxon.find("OtherNames")
    if other_names is not None:
        for tag in ("GenbankSynonym", "Synonym", "EquivalentName"):
            for el in other_names.findall(tag):
                if el.text:
                    record["synonyms"].append(el.text)
    return record


def load_organism(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO organism
            (ncbi_taxid, name, rank, parent_taxid, source_db, last_verified)
        VALUES (?, ?, ?, ?, ?, date('now'))
        """,
        (record["taxid"], record["name"], record["rank"], record["parent_taxid"], "NCBI Taxonomy"),
    )
    conn.execute("DELETE FROM organism_synonym WHERE ncbi_taxid = ?", (record["taxid"],))
    for syn in record["synonyms"]:
        conn.execute(
            "INSERT INTO organism_synonym (ncbi_taxid, synonym_name) VALUES (?, ?)",
            (record["taxid"], syn),
        )


# --- Disbiome-driven prioritization ----------------------------------------

def build_organism_lookup(conn) -> set:
    known = set()
    for (name,) in conn.execute("SELECT name FROM organism"):
        known.add(name.lower())
    for (syn,) in conn.execute("SELECT synonym_name FROM organism_synonym"):
        known.add(syn.lower())
    return known


def get_top_unmatched_organisms(known_names: set, top_n: int) -> list:
    print("Fetching experiments from Disbiome to compute priority order ...")
    resp = requests.get(DISBIOME_EXPERIMENT_URL, timeout=60)
    resp.raise_for_status()
    experiments = resp.json()

    counts = Counter()
    for exp in experiments:
        name = (exp.get("organism_name") or "").strip()
        if name and name.lower() not in known_names:
            counts[name] += 1

    return counts.most_common(top_n)


def main():
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 100

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    known_names = build_organism_lookup(conn)
    ranked = get_top_unmatched_organisms(known_names, top_n)

    print(f"\nTop {len(ranked)} unmatched organisms by experiment count -- resolving via NCBI:\n")

    loaded, skipped, failed = 0, 0, 0
    for name, exp_count in ranked:
        search_name = KNOWN_NAME_CORRECTIONS.get(name, name)
        try:
            taxid = search_taxid(search_name)
            time.sleep(REQUEST_DELAY)
            if not taxid:
                print(f"  [skip] no taxid found for '{name}' ({exp_count} experiments waiting on it)")
                skipped += 1
                continue

            record = fetch_taxon_record(taxid)
            time.sleep(REQUEST_DELAY)
            load_organism(conn, record)
            # Safety net: if the name we searched differs from what NCBI
            # resolved to (a typo correction, or any other mismatch),
            # keep the original Disbiome string as a synonym too -- so
            # 04_load_disease_associations.py still matches Disbiome's
            # own (possibly imperfect) spelling to the right organism.
            if name.lower() != record["name"].lower():
                conn.execute(
                    "INSERT OR IGNORE INTO organism_synonym (ncbi_taxid, synonym_name) VALUES (?, ?)",
                    (record["taxid"], name),
                )
            conn.commit()
        except Exception as exc:
            print(f"  [fail] '{name}' -> {type(exc).__name__}: {exc}")
            failed += 1
            continue

        print(
            f"  [ok] {name} -> {record['name']}, taxid {record['taxid']} ({record['rank']}) "
            f"-- unlocks {exp_count} experiment row(s)"
        )
        loaded += 1

    conn.close()
    print(f"\nDone. Loaded {loaded}, skipped {skipped}, failed {failed} (of {len(ranked)} attempted).")
    print("Now re-run scripts/04_load_disease_associations.py to load the disease")
    print("associations these newly-added organisms just unlocked.")


if __name__ == "__main__":
    main()
