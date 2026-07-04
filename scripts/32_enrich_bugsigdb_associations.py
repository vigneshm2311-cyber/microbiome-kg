"""
32_enrich_bugsigdb_associations.py

Adds three evidence-quality fields to BugSigDB-sourced rows in one
pass over the full_dump.csv already downloaded for scripts 25/28/29:

1. sample_uberon_id on microbe_disease: body site of the sample
   (100% populated in our filtered set) -- allows filtering by
   sampling location, crucial for interpreting cross-site associations.

2. lda_score on microbe_disease: LEfSe LDA score threshold as an
   effect-size proxy (28% populated -- only available for LEfSe-
   analyzed signatures; missing = different statistical method used,
   not a weaker association).

3. detection_method + sequencing_region on study: sequencing
   technology (16S/WMS/PCR/ITS/18S) and variable region if 16S
   (99% and 76% populated respectively). 16S typically can't resolve
   below genus level; WMS can. Named detection_method rather than
   sequencing_type to match the existing canonical schema column.

Usage:
    python scripts/32_enrich_bugsigdb_associations.py
"""

import csv
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
DUMP_PATH = PROJECT_ROOT / "data" / "raw" / "bugsigdb" / "full_dump.csv"
SOURCE_LABEL = "BugSigDB"

CLEAR_DESIGNS = {
    "randomized controlled trial", "case-control",
    "cross-sectional observational, not case-control", "prospective cohort",
}


def is_real(val):
    return val and val.strip().upper() != "NA"


def ensure_columns(conn):
    md_cols = {row[1] for row in conn.execute("PRAGMA table_info(microbe_disease)")}
    for col, coltype in [
        ("sample_uberon_id", "TEXT"),
        ("lda_score", "REAL"),
        ("sequencing_region", "TEXT"),
    ]:
        if col not in md_cols:
            conn.execute(f"ALTER TABLE microbe_disease ADD COLUMN {col} {coltype}")

    st_cols = {row[1] for row in conn.execute("PRAGMA table_info(study)")}
    if "sequencing_region" not in st_cols:
        conn.execute("ALTER TABLE study ADD COLUMN sequencing_region TEXT")
    conn.commit()


