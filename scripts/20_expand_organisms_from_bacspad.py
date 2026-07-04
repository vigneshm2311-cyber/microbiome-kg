"""
20_expand_organisms_from_bacspad.py

Expands the organism table using the species from BacSPaD that had
no match in our table -- same data-driven-priority approach as
scripts 05 and 15, sourced from BacSPaD this time.

Priority signal: total genome count per species in the BacSPaD file
(same logic as prioritizing by Disbiome experiment count / UHGG
genome count in earlier scripts).

Species matching NCBI's own "Genus sp. <placeholder>" naming
convention (not-yet-formally-classified isolates, e.g. "Aggregatibacter
sp. oral taxon 513") are skipped -- not a real binomial name, nothing
to resolve, same category as GTDB's "sp + digits" placeholders found
during the UHGG work.

No extra fetch needed for body-site/pathogenicity data -- it's
already in the same CSV used by script 19, so each newly-added
organism gets its body-site rows attached in the same pass.

Usage:
    pip install requests pandas
    python scripts/20_expand_organisms_from_bacspad.py [top_n]
    (top_n defaults to 150)
"""

import re
import sys
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
CSV_PATH = PROJECT_ROOT / "data" / "raw" / "bacspad" / "Genomes_labeled.csv"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_DELAY = 0.4

SP_PLACEHOLDER_RE = re.compile(r"\bsp\.\s")


def search_taxid(name: str):
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
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 150

    if not CSV_PATH.exists():
        raise SystemExit(f"Expected {CSV_PATH} -- run script 19 setup first.")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    df = pd.read_csv(CSV_PATH, low_memory=False)
    df = df[df["species"].notna()]
    df = df[~df["species"].str.contains(SP_PLACEHOLDER_RE)]

    organism_lookup = build_organism_lookup(conn)
    known_species = {s for s, _ in organism_lookup}

    genome_counts = df.groupby("species").size().sort_values(ascending=False)
    unmatched = [s for s in genome_counts.index if s.lower() not in known_species]
    ranked = unmatched[:top_n]

    print(f"{len(unmatched)} unmatched real species found, resolving top {len(ranked)} by genome count via NCBI:\n")

    loaded, skipped, failed = 0, 0, 0
    bodysite_rows_added = 0
    for species in ranked:
        try:
            taxid = search_taxid(species)
            time.sleep(REQUEST_DELAY)
            if not taxid:
                print(f"  [skip] no taxid found for '{species}'")
                skipped += 1
                continue

            record = fetch_taxon_record(taxid)
            time.sleep(REQUEST_DELAY)
            load_organism(conn, record)

            site_rows = df[(df["species"] == species)
                           & df["isolation_source_category"].notna()
                           & (df["isolation_source_category"] != "")
                           & (df["isolation_source_category"] != "Other")]
            n_sites = 0
            for category, group in site_rows.groupby("isolation_source_category"):
                total = len(group)
                pathogenic = (group["pathogenicity_label"] == "HP").sum()
                site_id = f"BACSPAD:{category.replace(' ', '_').upper()}"
                conn.execute(
                    """
                    INSERT INTO microbe_bodysite
                        (ncbi_taxid, uberon_id, clinical_isolate_count, pathogenic_isolate_count,
                         pathogenic_fraction, source_db)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (record["taxid"], site_id, int(total), int(pathogenic), pathogenic / total,
                     "BacSPaD (BV-BRC clinical isolates)"),
                )
                n_sites += 1
            bodysite_rows_added += n_sites
            conn.commit()
        except Exception as exc:
            print(f"  [fail] '{species}' -> {type(exc).__name__}: {exc}")
            failed += 1
            continue

        print(f"  [ok] {species} -> {record['name']}, taxid {record['taxid']} ({record['rank']}), {n_sites} body-site row(s) attached")
        loaded += 1

    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(f"\nDone. Loaded {loaded}, skipped {skipped}, failed {failed} (of {len(ranked)} attempted).")
    print(f"{bodysite_rows_added} new microbe_bodysite rows attached in the same pass.")
    if issues:
        print(f"WARNING: {len(issues)} foreign key issues:")
        for i in issues[:10]:
            print(f"  {i}")
    else:
        print("Foreign key integrity check passed.")


if __name__ == "__main__":
    main()
