#!/usr/bin/env python
"""apply_i2_audit.py — restore intervention codes that filter I2 removed but the study did administer (R2026.36).

Filter I2 (apply_catchup_filters.py, R2026.32) emptied the codes of every project whose design was not a controlled trial, which
also removed real administrations in single-arm before/after studies. An Opus reading (I2 audit, 141 projects: the 121
single_arm_before_after projects + a random 20 of the 106 others) gave a verdict per original code.

Input: JSON list of audit entries (study_accession, codes[{code, verdict, evidence_quote, evidence_source, confidence}],
missing_codes, design, intervention_detail, note) and the I2 input list (pre-I2 rows: population_condition).
Output: data/inputs/gut/interventions/gut_intervention_studies_shard_zzi2audit_r36.parquet — sorts after every other shard
(including the R2026.34 probiotic shard), so build_gut_scope.load_intervention_studies (keep='last') takes its rows; each row
starts from the project's current effective row, so codes added by earlier corrections are kept.

Rules (confidence >= MIN_CONF; 'unclear', 'observed_routine_care' and 'false_hit' never add anything):
  administered_by_study code      -> added to the project's codes (and removed from interventions_unreplicated)
  missing_codes (known codes)     -> added when the reading's design is a trial or single-arm design and at least one entry of
                                     the project was read with confidence >= MIN_CONF
  design                          -> the reading's design when any code is added and the current design is none / unknown,
                                     or when the reading names a controlled trial
  intervention_detail             -> the reading's detail; population_condition -> the pre-I2 value
Prints counts only; writes a CSV log beside the shard.
"""
import argparse
import glob
import json
import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IV = os.path.join(ROOT, "data", "inputs", "gut", "interventions")
OUT = os.path.join(IV, "gut_intervention_studies_shard_zzi2audit_r36.parquet")
MIN_CONF = 0.7
KNOWN = {"antibiotic", "drug_therapy", "surgery", "probiotic", "synbiotic", "live_biotherapeutic", "prebiotic_or_fibre",
         "dietary_supplement", "diet_intervention", "fmt", "phage_therapy", "vaccine", "bowel_preparation", "exercise",
         "other_intervention"}
TRIALS = {"randomized_controlled_trial", "non_randomized_controlled", "crossover"}
ADMIN_DESIGNS = TRIALS | {"single_arm_before_after"}


def codes(s):
    return [c for c in str(s or "").split(";") if c and c != "nan"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audit", help="JSON list of I2 audit entries")
    ap.add_argument("--i2-input", required=True, help="JSON list given to the audit (pre-I2 rows)")
    a = ap.parse_args()
    aud = json.load(open(a.audit))
    pre = {r["study_accession"]: r for r in json.load(open(a.i2_input))}
    files = sorted(f for f in glob.glob(os.path.join(IV, "gut_intervention_studies_shard_*.parquet")) if f != OUT)
    cur = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True).drop_duplicates("study_accession", keep="last").set_index("study_accession")
    cols = list(cur.columns)
    rows, log = [], []
    for e in aud:
        acc = e["study_accession"]
        ok = [c for c in e.get("codes") or [] if float(c.get("confidence") or 0) >= MIN_CONF]
        add = [c for c in ok if c.get("verdict") == "administered_by_study" and c.get("code") in KNOWN]
        design = e.get("design") or "unknown"
        miss = []
        if design in ADMIN_DESIGNS and ok:
            miss = [m for m in (x.strip() for x in str(e.get("missing_codes") or "").replace(",", ";").split(";")) if m in KNOWN]
        base = cur.loc[acc].to_dict() if acc in cur.index else {c: None for c in cols}
        have = codes(base.get("interventions"))
        new_codes = sorted(set([c["code"] for c in add] + miss) - set(have))
        trial_fix = design in TRIALS and base.get("intervention_design") not in TRIALS and bool(set(have) | set(new_codes))
        if not new_codes and not trial_fix:
            v = ";".join(sorted({c.get("verdict") for c in e.get("codes") or []}))
            log.append((acc, design, "no change", v))
            continue
        allc = sorted(set(have) | set(new_codes))
        d0 = base.get("intervention_design")
        if trial_fix or d0 in (None, "", "none", "unknown") or pd.isna(d0):
            base["intervention_design"] = design
        ev = add[0] if add else (ok[0] if ok else {})
        base.update(interventions=";".join(allc),
                    interventions_unreplicated=";".join(c for c in codes(base.get("interventions_unreplicated")) if c not in set(allc)),
                    intervention_detail=e.get("intervention_detail") or base.get("intervention_detail"),
                    population_condition=base.get("population_condition") or (pre.get(acc) or {}).get("population_condition"),
                    evidence_source=ev.get("evidence_source") or base.get("evidence_source"),
                    evidence_quote=ev.get("evidence_quote") or base.get("evidence_quote"),
                    confidence=max([float(c.get("confidence") or 0) for c in add] or [MIN_CONF]),
                    route=base.get("route") or "R3", determined_by="i2_audit_r2026_36:claude-opus-5",
                    note=((base.get("note") or "") + f"; {','.join(new_codes) or 'design'} restored by the R2026.36 I2 audit").lstrip("; "))
        base["study_accession"] = acc
        rows.append(base)
        log.append((acc, design, ("added " + ",".join(new_codes)) if new_codes else "design set", ""))
    out = pd.DataFrame(rows, columns=["study_accession"] + cols)
    for c in out.columns:
        if c == "confidence":
            out[c] = pd.to_numeric(out[c], errors="coerce")
        elif c == "abstract_available":
            out[c] = out[c].fillna(False).astype(bool)
        elif c == "n_abstracts":
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).astype("int64")
        else:
            out[c] = out[c].astype("string")
    out.to_parquet(OUT, index=False)
    lg = pd.DataFrame(log, columns=["study_accession", "design_read", "action", "verdicts"])
    lg.to_csv(OUT.replace(".parquet", "_log.csv"), index=False)
    print(json.dumps({"entries": len(aud), "rows_written": len(out), "actions": lg.action.str.split(" ").str[0].value_counts().to_dict(),
                      "codes_added": int(sum(len(r.split(",")) for r in lg.action if r.startswith("added")))}))


if __name__ == "__main__":
    main()
