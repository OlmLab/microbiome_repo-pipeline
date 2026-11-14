#!/usr/bin/env python
"""R2026.41 corrections: health-condition labels whose stored detail did not say what the samples are.

Reads docs/audits/r41/condition_string_decisions.csv (one row per (project, code, detail) pair: two independent Opus 5 readings,
a third reading where the two differed, the audit verdict where the pair was audited, and the decision) and writes data/inputs/gut/dq_corrections_r41.parquet, a file
build_gut_scope.apply_corrections reads (GUT_CORRECTIONS in the Makefile).

Rules
  * decision `correct`: every current sample of the project that carries the (code, detail) pair in --package gets the explicit
    detail; its health_condition is recoded only when both readings named the same other code;
  * decision `withdraw`: health_condition and its detail are withdrawn from those samples (the label is not a statement about a
    health condition and an audit confirmed that the project gives no other basis for the code);
  * every other decision changes nothing;
  * label hunt (docs/audits/r41/label_hunt_decisions.csv): per-sample case / control labels read from an archive attribute,
    a run field or a name pattern whose meaning the project's text states, proposed by one Opus 5 reader and checked by a
    second; written for the samples the rule covers;
  * rows are per sample and determined_by dq_adjudication_v2:r41 (checked value: full quote kept, ranked above automatic routes).
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

BY = "dq_adjudication_v2:r41"
COLS = ["sample_key", "study_accession", "field_name", "action", "old_value", "old_route", "old_determined_by", "new_value",
        "evidence_source", "evidence_locator", "evidence_quote", "confidence", "rationale", "determined_by", "origin"]
CONF = {"high": 0.9, "medium": 0.8, "low": 0.7}


def hunt_rows(a, rows, pid):
    """Per-sample case / control labels found by the label hunt (docs/audits/r41/label_hunt_decisions.csv: one row per project
    with the first reader's rule, the verifier's verdict and the decision; docs/audits/r41/label_hunt_assign/<project>.csv: the
    samples the rule covers). Only decision `write` is used; rows are limited to current samples of the project in --package and,
    for a partial acceptance, to the accepted groups (role and code)."""
    f = os.path.join(os.path.dirname(a.decisions), "label_hunt_decisions.csv")
    st = {"hunt_projects": 0, "hunt_samples": 0, "hunt_code_written": 0, "hunt_samples_not_current": 0}
    if not os.path.exists(f):
        return st
    h = pd.read_csv(f, dtype=str).fillna("")
    h = h[h.decision == "write"]
    if not len(h):
        return st
    w = pd.read_parquet(os.path.join(a.package, "catalog_sample_metadata_wide.parquet"), filters=[("study_accession", "in", sorted(set(h.study_accession)))],
                        columns=["sample_key", "study_accession", "release_retired", "health_condition", "health_condition_detail"])
    w = w[w.release_retired.isna()].set_index("sample_key")
    for r in h.itertuples():
        g = pd.read_csv(os.path.join(os.path.dirname(a.decisions), "label_hunt_assign", f"{r.study_accession}.csv"), dtype=str).fillna("")
        if r.accepted_groups:
            ok = set(tuple(x.split("=", 1)) for x in r.accepted_groups.split("|"))
            g = g[[(ro, co) in ok for ro, co in zip(g.role, g.catalog_code)]]
        cur = w[w.study_accession == r.study_accession]
        st["hunt_samples_not_current"] += int((~g.sample_key.isin(cur.index)).sum())
        g = g[g.sample_key.isin(cur.index) & g.catalog_code.ne("")].drop_duplicates("sample_key")
        loc = (pid.get(r.study_accession, "") or r.study_accession) if r.evidence_source.startswith("paper") else r.study_accession
        base = dict(study_accession=r.study_accession, old_route="", old_determined_by="", evidence_source=r.evidence_source, evidence_locator=loc,
                    evidence_quote=r.quote_definition, confidence=0.85 if r.definition == "text" else 0.75, determined_by=BY, origin="label_hunt")
        why = ("Group label of the project's comparison, read from " + r.rule[:120] + "; checked by a second reader"
               + (" (meaning fixed by the stated group sizes)" if r.definition == "sizes_only" else "") + ".")
        for x in g.itertuples():
            old = cur.health_condition.get(x.sample_key)
            old = "" if old is None or old != old else str(old)
            if old != x.catalog_code:
                rows.append(dict(base, sample_key=x.sample_key, field_name="health_condition", action="recode", old_value="", new_value=x.catalog_code, rationale=why))
                st["hunt_code_written"] += 1
            rows.append(dict(base, sample_key=x.sample_key, field_name="health_condition_detail", action="recode", old_value="", new_value=x.detail[:120], rationale=why))
            st["hunt_samples"] += 1
        st["hunt_projects"] += 1
    return st


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--decisions", default="docs/audits/r41/condition_string_decisions.csv")
    ap.add_argument("--package", required=True, help="unpacked package the pairs are looked up in (the release before R2026.41)")
    ap.add_argument("--papers", default="data/inputs/catalog/study_papers.parquet")
    ap.add_argument("--out", default="data/inputs/gut/dq_corrections_r41.parquet")
    a = ap.parse_args(argv)
    d = pd.read_csv(a.decisions, dtype=str).fillna("")
    d = d[d.decision.isin(["correct", "withdraw"])]
    w = pd.read_parquet(os.path.join(a.package, "catalog_sample_metadata_wide.parquet"), filters=[("study_accession", "in", sorted(set(d.study_accession)))],
                        columns=["sample_key", "study_accession", "release_retired", "health_condition", "health_condition_detail"])
    w = w[w.release_retired.isna()]
    w["code"] = w.health_condition.fillna(""); w["detail"] = w.health_condition_detail.fillna("")
    pp = pd.read_parquet(a.papers)
    pp = pp[pp.relation == "own_data"]
    pid = {}
    for r in pp.itertuples():
        pid.setdefault(r.study_accession, str(getattr(r, "pmcid", "") or "") or ("PMID:" + str(r.pmid) if str(getattr(r, "pmid", "") or "") not in ("", "nan", "None") else ""))
    rows, stats = [], {"pairs": 0, "pairs_without_samples": 0, "samples": 0, "recoded": 0, "withdrawn": 0}
    for r in d.itertuples():
        s = w[(w.study_accession == r.study_accession) & (w.code == r.code) & (w.detail == r.detail)]
        stats["pairs"] += 1
        if not len(s):
            stats["pairs_without_samples"] += 1
            continue
        loc = (pid.get(r.study_accession, "") or r.study_accession) if r.evidence_source.startswith("paper") else r.study_accession
        conf = min(CONF.get(r.confidence_a, 0.8), CONF.get(r.confidence_b, 0.8))
        base = dict(study_accession=r.study_accession, old_route="", old_determined_by="", evidence_source=r.evidence_source, evidence_locator=loc,
                    evidence_quote=r.quote_final, confidence=conf, determined_by=BY, origin=f"condition_strings:item{r.item_id}")
        for k in s.sample_key.astype(str):
            if r.decision == "withdraw":
                why = "The label is not a statement about a health condition; the readings and a check found no other basis for the code."
                rows.append(dict(base, sample_key=k, field_name="health_condition", action="retire", old_value="", new_value="", rationale=why))
                rows.append(dict(base, sample_key=k, field_name="health_condition_detail", action="retire", old_value="", new_value="", rationale=why))
                stats["withdrawn"] += 1
                continue
            why = f"Independent readings of the project's texts agree on what the label means; the detail now says it. Stored detail was: {r.detail[:80]}"
            rows.append(dict(base, sample_key=k, field_name="health_condition_detail", action="recode", old_value="", new_value=r.new_detail[:120], rationale=why))
            if r.new_code and r.new_code != r.code:
                rows.append(dict(base, sample_key=k, field_name="health_condition", action="recode", old_value=r.code, new_value=r.new_code,
                                 rationale=f"The agreeing readings name the code {r.new_code} for these samples."))
                stats["recoded"] += 1
            stats["samples"] += 1
    hstats = hunt_rows(a, rows, pid)
    out = pd.DataFrame(rows, columns=COLS)
    dup = out.duplicated(["sample_key", "field_name"], keep="first")
    out = out[~dup]
    out.to_parquet(a.out, index=False)
    print(stats, hstats, {"dropped_second_row_for_same_sample_field": int(dup.sum())}, len(out), out.field_name.value_counts().to_dict(), out.action.value_counts().to_dict())


if __name__ == "__main__":
    main()
