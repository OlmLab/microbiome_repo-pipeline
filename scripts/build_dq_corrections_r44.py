#!/usr/bin/env python
"""R2026.44 corrections: condition labels settled by the Conditions chat, case / control fixes, and value fixes that a
head-to-head with another resource (Metalog) and the blind audit proposed and a worker confirmed against the cited source.

Inputs (docs/audits/r44/):
  condition_pair_changes_r44.csv    one row per (project, code, detail) pair whose label changes: the detail always, the code
                                    where the readings name another one; `retire` withdraws code and detail
  condition_sample_changes_r44.csv  per-sample rows (case / control fixes)
  confirmed_fixes_r44.csv           per-sample rows of any field, each re-derived from its source (optional)
Output: data/inputs/gut/dq_corrections_r44.parquet in the layout build_gut_scope.apply_corrections reads (GUT_CORRECTIONS).

Rules
  * a pair is looked up among the CURRENT samples of --package (the release before R2026.44); pairs that no longer exist are counted;
  * rows are per sample, determined_by dq_adjudication_v2:r44 (a checked value: the whole quote is kept and the value ranks above
    automatic routes);
  * the free-text source descriptions of the workers are mapped to the catalog's source labels (ena.study_description,
    biosample.attribute, paper.fulltext[.methods], paper.supp...), because the route of a value is derived from that label;
  * fields that are not determinations (body_site_code, sample_type) cannot be corrected through this file: they are written to
    --other-out for the build's own override inputs and are NOT in the parquet;
  * a sample and field corrected twice keeps one row: a per-sample fix wins over a change made for its whole pair.
"""
from __future__ import annotations

import argparse
import os
import re

import pandas as pd

BY = "dq_adjudication_v2:r44"
COLS = ["sample_key", "study_accession", "field_name", "action", "old_value", "old_route", "old_determined_by", "new_value",
        "evidence_source", "evidence_locator", "evidence_quote", "confidence", "rationale", "determined_by", "origin"]
NOT_DETERMINATIONS = {"body_site_code", "sample_type"}
PMC = re.compile(r"PMC\d+")
ACC = re.compile(r"\b(?:SAM[NED]A?\d+|SAMEA\d+|[EDS]R[RSX]\d+)\b")


def source_label(text: str) -> str:
    """Worker's description of a source -> the catalog's source label (the part before any bracket is what decides the route)."""
    s = str(text or "").strip()
    low = s.lower()
    if low.startswith(("biosample.attribute", "paper.supp", "paper.fulltext", "paper.abstract", "ena.", "run.", "sample.attr")):
        return s.split(" (")[0].strip()
    if "supp" in low or "table s" in low or ".xlsx" in low or ".csv" in low or ".docx" in low:
        return "paper.supp"
    if "biosample" in low or "attribute" in low or "package determinations" in low or "sample name" in low or "sample title" in low:
        return "biosample.attribute"
    if "abstract" in low:
        return "paper.abstract"
    if "paper" in low or "pmc" in low:
        return "paper.fulltext.methods" if "method" in low else "paper.fulltext"
    return "ena.study_description"


