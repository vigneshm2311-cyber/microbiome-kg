"""
21_reclassify_study_types.py

Fixes a known, previously-flagged inaccuracy: every Disbiome-derived
study was hardcoded to study_type='observational' in script 04, with
a note at the time that this was probably wrong for at least some of
the underlying papers. Disbiome's own paper describes itself as
curating data from "case-control studies" (a real form of
observational study), so 'observational' being the dominant category
isn't actually wrong -- it was just applied with false uniformity.
This finds the real minority that are something else, using titles
we already have stored (script 09's real PMID migration) -- no live
API call needed at all.

Keyword heuristics, checked in this priority order:
  - rct: randomized/randomised controlled/clinical trial language
  - gnotobiotic_mouse: germ-free/gnotobiotic/axenic mice
  - in_vitro: explicit in vitro / bioreactor / chemostat language
  - otherwise left as observational (the honest default, not a guess)

This is a heuristic on title text, not a perfect classifier -- some
real RCTs or animal studies won't mention it in the title and will
stay (correctly humble) as observational. Studies with no title
(still-unmapped placeholders) are left untouched entirely, since
there's no signal to work from.

Usage:
    python scripts/21_reclassify_study_types.py
"""

import re
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"

PATTERNS = [
    ("rct", re.compile(r"randomi[sz]ed.{0,30}(controlled|clinical)?\s*trial|double-blind|placebo-controlled|\bRCT\b", re.I)),
    ("gnotobiotic_mouse", re.compile(r"gnotobiotic|germ-free mice|germ free mice|\bGF mice\b|axenic mice|humanized mice", re.I)),
    ("in_vitro", re.compile(r"\bin vitro\b|bioreactor|chemostat|fermentation model|simulated (gut|intestinal|colon)", re.I)),
]


def classify(title: str) -> str:
    for label, pattern in PATTERNS:
        if pattern.search(title):
            return label
    return "observational"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    rows = conn.execute(
        "SELECT study_id, title, study_type FROM study WHERE title IS NOT NULL AND title != ''"
    ).fetchall()

    changes = {"rct": 0, "gnotobiotic_mouse": 0, "in_vitro": 0}
    examples = {"rct": [], "gnotobiotic_mouse": [], "in_vitro": []}
    unchanged = 0

    for study_id, title, current_type in rows:
        new_type = classify(title)
        if new_type == "observational":
            unchanged += 1
            continue
        conn.execute("UPDATE study SET study_type = ? WHERE study_id = ?", (new_type, study_id))
        changes[new_type] += 1
        if len(examples[new_type]) < 3:
            examples[new_type].append(title)

    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    issues = conn.execute("PRAGMA foreign_key_check").fetchall()

    no_title = conn.execute(
        "SELECT COUNT(*) FROM study WHERE title IS NULL OR title = ''"
    ).fetchone()[0]
    conn.close()

    print(f"Scanned {len(rows)} studies with a real title.\n")
    for label, count in changes.items():
        print(f"  {label}: {count} reclassified")
        for ex in examples[label]:
            print(f"    e.g. \"{ex[:90]}{'...' if len(ex) > 90 else ''}\"")
    print(f"\n  observational (unchanged, no matching keyword found): {unchanged}")
    print(f"\n{no_title} studies have no title at all -- left untouched, no signal to classify from.")
    print("\nForeign key check:", "passed" if not issues else issues)


if __name__ == "__main__":
    main()
