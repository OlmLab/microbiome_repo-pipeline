#!/usr/bin/env python
"""merge_design_rerun.py — fold a re-run of scripts/design_passes.py (shards tagged e.g. V2_) into the design inputs (R2026.34).

For every project of the re-run (its targets CSV):
  design/backup_pre_statement_check/design_reading_all.parquet   the unchecked study table: the project's row is replaced by
                                                                 the re-run row (the old table is kept as *_pre_<tag>.parquet)
  design/design_sample_groups_all.parquet                        the project's rows are replaced by the re-run's
  deep/deep_determinations_shard_GRPD<k>.parquet (first run)     the project's rows are removed (backup in
                                                                 design/backup_pre_<tag>/); the re-run's GRPD<tag>* shards stay
  design/statement_check_void.txt                                the projects, so their earlier statement checks no longer apply
Then run scripts/design_statement_check.py --projects <targets> --tag <tag> on the re-run's statements and --apply.
Prints counts only.

usage: python scripts/merge_design_rerun.py --targets data/inputs/gut/design/design_targets_r2026_34_v2.csv --tag V2_
"""
import argparse, glob, json, os, shutil

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUT = os.path.join(ROOT, "data", "inputs", "gut")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", required=True)
    ap.add_argument("--tag", required=True)
    a = ap.parse_args(argv)
    D = os.path.join(GUT, "design")
    ids = set(pd.read_csv(a.targets).study_accession.astype(str))
    new = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(D, f"design_reading_shard_D{a.tag}*.parquet")))], ignore_index=True)
    new = new.drop_duplicates("study_accession", keep="last")
    new = new[new.study_accession.isin(ids)]   # R2026.40: only the targets named (the shards may hold more projects than are merged)
    bk = os.path.join(D, f"backup_pre_{a.tag.strip('_')}")
    os.makedirs(bk, exist_ok=True)
    out = {"rerun_projects": len(ids), "rerun_rows": len(new)}
    # unchecked study table
    up = os.path.join(D, "backup_pre_statement_check", "design_reading_all.parquet")
    base = up if os.path.exists(up) else os.path.join(D, "design_reading_all.parquet")
    old = pd.read_parquet(base)
    shutil.copy2(base, os.path.join(bk, "design_reading_all_unchecked.parquet"))
    keep = old[~old.study_accession.isin(set(new.study_accession))]
    R = pd.concat([keep, new], ignore_index=True)
    for c_ in R.columns:   # keep each column one type
        if R[c_].dtype == object and R[c_].map(lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)).any() and R[c_].map(lambda v: isinstance(v, str)).any():
            R[c_] = R[c_].map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v))
    os.makedirs(os.path.dirname(up), exist_ok=True)
    R.to_parquet(up, index=False)
    R.to_parquet(os.path.join(D, "design_reading_all.parquet"), index=False)   # unchecked until --apply of the statement check
    out.update(study_rows=len(R), replaced=int(old.study_accession.isin(set(new.study_accession)).sum()))
    # sample groups
    sg_p = os.path.join(D, "design_sample_groups_all.parquet")
    sg = pd.read_parquet(sg_p)
    shutil.copy2(sg_p, os.path.join(bk, "design_sample_groups_all.parquet"))
    sgn = [pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(D, f"design_sample_groups_shard_D{a.tag}*.parquet")))]
    sgn = pd.concat([x for x in sgn if len(x)], ignore_index=True) if any(len(x) for x in sgn) else sg.iloc[0:0]
    sgn = sgn[sgn.study_accession.isin(ids)]
    sg2 = pd.concat([sg[~sg.study_accession.isin(ids)], sgn], ignore_index=True)
    sg2.to_parquet(sg_p, index=False)
    out.update(sample_group_rows_removed=int(sg.study_accession.isin(ids).sum()), sample_group_rows_added=len(sgn))
    # first-run per-sample rows of the re-run projects
    removed = 0
    for f in sorted(glob.glob(os.path.join(GUT, "deep", "deep_determinations_shard_GRPD*.parquet"))):
        b = os.path.basename(f)
        if b.startswith(f"deep_determinations_shard_GRPD{a.tag}"):
            continue
        d = pd.read_parquet(f)
        m = d.study_accession.isin(ids)
        if m.any():
            shutil.copy2(f, os.path.join(bk, b))
            d[~m].to_parquet(f, index=False)
            removed += int(m.sum())
    out["first_run_sample_rows_removed"] = removed
    out["rerun_sample_rows"] = int(sum(len(pd.read_parquet(f)) for f in glob.glob(os.path.join(GUT, "deep", f"deep_determinations_shard_GRPD{a.tag}*.parquet"))))
    vp = os.path.join(D, "statement_check_void.txt")
    prev = set(x.strip() for x in open(vp) if x.strip()) if os.path.exists(vp) else set()
    open(vp, "w").write("\n".join(sorted(prev | ids)) + "\n")
    out["status_rerun"] = new.status.value_counts().to_dict()
    out["statements_rerun"] = int(new.design_statement.notna().sum())
    out["tiebreak"] = new.shape_tiebreak.value_counts().to_dict() if "shape_tiebreak" in new.columns else {}
    print(json.dumps(out, default=str))


if __name__ == "__main__":
    main()
