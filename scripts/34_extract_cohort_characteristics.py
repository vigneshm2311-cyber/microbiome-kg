"""
34_extract_cohort_characteristics.py

Extracts cohort characteristics (age groups, sex, geography) from
PubMed MeSH terms for studies that have a real PMID.

MeSH age terms are overlapping ranges stored as semicolon-separated
strings rather than collapsed to a single label.
~95% age coverage, ~80% sex coverage, ~20% geography coverage
expected based on real sample testing.

New columns on study: population_age, population_sex, geography

Usage:
    python scripts/34_extract_cohort_characteristics.py
"""

import sqlite3
import time
import xml.etree.ElementTree as ET
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_DELAY = 0.4
BATCH_SIZE = 100

AGE_KEYWORDS = {
    "Infant", "Child", "Child, Preschool", "Adolescent",
    "Young Adult", "Adult", "Middle Aged", "Aged", "Aged, 80 and over",
}
SEX_KEYWORDS = {"Female", "Male"}
GEO_KEYWORDS = {
    "China", "Japan", "Korea", "India", "Europe", "United States",
    "United Kingdom", "Germany", "France", "Italy", "Spain",
    "Netherlands", "Sweden", "Denmark", "Finland", "Norway",
    "Australia", "Canada", "Brazil", "Iran", "Turkey",
    "Mexico", "Egypt", "Israel", "Singapore",
}


def ensure_columns(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(study)")}
    for col in ("population_age", "population_sex", "geography"):
        if col not in cols:
            conn.execute(f"ALTER TABLE study ADD COLUMN {col} TEXT")
    conn.commit()


def fetch_mesh_terms(pmids: list) -> dict:
    url = f"{EUTILS_BASE}/efetch.fcgi?db=pubmed&id={','.join(pmids)}&rettype=xml"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        root = ET.fromstring(resp.read())
    results = {}
    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//PMID")
        if pmid_el is None:
            continue
        terms = [h.findtext("DescriptorName") for h in article.findall(".//MeshHeading")]
        results[pmid_el.text] = [t for t in terms if t]
    return results


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    ensure_columns(conn)

    rows = conn.execute(
        "SELECT study_id, pmid FROM study WHERE pmid IS NOT NULL AND pmid != ''"
    ).fetchall()
    print(f"{len(rows)} studies have a real PMID\n")

    by_pmid = {pmid: study_id for study_id, pmid in rows}
    all_pmids = list(by_pmid.keys())

    extracted_age, extracted_sex, extracted_geo = 0, 0, 0
    not_found, errors = 0, 0

    total_batches = (len(all_pmids) - 1) // BATCH_SIZE + 1 if all_pmids else 0
    for i in range(0, len(all_pmids), BATCH_SIZE):
        batch = all_pmids[i:i + BATCH_SIZE]
        try:
            mesh_by_pmid = fetch_mesh_terms(batch)
        except Exception as exc:
            print(f"  [batch {i // BATCH_SIZE + 1} failed] {type(exc).__name__}: {exc}")
            errors += len(batch)
            time.sleep(REQUEST_DELAY)
            continue

        for pmid in batch:
            terms = mesh_by_pmid.get(pmid)
            if not terms:
                not_found += 1
                continue

            term_set = set(terms)
            age = ";".join(sorted(t for t in term_set if t in AGE_KEYWORDS)) or None
            sex = ";".join(sorted(t for t in term_set if t in SEX_KEYWORDS)) or None
            geo = ";".join(sorted(t for t in term_set if t in GEO_KEYWORDS)) or None

            if age or sex or geo:
                conn.execute(
                    "UPDATE study SET population_age = ?, population_sex = ?, geography = ? WHERE study_id = ?",
                    (age, sex, geo, by_pmid[pmid]),
                )
                if age: extracted_age += 1
                if sex: extracted_sex += 1
                if geo: extracted_geo += 1

        conn.commit()
        print(f"  batch {i // BATCH_SIZE + 1}/{total_batches} done ({len(batch)} PMIDs)")
        time.sleep(REQUEST_DELAY)

    conn.execute("PRAGMA foreign_keys = ON")
    conn.close()

    total = len(all_pmids)
    print(f"\nDone.")
    print(f"  population_age populated: {extracted_age}/{total} ({100*extracted_age/total:.0f}%)")
    print(f"  population_sex populated: {extracted_sex}/{total} ({100*extracted_sex/total:.0f}%)")
    print(f"  geography populated:      {extracted_geo}/{total} ({100*extracted_geo/total:.0f}%)")
    print(f"  {not_found} PMIDs had no MeSH terms returned")
    print(f"  {errors} batch errors")


if __name__ == "__main__":
    main()
