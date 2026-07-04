"""
Diagnostic: how often are the fields we actually care about populated
across the real BacSPaD file, not just whether the columns exist.

Usage:
    pip install pandas
    python /tmp/check_bacspad_fill_rates.py
"""

import pandas as pd

df = pd.read_csv("/tmp/Genomes_labeled.csv", low_memory=False)
print(f"Total rows: {len(df)}\n")

fields_of_interest = [
    "pathogenicity_label", "body_sample_site", "biotic_relationship",
    "isolation_source", "isolation_source_category", "host_health",
    "host_status", "disease", "pathogenicity_details", "risk_group",
]

print(f"{'field':<26} {'non-empty':>10} {'%':>7}")
for col in fields_of_interest:
    non_empty = df[col].notna().sum() - (df[col] == "").sum()
    pct = 100 * non_empty / len(df)
    print(f"{col:<26} {non_empty:>10} {pct:>6.1f}%")

print("\nReal biotic_relationship values seen (with counts):")
print(df["biotic_relationship"].value_counts(dropna=True).head(10))

print("\nDistinct species in the file:", df["species"].nunique())
print("\nSample of populated body_sample_site values:")
print(df[df["body_sample_site"].notna() & (df["body_sample_site"] != "")]["body_sample_site"].head(10).tolist())

print("\nSample of populated isolation_source values:")
print(df[df["isolation_source"].notna() & (df["isolation_source"] != "")]["isolation_source"].head(10).tolist())
