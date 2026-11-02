#!/usr/bin/env python
"""apply_persontime_filters.py — deterministic filters on the person / time pass outputs (R2026.34 production audit), then
copy them into the build inputs.

F1  an AGE time axis without repeated persons: a numeric time key named like an age (age, age_weeks, host_age, ...) in a
    project where the written subject ids (or the catalog's) give no person two samples -> its time rows are dropped
    (audit: PRJNA838641, 17 girls sampled once, age in years written as the time axis).
F2  a signed time code folded onto its unsigned twin by the letter/digit tokenizer ('W-2' two weeks BEFORE, 'W2' two weeks
    after -> both 'W_2'): rows whose raw source value carries the sign are dropped (audit: PRJNA625520).
F3  projects named by the audit: all time rows withheld (data/inputs/gut/design/persontime_withhold.csv: study_accession,
    scope [time|all], reason).
Reads  --stage (the pass's --out of the production run): design/persontime_reading_shard_PT*.parquet,
       deep/deep_determinations_shard_PTD*.parquet, timing/timing_samples_pt_shard_*.parquet
Writes data/inputs/gut/deep/deep_determinations_shard_PTD<k>.parquet, data/inputs/gut/timing/timing_samples_pt_shard_<k>.parquet,
       data/inputs/gut/design/persontime_reading_all.parquet, data/inputs/gut/design/persontime_filter_log.csv
Prints counts only.

usage: python scripts/apply_persontime_filters.py --stage ~/catalog/cache/persontime_prod
"""
import argparse, glob, json, os, re

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUT = os.path.join(ROOT, "data", "inputs", "gut")
AGE_KEY = re.compile(r"(?i)(^|[^a-z])age([^a-z]|$)|age_(weeks|days|months|years)|_age$|^age")
SIGNED = re.compile(r"[A-Za-z][-−][0-9]")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    a = ap.parse_args(argv)
    S = os.path.expanduser(a.stage)
    wh_p = os.path.join(GUT, "design", "persontime_withhold.csv")
    wh = pd.read_csv(wh_p) if os.path.exists(wh_p) else pd.DataFrame(columns=["study_accession", "scope", "reason"])
    w_time = set(wh.study_accession[wh.scope.isin(["time", "all"])])
    w_all = set(wh.study_accession[wh.scope == "all"])
    log = []
    deep = {f: pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(S, "deep", "deep_determinations_shard_PTD*.parquet")))}
    tim = {f: pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(S, "timing", "timing_samples_pt_shard_*.parquet")))}
    D = pd.concat(deep.values(), ignore_index=True) if deep else pd.DataFrame()
    Tm = pd.concat(tim.values(), ignore_index=True) if tim else pd.DataFrame()
    # persons per project after the pass: written ids, else the catalog's
    w = pd.read_parquet(os.path.join(ROOT, "build", "package", "catalog_sample_metadata_wide.parquet"), columns=["study_accession", "sample_key", "subject_id"],
                        filters=[("study_accession", "in", sorted(set(Tm.study_accession) | set(D.study_accession)))]) if len(Tm) or len(D) else pd.DataFrame()
    subj = dict(zip(w.sample_key, w.subject_id)) if len(w) else {}
    for r in D[D.field_name == "subject_id"].itertuples():
        subj[r.sample_key] = r.value_normalized
    def repeated(acc):
        ks = w.sample_key[w.study_accession == acc] if len(w) else []
        v = pd.Series([subj.get(k) for k in ks]).dropna()
        return bool(len(v)) and int(v.value_counts().max()) >= 2
    drop_time = {}   # acc -> reason (whole project's time rows)
    for acc, g in Tm.groupby("study_accession"):
        src = g.code_source.astype(str)
        if src.str.startswith("numeric:").all() and src.map(lambda s: bool(AGE_KEY.search(s.split(":", 1)[1]))).all() and not repeated(acc):
            drop_time[acc] = "F1_age_axis_without_repeats"
    for acc in w_time:
        drop_time.setdefault(acc, "F3_withheld_by_audit")
    # F2: signed codes folded by the fine tokenizer onto an unsigned twin: within one project, one time code whose raw values
    # include both a signed ('W-2') and an unsigned ('W2') form -> the signed samples' rows are dropped
    fr = []
    lab = D[(D.field_name == "timepoint_label") & D.evidence_locator.astype(str).str.contains(":fine:")] if len(D) else D
    for r in lab.itertuples():
        fr.append((r.study_accession, r.sample_key, str(r.field_value), str(r.evidence_quote).split(" = ", 1)[-1]))
    fine = Tm[Tm.code_source.astype(str).str.contains(":fine:")] if len(Tm) else Tm
    if len(fine):
        at = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "registry", "registry_biosample_attributes.parquet"),
                             columns=["sample_acc", "study_accession", "attr_key", "attr_value"], filters=[("study_accession", "in", sorted(set(fine.study_accession)))])
        fk = fine.assign(attr_key=fine.code_source.astype(str).str.split(":").str[1])[["study_accession", "sample_key", "attr_key", "code"]].drop_duplicates()
        jj = fk.merge(at.rename(columns={"sample_acc": "sample_key"})[["sample_key", "attr_key", "attr_value"]], on=["sample_key", "attr_key"], how="left")
        fr += list(zip(jj.study_accession, jj.sample_key, jj.code.astype(str), jj.attr_value.astype(str)))
    F = pd.DataFrame(fr, columns=["study_accession", "sample_key", "code", "raw"]).drop_duplicates("sample_key")
    signed_keys = set()
    if len(F):
        F["signed"] = F.raw.map(lambda v: bool(SIGNED.search(v)))
        g2 = F.groupby(["study_accession", "code"]).signed.agg(["any", "all"])
        clash = set(g2.index[g2["any"] & ~g2["all"]])
        signed_keys = set(F.sample_key[F.signed & pd.Series(list(zip(F.study_accession, F.code)), index=F.index).isin(clash)])
    # offsets of the same samples (their code_source is a fine split too)
    for f, d in list(deep.items()):
        m_time = d.field_name.eq("timepoint_label") & d.study_accession.isin(drop_time)
        m_all = d.study_accession.isin(w_all)
        m_sig = d.field_name.eq("timepoint_label") & d.sample_key.isin(signed_keys)
        m = m_time | m_all | m_sig
        for acc, n in d[m].groupby("study_accession").size().items():
            log.append(dict(study_accession=acc, table="deep", rows_dropped=int(n),
                            reason=drop_time.get(acc) or ("F3_withheld_by_audit" if acc in w_all else "F2_signed_code_folded")))
        deep[f] = d[~m]
    for f, t in list(tim.items()):
        m = t.study_accession.isin(drop_time) | t.study_accession.isin(w_all) | (t.code_source.astype(str).str.contains(":fine:") & t.sample_key.isin(signed_keys))
        for acc, n in t[m].groupby("study_accession").size().items():
            log.append(dict(study_accession=acc, table="timing", rows_dropped=int(n),
                            reason=drop_time.get(acc) or ("F3_withheld_by_audit" if acc in w_all else "F2_signed_code_folded")))
        tim[f] = t[~m]
    os.makedirs(os.path.join(GUT, "deep"), exist_ok=True)
    os.makedirs(os.path.join(GUT, "timing"), exist_ok=True)
    for f, d in deep.items():
        d.to_parquet(os.path.join(GUT, "deep", os.path.basename(f)), index=False)
    for f, t in tim.items():
        t.to_parquet(os.path.join(GUT, "timing", os.path.basename(f)), index=False)
    R = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(S, "design", "persontime_reading_shard_PT*.parquet")))], ignore_index=True)
    for c_ in R.columns:
        if R[c_].dtype == object and R[c_].map(lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)).any() and R[c_].map(lambda v: isinstance(v, str)).any():
            R[c_] = R[c_].map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v))
    R["filter"] = R.study_accession.map(lambda x: drop_time.get(x) or ("F3_withheld_by_audit" if x in w_all else None))
    R.drop_duplicates("study_accession", keep="last").to_parquet(os.path.join(GUT, "design", "persontime_reading_all.parquet"), index=False)
    L = pd.DataFrame(log, columns=["study_accession", "table", "rows_dropped", "reason"])
    L.to_csv(os.path.join(GUT, "design", "persontime_filter_log.csv"), index=False)
    Dn = pd.concat(deep.values(), ignore_index=True) if deep else pd.DataFrame()
    Tn = pd.concat(tim.values(), ignore_index=True) if tim else pd.DataFrame()
    print(json.dumps({"dropped": L.groupby("reason").rows_dropped.sum().to_dict() if len(L) else {}, "projects_filtered": int(L.study_accession.nunique()) if len(L) else 0,
                      "kept_det_rows": Dn.field_name.value_counts().to_dict() if len(Dn) else {}, "kept_offsets": len(Tn),
                      "projects_subject": int(Dn[Dn.field_name == "subject_id"].study_accession.nunique()) if len(Dn) else 0,
                      "projects_offsets": int(Tn.study_accession.nunique()) if len(Tn) else 0}))


if __name__ == "__main__":
    main()
