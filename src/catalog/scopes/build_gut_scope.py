"""Curated scope `gut_all` (config/packs/gut.yaml): assemble gut_sample_determinations / gut_sample_metadata_wide / gut_studies.

Sources, in precedence order per (sample, field):
  0. the curated infant catalog (sample_determinations.parquet current rows of the 389 included studies; src_track infant_catalog) — wins
  1. R1 archive attributes: registry_biosamples (normalised age / sex / country) + gut_r1_determinations (bmi, antibiotics, subject,
     timepoint, health_condition_detail …)
  2. R2 supplementary tables (gut_r2_determinations_shard_*.parquet)
  3. R3 (full-text cohort statements, study_all; from 1.9.0)
  4. R4 abstract / study-description statements (gut_r4_determinations_shard_*.parquet, scope study_all) expanded to the study's samples
     that have no sample-level value for the field (confidence ≤ 0.5, evidence_limited_to_abstract = 1)
health_condition codes come from gut_health_condition_map (key, value → code) applied to health_condition_detail rows; antibiotic codes
from gut_antibiotic_map. Every row keeps the determination schema of the infant tables. Deterministic; no LLM calls.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import pandas as pd
import yaml

DET_COLS = ["sample_key", "field_name", "study_accession", "field_value", "value_normalized", "confidence", "evidence_source", "evidence_locator",
            "evidence_quote", "evidence_limited_to_abstract", "determined_by", "route", "scope", "parse_note", "group_audit", "src_track",
            "release_added", "release_retired", "package_added"]
# Field lists come from config/packs/gut.yaml (pack_fields(); 1.12.0) — the module-level names are kept for callers/tests and are
# refreshed from the pack when build() runs.
PACK_FIELDS: list = []  # filled after pack_fields() is defined (see _default_pack_fields)
INFANT_ONLY = ["delivery_mode", "feeding_mode", "preterm_status", "gestational_age_weeks", "birth_weight_grams", "maternal_antibiotics", "probiotic_exposure", "hmo_supplementation", "nec_status"]
ROUTE_RANK = {"R1": 1, "R2": 2, "R3": 3, "R4": 4}
SRC = "gut_all_v1"
STAGE_TO_CAT = {"neonate": "neonate", "infant": "infant", "child": "child", "adolescent": "adolescent", "adult": "adult", "elderly": "elderly"}


def _load_pack(cfg_dir):
    return yaml.safe_load(open(os.path.join(cfg_dir, "packs", "gut.yaml"), encoding="utf-8"))


def pack_fields(pack: dict) -> dict:
    """Field tiers from the pack: fields (determined per sample, in pack order), infant_only (derived_fields with infant_only: true),
    compose (derived_fields with `compose`), year_of (derived_fields with `from`), core, key, vocab (field → vocab yaml path)."""
    fields = list(pack["fields"].keys())
    derived = pack.get("derived_fields") or {}
    infant_only = [f for f, s in derived.items() if isinstance(s, dict) and s.get("infant_only")]
    compose = {f: s for f, s in derived.items() if isinstance(s, dict) and s.get("compose")}
    year_of = {f: s["from"] for f, s in derived.items() if isinstance(s, dict) and s.get("from")}
    vocab = {f: s["vocab"] for f, s in pack["fields"].items() if isinstance(s, dict) and s.get("type") == "vocab" and s.get("vocab")}
    # 1.13.0: `vocab_list` fields hold ';'-joined sorted codes (medication); `int` fields carry an inclusive [lo, hi] range (stool_consistency_bristol)
    vocab_list = {f: s["vocab"] for f, s in pack["fields"].items() if isinstance(s, dict) and s.get("type") == "vocab_list" and s.get("vocab")}
    int_range = {f: tuple(s.get("range") or (None, None)) for f, s in pack["fields"].items() if isinstance(s, dict) and s.get("type") == "int"}
    return dict(fields=fields, infant_only=infant_only, compose=compose, year_of=year_of, core=list(pack.get("core_fields") or []),
                key=list(pack.get("key_fields") or []), vocab=vocab, vocab_list=vocab_list, int_range=int_range)


def _default_pack_fields():
    """Field order from config/packs/gut.yaml (CATALOG_CONFIG_DIR or the repo config); falls back to the 1.12.0 list when unreadable."""
    fallback = ["age_at_collection_days", "sex", "bmi", "country", "health_condition", "health_condition_detail", "antibiotic_exposure", "subject_id", "timepoint_label",
                "collection_date", "location_region", "location_locality", "location_site", "latitude", "longitude", "lifestyle", "lifestyle_detail"]
    try:
        cfg = os.environ.get("CATALOG_CONFIG_DIR") or os.path.join(os.path.dirname(__file__), "..", "..", "..", "config")
        return pack_fields(_load_pack(cfg))["fields"]
    except Exception:
        return fallback


PACK_FIELDS = _default_pack_fields()


def compose_detailed_location(parts) -> str | None:
    """'site, locality, region' with empty parts dropped (the derived detailed_location column)."""
    vals = [str(p).strip() for p in parts if not pd.isna(p) and str(p).strip() not in ("", "None", "nan", "<NA>")]
    return ", ".join(vals) if vals else None


def collection_year(value) -> float | None:
    """Leading YYYY of an ISO partial collection_date (start of an interval) as a number, else None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    m = pd.Series([str(value)]).str.extract(r"^(\d{4})")[0].iloc[0]
    return float(m) if isinstance(m, str) else None


def load_vocab_codes(cfg_dir: str, rel_path: str) -> set:
    p = rel_path if os.path.isabs(rel_path) else os.path.join(cfg_dir, *rel_path.replace("config/", "", 1).split("/"))
    return set((yaml.safe_load(open(p, encoding="utf-8")).get("codes") or {}).keys())


