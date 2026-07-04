"""
28_load_nondrug_interventions.py

Populates the 5 intervention types that have sat empty since the
schema was designed (food, probiotic, exercise, sleep, fasting).

Source: the same quality-filtered BugSigDB signature set used by
script 25. Real terms identified via direct inspection:
  food:      EFO:0002755 Diet, EFO:0008111 Diet measurement,
             EFO:0010757 Response to diet, EFO:0002757 High fat diet,
             EFO:0009371/EFO:0009372 Ketogenic diet (response)
  exercise:  EFO:0000483 Exercise, EFO:0003940/EFO:0008002
             Physical activity (measurement)
  fasting:   EFO:0002756 Fasting
  sleep:     OBA:2040171 Sleep duration
  probiotic: NCBITAXON:568703 (Lactobacillus rhamnosus GG),
             NCBITAXON:103818 (Lactobacillus kimchii)

Usage:
    Place full_dump.csv at data/raw/bugsigdb/full_dump.csv
    python scripts/28_load_nondrug_interventions.py
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

TARGET_TERMS = {
    "EFO:0002755": ("food", "Diet"),
    "EFO:0008111": ("food", "Diet measurement"),
    "EFO:0010757": ("food", "Response to diet"),
    "EFO:0002757": ("food", "High fat diet"),
    "EFO:0009371": ("food", "Ketogenic diet"),
    "EFO:0009372": ("food", "Response to ketogenic diet"),
    "EFO:0000483": ("exercise", "Exercise"),
    "EFO:0003940": ("exercise", "Physical activity"),
    "EFO:0008002": ("exercise", "Physical activity measurement"),
    "EFO:0002756": ("fasting", "Fasting"),
    "OBA:2040171": ("sleep", "Sleep duration"),
    "NCBITAXON:568703": ("probiotic", "Lactobacillus rhamnosus GG"),
    "NCBITAXON:103818": ("probiotic", "Lactobacillus kimchii"),
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


def ensure_columns(conn):
    mi_cols = {row[1] for row in conn.execute("PRAGMA table_info(microbe_intervention)")}
    if "source_db" not in mi_cols:
        conn.execute("ALTER TABLE microbe_intervention ADD COLUMN source_db TEXT")
    conn.commit()


def main():
    if not DUMP_PATH.exists():
        raise SystemExit(f"Expected {DUMP_PATH} -- same file already used by script 25.")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")
    ensure_columns(conn)

    target_ids = list(TARGET_TERMS.keys())
    placeholders = ",".join("?" for _ in target_ids)
    conn.execute(f"DELETE FROM microbe_intervention WHERE intervention_id IN ({placeholders})", target_ids)
    conn.commit()

    for tid, (itype, name) in TARGET_TERMS.items():
        conn.execute(
            "INSERT OR IGNORE INTO intervention (intervention_id, type, name, source) VALUES (?, ?, ?, ?)",
            (tid, itype, name, SOURCE_LABEL),
        )
    conn.commit()

    print("Loading and filtering BugSigDB dump ...")
    rows = load_filtered_rows()
    print(f"  {len(rows)} signatures pass quality filters\n")

    known_taxids = {row[0] for row in conn.execute("SELECT ncbi_taxid FROM organism")}

    pairs = defaultdict(lambda: {"increased": 0, "decreased": 0})
    for r in rows:
        ids = [x.strip() for x in r["EFO ID"].split(",")]
        taxids = [t.strip() for t in r["NCBI Taxonomy IDs"].split("|")]
        direction = r["Abundance in Group 1"]

        for cid in ids:
            if cid not in TARGET_TERMS:
                continue
            for taxid in taxids:
                if taxid in known_taxids:
                    pairs[(taxid, cid)][direction] += 1

    print(f"Inserting {len(pairs)} (organism, intervention) pairs ...")
    by_type = defaultdict(int)
    for (taxid, tid), counts in pairs.items():
        majority_dir = "increases" if counts["increased"] >= counts["decreased"] else "decreases"
        conn.execute(
            """
            INSERT INTO microbe_intervention
                (ncbi_taxid, intervention_id, effect_direction, evidence_level, source_db)
            VALUES (?, ?, ?, ?, ?)
            """,
            (taxid, tid, majority_dir, f"{counts['increased']+counts['decreased']} BugSigDB signature(s)", SOURCE_LABEL),
        )
        by_type[TARGET_TERMS[tid][0]] += 1

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()

    print(f"\nDone. {len(pairs)} microbe_intervention rows added, by type:")
    for itype, count in sorted(by_type.items()):
        print(f"  {itype}: {count}")
    if issues:
        print(f"\nWARNING: {len(issues)} foreign key issues:")
        for i in issues[:10]:
            print(f"  {i}")
    else:
        print("\nForeign key integrity check passed.")


if __name__ == "__main__":
    main()