def main():
    if not DUMP_PATH.exists():
        raise SystemExit(f"Expected {DUMP_PATH} -- same file used by scripts 25/28/29.")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    ensure_columns(conn)

    with open(DUMP_PATH, encoding="utf-8") as f:
        next(f)
        reader = csv.DictReader(f)
        rows = list(reader)

    filtered = [
        r for r in rows
        if r["Study design"] in CLEAR_DESIGNS
        and is_real(r.get("NCBI Taxonomy IDs", ""))
        and is_real(r.get("Group 1 sample size", ""))
        and len(r["NCBI Taxonomy IDs"].split("|")) <= 40
        and is_real(r.get("EFO ID", ""))
        and r.get("Abundance in Group 1") in ("increased", "decreased")
    ]
    print(f"{len(filtered)} filtered BugSigDB rows to process\n")

    known_taxids = {row[0] for row in conn.execute("SELECT ncbi_taxid FROM organism")}
    existing_mondo_ids = {row[0] for row in conn.execute("SELECT mondo_id FROM disease")}
    existing_uberon_ids = {row[0] for row in conn.execute("SELECT uberon_id FROM body_site")}

    new_uberon = 0
    for r in filtered:
        uberon_id = r.get("UBERON ID", "").strip()
        body_site_name = r.get("Body site", "").strip()
        if is_real(uberon_id) and uberon_id not in existing_uberon_ids:
            conn.execute(
                "INSERT OR IGNORE INTO body_site (uberon_id, name, source_db) VALUES (?, ?, ?)",
                (uberon_id, body_site_name, "BugSigDB"),
            )
            existing_uberon_ids.add(uberon_id)
            new_uberon += 1
    conn.commit()
    print(f"Registered {new_uberon} new UBERON body-site IDs from BugSigDB\n")

    pair_data = defaultdict(lambda: {"uberon_ids": [], "lda_scores": [], "seq_types": [], "seq_regions": []})

    for r in filtered:
        ids = [x.strip() for x in r["EFO ID"].split(",")]
        taxids = [t.strip() for t in r["NCBI Taxonomy IDs"].split("|")]
        uberon_id = r.get("UBERON ID", "").strip()
        lda_raw = r.get("LDA Score above", "").strip()
        seq_type = r.get("Sequencing type", "").strip()
        seq_region = r.get("16S variable region", "").strip()

        for cond_id in ids:
            prefix = cond_id.split(":")[0] if ":" in cond_id else ""
            if prefix not in ("MONDO", "EFO"):
                continue
            if cond_id not in existing_mondo_ids:
                continue
            for taxid in taxids:
                if taxid not in known_taxids:
                    continue
                key = (taxid, cond_id)
                if is_real(uberon_id):
                    pair_data[key]["uberon_ids"].append(uberon_id)
                if is_real(lda_raw):
                    try:
                        pair_data[key]["lda_scores"].append(float(lda_raw))
                    except ValueError:
                        pass
                if is_real(seq_type):
                    pair_data[key]["seq_types"].append(seq_type)
                if is_real(seq_region):
                    pair_data[key]["seq_regions"].append(seq_region)

    updated_md = 0
    for (taxid, mondo_id), data in pair_data.items():
        uberon_id = max(set(data["uberon_ids"]), key=data["uberon_ids"].count) if data["uberon_ids"] else None
        lda = max(data["lda_scores"]) if data["lda_scores"] else None
        seq_type = max(set(data["seq_types"]), key=data["seq_types"].count) if data["seq_types"] else None
        seq_region = max(set(data["seq_regions"]), key=data["seq_regions"].count) if data["seq_regions"] else None

        conn.execute(
            """
            UPDATE microbe_disease
            SET sample_uberon_id = ?, lda_score = ?, sequencing_region = ?
            WHERE ncbi_taxid = ? AND mondo_id = ? AND source_db = ?
            """,
            (uberon_id, lda, seq_region, taxid, mondo_id, SOURCE_LABEL),
        )
        updated_md += 1

    conn.commit()
    print(f"Updated {updated_md} microbe_disease rows with body-site, LDA score, sequencing region\n")

    updated_studies = 0
    seen_pmids = set()
    for r in filtered:
        pmid = r.get("PMID", "").strip()
        if not is_real(pmid) or pmid in seen_pmids:
            continue
        seen_pmids.add(pmid)
        seq_type = r.get("Sequencing type", "").strip()
        seq_region = r.get("16S variable region", "").strip()
        study_id = f"PMID:{pmid}"
        if is_real(seq_type) or is_real(seq_region):
            conn.execute(
                """
                UPDATE study SET detection_method = ?, sequencing_region = ?
                WHERE study_id = ? AND (detection_method IS NULL OR detection_method = '')
                """,
                (seq_type if is_real(seq_type) else None,
                 seq_region if is_real(seq_region) else None,
                 study_id),
            )
            updated_studies += 1

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()

    md_with_uberon = conn.execute(
        "SELECT COUNT(*) FROM microbe_disease WHERE sample_uberon_id IS NOT NULL"
    ).fetchone()[0]
    md_with_lda = conn.execute(
        "SELECT COUNT(*) FROM microbe_disease WHERE lda_score IS NOT NULL"
    ).fetchone()[0]
    studies_with_method = conn.execute(
        "SELECT COUNT(*) FROM study WHERE detection_method IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    print(f"Done.")
    print(f"  microbe_disease rows with sample_uberon_id: {md_with_uberon}")
    print(f"  microbe_disease rows with lda_score: {md_with_lda}")
    print(f"  study rows with detection_method: {studies_with_method}")
    print(f"  study rows updated this run: {updated_studies}")
    if issues:
        print(f"\nWARNING: {len(issues)} foreign key issues:")
        for i in issues[:10]:
            print(f"  {i}")
    else:
        print("\nForeign key integrity check passed.")


if __name__ == "__main__":
    main()
