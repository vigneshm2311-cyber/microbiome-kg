"""
25_load_bugsigdb.py

Loads real differential-abundance signatures from BugSigDB
(waldronlab/BugSigDBExports, full_dump.csv) into microbe_disease,
phenotype, and intervention -- the first new disease-association
source since Disbiome, and the first data ever loaded into
`phenotype` (empty since the schema was designed).

Quality filters applied:
  - Study design restricted to randomized controlled trial,
    case-control, cross-sectional observational (not case-control),
    or prospective cohort.
  - Real (non-NA) NCBI Taxonomy IDs required.
  - Real (non-NA) Group 1 sample size required.
  - Signature size capped at 40 taxa.

The "EFO ID" column is multi-ontology and can be comma-separated.
After splitting on comma and bucketing by real prefix:
  - MONDO: (283 distinct terms) -- joined directly, no crosswalk.
  - EFO: classified via OLS4's real ontological hierarchy -- 37 of
    171 are genuine diseases.
  - HP: (44 terms) -- loaded into phenotype table.
  - CHEBI: (10 terms) -- loaded into intervention table.
  - Everything else -- out of scope this pass.

Abundance in Group 1 ("increased"/"decreased") maps directly onto
our existing direction column; verified to apply uniformly across
every taxon in a signature.

Usage:
    pip install requests
    Place full_dump.csv at data/raw/bugsigdb/full_dump.csv
    python scripts/25_load_bugsigdb.py
"""

import csv
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
DUMP_PATH = PROJECT_ROOT / "data" / "raw" / "bugsigdb" / "full_dump.csv"
OLS4_BASE = "https://www.ebi.ac.uk/ols4/api"
REQUEST_DELAY = 0.3
SOURCE_LABEL = "BugSigDB"

CLEAR_DESIGNS = {
    "randomized controlled trial", "case-control",
    "cross-sectional observational, not case-control", "prospective cohort",
}


def is_real(val):
    return val and val.strip().upper() != "NA"


def load_filtered_rows():
    with open(DUMP_PATH, encoding="utf-8") as f:
        next(f)
        reader = csv.DictReader(f)
        rows = list(reader)

    filtered = []
    for r in rows:
        if r["Study design"] not in CLEAR_DESIGNS:
            continue
        if not is_real(r.get("NCBI Taxonomy IDs", "")):
            continue
        if not is_real(r.get("Group 1 sample size", "")):
            continue
        if len(r["NCBI Taxonomy IDs"].split("|")) > 40:
            continue
        if not is_real(r.get("EFO ID", "")):
            continue
        if r.get("Abundance in Group 1") not in ("increased", "decreased"):
            continue
        filtered.append(r)
    return filtered


def classify_efo_term(efo_id, cache):
    if efo_id in cache:
        return cache[efo_id]
    try:
        resp = requests.get(f"{OLS4_BASE}/ontologies/efo/terms", params={"obo_id": efo_id}, timeout=20)
        resp.raise_for_status()
        terms = resp.json().get("_embedded", {}).get("terms", [])
        if not terms:
            cache[efo_id] = (False, None)
            return cache[efo_id]
        term = terms[0]
        links = term.get("_links", {})
        if "hierarchicalAncestors" not in links:
            cache[efo_id] = (False, term.get("label"))
            return cache[efo_id]
        anc_resp = requests.get(links["hierarchicalAncestors"]["href"], timeout=20)
        ancestors = anc_resp.json().get("_embedded", {}).get("terms", [])
        anc_labels = [a.get("label", "").lower() for a in ancestors if a.get("label")]
        is_disease = "disease" in anc_labels
        cache[efo_id] = (is_disease, term.get("label"))
    except Exception:
        cache[efo_id] = (False, None)
    time.sleep(REQUEST_DELAY)
    return cache[efo_id]


def ensure_columns(conn):
    md_cols = {row[1] for row in conn.execute("PRAGMA table_info(microbe_disease)")}
    if "source_db" not in md_cols:
        conn.execute("ALTER TABLE microbe_disease ADD COLUMN source_db TEXT")
    pheno_cols = {row[1] for row in conn.execute("PRAGMA table_info(phenotype)")}
    if "source_db" not in pheno_cols:
        conn.execute("ALTER TABLE phenotype ADD COLUMN source_db TEXT")
    has_mp = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='microbe_phenotype'"
    ).fetchone()
    if not has_mp:
        conn.execute(
            """
            CREATE TABLE microbe_phenotype (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ncbi_taxid TEXT NOT NULL REFERENCES organism(ncbi_taxid),
                hpo_id TEXT NOT NULL REFERENCES phenotype(hpo_id),
                direction TEXT,
                study_count INTEGER,
                source_db TEXT
            )
            """
        )
    conn.commit()


