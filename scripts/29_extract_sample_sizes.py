"""
29_extract_sample_sizes.py

Fills sample_size for studies that have it as NULL -- currently 100%
of studies (1,099 of 1,099). Two root causes: Disbiome's own
experiment records mostly lack subject_value/control_value, and
BugSigDB studies were never registered as `study` rows at all in
script 25.

Step 1: registers real study rows for BugSigDB studies (by PMID),
using the real Group 0 + Group 1 sample sizes already present in
BugSigDB's own data.

Step 2: for remaining Disbiome-sourced studies with a real PMID,
fetches the real abstract and extracts a sample size via regex on
common phrasings. Patterns are intentionally narrow -- a number is
only extracted when directly attached to a participant-counting
phrase (allowing up to 2 descriptive words in between, e.g. "42
healthy controls"). When multiple distinct candidate numbers are
found, the row is left NULL and flagged rather than guessing.

Note: BugSigDB's own real source data contains one row with
PMID=12345678 (a clear upstream curator typo -- that PMID actually
belongs to a 1990s UN population policy document, not a microbiome
paper). This coincidentally matches our own smoke-test fixture's fake
PMID, so it's explicitly excluded to avoid tripping our own
test-contamination integrity check.

Usage:
    pip install requests
    python scripts/29_extract_sample_sizes.py
"""

import csv
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
DUMP_PATH = PROJECT_ROOT / "data" / "raw" / "bugsigdb" / "full_dump.csv"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_DELAY = 0.4
BATCH_SIZE = 100

SAMPLE_SIZE_PATTERNS = [
    re.compile(r"\bn\s*=\s*(\d{2,4})\b"),
    re.compile(r"\b(\d{2,4})\s+(?:\w+\s+){0,2}(?:patients|subjects|participants|individuals|volunteers|controls|cases)\b", re.I),
    re.compile(r"\bcohort of\s+(\d{2,4})\b", re.I),
    re.compile(r"\benrolled\s+(\d{2,4})\b", re.I),
    re.compile(r"\ba total of\s+(\d{2,4})\s+(?:\w+\s+){0,2}(?:patients|subjects|participants|individuals)\b", re.I),
]


def extract_sample_size(abstract: str):
    candidates = set()
    for pattern in SAMPLE_SIZE_PATTERNS:
        for match in pattern.finditer(abstract):
            candidates.add(int(match.group(1)))
    if len(candidates) == 1:
        return candidates.pop(), False
    if len(candidates) > 1:
        return None, True
    return None, False


def is_real(val):
    return val and val.strip().upper() != "NA"


EXCLUDED_PMIDS = {"12345678"}


def register_bugsigdb_studies(conn):
    if not DUMP_PATH.exists():
        print("BugSigDB dump not found -- skipping BugSigDB study registration.")
        return 0

    with open(DUMP_PATH, encoding="utf-8") as f:
        next(f)
        reader = csv.DictReader(f)
        rows = list(reader)

    registered = 0
    seen_pmids = set()
    for r in rows:
        pmid = r.get("PMID", "").strip()
        if not is_real(pmid) or pmid in seen_pmids or pmid in EXCLUDED_PMIDS:
            continue
        seen_pmids.add(pmid)

        g0 = r.get("Group 0 sample size", "").strip()
        g1 = r.get("Group 1 sample size", "").strip()
        total = None
        if is_real(g0) and is_real(g1):
            try:
                total = int(g0) + int(g1)
            except ValueError:
                total = None

        study_id = f"PMID:{pmid}"
        existing = conn.execute("SELECT sample_size FROM study WHERE study_id = ?", (study_id,)).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO study (study_id, study_type, pmid, sample_size, title) VALUES (?, ?, ?, ?, ?)",
                (study_id, "observational", pmid, total, r.get("Title", "")),
            )
            registered += 1
        elif existing[0] is None and total is not None:
            conn.execute("UPDATE study SET sample_size = ? WHERE study_id = ?", (total, study_id))
            registered += 1

    conn.commit()
    return registered


def fetch_abstracts(pmids):
    resp = requests.post(
        f"{EUTILS_BASE}/efetch.fcgi",
        data={"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"},
        timeout=30,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    results = {}
    for article in root.findall(".//PubmedArticle"):
        pmid_el = article.find(".//PMID")
        if pmid_el is None:
            continue
        parts = [el.text for el in article.findall(".//AbstractText") if el.text]
        results[pmid_el.text] = " ".join(parts)
    return results


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    print("Step 1: registering BugSigDB study rows with their real sample sizes ...")
    n_registered = register_bugsigdb_studies(conn)
    print(f"  {n_registered} study rows registered/updated from BugSigDB's own data\n")

    print("Step 2: extracting sample sizes for remaining studies via abstract mining ...")
    rows = conn.execute(
        "SELECT study_id, pmid FROM study WHERE sample_size IS NULL AND pmid IS NOT NULL AND pmid != ''"
    ).fetchall()
    print(f"  {len(rows)} studies have a real PMID and still need a sample size\n")

    by_pmid = {pmid: study_id for study_id, pmid in rows}
    all_pmids = list(by_pmid.keys())

    extracted, ambiguous, not_found = 0, 0, 0
    ambiguous_examples = []

    total_batches = (len(all_pmids) - 1) // BATCH_SIZE + 1 if all_pmids else 0
    for i in range(0, len(all_pmids), BATCH_SIZE):
        batch = all_pmids[i:i + BATCH_SIZE]
        try:
            abstracts = fetch_abstracts(batch)
        except Exception as exc:
            print(f"  [batch {i // BATCH_SIZE + 1} failed] {type(exc).__name__}: {exc}")
            time.sleep(REQUEST_DELAY)
            continue

        for pmid in batch:
            abstract = abstracts.get(pmid)
            if not abstract:
                not_found += 1
                continue
            size, is_ambiguous = extract_sample_size(abstract)
            if size is not None:
                conn.execute("UPDATE study SET sample_size = ? WHERE study_id = ?", (size, by_pmid[pmid]))
                extracted += 1
            elif is_ambiguous:
                ambiguous += 1
                if len(ambiguous_examples) < 5:
                    ambiguous_examples.append((pmid, abstract[:200]))

        conn.commit()
        print(f"  batch {i // BATCH_SIZE + 1}/{total_batches} done ({len(batch)} PMIDs)")
        time.sleep(REQUEST_DELAY)

    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()

    total_studies = conn.execute("SELECT COUNT(*) FROM study").fetchone()[0]
    still_null = conn.execute("SELECT COUNT(*) FROM study WHERE sample_size IS NULL").fetchone()[0]
    conn.close()

    print(f"\nDone.")
    print(f"  {extracted} sample sizes extracted from abstracts (single, unambiguous match)")
    print(f"  {ambiguous} abstracts had multiple disagreeing candidate numbers -- left NULL, not guessed")
    print(f"  {not_found} PMIDs had no abstract returned")
    print(f"\n  {total_studies - still_null} of {total_studies} studies now have a real sample_size ({100*(total_studies-still_null)/total_studies:.0f}%)")
    if ambiguous_examples:
        print("\nSample of ambiguous cases (for manual review):")
        for pmid, snippet in ambiguous_examples:
            print(f"  PMID {pmid}: {snippet}...")
    print("\nForeign key check:", "passed" if not issues else issues)


if __name__ == "__main__":
    main()
