"""
14_explore_uhgg_metadata.py

Diagnostic only -- not the real loader. I couldn't verify this
file's actual column structure myself: EBI's FTP site blocks
automated fetching (robots.txt) and isn't reachable from my
sandboxed environment at all. Multiple independent sources confirm
the file exists and is a single bulk TSV (289,232 genomes, 4,744
species representatives), but I don't have a confirmed column list
the way we got real ground truth for Disbiome and MONDO earlier.

This just prints the real header row and a couple of sample rows so
we design the actual loader against confirmed structure instead of
guessing column names.

Usage:
    pip install requests
    python scripts/14_explore_uhgg_metadata.py
"""

import requests

UHGG_METADATA_URL = (
    "https://ftp.ebi.ac.uk/pub/databases/metagenomics/mgnify_genomes/"
    "human-gut/v2.0.2/genomes-all_metadata.tsv"
)

resp = requests.get(UHGG_METADATA_URL, timeout=120, stream=True)
resp.raise_for_status()

lines = []
for i, line in enumerate(resp.iter_lines(decode_unicode=True)):
    lines.append(line)
    if i >= 5:
        break

header = lines[0].split("\t")
print(f"Column count: {len(header)}\n")
print("Columns:")
for col in header:
    print(f"  {col}")

print("\nFirst data row, field by field:")
first_row = lines[1].split("\t")
for col, val in zip(header, first_row):
    print(f"  {col}: {val}")
