#!/usr/bin/env python
"""prune_pass_exceptions.py — after a catch-up run, keep in config/pass_coverage_exceptions.csv only the project-passes that
are still not attempted, each with the reason it could not be run (R2026.32).

Runs scripts/check_pass_coverage.py's coverage() WITHOUT the exceptions file against the current logs, then:
  * drops every exception row whose pass is now done / attempted_failed / not_applicable;
  * rewrites the reason of the rows that stay missing from the catch-up logs (e.g. both readings refused by the content
    filter, one reading only), falling back to the old reason.
Deterministic; prints counts only.

usage: python scripts/prune_pass_exceptions.py --studies build/package/catalog_studies.parquet [--gut-dir data/inputs/gut]
                                               [--exceptions config/pass_coverage_exceptions.csv] [--dry-run]
"""
import argparse
import glob
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_pass_coverage import coverage  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--studies", required=True)
    ap.add_argument("--gut-dir", default="data/inputs/gut")
    ap.add_argument("--papers", default="data/inputs/registry/registry_study_papers.parquet")
    ap.add_argument("--exceptions", default="config/pass_coverage_exceptions.csv")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    T, passes = coverage(a.gut_dir, a.studies, a.papers, None)
    T = T.set_index("study_accession")
    ex = pd.read_csv(a.exceptions, dtype=str)
    # reasons from the catch-up logs
    s4 = pd.concat([pd.read_parquet(f) for f in glob.glob(os.path.join(a.gut_dir, "r4c", "gut_r4c_study_summary_shard_*.parquet"))] or [pd.DataFrame(columns=["study_accession", "status"])])
    st4 = dict(zip(s4.study_accession, s4.status)) if len(s4) else {}
    why = {"no_reading": "both readings of the catch-up pass were refused by the model's content filter (pathogen / infection subject)",
           "one_reading": "only one of the two catch-up readings returned; nothing is committed from a single reading"}
    keep = []
    for r in ex.itertuples(index=False):
        p = r[1]
        st = T.at[r.study_accession, p] if r.study_accession in T.index and p in T.columns else None
        if st is None or st != "missing":
            continue
        reason = f"R2026.32: {why[st4[r.study_accession]]}" if st4.get(r.study_accession) in why else r.reason
        keep.append(dict(study_accession=r.study_accession, **{"pass": p}, reason=reason))
    # project-passes missing that the old file did not list either (should be none)
    listed = {(r.study_accession, r[1]) for r in ex.itertuples(index=False)}
    new_missing = [(acc, p) for acc in T.index for p in passes if T.at[acc, p] == "missing" and (acc, p) not in listed]
    out = pd.DataFrame(keep, columns=["study_accession", "pass", "reason"])
    print({"rows_before": len(ex), "rows_after": len(out), "by_pass_after": out["pass"].value_counts().to_dict(),
           "missing_not_listed": len(new_missing), "missing_not_listed_by_pass": pd.Series([p for _, p in new_missing]).value_counts().to_dict() if new_missing else {}})
    if not a.dry_run:
        out.to_csv(a.exceptions, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
