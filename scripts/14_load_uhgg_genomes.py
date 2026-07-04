"""
14_load_uhgg_genomes.py

Populates the empty `genome` table using UHGG v2.0.2's species-
representative genomes, matched against organisms already in our
table.

Real finding from the exploration pass: UHGG's metadata has NO NCBI
taxid column at all -- its Lineage field is GTDB-style taxonomy
(d__/p__/c__/o__/f__/g__/s__), not NCBI taxonomy. GTDB and NCBI
frequently disagree or use different names for the same organism
(the Lactobacillus -> Limosilactobacillus split earlier this session
was originally GTDB-driven, later adopted by NCBI). So this can't be
a simple taxid join like the MONDO/Disbiome integrations were.

Instead, this matches UHGG's species-rep genomes against our
existing organism table by name (exact match against organism.name
or organism_synonym.synonym_name) -- the same proven pattern used
for Disbiome organism names. Realistically this enriches organisms
we already have with real genome-quality metadata, rather than
expanding organism count much -- most UHGG species are uncultured
with placeholder names (e.g. "GCA-900066495 sp902362365") that have
nothing to match against.

Note: I could not functionally test this script myself -- EBI's FTP
isn't reachable from my sandbox and blocks my fetch tool via
robots.txt. Syntax-verified only; real testing happens on your run.

Usage:
    pip install requests
    python scripts/14_load_uhgg_genomes.py
"""

import re
import sqlite3
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"

UHGG_METADATA_URL = (
    "https://ftp.ebi.ac.uk/pub/databases/metagenomics/mgnify_genomes/"
    "human-gut/v2.0.2/genomes-all_metadata.tsv"
)


def parse_species_name(lineage: str):
    """Extract a clean binomial species name from a GTDB-style
    lineage string's s__ segment. Returns None for uncultured
    placeholder names (e.g. "GCA-900066495 sp902362365" -- GTDB's
    convention for unnamed species is an accession-style genus and a
    "sp" + digits species epithet), which won't match anything real
    anyway."""
    match = re.search(r"s__([^;]+)", lineage)
    if not match:
        return None
    name = match.group(1).strip()
    if not name:
        return None
    if re.search(r"\bsp\d{5,}\b", name):
        return None
    return name


def build_organism_lookup(conn) -> dict:
    lookup = {}
    for taxid, name in conn.execute("SELECT ncbi_taxid, name FROM organism"):
        lookup[name.lower()] = taxid
    for taxid, syn in conn.execute("SELECT ncbi_taxid, synonym_name FROM organism_synonym"):
        lookup.setdefault(syn.lower(), taxid)
    return lookup


def quality_tier(completeness: float, contamination: float) -> str:
    if completeness >= 90 and contamination <= 5:
        return "high"
    if completeness >= 50:
        return "medium"
    return "low"


def main():
    print("Downloading UHGG metadata (real download, may take a little while) ...")
    resp = requests.get(UHGG_METADATA_URL, timeout=300)
    resp.raise_for_status()
    lines = resp.text.splitlines()
    header = lines[0].split("\t")
    idx = {col: i for i, col in enumerate(header)}
    print(f"  {len(lines) - 1} total genome rows received")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    lookup = build_organism_lookup(conn)
    print(f"  matching against {len(lookup)} known organism names/synonyms\n")

    matched, skipped_uncultured, skipped_no_match = 0, 0, 0

    for line in lines[1:]:
        fields = line.split("\t")
        if fields[idx["Genome"]] != fields[idx["Species_rep"]]:
            continue

        species_name = parse_species_name(fields[idx["Lineage"]])
        if not species_name:
            skipped_uncultured += 1
            continue

        taxid = lookup.get(species_name.lower())
        if not taxid:
            skipped_no_match += 1
            continue

        completeness = float(fields[idx["Completeness"]])
        contamination = float(fields[idx["Contamination"]])

        conn.execute(
            """
            INSERT OR IGNORE INTO genome
                (genome_id, ncbi_taxid, source_db, accession, is_mag,
                 completeness_pct, contamination_pct, quality_tier)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fields[idx["Genome"]],
                taxid,
                "UHGG v2.0.2",
                fields[idx["Genome_accession"]],
                fields[idx["Genome_type"]] != "Isolate",
                completeness,
                contamination,
                quality_tier(completeness, contamination),
            ),
        )
        conn.execute(
            "UPDATE organism SET representative_genome_id = ? WHERE ncbi_taxid = ?",
            (fields[idx["Genome"]], taxid),
        )
        matched += 1
        print(
            f"  [match] {species_name} -> taxid {taxid}, "
            f"completeness={completeness}%, contamination={contamination}%"
        )

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(
        f"\nDone. {matched} organisms matched and enriched with real genome data, "
        f"{skipped_uncultured} UHGG species skipped (uncultured/no usable binomial name), "
        f"{skipped_no_match} UHGG species had a real name but no match in our organism table."
    )
    if issues:
        print(f"WARNING: {len(issues)} foreign key issues:")
        for i in issues[:10]:
            print(f"  {i}")
    else:
        print("Foreign key integrity check passed.")


if __name__ == "__main__":
    main()
