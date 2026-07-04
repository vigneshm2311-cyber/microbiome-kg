"""
23_fix_overeager_in_vitro_classifications.py

Script 22's in_vitro pattern matched any occurrence of "in vitro"
anywhere in the abstract -- too permissive. Manual verification
showed most matches were a secondary mechanistic follow-up bolted
onto an otherwise clinical/observational study, not studies where
in-vitro work was the actual primary design. Disbiome curates
compositional comparisons between patient and healthy samples -- that
comparison is almost always the clinical sampling itself.

This reverts every current in_vitro classification back to
observational, then retries with a much tighter pattern requiring
phrases that describe the *overall* study apparatus, not a passing
mention of something tested in vitro as one part of a larger study.

rct and gnotobiotic_mouse classifications from script 22 are left
untouched -- manual verification confirmed those were genuine.

Usage:
    pip install requests
    python scripts/23_fix_overeager_in_vitro_classifications.py
"""

import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

TIGHT_IN_VITRO = re.compile(
    r"\bin vitro (model|system|fermentation|digestion model|gut model|colon model)\b"
    r"|\b(developed|established|used) an in vitro\b",
    re.I,
)


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

    in_vitro_rows = conn.execute(
        "SELECT study_id, pmid FROM study WHERE study_type = 'in_vitro' AND pmid IS NOT NULL"
    ).fetchall()
    print(f"Reverting {len(in_vitro_rows)} in_vitro classifications to observational, then retrying with a tighter pattern.\n")

    for study_id, _ in in_vitro_rows:
        conn.execute("UPDATE study SET study_type = 'observational' WHERE study_id = ?", (study_id,))
    conn.commit()

    pmids = [pmid for _, pmid in in_vitro_rows]
    abstracts = fetch_abstracts(pmids)

    rematched, examples = 0, []
    for study_id, pmid in in_vitro_rows:
        abstract = abstracts.get(pmid)
        if not abstract:
            continue
        match = TIGHT_IN_VITRO.search(abstract)
        if match:
            conn.execute("UPDATE study SET study_type = 'in_vitro' WHERE study_id = ?", (study_id,))
            rematched += 1
            start, end = max(0, match.start() - 50), min(len(abstract), match.end() + 50)
            examples.append((pmid, match.group(), abstract[start:end]))

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(f"Done. {rematched} of {len(in_vitro_rows)} still genuinely match a whole-study-design pattern; "
          f"the rest are now correctly back to observational.\n")
    for pmid, matched, context in examples:
        print(f"  PMID {pmid}: matched \"{matched}\"")
        print(f"    ...{context}...")
    print("\nForeign key check:", "passed" if not issues else issues)


if __name__ == "__main__":
    main()