def clean_vocab_list(value, codes: set) -> str | None:
    """';'-joined code list → sorted, de-duplicated list of the codes that are in the vocabulary (None when none remain)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    kept = sorted({c.strip() for c in str(value).split(";") if c.strip() in codes})
    return ";".join(kept) if kept else None


def clean_int(value, lo, hi) -> int | None:
    """Integer within the inclusive [lo, hi] range (also '4.0'); anything else → None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        x = float(str(value).strip())
    except ValueError:
        return None
    if not x.is_integer():
        return None
    n = int(x)
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        return None
    return n


def validate_vocab_fields(det: pd.DataFrame, pf: dict, cfg_dir: str) -> tuple[pd.DataFrame, dict]:
    """Rows of vocab-typed fields (health_condition, lifestyle, diet, smoking_status) whose normalised value is not a code of the field's
    vocabulary are dropped; vocab_list fields (medication) keep the valid codes of the list and drop the row when none remain; int fields
    (stool_consistency_bristol) must be integers within the pack range. The count per field is returned (infant-catalog rows are exempt:
    their legacy codes are in the vocab as legacy_infant)."""
    dropped = {}
    keep = pd.Series(True, index=det.index)
    det = det.copy()
    for f, vp in pf["vocab"].items():
        codes = load_vocab_codes(cfg_dir, vp)
        m = (det.field_name == f) & (det.src_track != "infant_catalog") & ~det.value_normalized.astype("string").isin(codes).fillna(False)
        dropped[f] = int(m.sum())
        keep &= ~m
    for f, vp in (pf.get("vocab_list") or {}).items():
        codes = load_vocab_codes(cfg_dir, vp)
        sel = (det.field_name == f) & (det.src_track != "infant_catalog")
        cleaned = det.loc[sel, "value_normalized"].map(lambda v: clean_vocab_list(v, codes))
        n_codes_before = det.loc[sel, "value_normalized"].astype(str).str.count(";").add(1).where(det.loc[sel, "value_normalized"].notna(), 0).sum()
        n_codes_after = cleaned.astype(str).str.count(";").add(1).where(cleaned.notna(), 0).sum()
        det.loc[sel, "value_normalized"] = cleaned
        m = sel & det.value_normalized.isna()
        dropped[f] = int(m.sum())
        dropped[f + "__codes"] = int(n_codes_before - n_codes_after)
        keep &= ~m
    for f, (lo, hi) in (pf.get("int_range") or {}).items():
        sel = (det.field_name == f) & (det.src_track != "infant_catalog")
        cleaned = det.loc[sel, "value_normalized"].map(lambda v: clean_int(v, lo, hi)).astype("Int64")  # None → <NA>, never the string 'nan'
        det.loc[sel, "value_normalized"] = cleaned.map(lambda n: None if pd.isna(n) else str(int(n)))
        m = sel & det.value_normalized.isna()
        dropped[f] = int(m.sum())
        keep &= ~m
    return det[keep], dropped


def sequencing_summary(gut_runs: pd.DataFrame) -> pd.DataFrame:
    """Study-level sequencing columns from gut_runs: n_runs_total, gbp_per_run_mean / median, instrument_models_top (JSON counts, top 5),
    library_layouts (JSON counts), sandpiper_profiled_share."""
    g = gut_runs.assign(_gbp=pd.to_numeric(gut_runs.base_count, errors="coerce") / 1e9).groupby("study_accession")
    out = pd.DataFrame({"n_runs_total": g.size(),
                        "gbp_per_run_mean": g._gbp.mean().round(3), "gbp_per_run_median": g._gbp.median().round(3),
                        "instrument_models_top": g.instrument_model.apply(lambda s: json.dumps(s.dropna().value_counts().head(5).to_dict())),
                        "library_layouts": g.library_layout.apply(lambda s: json.dumps(s.dropna().value_counts().to_dict())),
                        "sandpiper_profiled_share": g.sandpiper_profiled.apply(lambda s: round(float(s.astype(bool).mean()), 4) if len(s) else None)})
    per = gut_runs.dropna(subset=["sample_key"]).assign(_gbp=pd.to_numeric(gut_runs.base_count, errors="coerce") / 1e9).groupby(["study_accession", "sample_key"])._gbp.sum(min_count=1)
    out["gbp_per_sample_median"] = per[per > 0].groupby(level=0).median().round(3)
    return out


def _q12(v):
    s = "" if v is None else str(v)
    return " ".join(s.split()[:12])[:200]


def _row(sample, field, study, value, norm, conf, src, loc, quote, route, by, note="", scope="sample", limited=0, release_id="", pv=""):
    return dict(sample_key=sample, field_name=field, study_accession=study, field_value=str(value), value_normalized=None if norm is None else str(norm),
                confidence=float(conf), evidence_source=src, evidence_locator=loc, evidence_quote=_q12(quote), evidence_limited_to_abstract=float(limited),
                determined_by=by, route=route, scope=scope, parse_note=note, group_audit=None, src_track=SRC, release_added=release_id, release_retired=None, package_added=pv)


def age_category(days, pack):
    if days is None or pd.isna(days):
        return None
    for k, (lo, hi) in pack["age_categories"].items():
        if days >= lo and (hi is None or days <= hi):
            return k
    return None


