"""apply_design_filters.py — apply gate G1 of scripts/design_passes.py (place-only group keys) to design-pass outputs that were
written before the gate existed (R2026.33 production run). Projects listed in --drop (JSON list, computed with the same rule:
all assigned groups of kind geography and NOT (agreed shape cross_sectional_groups and a verbatim group quote)) lose their
per-sample rows; their study row gets status done_geography_not_a_comparison. Backups go to design/backup_pre_g1/.
usage: python scripts/apply_design_filters.py --drop /tmp/geo_drop.json [--gut data/inputs/gut]"""
import argparse, glob, json, os, shutil
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--drop", required=True)
ap.add_argument("--gut", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inputs", "gut"))
a = ap.parse_args()
drop = set(json.load(open(a.drop)))
bk = os.path.join(a.gut, "design", "backup_pre_g1")
os.makedirs(bk, exist_ok=True)
n = {}
for f in glob.glob(os.path.join(a.gut, "deep", "deep_determinations_shard_GRPD*.parquet")) + glob.glob(os.path.join(a.gut, "design", "design_sample_groups_shard_D*.parquet")) + \
         [os.path.join(a.gut, "design", "design_sample_groups_all.parquet")]:
    d = pd.read_parquet(f)
    m = d.study_accession.isin(drop)
    if m.any():
        shutil.copy2(f, os.path.join(bk, os.path.basename(f)))
        d[~m].to_parquet(f, index=False)
        n[os.path.basename(f)] = int(m.sum())
for f in glob.glob(os.path.join(a.gut, "design", "design_reading_shard_D*.parquet")) + [os.path.join(a.gut, "design", "design_reading_all.parquet")]:
    d = pd.read_parquet(f)
    m = d.study_accession.isin(drop)
    if m.any():
        shutil.copy2(f, os.path.join(bk, os.path.basename(f)))
        d.loc[m, "status"] = "done_geography_not_a_comparison"
        for c in ("n_assigned", "n_groups_assigned"):
            if c in d.columns:
                d.loc[m, c] = 0
        d.to_parquet(f, index=False)
print(json.dumps({"projects": len(drop), "rows_removed": n}))
