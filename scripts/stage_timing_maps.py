"""R2026.25 visit-code timing pass -> per-sample day offsets.

Reads timing_map_shard_*.parquet (one row per project x visit code: code_source, code_regex, code, offset_days, time_origin,
evidence ...) and resolves every catalog sample of the mapped projects to an offset:
  code_source timepoint_label        -> the sample's published timepoint_label equals the code (whitespace-trimmed)
  code_source sample_title / library_name -> code_regex (one capture group) applied to the archive record
A project is used only when one time origin is stated for all its codes. Output feeds build_gut_scope --timing-samples
(axis 'timepoint_mapped' in catalog.scopes.longitudinal)."""
import argparse
import glob
import re

import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--maps", default="data/inputs/gut/timing/timing_map_shard_*.parquet")
ap.add_argument("--wide", default="data/inputs/data_package/gut_sample_metadata_wide.parquet")
ap.add_argument("--runs", default="data/inputs/registry/registry_runs.parquet")
ap.add_argument("--out", default="data/inputs/gut/timing/timing_samples.parquet")
a = ap.parse_args()

fs = sorted(glob.glob(a.maps))
M = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()
if not len(M):
    raise SystemExit("no timing maps")
M = M[pd.to_numeric(M.offset_days, errors="coerce").notna() & M.evidence_quote.notna()]
M["offset_days"] = pd.to_numeric(M.offset_days)
one_origin = M.groupby("study_accession").time_origin.apply(lambda v: v.fillna("").astype(str).str.lower().str.strip().nunique() <= 1)
M = M[M.study_accession.isin(one_origin[one_origin].index)]
accs = sorted(set(M.study_accession))
w = pd.read_parquet(a.wide, columns=["sample_key", "study_accession", "timepoint_label"], filters=[("study_accession", "in", accs)])
r = pd.read_parquet(a.runs, columns=["study_accession", "sample_accession", "sample_title", "library_name"], filters=[("study_accession", "in", accs)])
r = r.drop_duplicates("sample_accession").rename(columns={"sample_accession": "sample_key"})
w = w.merge(r[["sample_key", "sample_title", "library_name"]], on="sample_key", how="left")
out = []
for acc, mm in M.groupby("study_accession"):
    x = w[w.study_accession == acc]
    for src, g in mm.groupby("code_source"):
        lut = {str(c).strip(): row for c, row in zip(g.code, g.itertuples(index=False))}
        if src == "timepoint_label":
            codes = x.timepoint_label.astype("string").str.strip()
        else:
            rx = g.code_regex.dropna().astype(str).iloc[0] if g.code_regex.notna().any() else None
            if not rx or src not in x.columns:
                continue
            try:
                cre = re.compile(rx)
            except re.error:
                continue
            codes = x[src].astype("string").map(lambda s: (lambda m_: m_.group(1) if m_ else None)(cre.search(s)) if isinstance(s, str) else None)
        for sk, c in zip(x.sample_key, codes):
            if isinstance(c, str) and c.strip() in lut:
                row = lut[c.strip()]
                out.append(dict(sample_key=sk, study_accession=acc, code=c.strip(), code_source=src, offset_days=float(row.offset_days), time_origin=row.time_origin,
                                evidence_source=row.evidence_source, evidence_locator=row.evidence_locator, evidence_quote=row.evidence_quote,
                                confidence=row.confidence, determined_by=row.determined_by))
O = pd.DataFrame(out)
if len(O):
    O = O.drop_duplicates("sample_key")
O.to_parquet(a.out, index=False)
print(len(fs), "map files;", len(M), "codes;", len(O), "samples;", O.study_accession.nunique() if len(O) else 0, "projects")
