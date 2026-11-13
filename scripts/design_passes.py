#!/usr/bin/env python
"""design_passes.py — the study-design reading (R2026.33, owner request "understand the overall design of each study").

For each project: ONE reading of the best text available (the generating paper's title / abstract / data availability /
methods, else up to 3 abstracts, else the archive record; same text builder as scripts/catchup_passes.py, paper text cut at
16,000 characters), read twice independently (model role `rubric`), plus the project's CANDIDATE GROUP VARIABLES: every
sample attribute (and sample-name stem) with 2-40 distinct values over the project's catalog specimens, with all values.
Each reading returns
    study_shape          design type (pilot_passes.SHAPES)
    design_statement     one factual sentence: who, groups / arms, sampling scheme
    groups               the compared groups as the text names them (kind, health_condition code, intervention codes, n)
    sample_group_key     the candidate variable that tells which group each sample is in, and a value -> group map
    n_participants_stated

Merging (deterministic):
  study_shape      both readings agree -> the shape; else a tie-break by the `adjudicate` role (Opus 5; R2026.34) picks one
                   reading's shape (shape_tiebreak first / second) or neither -> 'suggested:a|b' ("two readings differed")
  design_statement reading 1's sentence when the shapes agree; the chosen reading's sentence after a tie-break
  readings         a reading missing a required field (or with an empty / placeholder statement) is re-asked once
  sample groups    both readings chose the same candidate key; a value is agreed when both map it to the same group (same
                   kind class and intervention codes, and the same unique health_condition code, or shared label words, or
                   the only group of its kind). Technical-control values / labels are dropped. The proposal stands when the
                   agreed values cover >= 70 % of the specimens with >= 2 groups, no group is a body site, time-point-only
                   keys occur only in single-arm / crossover interventions, place-only keys only when the readings agree on
                   cross_sectional_groups and a group quote is verbatim (G1), and no group holds more people (or, without
                   person ids and repeated sampling, specimens) than 1.25x (1.5x) the number both readings state for it.
  Opus check       every proposed value -> group map is checked by the `adjudicate` role (Opus 5) with the same text; only
                   values it confirms are written, and a health_condition code only when it confirms the code. Each kept
                   specimen gets
                     comparison_group  the group label (reading 1's wording)                       route R1, conf 0.8
                     health_condition  the group's code, when both readings gave the same one        route R1, conf 0.8
                     intervention      the arm's / period's intervention codes                       route R1, conf 0.8
                   evidence_source sample.attr.<key> (sample.name_rule:<key> for name stems, sample.attr_stem:<key> for
                   free-text stems), quote "<key> = <value>". A specimen that already has a sample-level value for
                   health_condition / intervention keeps it (the new row is not written; disagreements are counted).
Outputs under --out (data/inputs/gut), shard k:
  design/design_reading_shard_D{k}.parquet       one row per project (shape per reading, agreed shape, statement, groups)
  design/design_sample_groups_shard_D{k}.parquet one row per assigned specimen (key, value, group, codes)
  deep/deep_determinations_shard_GRPD{k}.parquet per-sample rows (comparison_group, health_condition, intervention)
  design/logs/design_summary_D{k}.json
Prints counts only (no project content).

usage (inside a kernel that has `host`, via runpy with init_globals={"host": host}):
  python scripts/design_passes.py --projects LIST.csv --shard K --nshards N --budget USD [--limit N] [--out data/inputs/gut]
  python scripts/design_passes.py --merge [--out data/inputs/gut]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)
import catchup_passes as CP  # noqa: E402  (project_texts, load_dossier, norm, quote_ok, codes, SOURCES)
from catalog import models as M  # noqa: E402
from catalog.llm_backend import LLM, BudgetExceeded  # noqa: E402

M.set_host(globals().get("host"))
PP = CP.PP
_orig_sections = PP.paper_sections
PP.paper_sections = lambda pmcid, max_chars=16000: _orig_sections(pmcid, max_chars=min(max_chars, 16000))   # design info is in methods
PKG = os.environ.get("CATALOG_PKG") or os.path.join(ROOT, "build", "package")   # R2026.34: CATALOG_PKG = an intermediate build
ATTR = os.path.join(ROOT, "data", "inputs", "registry", "registry_biosample_attributes.parquet")
REG_RUNS = os.path.join(ROOT, "data", "inputs", "registry", "registry_runs.parquet")
RUN_FIELDS = ["library_name", "sample_title", "host_phenotype", "host_status", "disease", "dev_stage", "experimental_factor", "isolation_source"]
RUN_FIELDS_ON = False   # set by --run-fields
PASS = "catalog_design_v1"
NOTHINK = {"type": "disabled"}
MODEL_TAG = "rubric"
SHAPES = list(PP.SHAPES)
KINDS = ["condition", "healthy_control", "intervention_arm", "exposure_or_lifestyle", "geography", "age_or_life_stage", "time_point", "body_site", "other"]
HC = {k: v for k, v in CP.HC.items()}
IV = {k: v for k, v in CP.IV.items()}
DET_COLS = CP.DET_COLS
ADMIN = re.compile(r"^(ENA-|INSDC|BioSampleModel|NCBI submission|SAMPLE_NAME\.|center[ _]name|organism$|Package$|Model$|DESCRIPTION$|lat_lon$|"
                   r"collection[ _]date$|geo_loc_name$|host$|env_|.*accession|broker|external[ _]id|submitter|sra|title$|alias$|"
                   r"sample[ _]name$|description$|.*date$|.*time$|latitude|longitude|depth$|elevation|altitude)", re.I)
NAME_KEYS = {"alias", "TITLE", "title", "sample_title", "Sample Name", "sample_name", "SAMPLE_NAME", "library_name"}
REPEATED = {"cohort_longitudinal", "rct", "crossover", "single_arm_intervention", "non_randomised_intervention"}
NS = {"type": ["string", "null"]}
NI = {"type": ["integer", "null"]}

SYSTEM = f"""You read the text of one sequencing project of human-associated microbial communities (the archive record and,
when available, the paper that generated the data or its abstract) and describe its STUDY DESIGN for a public catalog.
Record, with the `record` tool:

