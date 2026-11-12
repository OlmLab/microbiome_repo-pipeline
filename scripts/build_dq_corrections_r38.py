#!/usr/bin/env python
"""R2026.38 corrections: turn the decision tables of the November 2026 check (docs/audits/r38/) into
data/inputs/gut/dq_corrections_r38.parquet, the file build_gut_scope.apply_corrections reads.

Inputs (docs/audits/r38/):
  antibiotic_decisions_part{1,2}.csv   one decision per cohort-wide antibiotic statement (keep | retire)
  lifestyle_decisions_part{1,2,3}.csv  one decision per (project, lifestyle code) whose stored quote was the code itself
  corrections_classes.csv              four classes of suspect health_condition values
  corrections_projects.csv             single-project errors
and the previous package (--package) for the per-sample expansion of project-level recodes.

Rules
  * retire rows stay project-level and are limited by old_value, old_route and (lifestyle) old_determined_by;
    a retired cohort-wide antibiotic statement is withdrawn on both statement routes (R3 full text, R4 abstract / description);
  * a project-level recode is expanded to one row per sample that carries the old value now, unless that value is itself the
    expansion of a cohort-wide statement, in which case the statement is replaced (a project-level recode would otherwise be
    spread over every sample of the project that lacks a value);
  * every row is determined_by dq_adjudication_v2:r38 (full quote kept, ranked above the automatic routes).
"""
from __future__ import annotations

import argparse
import glob
import os
import re

import pandas as pd
import yaml

BY = "dq_adjudication_v2:r38"
COLS = ["sample_key", "study_accession", "field_name", "action", "old_value", "old_route", "old_determined_by", "new_value",
        "evidence_source", "evidence_locator", "evidence_quote", "confidence", "rationale", "determined_by", "origin"]
SECTION = {"abstract": "abstract", "method": "methods", "result": "results", "discussion": "discussion", "introduction": "introduction"}


def _row(**k):
    r = {c: "" for c in COLS}
    r.update(k)
    r["determined_by"] = BY
    return r


def antibiotic_rows(d: str) -> list[dict]:
    dec = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(os.path.join(d, "antibiotic_decisions_part*.csv")))], ignore_index=True)
    st = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(os.path.join(d, "antibiotic_statements_part*.csv")))], ignore_index=True)
    dec = dec.merge(st[["row_id", "route", "evidence_locator"]], on="row_id", how="left", validate="1:1")
    assert dec.decision.isin(["keep", "retire"]).all() and dec.route.isin(["R3", "R4"]).all()
    ret = dec[dec.decision == "retire"]
    # one statement per (project, value) among the retired ones, so (project, value, route) identifies the rows exactly
    assert not ret.duplicated(["study_accession", "value_normalized"]).any()
    assert not (set(ret.study_accession) & set(dec[dec.decision == "keep"].study_accession))   # no project has a kept statement too
    rows = []
    for r in ret.itertuples(index=False):
        for route in ("R3", "R4"):
            # every cohort-wide antibiotic statement of the project goes, whatever its value: withdrawing "no" from a trial that
            # gives antibiotics otherwise lets the abstract's "yes" spread over the baseline samples (first build: 326 samples)
            rows.append(_row(study_accession=r.study_accession, field_name="antibiotic_exposure", action="retire", old_value="",
                             old_route=route, evidence_locator=str(r.evidence_locator or ""), evidence_quote=str(r.paper_quote) if pd.notna(r.paper_quote) else "",
                             rationale=f"{r.category}: {r.reason}", origin=f"antibiotic_audit:row{r.row_id}"))
    return rows


def lifestyle_rows(d: str, codes: set) -> list[dict]:
    dec = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(os.path.join(d, "lifestyle_decisions_part*.csv")))], ignore_index=True)
    assert dec.decision.isin(["keep", "recode", "retire"]).all() and not dec.duplicated(["study_accession", "code"]).any()
    rows = []
    for r in dec.itertuples(index=False):
        base = dict(study_accession=r.study_accession, field_name="lifestyle", old_value=r.code, old_route="R3", old_determined_by="fulltext_setting_v1",
                    rationale=str(r.reason), origin="lifestyle_audit")
        if r.decision == "retire":
            rows.append(_row(action="retire", **base))
            # the same code from the abstract or project description is no better supported (PRJEB63347: one arm of two)
            rows.append(_row(action="retire", **(base | dict(old_route="R4", old_determined_by=""))))
            continue
        new = r.code if r.decision == "keep" else str(r.new_code)
        assert new in codes, (r.study_accession, new)
        assert isinstance(r.quote, str) and len(r.quote.split()) >= 4 and re.match(r"PMC\d+$", str(r.pmcid)), r.study_accession
        sec = next((v for k, v in SECTION.items() if k in str(r.section).lower()), "")
        rows.append(_row(action="recode", new_value=new, evidence_source="paper.fulltext" + (f".{sec}" if sec else ""), evidence_locator=str(r.pmcid),
                         evidence_quote=r.quote, confidence=0.8, **base))
    return rows


