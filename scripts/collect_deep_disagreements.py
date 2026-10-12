"""R2026.25: gather deep-pass (supplementary / body table) rows that DISAGREE with a published value. integrate_deep.py drops them
(the published value has an equal or better route), so without this file the contradiction is invisible. Output feeds
build_gut_scope --contradiction-extra (catalog.scopes.contradictions)."""
import argparse, glob, os
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--raw-glob", action="append", required=True, help="raw deep_determinations_shard_*.parquet globs (repeatable)")
ap.add_argument("--out", required=True)
a = ap.parse_args()
fs = sorted({f for g in a.raw_glob for f in glob.glob(g)})
parts = []
for f in fs:
    d = pd.read_parquet(f)
    if "parse_note" not in d.columns or not len(d):
        continue
    m = d.parse_note.astype(str).str.contains("DISAGREE", case=False) & (d.get("scope", pd.Series("sample", index=d.index)).astype(str) != "study_all")
    if m.any():
        parts.append(d[m].assign(shard_file=os.path.basename(f)))
out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["sample_key", "field_name", "study_accession", "value_normalized"])
out = out.sort_values("confidence", ascending=False, kind="mergesort").drop_duplicates(["sample_key", "field_name", "value_normalized"])
out.to_parquet(a.out, index=False)
print(len(fs), len(out), out.study_accession.nunique() if len(out) else 0, out.field_name.value_counts().to_dict() if len(out) else {})
