# Microbiome knowledge graph

A structured, evidence-graded database connecting microbes to diseases, body
sites, metabolites, and interventions — built incrementally, source by
source, with every claim tagged by evidence quality rather than presented as
flat fact.

See `microbiome_kg_schema.md` (the design doc) for the full rationale behind
every table and field. This README covers how to actually run the project.

## Project structure
microbiome_kg/

├── data/

│   ├── raw/          # untouched downloads from source databases — never hand-edit

│   └── processed/    # cleaned/normalized output of our scripts — what actually loads into the DB

├── db/

│   ├── schema.sql     # the DDL — table definitions, constraints, indexes

│   └── microbiome.db  # the actual SQLite database (generated, not hand-edited)

├── scripts/           # numbered, run in order

├── viewer/            # the Streamlit app

├── requirements.txt

└── README.md

## Setup

```bash
python3 -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Build status

| Step | Script | Status |
|---|---|---|
| 1 | `01_create_db.py` | DONE - creates all tables from schema.sql |
| 2 | `02_smoke_test.py` | DONE - inserts one realistic example end-to-end and reads it back via joins |
| 3 | `03_load_taxonomy.py` | DONE - 15 seed organisms loaded from live NCBI Taxonomy, with real synonym/reclassification history captured |
| 4 | `04_load_disease_associations.py` | DONE - evidence-graded disease associations loaded from live Disbiome data, aggregated with replication/contradiction counts |
| 5 | `05_expand_organisms_from_disbiome.py` | DONE - expands organism table using Disbiome's most-frequent unmatched names, prioritized by real impact |
| 6 | `06_map_diseases_to_mondo.py` | DONE - migrates MEDDRA placeholder disease IDs to real MONDO IDs where a mapping exists |
| 7 | `07_fuzzy_match_diseases_to_mondo.py` | DONE - backup name-matching pass for diseases the exact-code crosswalk missed |
| 8 | `08_explore_gmrepo_response.py` | ABANDONED - diagnostic only; GMrepo's documented API returns HTTP 500 on every endpoint tested, including parameter-free calls. Likely broken/moved post-v3, not a request-formatting issue on our end. |
| 9 | `09_map_studies_to_pmid.py` | DONE - migrates DISBIOME_PUB placeholder study IDs to real PMIDs (780 of 913 migrated; 133 lack a PMID in Disbiome's own record) |
| 10 | `10_score_study_quality.py` | DONE - scores each study's methodological reporting quality (high/medium/low) from Disbiome's own questionnaire data, kept as a separate signal from evidence_grade and surfaced in the viewer |
| 16 | `16_load_drug_interventions.py` | DONE - real drug-organism inhibition data from Maier et al. 2018 (Nature), 5,396 hits across 1,200 drugs and 38 organisms, FDR-adjusted p-values preserved |
| 18-19 | `19_load_bacspad_pathogenicity.py` | DONE -- real body-site + pathogenicity data from BacSPaD (BV-BRC clinical genomes, Zenodo 13235447): 356 (organism, site) pairs across 13 anatomical categories. Two fields that looked like an exact match on paper (biotic_relationship, body_sample_site) turned out to be populated on <2% of rows and partially mislabeled -- not used. Built instead on pathogenicity_label and isolation_source_category (~99% populated each). Column names (clinical_isolate_count, pathogenic_isolate_count, pathogenic_fraction) deliberately describe what's measured -- fraction of *clinically sequenced* isolates from a site that were pathogenic -- not general healthy-population ecology, since BV-BRC's clinical-genomics sourcing skews every site toward over-representing infection. No Uberon IDs invented for the 13 body-site categories; stored as BacSPaD's own category text with uberon_id left NULL, same honest-gap pattern as unmapped diseases. |
| - | `viewer/app.py` | DONE - Streamlit viewer: organism search, disease/body-site cards with evidence-grade + study-quality badges, interactive network graph |

**Layer 6 (interventions) is now real, not empty.** First new layer populated since the project committed to "finish layer 5 properly, then expand" -- sourced from a single peer-reviewed paper's supplementary dataset rather than a live API, following the same "real bulk file" pattern that worked for Disbiome/MONDO/UHGG. `intervention` and `microbe_intervention` were empty since the schema was first designed; both now have real, verified data.

**Note:** the original smoke-test fixture rows (fake `PMID:12345678` study, fake `MGnify` body-site row) were deleted once real Disbiome/NCBI data fully superseded them. The database now contains no fabricated data -- everything traces to a real source.

### Known open items (real gaps, not yet addressed)
- MONDO crosswalk is now partial, not absent: 45 of 309 diseases (~15%) migrated to real MONDO IDs via `06_map_diseases_to_mondo.py`; the remaining 264 stay as `MEDDRA:<id>` placeholders because MONDO's curated MedDRA crosswalk (~1,460 codes) doesn't cover them. This is a real coverage limit in the field's mapping work, not a bug in our pipeline.
- Study IDs from Disbiome are `DISBIOME_PUB:<id>` (Disbiome's internal ID), not real PMIDs.
- Organism matching against Disbiome is exact-name-only. Many genus/species pairs now resolve correctly, but the long tail of ~1,400 still-unmatched Disbiome organism names remains.
- `gene_protein`, `pathway`, and `host_effect` tables from the design doc are not yet in `schema.sql` -- deliberately deferred as Tier-3/sparse.
- `microbe_bodysite` (the normal-flora data) is still essentially empty outside the one smoke-test row -- no real composition/abundance data source (MGnify, GMrepo) has been integrated yet.

## Running everything in order

```bash
cd microbiome_kg
python3 scripts/01_create_db.py
python3 scripts/02_smoke_test.py
python3 scripts/03_load_taxonomy.py
python3 scripts/04_load_disease_associations.py
streamlit run viewer/app.py
```

## Inspecting the database directly

```bash
sqlite3 db/microbiome.db
sqlite> .tables
sqlite> SELECT * FROM organism;
sqlite> .quit
```

| 28 | `28_load_nondrug_interventions.py` | DONE -- populates the 5 intervention types empty since the schema was designed (food, probiotic, exercise, sleep, fasting). Sourced from the same quality-filtered BugSigDB signature set as script 25, picking out real experimental-factor terms that were correctly excluded from the disease table during that load. 439 microbe_intervention rows added: food 308, exercise 56, sleep 33, probiotic 26, fasting 16. The probiotic terms are notable structurally: two real strains (Lactobacillus rhamnosus GG, Lactobacillus kimchii) used AS the intervention itself, tagged via their own real NCBI taxid rather than an EFO behavioral-factor term like the other four types.

| 29 | `29_extract_sample_sizes.py` | DONE -- sample_size went from 0% to 73% populated (2,135 of 2,918 studies). Two-step approach: (1) registered 2,040 BugSigDB study rows using BugSigDB's own clean Group 0 + Group 1 sample size fields, no extraction needed; (2) for the remaining Disbiome-sourced studies, extracted 188 sample sizes from real PubMed abstract text via narrow regex patterns (number directly attached to a participant-counting phrase, allowing up to 2 descriptive words in between). 193 abstracts had multiple disagreeing candidate numbers and were correctly left NULL rather than guessed. Caught and excluded one genuine upstream data-entry error in BugSigDB's own source (a row with PMID=12345678 -- actually a 1990s UN policy document, not a microbiome paper -- which coincidentally matches our own smoke-test fixture's fake PMID and would have falsely tripped the test-contamination integrity check).

- MicroPhenoDB was investigated and deliberately NOT integrated. Its own paper names exactly four source ingredients -- IDSA guideline, NCIT, HMDAD, and Disbiome -- mixed together with no visible per-row attribution to tell which underlying source a given association came from. Since Disbiome is already our primary, most deeply-used source, there's no reliable way to distinguish genuine independent confirmation from a restatement of evidence we already have, which would risk silently inflating replication_count -- the exact failure mode this project has consistently guarded against (see the BugSigDB/Disbiome overlap handling). Compounding that: both hosting URLs are Chinese academic servers (liwzlab.cn, sysu.edu.cn) with no confirmed GitHub/Zenodo-style bulk export, and disease classification uses EFO, meaning a build would require redoing the same EFO-vs-MONDO classification work already completed for BugSigDB, on a smaller dataset (5,677 total associations) with materially worse provenance. Given low realistic value against real data-integrity risk, this is a deliberate 'no,' not a deferred task.

## Strain-level resolution — documented field-level gap (investigated 2026-06)

Item 7 on the roadmap was investigated in depth and deliberately not built.
The conclusion: strain-level resolution via BV-BRC virulence gene prevalence
is the wrong approach for a gut microbiome knowledge graph.

What was found:
- BV-BRC has excellent curated virulence gene data (VFDB/Victors) for classic
  respiratory and systemic pathogens (S. pneumoniae, P. aeruginosa, M. tuberculosis,
  Salmonella, Brucella) -- but these are not the gut microbiome organisms
  researchers using this database are asking about.
- The organisms researchers actually care about (F. prausnitzii, Akkermansia,
  Roseburia, Bifidobacterium) have no meaningful strain-differentiating virulence
  gene data in BV-BRC -- correctly, because they are commensals, not pathogens.
- E. coli's virulence gene curation in BV-BRC at the species level (taxid 562)
  returns housekeeping genes (gyrA, motA, dsbA) present in virtually all strains,
  not the pathotype-defining genes (stx1/stx2 for O157:H7) that would enable
  strain resolution.
- BV-BRC genome counts confirm scale is intractable for full enumeration:
  E. coli has 97,545 genomes, F. prausnitzii has 1,604, Akkermansia has 1,984.

Root cause: strain-level resolution for gut commensals requires clinical
metagenomics studies sequenced to strain level using long-read technology
(PacBio, Oxford Nanopore) -- a small, technically demanding, rapidly evolving
literature without a clean bulk database equivalent of BV-BRC for the gut
microbiome specifically.

Resources to monitor:
- GMrepo v2 (was returning HTTP 500 when checked; has strain-level abundance data)
- MGnify pangenome analysis (EBI building pangenome-level accessory gene analysis
  for UHGG strains -- the right unit of analysis for gut commensals)
- curatedMetagenomicData (Bioconductor) -- strain-resolved metagenomics datasets
  accumulating rapidly as long-read sequencing costs fall

This is a genuine field-level limitation, not a pipeline gap. Revisit in 12-18 months.
