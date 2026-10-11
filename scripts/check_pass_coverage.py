"""Release check (R2026.25, owner request "each project must have had all available passes"): for every catalog project,
record whether each annotation pass that applies to it was attempted, and fail the release when a pass was never attempted.

Passes and where their per-project logs live (data/inputs/gut/...):
  fulltext      R3 / R3b study summaries (r3*/gut_r3*_study_summary_all.parquet, r3*/shards/*study_summary*gap*), deep_sources pmcids_tried
                applies when the project has an uncontested linked paper with a PMCID
  abstract      R4 / R4b study summaries (r4/gut_r4_study_summary_shard_*.parquet, r4b/shards/gut_r4b_study_summary_shard_*.parquet)
  interventions interventions/gut_intervention_studies_shard_*.parquet
  deep          deep/deep_sources_shard_*.parquet (per-sample archive + paper-table pass)
  supplementary r2/gut_r2_gate_shard_*.parquet or a deep_sources row with supp_files_found recorded; applies with a PMCID
  subject       design/logs/subject_log_shard_*.parquet + external logs; applies when subject_id coverage < 95 %
  design        design/study_design_shard_*.parquet (relatedness + longitudinal design)
Statuses: done | attempted_failed (source unavailable, e.g. Europe PMC errors — counted as attempted) | not_applicable | missing.
A project/pass may be exempted with a reason in config/pass_coverage_exceptions.csv (study_accession, pass, reason), e.g. a
content-filter refusal. Writes --out (one row per project) and exits 1 when more than --max project-passes are missing.
"""
import argparse
import glob
import os
import sys

import pandas as pd


def _set(pattern, col="study_accession", where=None):
    s = set()
    for f in glob.glob(pattern):
        d = pd.read_parquet(f) if f.endswith(".parquet") else pd.read_csv(f, dtype=str)
        if col not in d.columns:
            continue
        if where is not None:
            d = d[where(d)]
        s |= set(d[col].dropna().astype(str))
    return s


def _truthy(v):
    return v.astype(str).str.lower().isin(["true", "1", "1.0", "yes"])


