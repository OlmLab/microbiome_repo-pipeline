#!/usr/bin/env python
"""write_large_update_pass_log.py — record which per-sample passes the large update ran for the projects it admitted
(R2026.32), so scripts/check_pass_coverage.py can credit them instead of listing them as exceptions.

The large update (R2026.30 / R2026.31) did the catalog's per-sample work for the projects it admitted with its own
stages rather than the deep / R2 leaves:
  deep           stage 3/5/16: every BioSample of the project harvested and its attributes mapped to catalog fields
                 (two-model key and value normalisation). Credited when the project's samples appear in the harvested
                 attribute tables; a project whose BioSamples carry no attributes at all is credited as attempted.
  supplementary  stage 7 (+ the R2026.32 catch-up, stage7c): supplementary files of the generating papers downloaded,
                 every table tried against the archive sample keys with the join ladder, joinable columns classified
                 and mapped. Credited when the project went through the ladder; when the paper's own JATS lists no
                 supplementary material the pass has nothing to read and is credited as done with that basis; when
                 only re-used-data papers carry a PMCID it is not applicable.
Deterministic; prints counts only.

usage: python scripts/write_large_update_pass_log.py [--projects config/pass_coverage_exceptions.csv]
                                                      [--out data/inputs/gut/large_update_pass_log.csv]
"""
import argparse
import glob
import os
import re
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))
LU = os.path.join(CACHE, "large_update")


def jats_file(pmcid):
    c = sorted(glob.glob(f"{CACHE}/pmc_oa/{pmcid}.*/{pmcid}.*.xml")) + sorted(glob.glob(f"{CACHE}/pmc_oa/{pmcid}.epmc/{pmcid}.xml"))
    return c[-1] if c else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=os.path.join(ROOT, "config", "pass_coverage_exceptions.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "inputs", "gut", "large_update_pass_log.csv"))
    a = ap.parse_args(argv)
    ex = pd.read_csv(a.projects, dtype=str)
    need = {p: set(ex.loc[ex["pass"] == p, "study_accession"]) for p in ("deep", "supplementary")}
    rows = []
    # ---- deep: harvested + mapped archive attributes
    seen = set()
    for f in [f"{LU}/stage5/sample_values.parquet", f"{LU}/new_biosample_attributes.parquet"] + sorted(glob.glob(f"{LU}/stage16_delta_samples*/sample_values.parquet")) + \
             sorted(glob.glob(f"{LU}/stage16_delta_samples*/attrs_plain.parquet")):
        if os.path.exists(f):
            seen |= set(pd.read_parquet(f, columns=["study_accession"]).study_accession.astype(str))
    reg = os.path.join(ROOT, "data", "inputs", "registry", "registry_biosample_attributes.parquet")
    if os.path.exists(reg):
        import pyarrow.parquet as pq
        if "study_accession" in pq.read_schema(reg).names:
            seen |= set(pd.read_parquet(reg, columns=["study_accession"], filters=[("study_accession", "in", sorted(need["deep"]))]).study_accession.astype(str))
    for p in sorted(need["deep"]):
        if p in seen:
            rows.append(dict(study_accession=p, **{"pass": "deep"}, status="done",
                             basis="large update stage 3/5/16: BioSample attributes harvested and mapped to catalog fields"))
        else:
            rows.append(dict(study_accession=p, **{"pass": "deep"}, status="attempted_failed",
                             basis="large update harvest: no BioSample attribute records for this project's samples"))
    # ---- supplementary
    lad = set()
    for d in ("stage7",):   # stage7c (R2026.32 catch-up) is credited from its attempted.csv below
        for f in glob.glob(f"{LU}/{d}/proj_*.txt"):
            lad |= {l.strip().split(",")[0] for l in open(f) if l.strip()}
        for f in glob.glob(f"{LU}/{d}/ladder_*.csv"):
            lad |= set(pd.read_csv(f, usecols=["project"]).project.astype(str))
    tried_c = set()
    if os.path.exists(f"{LU}/stage7c/attempted.csv"):
        tc = pd.read_csv(f"{LU}/stage7c/attempted.csv", dtype=str)
        tried_c = dict(zip(tc.study_accession, tc.status))
    sp = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "catalog", "study_papers.parquet"))
    rp = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "registry", "registry_study_papers.parquet"),
                         columns=["study_accession", "pmcid", "relation", "contested"])
    own = pd.concat([sp.loc[sp.relation == "own_data", ["study_accession", "pmcid"]],
                     rp.loc[(rp.relation == "own_data") & ~rp.contested.fillna(False).astype(bool), ["study_accession", "pmcid"]]])
    own = own[own.pmcid.notna()].drop_duplicates()
    for p in sorted(need["supplementary"]):
        pm = sorted(set(own.loc[own.study_accession == p, "pmcid"]))
        if p in lad:
            rows.append(dict(study_accession=p, **{"pass": "supplementary"}, status="done",
                             basis="supplementary files downloaded and every table tried with the join ladder (large update stage 7)"))
        elif p in tried_c:
            st = tried_c[p]
            rows.append(dict(study_accession=p, **{"pass": "supplementary"}, status="done" if st.startswith("done") else "attempted_failed",
                             basis=f"R2026.32 supplementary catch-up: {st}"))
        elif not pm:
            rows.append(dict(study_accession=p, **{"pass": "supplementary"}, status="not_applicable",
                             basis="only papers that re-use the data carry a PMCID; their supplementary files do not describe these samples"))
        else:
            fs = [jats_file(x) for x in pm]
            fs = [f for f in fs if f]
            n_supp = sum(len(re.findall(r"<supplementary-material", open(f, errors="ignore").read())) for f in fs)
            if fs and n_supp == 0:
                rows.append(dict(study_accession=p, **{"pass": "supplementary"}, status="done",
                                 basis="the generating paper's full text lists no supplementary material"))
    out = pd.DataFrame(rows, columns=["study_accession", "pass", "status", "basis"])
    if os.path.exists(a.out):   # R2026.34: keep the rows of earlier runs; a project-pass logged again takes the new row
        out = pd.concat([pd.read_csv(a.out, dtype=str), out.astype(str)], ignore_index=True).drop_duplicates(['study_accession', 'pass'], keep='last')
    out.to_csv(a.out, index=False)
    print({"rows": len(out), **{f"{k}:{s}": int(v) for (k, s), v in out.groupby(["pass", "status"]).size().items()},
           "supplementary_not_logged": len(need["supplementary"] - set(out.loc[out["pass"] == "supplementary", "study_accession"]))})


if __name__ == "__main__":
    sys.exit(main())
