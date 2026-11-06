"""integrity_checks.py — cross-table release checks on the CURRENT rows of a built package (R2026.36).

Lessons from 1.29.1 (2026-11-06): two false alarms came from counting retired rows (release_retired set) and from grouping
people by person_key alone (it is unique only within a project). Every check here uses current rows and (study, person) pairs.

    python -m catalog.release.integrity_checks --package build/package [--out build/integrity_checks.json] [--sandpiper-genus FILE]

FAIL checks stop the release (exit 1); WARN checks are reported.
  F1 sample_key unique among current wide-table rows
  F2 current samples without catalog_runs rows <= --max-samples-without-runs (archive has no runs for a few)
  F3 Sandpiper summary rows belong to current samples
  F4 every Sandpiper sample has a profiled run in catalog_runs (sandpiper_profiled true, listed in runs_profiled)
  F5 Sandpiper study coverage n_samples equals current wide-table rows per project
  F6 person_invariant determination rows copy a value from a sample of the same project and same person
  F7 no (study_accession, person_key) carries two sex values
  F8 ages within 0-122 years; collection dates 1980-01-01 .. build date (W3 lists ages above 110 years)
  F9 Sandpiper genus table (when given) only holds summary samples
  W1 (study, person) groups with two country values
  W2 retired rows present (count; they are kept for history, never counted)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import pandas as pd


def run(pkg: str, max_without_runs: int = 100, genus: str | None = None) -> dict:
    res = {"fail": {}, "warn": {}, "info": {}}
    W = pd.read_parquet(os.path.join(pkg, "catalog_sample_metadata_wide.parquet"),
                        columns=["sample_key", "study_accession", "release_retired", "person_key", "sex", "country",
                                 "age_at_collection_days", "collection_date"])
    w = W[W.release_retired.isna()]
    res["info"]["wide_rows"], res["info"]["current_samples"] = len(W), len(w)
    res["warn"]["W2_retired_rows"] = int(W.release_retired.notna().sum())

    def fail(k, n, detail=None):
        res["fail"][k] = {"n": int(n), **({"detail": detail} if detail is not None else {})}

    fail("F1_duplicate_current_sample_keys", w.sample_key.duplicated().sum())
    runs = pd.read_parquet(os.path.join(pkg, "catalog_runs.parquet"), columns=["run_accession", "sample_key", "sandpiper_profiled"])
    no_runs = w[~w.sample_key.isin(set(runs.sample_key))]
    n = len(no_runs)
    fail("F2_current_samples_without_runs", n if n > max_without_runs else 0,
         {"count": n, "allowed": max_without_runs, "projects": no_runs.study_accession.value_counts().head(10).to_dict()})
    sp_p = os.path.join(pkg, "catalog_sandpiper_sample_summary.parquet")
    if os.path.exists(sp_p):
        sp = pd.read_parquet(sp_p, columns=["sample_key", "study_accession", "runs_profiled"])
        cur = set(w.sample_key)
        fail("F3_sandpiper_rows_not_current", (~sp.sample_key.isin(cur)).sum())
        prof = runs[runs.sandpiper_profiled.fillna(False).astype(bool)]
        ex = sp.assign(r=sp.runs_profiled.astype(str).str.split(";")).explode("r")
        ok = ex.merge(prof[["run_accession", "sample_key"]], left_on=["r", "sample_key"], right_on=["run_accession", "sample_key"], how="inner")
        fail("F4_sandpiper_samples_without_flagged_profiled_run", (~sp.sample_key.isin(set(ok.sample_key))).sum())
        res["info"]["sandpiper_samples"], res["info"]["sandpiper_share_of_current"] = len(sp), round(len(sp) / max(len(w), 1), 4)
        cov_p = os.path.join(pkg, "catalog_sandpiper_study_coverage.csv")
        if os.path.exists(cov_p):
            cov = pd.read_csv(cov_p, usecols=["study_accession", "n_samples"]).set_index("study_accession").n_samples
            per = w.groupby("study_accession").size()
            j = pd.concat([cov.rename("cov"), per.rename("wide")], axis=1).fillna(0)
            fail("F5_coverage_n_samples_mismatch", (j["cov"] != j["wide"]).sum(), j[j["cov"] != j["wide"]].head(5).astype(int).to_dict("index"))
        if genus and os.path.exists(genus):
            import pyarrow.dataset as ds
            gk = set(ds.dataset(genus, format="parquet").to_table(columns=["sample_key"]).column("sample_key").unique().to_pylist())
            fail("F9_genus_samples_not_in_summary", len(gk - set(sp.sample_key)))
    det = pd.read_parquet(os.path.join(pkg, "catalog_sample_determinations.parquet"),
                          columns=["sample_key", "study_accession", "field_name", "determined_by", "parse_note", "release_retired"],
                          filters=[("determined_by", "=", "person_invariant_v1")])
    det = det[det.release_retired.isna()]
    if len(det):
        donor = det.parse_note.astype(str).str.extract(r" as (\S+);")[0]
        pk = w.set_index("sample_key")
        same = (donor.map(pk.study_accession) == det.study_accession.values) & (donor.map(pk.person_key) == det.sample_key.map(pk.person_key))
        fail("F6_person_invariant_rows_outside_person", (~same.fillna(False)).sum())
    res["info"]["person_invariant_rows"] = len(det)
    key = w.study_accession.astype(str) + "\x1f" + w.person_key.astype(str)
    real = w.person_key.notna() & ~w.person_key.astype(str).str.startswith("sample:")
    for f, code, kind in (("sex", "F7_people_with_two_sex_values", "fail"), ("country", "W1_people_with_two_country_values", "warn")):
        m = real & w[f].notna()
        nn = w.loc[m, f].astype(str).groupby(key[m]).nunique()
        bad = nn[nn > 1]
        if kind == "fail":
            fail(code, len(bad))
        else:
            res["warn"][code] = {"n": int(len(bad)), "projects": pd.Series([k.split("\x1f")[0] for k in bad.index]).value_counts().head(10).to_dict()}
    age = pd.to_numeric(w.age_at_collection_days, errors="coerce")
    vj = json.load(open(os.path.join(pkg, "VERSION.json"))) if os.path.exists(os.path.join(pkg, "VERSION.json")) else {}
    bd = pd.Timestamp(str(vj.get("build_date") or pd.Timestamp.today().date()))
    d = pd.to_datetime(w.collection_date.astype(str).str.extract(r"^(\d{4}(?:-\d{2}(?:-\d{2})?)?)")[0], errors="coerce", format="mixed")
    fail("F8_values_out_of_range", int(((age < 0) | (age > 122 * 365.25)).sum() + ((d < pd.Timestamp("1980-01-01")) | (d > bd)).sum()),
         {"age_lt0": int((age < 0).sum()), "age_gt122y": int((age > 122 * 365.25).sum()), "date_lt1980": int((d < pd.Timestamp("1980-01-01")).sum()), "date_after_build": int((d > bd).sum())})
    res["warn"]["W3_ages_above_110y"] = {"n": int((age > 110 * 365.25).sum()), "projects": w[age > 110 * 365.25].study_accession.value_counts().to_dict()}
    res["ok"] = all(v["n"] == 0 for v in res["fail"].values())
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True); ap.add_argument("--out")
    ap.add_argument("--max-samples-without-runs", type=int, default=100); ap.add_argument("--sandpiper-genus")
    a = ap.parse_args(argv)
    r = run(a.package, a.max_samples_without_runs, a.sandpiper_genus)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(r, open(a.out, "w"), indent=1, default=str)
    for k, v in r["fail"].items():
        print(("FAIL " if v["n"] else "ok   ") + k, v)
    for k, v in r["warn"].items():
        print("warn " + k, v)
    print("info", r["info"])
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
