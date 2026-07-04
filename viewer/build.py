"""
viewer/build.py

Generates viewer/index.html -- a single, self-contained, static HTML
file with all data embedded inline. No server, no Streamlit reruns:
every sort, filter, and row click happens instantly in the browser.

Four tabs: Organisms, Diseases, Interventions, Phenotypes.
(Interventions was previously labeled "Drugs" -- renamed once the
table started holding food/exercise/sleep/fasting/probiotic rows too,
not just drugs. Phenotypes is new: the first time microbe_phenotype
data, populated via BugSigDB, has been surfaced in the viewer at all.)

Re-run this any time the database changes, then just open
viewer/index.html in a browser (double-click works, or "open with"
your browser of choice -- no streamlit run, no server).

Usage:
    python viewer/build.py
"""

import json
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "db" / "microbiome.db"
SUMMARY_PATH = Path(__file__).resolve().parent / "data-summary.json"
DETAIL_PATH = Path(__file__).resolve().parent / "data-organisms.json"


def build_data(conn):
    disease_counts = dict(conn.execute(
        "SELECT ncbi_taxid, COUNT(*) FROM microbe_disease GROUP BY ncbi_taxid"
    ).fetchall())
    intervention_counts = dict(conn.execute(
        "SELECT ncbi_taxid, COUNT(*) FROM microbe_intervention GROUP BY ncbi_taxid"
    ).fetchall())
    phenotype_counts = dict(conn.execute(
        "SELECT ncbi_taxid, COUNT(*) FROM microbe_phenotype GROUP BY ncbi_taxid"
    ).fetchall())

    bodysite_names = dict(conn.execute("SELECT uberon_id, name FROM body_site").fetchall())

    org_disease_detail = {}
    for taxid, dname, mondo_id, direction, grade, repl, contra, study_type, sample_size, sample_uberon_id, lda_score, seq_region, det_method, pop_age, pop_sex, geography in conn.execute(
        """
        SELECT md.ncbi_taxid, d.name, d.mondo_id, md.direction, md.evidence_grade,
               md.replication_count, md.contradiction_count, s.study_type, s.sample_size,
               md.sample_uberon_id, md.lda_score, md.sequencing_region, s.detection_method,
               s.population_age, s.population_sex, s.geography
        FROM microbe_disease md
        JOIN disease d ON d.mondo_id = md.mondo_id
        LEFT JOIN study s ON s.study_id = md.study_id
        """
    ).fetchall():
        org_disease_detail.setdefault(taxid, []).append({
            "disease": dname, "mondoId": mondo_id, "isMapped": mondo_id.startswith("MONDO"),
            "direction": direction, "grade": grade, "replications": repl,
            "contradictions": contra, "studyType": study_type, "sampleSize": sample_size,
            "sampleUberonId": sample_uberon_id,
            "sampleSite": bodysite_names.get(sample_uberon_id) if sample_uberon_id else None,
            "ldaScore": lda_score,
            "sequencingRegion": seq_region, "detectionMethod": det_method,
            "populationAge": pop_age, "populationSex": pop_sex, "geography": geography,
        })

    org_intervention_detail = {}
    for taxid, iname, direction, pvalue, itype, evidence_level in conn.execute(
        """
        SELECT mi.ncbi_taxid, i.name, mi.effect_direction, mi.adjusted_pvalue, i.type, mi.evidence_level
        FROM microbe_intervention mi JOIN intervention i ON i.intervention_id = mi.intervention_id
        """
    ).fetchall():
        org_intervention_detail.setdefault(taxid, []).append({
            "name": iname, "direction": direction, "pvalue": pvalue,
            "type": itype, "evidenceLevel": evidence_level,
        })

    org_phenotype_detail = {}
    for taxid, pname, direction, study_count in conn.execute(
        """
        SELECT mp.ncbi_taxid, p.name, mp.direction, mp.study_count
        FROM microbe_phenotype mp JOIN phenotype p ON p.hpo_id = mp.hpo_id
        """
    ).fetchall():
        org_phenotype_detail.setdefault(taxid, []).append({
            "name": pname, "direction": direction, "studyCount": study_count,
        })

    org_bodysite_detail = {}
    for taxid, site, status, prevalence, flora, clinical_n, pathogenic_n, frac, study_n, source in conn.execute(
        """
        SELECT mb.ncbi_taxid, b.name, mb.cohort_health_status, mb.prevalence, mb.flora_classification,
               mb.clinical_isolate_count, mb.pathogenic_isolate_count, mb.pathogenic_fraction,
               mb.study_count, mb.source_db
        FROM microbe_bodysite mb JOIN body_site b ON b.uberon_id = mb.uberon_id
        """
    ).fetchall():
        org_bodysite_detail.setdefault(taxid, []).append({
            "site": site, "status": status, "prevalence": prevalence, "flora": flora,
            "clinicalIsolateCount": clinical_n, "pathogenicIsolateCount": pathogenic_n,
            "pathogenicFraction": frac, "studyCount": study_n, "source": source,
        })

    specialty_counts = dict(conn.execute(
        "SELECT ncbi_taxid, COUNT(*) FROM organism_specialty_gene GROUP BY ncbi_taxid"
    ).fetchall())

    org_specialty_detail = {}
    for taxid, property_, source, gene, product in conn.execute(
        """
        SELECT ncbi_taxid, property, property_source, gene, product
        FROM organism_specialty_gene
        ORDER BY ncbi_taxid, property, property_source
        """
    ).fetchall():
        org_specialty_detail.setdefault(taxid, []).append({
            "property": property_, "source": source,
            "gene": gene, "product": product,
        })

    org_synonyms = {}
    for taxid, syn in conn.execute("SELECT ncbi_taxid, synonym_name FROM organism_synonym").fetchall():
        org_synonyms.setdefault(taxid, []).append(syn)

    genome_counts = dict(conn.execute(
        "SELECT ncbi_taxid, COUNT(*) FROM genome GROUP BY ncbi_taxid"
    ).fetchall())

    organisms = []
    for taxid, name, rank, q_tier, completeness, contamination, genome_id, accession, is_mag, genome_source, lineage, division in conn.execute(
        """
        SELECT o.ncbi_taxid, o.name, o.rank, g.quality_tier, g.completeness_pct, g.contamination_pct,
               g.genome_id, g.accession, g.is_mag, g.source_db, o.lineage, o.division
        FROM organism o LEFT JOIN genome g ON g.genome_id = o.representative_genome_id
        """
    ).fetchall():
        organisms.append({
            "taxid": taxid, "name": name, "rank": rank,
            "lineage": lineage, "division": division,
            "diseaseCount": disease_counts.get(taxid, 0),
            "interventionCount": intervention_counts.get(taxid, 0),
            "phenotypeCount": phenotype_counts.get(taxid, 0),
            "genomeQuality": q_tier, "completeness": completeness,
            "contamination": contamination, "genomeId": genome_id, "genomeAccession": accession,
            "isMag": bool(is_mag) if is_mag is not None else None, "genomeSource": genome_source,
            "totalGenomesAvailable": genome_counts.get(taxid, 0),
            "specialtyGeneCount": specialty_counts.get(taxid, 0),
            "specialtyGenes": org_specialty_detail.get(taxid, []),
            "diseases": org_disease_detail.get(taxid, []),
            "interventions": org_intervention_detail.get(taxid, []),
            "phenotypes": org_phenotype_detail.get(taxid, []),
            "bodysites": org_bodysite_detail.get(taxid, []),
            "synonyms": org_synonyms.get(taxid, []),
        })

    disease_organism_detail = {}
    for mondo_id, oname, direction, grade, repl, contra, study_type in conn.execute(
        """
        SELECT md.mondo_id, o.name, md.direction, md.evidence_grade, md.replication_count,
               md.contradiction_count, s.study_type
        FROM microbe_disease md
        JOIN organism o ON o.ncbi_taxid = md.ncbi_taxid
        LEFT JOIN study s ON s.study_id = md.study_id
        """
    ).fetchall():
        disease_organism_detail.setdefault(mondo_id, []).append({
            "organism": oname, "direction": direction, "grade": grade,
            "replications": repl, "contradictions": contra, "studyType": study_type,
        })

    diseases = []
    for mondo_id, name, organism_count, grade_a, grade_d in conn.execute(
        """
        SELECT d.mondo_id, d.name, COUNT(DISTINCT md.ncbi_taxid),
               SUM(CASE WHEN md.evidence_grade='A' THEN 1 ELSE 0 END),
               SUM(CASE WHEN md.evidence_grade='D' THEN 1 ELSE 0 END)
        FROM disease d JOIN microbe_disease md ON md.mondo_id = d.mondo_id
        GROUP BY d.mondo_id, d.name
        """
    ).fetchall():
        diseases.append({
            "mondoId": mondo_id, "name": name, "isMapped": mondo_id.startswith("MONDO"),
            "organismCount": organism_count, "gradeA": grade_a or 0, "gradeD": grade_d or 0,
            "organisms": disease_organism_detail.get(mondo_id, []),
        })

    intervention_organism_detail = {}
    for iid, oname, direction, pvalue, evidence_level in conn.execute(
        """
        SELECT mi.intervention_id, o.name, mi.effect_direction, mi.adjusted_pvalue, mi.evidence_level
        FROM microbe_intervention mi JOIN organism o ON o.ncbi_taxid = mi.ncbi_taxid
        """
    ).fetchall():
        intervention_organism_detail.setdefault(iid, []).append({
            "organism": oname, "direction": direction, "pvalue": pvalue, "evidenceLevel": evidence_level,
        })

    interventions = []
    for iid, name, itype, organism_count, best_pvalue in conn.execute(
        """
        SELECT i.intervention_id, i.name, i.type, COUNT(DISTINCT mi.ncbi_taxid), MIN(mi.adjusted_pvalue)
        FROM intervention i JOIN microbe_intervention mi ON i.intervention_id = mi.intervention_id
        GROUP BY i.intervention_id, i.name, i.type
        """
    ).fetchall():
        interventions.append({
            "id": iid, "name": name, "type": itype, "organismCount": organism_count,
            "bestPvalue": best_pvalue, "organisms": intervention_organism_detail.get(iid, []),
        })

    phenotype_organism_detail = {}
    for hpo_id, oname, direction, study_count in conn.execute(
        """
        SELECT mp.hpo_id, o.name, mp.direction, mp.study_count
        FROM microbe_phenotype mp JOIN organism o ON o.ncbi_taxid = mp.ncbi_taxid
        """
    ).fetchall():
        phenotype_organism_detail.setdefault(hpo_id, []).append({
            "organism": oname, "direction": direction, "studyCount": study_count,
        })

    phenotypes = []
    for hpo_id, name, organism_count in conn.execute(
        """
        SELECT p.hpo_id, p.name, COUNT(DISTINCT mp.ncbi_taxid)
        FROM phenotype p JOIN microbe_phenotype mp ON mp.hpo_id = p.hpo_id
        GROUP BY p.hpo_id, p.name
        """
    ).fetchall():
        phenotypes.append({
            "hpoId": hpo_id, "name": name, "organismCount": organism_count,
            "organisms": phenotype_organism_detail.get(hpo_id, []),
        })

    return {"organisms": organisms, "diseases": diseases, "interventions": interventions, "phenotypes": phenotypes}


