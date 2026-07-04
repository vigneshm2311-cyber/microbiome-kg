"""
09_map_studies_to_pmid.py

Closes the documented "DISBIOME_PUB:<id> isn't a real PMID" gap.
Disbiome's /publication endpoint (found the same way as /experiment --
following the same REST naming pattern) returns rich per-study
metadata: real PubMed URLs (and therefore real PMIDs), DOIs, titles,
and first authors.

Worth knowing about for later, not used here: the same records also
carry Disbiome's own per-study quality-assessment questionnaire
answers (age of subjects reported, controls matched for confounders,
etc.) -- a real opportunity to make evidence_grade computation more
rigorous in a future pass, not attempted in this one.

Migrates study.study_id from DISBIOME_PUB:<id> to PMID:<pmid> where a
PMID is available, updating every table that references study_id
(microbe_disease, microbe_metabolite, microbe_intervention) so
foreign keys stay consistent, and merging instead of duplicating if
two different Disbiome publication records turn out to share a PMID.

Usage:
    pip install requests
    python scripts/09_map_studies_to_pmid.py
"""

import re
import sqlite3
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"

DISBIOME_PUBLICATION_URL = "https://disbiome.ugent.be:8080/publication"

# Every table with a study_id foreign key -- kept in one place so a
# future new edge table referencing study_id is just one line to add.
REFERENCING_TABLES = ["microbe_disease", "microbe_metabolite", "microbe_intervention"]


def extract_pmid(pubmed_url):
    if not pubmed_url:
        return None
    match = re.search(r"/pubmed/(\d+)", pubmed_url)
    return match.group(1) if match else None


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(study)")}
    for col in ("pmid", "title", "first_author", "doi"):
        if col not in cols:
            conn.execute(f"ALTER TABLE study ADD COLUMN {col} TEXT")
    conn.commit()


def fetch_publications() -> list:
    resp = requests.get(DISBIOME_PUBLICATION_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()


def repoint_references(conn: sqlite3.Connection, old_id: str, new_id: str) -> None:
    for table in REFERENCING_TABLES:
        conn.execute(f"UPDATE {table} SET study_id = ? WHERE study_id = ?", (new_id, old_id))


def main():
    print("Fetching publication metadata from Disbiome ...")
    publications = fetch_publications()
    print(f"  {len(publications)} publication records received")
    by_disbiome_id = {p["publication_id"]: p for p in publications}

    conn = sqlite3.connect(DB_PATH)
    ensure_columns(conn)
    conn.execute("PRAGMA foreign_keys = OFF")

    placeholders = conn.execute(
        "SELECT study_id FROM study WHERE study_id LIKE 'DISBIOME_PUB:%'"
    ).fetchall()

    migrated, merged, no_pmid = 0, 0, 0

    for (placeholder_id,) in placeholders:
        disbiome_id = int(placeholder_id.split(":", 1)[1])
        pub = by_disbiome_id.get(disbiome_id)
        if not pub:
            no_pmid += 1
            continue

        pmid = extract_pmid(pub.get("pubmed_url"))
        if not pmid:
            no_pmid += 1
            continue

        real_study_id = f"PMID:{pmid}"
        existing = conn.execute(
            "SELECT 1 FROM study WHERE study_id = ?", (real_study_id,)
        ).fetchone()

        if existing:
            repoint_references(conn, placeholder_id, real_study_id)
            conn.execute("DELETE FROM study WHERE study_id = ?", (placeholder_id,))
            merged += 1
        else:
            conn.execute(
                """
                UPDATE study
                SET study_id = ?, pmid = ?, title = ?, first_author = ?, doi = ?,
                    publication_date = ?
                WHERE study_id = ?
                """,
                (
                    real_study_id,
                    pmid,
                    pub.get("title"),
                    pub.get("first_author"),
                    pub.get("doi"),
                    str(pub.get("year_of_publication") or ""),
                    placeholder_id,
                ),
            )
            repoint_references(conn, placeholder_id, real_study_id)
            migrated += 1

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(
        f"\nDone. {migrated} studies migrated to real PMIDs, {merged} merged into an "
        f"already-migrated study, {no_pmid} left as placeholders (no PMID available)."
    )
    if issues:
        print(f"WARNING: {len(issues)} foreign key issues detected:")
        for issue in issues[:10]:
            print(f"  {issue}")
    else:
        print("Foreign key integrity check passed.")


if __name__ == "__main__":
    main()
