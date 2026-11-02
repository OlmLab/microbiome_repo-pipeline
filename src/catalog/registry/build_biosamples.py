"""Registry sample tier (scale-up S2, R2026.5): registry_biosamples.parquet + study roll-up columns for registry_studies.

Inputs
  --attributes  registry_biosample_attributes.parquet — one row per (sample, attribute key, value) from the ENA/NCBI BioSample
                harvest (columns sample_acc, acc_resolved, study_accession, attr_key, attr_key_norm, attr_value, attr_units,
                source, field, is_placeholder). `field` is the R1 target field assigned by the harvest key map
                (age | sex | body_site | disease | country | collection_date | null).
  --norm        normalised distinct (field, attr_key_norm, attr_value) pairs from the utility-model pass: columns field,
                attr_key_norm, attr_value, body_site_code, life_stage, age_days, sex, country_iso2, confidence, null_output.
  --studies     registry_studies.parquet as written by build_registry (study-level classification).

Outputs (--out dir)
  registry_biosamples.parquet   one row per harvested BioSample with the normalised R1 fields + raw provenance
  registry_studies.parquet      the input studies table with the roll-up columns filled and, where the study-level site /
                                life stage was unknown and the samples are decisive, the primary value refined
                                (evidence row source = sample.attr.<key>, quote = "<value> (n=<k>/<n>)").
Deterministic; no LLM calls here (the normalisation map is an input).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import pandas as pd

MIN_CONF = 0.5
SHARE_ADD = 0.10          # a sample-derived code joins the study's body_sites / life_stages list at >= 10 % of harvested samples (>= 3 samples)
MAX_AGE_DAYS = 120 * 365.25   # ages above 120 years are unit errors
SHARE_PRIMARY = 0.60      # ... and replaces an unknown_* primary at >= 60 %
ROLLUP_COLS = ["n_biosamples_harvested", "n_biosamples_with_site", "n_biosamples_with_age", "n_biosamples_with_sex",
               "sample_body_sites", "sample_life_stages", "sample_countries", "sample_age_days_median"]
UNKNOWN = {"body_site_code": "unknown_site", "life_stage": "unknown_age", "sex": "unknown"}
YEAR_RE = re.compile(r"(1[89]\d\d|20\d\d)")


def _pick(df: pd.DataFrame, code_col: str) -> pd.DataFrame:
    """Best normalised value per sample for one field: highest confidence, then the pair seen in most samples."""
    d = df[df[code_col].notna() & (df[code_col] != UNKNOWN.get(code_col, "")) & (df["confidence"].fillna(0) >= MIN_CONF)]
    d = d.sort_values(["sample_acc", "confidence", "n_samples"], ascending=[True, False, False]).drop_duplicates("sample_acc")
    return d


def build_biosamples(att: pd.DataFrame, norm: pd.DataFrame) -> pd.DataFrame:
    att = att[~att["is_placeholder"].astype(bool)].copy()
    samples = att[["sample_acc", "study_accession"]].drop_duplicates("sample_acc").set_index("sample_acc")
    n_attr = att.groupby("sample_acc").size()
    src = att.groupby("sample_acc")["source"].first()
    out = pd.DataFrame({"sample_accession": samples.index, "study_accession": samples["study_accession"].values})
    out["n_attributes"] = out["sample_accession"].map(n_attr).fillna(0).astype(int)
    out["source"] = out["sample_accession"].map(src)
    tgt = att[att["field"].notna()]
    pair_n = norm.groupby(["field", "attr_key_norm", "attr_value"]).size().rename("n_samples").reset_index()
    nm = norm.merge(pair_n, on=["field", "attr_key_norm", "attr_value"], how="left") if "n_samples" not in norm.columns else norm
    joined = tgt.merge(nm, on=["field", "attr_key_norm", "attr_value"], how="left")
    for field, code_col, raw_prefix in (("body_site", "body_site_code", "body_site"), ("age", "life_stage", "age"),
                                        ("sex", "sex", "sex"), ("country", "country_iso2", "country")):
        f = joined[joined["field"] == field]
        best = _pick(f, code_col).set_index("sample_acc")
        out[code_col] = out["sample_accession"].map(best[code_col])
        out[f"{raw_prefix}_raw_key"] = out["sample_accession"].map(best["attr_key_norm"])
        out[f"{raw_prefix}_raw_value"] = out["sample_accession"].map(best["attr_value"])
        if field == "age":
            out["age_days"] = pd.to_numeric(out["sample_accession"].map(best["age_days"]), errors="coerce")
            out.loc[out["age_days"] > MAX_AGE_DAYS, "age_days"] = float("nan")   # unit misread (e.g. months taken as years): keep the stage, drop the number
        # a sample that HAS a value for the field but no accepted code → the unknown code (distinct from "no attribute")
        has_any = set(f["sample_acc"])
        if code_col in UNKNOWN:
            mask = out[code_col].isna() & out["sample_accession"].isin(has_any)
            out[code_col] = out[code_col].astype(object)   # an all-missing column comes back float64 (R2026.34)
            out.loc[mask, code_col] = UNKNOWN[code_col]
    # collection date: deterministic (first non-placeholder value; year extracted)
    cd = tgt[tgt["field"] == "collection_date"].drop_duplicates("sample_acc").set_index("sample_acc")["attr_value"]
    out["collection_date_raw"] = out["sample_accession"].map(cd)
    out["collection_year"] = pd.to_numeric(out["collection_date_raw"].astype("string").str.extract(YEAR_RE, expand=False), errors="coerce").astype("Int64")
    dis = tgt[tgt["field"] == "disease"].drop_duplicates("sample_acc").set_index("sample_acc")["attr_value"]
    out["disease_raw"] = out["sample_accession"].map(dis).astype("string").str.slice(0, 200)
    out["life_stage"] = out["life_stage"].where(out["life_stage"].notna() | out["age_days"].isna(), out["age_days"].map(_stage_from_days))
    return out.sort_values("sample_accession", kind="mergesort").reset_index(drop=True)


def _stage_from_days(d):
    if pd.isna(d):
        return None
    if d <= 28:
        return "neonate"
    if d <= 1100:
        return "infant"
    if d < 12 * 365.25:
        return "child"
    if d < 18 * 365.25:
        return "adolescent"
    if d < 65 * 365.25:
        return "adult"
    return "elderly"


def _counts_json(s: pd.Series, top: int | None = None) -> str:
    vc = s.dropna().value_counts()
    if top:
        vc = vc.head(top)
    return json.dumps({str(k): int(v) for k, v in vc.items()}, ensure_ascii=False)


def rollup_studies(studies: pd.DataFrame, bios: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    st = studies.copy()
    for c in ROLLUP_COLS:
        if c not in st.columns:
            st[c] = None
    g = bios.groupby("study_accession")
    agg = pd.DataFrame({
        "n_biosamples_harvested": g.size(),
        "n_biosamples_with_site": g["body_site_code"].apply(lambda s: int(s.notna().sum() - (s == "unknown_site").sum())),
        "n_biosamples_with_age": g["life_stage"].apply(lambda s: int(s.notna().sum() - (s == "unknown_age").sum())),
        "n_biosamples_with_sex": g["sex"].apply(lambda s: int(s.isin(["female", "male"]).sum())),
        "sample_body_sites": g["body_site_code"].apply(_counts_json),
        "sample_life_stages": g["life_stage"].apply(_counts_json),
        "sample_countries": g["country_iso2"].apply(lambda s: _counts_json(s, top=5)),
        "sample_age_days_median": g["age_days"].median(),
    })
    st = st.set_index("study_accession")
    common = st.index.intersection(agg.index)
    for c in ROLLUP_COLS:
        st.loc[common, c] = agg.loc[common, c]
    for c in ("n_biosamples_harvested", "n_biosamples_with_site", "n_biosamples_with_age", "n_biosamples_with_sex"):
        st[c] = pd.to_numeric(st[c], errors="coerce").fillna(0).astype(int)
    st["sample_age_days_median"] = pd.to_numeric(st["sample_age_days_median"], errors="coerce").round(1)
    for c in ("sample_body_sites", "sample_life_stages", "sample_countries"):
        st[c] = st[c].where(st[c].notna(), "{}")
    # ---- refinement of the study-level lists / primaries from decisive sample evidence
    stats = {"sites_added": 0, "stages_added": 0, "site_primary_refined": 0, "stage_primary_refined": 0}
    raw_key = {"body_site": bios.groupby("study_accession")["body_site_raw_key"].agg(lambda s: s.dropna().mode().iat[0] if s.notna().any() else "host_body_site"),
               "age": bios.groupby("study_accession")["age_raw_key"].agg(lambda s: s.dropna().mode().iat[0] if s.notna().any() else "host_age")}
    raw_val = {"body_site": bios.groupby(["study_accession", "body_site_code"])["body_site_raw_value"].agg(lambda s: s.dropna().mode().iat[0] if s.notna().any() else ""),
               "age": bios.groupby(["study_accession", "life_stage"])["age_raw_value"].agg(lambda s: s.dropna().mode().iat[0] if s.notna().any() else "")}
    for acc in common:
        n = int(st.at[acc, "n_biosamples_harvested"])
        if n < 3:
            continue
        for list_col, prim_col, ev_col, json_col, unk, kind in (("body_sites", "body_site_primary", "body_site_evidence", "sample_body_sites", "unknown_site", "body_site"),
                                                                ("life_stages", "life_stage_primary", "life_stage_evidence", "sample_life_stages", "unknown_age", "age")):
            counts = {k: v for k, v in json.loads(st.at[acc, json_col]).items() if not k.startswith("unknown")}
            if not counts:
                continue
            have = [p for p in str(st.at[acc, list_col] or "").split(";") if p]
            ev = json.loads(st.at[acc, ev_col] or "[]") if isinstance(st.at[acc, ev_col], str) else []
            changed = False
            for code, k in sorted(counts.items(), key=lambda kv: -kv[1]):
                if k >= 3 and k / n >= SHARE_ADD and code not in have:
                    have = [p for p in have if not p.startswith("unknown")] + [code]
                    q = str(raw_val[kind].get((acc, code), ""))[:60]
                    words = q.split()
                    ev.append({"source": f"sample.attr.{raw_key[kind].get(acc, 'attr')}", "quote": " ".join(words[:8]) + f" (n={k}/{n})"})
                    stats["sites_added" if kind == "body_site" else "stages_added"] += 1
                    changed = True
            top_code, top_k = max(counts.items(), key=lambda kv: kv[1])
            if (pd.isna(st.at[acc, prim_col]) or st.at[acc, prim_col] in (unk, "", None)) and top_k >= 3 and top_k / n >= SHARE_PRIMARY:
                st.at[acc, prim_col] = top_code
                stats["site_primary_refined" if kind == "body_site" else "stage_primary_refined"] += 1
                changed = True
            if changed:
                st.at[acc, list_col] = ";".join(have) if have else unk
                st.at[acc, ev_col] = json.dumps(ev, ensure_ascii=False)
    st = st.reset_index()
    return st, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attributes", required=True), ap.add_argument("--norm", required=True), ap.add_argument("--studies", required=True)
    ap.add_argument("--out", required=True), ap.add_argument("--release-id", default="R2026.5"), ap.add_argument("--package-version", default="1.7.0")
    ap.add_argument("--previous", help="registry_biosamples.parquet of the previous package (release columns carried)")
    ap.add_argument("--summary", help="where to write the JSON summary (outside the package dir)")
    a = ap.parse_args(argv)
    att = pd.read_parquet(a.attributes)
    norm = pd.read_parquet(a.norm)
    studies = pd.read_parquet(a.studies)
    bios = build_biosamples(att, norm)
    bios["release_added"], bios["release_retired"], bios["package_added"] = a.release_id, None, a.package_version
    if a.previous and os.path.exists(a.previous):
        from catalog.registry.build_registry import carry_release_columns
        bios = carry_release_columns(bios, pd.read_parquet(a.previous), ["sample_accession"], a.release_id)
    st, stats = rollup_studies(studies, bios[bios["release_retired"].isna()])
    if "scope_memberships" in st.columns:  # memberships follow the (possibly refined) lists
        from catalog.registry.build_registry import derive_scope_memberships
        st["scope_memberships"] = [";".join(derive_scope_memberships(h, b.split(";") if b else [], l.split(";") if l else [], asy, i))
                                   for h, b, l, asy, i in zip(st.host_human, st.body_sites.fillna(""), st.life_stages.fillna(""), st.assay, st.in_infant_catalog)]
    os.makedirs(a.out, exist_ok=True)
    bios.to_parquet(os.path.join(a.out, "registry_biosamples.parquet"), index=False)
    st.to_parquet(os.path.join(a.out, "registry_studies.parquet"), index=False)
    summary = {"n_biosamples": len(bios), "n_studies_with_samples": int(bios.study_accession.nunique()),
               "site_coverage": round(float(bios.body_site_code.notna().mean() - (bios.body_site_code == "unknown_site").mean()), 4),
               "age_coverage": round(float(bios.life_stage.notna().mean() - (bios.life_stage == "unknown_age").mean()), 4),
               "sex_coverage": round(float(bios.sex.isin(["female", "male"]).mean()), 4),
               "country_coverage": round(float(bios.country_iso2.notna().mean()), 4), **stats}
    print(json.dumps(summary))
    if a.summary:
        os.makedirs(os.path.dirname(a.summary) or ".", exist_ok=True)
        json.dump(summary, open(a.summary, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