def locator(src_label: str, given: str, study: str, sample: str = "") -> str:
    g = str(given or "")
    if src_label.startswith("paper"):
        m = PMC.search(g)
        return m.group(0) if m else (g or study)
    if src_label.startswith("biosample") or src_label.startswith("run."):
        m = ACC.search(g)
        return m.group(0) if m else (sample or study)
    return study


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="docs/audits/r44")
    ap.add_argument("--package", required=True, help="unpacked package the pairs are looked up in (the release before R2026.44)")
    ap.add_argument("--out", default="data/inputs/gut/dq_corrections_r44.parquet")
    ap.add_argument("--other-out", default="docs/audits/r44/non_determination_fixes_r44.csv")
    a = ap.parse_args(argv)
    rows, stats = [], {}

    # 1. pair changes
    d = pd.read_csv(os.path.join(a.dir, "condition_pair_changes_r44.csv"), dtype=str, keep_default_na=False)
    w = pd.read_parquet(os.path.join(a.package, "catalog_sample_metadata_wide.parquet"), filters=[("study_accession", "in", sorted(set(d.study_accession)))],
                        columns=["sample_key", "study_accession", "release_retired", "health_condition", "health_condition_detail"])
    w = w[w.release_retired.isna()]
    w["code"] = w.health_condition.fillna("").astype(str)
    w["detail"] = w.health_condition_detail.fillna("").astype(str)
    grp = {k: g.sample_key.astype(str).tolist() for k, g in w.groupby(["study_accession", "code", "detail"])}
    st = {"pairs": 0, "pairs_without_samples": 0, "samples": 0, "code_changed": 0, "withdrawn": 0}
    for r in d.itertuples(index=False):
        keys = grp.get((r.study_accession, r.old_health_condition, r.old_health_condition_detail), [])
        st["pairs"] += 1
        if not keys:
            st["pairs_without_samples"] += 1
            continue
        lab = source_label(r.evidence_source)
        base = dict(study_accession=r.study_accession, old_route="", old_determined_by="", evidence_source=lab,
                    evidence_locator=locator(lab, r.evidence_locator, r.study_accession), evidence_quote=r.evidence_quote,
                    confidence=float(r.confidence or 0.8), determined_by=BY, origin=f"conditions:{r.origin}")
        for k in keys:
            if r.action == "retire":
                for f in ("health_condition", "health_condition_detail"):
                    rows.append(dict(base, sample_key=k, field_name=f, action="retire", old_value="", new_value="", rationale=r.rationale))
                st["withdrawn"] += 1
                continue
            if r.new_health_condition_detail and r.new_health_condition_detail != r.old_health_condition_detail:
                rows.append(dict(base, sample_key=k, field_name="health_condition_detail", action="recode", old_value="",
                                 new_value=r.new_health_condition_detail[:120], rationale=(r.rationale + f" Stored detail was: {r.old_health_condition_detail[:80]}")[:400]))
            if r.new_health_condition and r.new_health_condition != r.old_health_condition:
                rows.append(dict(base, sample_key=k, field_name="health_condition", action="recode", old_value=r.old_health_condition,
                                 new_value=r.new_health_condition, rationale=r.rationale))
                st["code_changed"] += 1
            st["samples"] += 1
    stats["pair_changes"] = st

    # 2. + 3. per-sample files
    other, sample_rows = [], []
    for name in ("condition_sample_changes_r44.csv", "confirmed_fixes_r44.csv"):
        f = os.path.join(a.dir, name)
        if not os.path.exists(f):
            stats[name] = "absent"
            continue
        s = pd.read_csv(f, dtype=str, keep_default_na=False)
        n_det = 0
        for r in s.itertuples(index=False):
            lab = source_label(r.evidence_source)
            row = dict(sample_key=r.sample_key, study_accession=r.study_accession, field_name=r.field_name, action=r.action or "recode",
                       old_value="", old_route="", old_determined_by="", new_value=r.new_value, evidence_source=lab,
                       evidence_locator=locator(lab, r.evidence_locator, r.study_accession, r.sample_key), evidence_quote=r.evidence_quote,
                       confidence=float(r.confidence or 0.8), rationale=(r.rationale + (f" Was: {r.old_value[:60]}" if r.old_value else ""))[:400],
                       determined_by=BY, origin=f"{name.split('_r44')[0]}:{r.origin}")
            if r.field_name in NOT_DETERMINATIONS:
                other.append(dict(row, old_value=r.old_value))
            else:
                sample_rows.append(row)
                n_det += 1
        stats[name] = {"rows": len(s), "determination_rows": n_det, "fields": s.field_name.value_counts().to_dict()}

    # a per-sample fix is the more specific statement: it comes first and wins over a change made for the whole pair
    rows = sample_rows + rows
    out = pd.DataFrame(rows, columns=COLS)
    dup = out.duplicated(["sample_key", "field_name"], keep="first")
    out = out[~dup]
    out.to_parquet(a.out, index=False)
    pd.DataFrame(other, columns=COLS).to_csv(a.other_out, index=False)
    print(stats, {"dropped_second_row_for_same_sample_field": int(dup.sum()), "rows": len(out), "non_determination_rows": len(other)},
          out.field_name.value_counts().to_dict(), out.action.value_counts().to_dict(), out.evidence_source.value_counts().to_dict())


if __name__ == "__main__":
    main()
