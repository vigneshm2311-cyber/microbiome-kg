import pandas as pd

df = pd.read_csv("/tmp/Genomes_labeled.csv", low_memory=False)

print("isolation_source_category value counts:")
print(df["isolation_source_category"].value_counts(dropna=True))

print("\nCross-tab: isolation_source_category x pathogenicity_label")
print(pd.crosstab(df["isolation_source_category"], df["pathogenicity_label"]))

print("\nHow many distinct species have BOTH a populated isolation_source_category AND match our likely organism table (species-level binomial)?")
clean_rows = df[df["isolation_source_category"].notna() & (df["isolation_source_category"] != "")]
print("Rows with populated category:", len(clean_rows))
print("Distinct species among those:", clean_rows["species"].nunique())
