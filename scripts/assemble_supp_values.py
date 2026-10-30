"""assemble_supp_values.py — per-sample values from the joined supplementary tables (route R2, R2026.30 stage 7).

Inputs: the long rows from extract_supp_rows.py, the column classification from classify_supp_columns.py, and the
value map from map_attribute_pairs.py. Deterministic; no model calls.

Coded fields (body_site, age, sex, country, disease) take the mapped code. Text fields (subject_id, timepoint_label,
study_arm, location_locality, ethnicity_or_population) keep the table's own value. bmi and collection_date are parsed
numerically and range-checked. The remaining classified fields (diet, smoking_status, antibiotic_exposure,
intervention) are kept as DETAIL text only — they are not coded here, because a wrong code is worse than none.

One value per (sample, field): when two columns of a project disagree, neither is kept and the pair is counted as a
conflict. Every row carries the table and column it came from, so the site can show the evidence.

Cross-check against the archive: where a sample has an archive value for the same field, the two must agree. A
(project, field) whose values disagree on more than 10 % of at least 10 overlapping samples is dropped — a join that
is wrong where it can be checked is not trusted where it cannot. A project failing on more than a quarter of the
checkable values of any field is dropped entirely.

usage: python scripts/assemble_supp_values.py ROWS_GLOB COLUMNS.parquet PAIRS.parquet OUT.parquet [ARCHIVE.parquet]
"""
import glob, json, re, sys

import numpy as np
import pandas as pd

CODED = {"body_site": "body_site_code", "age": "age_days", "sex": "sex", "country": "country_iso2",
         "disease": "condition_code"}
TEXT = ["subject_id", "timepoint_label", "study_arm", "location_locality", "ethnicity_or_population"]
DETAIL = ["diet", "smoking_status", "antibiotic_exposure", "intervention"]


CHECK = {"sex": "sex", "age_days": "age_days", "condition_code": "condition_code",
         "body_site_code": "body_site_code", "country_iso2": "country_iso2"}


def cross_check(o, archive_path, stats):
    """Drop (project, field) groups whose values disagree with the archive where both exist."""
    av = pd.read_parquet(archive_path).drop_duplicates("sample_accession").set_index("sample_accession")
    drop_pf, drop_proj = set(), set()
    for fld, col in CHECK.items():
        if col not in av.columns:
            continue
        part = o[o["field_name"] == fld]
        idx = part["sample_key"]
        have = av[col].reindex(idx)
        m = have.notna().values
        if m.sum() < 1:
            continue
        a = have[m].astype(str).str.lower().values
        b = part.loc[m, "value"].astype(str).str.lower().values
        if fld == "age_days":
            ok = (pd.to_numeric(a, errors="coerce") - pd.to_numeric(b, errors="coerce")).__abs__() <= 31
        else:
            ok = a == b
        chk = pd.DataFrame({"proj": part.loc[m, "study_accession"].values, "ok": ok})
        rate = chk.groupby("proj")["ok"].agg(["mean", "size"])
        for pr, r in rate.iterrows():
            if r["size"] >= 10 and r["mean"] < 0.9:
                drop_pf.add((pr, fld))
                if r["mean"] < 0.75:
                    drop_proj.add(pr)
        stats.setdefault("cross_check", {})[fld] = {"checked": int(m.sum()), "agreement": round(float(ok.mean()), 3)}
    stats["dropped_project_field"] = sorted(f"{p}:{f}" for p, f in drop_pf)
    stats["dropped_projects"] = sorted(drop_proj)
    keep = ~(o.set_index(["study_accession", "field_name"]).index.isin(drop_pf)) & \
           ~o["study_accession"].isin(drop_proj)
    return o[keep]


