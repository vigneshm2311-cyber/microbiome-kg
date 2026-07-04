"""
10_score_study_quality.py

Disbiome's /publication records (the same ones 09_map_studies_to_pmid.py
used) carry the methodological quality-assessment questionnaire
described in the original Disbiome paper: ~16 yes/no questions about
reporting practices (age of subjects reported? controls matched for
confounders? sample size justified? etc.).

This is deliberately kept as a SEPARATE, ADDITIONAL signal from
evidence_grade rather than folded into it. evidence_grade answers
"do multiple studies agree" (replication/contradiction) -- a
different question from "was this particular study well-reported."
Collapsing both into one letter would hide information, not surface
it more honestly. So this adds quality_score (0-100) and
quality_tier (high/medium/low) directly on the `study` table,
displayed alongside evidence_grade, not instead of it.

Scoring: each answered question contributes 1.0 for a clear "yes"/"y",
0.5 for a qualified yes (e.g. "yes, only in graphs" -- some effort,
not full transparency), 0.0 for "no"/"n", and is excluded from the
denominator if unanswered. quality_score is the percentage of that
total. Tier thresholds (high >=75, medium 40-75, low <40) are a
judgment call made here, not an external standard -- worth knowing
if you ever want to change them.

Usage:
    pip install requests
    python scripts/10_score_study_quality.py
"""

import re
import sqlite3
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"

DISBIOME_PUBLICATION_URL = "https://disbiome.ugent.be:8080/publication"

QUALITY_FIELDS = [
    "age_of_subjects_given",
    "geographical_origin_study_participants_given",
    "microbiome_influencing_factors_reported",
    "conflict_of_interest_statement_given",
    "research_hypothesis_stated",
    "sample_size_justified",
    "controls_matched_for_possible_confounding_factors",
    "type_of_control_group_reported",
    "inclusion_exclusion_criteria_stated",
    "sample_traceability_stated",
    "samples_blinded_at_analysis_of_outcome",
    "specific_test_statistics_reported",
    "measure_of_variance_reported",
    "numerical_microbiome_changes_given",
    "raw_date_reported_for_individual_subjects",
    "unit_of_analysis_specified",
]


def score_answer(value):
    if not value:
        return None
    value = value.strip().lower()
    if value in ("y", "yes"):
        return 1.0
    if value.startswith("yes"):
        return 0.5  # qualified yes, e.g. "yes, only in graphs"
    if value in ("n", "no") or value.startswith("no"):
        return 0.0
    return None


def compute_quality(pub: dict):
    scores = [s for s in (score_answer(pub.get(f)) for f in QUALITY_FIELDS) if s is not None]
    if not scores:
        return None, None
    pct = round(sum(scores) / len(scores) * 100, 1)
    tier = "high" if pct >= 75 else "medium" if pct >= 40 else "low"
    return pct, tier


def extract_pmid(pubmed_url):
    if not pubmed_url:
        return None
    match = re.search(r"/pubmed/(\d+)", pubmed_url)
    return match.group(1) if match else None


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(study)")}
    for col in ("quality_score", "quality_tier"):
        if col not in cols:
            conn.execute(f"ALTER TABLE study ADD COLUMN {col} TEXT")
    conn.commit()


def main():
    print("Fetching publication metadata from Disbiome ...")
    resp = requests.get(DISBIOME_PUBLICATION_URL, timeout=60)
    resp.raise_for_status()
    publications = resp.json()
    print(f"  {len(publications)} publication records received")

    conn = sqlite3.connect(DB_PATH)
    ensure_columns(conn)

    scored, no_study_row, no_answers = 0, 0, 0
    tier_counts = {"high": 0, "medium": 0, "low": 0}

    for pub in publications:
        pct, tier = compute_quality(pub)
        if pct is None:
            no_answers += 1
            continue

        pmid = extract_pmid(pub.get("pubmed_url"))
        disbiome_placeholder = f"DISBIOME_PUB:{pub['publication_id']}"

        row = None
        if pmid:
            row = conn.execute(
                "SELECT study_id FROM study WHERE study_id = ?", (f"PMID:{pmid}",)
            ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT study_id FROM study WHERE study_id = ?", (disbiome_placeholder,)
            ).fetchone()

        if not row:
            no_study_row += 1
            continue

        conn.execute(
            "UPDATE study SET quality_score = ?, quality_tier = ? WHERE study_id = ?",
            (pct, tier, row[0]),
        )
        tier_counts[tier] += 1
        scored += 1

    conn.commit()
    conn.close()

    print(
        f"\nDone. {scored} studies scored ({tier_counts['high']} high, "
        f"{tier_counts['medium']} medium, {tier_counts['low']} low quality-tier), "
        f"{no_answers} publications had no answerable quality fields, "
        f"{no_study_row} publications don't correspond to a study row in our DB "
        f"(no disease association currently loaded from them)."
    )


if __name__ == "__main__":
    main()
