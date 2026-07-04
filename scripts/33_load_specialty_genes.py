"""
33_load_specialty_genes.py

Loads specialty genes (virulence factors, AMR genes, drug targets,
transporters, human homologs, metal resistance) from BV-BRC's public
API into a new organism_specialty_gene table.

Uses BV-BRC's sp_gene endpoint with taxon_id as the join key -- the
same NCBI taxid already on every organism row, so no genome-accession
crosswalk is needed. Confirmed working:
  https://www.bv-brc.org/api/sp_gene/?eq(taxon_id,{taxid})

Coverage estimate from a 20-organism random sample: ~55% of organisms
have at least one specialty gene in BV-BRC. Pure gut commensals that
have never been sequenced in a pathogen context will return empty --
this is expected and honest, not a gap in the loader.

Properties loaded: Antibiotic Resistance | Virulence Factor | Drug Target
                   Transporter | Human Homolog | Metal Resistance
(Essential Gene is skipped -- modeling artifact, not a clinically-relevant feature)

Normalizes BV-BRC's own typo: "Virulance factor" -> "Virulence Factor"
Safe to re-run: clears existing BV-BRC rows before reinserting.

Usage:
    python scripts/33_load_specialty_genes.py           # full run
    python scripts/33_load_specialty_genes.py --limit 5 # test first 5
"""

import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
API_BASE = "https://www.bv-brc.org/api/sp_gene"
REQUEST_DELAY = 0.35
BATCH_SIZE = 1000
SOURCE_LABEL = "BV-BRC"

PROPERTY_NORMALIZE = {
    "virulance factor": "Virulence Factor",
    "virulence factor": "Virulence Factor",
    "antibiotic resistance": "Antibiotic Resistance",
    "drug target": "Drug Target",
    "transporter": "Transporter",
    "human homolog": "Human Homolog",
    "metal resistance": "Metal Resistance",
    "essential gene": "Essential Gene",
}

PROPERTIES_OF_INTEREST = {
    "Antibiotic Resistance", "Virulence Factor", "Drug Target",
    "Transporter", "Human Homolog", "Metal Resistance",
}


def ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS organism_specialty_gene (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ncbi_taxid       TEXT NOT NULL REFERENCES organism(ncbi_taxid),
            property         TEXT NOT NULL,
            property_source  TEXT,
            gene             TEXT,
            product          TEXT,
            pmid             TEXT,
            source_db        TEXT DEFAULT 'BV-BRC'
        )
        """
    )
    conn.commit()


def fetch_specialty_genes(taxid: str) -> list:
    url = (
        f"{API_BASE}/?eq(taxon_id,{taxid})"
        f"&select(property,property_source,gene,product,pmid)"
        f"&limit({BATCH_SIZE})"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def normalize_property(raw: str) -> str:
    return PROPERTY_NORMALIZE.get(raw.lower().strip(), raw.strip())


def main():
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    ensure_table(conn)

    conn.execute("DELETE FROM organism_specialty_gene WHERE source_db = ?", (SOURCE_LABEL,))
    conn.commit()

    taxids = [row[0] for row in conn.execute("SELECT ncbi_taxid FROM organism ORDER BY ncbi_taxid")]
    if limit:
        taxids = taxids[:limit]
        print(f"Test mode: first {limit} organisms only\n")
    else:
        print(f"Loading specialty genes for {len(taxids)} organisms from BV-BRC...\n")

    loaded, empty, errors = 0, 0, 0
    total_genes = 0

    for i, taxid in enumerate(taxids):
        try:
            rows = fetch_specialty_genes(taxid)
        except Exception as exc:
            errors += 1
            if errors <= 5:
                print(f"  [error] taxid {taxid}: {type(exc).__name__}: {exc}")
            time.sleep(REQUEST_DELAY)
            continue

        if not rows:
            empty += 1
            time.sleep(REQUEST_DELAY)
            continue

        inserted = 0
        for row in rows:
            prop = normalize_property(row.get("property", ""))
            if prop not in PROPERTIES_OF_INTEREST:
                continue

            pmids = row.get("pmid")
            if isinstance(pmids, list):
                pmids = ";".join(str(p) for p in pmids if p)
            elif pmids:
                pmids = str(pmids)

            conn.execute(
                """
                INSERT INTO organism_specialty_gene
                    (ncbi_taxid, property, property_source, gene, product, pmid, source_db)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (taxid, prop, row.get("property_source"), row.get("gene"),
                 row.get("product"), pmids, SOURCE_LABEL),
            )
            inserted += 1

        if inserted:
            loaded += 1
            total_genes += inserted
        else:
            empty += 1

        conn.commit()
        time.sleep(REQUEST_DELAY)

        if (i + 1) % 100 == 0:
            print(f"  ... {i + 1}/{len(taxids)} checked ({loaded} with data, {empty} empty, {errors} errors)")

    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(f"\nDone.")
    print(f"  {loaded} organisms with specialty gene data")
    print(f"  {total_genes} total rows inserted")
    print(f"  {empty} organisms with no BV-BRC data (expected for gut commensals)")
    print(f"  {errors} API errors")
    print("Foreign key check:", "passed" if not issues else issues)


if __name__ == "__main__":
    main()
