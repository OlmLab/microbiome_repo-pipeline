#!/usr/bin/env python
"""Apply the R2026.26 paper-link audit (owner request 2026-10-20) to the registry paper/author inputs.

The audit re-read every link that was labelled own_data only because the paper was the single one mentioning the project's
accession (method det_single_mention_fulltext / _abstract): a reasoning model judged, from the passages that mention the
accession plus the abstract, whether the paper generated the data or re-used / cited it (pilot: 30 % re-use).
  reused  -> relation 'cites_accession' (registry_study_papers) / paper_relation 'cites_accession' (registry_authors): no author credit
  unclear -> relation 'unsure' / paper_relation 'unsure' (listed as 'possibly associated')
  generated or no verdict -> unchanged
The verdict is recorded in contest_reason ('link audit R2026.26: ...'). Idempotent; the pre-audit tables are kept in ~/catalog/cache.
usage: python scripts/apply_paper_link_audit.py --audit data/inputs/registry/paper_link_audit_r2026_26.parquet --registry-dir data/inputs/registry
"""
import argparse, os, shutil
import pandas as pd

ap = argparse.ArgumentParser(); ap.add_argument("--audit", required=True); ap.add_argument("--registry-dir", required=True)
ap.add_argument("--backup-dir", default=os.path.expanduser("~/catalog/cache"))
a = ap.parse_args()
au = pd.read_parquet(a.audit)
au = au[au.relation.isin(["reused", "unclear"])]
key = {(s, str(p)): (r, why) for s, p, r, why in zip(au.study_accession, au.pmcid, au.relation, au.reason)}
sp_path = os.path.join(a.registry_dir, "registry_study_papers.parquet"); ra_path = os.path.join(a.registry_dir, "registry_authors.parquet")
for pth in (sp_path, ra_path):
    bk = os.path.join(a.backup_dir, os.path.basename(pth).replace(".parquet", "_pre_r2026_26.parquet"))
    if not os.path.exists(bk):
        shutil.copy(pth, bk)
sp = pd.read_parquet(sp_path)
hit = [key.get((s, str(p))) for s, p in zip(sp.study_accession, sp.pmcid)]
m = pd.Series([h is not None for h in hit], index=sp.index) & sp.relation.eq("own_data") & sp.method.isin(["det_single_mention_fulltext", "det_single_mention_abstract"])
new_rel = pd.Series([({"reused": "cites_accession", "unclear": "unsure"}[h[0]] if h else None) for h in hit], index=sp.index)
sp.loc[m, "relation"] = new_rel[m]
sp.loc[m, "contest_reason"] = ["link audit R2026.26: " + h[0] + " — " + str(h[1]) for h, mm in zip(hit, m) if mm]
sp.to_parquet(sp_path, index=False)
changed = {(s, str(p)): r for s, p, r in zip(sp.loc[m, "study_accession"], sp.loc[m, "pmid"], sp.loc[m, "relation"])}
ra = pd.read_parquet(ra_path)
rk = [changed.get((s, str(p))) for s, p in zip(ra.study_accession, ra.pmid)]
mr = pd.Series([x is not None for x in rk], index=ra.index) & ra.paper_relation.eq("own_data")
ra.loc[mr, "paper_relation"] = pd.Series(rk, index=ra.index)[mr]
ra.to_parquet(ra_path, index=False)
print(f"registry_study_papers: {int(m.sum())} links relabelled {sp.loc[m, 'relation'].value_counts().to_dict()}; registry_authors: {int(mr.sum())} rows")
