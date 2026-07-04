"""
15_expand_organisms_from_uhgg.py

Expands the organism table using UHGG species that have a real
binomial name but aren't in our table yet -- the same data-driven-
priority approach as 05_expand_organisms_from_disbiome.py, sourced
from UHGG instead of Disbiome.

Priority signal: total genome count per species across the full
289K-row UHGG file -- a species with many independently sequenced
genomes is a reasonable proxy for "well studied".

Genus-suffix fix: GTDB sometimes marks a within-genus split with a
letter suffix on the GENUS token (e.g. "Eubacterium_I"). Searching
the bare species epithet after dropping that token caused a false
match earlier ("ramulus" alone matched a stick-insect genus instead
of the real bacterium). This strips ONLY genus-token suffixes before
searching -- never species-token suffixes (e.g. "prausnitzii_G"),
which represent a genuinely distinct GTDB-recognized lineage, not a
formatting artifact.

Also caches the UHGG download locally now (data/raw/uhgg_metadata.tsv)
instead of re-downloading the same 289K rows on every run.

Usage:
    pip install requests
    python scripts/15_expand_organisms_from_uhgg.py [top_n]
    (top_n defaults to 200)
"""

import re
import sys
import sqlite3
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"

UHGG_METADATA_URL = (
    "https://ftp.ebi.ac.uk/pub/databases/metagenomics/mgnify_genomes/"
    "human-gut/v2.0.2/genomes-all_metadata.tsv"
)
UHGG_CACHE_PATH = PROJECT_ROOT / "data" / "raw" / "uhgg_metadata.tsv"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_DELAY = 0.4


def clean_search_term(name: str) -> str:
    parts = name.split(" ", 1)
    if len(parts) != 2:
        return name
    genus, rest = parts
    genus = re.sub(r"_[A-Z]+$", "", genus)
    return f"{genus} {rest}"


def get_uhgg_lines() -> list:
    if UHGG_CACHE_PATH.exists():
        print(f"Using cached UHGG metadata at {UHGG_CACHE_PATH}")
        return UHGG_CACHE_PATH.read_text().splitlines()

    print("Downloading UHGG metadata (will cache locally for next time) ...")
    resp = requests.get(UHGG_METADATA_URL, timeout=300)
    resp.raise_for_status()
    UHGG_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UHGG_CACHE_PATH.write_text(resp.text)
    return resp.text.splitlines()


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


def quality_tier(completeness: float, contamination: float) -> str:
    if completeness >= 90 and contamination <= 5:
        return "high"
    if completeness >= 50:
        return "medium"
    return "low"


def parse_species_name(lineage: str):
    match = re.search(r"s__([^;]+)", lineage)
    if not match:
        return None
    name = match.group(1).strip()
    if not name or re.search(r"\bsp\d{5,}\b", name):
        return None
    return name


def build_organism_lookup(conn) -> set:
    known = set()
    for (name,) in conn.execute("SELECT name FROM organism"):
        known.add(name.lower())
    for (syn,) in conn.execute("SELECT synonym_name FROM organism_synonym"):
        known.add(syn.lower())
    return known


def main():
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 200

    print("Loading UHGG metadata ...")
    lines = get_uhgg_lines()
    header = lines[0].split("\t")
    idx = {col: i for i, col in enumerate(header)}
    print(f"  {len(lines) - 1} total genome rows received")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    known_names = build_organism_lookup(conn)

    genome_counts = defaultdict(int)
    best_rep_row = {}

    for line in lines[1:]:
        fields = line.split("\t")
        name = parse_species_name(fields[idx["Lineage"]])
        if not name or name.lower() in known_names:
            continue
        genome_counts[name] += 1
        if fields[idx["Genome"]] == fields[idx["Species_rep"]]:
            best_rep_row[name] = fields

    ranked = sorted(genome_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    print(f"\nTop {len(ranked)} unmatched UHGG species by total genome count -- resolving via NCBI:\n")

    loaded, skipped, failed = 0, 0, 0
    for name, genome_count in ranked:
        search_term = clean_search_term(name)
        try:
            taxid = search_taxid(search_term)
            time.sleep(REQUEST_DELAY)
            if not taxid:
                print(f"  [skip] no taxid found for '{name}' (searched as '{search_term}', {genome_count} genomes in UHGG)")
                skipped += 1
                continue

            record = fetch_taxon_record(taxid)
            time.sleep(REQUEST_DELAY)
            load_organism(conn, record)

            rep_row = best_rep_row.get(name)
            if rep_row:
                completeness = float(rep_row[idx["Completeness"]])
                contamination = float(rep_row[idx["Contamination"]])
                conn.execute(
                    """
                    INSERT OR IGNORE INTO genome
                        (genome_id, ncbi_taxid, source_db, accession, is_mag,
                         completeness_pct, contamination_pct, quality_tier)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rep_row[idx["Genome"]], record["taxid"], "UHGG v2.0.2",
                        rep_row[idx["Genome_accession"]],
                        rep_row[idx["Genome_type"]] != "Isolate",
                        completeness, contamination,
                        quality_tier(completeness, contamination),
                    ),
                )
                conn.execute(
                    "UPDATE organism SET representative_genome_id = ? WHERE ncbi_taxid = ?",
                    (rep_row[idx["Genome"]], record["taxid"]),
                )
            conn.commit()
        except Exception as exc:
            print(f"  [fail] '{name}' -> {type(exc).__name__}: {exc}")
            failed += 1
            continue

        print(
            f"  [ok] {name} -> {record['name']}, taxid {record['taxid']} ({record['rank']}) "
            f"-- {genome_count} genomes in UHGG"
            + (", representative genome attached" if name in best_rep_row else "")
        )
        loaded += 1

    conn.close()
    print(f"\nDone. Loaded {loaded}, skipped {skipped}, failed {failed} (of {len(ranked)} attempted).")


if __name__ == "__main__":
    main()
