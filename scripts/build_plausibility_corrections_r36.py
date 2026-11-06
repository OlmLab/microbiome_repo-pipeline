#!/usr/bin/env python
"""build_plausibility_corrections_r36.py — turn the R2026.36 Opus readings into data/inputs/gut/plausibility_corrections.csv.

Readings (data/inputs/gut/verify_r2026_36/): Plausibility_1.json / Plausibility_2.json (projects with follow-up over 20 years,
impossible ages, dates before 1990) and Country_conflicts.json (people whose samples disagree on country). Only verdicts with
confidence >= 0.7 are applied; each rule below names the project, the reading's finding and the archive attribute it uses. New
subject ids are built from BioSample attributes (data/inputs/registry/registry_biosample_attributes.parquet) exactly as the
reading specifies; existing values are matched against the previous package's current determinations.
Also writes negative controls the readings identified to config/sample_types.csv (action mark) and a log of what was not applied.
usage: python scripts/build_plausibility_corrections_r36.py [--package data/inputs/data_package]
"""
import argparse
import csv
import json
import os
import re

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V = os.path.join(ROOT, "data", "inputs", "gut", "verify_r2026_36")
OUT = os.path.join(ROOT, "data", "inputs", "gut", "plausibility_corrections.csv")
MIN_CONF = 0.7
YEAR = 365.25


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--package", default=os.path.join(ROOT, "data", "inputs", "data_package"))
    a = ap.parse_args()
    R = {}
    for f in ("Plausibility_1.json", "Plausibility_2.json", "Country_conflicts.json"):
        d_ = json.load(open(os.path.join(V, f)))
        for p in (d_.get("structured_output") or d_)["projects"]:   # raw reading output, or the audit/r2026_36/readings copy
            R[(f.split(".")[0], p["study_accession"])] = p
    projects = sorted({k[1] for k in R})
    det = pd.read_parquet(os.path.join(a.package, "catalog_sample_determinations.parquet"),
                          columns=["sample_key", "study_accession", "field_name", "value_normalized", "evidence_source", "release_retired"],
                          filters=[("study_accession", "in", projects)])
    det = det[det.release_retired.isna()]
    attr = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "registry", "registry_biosample_attributes.parquet"),
                           columns=["sample_acc", "study_accession", "attr_key", "attr_value"], filters=[("study_accession", "in", projects)])
    A = attr.pivot_table(index="sample_acc", columns="attr_key", values="attr_value", aggfunc="first")

    def cur(acc, field):
        return det[(det.study_accession == acc) & (det.field_name == field)]

    rows, skipped = [], []

    def ev(src, acc):
        p = R[(src, acc)]
        return p["evidence_source"], p["evidence_quote"], float(p["confidence"])

    def add(src, acc, field, keys, action, rule_id, reason, new=""):
        es, eq, cf = ev(src, acc)
        if cf < MIN_CONF:
            skipped.append((acc, field, rule_id, f"confidence {cf} < {MIN_CONF}")); return
        if isinstance(keys, (list, set, tuple)):
            keys = ";".join(sorted(set(keys)))
            if not keys:
                skipped.append((acc, field, rule_id, "no matching samples")); return
        rows.append(dict(study_accession=acc, field=field, sample_keys=keys, action=action, new_value=new, rule_id=rule_id,
                         reason=reason, evidence_source=es, evidence_quote=eq))

    def set_each(src, acc, field, mapping, rule_id, reason):   # one 'set' row per sample (sample -> new value)
        for k, v in sorted(mapping.items()):
            if v is not None and str(v) != "":
                add(src, acc, field, k, "set", rule_id, reason, str(v))

    P1, P2, CC = "Plausibility_1", "Plausibility_2", "Country_conflicts"
    # ---------------- Plausibility 2 (projects with impossible values / pre-1990 dates)
    add(P2, "PRJEB91718", "age_at_collection_days", "all", "clear", "P36_age_misaligned", "'host age' is positionally misaligned with the samples it sits on")
    k = ["SAMN32942486", "SAMN32942487", "SAMN32942552", "SAMN32942553", "SAMN32942554"]
    add(P2, "PRJNA928758", "age_at_collection_days", k, "clear", "P36_age_contradictory", "patients 054 and 105 carry contradictory host_age values across visits days apart")
    add(P2, "PRJNA928758", "sex", k, "clear", "P36_sex_contradictory", "same records as the contradictory ages (patients 054 and 105)")
    add(P2, "PRJEB32762", "age_at_collection_days", ["SAMEA10314058", "SAMEA10314059", "SAMEA10314138", "SAMEA10314139", "SAMEA10314761", "SAMEA10314762",
                                                     "SAMEA10314783", "SAMEA10314784", "SAMEA10314785"], "clear", "P36_age_sentinel", "host_age -1, -2 or -957 years is a missing-value sentinel")
    add(P2, "PRJNA1031616", "collection_date", "SAMN37947821", "set", "P36_year_typo", "two-digit year typo inside the 2022 study period", "2022-05-19")
    add(P2, "PRJNA1031616", "collection_date", "SAMN37947814", "set", "P36_year_typo", "two-digit year typo inside the 2022 study period", "2022-06-13")
    add(P2, "PRJNA521587", "age_at_collection_days", ["SAMN10886036", "SAMN10886139"], "clear", "P36_age_sentinel", "age 119 and -959 in an adult cohort")
    add(P2, "PRJNA1138254", "age_at_collection_days", "all", "clear", "P36_age_row_counter", "BioSample 'age' is a 1..180 submission row counter")
    d = cur("PRJEB55787", "collection_date")
    pre = d[pd.to_datetime(d.value_normalized, errors="coerce", format="mixed") < pd.Timestamp("2016-01-01")].sample_key
    add(P2, "PRJEB55787", "collection_date", list(pre), "clear", "P36_birth_date", "the patient arm's 'collection date' is a date of birth (cohort recruited 2016-2021)")
    add(P2, "PRJNA679286", "collection_date", "all", "clear", "P36_placeholder_date", "one placeholder date (1980-06-01) on every sample")
    add(P2, "PRJNA1036630", "collection_date", "all", "clear", "P36_biobank_interval", "'1989/2018' is the biobank's collection interval on pooled records")
    # ---------------- Plausibility 1 (follow-up over 20 years)
    s = cur("PRJEB55648", "subject_id").sample_key
    set_each(P1, "PRJEB55648", "subject_id", {k: f"{A.at[k, 'Village']}|{A.at[k, 'Subject_ID']}" for k in s if k in A.index and pd.notna(A.at[k, "Village"])},
             "P36_regroup_village", "Subject_ID is a per-village participant number: person = Village + Subject_ID")
    d = cur("PRJEB38909", "age_at_collection_days")
    add(P1, "PRJEB38909", "age_at_collection_days", list(d[d.evidence_source.astype(str).str.contains("age_of_first_use|age_at_diagnosis")].sample_key) + ["SAMEA115100512"],
        "clear", "P36_age_wrong_attribute", "age read from an age-of-first-use attribute, or host_age 20 on a participant aged about 70 at the next visit")
    sb = cur("PRJNA1169096", "subject_id")
    don = list(sb[sb.value_normalized.isin(["D1", "D2", "D3"])].sample_key) + ["SAMN44065058"]
    add(P1, "PRJNA1169096", "age_at_collection_days", don, "clear", "P36_recipient_age_on_donor_unit", "FMT donor-unit records carry the recipient's age and sex")
    add(P1, "PRJNA1169096", "sex", don, "clear", "P36_recipient_age_on_donor_unit", "FMT donor-unit records carry the recipient's age and sex")
    add(P1, "PRJEB50408", "age_at_collection_days", "SAMEA12565697", "clear", "P36_age_placeholder", "age 0 on an adult (birth year 1975)")
    t1 = {"P1": 20, "P2": 59, "P3": 16, "P4": 55, "P5": 57, "P6": 37, "P7": 34}
    sb = cur("PRJEB27661", "subject_id")
    set_each(P1, "PRJEB27661", "age_at_collection_days", {r.sample_key: round(t1[r.value_normalized] * YEAR, 1) for r in sb.itertuples() if r.value_normalized in t1},
             "P36_age_table_rows", "ages re-read from Table 1 of PMID 31727954 by patient code")
    bm = [k for k in cur("PRJEB74322", "age_at_collection_days").sample_key if k in A.index and str(A.at[k, "alias"]).endswith("BM")]
    add(P1, "PRJEB74322", "age_at_collection_days", bm, "clear", "P36_days_postpartum", "on breast-milk samples the captured 'age' is days postpartum, not the mother's age")
    ag = cur("PRJEB83236", "age_at_collection_days"); sb = cur("PRJEB83236", "subject_id").set_index("sample_key").value_normalized
    # mothers (subject id ending in 0): any age below 18 years is the infant's age in days read onto the mother's record
    moth = ag[ag.sample_key.map(sb).astype(str).str.endswith("0") & (pd.to_numeric(ag.value_normalized, errors="coerce") < 18 * YEAR)].sample_key
    add(P1, "PRJEB83236", "age_at_collection_days", list(moth), "clear", "P36_infant_age_on_mother", "mothers' samples carry the infant's age in days (host_age_infant)")
    sb = cur("PRJNA419524", "subject_id")
    set_each(P1, "PRJNA419524", "subject_id", {r.sample_key: f"{r.value_normalized}|donor" for r in sb.itertuples()
                                               if r.sample_key in A.index and str(A.at[r.sample_key, "health_state"]) == "Organ donor"},
             "P36_ungroup_donor", "host subject id names a transplant pair; the organ donor is a different person")
    fix = {k: A.at[k, "subjectid"] for k in ["SAMEA7731553", "SAMEA7731550", "SAMEA7731429"] if k in A.index and "subjectid" in A.columns}
    set_each(P1, "PRJEB42155", "subject_id", fix, "P36_subject_attribute", "subjectid (= subject_id) is the person key; patient_id / host_subject_id are wrong on these samples")
    add(P1, "PRJEB101630", "age_at_collection_days", "SAMEA120472561", "clear", "P36_age_vs_title", "age 3.0 y on a sample titled '(adult)'")
    sb = cur("PRJEB59450", "subject_id")
    code = {}
    for r in sb.itertuples():
        txt = " ".join(str(A.at[r.sample_key, c]) for c in ("tube_id", "TITLE", "alias") if r.sample_key in A.index and c in A.columns)
        m = re.search(r"PDA[A-Z]*[0-9]+", txt)
        if m:
            code[r.sample_key] = m.group(0)
    set_each(P1, "PRJEB59450", "subject_id", code, "P36_regroup_patient_code", "host_subject_id is a running number reused by different patients; person = PDA patient code")
    sb = cur("PRJEB22863", "subject_id")
    set_each(P1, "PRJEB22863", "subject_id", {r.sample_key: f"{str(A.at[r.sample_key, 'alias']).split('_')[0]}|{r.value_normalized}" for r in sb.itertuples() if r.sample_key in A.index},
             "P36_regroup_cohort", "Patient ID is unique only within the NSCLC or RCC cohort: person = cohort + Patient ID")
    sb = cur("PRJNA1300398", "subject_id")
    set_each(P1, "PRJNA1300398", "subject_id", {r.sample_key: A.at[r.sample_key, "alias"] for r in sb.itertuples() if r.sample_key in A.index},
             "P36_regroup_alias", "HF-n and CF-n are different people: person = full sample alias")
    add(P1, "PRJEB39813", "age_at_collection_days", ["SAMEA7232963", "SAMEA7232987"], "clear", "P36_age_contradictory", "one subject's gut and oral samples give 50 and 30 years")
    add(P1, "PRJEB1220", "age_at_collection_days", ["SAMEA2042575", "SAMEA2042994", "SAMEA2042995"], "clear", "P36_age_contradictory", "V1 and O2 rows of patient UC38 disagree on age")
    add(P1, "PRJEB29373", "age_at_collection_days", ["SAMEA5054141", "SAMEA5054193"], "clear", "P36_age_table_rows", "ages joined to the wrong supplementary rows")
    # ---------------- country conflicts
    d = cur("PRJEB37731", "country")
    add(CC, "PRJEB37731", "country", list(d[d.value_normalized == "SE"].sample_key), "set", "P36_country_travel_attr", "the travel attribute was read instead of the sampling country", "DK")
    d = cur("PRJNA1430386", "country")
    add(CC, "PRJNA1430386", "country", list(d[d.value_normalized == "US"].sample_key), "set", "P36_country_submitter_city", "three records carry the submitting institution's city", "CA")
    add(CC, "PRJNA589866", "country", ["SAMN13292933", "SAMN13292934"], "set", "P36_country_site_code", "trial site 303 is in Serbia", "RS")
    d = cur("PRJNA624223", "country")
    add(CC, "PRJNA624223", "country", list(d[d.value_normalized == "CN"].sample_key), "set", "P36_country_single_site", "single-site Hong Kong cohort labelled two ways", "HK")
    d = cur("PRJNA644725", "country"); sb = set(cur("PRJNA644725", "subject_id").sample_key)
    add(CC, "PRJNA644725", "country", list(d[(d.value_normalized == "US") & d.sample_key.isin(sb)].sample_key), "set", "P36_country_supp_table",
        "every sample with an infant number from Supplementary Table S5 is Bangladeshi", "BD")
    add(CC, "PRJNA1157371", "subject_id", "all", "clear", "P36_ungroup_row_reference", "participant_ID is a leaked spreadsheet row reference ('=A100'), reused at every site")
    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False, quoting=csv.QUOTE_MINIMAL)
    pd.DataFrame(skipped, columns=["study_accession", "field", "rule_id", "why"]).to_csv(OUT.replace(".csv", "_not_applied.csv"), index=False)
    # negative controls named by the PRJNA419524 reading
    ctl = [k for k in A.index if str(A.at[k, "TITLE"] if "TITLE" in A.columns else "") in ("Buffer", "PCR NTC", "Molecular Water", "Extraction Buffer")]
    st = pd.read_csv(os.path.join(ROOT, "config", "sample_types.csv"), dtype=str)
    new = pd.DataFrame({"sample_accession": ctl, "study_accession": "PRJNA419524", "sample_type": "negative_control", "action": "mark",
                        "evidence": [f"title '{A.at[k, 'TITLE']}' (R2026.36 reading)" for k in ctl]})
    new = new[~new.sample_accession.isin(set(st.sample_accession))]
    if len(new):
        pd.concat([st, new], ignore_index=True).to_csv(os.path.join(ROOT, "config", "sample_types.csv"), index=False)
    print(json.dumps({"rows": len(out), "by_rule": out.rule_id.value_counts().to_dict(), "not_applied": skipped, "controls_marked": len(new)}, default=str))


if __name__ == "__main__":
    main()