def rows_from_registry_biosamples(bio: pd.DataFrame, release_id: str, pv: str) -> list[dict]:
    """R1 rows from the registry's normalised BioSample fields (evidence = the raw attribute the code came from)."""
    out = []
    for r in bio.itertuples(index=False):
        s, st = r.sample_accession, r.study_accession
        if pd.notna(r.age_days):
            out.append(_row(s, "age_at_collection_days", st, r.age_raw_value, round(float(r.age_days), 1), 0.85, f"sample.attr.{r.age_raw_key}", "biosample_attr", r.age_raw_value, "R1", "registry_norm_v1:age", "utility-model normalisation of the attribute pair", release_id=release_id, pv=pv))
        if isinstance(r.sex, str) and r.sex in ("female", "male"):
            out.append(_row(s, "sex", st, r.sex_raw_value, r.sex, 0.9, f"sample.attr.{r.sex_raw_key}", "biosample_attr", r.sex_raw_value, "R1", "registry_norm_v1:sex", release_id=release_id, pv=pv))
        if isinstance(r.country_iso2, str) and len(r.country_iso2) == 2:
            out.append(_row(s, "country", st, r.country_raw_value, r.country_iso2, 0.9, f"sample.attr.{r.country_raw_key}", "biosample_attr", r.country_raw_value, "R1", "registry_norm_v1:country", release_id=release_id, pv=pv))
    return out


def load_any(pat: str | None) -> pd.DataFrame:
    if not pat:
        return pd.DataFrame(columns=DET_COLS)
    fs = sorted(glob.glob(pat)) if "*" in pat else ([pat] if os.path.exists(pat) else [])
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True).reindex(columns=DET_COLS) if fs else pd.DataFrame(columns=DET_COLS)


def _attr_key(src) -> str | None:
    s = str(src)
    return s[len("sample.attr."):] if s.startswith("sample.attr.") else None


def apply_condition_maps(det: pd.DataFrame, cond_map: pd.DataFrame | None, abx_map: pd.DataFrame | None, release_id, pv) -> pd.DataFrame:
    """health_condition_detail rows (raw text) → health_condition code rows via the normalisation map (key + value); antibiotic raw
    rows without a normalised value → yes/no via the antibiotic map."""
    extra = []
    if cond_map is not None and len(cond_map):
        cm = cond_map.dropna(subset=["health_condition"])
        cm = cm[(cm.health_condition != "unknown") & (cm.confidence.fillna(0) >= 0.5)]
        key = {(str(k), str(v)): (c, cf, m) for k, v, c, cf, m in zip(cm.attr_key_norm, cm.attr_value, cm.health_condition, cm.confidence, cm.method)}
        # value-only map for non-attribute sources (R2 supplementary columns): a value that every key maps to the same code
        vals = cm.assign(v=cm.attr_value.astype(str).str.strip().str.lower()).groupby("v").agg(codes=("health_condition", lambda x: set(x)), cf=("confidence", "min"))
        vmap = {v: (next(iter(r.codes)), float(r.cf)) for v, r in vals.iterrows() if len(r.codes) == 1}
        d = det[det.field_name == "health_condition_detail"]
        for r in d.itertuples(index=False):
            k = _attr_key(r.evidence_source)
            hit = key.get((k, str(r.field_value))) if k else None
            if hit is None and k is None:
                vh = vmap.get(str(r.field_value).strip().lower())
                hit = (vh[0], min(vh[1], 0.8), "value_match") if vh else None
            if hit:
                c, cf, m = hit
                extra.append(_row(r.sample_key, "health_condition", r.study_accession, r.field_value, c, min(float(r.confidence), float(cf)), r.evidence_source, r.evidence_locator, r.evidence_quote, r.route, f"gut_condition_map:{m}", "code from config/vocab/health_conditions.yaml", release_id=release_id, pv=pv))
    if abx_map is not None and len(abx_map):
        am = abx_map[abx_map.antibiotic_exposure.isin(["yes", "no"]) & (abx_map.confidence.fillna(0) >= 0.5)]
        key = {(str(k), str(v)): (c, cf, m) for k, v, c, cf, m in zip(am.attr_key_norm, am.attr_value, am.antibiotic_exposure, am.confidence, am.method)}
        d = det[(det.field_name == "antibiotic_exposure") & det.value_normalized.isna()]
        for r in d.itertuples(index=False):
            k = _attr_key(r.evidence_source)
            hit = key.get((k, str(r.field_value))) if k else None
            if hit:
                c, cf, m = hit
                extra.append(_row(r.sample_key, "antibiotic_exposure", r.study_accession, r.field_value, c, min(float(r.confidence), float(cf)), r.evidence_source, r.evidence_locator, r.evidence_quote, r.route, f"gut_antibiotic_map:{m}", release_id=release_id, pv=pv))
    return pd.concat([det, pd.DataFrame(extra, columns=DET_COLS)], ignore_index=True) if extra else det


def _route_of(src, sample_level: bool) -> str:
    s = str(src or "")
    if s.startswith(("biosample.attribute", "sample.attr")):
        return "R1"
    if s.startswith("paper.fulltext"):
        return "R3"
    if s.startswith(("paper.abstract", "ena.")):
        return "R4"
    return "R2" if sample_level else "R3"