def main():
    if not DUMP_PATH.exists():
        raise SystemExit(f"Expected {DUMP_PATH} -- place full_dump.csv there first.")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    ensure_columns(conn)

    conn.execute("DELETE FROM microbe_disease WHERE source_db = ?", (SOURCE_LABEL,))
    conn.execute("DELETE FROM microbe_phenotype WHERE source_db = ?", (SOURCE_LABEL,))
    conn.execute("DELETE FROM intervention WHERE source = ?", (SOURCE_LABEL,))
    conn.commit()

    print("Loading and filtering BugSigDB dump ...")
    rows = load_filtered_rows()
    print(f"  {len(rows)} signatures pass all quality filters\n")

    known_taxids = {row[0] for row in conn.execute("SELECT ncbi_taxid FROM organism")}

    efo_cache = {}
    disease_pairs = defaultdict(lambda: {"increased": 0, "decreased": 0})
    phenotype_pairs = defaultdict(int)
    chebi_terms_seen = {}

    print("Classifying conditions and aggregating evidence ...")
    for i, r in enumerate(rows):
        ids = [x.strip() for x in r["EFO ID"].split(",")]
        labels = [x.strip() for x in r["Condition"].split(",")]
        taxids = [t.strip() for t in r["NCBI Taxonomy IDs"].split("|")]
        direction = r["Abundance in Group 1"]

        for j, cond_id in enumerate(ids):
            label = labels[j] if j < len(labels) else r["Condition"]
            prefix = cond_id.split(":")[0] if ":" in cond_id else "UNKNOWN"

            if prefix == "MONDO":
                mondo_id = cond_id
            elif prefix == "EFO":
                is_disease, _ = classify_efo_term(cond_id, efo_cache)
                if not is_disease:
                    continue
                mondo_id = cond_id
            elif prefix == "HP":
                conn.execute(
                    "INSERT OR IGNORE INTO phenotype (hpo_id, name, source_db) VALUES (?, ?, ?)",
                    (cond_id, label, SOURCE_LABEL),
                )
                for taxid in taxids:
                    if taxid in known_taxids:
                        phenotype_pairs[(taxid, cond_id, direction)] += 1
                continue
            elif prefix == "CHEBI":
                chebi_terms_seen[cond_id] = label
                continue
            else:
                continue

            disease_exists = conn.execute("SELECT 1 FROM disease WHERE mondo_id = ?", (mondo_id,)).fetchone()
            if not disease_exists:
                conn.execute("INSERT OR IGNORE INTO disease (mondo_id, name) VALUES (?, ?)", (mondo_id, label))

            for taxid in taxids:
                if taxid not in known_taxids:
                    continue
                disease_pairs[(taxid, mondo_id)][direction] += 1

        if (i + 1) % 1000 == 0:
            print(f"  ... {i + 1}/{len(rows)} signatures processed")

    print(f"\nInserting {len(disease_pairs)} (organism, disease) pairs ...")
    for (taxid, mondo_id), counts in disease_pairs.items():
        total = counts["increased"] + counts["decreased"]
        majority_dir = "increased" if counts["increased"] >= counts["decreased"] else "decreased"
        minority = min(counts["increased"], counts["decreased"])
        grade = "D" if minority > 0 else ("A" if total >= 2 else "B")
        conn.execute(
            """
            INSERT INTO microbe_disease
                (ncbi_taxid, mondo_id, direction, evidence_grade,
                 replication_count, contradiction_count, source_db)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (taxid, mondo_id, majority_dir, grade, max(counts["increased"], counts["decreased"]), minority, SOURCE_LABEL),
        )

    print(f"Inserting {len(phenotype_pairs)} (organism, phenotype) pairs ...")
    for (taxid, hpo_id, direction), count in phenotype_pairs.items():
        conn.execute(
            "INSERT INTO microbe_phenotype (ncbi_taxid, hpo_id, direction, study_count, source_db) VALUES (?, ?, ?, ?, ?)",
            (taxid, hpo_id, direction, count, SOURCE_LABEL),
        )

    print(f"Inserting {len(chebi_terms_seen)} CHEBI chemical terms into intervention ...")
    for chebi_id, label in chebi_terms_seen.items():
        conn.execute(
            "INSERT OR IGNORE INTO intervention (intervention_id, type, name, source) VALUES (?, ?, ?, ?)",
            (chebi_id, "drug", label, SOURCE_LABEL),
        )

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(f"\nDone.")
    print(f"  {len(disease_pairs)} microbe_disease rows added (BugSigDB)")
    print(f"  {len(phenotype_pairs)} microbe_phenotype rows added (first data ever in this table)")
    print(f"  {len(chebi_terms_seen)} intervention rows added (CHEBI chemicals, names only)")
    if issues:
        print(f"WARNING: {len(issues)} foreign key issues:")
        for i in issues[:10]:
            print(f"  {i}")
    else:
        print("Foreign key integrity check passed.")


if __name__ == "__main__":
    main()
