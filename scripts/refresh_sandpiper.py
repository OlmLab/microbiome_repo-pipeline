#!/usr/bin/env python
"""refresh_sandpiper.py — rebuild every catalog Sandpiper table from one bulk snapshot (R2026.36).

Replaces the incremental path (extend_sandpiper.py appended new samples to tables built for package 1.11.0). Here all
tables are rebuilt in one pass for the CURRENT catalog samples only (catalog_sample_metadata_wide rows with
release_retired null), with the same rules as before:
  * runs: every DNA run (nucleic_acid = dna) of a current sample in catalog_runs.parquet is a candidate; a run is profiled
    when the bulk file has a profile for it (filter_gut.py); n_runs_total counts the sample's DNA runs;
  * per-sample coverage = sum over the sample's profiled runs; genus/species tables normalised by root coverage with the
    unassigned remainder (build_gut_tables.py);
  * QC flags (flagged, never dropped) from Sandpiper's per-run metadata (per_acc_summary) for EVERY sample — previously
    left empty for the 83,544 samples added in R2026.31: low_depth (root < 2), low_complexity (any run), readfraction_warning
    (any run), non_metagenome (strict rule minus 'Homo sapiens'), non_metagenome_strict, synthetic, rna (strict),
    predicted_ecological (host_or_not = ecological), partial, no_genus_assigned; qc_any_flag = any of them;
  * spf weighted by metagenome size, known_species_fraction weighted by bacterial+archaeal bases;
  * CLR-PCA as in the 1.11.0 report (root >= 2, genera with relabund >= 0.001 in >= 1 % of samples, re-closed,
    multiplicative replacement delta 1e-5, column-centred, randomized SVD 5 comps / 30 oversamples / 7 iterations / seed 0,
    largest-|loading| genus positive).

usage: python scripts/refresh_sandpiper.py --bulk sandpiper2.0.1.gtdb.csv.gz --per-acc sandpiper2.0.1.per_acc_summary.csv.gz \
         --version 2.0.1 --record 21287531 --catalog-dir build/package --work WORK --out data/inputs/catalog/sandpiper \
         [--previous data/inputs/catalog/sandpiper] [--registry-runs data/inputs/registry/registry_runs_sandpiper.parquet]
Writes OUT/gut_sandpiper_{sample_summary,sample_genus,sample_species,pca_scores,pca_loadings}.parquet,
gut_sandpiper_{study_coverage,pca_variance}.csv, GUT_SANDPIPER_REPORT.md, gut_sandpiper_refresh_log.json. With
--registry-runs, rewrites that file's sandpiper_profiled flags from the per_acc_summary run list (backup kept alongside).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GS = os.path.join(ROOT, "src", "catalog", "sandpiper", "gut")   # filter_gut.py, build_gut_tables.py, unfilled_to_filled.py (tracked copies)
sys.path.insert(0, os.path.join(ROOT, "src"))
from catalog.sandpiper.prepare_inputs import flags as run_flags  # noqa: E402  (same per-run flag rules as the 1.x build)

FLAGS = ["qc_low_depth", "qc_low_complexity", "qc_readfraction_warning", "qc_non_metagenome", "qc_non_metagenome_strict",
         "qc_synthetic", "qc_rna", "qc_predicted_ecological", "qc_partial", "qc_no_genus_assigned"]
SUMMARY_COLS = ["sample_key", "study_accession", "n_runs_profiled", "n_runs_total", "qc_partial", "root_coverage_sum",
                "richness_genus", "shannon_genus", "shannon_genus_assigned", "top_genus", "top_genus_relabund",
                "unassigned_genus_relabund", "unassigned_species_relabund", "spf", "known_species_fraction", "qc_flags",
                "qc_any_flag", "qc_low_depth", "qc_low_complexity", "qc_readfraction_warning", "qc_non_metagenome",
                "qc_non_metagenome_strict", "qc_synthetic", "qc_rna", "qc_predicted_ecological", "qc_no_genus_assigned",
                "organism_labels", "runs_profiled", "sandpiper_url", "age_category", "body_site_class", "infant_scope",
                "taxonomy_db", "taxonomy_version", "sandpiper_version", "zenodo_record"]


def log(msg, t0):
    print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)


def wmean(x, w):
    x = np.asarray(x, float); w = np.asarray(w, float)
    ok = ~np.isnan(x)
    if not ok.any():
        return np.nan
    if np.nansum(w[ok]) > 0:
        return float(np.nansum(x[ok] * w[ok]) / np.nansum(w[ok]))
    return float(np.nanmean(x[ok]))


def clr_pca(genus_long, root, seed=0):
    """genus_long: sample_key, genus, relabund (assigned genera only). Returns scores, loadings, variance frames."""
    from sklearn.utils.extmath import randomized_svd
    keep_s = set(root[root >= 2].index)
    g = genus_long[genus_long.sample_key.isin(keep_s)]
    n_mat = len(keep_s)
    prev = g[g.relabund >= 0.001].groupby("genus").sample_key.nunique() / n_mat
    genera = sorted(prev[prev >= 0.01].index)
    g = g[g.genus.isin(set(genera))]
    samples = sorted(set(g.sample_key))
    si = pd.Index(samples); gi = pd.Index(genera)
    X = np.zeros((len(samples), len(genera)), dtype=np.float64)
    X[si.get_indexer(g.sample_key), gi.get_indexer(g.genus)] = g.relabund.to_numpy(float)
    kept_share = X.sum(axis=1)
    mean_relabund = X.mean(axis=0)
    prev_ge = (X >= 0.001).mean(axis=0)
    X = X / kept_share[:, None]
    delta = 1e-5
    nz = (X == 0).sum(axis=1)
    X = np.where(X == 0, delta, X * (1 - nz * delta)[:, None])
    L = np.log(X)
    C = L - L.mean(axis=1, keepdims=True)
    clr_mean = C.mean(axis=0)
    C -= clr_mean
    total = float((C ** 2).sum())
    U, S, Vt = randomized_svd(C, n_components=5, n_oversamples=30, n_iter=7, random_state=seed)
    for k in range(5):  # sign convention: largest-|loading| genus positive
        j = int(np.argmax(np.abs(Vt[k])))
        if Vt[k, j] < 0:
            Vt[k] *= -1; U[:, k] *= -1
    scores = pd.DataFrame((U * S).astype(np.float32), columns=[f"pc{i}" for i in range(1, 6)])
    scores.insert(0, "sample_key", samples)
    loadings = pd.DataFrame(Vt.T, columns=[f"pc{i}" for i in range(1, 6)])
    loadings.insert(0, "genus", genera)
    loadings["prevalence_ge_0p1pct"] = prev_ge.round(4); loadings["mean_relabund"] = mean_relabund.round(6)
    loadings["clr_mean"] = clr_mean.round(4)
    evr = S ** 2 / total
    var = pd.DataFrame({"pc": [f"pc{i}" for i in range(1, 6)], "explained_variance_ratio": evr.round(6),
                        "singular_value": S.round(3), "cumulative": np.cumsum(evr).round(6), "n_samples": len(samples),
                        "n_genera": len(genera), "root_coverage_min": 2, "prevalence_min": 0.01, "prevalence_detection": 0.001,
                        "pseudocount": delta, "total_clr_variance": round(total, 3)})
    meta = {"n_matrix_samples": n_mat, "n_scored": len(samples), "n_unscored_no_kept_genus": n_mat - len(samples),
            "kept_share_median": float(np.median(kept_share)), "kept_share_p5": float(np.percentile(kept_share, 5))}
    return scores, loadings, var, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulk", required=True); ap.add_argument("--per-acc", required=True)
    ap.add_argument("--version", required=True); ap.add_argument("--record", required=True)
    ap.add_argument("--taxonomy-version", default="R232")
    ap.add_argument("--catalog-dir", required=True); ap.add_argument("--work", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--previous", help="previous OUT dir, for the change section of the report")
    ap.add_argument("--registry-runs", help="registry_runs_sandpiper.parquet to re-flag from the per_acc_summary run list")
    ap.add_argument("--skip-filter", action="store_true", help="reuse WORK/work/sp/raw_parts from an earlier pass")
    ap.add_argument("--update-package", action="store_true",
                    help="also set catalog_runs.sandpiper_profiled (and catalog_studies.sandpiper_profiled_share) in --catalog-dir from the "
                         "per-run run list; for runs that reached the catalog outside registry_runs (monthly delta, re-fetch)")
    a = ap.parse_args()
    t0 = time.time()
    os.makedirs(f"{a.work}/work/sp", exist_ok=True); os.makedirs(a.out, exist_ok=True)
    prev_sum = None   # read before anything is written: --previous may be the --out directory
    if a.previous and os.path.exists(f"{a.previous}/gut_sandpiper_sample_summary.parquet"):
        prev_sum = pd.read_parquet(f"{a.previous}/gut_sandpiper_sample_summary.parquet", columns=["sample_key", "root_coverage_sum", "top_genus", "sandpiper_version"])
    env = dict(os.environ, SANDPIPER_VERSION=a.version, SANDPIPER_ZENODO_RECORD=a.record, SANDPIPER_TAXONOMY_VERSION=a.taxonomy_version)

    # ---- current samples and their runs
    w = pd.read_parquet(f"{a.catalog_dir}/catalog_sample_metadata_wide.parquet",
                        columns=["sample_key", "study_accession", "age_category", "body_site_class", "infant_scope", "release_retired"])
    n_wide = len(w); w = w[w.release_retired.isna()].drop(columns="release_retired")
    runs = pd.read_parquet(f"{a.catalog_dir}/catalog_runs.parquet",
                           columns=["run_accession", "study_accession", "sample_key", "nucleic_acid", "library_source", "library_strategy"])
    runs = runs[runs.sample_key.isin(set(w.sample_key))].drop_duplicates("run_accession")
    log(f"current samples {len(w):,} (wide rows {n_wide:,}); their runs {len(runs):,}", t0)
    # DNA runs only: a metatranscriptome run that Sandpiper happens to have profiled is not mixed into a community DNA profile
    cand = runs[runs.nucleic_acid.eq("dna")].assign(sandpiper_profiled=True)
    cand.to_parquet(f"{a.work}/candidate_runs.parquet", index=False)

    # ---- streaming filter of the bulk file
    if not a.skip_filter:
        for d_ in ("raw_parts", "raw_parts_unfilled"):
            if os.path.isdir(f"{a.work}/work/sp/{d_}"):
                shutil.rmtree(f"{a.work}/work/sp/{d_}")
        subprocess.run([sys.executable, f"{GS}/filter_gut.py", os.path.abspath(a.bulk), os.path.abspath(f"{a.work}/candidate_runs.parquet")],
                       cwd=a.work, check=True, env=env)
    flog = json.load(open(f"{a.work}/work/sp/filter_log.json"))
    # Sandpiper >= 2.0.1 ships unfilled coverage (header 'coverage' instead of 'filled_coverage'): convert to filled
    import gzip
    with gzip.open(a.bulk, "rt") as fh:
        header = fh.readline().strip().split("\t")
    unfilled = header[1] == "coverage"
    if unfilled and not os.path.isdir(f"{a.work}/work/sp/raw_parts_unfilled"):
        os.rename(f"{a.work}/work/sp/raw_parts", f"{a.work}/work/sp/raw_parts_unfilled")
        subprocess.run([sys.executable, f"{GS}/unfilled_to_filled.py"], cwd=a.work, check=True, env=env)
    flog["coverage_format"] = "unfilled (converted to filled)" if unfilled else "filled"
    import pyarrow.dataset as ds
    prof_runs = set(ds.dataset(f"{a.work}/work/sp/raw_parts", format="parquet").to_table(columns=["run"]).column("run").unique().to_pylist())
    pr = runs[runs.run_accession.isin(prof_runs)]
    pr[["run_accession", "sample_key", "study_accession"]].to_parquet(f"{a.work}/work/sp/gut_runs_profiled.parquet", index=False)
    log(f"profiled runs {len(pr):,} of {len(runs):,}; samples {pr.sample_key.nunique():,}", t0)
    subprocess.run([sys.executable, f"{GS}/build_gut_tables.py"], cwd=a.work, check=True, env=env)
    W = f"{a.work}/work/out"
    blog = json.load(open(f"{a.work}/work/sp/build_log.json"))

    # ---- per-run metadata and flags
    keep = set(pr.run_accession); parts = []
    for ch in pd.read_csv(a.per_acc, chunksize=500_000, dtype={"sample": "string"}):
        parts.append(ch[ch["sample"].isin(keep)])
    qc = pd.concat(parts, ignore_index=True).rename(columns={"sample": "run_accession"}).drop_duplicates("run_accession")
    qc = qc.merge(runs[["run_accession", "sample_key", "library_source", "library_strategy"]], on="run_accession", how="left")
    qc = pd.concat([qc, run_flags(qc)], axis=1)
    org = qc["organism"].fillna("").astype(str)
    qc["f_nonmg"] = qc.sp_flag_non_metagenome_strict & org.ne("Homo sapiens")
    qc["f_eco"] = qc["host_or_not"].astype(str).str.lower().eq("ecological")
    n_runs_no_meta = len(keep) - len(qc)
    log(f"per-run metadata rows {len(qc):,} (profiled runs without metadata {n_runs_no_meta:,})", t0)
    g = qc.groupby("sample_key")
    rq = pd.DataFrame({
        "spf": g.apply(lambda d: wmean(d.singlem_prokaryotic_fraction, d.metagenome_size.fillna(0) / 1e6)),
        "known_species_fraction": g.apply(lambda d: wmean(d.known_species_fraction, d.bacterial_archaeal_bases.fillna(0) / 1e6)),
        "qc_low_complexity": g.sp_flag_low_complexity.any(), "qc_readfraction_warning": g.sp_flag_readfraction_warning.any(),
        "qc_non_metagenome": g.f_nonmg.any(), "qc_non_metagenome_strict": g.sp_flag_non_metagenome_strict.any(),
        "qc_synthetic_meta": g.sp_flag_synthetic.any(), "qc_rna": g.sp_flag_rna_strict.any(), "qc_predicted_ecological": g.f_eco.any(),
        "organism_labels": g.organism.apply(lambda s: ";".join(sorted({str(x) for x in s.dropna() if str(x).strip()})) or None),
    })
    core = pd.read_parquet(f"{W}/_summ_core.parquet")
    s = core.merge(w, on="sample_key", how="left")
    gp = pr.groupby("sample_key")
    s["n_runs_profiled"] = s.sample_key.map(gp.size()).fillna(0).astype(int)
    dna = runs[runs.nucleic_acid.eq("dna") | runs.run_accession.isin(keep)]
    s["n_runs_total"] = s.sample_key.map(dna.groupby("sample_key").size()).fillna(0).astype(int)
    s["runs_profiled"] = s.sample_key.map(gp.run_accession.apply(lambda x: ";".join(sorted(x))))
    s["sandpiper_url"] = "https://sandpiper.qut.edu.au/run/" + s.runs_profiled.str.split(";").str[0]
    s = s.merge(rq, left_on="sample_key", right_index=True, how="left")
    syn_src = set(runs[runs.library_source.astype(str).str.upper().eq("SYNTHETIC") & runs.run_accession.isin(keep)].sample_key)
    s["qc_synthetic"] = s.qc_synthetic_meta.fillna(False).astype(bool) | s.sample_key.isin(syn_src)
    for c in ("qc_low_complexity", "qc_readfraction_warning", "qc_non_metagenome", "qc_non_metagenome_strict", "qc_rna", "qc_predicted_ecological"):
        s[c] = s[c].fillna(False).astype(bool)
    s["qc_partial"] = s.n_runs_profiled < s.n_runs_total
    s["qc_low_depth"] = s.root_coverage_sum < 2
    s["qc_no_genus_assigned"] = s.richness_genus.fillna(0) == 0
    s["qc_flags"] = s[FLAGS].apply(lambda x: ";".join(c[3:] for c, v in x.items() if bool(v)), axis=1)
    s["qc_any_flag"] = s[FLAGS].any(axis=1)
    s["taxonomy_db"], s["taxonomy_version"], s["sandpiper_version"], s["zenodo_record"] = "GTDB", a.taxonomy_version, a.version, a.record
    s = s[SUMMARY_COLS].sort_values(["study_accession", "sample_key"]).reset_index(drop=True)
    assert s.sample_key.is_unique and s.sample_key.isin(set(w.sample_key)).all()
    s.to_parquet(f"{a.out}/gut_sandpiper_sample_summary.parquet", index=False)
    for rank in ("genus", "species"):
        shutil.copy(f"{W}/gut_sandpiper_sample_{rank}.parquet", f"{a.out}/gut_sandpiper_sample_{rank}.parquet")
    log(f"summary {len(s):,} samples in {s.study_accession.nunique():,} projects", t0)

    # ---- PCA
    gl = pd.read_parquet(f"{W}/_genus_long_ge2.parquet")
    scores, loadings, var, pmeta = clr_pca(gl, s.set_index("sample_key").root_coverage_sum)
    scores = scores.merge(s[["sample_key", "study_accession", "root_coverage_sum"]], on="sample_key", how="left")
    for df_ in (scores, loadings):
        df_["taxonomy_db"], df_["taxonomy_version"], df_["sandpiper_version"] = "GTDB", a.taxonomy_version, a.version
    scores["zenodo_record"] = a.record; scores["pca_method"] = "clr_multiplicative_pseudocount_1e-5_randomized_svd"
    scores.to_parquet(f"{a.out}/gut_sandpiper_pca_scores.parquet", index=False)
    loadings.to_parquet(f"{a.out}/gut_sandpiper_pca_loadings.parquet", index=False)
    var.to_csv(f"{a.out}/gut_sandpiper_pca_variance.csv", index=False)
    log(f"PCA scored {len(scores):,} samples on {len(loadings)} genera; EVR {var.explained_variance_ratio.round(4).tolist()}", t0)

    # ---- study coverage (every project with a current sample)
    st = pd.read_parquet(f"{a.catalog_dir}/catalog_studies.parquet")
    tcol = next((c for c in ("study_title", "title") if c in st.columns), None)
    if "release_retired" in st.columns:   # current row first, one row per project
        st = st.assign(_r=st.release_retired.notna()).sort_values(["study_accession", "_r"])
    st = st.drop_duplicates("study_accession", keep="first")
    titles = st.set_index("study_accession")[tcol] if tcol else pd.Series(dtype=str)
    cov = w.groupby("study_accession").agg(n_samples=("sample_key", "size"), n_infant_scope=("infant_scope", lambda x: int(pd.Series(x).fillna(False).astype(bool).sum()))).reset_index()
    cov["study_title"] = cov.study_accession.map(titles)
    cov["n_runs"] = cov.study_accession.map(runs.groupby("study_accession").size()).fillna(0).astype(int)
    cov["n_runs_profiled"] = cov.study_accession.map(pr.groupby("study_accession").size()).fillna(0).astype(int)
    gs = s.groupby("study_accession")
    for col, val in (("n_samples_profiled", gs.size()), ("n_samples_partial", gs.qc_partial.sum()), ("n_low_depth", gs.qc_low_depth.sum()),
                     ("n_low_complexity", gs.qc_low_complexity.sum()), ("n_any_flag", gs.qc_any_flag.sum())):
        cov[col] = cov.study_accession.map(val).fillna(0).astype(int)
    for col, src in (("root_coverage_median", "root_coverage_sum"), ("richness_genus_median", "richness_genus"), ("shannon_genus_median", "shannon_genus")):
        cov[col] = cov.study_accession.map(gs[src].median()).round(3)
    cov["frac_runs_profiled"] = (cov.n_runs_profiled / cov.n_runs.where(cov.n_runs > 0)).round(4).fillna(0)
    cov["frac_samples_profiled"] = (cov.n_samples_profiled / cov.n_samples).round(4)
    f = cov.frac_samples_profiled
    cov["coverage_class"] = np.select([f >= 0.95, f >= 0.5, f > 0], ["complete", "majority", "partial"], "none")
    cov["top_genus_mode"] = cov.study_accession.map(gs.top_genus.agg(lambda x: x.mode().iloc[0] if x.notna().any() else None))
    cov["prefix"] = cov.study_accession.str[:5]
    cov["sandpiper_version"], cov["taxonomy_db"], cov["taxonomy_version"], cov["zenodo_record"] = a.version, "GTDB", a.taxonomy_version, a.record
    cols = ["study_accession", "study_title", "n_runs", "n_runs_profiled", "n_samples", "n_infant_scope", "n_samples_profiled", "n_samples_partial",
            "n_low_depth", "n_low_complexity", "n_any_flag", "root_coverage_median", "richness_genus_median", "shannon_genus_median",
            "frac_runs_profiled", "frac_samples_profiled", "coverage_class", "top_genus_mode", "prefix", "sandpiper_version", "taxonomy_db",
            "taxonomy_version", "zenodo_record"]
    cov[cols].sort_values("study_accession").to_csv(f"{a.out}/gut_sandpiper_study_coverage.csv", index=False)

    # ---- registry run flags
    reg = None
    if a.registry_runs:
        allrun = set()
        for ch in pd.read_csv(a.per_acc, chunksize=1_000_000, usecols=["sample"], dtype={"sample": "string"}):
            allrun.update(ch["sample"].dropna())
        rr = pd.read_parquet(a.registry_runs)
        bk = a.registry_runs.replace(".parquet", f"_pre_sandpiper{a.version}.parquet")
        if not os.path.exists(bk):
            shutil.copy(a.registry_runs, bk)
        before = int(rr.sandpiper_profiled.sum())
        # profiled = in the per-run run list, or profiled for a catalog sample in this pass
        rr["sandpiper_profiled"] = rr.run_accession.isin(allrun) | rr.run_accession.isin(keep)
        rr.to_parquet(a.registry_runs, index=False)
        reg = {"registry_runs": len(rr), "profiled_before": before, "profiled_after": int(rr.sandpiper_profiled.sum()),
               "catalog_profiled_runs_not_in_per_acc": len(keep - allrun)}
        log(f"registry flags {reg}", t0)

    # ---- package run flags (runs added by the monthly delta / re-fetch paths are not in registry_runs, so the gut build left them False)
    pkg_upd = None
    if a.update_package or a.registry_runs:
        allrun = set()
        for ch in pd.read_csv(a.per_acc, chunksize=1_000_000, usecols=["sample"], dtype={"sample": "string"}):
            allrun.update(ch["sample"].dropna())
    if a.registry_runs:
        rr = pd.read_parquet(a.registry_runs)
        cr0 = pd.read_parquet(f"{a.catalog_dir}/catalog_runs.parquet", columns=["run_accession", "study_accession"]).drop_duplicates("run_accession")
        add = cr0[~cr0.run_accession.isin(set(rr.run_accession))].assign(sandpiper_profiled=lambda d: d.run_accession.isin(allrun) | d.run_accession.isin(keep))
        if len(add):
            rr = pd.concat([rr, add[rr.columns]], ignore_index=True); rr.to_parquet(a.registry_runs, index=False)
        reg["catalog_runs_added_to_registry_flags"] = len(add)
    if a.update_package:
        cp = f"{a.catalog_dir}/catalog_runs.parquet"; cr = pd.read_parquet(cp)
        before = int(cr.sandpiper_profiled.fillna(False).astype(bool).sum())
        cr["sandpiper_profiled"] = cr.sandpiper_profiled.fillna(False).astype(bool) | cr.run_accession.isin(allrun) | cr.run_accession.isin(keep)
        cr.to_parquet(cp, index=False)
        sp_ = f"{a.catalog_dir}/catalog_studies.parquet"; st_ = pd.read_parquet(sp_)
        if "sandpiper_profiled_share" in st_.columns:
            sh = cr.groupby("study_accession").sandpiper_profiled.mean().round(4)
            cur_ = st_.release_retired.isna() if "release_retired" in st_.columns else pd.Series(True, index=st_.index)
            st_.loc[cur_, "sandpiper_profiled_share"] = st_.loc[cur_, "study_accession"].map(sh).astype(float).values
            st_.to_parquet(sp_, index=False)
        pkg_upd = {"catalog_runs_flagged_before": before, "after": int(cr.sandpiper_profiled.sum())}
        log(f"package run flags {pkg_upd}", t0)

    # ---- previous tables (for the change section)
    chg = None
    if prev_sum is not None:
        old = prev_sum
        cur = set(w.sample_key); ok_old = old[old.sample_key.isin(cur)]
        m = ok_old.merge(s[["sample_key", "root_coverage_sum", "top_genus"]], on="sample_key", suffixes=("_old", "_new"))
        chg = {"previous_rows": len(old), "previous_rows_retired_samples": int((~old.sample_key.isin(cur)).sum()),
               "previous_versions": old.sandpiper_version.value_counts().to_dict(), "samples_new": int((~s.sample_key.isin(set(old.sample_key))).sum()),
               "samples_lost": int((~ok_old.sample_key.isin(set(s.sample_key))).sum()),
               "root_changed_gt1pct": int(((m.root_coverage_sum_new - m.root_coverage_sum_old).abs() > 0.01 * m.root_coverage_sum_old.abs().clip(lower=1e-9)).sum()),
               "top_genus_changed": int((m.top_genus_old.fillna("") != m.top_genus_new.fillna("")).sum()), "compared": len(m)}
        log(f"changes vs previous {chg}", t0)

    # ---- report
    def pct(x, n):
        return f"{100 * x / n:.1f} %" if n else "—"
    by_prefix = cov.groupby("prefix").agg(projects=("study_accession", "size"), projects_profiled=("n_samples_profiled", lambda x: int((x > 0).sum())),
                                          runs=("n_runs", "sum"), runs_profiled=("n_runs_profiled", "sum"), samples=("n_samples", "sum"),
                                          samples_profiled=("n_samples_profiled", "sum")).reset_index()
    by_age = w.assign(p=w.sample_key.isin(set(s.sample_key))).groupby(w.age_category.fillna("unknown")).p.agg(["size", "sum"]).reset_index()
    flag_rows = [(c, int(s[c].sum()), pct(int(s[c].sum()), len(s))) for c in FLAGS + ["qc_any_flag"]]
    L = [f"# CATALOG_SANDPIPER_REPORT — Sandpiper {a.version} profiles for the current catalog samples",
         "", f"Built by `scripts/refresh_sandpiper.py` (one pass, all tables rebuilt). Inputs: `{os.path.basename(a.bulk)}` (GTDB {a.taxonomy_version}, Zenodo record {a.record}), "
         f"`{os.path.basename(a.per_acc)}`, `catalog_runs.parquet` and `catalog_sample_metadata_wide.parquet` from `{a.catalog_dir}`. "
         f"Only current samples (release_retired null) are used: {len(w):,} of {n_wide:,} wide-table rows.", "",
         "## 1. Streaming pass", "", "| quantity | value |", "|---|---|",
         f"| bulk rows scanned | {flog['rows_scanned']:,} |", f"| rows kept | {flog['rows_kept']:,} |",
         f"| candidate runs (DNA runs of current samples) | {flog['matched_runs_expected']:,} |", f"| runs with a profile | {flog['runs_kept']:,} |",
         f"| coverage format in the bulk file | {flog.get('coverage_format', 'filled')} |", f"| wall time | {flog['seconds']} s |", "",
         f"Unfilled coverage rows below −0.02: {blog['negative_unfilled_rows']}. Genus sum-check (min, max, n): {blog['genus']['sum_check']}.", "",
         "## 2. Coverage", "",
         f"**{len(s):,} of {len(w):,} current samples ({pct(len(s), len(w))}) in {s.study_accession.nunique():,} of {w.study_accession.nunique():,} projects; "
         f"{len(pr):,} of {len(runs):,} runs ({pct(len(pr), len(runs))}).** Profiled runs without per-run metadata: {n_runs_no_meta:,}.", "",
         by_prefix.to_markdown(index=False), "", "Coverage classes (frac_samples_profiled; complete ≥ 0.95, majority ≥ 0.5, partial > 0): "
         + ", ".join(f"{k} {v:,}" for k, v in cov.coverage_class.value_counts().items()) + ".", "",
         "By age category:", "", by_age.rename(columns={"age_category": "age_category", "size": "n_samples", "sum": "n_profiled"}).assign(
             frac_profiled=lambda d: (d.n_profiled / d.n_samples).round(3)).to_markdown(index=False), "",
         "## 3. QC flags per sample (flagged, never dropped; now computed for every sample)", "", "| flag | n_samples | pct |", "|---|---|---|"]
    L += [f"| {c} | {n:,} | {p} |" for c, n, p in flag_rows]
    L += ["", "Definitions as in the 1.x report: low_depth = summed root coverage < 2; low_complexity / readfraction_warning / predicted_ecological "
          "(host_or_not = ecological) = on any run; non_metagenome = organism names a single non-human species on any run (strict rule minus "
          "'Homo sapiens'); non_metagenome_strict includes 'Homo sapiens'; synthetic = organism synthetic/simulat or library_source SYNTHETIC; "
          "rna = RNA library strategy/source; partial = n_runs_profiled < n_runs_total (DNA runs); no_genus_assigned = richness 0.", "",
          "## 4. PCA", "", f"Matrix: {pmeta['n_matrix_samples']:,} samples with root coverage ≥ 2; {len(loadings)} genera detected (≥ 0.001) in ≥ 1 % of them; "
          f"{pmeta['n_unscored_no_kept_genus']:,} samples contain none of the kept genera → **{len(scores):,} scored samples**. Kept genera hold a median "
          f"{pmeta['kept_share_median']:.3f} (5th percentile {pmeta['kept_share_p5']:.3f}) of a sample's root coverage. Method unchanged from 1.x.", "",
          "Explained variance ratio: " + ", ".join(f"{r.pc.upper()} {r.explained_variance_ratio:.4f}" for r in var.itertuples()) + f" (cumulative {var.cumulative.iloc[-1]:.4f}).", ""]
    for k in range(1, 6):
        lo = loadings.sort_values(f"pc{k}")
        L.append(f"- **PC{k}** + " + ", ".join(f"{r.genus} {getattr(r, f'pc{k}'):.3f}" for r in lo.tail(6)[::-1].itertuples())
                 + "; − " + ", ".join(f"{r.genus} {getattr(r, f'pc{k}'):.3f}" for r in lo.head(5).itertuples()))
    if chg:
        L += ["", "## 5. Changes against the previous tables", "", "| quantity | value |", "|---|---|"]
        L += [f"| {k} | {v:,} |" if isinstance(v, int) else f"| {k} | {v} |" for k, v in chg.items()]
    if reg:
        L += ["", "## 6. Registry run flags", "", f"`registry_runs_sandpiper.parquet`: {reg['registry_runs']:,} runs; sandpiper_profiled "
              f"{reg['profiled_before']:,} → {reg['profiled_after']:,} (run list of `{os.path.basename(a.per_acc)}`)."]
    open(f"{a.out}/GUT_SANDPIPER_REPORT.md", "w", encoding="utf-8").write("\n".join(L) + "\n")
    out = {"version": a.version, "record": a.record, "current_samples": len(w), "samples_profiled": len(s), "projects_profiled": int(s.study_accession.nunique()),
           "runs": len(runs), "runs_profiled": len(pr), "pca_scored": len(scores), "pca_genera": len(loadings), "filter": {k: v for k, v in flog.items() if k != "matched_runs_missing_from_bulk_sample"},
           "changes": chg, "registry": reg, "package_run_flags": pkg_upd, "flags": {c: n for c, n, _ in flag_rows}, "seconds": round(time.time() - t0, 1)}
    json.dump(out, open(f"{a.out}/gut_sandpiper_refresh_log.json", "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in out.items() if k not in ("filter",)}, default=str))


if __name__ == "__main__":
    main()