def coverage(gut_dir, studies, papers, exceptions=None):
    st = pd.read_parquet(studies)
    st = st[st.release_retired.isna()] if "release_retired" in st.columns else st
    T = pd.DataFrame(index=sorted(set(st.study_accession)))
    T.index.name = "study_accession"
    pl = pd.read_parquet(papers)
    pl = pl[~pl.get("contested", pd.Series(False, index=pl.index)).fillna(False).astype(bool)]
    has_pmc = set(pl.study_accession[pl.pmcid.notna()])
    T["has_pmcid"] = T.index.isin(has_pmc)
    j = lambda *p: os.path.join(gut_dir, *p)
    # full text
    sums = glob.glob(j("r3*", "gut_r3*_study_summary_all.parquet")) + glob.glob(j("r3*", "shards", "gut_r3*_study_summary*gap*.parquet"))
    ft_try, ft_ok = set(), set()
    for f in sums:
        d = pd.read_parquet(f)
        ft_try |= set(d.study_accession.astype(str))
        if "fulltext_available" in d.columns:
            ft_ok |= set(d.study_accession[_truthy(d.fulltext_available)].astype(str))
    ds = pd.concat([pd.read_parquet(f) for f in glob.glob(j("deep", "deep_sources_shard_*.parquet"))], ignore_index=True) if glob.glob(j("deep", "deep_sources_shard_*.parquet")) else pd.DataFrame(columns=["study_accession"])
    if len(ds):
        tried = ds.pmcids_tried.map(lambda v: bool(len(v)) if hasattr(v, "__len__") and not isinstance(v, str) else bool(str(v or "").strip()) and str(v) not in ("nan", "None", "[]"))
        ft_try |= set(ds.study_accession[tried].astype(str))
        ft_ok |= set(ds.study_accession[_truthy(ds.fulltext_available)].astype(str))
    T["fulltext"] = ["not_applicable" if not p else ("done" if a in ft_ok else ("attempted_failed" if a in ft_try else "missing")) for a, p in zip(T.index, T.has_pmcid)]
    ab = _set(j("r4", "gut_r4_study_summary_shard_*.parquet")) | _set(j("r4b", "shards", "gut_r4b_study_summary_shard_*.parquet"))
    T["abstract"] = ["done" if a in ab else "missing" for a in T.index]
    iv = _set(j("interventions", "gut_intervention_studies_shard_*.parquet"))
    T["interventions"] = ["done" if a in iv else "missing" for a in T.index]
    dp = set(ds.study_accession.astype(str)) if len(ds) else set()
    T["deep"] = ["done" if a in dp else "missing" for a in T.index]
    su = _set(j("r2", "gut_r2_gate_shard_*.parquet"))
    su_fail = set()
    if len(ds) and "supp_files_found" in ds.columns:
        # a deep_sources row counts only when the supplementary download actually happened; rows whose note says the zip was
        # never fetched (time budget) do not count, rows with download errors count as attempted_failed (R2026.25)
        notes = ds.get("failure_notes", pd.Series("", index=ds.index)).fillna("").astype(str).str.lower()
        n_found = pd.to_numeric(ds.supp_files_found, errors="coerce")
        not_reached = (n_found.fillna(0) == 0) & notes.str.contains(r"not downloaded|not fetched|time budget")
        errored = (n_found.fillna(0) == 0) & ~not_reached & notes.str.contains(r"500|error|timeout|fail|unreadable|not a zip")
        ok = ds.supp_files_found.notna() & ~not_reached & ~errored
        su |= set(ds.study_accession[ok].astype(str))
        su_fail = set(ds.study_accession[errored].astype(str)) - su
    T["supplementary"] = ["not_applicable" if not p else ("done" if a in su else ("attempted_failed" if a in su_fail else "missing")) for a, p in zip(T.index, T.has_pmcid)]
    sj = _set(j("design", "logs", "subject_log_shard_*.parquet")) | _set(j("design", "logs", "external", "*subject_log*.csv")) | _set(j("design", "logs", "external", "v*", "subject_log.csv"))
    cov = st.set_index("study_accession").get("cov_subject_id")
    need = (pd.to_numeric(cov, errors="coerce").reindex(T.index).fillna(0) < 0.95) if cov is not None else pd.Series(True, index=T.index)
    T["subject"] = ["done" if a in sj else ("not_applicable" if not n else "missing") for a, n in zip(T.index, need)]
    dz = _set(j("design", "study_design_shard_*.parquet"))
    T["design"] = ["done" if a in dz else "missing" for a in T.index]
    passes = ["fulltext", "abstract", "interventions", "deep", "supplementary", "subject", "design"]
    if exceptions and os.path.exists(exceptions):
        ex = pd.read_csv(exceptions, dtype=str)
        for _, r in ex.iterrows():
            p = r.get("pass")
            if r.study_accession in T.index and p in passes and T.at[r.study_accession, p] == "missing":
                T.at[r.study_accession, p] = "exempt: " + str(r.get("reason", ""))
    T["n_missing"] = (T[passes] == "missing").sum(axis=1)
    return T.reset_index(), passes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gut-dir", default="data/inputs/gut")
    ap.add_argument("--studies", required=True)
    ap.add_argument("--papers", default="data/inputs/registry/registry_study_papers.parquet")
    ap.add_argument("--exceptions", default="config/pass_coverage_exceptions.csv")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max", type=int, default=0, help="allowed number of missing project-passes")
    a = ap.parse_args()
    T, passes = coverage(a.gut_dir, a.studies, a.papers, a.exceptions)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    T.to_csv(a.out, index=False)
    miss = {p: int((T[p] == "missing").sum()) for p in passes}
    tot = sum(miss.values())
    print(f"pass coverage: {len(T)} projects; missing project-passes {tot} {miss}; written {a.out}")
    if tot > a.max:
        print(f"FAIL: {tot} project-passes never attempted (> --max {a.max}); see {a.out} (column n_missing) — run the pass or add an exception with a reason", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
