#!/usr/bin/env python
"""apply_catchup_filters.py — deterministic filters on the R2026.32 catch-up outputs, from the full-run audit
(25-project subject sample, 30-project intervention sample). Idempotent; backups under data/inputs/gut/verify_r2026_32/backup.

S1  generated subject ids: dropped for a project whose stated number of participants is smaller than its number of
    specimens — then the samples cannot be one per person (audit: 84 % -> 95.5 % on the sample; 22 projects).
I2  interventions: study-level codes are kept only for trial designs (randomized_controlled_trial, non_randomized_controlled,
    crossover). Elsewhere the reading mostly coded routine care the study only observed (antibiotics, drug therapy, surgery,
    bowel preparation) as administered (audit: 46.7 % -> 91.7 %). Dropped codes move to interventions_unreplicated and the
    design becomes 'unknown'.
D1  relatedness: setting-type calls (hospital_ward, community, workplace, fmt_donor_recipient, other) are cleared — the reading
    assigned them from a setting the paper mentions rather than from people sampled as related groups (audit: 9 of 10 wrong).
    Kinship calls are checked by scripts/verify_catchup_rows.py.
Prints counts only.
"""
import glob
import json
import os
import shutil

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUT = os.path.join(ROOT, "data", "inputs", "gut")
BK = os.path.join(GUT, "verify_r2026_32", "backup")
TRIALS = {"randomized_controlled_trial", "non_randomized_controlled", "crossover"}
NON_KIN = {"hospital_ward", "community", "other", "workplace", "fmt_donor_recipient"}


def backup(f):
    os.makedirs(BK, exist_ok=True)
    if not os.path.exists(os.path.join(BK, os.path.basename(f))):
        shutil.copy(f, BK)


def main():
    stats = {}
    logs = pd.concat([pd.read_parquet(f) for f in glob.glob(os.path.join(GUT, "design", "logs", "subject_log_shard_C*.parquet"))], ignore_index=True)
    sp = pd.to_numeric(logs.stated_participants, errors="coerce")
    bad = set(logs.study_accession[sp.notna() & (pd.to_numeric(logs.n_samples, errors="coerce") > sp) & (logs.generated_n.fillna(0) > 0)])
    stats["S1_projects"] = len(bad)
    n_rm = 0
    for f in glob.glob(os.path.join(GUT, "deep", "deep_determinations_shard_SUBJC*.parquet")):
        d = pd.read_parquet(f)
        if not len(d):
            continue
        backup(f)
        m = d.study_accession.isin(bad)
        n_rm += int(m.sum())
        d[~m].to_parquet(f, index=False)
    stats["S1_rows_removed"] = n_rm
    for f in glob.glob(os.path.join(GUT, "design", "logs", "subject_log_shard_C*.parquet")):
        d = pd.read_parquet(f)
        m = d.study_accession.isin(bad) & (d.generated_n.fillna(0) > 0)
        if m.any():
            backup(f)
            d.loc[m, "blocked_reason"] = "stated participants fewer than specimens (audit filter S1)"
            d.loc[m, "generated_n"] = 0
            d.loc[m, "generation_basis"] = ""
            d.to_parquet(f, index=False)
    n_proj = n_codes = 0
    for f in glob.glob(os.path.join(GUT, "interventions", "gut_intervention_studies_shard_C*.parquet")):
        d = pd.read_parquet(f)
        m = (d.interventions.fillna("") != "") & ~d.intervention_design.isin(TRIALS)
        if not m.any():
            continue
        backup(f)
        n_proj += int(m.sum())
        n_codes += int(d.loc[m, "interventions"].str.split(";").str.len().sum())
        d.loc[m, "interventions_unreplicated"] = [";".join(sorted({c for c in (str(a) + ";" + str(b or "")).split(";") if c and c != "nan"}))
                                                  for a, b in zip(d.loc[m, "interventions"], d.loc[m, "interventions_unreplicated"])]
        d.loc[m, "interventions"] = ""
        d.loc[m, "intervention_design"] = "unknown"
        d.loc[m, "intervention_detail"] = ""
        d.loc[m, "population_condition"] = ""
        d.loc[m, "note"] = d.loc[m, "note"].fillna("") + "; codes not committed outside a trial design (audit filter I2)"
        d.to_parquet(f, index=False)
    stats["I2_projects_emptied"], stats["I2_codes_moved"] = n_proj, n_codes
    n_rel = 0
    for f in glob.glob(os.path.join(GUT, "design", "study_design_shard_C*.parquet")):
        d = pd.read_parquet(f)
        m = d.relatedness_design.isin(NON_KIN)
        if not m.any():
            continue
        backup(f)
        n_rel += int(m.sum())
        d.loc[m, "relatedness_design"] = "none"
        d.loc[m, "relatedness_summary"] = ""
        d.loc[m, "n_families"] = None
        d.loc[m, "notes"] = d.loc[m, "notes"].fillna("") + "; setting-type relatedness not committed (audit filter D1)"
        d["screen"] = ["both" if l == "yes" and r != "none" else ("longitudinal" if l == "yes" else ("related" if r != "none" else "none"))
                       for l, r in zip(d.longitudinal_design, d.relatedness_design)]
        d.to_parquet(f, index=False)
    stats["D1_relatedness_cleared"] = n_rel
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