def main(rows_glob, cols_path, pairs_path, out, archive=None):
    rows = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(rows_glob))], ignore_index=True)
    cc = pd.read_parquet(cols_path)
    cc = cc[(cc["field"].notna()) & (cc["field"] != "none") & (cc["confidence"] >= 0.6)]
    df = rows.merge(cc[["attr_key_norm", "field", "unit"]], on="attr_key_norm", how="inner")
    pm = pd.read_parquet(pairs_path)

    out_rows, stats = [], {"input_rows": int(len(df)), "conflicts": 0}
    # ---- coded fields
    key = ["field", "attr_key_norm", "attr_value"]
    pm = pm[[c for c in pm.columns if c in key + list(CODED.values()) + ["life_stage", "null_output", "confidence"]]]
    m = df.merge(pm, on=key, how="left")
    for fld, col in CODED.items():
        part = m[(m["field"] == fld) & m[col].notna()]
        if "null_output" in part.columns:
            part = part[part["null_output"] != True]  # noqa: E712 — tri-state column
        for r in part.itertuples():
            out_rows.append({"sample_key": r.sample_key, "study_accession": r.study_accession, "field_name": col,
                             "value": getattr(r, col), "source": r.source, "route": "R2",
                             "confidence": min(float(getattr(r, "confidence", 0.8) or 0.8), 0.85)})
        if fld == "age":
            ls = part[part["life_stage"].notna()] if "life_stage" in part.columns else part.iloc[0:0]
            for r in ls.itertuples():
                out_rows.append({"sample_key": r.sample_key, "study_accession": r.study_accession,
                                 "field_name": "life_stage", "value": r.life_stage, "source": r.source,
                                 "route": "R2", "confidence": 0.8})
    # ---- text fields
    for fld in TEXT:
        part = df[df["field"] == fld]
        for r in part.itertuples():
            out_rows.append({"sample_key": r.sample_key, "study_accession": r.study_accession, "field_name": fld,
                             "value": str(r.attr_value)[:120], "source": r.source, "route": "R2", "confidence": 0.8})
    # ---- numeric / date
    for fld, col, lo, hi in [("bmi", "bmi", 10, 80)]:
        part = df[df["field"] == fld].copy()
        part["v"] = pd.to_numeric(part["attr_value"], errors="coerce")
        part = part[(part["v"] >= lo) & (part["v"] <= hi)]
        for r in part.itertuples():
            out_rows.append({"sample_key": r.sample_key, "study_accession": r.study_accession, "field_name": col,
                             "value": r.v, "source": r.source, "route": "R2", "confidence": 0.8})
    part = df[df["field"] == "collection_date"].copy()
    part["y"] = part["attr_value"].astype(str).str.extract(r"((?:19|20)\d{2})")[0]
    part = part[part["y"].notna()]
    for r in part.itertuples():
        out_rows.append({"sample_key": r.sample_key, "study_accession": r.study_accession,
                         "field_name": "collection_year", "value": int(r.y), "source": r.source, "route": "R2",
                         "confidence": 0.8})
    # ---- detail-only fields
    for fld in DETAIL:
        part = df[df["field"] == fld]
        for r in part.itertuples():
            out_rows.append({"sample_key": r.sample_key, "study_accession": r.study_accession,
                             "field_name": f"{fld}_detail", "value": str(r.attr_value)[:160], "source": r.source,
                             "route": "R2", "confidence": 0.7})

    o = pd.DataFrame(out_rows)
    if len(o):
        o["value"] = o["value"].astype(str)
        n = o.groupby(["sample_key", "field_name"])["value"].transform("nunique")
        stats["conflicts"] = int((n > 1).groupby([o["sample_key"], o["field_name"]]).first().sum())
        o = o[n == 1].drop_duplicates(["sample_key", "field_name"])
    if archive and len(o):
        o = cross_check(o, archive, stats)
    o.to_parquet(out, index=False)
    stats.update(rows=int(len(o)), samples=int(o["sample_key"].nunique()) if len(o) else 0,
                 projects=int(o["study_accession"].nunique()) if len(o) else 0,
                 by_field=o["field_name"].value_counts().to_dict() if len(o) else {})
    print(json.dumps(stats))


if __name__ == "__main__":
    main(*sys.argv[1:6])
