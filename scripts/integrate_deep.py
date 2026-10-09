"""integrate_deep.py — validate the deep per-sample annotation leaf outputs (R2026.18) and stage them for build_gut_scope.

Inputs: deep_determinations_shard_*.parquet and deep_sources_shard_*.parquet (one pair per leaf) in --in-dir.
Outputs in --out-dir (default data/inputs/gut/deep): the same shard files, filtered, plus DEEP_QA.json and
deep_rejected.parquet (every dropped row with the reason).

Row checks (deterministic):
  * field_name must be a pack field (config/packs/gut.yaml); scope forced to 'sample'; src_track 'gut_all_v1'
  * value_normalized must be non-empty and not 'unknown'
  * enum / vocabulary fields must use a valid code (sex, antibiotic_exposure, health_condition, lifestyle, diet, smoking_status,
    medication, intervention incl. placebo / no_intervention); country = ISO-3166 alpha-2 (two upper-case letters)
  * numeric fields in range: bmi 10–80, age 0–43,800 days (120 y), latitude / longitude, Bristol 1–7, gestational age 20–45 wk,
    birth weight 300–6,000 g
  * evidence_source and evidence_quote present; confidence clipped to <= 0.9
  * one row per (sample_key, field_name, route): the highest confidence wins
  * gap-fill only against the published release (--wide): a new row is kept when the sample has no value for the field, or when
    the published value comes from a lower-ranked route (R1 > R2 > R3 > R4; e.g. a supplementary-table value replaces a cohort-wide
    R3/R4 statement). Rows that duplicate or contradict an equal-or-better published value are dropped (counted as agree / disagree).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

import pandas as pd
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DET_COLS = ["sample_key", "field_name", "study_accession", "field_value", "value_normalized", "confidence", "evidence_source", "evidence_locator",
            "evidence_quote", "evidence_limited_to_abstract", "determined_by", "route", "scope", "parse_note", "group_audit", "src_track"]
RANGES = {"bmi": (10, 80), "age_at_collection_days": (0, 43800), "latitude": (-90, 90), "longitude": (-180, 180),
          "stool_consistency_bristol": (1, 7), "gestational_age_weeks": (20, 45), "birth_weight_grams": (300, 6000)}


def vocab_codes(name):
    p = os.path.join(REPO, "config", "vocab", name)
    if not os.path.exists(p):
        return None
    v = yaml.safe_load(open(p)) or {}
    c = v.get("codes", v)
    if isinstance(c, dict):
        return set(c.keys())
    return {x if isinstance(x, str) else x.get("code") for x in c}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", required=True)
    ap.add_argument("--out-dir", default=os.path.join(REPO, "data", "inputs", "gut", "deep"))
    ap.add_argument("--wide", default=os.path.join(REPO, "build", "package", "gut_sample_metadata_wide.parquet"))
    a = ap.parse_args()
    pack = yaml.safe_load(open(os.path.join(REPO, "config", "packs", "gut.yaml")))
    fields = set(pack["fields"].keys())
    voc = {"sex": {"female", "male"}, "antibiotic_exposure": {"yes", "no"},
           "health_condition": vocab_codes("health_conditions.yaml"), "lifestyle": vocab_codes("lifestyle.yaml"),
           "diet": vocab_codes("diet.yaml"), "smoking_status": vocab_codes("smoking.yaml")}
    med, iv = vocab_codes("medication.yaml"), (vocab_codes("interventions.yaml") or set()) | {"placebo", "no_intervention"}
    os.makedirs(a.out_dir, exist_ok=True)
    import pyarrow.parquet as pq
    wcols = pq.ParquetFile(a.wide).schema.names
    flist = [f for f in fields if f in wcols]
    w = pd.read_parquet(a.wide, columns=["sample_key", "release_retired"] + flist + [f + "__route" for f in flist if f + "__route" in wcols])
    w = w[w.release_retired.isna()]
    cur = {}
    for f in flist:
        has = w[f].notna() & (w[f].astype(str) != "unknown")
        rt = w[f + "__route"] if f + "__route" in w.columns else pd.Series("R1", index=w.index)
        cur[f] = pd.DataFrame({"sample_key": w.loc[has, "sample_key"].values, "cur_value": w.loc[has, f].astype(str).values, "cur_route": rt[has].fillna("R1").values})
    RANK = {"R1": 1, "R2": 2, "R3": 3, "R4": 4}
    qa, rej_all = {}, []
    for f in sorted(glob.glob(os.path.join(a.in_dir, "deep_determinations_shard_*.parquet"))):
        d = pd.read_parquet(f).reindex(columns=DET_COLS)
        n0 = len(d)
        reason = pd.Series("", index=d.index)
        v = d.value_normalized.astype("string").str.strip()
        reason[~d.field_name.isin(fields)] = "field_not_in_pack"
        reason[(reason == "") & (v.isna() | v.isin(["", "unknown", "nan", "None"]))] = "empty_or_unknown"
        for fld, codes in voc.items():
            if codes:
                m = (reason == "") & (d.field_name == fld) & ~v.isin(codes)
                reason[m] = "bad_code"
        for fld, codes in (("medication", med), ("intervention", iv)):
            if codes:
                m = (reason == "") & (d.field_name == fld)
                bad = m & ~v.fillna("").map(lambda s: bool(s) and all(x in codes for x in s.split(";")))
                reason[bad] = "bad_code"
        m = (reason == "") & (d.field_name == "country") & ~v.fillna("").str.fullmatch(r"[A-Z]{2}")
        reason[m] = "bad_country"
        for fld, (lo, hi) in RANGES.items():
            m = (reason == "") & (d.field_name == fld)
            num = pd.to_numeric(v.where(m), errors="coerce")
            reason[m & (num.isna() | (num < lo) | (num > hi))] = "out_of_range"
        reason[(reason == "") & (d.evidence_source.isna() | d.evidence_quote.isna())] = "no_evidence"
        rej = d[reason != ""].assign(reject_reason=reason[reason != ""], shard_file=os.path.basename(f))
        rej_all.append(rej)
        d = d[reason == ""].copy()
        d["value_normalized"] = d.value_normalized.astype(str).str.strip()
        d["scope"] = d.scope.where(d.scope.astype(str) == "study_all", "sample")   # cohort-wide rows (newly linked papers) stay study_all
        d["src_track"], d["group_audit"] = "gut_all_v1", None
        d["confidence"] = pd.to_numeric(d.confidence, errors="coerce").fillna(0.7).clip(upper=0.9)
        d["evidence_limited_to_abstract"] = False
        d = d.sort_values("confidence", ascending=False, kind="mergesort").drop_duplicates(["sample_key", "field_name", "route"])
        keep, agree, disagree = [], 0, 0
        for fld, g in d.groupby("field_name"):
            if fld not in cur:
                keep.append(g); continue
            mg = g.merge(cur[fld], on="sample_key", how="left")
            better = mg.cur_route.isna() | (mg.cur_route.map(RANK).fillna(9) > mg.route.map(RANK).fillna(9))
            same = mg.value_normalized.str.lower() == mg.cur_value.astype(str).str.lower()
            agree += int((~better & same).sum()); disagree += int((~better & ~same).sum())
            keep.append(mg[better].drop(columns=["cur_value", "cur_route"]))
        d = pd.concat(keep, ignore_index=True).reindex(columns=DET_COLS) if keep else d.iloc[0:0]
        d.to_parquet(os.path.join(a.out_dir, os.path.basename(f)), index=False)
        qa[os.path.basename(f)] = dict(rows_in=int(n0), rows_kept=int(len(d)), dup_agree=agree, dup_disagree=disagree, rejected=rej.reject_reason.value_counts().to_dict())
    for f in sorted(glob.glob(os.path.join(a.in_dir, "deep_sources_shard_*.parquet"))):
        pd.read_parquet(f).to_parquet(os.path.join(a.out_dir, os.path.basename(f)), index=False)
    rj = pd.concat(rej_all, ignore_index=True) if rej_all else pd.DataFrame()
    rj.to_parquet(os.path.join(a.out_dir, "deep_rejected.parquet"), index=False)
    tot = dict(rows_in=sum(x["rows_in"] for x in qa.values()), rows_kept=sum(x["rows_kept"] for x in qa.values()),
               rejected=rj.reject_reason.value_counts().to_dict() if len(rj) else {})
    json.dump(dict(total=tot, shards=qa), open(os.path.join(a.out_dir, "DEEP_QA.json"), "w"), indent=1)
    print(json.dumps(tot))


if __name__ == "__main__":
    main()
