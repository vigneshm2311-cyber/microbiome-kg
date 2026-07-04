"""
22_classify_studies_from_abstracts.py

Improves on script 21's title-only classification by fetching real
abstracts via NCBI's PubMed E-utilities (the same eutils.ncbi.nlm.nih.gov
endpoint already used successfully in scripts 03/05/15/16/20 --
unaffected by the separate Disbiome connectivity issue). Abstracts
almost always state study design explicitly ("we conducted a
randomized, double-blind trial..."), unlike journal titles, which
rarely do -- this is why script 21 only reclassified 1 of 936 studies
despite the underlying literature surely containing more RCTs and
animal studies than that.

Same classification priority and patterns as script 21 (rct >
gnotobiotic_mouse > in_vitro > observational default), just applied
to abstract text instead of title text.

Usage:
    pip install requests
    python scripts/22_classify_studies_from_abstracts.py
"""

import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
REQUEST_DELAY = 0.4
BATCH_SIZE = 100

PATTERNS = [
    ("rct", re.compile(r"randomi[sz]ed.{0,30}(controlled|clinical)?\s*trial|double-blind|placebo-controlled|\bRCT\b", re.I)),
    ("gnotobiotic_mouse", re.compile(r"gnotobiotic|germ-free mice|germ free mice|\bGF mice\b|axenic mice|humanized mice|conventionalized mice", re.I)),
    ("in_vitro", re.compile(r"\bin vitro\b|bioreactor|chemostat|fermentation model|simulated (gut|intestinal|colon)", re.I)),
]


def classify(text: str) -> str:
    for label, pattern in PATTERNS:
        if pattern.search(text):
            return label
    return "observational"


def fetch_abstracts(pmids: list) -> dict:
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
        if pmid_el is None or not pmid_el.text:
            continue
        pmid = pmid_el.text
        abstract_parts = [el.text for el in article.findall(".//AbstractText") if el.text]
        results[pmid] = " ".join(abstract_parts)
    return results


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    rows = conn.execute(
        "SELECT study_id, pmid, study_type FROM study WHERE pmid IS NOT NULL AND pmid != ''"
    ).fetchall()
    print(f"{len(rows)} studies have a real PMID -- fetching abstracts in batches of {BATCH_SIZE}\n")

    by_pmid = {pmid: (study_id, current_type) for study_id, pmid, current_type in rows}
    all_pmids = list(by_pmid.keys())

    changes = {"rct": 0, "gnotobiotic_mouse": 0, "in_vitro": 0}
    examples = {"rct": [], "gnotobiotic_mouse": [], "in_vitro": []}
    unchanged = 0
    not_found = 0

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
            study_id, current_type = by_pmid[pmid]
            new_type = classify(abstract)
            if new_type == "observational":
                unchanged += 1
                continue
            conn.execute("UPDATE study SET study_type = ? WHERE study_id = ?", (new_type, study_id))
            changes[new_type] += 1
            if len(examples[new_type]) < 3:
                examples[new_type].append(abstract[:150])

        conn.commit()
        print(f"  batch {i // BATCH_SIZE + 1}/{total_batches} done ({len(batch)} PMIDs)")
        time.sleep(REQUEST_DELAY)

    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(f"\nDone.")
    for label, count in changes.items():
        print(f"  {label}: {count} reclassified")
        for ex in examples[label]:
            print(f"    e.g. \"{ex}...\"")
    print(f"\n  observational (unchanged): {unchanged}")
    print(f"  {not_found} PMIDs had no abstract returned (retracted, missing, or malformed response)")
    print("\nForeign key check:", "passed" if not issues else issues)


if __name__ == "__main__":
    main()