def apply_corrections(det: pd.DataFrame, path: str | None, rid: str, pv: str) -> tuple[pd.DataFrame, dict]:
    """dq_corrections.parquet columns: sample_key (may be empty = whole study), study_accession, field_name, action {retire, recode, keep},
    old_value, new_value, evidence_source, evidence_locator, evidence_quote, confidence, rationale, determined_by."""
    if not path or not os.path.exists(path):
        return det, {"n_rows": 0}
    c = pd.read_parquet(path)
    c = c[c.action.isin(["retire", "recode"])].copy()
    if not len(c):
        return det, {"n_rows": 0}
    c["sample_key"] = c.get("sample_key", pd.Series([""] * len(c))).fillna("").astype(str)
    c["old_value"] = c.old_value.fillna("").astype(str)
    dv = det.value_normalized.astype("string").fillna("").astype(str)
    drop = pd.Series(False, index=det.index)
    for r in c.itertuples(index=False):
        m = (det.field_name == r.field_name) & (det.study_accession == r.study_accession)
        if r.sample_key:
            m &= det.sample_key.astype(str) == r.sample_key
        if r.old_value:
            m &= dv == r.old_value
        drop |= m
    n_dropped = int(drop.sum())
    out = det[~drop]
    rec = c[(c.action == "recode") & c.new_value.notna() & (c.new_value.astype(str) != "")]
    new_rows = [_row(r.sample_key, r.field_name, r.study_accession, r.new_value, r.new_value, float(r.confidence) if pd.notna(r.confidence) else 0.7,
                     r.evidence_source or "dq_adjudication", r.evidence_locator or "", r.evidence_quote or "", _route_of(r.evidence_source, bool(r.sample_key)),
                     str(getattr(r, "determined_by", "") or "dq_adjudication_v1"), note=str(getattr(r, "rationale", "") or "")[:200],
                     scope="sample" if r.sample_key else "study_all", release_id=rid, pv=pv) for r in rec.itertuples(index=False)]
    if new_rows:
        out = pd.concat([out, pd.DataFrame(new_rows, columns=DET_COLS)], ignore_index=True)
    return out, {"n_rows": int(len(c)), "n_dropped": n_dropped, "n_recoded": len(new_rows)}


