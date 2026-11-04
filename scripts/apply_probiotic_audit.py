#!/usr/bin/env python
"""apply_probiotic_audit.py — fold an Opus reading of probiotic mentions into the study-level intervention table (R2026.34).

Input: a JSON list of verdicts (one per project; fields of the audit output schema: study_accession, verdict, agent, design,
evidence_quote, evidence_source, confidence). Output: data/inputs/gut/interventions/gut_intervention_studies_shard_zprobiotic_r34.parquet,
which sorts after every other shard, so build_gut_scope.load_intervention_studies (drop_duplicates keep='last') takes its rows.

Rules (confidence >= MIN_CONF only; 'unclear' never changes anything):
  administered_by_study           -> add the agent code (probiotic / synbiotic / live_biotherapeutic) to the project's codes;
                                     a design of none / unknown becomes the design the reading names
  participant_own_use_recorded,
  exclusion_criterion_only,
  taxon_or_product_only, unrelated -> remove probiotic / synbiotic / live_biotherapeutic from the project's codes
Every other column of the project's current row is kept. Prints counts only; writes a CSV log beside the shard.
"""
import argparse
import glob
import json
import os
import re

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IV = os.path.join(ROOT, "data", "inputs", "gut", "interventions")
OUT = os.path.join(IV, "gut_intervention_studies_shard_zprobiotic_r34.parquet")
AGENTS = {"probiotic", "synbiotic", "live_biotherapeutic"}
MIN_CONF = 0.7
NEG = {"participant_own_use_recorded", "exclusion_criterion_only", "taxon_or_product_only", "unrelated"}


def design_code(text):
    t = (text or "").lower()
    if "crossover" in t or "cross-over" in t:
        return "crossover"
    if re.search(r"random", t) and not re.search(r"non-?random", t):
        return "randomized_controlled_trial"
    if re.search(r"non-?random|controlled", t):
        return "non_randomized_controlled"
    if re.search(r"single[- ]arm|open[- ]label|before[- /]after|pre[- /]post|uncontrolled", t):
        return "single_arm_before_after"
    if re.search(r"observational|cohort|routine|protocol|case", t):
        return "observational_with_procedure"
    return "unknown"


def codes(s):
    return [c for c in str(s or "").split(";") if c and c != "nan"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("verdicts", help="JSON list of audit verdicts")
    a = ap.parse_args()
    ver = pd.DataFrame(json.load(open(a.verdicts)))
    files = sorted(f for f in glob.glob(os.path.join(IV, "gut_intervention_studies_shard_*.parquet")) if f != OUT)
    cur = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True).drop_duplicates("study_accession", keep="last").set_index("study_accession")
    cols = list(cur.columns)
    rows, log = [], []
    for v in ver.itertuples(index=False):
        conf = float(v.confidence or 0)
        if v.verdict == "unclear" or conf < MIN_CONF:
            log.append((v.study_accession, v.verdict, "no change (unclear or low confidence)"))
            continue
        base = cur.loc[v.study_accession].to_dict() if v.study_accession in cur.index else {c: None for c in cols}
        have = codes(base.get("interventions"))
        if v.verdict == "administered_by_study" and v.agent in AGENTS:
            if v.agent in have:
                log.append((v.study_accession, v.verdict, "already coded"))
                continue
            new = sorted(set(have) | {v.agent})
            d0 = base.get("intervention_design")
            design = d0 if d0 not in (None, "", "none", "unknown") and not pd.isna(d0) else design_code(getattr(v, "design", ""))
            base.update(interventions=";".join(new), intervention_design=design,
                        interventions_unreplicated=";".join(c for c in codes(base.get("interventions_unreplicated")) if c != v.agent),
                        evidence_source=v.evidence_source, evidence_quote=v.evidence_quote, confidence=conf, route=base.get("route") or "R3",
                        determined_by="probiotic_audit_r2026_34:claude-opus-5", note=((base.get("note") or "") + "; probiotic added by the R2026.34 audit").lstrip("; "))
            log.append((v.study_accession, v.verdict, f"added {v.agent}"))
        elif v.verdict in NEG:
            drop = [c for c in have if c in AGENTS]
            if not drop:
                log.append((v.study_accession, v.verdict, "nothing to remove"))
                continue
            new = [c for c in have if c not in AGENTS]
            base.update(interventions=";".join(new), determined_by="probiotic_audit_r2026_34:claude-opus-5",
                        note=((base.get("note") or "") + f"; {','.join(drop)} removed by the R2026.34 audit ({v.verdict})").lstrip("; "))
            if not new:
                base.update(intervention_detail="", evidence_source=None, evidence_quote=None)
            log.append((v.study_accession, v.verdict, f"removed {','.join(drop)}"))
        else:
            log.append((v.study_accession, v.verdict, "no change"))
            continue
        base["study_accession"] = v.study_accession
        rows.append(base)
    out = pd.DataFrame(rows, columns=["study_accession"] + cols)
    for c in out.columns:
        if c in ("confidence",):
            out[c] = pd.to_numeric(out[c], errors="coerce")
        elif c == "abstract_available":
            out[c] = out[c].fillna(False).astype(bool)
        elif c == "n_abstracts":
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).astype("int64")
        else:
            out[c] = out[c].astype("string")
    out.to_parquet(OUT, index=False)
    lg = pd.DataFrame(log, columns=["study_accession", "verdict", "action"])
    lg.to_csv(OUT.replace(".parquet", "_log.csv"), index=False)
    print(json.dumps({"verdicts": len(ver), "rows_written": len(out), "actions": lg.action.str.split(" ").str[0].value_counts().to_dict()}))


if __name__ == "__main__":
    main()