1. study_shape: how participants were selected and sampled. Take the FIRST that applies:
   1 method_or_benchmark: the aim is a method, protocol, sequencing or diagnostic performance (kit comparisons, mock
     communities, clinical mNGS diagnostic yield: 'compared mNGS with culture', 'detection rate', 'diagnostic performance',
     'sensitivity'), not a biological comparison of people; a patient-group contrast inside such a study does not override it;
   2 rct / crossover / single_arm_intervention / non_randomised_intervention: the study assigns an intervention (randomised
     parallel arms / randomised sequence / everyone receives it, before vs after / non-random intervention vs control groups);
   3 cohort_longitudinal: the same people sampled repeatedly over time, no assigned intervention;
   4 case_control: people with a condition compared with people without it OR with a different condition. This holds even
     when the source calls itself 'cross-sectional': if one compared group is disease-free or has a different disease, the
     answer is case_control (e.g. healthy controls vs three stages of cirrhosis = case_control);
   5 cross_sectional_groups: other groups compared at one time (lifestyle, diet, geography, age, exposure; also strata of ONE
     disease such as severity or exacerbation vs stable);
   6 case_series: people with a condition only, no comparison group. A comparison between tissues, sites or specimen types
     taken from the same people (tumour vs adjacent normal, sputum vs stool) is never case_control: when every group comes
     from one condition cohort the shape is case_series (cohort_longitudinal when they are sampled repeatedly);
   7 population_survey: one population, no comparison groups;
   8 unclear: the text does not say. When the prose (title, description, abstract, methods) names no design and the only
     evidence is sample names or attribute values, answer unclear, unless those values themselves name the arms or periods
     of an intervention the prose states. You may still give the groups and the group key in that case.
   Give a verbatim quote (at most 25 words) that shows it, and its source section.
2. design_statement: ONE factual sentence (at most 40 words) a reader can rely on: who was sampled, the groups or arms
   compared, and the sampling scheme (how often, which body sites). Only facts stated in the PROSE of the text (title,
   description, abstract, methods), numbers as stated; a number of samples is never a number of participants, and when the
   text gives no participant count give none; name only groups and comparisons the prose names (not ones that appear only
   in sample names or attribute values); no results, no interpretation. Plain English.