def main():
    conn = sqlite3.connect(DB_PATH)
    data = build_data(conn)
    conn.close()

    print(
        f"Exported {len(data['organisms'])} organisms, {len(data['diseases'])} diseases, "
        f"{len(data['interventions'])} interventions, {len(data['phenotypes'])} phenotypes."
    )

    # Split into two files to keep index.html under GitHub's 100MB limit:
    # data-summary.json  -- table-level fields only (~3MB), embedded in index.html
    # data-organisms.json -- full organism detail with nested arrays (~120MB),
    #                        fetched on first click and cached in memory

    ORGANISM_DETAIL_KEYS = {"diseases", "interventions", "phenotypes", "bodysites",
                            "synonyms", "specialtyGenes"}

    summary_orgs = [{k: v for k, v in o.items() if k not in ORGANISM_DETAIL_KEYS}
                    for o in data["organisms"]]
    detail_orgs = {o["taxid"]: {k: v for k, v in o.items() if k in ORGANISM_DETAIL_KEYS}
                   for o in data["organisms"]}

    summary_data = {
        "organisms": summary_orgs,
        "diseases": data["diseases"],
        "interventions": data["interventions"],
        "phenotypes": data["phenotypes"],
    }

    # Write the large detail file separately
    output_dir = Path(__file__).resolve().parent
    DETAIL_PATH.write_text(json.dumps(detail_orgs))
    print(f"Wrote {DETAIL_PATH} ({DETAIL_PATH.stat().st_size / 1024 / 1024:.1f} MB) -- organism detail")

    # Embed only the summary into index.html
    template_path = output_dir / "template.html"
    html = template_path.read_text()
    html = html.replace("__DATA_JSON__", json.dumps(summary_data))
    output_path = output_dir / "index.html"
    output_path.write_text(html)
    print(f"Wrote {output_path} ({output_path.stat().st_size / 1024:.0f} KB) -- main viewer")
    print("Open index.html in a browser. Keep data-organisms.json in the same folder.")


if __name__ == "__main__":
    main()
