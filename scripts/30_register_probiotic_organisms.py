"""
30_register_probiotic_organisms.py

Fixes a real gap from script 28: the two probiotic strains used as
interventions (Lactobacillus rhamnosus GG, Lactobacillus kimchii)
were registered in the `intervention` table under their real NCBI
taxids (NCBITAXON:568703, NCBITAXON:103818) but never actually
registered as organisms.

Same proven NCBI Taxonomy lookup pattern as scripts 03/05/15/20.

Usage:
    pip install requests
    python scripts/30_register_probiotic_organisms.py
"""

import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_DELAY = 0.4

PROBIOTIC_TAXIDS = {
    "568703": "Lactobacillus rhamnosus GG",
    "103818": "Lactobacillus kimchii",
}


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
        raise ValueError(f"no <Taxon> element for taxid {taxid}")
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


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    registered, already_present, failed = 0, 0, 0
    for taxid, expected_name in PROBIOTIC_TAXIDS.items():
        existing = conn.execute("SELECT 1 FROM organism WHERE ncbi_taxid = ?", (taxid,)).fetchone()
        if existing:
            print(f"  [already present] taxid {taxid} ({expected_name})")
            already_present += 1
            continue

        try:
            record = fetch_taxon_record(taxid)
        except Exception as exc:
            print(f"  [fail] taxid {taxid} ({expected_name}) -> {type(exc).__name__}: {exc}")
            failed += 1
            continue
        time.sleep(REQUEST_DELAY)

        conn.execute(
            """
            INSERT INTO organism
                (ncbi_taxid, name, rank, parent_taxid, source_db, last_verified)
            VALUES (?, ?, ?, ?, ?, date('now'))
            """,
            (record["taxid"], record["name"], record["rank"], record["parent_taxid"], "NCBI Taxonomy"),
        )
        for syn in record["synonyms"]:
            conn.execute(
                "INSERT INTO organism_synonym (ncbi_taxid, synonym_name) VALUES (?, ?)",
                (record["taxid"], syn),
            )
        print(f"  [registered] taxid {record['taxid']} -> {record['name']} ({record['rank']})")
        registered += 1

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(
        f"\nDone. {registered} newly registered, {already_present} already present, "
        f"{failed} failed (of {len(PROBIOTIC_TAXIDS)} total)."
    )
    print("Foreign key check:", "passed" if not issues else issues)


if __name__ == "__main__":
    main()