3. groups: the groups the study compares, as the text names them (at most 8; empty for a single-group study). For each:
   label (short, the source's own name), kind, health_condition, intervention_codes, n_people_stated (people in THIS group,
   null if not stated) and a verbatim quote (at most 25 words) that defines the group.
   health_condition: a code from the list below; healthy_control ONLY when the text says the PEOPLE in the group are
   healthy, disease-free or asymptomatic (a control group of patients with other diseases gets its own code and kind
   'condition'; 'normal', 'adjacent normal' or 'non-tumour' TISSUE from patients describes the specimen, not a healthy
   person, and takes the patients' code); 'none' when the group is not defined by a health condition. Code choices: ME/CFS and fibromyalgia -> neurological_psychiatric;
   sarcoidosis -> autoimmune_inflammatory; periodontitis -> other_disease; respiratory and other non-gut infections ->
   other_infection (never gi_infection_or_diarrhoea); preterm_nicu, nec, sepsis_or_infection and ibd_or_gi_disease are for
   infant cohorts only.
   intervention_codes: for intervention arms (placebo / no_intervention for control arms), and for the before / after periods
   of a single-arm or crossover intervention (kind time_point; no_intervention for 'before'); empty otherwise.
4. sample_group_key and value_map: below the text you get CANDIDATE GROUP VARIABLES: sample attributes, stems of sample
   names (keys starting 'name:') and stems of free-text attributes (keys starting 'stem:'), with every distinct value and its
   sample count. If one of them tells which compared group each sample belongs to, give its key EXACTLY as listed and map
   EVERY listed value to one of your group labels, or to 'exclude'. Rules:
   - choose the key ONLY from the CANDIDATE GROUP VARIABLES list (exactly as written) or answer 'none'; never name any
     other attribute;
   - technical controls are always 'exclude': water / reagent / extraction blanks, negative and positive controls, spiked
     controls, mock or standard communities, bronchoscope prewash, reagent samples, library-preparation variants;
   - stool donors, donor pools and banked products of a transplant or FMT study are not a compared group: 'exclude';
   - a value maps to a group only when its meaning is clear from the value and the text (e.g. 'CD' = Crohn's disease when
     the text compares patients with Crohn's disease and healthy controls); a value that names a sampling site or sample type
     rather than a group of people is 'exclude';
   - a variable that separates body sites, sample types, time points or sequencing batches is NEVER the group key, except the
     before / after variable of a single-arm or crossover intervention;
   - if no listed variable separates the groups, give 'none' and an empty map.
5. n_participants_stated: the number of participants (people) the text states for the samples of this project, or null.

health_condition codes: {', '.join(f'{k} ({v})' for k, v in HC.items())}
intervention codes: {', '.join(f'{k} ({v})' for k, v in IV.items())}
"""

VERIFY_SYSTEM = """You check a proposed assignment of sequencing samples to the compared groups of one study. You get the project's
text (archive record and paper or abstract), one sample variable with every distinct value and its sample count, and the proposed
value -> group map (group label, kind, health_condition code, intervention codes). For EACH value answer:
  value_ok: yes = the text and the value make it clear that samples with this value belong to this group of people;
            no = wrong group, or the value names a sampling site / sample type / technical control / stool donor rather than
            a compared group; unsure = cannot be told from the text. Judge ONLY the assignment here: when the group is right
            but its health_condition code is wrong, answer value_ok yes and code_ok no.
            A time-point value (before / after, baseline / endline) IS a valid group when the study is a single-arm or
            crossover intervention (study design given below) and the group is that intervention's before or after period.
  code_ok: yes / no / not_applicable - is the group's health_condition code right for that group (healthy_control only for
            healthy, disease-free or asymptomatic people, not for normal tissue taken from patients; infant-only codes
            preterm_nicu, nec, sepsis_or_infection, ibd_or_gi_disease only for infant cohorts)? not_applicable when the code
            is 'none'.
Answer every numbered value, giving its number as `index`. Judge only from the text and values given; unsure when in doubt."""

VERIFY_TOOL = PP.tool("verify", {"items": {"type": "array", "items": {"type": "object", "properties": {
    "index": {"type": "integer"}, "value": {"type": "string"}, "value_ok": {"type": "string", "enum": ["yes", "no", "unsure"]},
    "code_ok": {"type": "string", "enum": ["yes", "no", "not_applicable"]}, "reason": {"type": "string"}},
    "required": ["value", "value_ok", "code_ok"]}}}, ["items"])

TIE_SYSTEM = """Two independent readings of one sequencing project's text disagreed on its STUDY DESIGN type. You get the text
(archive record and paper or abstract) and the two answers, each with its supporting quote. Decide which answer the text
supports, using the same rules the readers had (take the FIRST that applies):
  1 method_or_benchmark (aim is a method / protocol / kit / diagnostic performance; a patient-group contrast inside it does
    not override); 2 rct / crossover / single_arm_intervention / non_randomised_intervention (an assigned intervention);
  3 cohort_longitudinal (same people sampled repeatedly, no assigned intervention); 4 case_control (people with a condition
    vs people without it or with a different condition); 5 cross_sectional_groups (other groups at one time, or strata of
    one disease); 6 case_series (one condition, no comparison group; tissue / site contrasts within the same patients);
  7 population_survey (one population, no groups); 8 unclear (the prose names no design).
Answer `choice` = 'first', 'second' or 'neither' (neither when the text supports neither answer or does not say), with a
verbatim quote (at most 25 words) from the text."""
TIE_TOOL = PP.tool("tiebreak", {"choice": {"type": "string", "enum": ["first", "second", "neither"]}, "quote": NS, "reason": NS}, ["choice"])

HARD_ADMIN = re.compile(r"^(ENA-|INSDC|BioSampleModel|NCBI submission|SAMPLE_NAME\.|center[ _]name$|organism$|Package$|Model$|"
                        r"lat_lon$|collection[ _]date$|geo_loc_name$|host$|.*accession|broker|submitter|.*date$|.*time$|latitude|longitude|"
                        r"depth$|elevation|altitude|env_broad_scale|env_local_scale|env_medium)", re.I)
CONTROL_LABEL = re.compile(r"blank|negative[ _-]?control|\bntc\b|no[ -]template|\bwater\b|reagent|kveim|prewash|positive[ _-]?control|"
                           r"spike|\bmock\b|zymo|extraction[ _-]?control|kit[ _-]?control|\bbuffer\b|\bpbs\b|standard community", re.I)
CONTROL_VALUE = re.compile(r"^\s*(blank|water|ntc|mock|zymo|negative control|positive control|neg[ _-]?ctrl|pos[ _-]?ctrl|kit control|"
                           r"extraction (blank|control)|reagent( control)?|buffer|pbs)\s*$", re.I)
STOP = {"group", "groups", "patients", "patient", "subjects", "subject", "participants", "people", "the", "with", "and", "of", "in", "arm", "cohort", "individuals"}


TOOL = PP.tool("record", {
    "study_shape": {"type": "string", "enum": SHAPES},
    "shape_quote": NS,
    "shape_source": {"type": ["string", "null"], "enum": CP.SOURCES + [None]},
    "design_statement": {"type": "string"},
    "groups": {"type": "array", "items": {"type": "object", "properties": {
        "label": {"type": "string"}, "kind": {"type": "string", "enum": KINDS},
        "health_condition": {"type": "string", "enum": list(HC) + ["none"]},
        "intervention_codes": {"type": "array", "items": {"type": "string", "enum": list(IV)}},
        "n_people_stated": NI, "quote": NS}, "required": ["label", "kind", "health_condition"]}},
    "sample_group_key": {"type": "string"},
    "value_map": {"type": "array", "items": {"type": "object", "properties": {
        "value": {"type": "string"}, "group_label": {"type": "string"}}, "required": ["value", "group_label"]}},
    "n_participants_stated": NI,
}, ["study_shape", "design_statement", "groups", "sample_group_key", "value_map"])


def _stem_alpha(v):
    m = re.match(r"\s*([A-Za-z]+)", str(v))
    return m.group(1) if m else None


def _stem_nodigits(v):
    s = re.sub(r"\b(SAM[NED][A-Z]?\d+|ERS\d+|SRS\d+|DRS\d+)\b", "", str(v))
    s = re.sub(r"\d+", "", s)
    s = re.sub(r"[\s._\-]+", "_", s).strip("_")
    return s or None


def _stem_paren(v):
    m = re.findall(r"[(\[]([^()\[\]]{2,40})[)\]]", str(v))
    return m[-1].strip() if m else None


def _ok_series(t, n, max_distinct, min_cov):
    return 2 <= t.nunique() <= max_distinct and len(t) >= min_cov * n and t.nunique() < len(t)


def candidate_variables(attrs, specimens):
    """attrs: attribute rows (sample_acc, attr_key, attr_value) of the project's specimens.
    -> (lines for the prompt, {key: Series sample_key -> value}). Keys: attributes with 2-40 distinct values over >= 50 % of
    the specimens; 'name:<key>:<kind>' stems of sample names and 'stem:<key>:<kind>' stems of other free-text attributes
    (parenthetical tail, digits removed, leading letters) with 2-20 values over >= 80 % of the specimens."""
    n = len(specimens)
    out, lines = {}, []
    if not n or not len(attrs):
        return lines, out
    a = attrs[attrs.sample_acc.isin(specimens)].drop_duplicates(["sample_acc", "attr_key"])
    for key, g in a.groupby("attr_key"):
        if HARD_ADMIN.match(str(key)):
            continue
        s = g.set_index("sample_acc").attr_value.astype(str).str.strip()
        s = s[(s != "") & ~s.str.lower().isin(["nan", "none", "missing", "not applicable", "not collected", "na", "n/a"])]
        if not len(s):
            continue
        if key not in NAME_KEYS and _ok_series(s, n, 40, 0.5):
            out[str(key)] = s
            continue
        if s.nunique() <= 40 and s.nunique() < len(s) and key not in NAME_KEYS:
            continue
        pre = "name" if key in NAME_KEYS else "stem"
        for kind, fn in (("paren", _stem_paren), ("alpha", _stem_alpha), ("nodigits", _stem_nodigits)):
            t2 = s.map(fn).dropna()
            if _ok_series(t2, n, 20, 0.8):
                out[f"{pre}:{key}:{kind}"] = t2
    seen, uniq = set(), {}
    RK = {"paren": 0, "alpha": 1, "nodigits": 2}
    for k in sorted(out, key=lambda k: (out[k].nunique(), -len(out[k]), k.count(":") > 0, RK.get(k.rsplit(":", 1)[-1], 0), k)):   # drop derived keys identical to an earlier one
        sig = tuple(sorted(out[k].groupby(out[k]).apply(lambda x: tuple(sorted(x.index))).tolist()))
        if sig in seen:
            continue
        seen.add(sig)
        uniq[k] = out[k]
    keys = list(uniq)[:25]
    out = {k: uniq[k] for k in keys}
    for k in keys:
        vc = out[k].value_counts()
        lines.append(f"  {k} [{len(out[k])} of {n} samples]: " + "; ".join(f"{str(v)[:80]} x{c}" for v, c in vc.items()))
    return lines, out


def valid_reading(r, cand=None):
    if not isinstance(r, dict):
        return False
    st = r.get("study_shape")
    ds = str(r.get("design_statement") or "").strip()
    key = r.get("sample_group_key")
    return (st in SHAPES and isinstance(r.get("groups"), list) and isinstance(key, str)
            and len(ds) >= 15 and "placeholder" not in ds.lower() and (cand is None or key == "none" or key in cand))


def _tokens(label):
    return {w for w in re.findall(r"[a-z0-9]+", str(label).lower()) if w not in STOP}


def _same_group(ga, gb, ctx):
    """Two readings' groups are the same when kind class and intervention codes match and either the health_condition code
    is the same and unique within each reading, or the labels share words, or the group is the only one of its kind class /
    codes in both readings. ctx = (canon counts A, canon counts B, code counts A, code counts B)."""
    ca, cb, ha, hb = ctx
    kc = lambda g: "condition_or_control" if g.get("kind") in ("condition", "healthy_control") else g.get("kind")
    canon = lambda g: (kc(g), () if g.get("kind") == "time_point" else tuple(sorted(g.get("intervention_codes") or [])))
    if canon(ga) != canon(gb):
        return False
    hca, hcb = ga.get("health_condition") or "none", gb.get("health_condition") or "none"
    if hca == hcb and hca != "none" and ha.get(hca, 0) == 1 and hb.get(hcb, 0) == 1:
        return True
    ta, tb = _tokens(ga.get("label")), _tokens(gb.get("label"))
    if ta and tb and (len(ta & tb) / len(ta | tb) >= 0.34 or ta <= tb or tb <= ta):   # containment: 'Y' vs 'Y (year follow-up)'
        return True
    return ca.get(canon(ga), 0) == 1 and cb.get(canon(gb), 0) == 1


def merge_project(acc, tx, reps, cand, specimens, existing, subj=None):
    """-> study row, proposal dict (key, {value: group}, hc agreed per value) or None"""
    r1, r2 = reps[0] or {}, reps[1] or {}
    s1, s2 = r1.get("study_shape"), r2.get("study_shape")
    shape = s1 if s1 == s2 else f"suggested:{s1}|{s2}"
    segs = tx["segs"]
    qok = any(CP.find_quote(r.get("shape_quote") or "", segs, r.get("shape_source")) for r in (r1, r2) if r.get("shape_quote"))
    gq = [bool(CP.find_quote(g.get("quote") or "", segs)) for g in (r1.get("groups") or []) if isinstance(g, dict) and g.get("quote")]
    def _int(x):
        try:
            return int(float(x)) if x is not None and str(x).strip() != "" else None
        except (TypeError, ValueError):
            return None
    np1, np2 = _int(r1.get("n_participants_stated")), _int(r2.get("n_participants_stated"))
    row = dict(study_accession=acc, text_mode=tx["mode"], pmcid=tx.get("pmcid"), shape_r1=s1, shape_r2=s2, study_shape=shape,
               shape_quote_verbatim=bool(qok), group_quotes_verbatim=int(sum(gq)), group_quotes=len(gq),
               design_statement=(r1.get("design_statement") if s1 == s2 else None),
               design_statement_r2=r2.get("design_statement"), groups_json=json.dumps(r1.get("groups") or [], ensure_ascii=False)[:4000],
               n_groups_r1=len(r1.get("groups") or []), n_groups_r2=len(r2.get("groups") or []),
               n_participants_stated=(np1 if np1 is not None and np1 == np2 else None),
               key_r1=r1.get("sample_group_key"), key_r2=r2.get("sample_group_key"), group_key=None, group_coverage=0.0,
               n_specimens=len(specimens), n_assigned=0, n_groups_assigned=0, n_candidate_keys=len(cand), status="done")
    k1, k2 = r1.get("sample_group_key"), r2.get("sample_group_key")
    if not k1 or k1 != k2 or k1 == "none" or k1 not in cand:
        row["status"] = "done_no_key" if (k1 in (None, "none") and k2 in (None, "none")) else "done_key_disagree_or_invalid"
        return row, None
    g1 = {g.get("label"): g for g in (r1.get("groups") or []) if isinstance(g, dict) and g.get("label")}
    g2 = {g.get("label"): g for g in (r2.get("groups") or []) if isinstance(g, dict) and g.get("label")}
    canon = lambda g: ("condition_or_control" if g.get("kind") in ("condition", "healthy_control") else g.get("kind"),
                       () if g.get("kind") == "time_point" else tuple(sorted(g.get("intervention_codes") or [])))
    ca = pd.Series([canon(g) for g in g1.values()]).value_counts().to_dict() if g1 else {}
    cb = pd.Series([canon(g) for g in g2.values()]).value_counts().to_dict() if g2 else {}
    ha = pd.Series([g.get("health_condition") or "none" for g in g1.values()]).value_counts().to_dict() if g1 else {}
    hb = pd.Series([g.get("health_condition") or "none" for g in g2.values()]).value_counts().to_dict() if g2 else {}
    ctx = (ca, cb, ha, hb)
    m1 = {str(x.get("value")).strip(): x.get("group_label") for x in (r1.get("value_map") or []) if isinstance(x, dict)}
    m2 = {str(x.get("value")).strip(): x.get("group_label") for x in (r2.get("value_map") or []) if isinstance(x, dict)}
    ser = cand[k1]
    agreed, hc_ok, iv_ok = {}, {}, {}
    for v in ser.unique():
        a, b = m1.get(v), m2.get(v)
        if not a or not b or a == "exclude" or b == "exclude" or a not in g1 or b not in g2 or CONTROL_VALUE.match(str(v)):
            continue
        ga, gb = g1[a], g2[b]
        if CONTROL_LABEL.search(str(a)) or CONTROL_LABEL.search(str(b)):
            continue
        if _same_group(ga, gb, ctx):
            agreed[v] = ga
            hca, hcb = ga.get("health_condition") or "none", gb.get("health_condition") or "none"
            hc_ok[v] = hca if (hca == hcb and hca != "none") else None
            ia, ib = sorted(ga.get("intervention_codes") or []), sorted(gb.get("intervention_codes") or [])
            iv_ok[v] = ";".join(ia) if (ia and ia == ib and ga.get("kind") in ("intervention_arm", "time_point")) else None
    assigned = ser[ser.isin(list(agreed))]
    cov = len(assigned) / max(1, len(specimens))
    kinds = {agreed[v].get("kind") for v in assigned.unique()}
    ngr = len({agreed[v].get("label") for v in assigned.unique()})
    row.update(group_key=k1, group_coverage=round(cov, 3), n_groups_assigned=ngr)
    if cov < 0.7 or ngr < 2:
        row["status"] = "done_mapping_below_threshold"
        return row, None
    if "body_site" in kinds or (kinds <= {"time_point", "other"} and s1 not in ("single_arm_intervention", "crossover")):
        row["status"] = "done_key_not_a_comparison"
        return row, None
    # G1 (production audit R2026.33): a place is a compared group only when the study compares places, i.e. the readings agree
    # on cross_sectional_groups and at least one of the assigned groups' defining quotes is verbatim in the text
    if kinds <= {"geography"}:
        labs = {agreed[v].get("label") for v in assigned.unique()}
        if not (shape == "cross_sectional_groups" and any(CP.find_quote(g1[l].get("quote") or "", segs) for l in labs if l in g1)):
            row["status"] = "done_geography_not_a_comparison"
            return row, None
    # group sizes: a group holding more people (or specimens) than both readings state for it loses its values
    dropped = []
    for lab in sorted({agreed[v].get("label") for v in assigned.unique()}):
        gA = g1.get(lab) or {}
        nst = gA.get("n_people_stated")
        twin = next((g for g in g2.values() if _same_group(gA, g, ctx)), {})
        if not isinstance(nst, int) or nst <= 0 or twin.get("n_people_stated") != nst:
            continue
        ks = [sk for sk, v in assigned.items() if agreed[v].get("label") == lab]
        if subj is not None and subj.reindex(ks).notna().mean() >= 0.8:
            over = subj.reindex(ks).dropna().nunique() > 1.25 * nst + 2
        else:
            over = s1 not in REPEATED and len(ks) > 1.5 * nst + 2
        if over:
            dropped.append(lab)
    if dropped:
        for v in [v for v, g in agreed.items() if g.get("label") in dropped]:
            agreed.pop(v)
        assigned = ser[ser.isin(list(agreed))]
        cov = len(assigned) / max(1, len(specimens))
        ngr = len({agreed[v].get("label") for v in assigned.unique()})
        row.update(group_coverage=round(cov, 3), n_groups_assigned=ngr, groups_dropped_size=len(dropped))
        if cov < 0.7 or ngr < 2:
            row["status"] = "done_group_size_mismatch"
            return row, None
    row["status"] = "proposed"
    return row, dict(key=k1, agreed=agreed, hc=hc_ok, iv=iv_ok, assigned=assigned, values=ser.value_counts().to_dict(), shape=shape)


def finalise(acc, tx, row, prop, verdicts, existing):
    """Apply the Opus check: keep values with value_ok == yes; health_condition only when code_ok == yes (or n/a)."""
    k1, agreed, hc_ok, iv_ok, assigned = prop["key"], prop["agreed"], prop["hc"], prop["iv"], prop["assigned"]
    order = list(agreed)
    nv = lambda s: re.sub(r"\s+x\d+\s*$", "", str(s).split("->")[0]).strip().strip("'\"").strip().lower()
    V = {}
    for x in (verdicts or []):
        if not isinstance(x, dict):
            continue
        ix = x.get("index")
        if isinstance(ix, int) and 1 <= ix <= len(order):
            V[order[ix - 1]] = x
            continue
        hit = [v for v in order if nv(v) == nv(x.get("value"))]
        if len(hit) == 1:
            V[hit[0]] = x
    row["n_verdicts_matched"] = len(V)
    keep = {v for v in agreed if (V.get(v) or {}).get("value_ok") == "yes"}
    row["n_values_proposed"] = len(agreed)
    row["n_values_confirmed"] = len(keep)
    srows, drows, aud = [], [], dict(hc_kept_existing=0, hc_disagree_existing=0, iv_kept_existing=0, iv_disagree_existing=0, hc_code_rejected=0)
    kept = assigned[assigned.isin(list(keep))]
    if len(kept) < 0.7 * max(1, row["n_specimens"]) or len({agreed[v].get("label") for v in kept.unique()}) < 2:
        row["status"] = "done_verification_below_threshold"
        return row, srows, drows, aud
    is_name = k1.startswith(("name:", "stem:"))
    src = f"sample.name_rule:{k1.split(':')[1]}" if k1.startswith("name:") else (f"sample.attr_stem:{k1.split(':')[1]}" if is_name else f"sample.attr.{k1}")
    for sk, v in kept.items():
        g = agreed[v]
        lab = str(g.get("label"))[:80]
        hc = hc_ok.get(v)
        if hc and (V.get(v) or {}).get("code_ok") != "yes":
            aud["hc_code_rejected"] += 1
            hc = None
        ivc = iv_ok.get(v)
        srows.append(dict(study_accession=acc, sample_key=sk, group_key=k1, value=str(v)[:200], group_label=lab, kind=g.get("kind"),
                          health_condition=hc, intervention=ivc))
        q = f"{k1} = {str(v)[:60]}"

        def det(field, val):
            return dict(sample_key=sk, field_name=field, study_accession=acc, field_value=str(v)[:200], value_normalized=val,
                        confidence=0.8, evidence_source=src, evidence_locator=k1, evidence_quote=q, evidence_limited_to_abstract=False,
                        determined_by=f"{PASS}:{MODEL_TAG}x2+verify", route="R1", scope="sample",
                        parse_note=f"design pass: '{k1}' value -> compared group '{lab}'; two readings of the {tx['mode']} text agreed, an Opus check confirmed")
        drows.append(det("comparison_group", lab))
        for field, val, tag in (("health_condition", hc, "hc"), ("intervention", ivc, "iv")):
            if not val:
                continue
            ex = existing.get((sk, field))
            if ex is not None:
                aud[f"{tag}_kept_existing"] += 1
                if str(ex) != str(val):
                    aud[f"{tag}_disagree_existing"] += 1
                continue
            drows.append(det(field, val))
    row.update(n_assigned=len(srows), n_groups_assigned=len({r["group_label"] for r in srows}), status="done_assigned")
    return row, srows, drows, aud


def _ask(llm, reqs, status):
    try:
        res = llm(reqs) if reqs else []
    except BudgetExceeded as e:
        status["budget"] = str(e)[:200]
        return [{} for _ in reqs]
    for r in res:
        status[r.get("status")] = status.get(r.get("status"), 0) + 1
    return res


def run(projects, out, budget, shard, host=None, verify_budget=None):
    if host is not None:
        M.set_host(host)
    for sub in ("design/logs", "deep"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    st = pd.read_parquet(os.path.join(PKG, "catalog_studies.parquet"))
    st = st[st.release_retired.isna()] if "release_retired" in st.columns else st
    st = st.set_index("study_accession")
    papers = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "catalog", "study_papers.parquet"))
    papers = papers[papers.relation == "own_data"]
    w = pd.read_parquet(os.path.join(PKG, "catalog_sample_metadata_wide.parquet"), columns=["study_accession", "sample_key", "sample_type", "subject_id", "release_retired"],
                        filters=[("study_accession", "in", list(projects))])
    w = w[w.release_retired.isna() & (w.sample_type.fillna("specimen") == "specimen")]
    spec_by = {k: list(g.sample_key) for k, g in w.groupby("study_accession")}
    subj_by = {k: g.set_index("sample_key").subject_id for k, g in w.groupby("study_accession")}
    attrs = pd.read_parquet(ATTR, columns=["sample_acc", "study_accession", "attr_key", "attr_value"], filters=[("study_accession", "in", list(projects))])
    if RUN_FIELDS_ON:
        # R2026.40 (deep pass B2): run-level fields of the archive (ENA run table) as further candidate group variables — a
        # project whose groups no BioSample attribute separates often carries them in the library name or in the ENA
        # host / disease / experimental-factor columns. One value per sample (samples whose runs disagree get none).
        rr = pd.read_parquet(REG_RUNS, columns=["study_accession", "sample_accession"] + RUN_FIELDS, filters=[("study_accession", "in", list(projects))])
        have_keys = set(attrs.attr_key.astype(str).str.lower())
        extra = []
        for c in RUN_FIELDS:
            v = rr[["study_accession", "sample_accession", c]].dropna()
            v = v[v[c].astype(str).str.strip().ne("")]
            g = v.groupby(["study_accession", "sample_accession"])[c].agg(lambda x: x.astype(str).str.strip().unique())
            g = g[g.map(len) == 1].map(lambda x: x[0]).reset_index()
            key = c if (c == "library_name" and c not in have_keys) else f"run:{c}"
            extra.append(pd.DataFrame({"sample_acc": g.sample_accession, "study_accession": g.study_accession, "attr_key": key, "attr_value": g[c]}))
        if extra:
            attrs = pd.concat([attrs] + extra, ignore_index=True)
    attrs_by = {k: g for k, g in attrs.groupby("study_accession")}
    cd = pd.read_parquet(os.path.join(PKG, "catalog_sample_determinations.parquet"), columns=["sample_key", "study_accession", "field_name", "value_normalized", "scope"],
                         filters=[("study_accession", "in", list(projects)), ("field_name", "in", ["health_condition", "intervention"])])
    cd = cd[cd.scope != "study_all"]
    existing = {(r.sample_key, r.field_name): r.value_normalized for r in cd.itertuples(index=False)}
    cache = os.path.expanduser("~/catalog/cache/llm/design_r2026_33")
    llm = LLM("rubric", cache_dir=cache, ledger=os.path.join(out, "design", "logs", f"ledger_D{shard}.csv"), budget_usd=budget,
              pass_name="design_r2026_33", max_concurrency=4)
    ver = LLM("adjudicate", cache_dir=cache + "_verify", ledger=os.path.join(out, "design", "logs", f"ledger_verify_D{shard}.csv"),
              budget_usd=verify_budget if verify_budget is not None else budget, pass_name="design_verify_r2026_33", max_concurrency=4)
    global MODEL_TAG
    MODEL_TAG = llm.model
    T, C, S, P, reqs, keys = {}, {}, {}, {}, [], []
    for acc in projects:
        if acc not in st.index:
            continue
        d = CP.load_dossier(acc)
        tx = CP.project_texts(acc, d, st.loc[acc].to_dict(), papers)
        spec = spec_by.get(acc, [])
        lines, cand = candidate_variables(attrs_by.get(acc, attrs.iloc[0:0]), spec)
        T[acc], C[acc], S[acc] = tx, cand, spec
        P[acc] = tx["prompt"] + f"\n\nSPECIMENS DEPOSITED IN THIS PROJECT: {len(spec)}" + "\n\nCANDIDATE GROUP VARIABLES (key [samples with a value]: every distinct value x samples):\n" + (
            "\n".join(lines) if lines else "  (none: no sample attribute or name stem has 2-40 distinct values)")
        for rep in (1, 2):
            reqs.append({"system": SYSTEM + ("" if rep == 1 else "\n(second, independent reading)"), "prompt": P[acc],
                         "tools": [TOOL], "tool_choice": {"type": "tool", "name": "record"}, "max_tokens": 2500, "thinking": NOTHINK})
            keys.append((acc, rep))
    status = {}
    res = _ask(llm, reqs, status)
    R = {}
    for (acc, rep), r in zip(keys, res):
        tu = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        R.setdefault(acc, [None, None])[rep - 1] = tu if valid_reading(tu, C.get(acc)) else None
    retry = [(acc, rep) for acc, x in R.items() for rep in (1, 2) if x[rep - 1] is None]   # one re-ask for missing / degenerate readings
    rq = [{"system": SYSTEM + ("" if rep == 1 else "\n(second, independent reading)") + "\n(retry: fill every required field; the group key must be one of the listed CANDIDATE GROUP VARIABLES or 'none')", "prompt": P[acc],
           "tools": [TOOL], "tool_choice": {"type": "tool", "name": "record"}, "max_tokens": 2500, "thinking": NOTHINK} for acc, rep in retry]
    for (acc, rep), r in zip(retry, _ask(llm, rq, status)):
        tu = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        if valid_reading(tu, C.get(acc)):
            R[acc][rep - 1] = tu
    ROWS, PROPS = {}, {}
    for acc, tx in T.items():
        reps = R.get(acc, [None, None])
        n_ok = sum(1 for x in reps if x)
        if n_ok < 2:
            ROWS[acc] = dict(study_accession=acc, text_mode=tx["mode"], status="no_reading" if n_ok == 0 else "one_reading",
                             shape_r1=(reps[0] or {}).get("study_shape"), shape_r2=(reps[1] or {}).get("study_shape"),
                             n_specimens=len(S[acc]), n_candidate_keys=len(C[acc]))
            continue
        row, prop = merge_project(acc, tx, reps, C[acc], S[acc], existing, subj_by.get(acc))
        ROWS[acc] = row
        if prop:
            PROPS[acc] = prop
    # R2026.34: shape tie-break (role `adjudicate`) where the two readings gave different design types
    treqs, tkeys = [], []
    for acc, row in ROWS.items():
        reps = R.get(acc, [None, None])
        if row.get("shape_r1") and row.get("shape_r2") and row["shape_r1"] != row["shape_r2"] and all(reps):
            ans = "\n".join(f"{nm} answer: {r_.get('study_shape')} (quote: {str(r_.get('shape_quote') or 'none')[:300]}); statement: {str(r_.get('design_statement') or '')[:400]}"
                            for nm, r_ in (("first", reps[0]), ("second", reps[1])))
            treqs.append({"system": TIE_SYSTEM, "prompt": T[acc]["prompt"][:24000] + "\n\nTHE TWO ANSWERS:\n" + ans, "tools": [TIE_TOOL],
                          "tool_choice": {"type": "tool", "name": "tiebreak"}, "max_tokens": 800, "thinking": NOTHINK})
            tkeys.append(acc)
    tstatus = {}
    for acc, r in zip(tkeys, _ask(ver, treqs, tstatus)):
        tu = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        ch = (tu or {}).get("choice") if isinstance(tu, dict) else None
        ROWS[acc]["shape_tiebreak"] = ch or "missing"
        reps = R[acc]
        if ch in ("first", "second"):
            rr = reps[0] if ch == "first" else reps[1]
            ROWS[acc]["study_shape"] = rr.get("study_shape")
            ROWS[acc]["design_statement"] = rr.get("design_statement")
            ROWS[acc]["shape_tiebreak_quote"] = str((tu or {}).get("quote") or "")[:300]
    # Opus check of every proposed value -> group map
    vreqs, vkeys = [], []
    for acc, prop in PROPS.items():
        txt = T[acc]["prompt"][:24000]
        vc = prop["values"]
        mp = "\n".join(f"  {i}. value: {str(v)[:80]} ({vc.get(v, 0)} samples) -> group '{g.get('label')}' (kind {g.get('kind')}, health_condition {prop['hc'].get(v) or 'none'}, "
                       f"intervention {prop['iv'].get(v) or 'none'})" for i, (v, g) in enumerate(prop["agreed"].items(), 1))
        vreqs.append({"system": VERIFY_SYSTEM, "prompt": f"{txt}\n\nSTUDY DESIGN (both readings): {prop['shape']}\nSAMPLE VARIABLE: {prop['key']}\nPROPOSED MAP (numbered):\n{mp}",
                      "tools": [VERIFY_TOOL], "tool_choice": {"type": "tool", "name": "verify"}, "max_tokens": 2000, "thinking": NOTHINK})
        vkeys.append(acc)
    vstatus = {}
    SROWS, DROWS = [], []
    AUD = dict(hc_kept_existing=0, hc_disagree_existing=0, iv_kept_existing=0, iv_disagree_existing=0, hc_code_rejected=0)
    for acc, r in zip(vkeys, _ask(ver, vreqs, vstatus)):
        tu = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        items = (tu or {}).get("items") if isinstance(tu, dict) else None
        if not isinstance(items, list):
            ROWS[acc]["status"] = "done_verification_missing"
            continue
        row, srows, drows, aud = finalise(acc, T[acc], ROWS[acc], PROPS[acc], items, existing)
        ROWS[acc] = row
        SROWS += srows
        DROWS += drows
        for k_, v_ in aud.items():
            AUD[k_] += v_
    k = shard
    df = pd.DataFrame(list(ROWS.values()))
    for c_ in df.columns:   # readings can return a number as a string: keep each column one type
        if df[c_].dtype == object and df[c_].map(lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)).any() and df[c_].map(lambda x: isinstance(x, str)).any():
            df[c_] = df[c_].map(lambda x: None if x is None or (isinstance(x, float) and x != x) else str(x))
    df.to_parquet(os.path.join(out, "design", f"design_reading_shard_D{k}.parquet"), index=False)
    pd.DataFrame(SROWS, columns=["study_accession", "sample_key", "group_key", "value", "group_label", "kind", "health_condition", "intervention"]).to_parquet(
        os.path.join(out, "design", f"design_sample_groups_shard_D{k}.parquet"), index=False)
    pd.DataFrame(DROWS, columns=DET_COLS).to_parquet(os.path.join(out, "deep", f"deep_determinations_shard_GRPD{k}.parquet"), index=False)
    summ = dict(shard=k, projects=len(T), llm_status=status, verify_status=vstatus, tiebreak_status=tstatus,
                tiebreak=df.shape_tiebreak.value_counts().to_dict() if "shape_tiebreak" in df.columns else {}, modes=pd.Series([t_["mode"] for t_ in T.values()]).value_counts().to_dict(),
                status=df.status.value_counts().to_dict() if len(df) else {},
                shapes_agree=int((df.shape_r1 == df.shape_r2).sum()) if len(df) else 0, retried_readings=len(retry),
                projects_proposed=len(PROPS), projects_assigned=int((df.status == "done_assigned").sum()) if len(df) else 0,
                values_proposed=int(df.get("n_values_proposed", pd.Series(dtype=float)).fillna(0).sum()),
                values_confirmed=int(df.get("n_values_confirmed", pd.Series(dtype=float)).fillna(0).sum()),
                specimens_assigned=len(SROWS), det_rows=pd.Series([d_["field_name"] for d_ in DROWS]).value_counts().to_dict() if DROWS else {},
                audit=AUD, cost=llm.summary(), cost_verify=ver.summary())
    json.dump(summ, open(os.path.join(out, "design", "logs", f"design_summary_D{k}.json"), "w"), indent=1, default=str)
    print(json.dumps(summ, default=str)[:1800])
    return summ


def merge(out):
    d = os.path.join(out, "design")
    for kind, name in (("design_reading", "design_reading_all.parquet"), ("design_sample_groups", "design_sample_groups_all.parquet")):
        fs = sorted(glob.glob(os.path.join(d, f"{kind}_shard_D*.parquet")))
        fs = [f for f in fs if "pilot" not in os.path.basename(f)] or fs
        parts = [pd.read_parquet(f) for f in fs]
        parts = [p for p in parts if len(p)]
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if len(df) and "study_accession" in df.columns and kind == "design_reading":
            df = df.drop_duplicates("study_accession", keep="last")
        df.to_parquet(os.path.join(d, name), index=False)
        print(kind, len(fs), len(df))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", help="CSV with a study_accession column")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="first N projects of the shard only (pilot)")
    ap.add_argument("--budget", type=float, default=5.0)
    ap.add_argument("--verify-budget", type=float, default=None, help="USD for the Opus check (default: same as --budget)")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "inputs", "gut"))
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--run-fields", action="store_true", help="also offer run-level archive fields (library name, ENA host / disease / experimental factor) as candidate group variables (R2026.40)")
    ap.add_argument("--tag", default="", help="shard name prefix for a re-run (R2026.34: 'V2_' sorts after the first run's shards, so --merge keeps the re-run)")
    a = ap.parse_args(argv)
    global RUN_FIELDS_ON
    RUN_FIELDS_ON = bool(a.run_fields)
    if a.merge:
        return merge(a.out)
    ids = sorted(set(pd.read_csv(a.projects).study_accession.astype(str)))
    mine = [p for i, p in enumerate(ids) if i % a.nshards == a.shard]
    if a.limit:
        mine = mine[:a.limit]
    return run(mine, a.out, a.budget, (f"{a.tag}{a.shard}" if a.tag else a.shard) if not a.limit else f"pilot{a.tag}{a.shard}", host=globals().get("host"), verify_budget=a.verify_budget)


if __name__ == "__main__":
    main()
