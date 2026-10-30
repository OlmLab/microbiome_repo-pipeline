"""extend_sandpiper.py — Sandpiper community profiles for the catalog samples that the existing tables do not cover
(R2026.31: samples of the projects admitted by the scope classification, every body site). Same bulk snapshot, same rules
as the existing tables (data/inputs/gut/sandpiper/filter_gut.py + build_gut_tables.py): runs of the new samples are kept
from the bulk file, per-sample coverage is the sum over the sample's profiled runs, genus / species tables are normalised by
root coverage with the unassigned remainder. QC flags that need the Sandpiper per-run metadata (low complexity, read-fraction
warning, organism labels, ecological prediction) are left empty for the new samples; low depth, partial, synthetic, RNA and
no-genus flags are computed as before.

usage: python scripts/extend_sandpiper.py --bulk sandpiper2.0.0.gtdb.csv.gz --old-dir data/inputs/gut/sandpiper \
         --catalog-dir BUILD_DIR(catalog_runs / catalog_sample_metadata_wide) --work WORK --out data/inputs/catalog/sandpiper
Output: OUT/gut_sandpiper_* (the old tables plus the new samples, same columns) — the Makefile copies them to catalog_sandpiper_*.
"""
import argparse, json, os, shutil, subprocess, sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulk", required=True); ap.add_argument("--old-dir", required=True); ap.add_argument("--catalog-dir", required=True)
    ap.add_argument("--work", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(f"{a.work}/work/sp", exist_ok=True); os.makedirs(a.out, exist_ok=True)
    old_sum = pd.read_parquet(f"{a.old_dir}/gut_sandpiper_sample_summary.parquet")
    runs = pd.read_parquet(f"{a.catalog_dir}/catalog_runs.parquet", columns=["run_accession", "study_accession", "sample_key", "nucleic_acid", "library_source", "library_strategy"])
    w = pd.read_parquet(f"{a.catalog_dir}/catalog_sample_metadata_wide.parquet", columns=["sample_key", "study_accession", "age_category", "body_site_class", "infant_scope", "release_retired"])
    w = w[w.release_retired.isna()]
    new_keys = set(w.sample_key) - set(old_sum.sample_key)
    r = runs[runs.sample_key.isin(new_keys) & runs.nucleic_acid.eq("dna")].drop_duplicates("run_accession")
    r = r.assign(sandpiper_profiled=True)
    r.to_parquet(f"{a.work}/new_runs.parquet", index=False)
    r[["run_accession", "sample_key", "study_accession"]].to_parquet(f"{a.work}/work/sp/gut_runs_profiled.parquet", index=False)
    print(json.dumps({"new_samples": len(new_keys), "new_sample_dna_runs": len(r)}), flush=True)
    gs_dir = os.path.join(HERE, "..", "data", "inputs", "gut", "sandpiper")
    subprocess.run([sys.executable, os.path.abspath(f"{gs_dir}/filter_gut.py"), os.path.abspath(a.bulk), os.path.abspath(f"{a.work}/new_runs.parquet")], cwd=a.work, check=True)
    subprocess.run([sys.executable, os.path.abspath(f"{gs_dir}/build_gut_tables.py")], cwd=a.work, check=True)
    W = f"{a.work}/work/out"
    core = pd.read_parquet(f"{W}/_summ_core.parquet")
    prof = pd.read_parquet(f"{a.work}/work/sp/raw_parts", columns=["run"]).run.unique()
    n_prof = r[r.run_accession.isin(set(prof))].groupby("sample_key").size()
    n_tot = r.groupby("sample_key").size()
    syn = r[r.library_source.astype(str).str.upper().eq("SYNTHETIC")].sample_key.unique()
    ns = core.merge(w[["sample_key", "study_accession", "age_category", "body_site_class", "infant_scope"]], on="sample_key", how="left")
    ns["n_runs_profiled"] = ns.sample_key.map(n_prof).fillna(0).astype(int)
    ns["n_runs_total"] = ns.sample_key.map(n_tot).fillna(0).astype(int)
    ns["qc_partial"] = ns.n_runs_profiled < ns.n_runs_total
    ns["qc_low_depth"] = ns.root_coverage_sum < 2
    ns["qc_no_genus_assigned"] = ns.richness_genus.fillna(0) == 0
    ns["qc_synthetic"] = ns.sample_key.isin(set(syn))
    ns["qc_rna"] = False
    for c in ("qc_low_complexity", "qc_readfraction_warning", "qc_non_metagenome", "qc_non_metagenome_strict", "qc_predicted_ecological"):
        ns[c] = pd.NA
    fl = ["qc_low_depth", "qc_partial", "qc_synthetic", "qc_no_genus_assigned"]
    ns["qc_flags"] = ns[fl].apply(lambda x: ";".join(c[3:] for c, v in x.items() if bool(v)), axis=1)
    ns["qc_any_flag"] = ns[fl].any(axis=1)
    ns["taxonomy_db"], ns["taxonomy_version"], ns["sandpiper_version"], ns["zenodo_record"] = "GTDB", "R232", "2.0.0", "20419175"
    ns["sandpiper_url"] = None
    ns = ns.reindex(columns=old_sum.columns)
    summ = pd.concat([old_sum, ns], ignore_index=True)
    summ.to_parquet(f"{a.out}/gut_sandpiper_sample_summary.parquet", index=False)
    for rank in ("genus", "species"):
        old = pd.read_parquet(f"{a.old_dir}/gut_sandpiper_sample_{rank}.parquet")
        new = pd.read_parquet(f"{W}/gut_sandpiper_sample_{rank}.parquet").reindex(columns=old.columns)
        pd.concat([old, new[~new.sample_key.isin(set(old.sample_key))]], ignore_index=True).to_parquet(f"{a.out}/gut_sandpiper_sample_{rank}.parquet", index=False, compression="zstd")
    for f in ("gut_sandpiper_pca_scores.parquet", "gut_sandpiper_pca_loadings.parquet", "gut_sandpiper_pca_variance.csv", "GUT_SANDPIPER_REPORT.md"):
        shutil.copy(f"{a.old_dir}/{f}", f"{a.out}/{f}")
    old_cov = pd.read_csv(f"{a.old_dir}/gut_sandpiper_study_coverage.csv")
    nw = w[~w.study_accession.isin(set(old_cov.study_accession))]
    nc = nw.groupby("study_accession").size().rename("n_samples").reset_index()
    nc["n_samples_profiled"] = nc.study_accession.map(ns.groupby("study_accession").sample_key.nunique()).fillna(0).astype(int)
    rr_ = r[r.study_accession.isin(set(nc.study_accession))]
    nc["n_runs"] = nc.study_accession.map(runs[runs.study_accession.isin(set(nc.study_accession))].groupby("study_accession").size()).fillna(0).astype(int)
    nc["n_runs_profiled"] = nc.study_accession.map(rr_[rr_.run_accession.isin(set(prof))].groupby("study_accession").size()).fillna(0).astype(int)
    nc["frac_samples_profiled"] = (nc.n_samples_profiled / nc.n_samples).round(4)
    pd.concat([old_cov, nc.reindex(columns=old_cov.columns)], ignore_index=True).to_csv(f"{a.out}/gut_sandpiper_study_coverage.csv", index=False)
    with open(f"{a.out}/GUT_SANDPIPER_REPORT.md", "a", encoding="utf-8") as fh:
        fh.write(f"\n\n## R2026.31 extension\n\n{len(ns):,} further catalog samples (projects admitted by the scope classification, every body site) were profiled from the same snapshot with the same rules; QC flags that need Sandpiper's per-run metadata are empty for them.\n")
    print(json.dumps({"new_samples_profiled": len(ns), "summary_rows": len(summ), "projects_with_profiles": int(summ.study_accession.nunique())}))


if __name__ == "__main__":
    main()
