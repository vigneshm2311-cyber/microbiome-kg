"""
03_load_taxonomy.py

Looks up real NCBI Taxonomy records for a starter list of well-known
microbiome-relevant organisms via NCBI's E-utilities API, and loads
them into the `organism` table (plus `organism_synonym` for any
historical names NCBI tracks under the same taxid).

This calls NCBI live over the network -- it will NOT run inside
Claude's sandboxed bash tool (ncbi.nlm.nih.gov isn't reachable from
there), but will work fine wherever you run it with normal internet
access.

NCBI's usage policy without an API key: max ~3 requests/second. This
script sleeps between calls to stay comfortably under that.

Usage:
    pip install requests
    python scripts/03_load_taxonomy.py
"""

import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_DELAY = 0.4  # seconds between calls -- stays under NCBI's rate limit

# Starter list -- edit freely. These are organism *names*, not taxids.
# The script resolves real taxids live, so nothing here is pre-guessed
# or hardcoded from memory.
SEED_ORGANISMS = [
    "Faecalibacterium prausnitzii",
    "Akkermansia muciniphila",
    "Bacteroides fragilis",
    "Bacteroides thetaiotaomicron",
    "Escherichia coli",
    "Limosilactobacillus reuteri",
    "Bifidobacterium longum",
    "Prevotella copri",
    "Roseburia intestinalis",
    "Eubacterium rectale",
    "Helicobacter pylori",
    "Candida albicans",
    "Methanobrevibacter smithii",
    "Staphylococcus aureus",
    "Clostridioides difficile",
]


def search_taxid(name: str) -> str | None:
    """esearch: organism scientific name -> NCBI taxid."""
    resp = requests.get(
        f"{EUTILS_BASE}/esearch.fcgi",
        params={"db": "taxonomy", "term": name, "retmode": "json"},
        timeout=15,
    )
    resp.raise_for_status()
    id_list = resp.json().get("esearchresult", {}).get("idlist", [])
    return id_list[0] if id_list else None


def fetch_taxon_record(taxid: str) -> dict:
    """efetch: taxid -> full taxonomy record (rank, parent, synonyms)."""
    resp = requests.get(
        f"{EUTILS_BASE}/efetch.fcgi",
        params={"db": "taxonomy", "id": taxid, "rettype": "xml"},
        timeout=15,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    taxon = root.find("Taxon")

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
    # Idempotent re-run: clear and re-insert synonyms rather than
    # appending duplicates every time this script runs.
    conn.execute("DELETE FROM organism_synonym WHERE ncbi_taxid = ?", (record["taxid"],))
    for syn in record["synonyms"]:
        conn.execute(
            "INSERT INTO organism_synonym (ncbi_taxid, synonym_name) VALUES (?, ?)",
            (record["taxid"], syn),
        )


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    loaded, skipped = 0, 0
    for name in SEED_ORGANISMS:
        taxid = search_taxid(name)
        time.sleep(REQUEST_DELAY)
        if not taxid:
            print(f"  [skip] no taxid found for '{name}'")
            skipped += 1
            continue

        record = fetch_taxon_record(taxid)
        time.sleep(REQUEST_DELAY)
        load_organism(conn, record)
        conn.commit()
        print(
            f"  [ok] {record['name']} -> taxid {record['taxid']} ({record['rank']}), "
            f"{len(record['synonyms'])} synonym(s)"
        )
        loaded += 1

    conn.close()
    print(f"\nDone. Loaded {loaded}, skipped {skipped} (of {len(SEED_ORGANISMS)} requested).")


if __name__ == "__main__":
    main()