def resolve(det: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One current row per (sample, field): precedence infant catalog > R1 > R2 > R3 > R4, then confidence. Losers whose normalised
    value differs from the winner are returned as conflicts."""
    d = det[det.value_normalized.notna()].copy()
    d["_rank"] = d.route.map(ROUTE_RANK).fillna(9)
    d.loc[d.src_track == "infant_catalog", "_rank"] = 0
    d = d.sort_values(["sample_key", "field_name", "_rank", "confidence"], ascending=[True, True, True, False], kind="mergesort")
    keep = d.drop_duplicates(["sample_key", "field_name"], keep="first")
    lost = d.loc[d.index.difference(keep.index)]
    win = keep.set_index(["sample_key", "field_name"]).value_normalized
    idx = pd.MultiIndex.from_arrays([lost.sample_key, lost.field_name])
    lost = lost.assign(_win=win.reindex(idx).values)
    conflicts = lost[lost.value_normalized.astype(str) != lost._win.astype(str)].drop(columns=["_win", "_rank"])
    return keep.drop(columns=["_rank"]), conflicts


IV_STUDY_COLS = ["interventions", "interventions_unreplicated", "intervention_design", "intervention_detail", "population_condition",
                 "intervention_evidence_source", "intervention_evidence_quote", "intervention_confidence", "intervention_route", "intervention_determined_by"]


def load_intervention_studies(pattern):
    """Study-level intervention classification (R2026.15 leaves, gut_intervention_studies_shard_*.parquet): one row per study with
    interventions (';'-joined sorted codes, '' = none administered), design, detail, population_condition and one evidence quote."""
    if not pattern:
        return None
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    d = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True).drop_duplicates("study_accession", keep="last")
    d = d.rename(columns={"evidence_source": "intervention_evidence_source", "evidence_quote": "intervention_evidence_quote", "confidence": "intervention_confidence",
                          "route": "intervention_route", "determined_by": "intervention_determined_by"})
    for c in IV_STUDY_COLS:
        if c not in d.columns:
            d[c] = None
    d["interventions"] = d["interventions"].fillna("").astype(str)
    # no evidence quote is shown for a study with nothing administered
    none = d["interventions"] == ""
    d.loc[none, ["intervention_evidence_quote", "intervention_evidence_source"]] = None
    return d[["study_accession"] + IV_STUDY_COLS]


def recode_intervention_cohort(det, iv, rid, pv):
    """health_condition = intervention_cohort was a placeholder for 'trial population'; with the study-level intervention table the
    population's underlying condition is known (e.g. ulcerative_colitis for a UC FMT trial, healthy_control for healthy volunteers).
    All rows of such a study are recoded to population_condition (per-sample arm information lives in the `intervention` field)."""
    if iv is None or det.empty:
        return det, 0
    pc = iv.set_index("study_accession").population_condition.dropna()
    pc = pc[~pc.isin(["unknown", "intervention_cohort", ""])]
    # every route: intervention_cohort is not a condition (R1 values came from arm / treatment-group attributes, which now feed the
    # `intervention` field); studies whose population condition is unknown keep the legacy code
    m = (det.field_name == "health_condition") & (det.value_normalized == "intervention_cohort") & det.study_accession.isin(pc.index)
    if not m.any():
        return det, 0
    det = det.copy()
    det.loc[m, "value_normalized"] = det.loc[m, "study_accession"].map(pc)
    det.loc[m, "parse_note"] = (det.loc[m, "parse_note"].fillna("").astype(str) + "; recoded from intervention_cohort to the trial population's condition (gut_intervention_r4_v1)").str.lstrip("; ")
    return det, int(m.sum())


def expand_study_all(det_sample: pd.DataFrame, det_study: pd.DataFrame, samples: pd.DataFrame) -> pd.DataFrame:
    """study_all statements → one row per study sample lacking a sample-level value for that field."""
    if det_study.empty:
        return pd.DataFrame(columns=DET_COLS)
    # one statement per (study, field): route precedence R1 > R2 > R3 > R4, then confidence (1.9.0 shipped both an R3 and an R4 row
    # for the same sample × field when a full-text statement and an abstract statement coexisted — fixed in 1.10.0)
    det_study = det_study.assign(_rank=det_study.route.map(ROUTE_RANK).fillna(9)).sort_values(["study_accession", "field_name", "_rank", "confidence"], ascending=[True, True, True, False], kind="mergesort")
    det_study = det_study.drop_duplicates(["study_accession", "field_name"], keep="first").drop(columns=["_rank"])
    have = set(zip(det_sample.sample_key, det_sample.field_name))
    per_study = samples.groupby("study_accession").sample_key.apply(list)
    rows = []
    for r in det_study.to_dict("records"):
        for s in per_study.get(r["study_accession"], []):
            if (s, r["field_name"]) in have:
                continue
            rows.append(dict(r, sample_key=s, scope="sample", parse_note=(str(r.get("parse_note") or "") + " | expanded from study_all").strip(" |")))
    return pd.DataFrame(rows, columns=DET_COLS) if rows else pd.DataFrame(columns=DET_COLS)


def build(a):
    global PACK_FIELDS, INFANT_ONLY
    cfg_dir = os.environ.get("CATALOG_CONFIG_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "..", "config"))
    pack = _load_pack(cfg_dir)
    pf = pack_fields(pack)
    PACK_FIELDS, INFANT_ONLY = pf["fields"], pf["infant_only"]
    rid, pv = a.release_id, a.package_version
    studies = pd.read_parquet(a.studies)
    reg = pd.read_parquet(a.registry_studies) if a.registry_studies else studies
    # the leaf-built study list is re-checked against the CURRENT registry build with the pack's study_rule, so a registry
    # owner override (config/registry_overrides.yaml, e.g. PRJNA50637 → other_site at R2026.14) removes a study from the scope
    if a.registry_studies:
        rr = reg[reg.study_accession.isin(studies.study_accession)]
        if "release_retired" in rr.columns:
            rr = rr[rr.release_retired.isna()]
        ok = (rr.host_human.isin(["yes", "mixed"]) & rr.assay.isin(["shotgun_dna", "mixed"])
              & rr.body_sites.fillna("").str.contains(r"(?:^|;)gut_stool(?:;|$)")) | (rr.in_infant_catalog == "include")
        dropped = sorted(set(studies.study_accession) - set(rr.loc[ok, "study_accession"]))
        if dropped:
            print(f"study_rule: {len(dropped)} leaf-listed studies fail the pack study_rule on the current registry and are dropped: {dropped[:20]}", file=sys.stderr)
            studies = studies[~studies.study_accession.isin(dropped)].copy()
    gut_acc = set(studies.study_accession)
    infant_acc = set(studies.loc[studies.in_infant_catalog == "include", "study_accession"]) if "in_infant_catalog" in studies.columns else set()

    # ---- samples: registry_biosamples of the non-infant gut studies + the curated infant samples
    bio = pd.read_parquet(a.biosamples)
    bio = bio[bio.study_accession.isin(gut_acc - infant_acc)]
    if "release_retired" in bio.columns:
        bio = bio[bio.release_retired.isna()]
    wide0 = pd.read_parquet(os.path.join(a.package, "sample_metadata_wide.parquet"))
    wide0 = wide0[wide0.study_accession.isin(infant_acc)]
    # BioSamples shared between BioProjects (umbrella / re-deposited studies): the curated infant assignment wins, so the infant
    # rows come first and the registry rows of the same sample are dropped
    samples = pd.concat([
        pd.DataFrame(dict(sample_key=wide0.sample_key.values, study_accession=wide0.study_accession.values, biosample_accession=wide0.biosample_accession.values, secondary_sample=wide0.secondary_sample.values,
                          sample_unit=wide0.sample_unit.values, body_site_code=wide0.body_site_class.map({"primary": "gut_stool", "unknown": None, "excluded": "other_site", "linked": "other_site"}).values,
                          sample_life_stage=None, curated_source="infant_catalog")),
        pd.DataFrame(dict(sample_key=bio.sample_accession.values, study_accession=bio.study_accession.values, biosample_accession=bio.sample_accession.values, secondary_sample=None,
                          sample_unit="biosample", body_site_code=bio.body_site_code.values, sample_life_stage=bio.life_stage.values, curated_source=SRC)),
    ], ignore_index=True).drop_duplicates("sample_key", keep="first")
    bio = bio[bio.sample_accession.isin(set(samples.loc[samples.curated_source == SRC, "sample_key"]))]
    samples["in_infant_catalog"] = samples.study_accession.isin(infant_acc)

    # ---- determinations
    parts = [pd.DataFrame(rows_from_registry_biosamples(bio, rid, pv), columns=DET_COLS), load_any(a.r1), load_any(a.r1_extra), load_any(getattr(a, "r1_newfields", None)),
             load_any(getattr(a, "r1_newfields_v2", None)), load_any(getattr(a, "r1_interventions", None)), load_any(a.r2_glob), load_any(a.r3_glob), load_any(a.r4_glob)]
    det0 = pd.read_parquet(os.path.join(a.package, "sample_determinations.parquet"))
    det0 = det0[det0.study_accession.isin(infant_acc) & det0.field_name.isin(PACK_FIELDS + INFANT_ONLY)]
    if "release_retired" in det0.columns:
        det0 = det0[det0.release_retired.isna()]
    det0 = det0.assign(src_track="infant_catalog").reindex(columns=DET_COLS)
    det = pd.concat(parts + [det0], ignore_index=True)
    det = det[det.study_accession.isin(gut_acc)]
    cond = pd.read_parquet(a.condition_map) if a.condition_map and os.path.exists(a.condition_map) else None
    abx = pd.read_parquet(a.antibiotic_map) if a.antibiotic_map and os.path.exists(a.antibiotic_map) else None
    det = apply_condition_maps(det, cond, abx, rid, pv)
    # 'unknown' / placeholder codes are not determinations (unknown stays unknown = no row); keep raw detail text
    unk = det.value_normalized.astype("string").str.lower().isin(["unknown", "unknown_age", "unknown_site", "none", "nan", ""]) & (det.field_name != "health_condition_detail")
    det = det[~unk.fillna(False)]
    # vocabulary validation (health_condition, lifestyle …): codes outside config/vocab/<field>.yaml are dropped and counted
    det, vocab_dropped = validate_vocab_fields(det, pf, cfg_dir)
    # routes allowed per field (config/packs/gut.yaml fields.<f>.routes); infant-catalog rows are exempt (their own rules applied)
    allowed = {f: set(v.get("routes", ["R1", "R2", "R3", "R4"])) for f, v in pack["fields"].items()}
    ok_route = [(r in allowed.get(f, {"R1", "R2", "R3", "R4"})) or (t == "infant_catalog") for f, r, t in zip(det.field_name, det.route, det.src_track)]
    det = det[pd.Series(ok_route, index=det.index)]
    # data-quality corrections (dq_corrections.parquet from the adjudication leaf; R2026.13): action retire = drop the matching rows
    # (sample_key or whole study × field × old_value), recode = drop them and add a row with the new value and the adjudication evidence
    iv_study = load_intervention_studies(getattr(a, "interventions_study", None))
    det, n_iv_recode = recode_intervention_cohort(det, iv_study, rid, pv)
    det, n_corr = apply_corrections(det, getattr(a, "corrections", None), rid, pv)
    n_corr["intervention_cohort_recoded"] = n_iv_recode
    det_study = det[det.scope == "study_all"]
    det_sample = det[det.scope != "study_all"]
    det_sample = det_sample[det_sample.sample_key.isin(set(samples.sample_key))]
    det_sample, conflicts = resolve(det_sample)
    det_r4 = expand_study_all(det_sample, det_study, samples[~samples.in_infant_catalog])
    det_all = pd.concat([det_sample, det_r4], ignore_index=True)
    det_all = det_all[det_all.field_name.isin(PACK_FIELDS + INFANT_ONLY)].copy()
    inf = det_all.src_track == "infant_catalog"
    det_all.loc[~inf, "release_added"] = rid
    det_all.loc[~inf, "package_added"] = pv
    det_all["release_retired"] = None

    # ---- wide
    piv = det_all.pivot_table(index="sample_key", columns="field_name", values="value_normalized", aggfunc="first")
    conf = det_all.pivot_table(index="sample_key", columns="field_name", values="confidence", aggfunc="first")
    route = det_all.pivot_table(index="sample_key", columns="field_name", values="route", aggfunc="first")
    w = samples.set_index("sample_key")
    for f in PACK_FIELDS + INFANT_ONLY:
        w[f] = piv[f] if f in piv.columns else None
        if f in PACK_FIELDS:
            w[f + "__confidence"] = conf[f] if f in conf.columns else None
            w[f + "__route"] = route[f] if f in route.columns else None
    w["age_at_collection_days"] = pd.to_numeric(w["age_at_collection_days"], errors="coerce")
    for f in ("bmi", "latitude", "longitude"):
        if f in w.columns:
            w[f] = pd.to_numeric(w[f], errors="coerce")
    for f in pf.get("int_range") or {}:  # stool_consistency_bristol: nullable integer column
        if f in w.columns:
            w[f] = pd.to_numeric(w[f], errors="coerce").astype("Int64")
    # derived columns (config/packs/gut.yaml derived_fields): detailed_location = compose parts; collection_year = leading YYYY
    for f, spec in pf["compose"].items():
        cols = [c for c in spec["compose"] if c in w.columns]
        w[f] = [compose_detailed_location(vals) for vals in zip(*[w[c] for c in cols])] if cols else None
    for f, src in pf["year_of"].items():
        w[f] = w[src].map(collection_year) if src in w.columns else None
    # study-level fallback only for SINGLE-stage studies (R2026.13, data-quality item 3: PRJNA1306521 "long-living adults (>= 85) and
    # young children (3-5)" had life_stage_primary = child and every sample became `child`); multi-stage studies stay unknown here
    if "life_stage_primary" in reg.columns:
        ri = reg.set_index("study_accession")
        multi = ri.life_stages.fillna("").astype(str).str.replace("unknown_age", "").str.strip(";").str.contains(";") if "life_stages" in ri.columns else pd.Series(False, index=ri.index)
        st_stage = ri.life_stage_primary.where(~multi)
    else:
        st_stage = pd.Series(dtype=str)
    cat_age = w.age_at_collection_days.map(lambda d: age_category(d, pack))
    cat_stage = w.sample_life_stage.map(STAGE_TO_CAT)
    ls_rows = det_study[det_study.field_name == "life_stage"].assign(_rank=lambda d: d.route.map(ROUTE_RANK).fillna(9)).sort_values(["_rank", "confidence"], ascending=[True, False]).drop_duplicates("study_accession").set_index("study_accession")
    r4_stage = ls_rows.value_normalized  # best group-level life stage per study (R3 full text beats R4 abstract)
    cat_r4 = w.study_accession.map(r4_stage).map(STAGE_TO_CAT)
    grp_basis = w.study_accession.map(ls_rows.route.map({"R3": "r3_fulltext_life_stage"}).fillna("r4_abstract_life_stage"))
    cat_study = w.study_accession.map(st_stage).map(STAGE_TO_CAT)
    scope0 = wide0.set_index("sample_key").age_scope
    cat_inf = pd.Series(w.index.map(scope0), index=w.index).map(lambda v: "infant" if v in ("infant_evidenced", "study_all_infant") else ("adult" if v == "adult_flagged" else None))
    is_inf = w.in_infant_catalog
    # infant-catalog rows: the curated age_scope is authoritative (no fall-back to the study's life stage — the catalog deliberately
    # leaves mixed-age / no-estimate samples unknown); registry rows: age → sample life stage → study life stage
    cat_curated = cat_age.fillna(cat_inf)
    cat_registry = cat_age.fillna(cat_stage).fillna(cat_r4).fillna(cat_study)
    w["age_category"] = cat_curated.where(is_inf, cat_registry).fillna("unknown")
    basis = pd.Series("unknown", index=w.index)
    basis = basis.mask(~is_inf & cat_study.notna(), "study_life_stage").mask(~is_inf & cat_r4.notna(), grp_basis).mask(~is_inf & cat_stage.notna(), "sample_life_stage").mask(is_inf & cat_inf.notna(), "infant_catalog_age_scope").mask(cat_age.notna(), "age_at_collection_days")
    w["age_category_basis"] = basis
    w["body_site_class"] = w.body_site_code.map(lambda c: "primary" if c == "gut_stool" else ("unknown" if (c is None or pd.isna(c) or c == "unknown_site") else "excluded"))
    w["body_site_basis"] = w.body_site_code.map(lambda c: "sample_attribute" if isinstance(c, str) and c not in ("unknown_site",) else "none")
    # a sample without any site attribute in a study whose ONLY registry body site is gut_stool is a gut sample by study design
    st_sites = reg.set_index("study_accession").body_sites if "body_sites" in reg.columns else pd.Series(dtype=str)
    single_gut = w.study_accession.map(st_sites).fillna("") == "gut_stool"
    no_attr = w.body_site_code.isna() | (w.body_site_code == "unknown_site")
    w.loc[~is_inf & single_gut & no_attr, "body_site_class"] = "primary"
    w.loc[~is_inf & single_gut & no_attr, "body_site_basis"] = "study_single_site"
    bsc0 = wide0.set_index("sample_key").body_site_class
    m0 = w.index.isin(bsc0.index)
    w.loc[m0, "body_site_class"] = pd.Series(w.index[m0].map(bsc0), index=w.index[m0])
    # infant_scope == the infant catalog's catalog_scope rule exactly (age_scope infant_evidenced/study_all_infant AND body site primary/unknown)
    inf_scope0 = pd.Series(w.index.map(scope0), index=w.index).isin(["infant_evidenced", "study_all_infant"])
    w["infant_scope"] = is_inf & inf_scope0 & w.body_site_class.isin(["primary", "unknown"])
    # n_fields_with_value counts the CORE + KEY fields (owner tiers, 2026-09-29); detailed_location counts through its derived column
    tier_cols = [f for f in pf["core"] + pf["key"] if f in w.columns]
    # age_category is never null ('unknown' = not covered), so it is masked before counting (R2026.15: core age = life stage)
    _tc = w[tier_cols].copy()
    if "age_category" in _tc.columns:
        _tc["age_category"] = _tc["age_category"].where(_tc["age_category"].fillna("unknown") != "unknown")
    w["n_fields_with_value"] = _tc.notna().sum(axis=1)
    w["release_added"], w["release_retired"], w["package_added"] = rid, None, pv
    w = w.reset_index()

    # ---- studies
    g = w.groupby("study_accession")
    cov = pd.DataFrame({f"cov_{f}": g[f].apply(lambda s: round(float(s.notna().mean()), 3)) for f in PACK_FIELDS})
    cov["cov_age_category"] = g.age_category.apply(lambda s: round(float((s.fillna("unknown") != "unknown").mean()), 3))   # core age (life stage)
    gs = studies.set_index("study_accession").join(cov, how="left")
    gs["n_samples_curated"] = g.size()
    gs["age_categories"] = g.age_category.apply(lambda s: json.dumps(s.value_counts().to_dict()))
    gs["health_conditions"] = g.health_condition.apply(lambda s: json.dumps(s.dropna().value_counts().head(6).to_dict()))
    gs["curated_depth"] = det_all.groupby("study_accession").route.apply(lambda s: ";".join(sorted(set(s))))
    gs["curated_source"] = ["infant_catalog" if x in infant_acc else SRC for x in gs.index]
    if iv_study is not None:
        gs = gs.join(iv_study.set_index("study_accession")[IV_STUDY_COLS], how="left")
        gs["interventions"] = gs["interventions"].fillna("")
    # ---- runs (gut_runs.parquet, 1.12.0) + study sequencing summary
    gut_runs = None
    if getattr(a, "registry_runs", None) and os.path.exists(a.registry_runs):
        from catalog.scopes.newfields_r1 import build_gut_runs
        sp = pd.read_parquet(a.registry_sandpiper) if getattr(a, "registry_sandpiper", None) and os.path.exists(a.registry_sandpiper) else None
        inf_runs_p = os.path.join(a.package, "runs.parquet")
        nb = pd.read_parquet(a.run_bases) if getattr(a, "run_bases", None) and os.path.exists(a.run_bases) else None
        eb_p = os.path.join(os.path.dirname(a.run_bases), "run_bases_estimated.parquet") if getattr(a, "run_bases", None) else None
        eb = pd.read_parquet(eb_p) if eb_p and os.path.exists(eb_p) else None
        gut_runs = build_gut_runs(pd.read_parquet(a.registry_runs), sp, gut_acc, w, infant_runs=pd.read_parquet(inf_runs_p) if os.path.exists(inf_runs_p) else None, ncbi_bases=nb, est_bases=eb)
        gs = gs.join(sequencing_summary(gut_runs), how="left")
        from catalog.scopes.newfields_r1 import sample_depth
        sd = sample_depth(gut_runs)
        for c in sd.columns:   # per-sample depth on the wide table (R2026.16: every sample shows a depth)
            w[c] = w.sample_key.map(sd[c])
        w["n_runs"] = w["n_runs"].fillna(0).astype(int)
        gs["n_runs_total"] = gs.n_runs_total.fillna(0).astype(int)
    gs["n_samples_curated"] = gs.n_samples_curated.fillna(0).astype(int)
    gs["release_added"], gs["release_retired"], gs["package_added"] = rid, None, pv
    gs = gs.reset_index()

    os.makedirs(a.out, exist_ok=True)
    det_all = det_all.reindex(columns=DET_COLS)
    if a.previous_dir:
        from catalog.registry.build_registry import carry_release_columns
        for name, key, df_ref in ((pack["tables"]["determinations"], ["sample_key", "field_name"], "det"), (pack["tables"]["samples_wide"], ["sample_key"], "w"), (pack["tables"]["studies"], ["study_accession"], "gs")):
            # gut_runs is a pure archive projection (no release columns): rebuilt in full each release
            prev_p = os.path.join(a.previous_dir, name)
            if os.path.exists(prev_p):
                prev = pd.read_parquet(prev_p)
                if df_ref == "det":
                    det_all = carry_release_columns(det_all, prev, key, rid)
                elif df_ref == "w":
                    w = carry_release_columns(w, prev, key, rid)
                else:
                    gs = carry_release_columns(gs, prev, key, rid)
    if gut_runs is not None:
        gs["n_runs_total"] = pd.to_numeric(gs.n_runs_total, errors="coerce").fillna(0).astype(int)
        gut_runs.to_parquet(os.path.join(a.out, pack["tables"].get("runs", "gut_runs.parquet")), index=False)
    for f in pf.get("int_range") or {}:  # keep the nullable-integer dtype through carry_release_columns
        if f in w.columns:
            w[f] = pd.to_numeric(w[f], errors="coerce").astype("Int64")
    det_all.to_parquet(os.path.join(a.out, pack["tables"]["determinations"]), index=False)
    w.to_parquet(os.path.join(a.out, pack["tables"]["samples_wide"]), index=False)
    gs.to_parquet(os.path.join(a.out, pack["tables"]["studies"]), index=False)
    conflicts.to_parquet(os.path.join(a.out, "gut_sample_determinations_conflicts.parquet"), index=False)
    summary = dict(n_studies=int(len(gs)), n_samples=int(len(w)), n_determinations=int(len(det_all)), n_conflicts=int(len(conflicts)), corrections=n_corr,
                   by_source=w.curated_source.value_counts().to_dict(), age_category=w.age_category.value_counts().to_dict(),
                   coverage={f: round(float(w[f].notna().mean()), 4) for f in PACK_FIELDS}, routes=det_all.route.value_counts().to_dict(),
                   n_infant_scope=int(w.infant_scope.sum()), health_condition=w.health_condition.value_counts().head(12).to_dict(),
                   vocab_dropped=vocab_dropped, n_runs=int(len(gut_runs)) if gut_runs is not None else None,
                   lifestyle=w.lifestyle.value_counts().to_dict() if "lifestyle" in w.columns else {},
                   diet=w.diet.value_counts().to_dict() if "diet" in w.columns else {}, smoking_status=w.smoking_status.value_counts().to_dict() if "smoking_status" in w.columns else {},
                   medication_codes=(w.medication.dropna().str.split(";").explode().value_counts().to_dict() if "medication" in w.columns else {}),
                   stool_consistency_bristol=({int(k): int(v) for k, v in w.stool_consistency_bristol.dropna().value_counts().sort_index().items()} if "stool_consistency_bristol" in w.columns else {}),
                   collection_year=({int(k): int(v) for k, v in w.collection_year.dropna().astype(int).value_counts().sort_index().items()} if "collection_year" in w.columns else {}))
    if a.summary:
        json.dump(summary, open(a.summary, "w"), indent=1)
    print(json.dumps(summary))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--studies", required=True, help="gut study list (registry columns + in_infant_catalog)")
    ap.add_argument("--registry-studies"), ap.add_argument("--biosamples", required=True), ap.add_argument("--package", required=True, help="previous package dir (infant curated tables)")
    ap.add_argument("--r1"), ap.add_argument("--r1-extra", help="additional R1 determination files (glob), e.g. the condition/antibiotic expansion"),
    ap.add_argument("--corrections", help="dq_corrections.parquet (retire / recode rows decided by the data-quality adjudication; R2026.13)"),
    ap.add_argument("--r1-newfields", help="R1 determinations of the 1.12.0 fields (collection_date, location_*, latitude/longitude, lifestyle) from catalog.scopes.newfields_r1"),
    ap.add_argument("--run-bases", help="NCBI SRA runinfo bases for runs without an ENA base_count (scripts/fill_run_bases.py)")
    ap.add_argument("--r1-interventions", help="R1 per-sample intervention arms (gut_r1_intervention_determinations.parquet, R2026.15)")
    ap.add_argument("--interventions-study", help="glob of study-level intervention classification shards (gut_intervention_studies_shard_*.parquet, R2026.15)")
    ap.add_argument("--r1-newfields-v2", help="R1 determinations of the 1.13.0 fields (diet, smoking_status, medication, stool_consistency_bristol) from catalog.scopes.newfields_r1_v2"),
    ap.add_argument("--registry-runs", help="registry_runs.parquet → gut_runs.parquet + study sequencing summary columns"), ap.add_argument("--registry-sandpiper", help="registry_runs_sandpiper.parquet (sandpiper_profiled per run)"), ap.add_argument("--r2-glob"), ap.add_argument("--r3-glob", help="R3 full-text study_all determinations (glob)"), ap.add_argument("--r4-glob"), ap.add_argument("--condition-map"), ap.add_argument("--antibiotic-map")
    ap.add_argument("--out", required=True), ap.add_argument("--summary"), ap.add_argument("--release-id", default="R2026.7"), ap.add_argument("--package-version", default="1.8.0")
    ap.add_argument("--previous-dir", help="previous package dir: gut_* tables there provide release_added / retirements")
    build(ap.parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
