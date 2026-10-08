"""Assemble registry_studies.parquet (+ REGISTRY_REPORT.md) from
    (a) the enumeration track's registry_universe_studies.parquet   (frozen columns; audit/registry_schema.json)
    (b) deterministic classification                                  (classify_deterministic.classify_frame)
    (c) an optional LLM classification table                          (classify_llm.to_registry_row rows; parquet/json)
    (d) infant verdicts                                                (universe_studies_all.parquet of the current package)
and derive scope_memberships from config/scope.yaml. Deterministic: same inputs → byte-identical parquet.

CLI:
    python -m catalog.registry.build_registry --universe <registry_universe_studies.parquet> --infant <universe_studies_all.parquet>
        [--llm <llm_rows.parquet|json>] [--audit <registry_universe_audit.csv>] --out <dir> --release-id R2026.4 --package-version 1.6.0
    python -m catalog.registry.build_registry --make-fixture --v3-studies <universe_v3_full_studies.parquet>
        --frame-free <frame_free_study_signal.parquet> --infant <universe_studies_all.parquet> --out tests/data/registry_fixture_universe.parquet
The fixture path (200 studies drawn deterministically from the 1.5.0 universe) is what tests use when the enumeration track's
table is absent.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from collections import Counter

import pandas as pd

from catalog.registry import vocab as V
from catalog.registry.classify_deterministic import classify_frame, split_multi

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
SCHEMA_PATH = os.path.join(REPO, "audit", "registry_schema.json")

# universe column → classifier input column (whichever exists first wins)
INPUT_ALIASES = {
    "description": ["description", "description_short"],
    "scientific_names": ["scientific_names", "scientific_names_top"],
    "sample_titles_sample": ["sample_titles_sample", "sample_titles"],
    "isolation_sources": ["isolation_sources", "isolation_source"],
    "environmental_medium": ["environmental_medium", "environmental_media"],
    "host_body_sites": ["host_body_sites", "host_body_site"],
    "host_scientific_names": ["host_scientific_names", "host_scientific_name"],
    "target_genes": ["target_genes", "target_gene"],
    "instrument_models": ["instrument_models", "instrument_model"],
    "library_selections": ["library_selections", "library_selection"],
    "library_layouts": ["library_layouts", "library_layout"],
    "ages": ["ages", "age"], "dev_stages": ["dev_stages", "dev_stage"],
}
FIXTURE_STRATA = [("include", None, 40), ("exclude", "host_nonhuman", 40), ("exclude", "host_environmental", 20),
                  ("exclude", "host_synthetic", 10), ("exclude", "age_adult_only", 30), ("exclude", "age_child_over_36m", 15),
                  ("exclude", "age_maternal_only", 10), ("exclude", "site_excluded", 15), ("exclude", "site_unknown", 5),
                  ("exclude", "age_unknown_no_evidence", 10), ("uncertain", None, 5)]


def load_schema() -> dict:
    return json.load(open(SCHEMA_PATH, encoding="utf-8"))


def study_columns() -> list[str]:
    return [c["name"] for c in load_schema()["tables"]["registry_studies.parquet"]["columns"]]


def normalise_universe(df: pd.DataFrame) -> pd.DataFrame:
    """Make every classifier input column available under its canonical name (missing → empty string)."""
    out = df.copy()
    for canon, cands in INPUT_ALIASES.items():
        if canon in out.columns:
            continue
        src = next((c for c in cands if c in out.columns), None)
        out[canon] = out[src] if src else ""
    return out


def _sci_strip_counts(v) -> str:
    """'human gut metagenome (120); pig gut metagenome (3)' → 'human gut metagenome | pig gut metagenome'."""
    import re

    parts = [re.sub(r"\s*\(\d+\)\s*$", "", p).strip() for p in split_multi(v)]
    return " | ".join(p for p in parts if p)


# --------------------------------------------------------------------------- scope derivation
def derive_scope_memberships(host_human: str, body_sites: list[str], life_stages: list[str], assay: str, in_infant: str) -> list[str]:
    """Deterministic rule documented in config/scope.yaml (order = scope order there)."""
    out = []
    bs, ls = set(body_sites or []), set(life_stages or [])
    human = host_human in ("yes", "mixed")
    for s in V.load_scope()["scopes"]:
        sid = s["id"]
        hit = False
        if sid == "human_all":
            hit = (human and assay in ("shotgun_dna", "mixed")) or in_infant == "include"   # a curated scope is always inside human_all
        elif sid == "infant_gut":
            hit = in_infant == "include"
        elif not human:
            hit = False
        elif sid == "gut_child":
            hit = "gut_stool" in bs and bool(ls & {"child", "adolescent"})
        elif sid == "gut_adult":
            hit = "gut_stool" in bs and bool(ls & {"adult", "elderly"})
        elif sid == "respiratory":
            hit = bool(bs & {"nasal_nasopharyngeal", "respiratory_lower"})
        elif sid == "other_site":
            hit = bool(bs & {"other_site", "eye_ear"})
        elif sid == "unknown_site":
            hit = not bs or bs == {"unknown_site"}
        else:  # oral, skin, vaginal_urogenital, milk, blood_tissue: same-named body-site code
            hit = sid in bs
        if hit:
            out.append(sid)
    return out


# --------------------------------------------------------------------------- assembly
def _first_public(df: pd.DataFrame, col: str) -> pd.Series:
    return df[col].astype("string").fillna("") if col in df.columns else pd.Series([""] * len(df), index=df.index, dtype="string")


LLM_ELIGIBLE_CLASSES = ("prior_human", "signal_human_new", "ambiguous_new")


def _llm_eligible(det: pd.DataFrame, merged: pd.DataFrame) -> pd.Series:
    """Studies the LLM stage is meant to cover (owner decision, S1b): deterministic `needs_llm` AND a human-candidate class
    (`candidate_class` of the enumeration). Studies without any human signal (nosignal_new) and prior non-human studies stay
    at their deterministic stage — they are not `pending`. Boolean Series indexed like ``det`` (study_accession)."""
    needs = det["needs_llm"].astype(bool)
    if "candidate_class" not in merged.columns:
        return needs
    m1 = merged.drop_duplicates("study_accession").set_index("study_accession")
    cls = m1["candidate_class"].reindex(det.index)
    ok = needs & cls.isin(LLM_ELIGIBLE_CLASSES).fillna(False)
    if "found_by" in m1.columns:  # carried infant-universe rows have no ENA library metadata to classify from: they keep their prior
        ok &= ~(m1["found_by"].reindex(det.index) == CARRY_FOUND_BY).fillna(False)
    return ok


CARRY_FOUND_BY = "infant_catalog_carry"


def carry_infant_universe(universe: pd.DataFrame, infant: pd.DataFrame) -> pd.DataFrame:
    """Append infant-universe studies that the ENA enumeration slices never surface (e.g. PRJNA61745, an INCLUDED study whose
    runs carry no METAGENOMIC/WGS library tags) so the registry is a superset of the screened infant universe (F13). Rows
    are built from universe_studies_all (title, counts, first_public); every other universe column is empty and
    ``found_by`` = infant_catalog_carry so they stay auditable. Current verdict rows only."""
    inf = infant
    if "release_retired" in inf.columns:
        inf = inf[inf["release_retired"].isna()]
    inf = inf.drop_duplicates("study_accession")
    missing = inf[~inf["study_accession"].isin(set(universe["study_accession"]))]
    if missing.empty:
        return universe
    rows = pd.DataFrame({"study_accession": missing["study_accession"].values})
    for src, dst in (("study_title", "study_title"), ("n_samples", "n_samples"), ("n_runs", "n_runs"), ("n_biosamples", "n_biosamples"),
                     ("first_public_min", "first_public_min"), ("triage_verdict", "in_infant_catalog"), ("reason_code", "infant_reason_code"),
                     ("body_site_call", "infant_body_site_call"), ("universe_slice", "infant_universe_slice")):
        if src in missing.columns and dst in universe.columns:
            rows[dst] = missing[src].values
    if "infant_screened" in universe.columns:
        rows["infant_screened"] = True
    if "candidate_class" in universe.columns:
        rows["candidate_class"] = "prior_human"
    if "found_by" in universe.columns:
        rows["found_by"] = CARRY_FOUND_BY
    if "has_ena_study_record" in universe.columns:
        rows["has_ena_study_record"] = False
    out = pd.concat([universe, rows.reindex(columns=universe.columns)], ignore_index=True)
    for c in universe.columns:  # keep dtypes stable for the string columns the classifier reads
        if universe[c].dtype == object or str(universe[c].dtype).startswith("str"):
            out[c] = out[c].where(out[c].notna(), "")
    return out


def join_sandpiper(universe: pd.DataFrame, sandpiper_runs: pd.DataFrame | None) -> pd.DataFrame:
    """Add n_runs_sandpiper per study from a run-level join table (run_accession, study_accession, sandpiper_profiled bool)
    — the registry runs matched against the pinned Sandpiper per-accession summary. Missing table → column of zeros."""
    out = universe.copy()
    if sandpiper_runs is None or sandpiper_runs.empty:
        out["n_runs_sandpiper"] = 0
        return out
    per = sandpiper_runs.groupby("study_accession")["sandpiper_profiled"].sum().astype(int)
    out["n_runs_sandpiper"] = out["study_accession"].map(per).fillna(0).astype(int)
    return out


def carry_release_columns(df: pd.DataFrame, previous: pd.DataFrame | None, key: list[str], release_id: str) -> pd.DataFrame:
    """Bitemporal convention for registry tables (config/releases.yaml registry_tables, built_with_release_columns): rows already
    present in the previous package keep their release_added / package_added; rows that disappeared are appended as retired
    (release_retired = this release); new rows carry this release. `previous` = the same table from the previous package
    (current rows only are considered on its side)."""
    if previous is None or previous.empty:
        return df
    prev = previous
    if "release_retired" in prev.columns:
        prev_retired = prev[prev["release_retired"].notna()]
        prev = prev[prev["release_retired"].isna()]
    else:
        prev_retired = prev.iloc[0:0]
    prev = prev.drop_duplicates(key, keep="first")  # a previous package may carry duplicate keys (1.9.0 gut R3/R4 rows); one carrier per key
    idx = prev.set_index(key)
    cur = df.set_index(key)
    common = cur.index.intersection(idx.index)
    for c in ("release_added", "package_added"):
        if c in idx.columns:
            cur.loc[common, c] = idx.loc[common, c]
    gone = idx.loc[idx.index.difference(cur.index)].copy()
    gone["release_retired"] = release_id
    out = pd.concat([cur.reset_index(), gone.reset_index().reindex(columns=cur.reset_index().columns), prev_retired.reindex(columns=cur.reset_index().columns)], ignore_index=True)
    return out


CURATED_ASSAY_OVERRIDE = ("other", "unknown", "amplicon_misfiled")


def apply_curated_precedence(out: pd.DataFrame) -> pd.DataFrame:
    """Curated verdict > archive-only classification (config/scope.yaml). A study INCLUDED in the infant gut catalog was confirmed
    by the curated cascade (paper + sample metadata) to be a human shotgun metagenome of infant stool: registry values that
    contradict that — host_human != yes/mixed, assay in {other, unknown, amplicon_misfiled} (ENA library_strategy OTHER deposits),
    gut_stool / infant missing from the site / stage lists — are overridden here and the note records it. Stage and evidence are
    untouched (the override is a precedence rule, not new evidence)."""
    inc = out["in_infant_catalog"] == "include"
    if not inc.any():
        return out
    out = out.copy()
    fix_host = inc & ~out["host_human"].isin(["yes", "mixed"])
    fix_assay = inc & out["assay"].isin(CURATED_ASSAY_OVERRIDE)
    out.loc[fix_host, "host_human"] = "yes"
    out.loc[fix_assay, "assay"] = "shotgun_dna"

    def _add(lst: str, code: str) -> str:
        parts = [p for p in (lst or "").split(";") if p and not p.startswith("unknown")]
        return ";".join(parts + ([code] if code not in parts else []))

    bs_missing = inc & ~out["body_sites"].fillna("").str.contains(r"(?:^|;)gut_stool(?:;|$)")
    ls_missing = inc & ~out["life_stages"].fillna("").str.contains(r"(?:^|;)(?:infant|neonate)(?:;|$)")
    out.loc[bs_missing, "body_sites"] = out.loc[bs_missing, "body_sites"].map(lambda v: _add(v, "gut_stool"))
    out.loc[bs_missing & (out["body_site_primary"].isna() | (out["body_site_primary"] == "unknown_site")), "body_site_primary"] = "gut_stool"
    out.loc[ls_missing, "life_stages"] = out.loc[ls_missing, "life_stages"].map(lambda v: _add(v, "infant"))
    out.loc[ls_missing & (out["life_stage_primary"].isna() | (out["life_stage_primary"] == "unknown_age")), "life_stage_primary"] = "infant"
    out.attrs["curated_precedence_applied"] = int((fix_host | fix_assay | bs_missing | ls_missing).sum())
    return out


OVERRIDES_PATH = Path(__file__).resolve().parents[3] / "config" / "registry_overrides.yaml"
OVERRIDE_COLUMNS = ("host_human", "assay", "body_sites", "body_site_primary", "life_stages", "life_stage_primary")


def apply_owner_overrides(out: pd.DataFrame, path: Path = OVERRIDES_PATH) -> pd.DataFrame:
    """Owner decisions (config/registry_overrides.yaml) > every classification stage. Each entry names a study and the
    columns it sets (OVERRIDE_COLUMNS); *_evidence lists are replaced by the decision record; classification_stage becomes
    owner_decision. Unknown studies are ignored (the decision stays on file for when the study enters the universe)."""
    if not path.exists():
        return out
    import yaml
    spec = yaml.safe_load(path.read_text()) or {}
    entries = spec.get("overrides") or []
    if not entries:
        return out
    out = out.copy()
    idx = out.set_index("study_accession").index
    n = 0
    for e in entries:
        acc = e["study_accession"]
        if acc not in idx:
            continue
        m = out["study_accession"] == acc
        for c in OVERRIDE_COLUMNS:
            if c in e:
                out.loc[m, c] = e[c]
        for c in ("host_evidence", "body_site_evidence", "life_stage_evidence"):
            if c in e:
                out.loc[m, c] = json.dumps(e[c])
        out.loc[m, "classification_stage"] = e.get("stage", "owner_decision")
        n += int(m.sum())
    out.attrs["owner_overrides_applied"] = n
    return out


def assemble(universe: pd.DataFrame, infant: pd.DataFrame, llm: pd.DataFrame | None, release_id: str, package_version: str,
             sandpiper_runs: pd.DataFrame | None = None) -> pd.DataFrame:
    universe = carry_infant_universe(universe, infant)
    universe = join_sandpiper(universe, sandpiper_runs)
    uni = normalise_universe(universe)
    uni["scientific_names"] = uni["scientific_names"].map(_sci_strip_counts)
    inf = infant.rename(columns={"triage_verdict": "infant_verdict", "reason_code": "infant_reason_code", "body_site_call": "infant_body_site_call"})
    inf_cols = [c for c in ("study_accession", "infant_verdict", "infant_reason_code", "infant_body_site_call", "controlled_access", "n_biosamples") if c in inf.columns]
    inf = inf[inf_cols].drop_duplicates("study_accession")
    if "release_retired" in infant.columns:  # current verdict rows only
        inf = inf[infant.loc[inf.index, "release_retired"].isna()] if len(inf) == len(infant) else inf
    merged = uni.merge(inf, on="study_accession", how="left", suffixes=("", "_inf"))
    det = classify_frame(merged)  # priors come from the merged infant_* columns
    det = det.set_index("study_accession")
    if llm is not None and len(llm):
        llm = llm.drop_duplicates("study_accession").set_index("study_accession")
        stage = llm.get("classification_stage", pd.Series(dtype=str, index=llm.index))
        # LLM rows carry values when the replicates agreed / were adjudicated, or when adjudication was unavailable and the
        # conservative replicate merge kept the agreed fields (stage stays `pending`, outcome replicates_unadjudicated);
        # blank sentinels (outcome sentinel_no_evidence) are dropped so the deterministic values stand.
        keep = stage.isin(["sonnet_x2", "opus_adjudicated"])
        if "outcome" in llm.columns:
            keep |= (stage == "pending") & (llm["outcome"] == "replicates_unadjudicated")
        llm = llm[keep]
        common = det.index.intersection(llm.index)
        for c in ("host_human", "host_evidence", "assay", "assay_evidence", "body_sites", "body_site_primary", "body_site_evidence",
                  "life_stages", "life_stage_primary", "life_stage_evidence", "population_flags", "population_evidence",
                  "classification_stage", "classification_confidence", "classification_model"):
            if c in llm.columns:
                det.loc[common, c] = llm.loc[common, c]
        det["health_context"] = None
        if "health_context" in llm.columns:
            det.loc[common, "health_context"] = llm.loc[common, "health_context"]
        det.loc[det.index.difference(common).intersection(det.index[_llm_eligible(det, merged)]), "classification_stage"] = "pending"
    else:
        det["health_context"] = None
        det.loc[_llm_eligible(det, merged), "classification_stage"] = "pending"
    det = det.reset_index()

    m = merged.merge(det, on="study_accession", how="left", suffixes=("_uni", ""))
    n = len(m)

    def col(name, default=""):
        return m[name] if name in m.columns else pd.Series([default] * n, index=m.index)

    in_inf = col("infant_verdict", None).fillna("not_screened").replace({"": "not_screened"})
    ctrl = col("controlled_access", False)
    ctrl = ctrl.map(lambda v: str(v).lower() in ("true", "1", "yes")) if ctrl.dtype == object else ctrl.fillna(False).astype(bool)
    n_runs = pd.to_numeric(col("n_runs", 0), errors="coerce").fillna(0).astype(int)
    access = pd.Series(["open"] * n, index=m.index)
    access[n_runs == 0] = "unknown"
    access[ctrl] = "controlled"
    scientific_top = universe["scientific_names_top"] if "scientific_names_top" in universe.columns else uni["scientific_names"]
    out = pd.DataFrame({
        "study_accession": m["study_accession"],
        "secondary_study_accession": col("secondary_study_accession"),
        "study_title": col("study_title"),
        "description_short": col("description").astype("string").fillna("").str.slice(0, 300),
        "center_name": col("center_name"),
        "first_public_min": _first_public(m, "first_public_min"), "first_public_max": _first_public(m, "first_public_max"),
        "n_runs": n_runs,
        "n_samples": pd.to_numeric(col("n_samples", 0), errors="coerce").fillna(0).astype(int),
        "n_biosamples": pd.to_numeric(col("n_biosamples", 0), errors="coerce").fillna(0).astype(int),
        "n_runs_sandpiper": pd.to_numeric(col("n_runs_sandpiper", 0), errors="coerce").fillna(0).astype(int),
        "library_strategies": col("library_strategies"), "library_sources": col("library_sources"),
        "instrument_platforms": col("instrument_platforms"),
        "scientific_names_top": scientific_top.reindex(m.index).fillna("") if len(scientific_top) == n else uni["scientific_names"],
        "host_tax_ids": col("host_tax_ids"),
        "n_runs_host_9606": pd.to_numeric(col("n_runs_host_9606", 0), errors="coerce").fillna(0).astype(int),
        "n_runs_nonhuman_host": pd.to_numeric(col("n_runs_nonhuman_host", 0), errors="coerce").fillna(0).astype(int),
        "human_signal": col("human_signal", False).fillna(False).astype(bool),
        "human_signal_rule": col("human_signal_rule", "none").fillna("none").replace({"": "none"}),
        "ambiguous": col("ambiguous", False).fillna(False).astype(bool),
        "host_human": m["host_human"], "host_evidence": m["host_evidence"], "assay": m["assay"], "access": access,
        "body_sites": m["body_sites"].fillna(""), "body_site_primary": m["body_site_primary"], "body_site_evidence": m["body_site_evidence"],
        "life_stages": m["life_stages"].fillna(""), "life_stage_primary": m["life_stage_primary"], "life_stage_evidence": m["life_stage_evidence"],
        "population_flags": m["population_flags"].fillna(""), "health_context": m["health_context"],
        "classification_stage": m["classification_stage"], "classification_confidence": m["classification_confidence"].astype(float),
        "classification_model": m["classification_model"],
        "in_infant_catalog": in_inf, "infant_reason_code": col("infant_reason_code", None),
        "universe_slice": col("universe_slice"),
    })
    out = apply_curated_precedence(out)
    out = apply_owner_overrides(out)
    out["scope_memberships"] = [";".join(derive_scope_memberships(h, b.split(";") if b else [], l.split(";") if l else [], a, i))
                                for h, b, l, a, i in zip(out.host_human, out.body_sites, out.life_stages, out.assay, out.in_infant_catalog)]
    # sample-tier roll-up columns (R2026.5, filled by catalog.registry.build_biosamples); defaults = "not harvested"
    for c in ("n_biosamples_harvested", "n_biosamples_with_site", "n_biosamples_with_age", "n_biosamples_with_sex"):
        out[c] = 0
    for c in ("sample_body_sites", "sample_life_stages", "sample_countries"):
        out[c] = "{}"
    out["sample_age_days_median"] = pd.Series([float("nan")] * len(out), index=out.index, dtype="float64")
    out["release_added"], out["release_retired"], out["package_added"] = release_id, None, package_version
    out = out[study_columns()].sort_values("study_accession").reset_index(drop=True)
    for c in out.columns:
        if out[c].dtype == object or str(out[c].dtype) == "string":
            out[c] = out[c].astype(object).where(out[c].notna(), None)
    validate_registry(out)
    return out


def validate_registry(df: pd.DataFrame) -> None:
    sch = load_schema()["tables"]["registry_studies.parquet"]
    cols = [c["name"] for c in sch["columns"]]
    assert list(df.columns) == cols, f"registry_studies columns != schema: {set(df.columns) ^ set(cols)}"
    assert df.study_accession.is_unique, "duplicate study_accession"
    for c in sch["columns"]:
        if "vocabulary" in c:
            bad = set(df[c["name"]].dropna()) - set(c["vocabulary"])
            assert not bad, f"{c['name']}: values outside vocabulary {bad}"
        if "vocabulary_ref" in c:
            ref = c["vocabulary_ref"]
            allowed = set(V.scope_ids()) if ref == "scopes" else set(V.codes(ref))
            vals = set()
            for v in df[c["name"]].dropna():
                vals.update(x for x in str(v).split(";") if x)
            bad = vals - allowed
            assert not bad, f"{c['name']}: codes outside {ref}: {bad}"
    for c in sch["columns"]:
        if c.get("max_length"):
            assert df[c["name"]].dropna().astype(str).str.len().max() <= c["max_length"] or df[c["name"]].dropna().empty, f"{c['name']} exceeds {c['max_length']}"
    for c in ("host_evidence", "body_site_evidence", "life_stage_evidence"):
        for v in df[c].dropna():
            rows = json.loads(v)
            assert isinstance(rows, list)
            for r in rows:
                assert len(str(r["quote"]).split()) <= 12, f"{c}: quote > 12 words: {r}"


# --------------------------------------------------------------------------- report
def write_report(df: pd.DataFrame, path: str, release_id: str, fixture: bool, audit: pd.DataFrame | None) -> None:
    def table(col, title):
        vc = df[col].fillna("(null)").value_counts()
        lines = [f"### {title}", "", "| value | studies |", "|---|---:|"] + [f"| {k} | {v:,} |" for k, v in vc.items()] + [""]
        return lines

    scopes = Counter(s for v in df.scope_memberships for s in (v.split(";") if v else []))
    lines = [f"# REGISTRY_REPORT — registry_studies ({release_id})", "",
             f"*{'FIXTURE RUN — 200-study sample drawn from the 1.5.0 infant universe; counts are provisional and describe the fixture, not the registry universe.' if fixture else 'Full registry universe.'}*",
             "", f"Studies: **{len(df):,}** · runs: **{int(df.n_runs.sum()):,}** · samples: **{int(df.n_samples.sum()):,}** · needs-LLM (stage pending): **{int((df.classification_stage == 'pending').sum()):,}**", ""]
    lines += table("host_human", "host_human") + table("body_site_primary", "body_site_primary") + table("life_stage_primary", "life_stage_primary")
    lines += table("assay", "assay") + table("classification_stage", "classification_stage") + table("in_infant_catalog", "in_infant_catalog") + table("access", "access")
    lines += ["### scope_memberships (a study can be in several)", "", "| scope | studies |", "|---|---:|"] + [f"| {k} | {v:,} |" for k, v in sorted(scopes.items(), key=lambda kv: -kv[1])] + [""]
    if audit is not None:
        lines += ["### registry_universe_audit (enumeration track)", "", audit.to_markdown(index=False), ""]
    open(path, "w", encoding="utf-8").write("\n".join(lines))


# --------------------------------------------------------------------------- fixture from 1.5.0 inputs
def make_fixture(v3_studies: str, frame_free: str, infant_universe: str, out_path: str, strata=FIXTURE_STRATA) -> pd.DataFrame:
    u3 = pd.read_parquet(v3_studies)
    ff = pd.read_parquet(frame_free, columns=["study_accession", "description", "human_signal", "signal_rule", "ambiguous", "n_runs_human_host", "n_runs_nonhuman_host"])
    ua = pd.read_parquet(infant_universe)
    ua = ua[ua.release_retired.isna()] if "release_retired" in ua.columns else ua
    u = u3.merge(ua[["study_accession", "triage_verdict", "reason_code", "universe_slice", "n_biosamples", "controlled_access"]], on="study_accession", how="inner")
    picks = []
    for verdict, code, k in strata:
        sub = u[(u.triage_verdict == verdict) & ((u.reason_code == code) if code else True)].sort_values("study_accession")
        picks.append(sub.head(k))
    fx = pd.concat(picks).drop_duplicates("study_accession").merge(ff, on="study_accession", how="left")
    has_h = fx.host_tax_ids.fillna("").str.contains(r"\b9606\b")
    out = pd.DataFrame({
        "study_accession": fx.study_accession, "secondary_study_accession": fx.secondary_study_accession, "study_title": fx.study_title,
        "description_short": fx.description.fillna("").str.slice(0, 300), "center_name": fx.center_name,
        "first_public_min": fx.first_public_min.astype(str), "first_public_max": fx.first_public_max.astype(str),
        "n_runs": fx.n_runs.astype(int), "n_samples": fx.n_samples.astype(int), "n_biosamples": pd.to_numeric(fx.n_biosamples, errors="coerce").fillna(0).astype(int),
        "library_strategies": fx.library_strategies, "library_sources": fx.library_sources, "instrument_platforms": fx.instrument_platforms,
        "scientific_names_top": fx.scientific_names, "host_tax_ids": fx.host_tax_ids,
        "n_runs_host_9606": pd.to_numeric(fx.n_runs_human_host, errors="coerce").fillna(has_h.astype(int) * fx.n_runs).astype(int),
        "n_runs_nonhuman_host": pd.to_numeric(fx.n_runs_nonhuman_host, errors="coerce").fillna(0).astype(int),
        "human_signal": fx.human_signal.fillna(has_h).astype(bool),
        "human_signal_rule": fx.signal_rule.fillna("").map(lambda s: s[:1] if s and s[:1] in "ABC" else ("A" if False else "none")),
        "ambiguous": fx.ambiguous.fillna(False).astype(bool), "universe_slice": fx.universe_slice,
        # classifier inputs beyond the frozen columns (the enumeration track carries the same aggregates)
        "sample_titles_sample": fx.sample_titles_sample, "isolation_sources": fx.isolation_sources, "environmental_medium": fx.environmental_medium,
        "host_body_sites": fx.host_body_sites, "host_scientific_names": fx.host_scientific_names, "ages": fx.ages, "dev_stages": fx.dev_stages,
        "instrument_models": fx.instrument_models, "library_selections": fx.library_selections, "library_layouts": fx.library_layouts,
        "base_count_median": fx.base_count_median, "read_count_median": fx.read_count_median, "serovars": fx.serovars,
        "sub_species": fx.sub_species, "strains": fx.strains,
    }).sort_values("study_accession").reset_index(drop=True)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    out.to_parquet(out_path, index=False)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe"), ap.add_argument("--infant", required=True), ap.add_argument("--llm"), ap.add_argument("--audit")
    ap.add_argument("--sandpiper-runs", help="run-level Sandpiper join table (run_accession, study_accession, sandpiper_profiled)")
    ap.add_argument("--previous", help="registry_studies.parquet of the previous package: release_added/package_added carried, vanished studies retired")
    ap.add_argument("--out", required=True), ap.add_argument("--release-id", default="R2026.4"), ap.add_argument("--package-version", default="1.6.0")
    ap.add_argument("--make-fixture", action="store_true"), ap.add_argument("--v3-studies"), ap.add_argument("--frame-free")
    ap.add_argument("--fixture", action="store_true", help="mark the report as a fixture run")
    a = ap.parse_args(argv)
    if a.make_fixture:
        fx = make_fixture(a.v3_studies, a.frame_free, a.infant, a.out)
        print(f"fixture {len(fx)} studies → {a.out}")
        return 0
    universe = pd.read_parquet(a.universe)
    infant = pd.read_parquet(a.infant)
    llm = None
    if a.llm:
        llm = pd.read_parquet(a.llm) if a.llm.endswith(".parquet") else pd.DataFrame(json.load(open(a.llm)).get("rows", []))
    audit = pd.read_csv(a.audit) if a.audit and os.path.exists(a.audit) else None
    os.makedirs(a.out, exist_ok=True)
    sp_runs = pd.read_parquet(a.sandpiper_runs) if a.sandpiper_runs else None
    df = assemble(universe, infant, llm, a.release_id, a.package_version, sandpiper_runs=sp_runs)
    if a.previous and os.path.exists(a.previous):
        df = carry_release_columns(df, pd.read_parquet(a.previous), ["study_accession"], a.release_id)
    df.to_parquet(os.path.join(a.out, "registry_studies.parquet"), index=False)
    if audit is not None:
        audit.to_csv(os.path.join(a.out, "registry_universe_audit.csv"), index=False)
    write_report(df, os.path.join(a.out, "REGISTRY_REPORT.md"), a.release_id, a.fixture, audit)
    print(f"registry_studies {len(df)} rows → {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
