"""
35_fetch_organism_descriptions.py

Fetches real taxonomic lineage strings from NCBI Taxonomy for every
organism and stores them in a new `lineage` column. Used by the
viewer's Overview tab to show a structured, evidence-based description
rather than invented free-text.

Safe to re-run: only updates rows where lineage IS NULL.

Usage:
    python scripts/35_fetch_organism_descriptions.py
"""

import sqlite3
import time
import xml.etree.ElementTree as ET
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_DELAY = 0.35
BATCH_SIZE = 50


def ensure_columns(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(organism)")}
    for col in ("lineage", "division"):
        if col not in cols:
            conn.execute(f"ALTER TABLE organism ADD COLUMN {col} TEXT")
    conn.commit()


def fetch_lineages(taxids: list) -> dict:
    url = f"{EUTILS_BASE}/efetch.fcgi?db=taxonomy&id={','.join(taxids)}&rettype=xml"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        root = ET.fromstring(resp.read())
    results = {}
    for taxon in root.findall("Taxon"):
        taxid = taxon.findtext("TaxId")
        if not taxid:
            continue
        lineage = taxon.findtext("Lineage") or ""
        if lineage.startswith("cellular organisms; "):
            lineage = lineage[len("cellular organisms; "):]
        results[taxid] = {
            "lineage": lineage,
            "division": taxon.findtext("Division"),
            "parent_taxid": taxon.findtext("ParentTaxId"),
        }
    return results


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    ensure_columns(conn)

    rows = conn.execute(
        "SELECT ncbi_taxid FROM organism WHERE lineage IS NULL OR lineage = ''"
    ).fetchall()
    taxids = [row[0] for row in rows]
    print(f"{len(taxids)} organisms need lineage data\n")

    fetched, errors = 0, 0
    total_batches = (len(taxids) - 1) // BATCH_SIZE + 1 if taxids else 0

    for i in range(0, len(taxids), BATCH_SIZE):
        batch = taxids[i:i + BATCH_SIZE]
        try:
            results = fetch_lineages(batch)
        except Exception as exc:
            print(f"  [batch {i // BATCH_SIZE + 1} failed] {type(exc).__name__}: {exc}")
            errors += len(batch)
            time.sleep(REQUEST_DELAY)
            continue

        for taxid in batch:
            data = results.get(taxid)
            if not data:
                continue
            conn.execute(
                "UPDATE organism SET lineage=?, division=?, parent_taxid=COALESCE(parent_taxid,?) WHERE ncbi_taxid=?",
                (data["lineage"], data["division"], data["parent_taxid"], taxid),
            )
            fetched += 1

        conn.commit()
        print(f"  batch {i // BATCH_SIZE + 1}/{total_batches} done")
        time.sleep(REQUEST_DELAY)

    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()
    print(f"\nDone. {fetched} lineages fetched, {errors} errors.")


if __name__ == "__main__":
    main()