def expand(rows: pd.DataFrame, idx: dict) -> list[dict]:
    """Project-level recodes -> per-sample rows where the current value is a per-sample row; statement replaced where it is a
    cohort-wide statement. Retire rows and rows that already name a sample pass through."""
    out = []
    for r in rows.to_dict("records"):
        if r["action"] != "recode" or r["sample_key"] or not r["old_value"]:
            out.append(r)
            continue
        g = idx.get((r["study_accession"], r["field_name"]))
        assert g is not None, (r["study_accession"], r["field_name"])
        m = g[g.value_normalized.astype(str) == r["old_value"]]
        if r["old_route"]:
            m = m[m.route == r["old_route"]]
        assert len(m), ("no current value matches", r["study_accession"], r["field_name"], r["old_value"], r["old_route"])
        stmt = m.parse_note.astype(str).str.contains("expanded from study_all")
        if stmt.any():
            out.append(dict(r, origin=r["origin"] + ":statement"))
        for sk in m.sample_key[~stmt]:
            out.append(dict(r, sample_key=sk))
    return out


def own_rows(cur: pd.DataFrame, attrs_path: str, classes: pd.DataFrame, package: str) -> list[dict]:
    rows = []
    hc = cur[cur.field_name == "health_condition"]
    # PRJNA474024: the stored statement itself names ulcerative colitis; the code was the unspecified IBD one
    x = hc[(hc.study_accession == "PRJNA474024") & (hc.value_normalized == "ibd_unspecified")]
    assert x.field_value.nunique() == 1 and "ulcerative colitis" in x.field_value.iloc[0] and x.route.eq("R4").all()
    uc_quote = "Patients with UC were selectively enrolled and treated initially with endoscopic FMT"
    rs = pd.read_parquet(os.path.join(package, "registry_studies.parquet"), columns=["study_accession", "description_short"])
    assert uc_quote in rs.loc[rs.study_accession == "PRJNA474024", "description_short"].iloc[0]
    rows.append(_row(study_accession="PRJNA474024", field_name="health_condition", action="recode", old_value="ibd_unspecified", old_route="R4",
                     new_value="ulcerative_colitis", evidence_source="ena.study_description", evidence_locator="PRJNA474024", evidence_quote=uc_quote,
                     confidence=0.8, rationale="the project description says the patients have UC (ulcerative colitis, as the stored value spells out); the broader IBD code was stored", origin="own:ibd_statement"))
    # PRJEB64515 / PRJEB91718 (hospitalised COVID-19 patients and healthy staff controls): the BioSample `subject type` decides the arm.
    # (a) controls labelled COVID-19 by an external resource -> healthy_control; (b) patients coded by a named comorbidity -> the
    # cohort condition, as the checker did for the bare yes/no flags. Evidence = the checker's rows for the same projects.
    import pyarrow.dataset as ds
    at = ds.dataset(attrs_path).to_table(filter=ds.field("study_accession").isin(["PRJEB64515", "PRJEB91718"]) & (ds.field("attr_key") == "subject type"),
                                         columns=["sample_acc", "attr_value"]).to_pandas().drop_duplicates("sample_acc").set_index("sample_acc").attr_value
    ev = {}
    for acc, new in (("PRJEB64515", "healthy_control"), ("PRJEB64515", "other_infection"), ("PRJEB91718", "other_infection")):
        e = classes[(classes.study_accession == acc) & (classes.new_value == new)].iloc[0]
        ev[(acc, new)] = dict(evidence_source=e.evidence_source, evidence_locator=e.evidence_locator, evidence_quote=e.evidence_quote, confidence=float(e.confidence))
    h = hc[hc.study_accession.isin(["PRJEB64515", "PRJEB91718"])].assign(arm=lambda d: d.sample_key.map(at))
    a = h[(h.study_accession == "PRJEB64515") & (h.arm == "control") & (h.value_normalized == "other_infection") & (h.evidence_source == "external.gmrepo")]
    for r in a.itertuples(index=False):
        rows.append(_row(sample_key=r.sample_key, study_accession=r.study_accession, field_name="health_condition", action="recode", old_value="other_infection",
                         old_route="R2", new_value="healthy_control", rationale="BioSample subject type = control; the external resource labels the staff controls as COVID-19 cases",
                         origin="own:covid_controls", **ev[("PRJEB64515", "healthy_control")]))
    b = h[(h.arm == "patient (case)") & h.value_normalized.isin(["type2_diabetes", "type1_diabetes", "other_cancer", "colorectal_cancer"]) & (h.route == "R1")]
    done = set(zip(classes.sample_key, classes.study_accession))
    for r in b.itertuples(index=False):
        if (r.sample_key, r.study_accession) in done:
            continue
        rows.append(_row(sample_key=r.sample_key, study_accession=r.study_accession, field_name="health_condition", action="recode", old_value=r.value_normalized,
                         old_route="R1", new_value="other_infection", rationale=f"BioSample subject type = patient (case); comorbidity attribute ({r.evidence_quote}) "
                         "does not replace the cohort condition", origin="own:covid_comorbidity", **ev[(r.study_accession, "other_infection")]))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit-dir", default="docs/audits/r38")
    ap.add_argument("--package", default="data/inputs/data_package", help="the previous (R2026.37) package, unpacked")
    ap.add_argument("--attributes", default="data/inputs/registry/registry_biosample_attributes.parquet")
    ap.add_argument("--out", default="data/inputs/gut/dq_corrections_r38.parquet")
    a = ap.parse_args(argv)
    life = set(yaml.safe_load(open("config/vocab/lifestyle.yaml"))["codes"])
    cur = pd.read_parquet(os.path.join(a.package, "catalog_sample_determinations.parquet"),
                          columns=["sample_key", "field_name", "study_accession", "field_value", "value_normalized", "route", "evidence_source", "evidence_quote", "parse_note", "release_retired"])
    cur = cur[cur.release_retired.isna()]
    cl = pd.read_csv(os.path.join(a.audit_dir, "corrections_classes.csv"), dtype=str).fillna("").assign(origin="classes")
    pr = pd.read_csv(os.path.join(a.audit_dir, "corrections_projects.csv"), dtype=str).fillna("").assign(origin="projects")
    # PRJEB6456 country: the archive attribute (Denmark) contradicts the paper's table (Sweden) and both were withheld. The checker's
    # project-wide Sweden statement replaces the archive value; the per-sample values from the paper's table then stand.
    m = (pr.study_accession == "PRJEB6456") & (pr.field_name == "country")
    assert m.sum() == 1 and pr.loc[m, "new_value"].iloc[0] == "SE"
    pr.loc[m, ["old_value", "old_route"]] = ["DK", "R1"]
    # PRJNA310878 age: the unit is not stated anywhere, so no source's reading of it stands (one prior resource reads the same
    # bare numbers as days, the archive normaliser as years)
    m = (pr.study_accession == "PRJNA310878") & (pr.field_name == "age_at_collection_days") & (pr.action == "retire")
    assert m.sum() == 1
    pr.loc[m, "old_route"] = ""
    both = pd.concat([cl, pr], ignore_index=True).drop_duplicates(["study_accession", "field_name", "sample_key", "action", "old_value"], keep="first")
    both["old_determined_by"] = ""
    sub = [r for r in both.to_dict("records")]
    stmt_ok = {("PRJEB6456", "country")}
    want = set(zip(both.study_accession, both.field_name))
    sel = cur[cur.study_accession.isin(set(both.study_accession)) & cur.field_name.isin(set(both.field_name))]
    idx = {k: g for k, g in sel.groupby(["study_accession", "field_name"]) if k in want}
    exp = []
    for r in sub:
        if (r["study_accession"], r["field_name"]) in stmt_ok:
            exp.append(dict(r, origin=r["origin"] + ":statement"))
        else:
            exp += expand(pd.DataFrame([r]), idx)
    rows = antibiotic_rows(a.audit_dir) + lifestyle_rows(a.audit_dir, life) + exp + own_rows(cur, a.attributes, cl, a.package)
    out = pd.DataFrame(rows)
    # a sample reached both by a checker's project-level row and by the arm rule keeps the checker's row
    per = out.sample_key.fillna("").ne("")
    out = pd.concat([out[~per], out[per].drop_duplicates(["sample_key", "field_name", "old_value"], keep="first")], ignore_index=True)
    out["determined_by"] = BY
    for c in COLS:
        if c not in out.columns:
            out[c] = ""
    out = out[COLS]
    out["confidence"] = pd.to_numeric(out.confidence, errors="coerce")
    for c in COLS:
        if c != "confidence":
            out[c] = out[c].fillna("").astype(str)
    rec = out[out.action == "recode"]
    assert (rec.new_value != "").all() and (rec.evidence_quote.str.len() > 0).all() and rec.confidence.notna().all()
    assert not out[out.sample_key != ""].duplicated(["sample_key", "field_name", "old_value", "old_route"]).any()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    out.to_parquet(a.out, index=False)
    print(out.groupby(["field_name", "action", out.sample_key.ne("").map({True: "per-sample", False: "project"})]).size().to_string())
    print("rows", len(out), "projects", out.study_accession.nunique(), "->", a.out)


if __name__ == "__main__":
    main()
