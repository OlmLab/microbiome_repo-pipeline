#!/usr/bin/env python
"""persontime_passes.py — who and when: the person and time-point variables of repeated-sampling projects (R2026.34).

Targets (from the built package): projects whose source describes repeated sampling but whose samples show no person with
more than one sample (longitudinal_status 'repeated_unresolved', mode `person`), and longitudinal projects whose repeated
persons have no time axis (longitudinal_resolved false, mode `time`).

Deterministic candidates over the project's catalog specimens:
  PERSON  sample attributes whose values repeat (>= 80 % coverage, >= 3 values, >= 50 % of samples share a value, median
          <= 40 samples per value; never a demographic / specimen vocabulary such as age, sex, group, tissue, source), and
          SPLITS of sample names / near-unique attributes into a person part and a time part ('P01_D7' -> 'P01' + 'D7';
          separators, then letter/digit boundaries; one token removed, or a prefix / suffix), computed on the samples of
          the commonest token count when they are >= 50 % of the specimens, kept when the person part repeats, the
          (person, time) pairs are near unique and the time part varies within >= 80 % of persons. Up to 8.
          In mode `time` the person is the catalog subject id (fixed).
  TIME    attributes, the catalog's timepoint labels and the time parts of the splits, with 2-60 values over >= 50 % of
          the specimens, varying within the persons of at least one person candidate (values numbered in the prompt);
          plus NUMERIC time variables (time-named attributes whose values are >= 90 % numbers, > 12 distinct values; no
          cap), for which the readings give the unit and the origin. Duplicate partitions are listed once; a reading that
          names a dropped duplicate is resolved to the kept key.
Two independent readings (role `rubric`) of the project's text (same text builder as the design pass) plus the candidates
return: repeated_sampling (yes / multiple_samples_per_person / no / unclear), n_participants_stated, person_key, time_key,
time_origin, time_unit and, for every numbered value of the time key, a label and the days since the time origin (null
unless the text or the value gives it). A key not on the list becomes 'none' (the other key is kept); such a reading is
re-asked once.
Merging (deterministic; R2026.34 pilot-audit rules):
  person   both readings chose the same key, or keys whose partitions agree on >= 90 % of shared samples both ways; both say
           repeated_sampling yes or multiple_samples_per_person; >= 2 persons; >= 30 % of samples in persons with >= 2
           samples; at most 1.5 x + 2 the participant count both readings state; sex constant within >= 90 % of persons.
  time     a reading says repeated_sampling yes and none says no; the same key (or partition-equivalent keys, values
           translated); a value is agreed when both give days within 1 day (or 5 %), else when the labels agree (same
           digits, containment or >= 50 % shared words; the shorter label kept, approximate-interval parentheticals
           removed); values that name a body site / sample type / fraction / role and no time word are dropped. Numeric
           keys: both readings give the same unit. Offsets need >= 2 distinct days varying within >= 50 % of persons.
  Opus check (role `adjudicate`) of the person key (example samples) and of every numbered time value (is it a TIME POINT
           and is the label right; are the days right) or of the numeric unit and origin. Written: subject ids only when
           person_ok yes; time labels and offsets whenever confirmed (independent of the person check); offsets only with
           >= 2 distinct confirmed days that vary within >= 30 % of the repeated persons; a confirmed decoding of a bare
           stored catalog code ('A' -> 'before ablation') replaces the code (confidence 0.91).
Outputs under --out (data/inputs/gut), shard k:
  design/persontime_reading_shard_PT{k}.parquet   one row per project
  deep/deep_determinations_shard_PTD{k}.parquet   subject_id (R1 0.8; 0.92 where the catalog's ids are one per sample)
                                                  and timepoint_label (R1 0.8, only where the sample has none)
  timing/timing_samples_pt_shard_{k}.parquet      per-sample day offsets (read by the build with timing_samples.parquet)
  design/logs/persontime_summary_PT{k}.json
Prints counts only.

usage (inside a kernel that has `host`, via runpy with init_globals={"host": host}):
  python scripts/persontime_passes.py --targets build/persontime_targets.csv --shard K --nshards N --budget USD [--limit N]
  python scripts/persontime_passes.py --merge
  python scripts/persontime_passes.py --make-targets build/persontime_targets.csv
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)
import design_passes as DP  # noqa: E402  (text builder via catchup_passes, HARD_ADMIN, NAME_KEYS, CONTROL_VALUE, _ask)
from catalog import models as M  # noqa: E402
from catalog.llm_backend import LLM  # noqa: E402
from catalog.scopes.longitudinal import parse_timepoint_days  # noqa: E402

CP, PP = DP.CP, DP.PP
M.set_host(globals().get("host"))
PKG = DP.PKG
ATTR = DP.ATTR
PASS = "catalog_persontime_v1"
NOTHINK = {"type": "disabled"}
NS, NI = {"type": ["string", "null"]}, {"type": ["integer", "null"]}
ORIGINS = ["birth", "enrolment_or_baseline", "intervention_start", "diagnosis", "procedure_or_surgery", "delivery", "admission", "other", "unknown"]
TIME_ADMIN = re.compile(r"^(ENA-|INSDC|BioSampleModel|NCBI submission|SAMPLE_NAME\.|center[ _]name$|organism$|Package$|Model$|lat_lon$|"
                        r"geo_loc_name$|host$|.*accession|broker|submitter|latitude|longitude|depth$|elevation|altitude|env_)", re.I)
TIMEISH = re.compile(r"(?i)time|visit|day|week|month|year|age|point|period|follow|baseline|sampling|collection|stage|phase|session|"
                     r"trimester|gestation|wave|round|occasion|\btp\b|date")
PERSONISH = re.compile(r"(?i)subject|patient|participant|individual|person|donor|host|infant|child|mother|baby|volunteer|\bid\b|pid|"
                       r"_id$|^id_|code")
NULLS = {"", "nan", "none", "missing", "not applicable", "not collected", "na", "n/a", "unknown", "not provided"}
SEP = re.compile(r"[_\-\.\s/:|,;#]+")

SYSTEM = """You read the text of one sequencing project of human-associated microbial communities (the archive record and, when
available, the paper that generated the data or its abstract) together with CANDIDATE VARIABLES computed from its samples,
and record WHO each sample comes from and WHEN it was taken, with the `record` tool:

1. repeated_sampling: 'yes' only when the PROSE (title, description, abstract, methods) says the same people were sampled
   more than once over time (time points, visits, days, before and after an intervention, a time series);
   'multiple_samples_per_person' when each person gave several samples at ONE visit (different body sites, sample types,
   fractions) and the prose describes no sampling over time; 'no' when it says each person gave one sample, or the samples
   are technical replicates; 'unclear' otherwise. repeated_quote: the verbatim sentence part (at most 30 words).
2. n_participants_stated: the number of people the text states for this project's samples, or null (a number of samples
   is never a number of people).
3. person_key: the listed PERSON VARIABLE whose values identify individual people: each value is one person and its
   samples are that person's samples. Never a variable whose values are groups, arms, households, sites, body sites, time
   points, ages, demographic categories or batches. The number of its values should fit the number of participants the text
   states. Give the key EXACTLY as listed, or 'none'. When the list says FIXED, give that key.
4. time_key: the listed TIME VARIABLE whose values say when each sample was taken (visit, time point, day, week, age,
   before / after). A variable whose values are body sites, sample types, materials, DNA fractions, donor / recipient
   roles or study groups is NEVER a time variable, even when each person has several of them: answer 'none'. The time
   variable records WHEN THE SAMPLE WAS TAKEN FROM THE PERSON, never how long a sample was stored, incubated, fermented,
   frozen or otherwise handled afterwards: answer 'none' for those. Give the key
   EXACTLY as listed, or 'none'. Keys starting 'numeric:' hold numbers (days, weeks, ... since an origin): for them give
   time_unit and time_origin and leave time_values empty.
5. time_origin: what time zero is (birth, enrolment_or_baseline, intervention_start, diagnosis, procedure_or_surgery,
   delivery, admission, other, unknown). time_unit: for a 'numeric:' key, the unit of its numbers (days, weeks, months,
   years) when the text or the variable name states it; otherwise 'none'.
6. time_values: for EVERY numbered value of the chosen time variable: index (its number in the list), value (as listed),
   label (a plain name in the text's words: 'baseline', 'week 4', 'day 7 after birth', 'after treatment'; do not add
   intervals the text does not give for that value) and days = days since the time origin, ONLY when the text gives the
   timing or the value states it unambiguously ('D7' = 7, 'W4' = 28, 'M6' = 183, '1y' = 365, baseline = 0; a visit code
   such as 'V2' or 'T3' only when the text gives the visit schedule; 'before' / 'after' only when the text gives the
   interval; an approximate interval the text states, such as 'about six months', counts). Otherwise days = null. Never
   guess a schedule.
Judge only from the text and the listed values."""

TOOL = PP.tool("record", {
    "repeated_sampling": {"type": "string", "enum": ["yes", "multiple_samples_per_person", "no", "unclear"]},
    "repeated_quote": NS,
    "n_participants_stated": NI,
    "person_key": {"type": "string"},
    "time_key": {"type": "string"},
    "time_origin": {"type": "string", "enum": ORIGINS},
    "time_unit": {"type": "string", "enum": ["days", "weeks", "months", "years", "none"]},
    "time_values": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"}, "value": {"type": "string"}, "label": {"type": "string"}, "days": {"type": ["number", "null"]}},
        "required": ["value", "label"]}},
}, ["repeated_sampling", "person_key", "time_key", "time_values"])
REP_OK = {"yes", "multiple_samples_per_person"}
UNIT_DAYS = {"days": 1.0, "weeks": 7.0, "months": 30.44, "years": 365.25}
UNIT_WORD = {"days": "day", "weeks": "week", "months": "month", "years": "year"}
# R2026.34 pilot audit: demographic / specimen vocabularies are never person ids; sample-type values are never time points
NOT_PERSON = re.compile(r"(?i)age|sex|gender|ethnic|race|stage|bmi|weight|height|group|arm\b|treat|adjuvant|response|diagnos|cohort|batch|"
                        r"\brun\b|kit|source|organism|species|material|tissue|isolation|disease|status|type|site|country|location|"
                        r"diet|antibiot|medicat|smok|delivery|feeding|condition|phenotype")
SITE_VALUE = re.compile(r"(?i)saliva|plaque|stool|faec|fec(es|al)|sputum|swab|tissue|tumou?r|biops|serum|plasma|lavage|\bbalf\b|nasal|"
                        r"\boral\b|conjunctiv|skin|urine|vagin|\bmilk\b|blood|mucosa|lumen|phage|bacteri|virom|paracancer|adjacent|"
                        r"donor|recipient|control group")
TIME_WORD = re.compile(r"(?i)day|week|month|year|visit|baseline|before|after|\bpre|\bpost|time|follow|birth|\bt\d|\bv\d|trimester|"
                       r"admission|discharge|enrol|screen|end of|start|onset|period|phase|wave|round")
HANDLING = re.compile(r"(?i)samp_mat_process|storage|stored|incubat|ferment|process|holding|\bhold\b|elapsed|thaw|freez|frozen|transport|"
                      r"shipping|extraction|room temperature|bioreactor|in vitro|culture")
GENERIC_TIME = {"timepoint", "time", "point", "tp", "visit", "sample", "sampling", "phase", "stage", "collection", "t", "v", "p", "s", "no", "number"}
DATE_LIKE = re.compile(r"(?i)(19|20)\d{2}[-/.]\d{1,2}([-/.]\d{1,2})?|\d{1,2}[-/. ]?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-/. ]?(19|20)?\d{2}")
TIMECODE = re.compile(r"(?i)^(d|day|w|wk|week|m|mo|month|y|yr|v|visit|t|tp|timepoint)[ _.-]?\d+$|baseline|\bpre|\bpost|before|after|follow")
BARE_CODE = re.compile(r"^\s*[A-Za-z]{0,3}[-_ ]?\d{0,3}[A-Za-z]?\s*$")
APPROX_PAREN = re.compile(r"\s*\((?=[^)]*(~|≈|approx|about|around|estimated|roughly|ca\.))[^)]*\)")

VERIFY_SYSTEM = """You check a proposed reading of WHO each sequencing sample comes from and WHEN it was taken, for one study. You get
the project's text (archive record and paper or abstract), the proposed person variable with example samples, and a numbered
map of time values (label, days since the time origin). Answer:
  person_ok: yes = the variable's values identify individual people (each value one person, consistent with the text's
             participants and sampling scheme); no = they are groups, households, sites, time points, batches or samples;
             unsure; not_applicable when the person variable is marked FIXED.
             not_applicable also when no person variable is proposed.
  for EACH numbered time value: label_ok yes / no / unsure: is this value a TIME POINT at which the sample was taken
             from the person (not a body site, sample type, fraction, donor / recipient role, study group, or a storage /
             incubation / processing time of the sample) AND does the label state it correctly? days_ok yes / no /
             unsure / not_applicable: are the days right from the text or the value itself (an approximate interval the
             text states, such as 'about six months' = 183, is yes; a schedule the text does not give is no);
             not_applicable when days are not given. Give the number as `index`.
  numeric_ok (only for a NUMERIC time variable): yes = the text or variable name shows that its numbers are the stated
             unit since the stated time origin; no; unsure; not_applicable when the time variable is not numeric.
Judge only from the text and the values given; unsure when in doubt."""

VERIFY_TOOL = PP.tool("verify", {
    "person_ok": {"type": "string", "enum": ["yes", "no", "unsure", "not_applicable"]},
    "person_reason": NS,
    "numeric_ok": {"type": "string", "enum": ["yes", "no", "unsure", "not_applicable"]},
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"}, "value": {"type": "string"}, "label_ok": {"type": "string", "enum": ["yes", "no", "unsure"]},
        "days_ok": {"type": "string", "enum": ["yes", "no", "unsure", "not_applicable"]}}, "required": ["index", "label_ok", "days_ok"]}},
}, ["person_ok", "items"])


# ---------------------------------------------------------------------------------------------------------------- candidates
def _clean(s):
    s = s.astype(str).str.strip()
    return s[~s.str.lower().isin(NULLS)]


def _toks(v, fine):
    t = [x for x in SEP.split(str(v).strip()) if x]
    if fine:
        t = [y for x in t for y in re.findall(r"[A-Za-z]+|\d+", x)]
    return tuple(t)


def _within(p, t):
    """share of persons with >= 2 samples whose samples have >= 2 distinct values of t"""
    j = pd.DataFrame({"p": p, "t": t}).dropna()
    if not len(j):
        return 0.0
    g = j.groupby("p")
    n = g.size()
    rep = n[n >= 2].index
    if not len(rep):
        return 0.0
    return float((g.t.nunique().loc[rep] >= 2).mean())


def _person_stats(p):
    per = p.value_counts()
    return dict(n_persons=int(len(per)), rep_share=float(per[per >= 2].sum() / max(len(p), 1)),
                per_median=float(per.median()) if len(per) else 0.0, per_max=int(per.max()) if len(per) else 0)


def _modal(T, n, min_cov=0.5):
    """the samples of the commonest token count, when they are >= min_cov of the specimens (R2026.34: a project that mixes
    two naming schemes keeps its majority scheme)"""
    L = T.map(len)
    if not len(L):
        return None, 0
    lm = int(L.mode().iloc[0])
    if lm < 2 or lm > 8:
        return None, lm
    T2 = T[L == lm]
    if len(T2) < min_cov * n:
        return None, lm
    return T2, lm


def _splits(s, n, src):
    """name splits of one near-unique series -> [(score, key, person Series, time Series, raw Series)]"""
    out = []
    for fine in (False, True):
        T, lm = _modal(s.map(lambda v: _toks(v, fine)), n)
        if T is None:
            continue
        cuts = [("drop%d" % j, lambda t, j=j: (t[:j] + t[j + 1:], t[j:j + 1])) for j in range(lm)]
        cuts += [("pre%d" % k, lambda t, k=k: (t[:k], t[k:])) for k in range(1, lm)]
        cuts += [("suf%d" % k, lambda t, k=k: (t[k:], t[:k])) for k in range(1, lm - 1)]
        for desc, fn in cuts:
            pt = T.map(fn)
            P0 = pt.map(lambda x: x[0])
            w0 = len(P0.iloc[0]) if len(P0) else 0
            # words constant over the project ('metagenome', 'genome') are dropped from the person id; the partition is unchanged
            keep = [i for i in range(w0) if not (P0.map(lambda t_, i=i: t_[i]).nunique() == 1 and P0.iloc[0][i].isalpha() and len(P0.iloc[0][i]) >= 4)] or list(range(w0))
            p = P0.map(lambda t_: "_".join(t_[i] for i in keep))
            t = pt.map(lambda x: "_".join(x[1]))
            if p.eq("").any() or t.eq("").any():
                continue
            st = _person_stats(p)
            nt = t.nunique()
            if not (2 <= st["n_persons"] <= len(p) - 1) or st["rep_share"] < 0.5 or not (2 <= nt <= 60):
                continue
            pair_u = float((~pd.DataFrame({"p": p, "t": t}).duplicated()).mean())
            wi = _within(p, t)
            if pair_u < 0.85 or wi < 0.8 or (fine and _sign_clash(t, s)):
                continue
            key = f"split:{src}:{'fine' if fine else 'sep'}:{desc}"
            # tie-break: a person part with more distinct values than the time part, and the larger sample cover
            sc = st["rep_share"] + pair_u + wi - 0.002 * nt + (0.05 if st["n_persons"] > nt else 0) + 0.2 * len(p) / n
            out.append((sc, key, p, t, s.loc[p.index]))
    return out


SIGNED_RAW = re.compile(r"[A-Za-z][-−+][0-9]")


def _sign_clash(t, raw):
    """R2026.34 production audit: a time code that merges a signed raw value ('W-2', two weeks before) with an unsigned one
    ('W2', two weeks after) -> True"""
    sg = raw.reindex(t.index).astype(str).map(lambda v: bool(SIGNED_RAW.search(v)))
    g = sg.groupby(t.values)
    return bool((g.any() & ~g.all()).any())


def _sig(p):
    return hash(tuple(sorted(tuple(sorted(g)) for g in p.groupby(p).groups.values())))


def _numeric(s):
    v = pd.to_numeric(s.astype(str).str.strip().str.replace(",", ".", regex=False), errors="coerce")
    return v.dropna() if v.notna().mean() >= 0.9 else None


def st_n(p):
    return int(p.nunique())


def _timeish_values(s):
    """+1 for a variable whose values read as time codes (W0, day 7, baseline), -1 for bare dates: the better-named twin wins
    de-duplication"""
    u = pd.Series(s.astype(str).unique())
    if not len(u):
        return 0.0
    if u.map(lambda v: bool(DATE_LIKE.search(v))).mean() >= 0.8:
        return -1.0
    return float(u.map(lambda v: bool(TIMECODE.search(v))).mean())


def _vals(s):
    """time values in prompt order (numbered 1..)"""
    return [str(v) for v in s.astype(str).value_counts().index]


def candidates(attrs, specimens, fixed_person=None, tp_labels=None):
    """-> person {key: dict(series, raw, kind, stats[, time])}, time {key: Series}, raw names of split time keys {key: Series},
    alias {de-duplicated key: kept key}, numeric {key: Series of numbers}"""
    n = len(specimens)
    person, time_, alias, numeric = {}, {}, {}, {}
    if not n:
        return person, time_, {}, alias, numeric
    a = attrs[attrs.sample_acc.isin(specimens)].drop_duplicates(["sample_acc", "attr_key"]) if len(attrs) else attrs
    series = {}
    for key, g in (a.groupby("attr_key") if len(a) else []):
        s = _clean(g.set_index("sample_acc").attr_value)
        if len(s):
            series[str(key)] = s
    traw = {}
    near = [k for k, s in series.items() if (k in DP.NAME_KEYS or not DP.HARD_ADMIN.match(k)) and len(s) >= 0.8 * n and s.nunique() >= 0.9 * len(s)]
    near = sorted(near, key=lambda k: (k not in DP.NAME_KEYS, k))[:6]
    if fixed_person is not None:
        fp = fixed_person.dropna().astype(str)
        person["catalog:subject_id"] = dict(series=fp, raw=fp, kind="fixed", stats=_person_stats(fp))
        # time parts of sample names / near-unique attributes that vary within the catalog's persons
        tparts = []
        for k in near:
            for fine in (False, True):
                T, lm = _modal(series[k].map(lambda v: _toks(v, fine)), n)
                if T is None:
                    continue
                for desc, sl in [("tok%d" % j, slice(j, j + 1)) for j in range(lm)] + [("tail%d" % j, slice(j, None)) for j in range(1, lm - 1)]:
                    t = T.map(lambda x: "_".join(x[sl]))
                    if t.eq("").any() or not (2 <= t.nunique() <= 60):
                        continue
                    wi = _within(fp, t)
                    if wi >= 0.8 and not (fine and _sign_clash(t, series[k])):
                        key = f"split:{k}:{'fine' if fine else 'sep'}:{desc}:time"
                        tparts.append((wi - 0.002 * t.nunique() + 0.2 * len(t) / n, key, t, series[k].loc[t.index]))
        seen_t = {}
        for sc, key, t, raw in sorted(tparts, key=lambda x: -x[0]):
            sg = _sig(t)
            if sg in seen_t:
                alias[key] = seen_t[sg]
                continue
            if len(traw) >= 3:
                continue
            seen_t[sg] = key
            traw[key] = (t, raw)
    else:
        cand = []
        for key, s in series.items():   # attribute person candidates (no demographic / specimen vocabularies)
            if DP.HARD_ADMIN.match(key) or key in DP.NAME_KEYS or len(s) < 0.8 * n or NOT_PERSON.search(key):
                continue
            st = _person_stats(s)
            if not (3 <= st["n_persons"] <= len(s) - 1) or st["rep_share"] < 0.5 or st["per_median"] > 40:
                continue
            if s.str.match(DP.CONTROL_VALUE).mean() > 0.5 or (n >= 20 and st["n_persons"] <= 4):
                continue
            sc = st["rep_share"] + (0.5 if PERSONISH.search(key) else 0) + 0.5 * min(1.0, st["n_persons"] / 10)
            cand.append((sc, key, s, None, s, "attr"))
        for k in near:   # splits of names and near-unique attributes
            for sc, key, p, t, raw in _splits(series[k], n, k):
                cand.append((sc, key, p, t, raw, "split"))
        seen = {}
        for sc, key, p, t, raw, kind in sorted(cand, key=lambda x: -x[0]):
            sg = _sig(p)
            if sg in seen:
                alias[key] = seen[sg]
                continue
            if len(person) >= 5 or (n >= 20 and st_n(p) <= 4):
                continue
            seen[sg] = key
            person[key] = dict(series=p, raw=raw, kind=kind, stats=_person_stats(p), time=t)
    # time candidates
    tc, numc = [], []
    for key, s in series.items():
        if TIME_ADMIN.match(key) or key in DP.NAME_KEYS or len(s) < 0.5 * n or HANDLING.search(key):
            continue
        wi = max([_within(v["series"], s) for v in person.values()] or [0.0])
        if wi < 0.5:
            continue
        nu = s.nunique()
        num = _numeric(s) if TIMEISH.search(key) else None
        if num is not None and num.nunique() >= 3 and (nu > 12 or nu > 60):
            numc.append((wi, f"numeric:{key}", num))
        if 2 <= nu <= 60:
            tc.append(((1 if TIMEISH.search(key) else 0) + wi + _timeish_values(s), key, s))
    if tp_labels is not None:
        s = _clean(tp_labels.dropna())
        if len(s) >= 0.5 * n and 2 <= s.nunique() <= 60:
            wi = max([_within(v["series"], s) for v in person.values()] or [0.0])
            if wi >= 0.5:
                tc.append((2 + wi + _timeish_values(s), "catalog:timepoint_label", s))
    for k, v in person.items():
        if v.get("time") is not None:
            tc.append((1.5 + _timeish_values(v["time"]), k + ":time", v["time"]))
            traw[k + ":time"] = (v["time"], v["raw"])
    for k, (t, raw) in list(traw.items()):
        if k.startswith("split:") and fixed_person is not None:
            tc.append((1.4 + _timeish_values(t), k, t))
    seen = {}
    for sc, key, s in sorted(tc, key=lambda x: -x[0]):
        sg = _sig(s)
        if sg in seen:
            alias[key] = seen[sg]
            continue
        if len(time_) >= 7:
            continue
        seen[sg] = key
        time_[key] = s
    for wi, key, num in sorted(numc, key=lambda x: -x[0])[:2]:
        time_[key] = num.astype(str)
        numeric[key] = num
    return person, time_, {k: v[1] for k, v in traw.items() if k in time_}, alias, numeric


def prompt_block(person, time_, n, fixed, traw=None, numeric=None):
    traw, numeric = traw or {}, numeric or {}
    lines = ["PERSON VARIABLES (which samples come from the same person):"]
    if not person:
        lines.append("  (none: no sample attribute or name part repeats like a person identifier)")
    for k, v in person.items():
        st = v["stats"]
        ex = ""
        if v["kind"] == "split":
            idx = list(v["series"].index[:4])
            ex = "; e.g. " + ", ".join(f"'{str(v['raw'].get(i))[:60]}' -> person '{v['series'][i]}', time part '{v['time'][i]}'" for i in idx)
        elif v["kind"] == "attr":
            ex = "; values e.g. " + ", ".join(str(x)[:30] for x in v["series"].value_counts().index[:6])
        lines.append(f"  {k} [{len(v['series'])} of {n} samples]{' (FIXED: the catalog subject id)' if fixed else ''}: {st['n_persons']} persons, "
                     f"samples per person median {st['per_median']:g}, max {st['per_max']}{ex}")
    lines.append("TIME VARIABLES (which time point each sample is; numbered values: value x samples):")
    if not time_:
        lines.append("  (none: no variable varies within persons)")
    for k, s in time_.items():
        if k in numeric:
            v = numeric[k]
            lines.append(f"  {k} [{len(v)} of {n} samples] NUMERIC: {v.nunique()} distinct numbers from {v.min():g} to {v.max():g}; e.g. "
                         + ", ".join(f"{x:g}" for x in v.drop_duplicates().head(10)))
            continue
        ex = ""
        if k in traw:
            ex = " (e.g. " + ", ".join(f"'{str(traw[k].get(i))[:60]}' -> '{s[i]}'" for i in list(s.index[:3])) + ")"
        vc = s.astype(str).value_counts()
        lines.append(f"  {k} [{len(s)} of {n} samples]{ex}: " + "; ".join(f"{j}) {str(v)[:120]} x{c}" for j, (v, c) in enumerate(vc.items(), 1)))
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------------- merge
def valid(r, person, time_, alias=None):
    """-> the reading with its keys resolved (de-duplicated aliases -> kept key; an unlisted key -> 'none', recorded), or None"""
    if not isinstance(r, dict) or r.get("repeated_sampling") not in ("yes", "multiple_samples_per_person", "no", "unclear"):
        return None
    if not isinstance(r.get("time_values") or [], list):
        return None
    r = dict(r)
    al = alias or {}
    r["_invalid"] = 0
    for k_, pool in (("person_key", person), ("time_key", time_)):
        v = str(r.get(k_) or "none").strip()
        v = al.get(v, v)
        if v != "none" and v not in pool:
            r["_invalid"] += 1
            r[k_ + "_unlisted"] = v
            v = "none"
        r[k_] = v
    return r


def _num(x):
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _clean_label(lb):
    return APPROX_PAREN.sub("", str(lb or "")).strip()[:80]


def _labels_agree(a, b):
    na, nb = _norm(a), _norm(b)
    A, B = set(na.split()), set(nb.split())
    if not A or not B:
        return False
    if {t for t in A if t.isdigit()} != {t for t in B if t.isdigit()}:
        return False
    if A == B or na in nb or nb in na:
        return True
    return len(A & B) / min(len(A), len(B)) >= 0.5


def _partition_agree(a, b):
    """two variables agree when, on the samples both cover, each value of one goes with one value of the other for >= 90 %
    of those samples, in both directions"""
    j = pd.DataFrame({"a": a.astype(str), "b": b.astype(str)}).dropna()
    if len(j) < 4:
        return False
    fa = j.groupby("a").b.agg(lambda x: x.value_counts().iloc[0]).sum() / len(j)
    fb = j.groupby("b").a.agg(lambda x: x.value_counts().iloc[0]).sum() / len(j)
    return fa >= 0.9 and fb >= 0.9


def _translate(src, dst):
    """value of src -> modal value of dst on the shared samples"""
    j = pd.DataFrame({"a": src.astype(str), "b": dst.astype(str)}).dropna()
    return j.groupby("a").b.agg(lambda x: x.value_counts().index[0]).to_dict()


def _time_map(r, s):
    """reading's time_values -> {value: item}, matched by exact value, else by its number, else by a unique prefix"""
    vl = _vals(s)
    vs = set(vl)
    out = {}
    for x in r.get("time_values") or []:
        if not isinstance(x, dict):
            continue
        sv, j = str(x.get("value") or ""), x.get("index")
        v = None
        if sv in vs:
            v = sv
        elif isinstance(j, int) and 1 <= j <= len(vl):
            v = vl[j - 1]
        elif len(sv) >= 10:
            c = [w for w in vl if w.startswith(sv)]
            v = c[0] if len(c) == 1 else None
        if v is not None:
            out[v] = x
    return out


def merge_project(acc, reps, person, time_, n, sex, fixed, numeric=None):
    numeric = numeric or {}
    r1, r2 = reps
    rp1, rp2 = r1.get("repeated_sampling"), r2.get("repeated_sampling")
    row = dict(study_accession=acc, mode="time" if fixed else "person", n_specimens=n, n_person_cand=len(person), n_time_cand=len(time_),
               rep_r1=rp1, rep_r2=rp2, person_r1=r1.get("person_key"), person_r2=r2.get("person_key"),
               time_r1=r1.get("time_key"), time_r2=r2.get("time_key"), origin_r1=r1.get("time_origin"), origin_r2=r2.get("time_origin"),
               n_stated_r1=_num(r1.get("n_participants_stated")), n_stated_r2=_num(r2.get("n_participants_stated")),
               repeated_quote=r1.get("repeated_quote"), person_status=None, time_status=None, n_unlisted_keys=int(r1.get("_invalid", 0) + r2.get("_invalid", 0)))
    prop = {}
    k1, k2 = r1.get("person_key"), r2.get("person_key")
    if fixed:
        row["person_status"] = "fixed"
        prop["person"] = "catalog:subject_id"
    else:
        pk = None
        if k1 != "none" and k1 == k2:
            pk = k1
        elif k1 != "none" and k2 != "none" and _partition_agree(person[k1]["series"], person[k2]["series"]):
            pk = k1 if len(person[k1]["series"]) >= len(person[k2]["series"]) else k2
            row["person_match"] = "partition"
        if pk is None:
            row["person_status"] = "no_key" if k1 == "none" and k2 == "none" else "keys_differ"
        elif not (rp1 in REP_OK and rp2 in REP_OK):
            row["person_status"] = "not_repeated_in_text"
        else:
            p = person[pk]["series"]
            st = person[pk]["stats"]
            ns = row["n_stated_r1"] if row["n_stated_r1"] is not None and row["n_stated_r1"] == row["n_stated_r2"] else None
            sx = pd.DataFrame({"p": p, "s": sex.reindex(p.index)}).dropna()
            sx_bad = float((sx.groupby("p").s.nunique() > 1).mean()) if len(sx) else 0.0
            if st["n_persons"] < 2 or st["rep_share"] < 0.3:
                row["person_status"] = "too_few_repeats"
            elif ns is not None and st["n_persons"] > 1.5 * ns + 2:
                row["person_status"] = "more_persons_than_stated"
            elif sx_bad > 0.1:
                row["person_status"] = "sex_differs_within_person"
            else:
                row["person_status"] = "proposed"
                prop["person"] = pk
            row.update(n_persons=st["n_persons"], person_rep_share=round(st["rep_share"], 3), sex_conflict_share=round(sx_bad, 3))
    # the person series the time checks use: the agreed one, else either reading's choice
    pser = None
    for k_ in (prop.get("person"), k1, k2):
        if k_ and k_ in person:
            pser = person[k_]["series"]
            break
    t1, t2 = r1.get("time_key"), r2.get("time_key")
    tk = None
    if t1 == "none" or t2 == "none":
        row["time_status"] = "no_key" if t1 == "none" and t2 == "none" else "keys_differ"
    elif "yes" not in (rp1, rp2) or "no" in (rp1, rp2):
        row["time_status"] = "not_repeated_in_text"   # R2026.34: a sample-type variable never becomes a time axis
    elif t1 == t2:
        tk = t1
    elif t1 not in numeric and t2 not in numeric and _partition_agree(time_[t1], time_[t2]):
        tk = t1
        row["time_match"] = "partition"
    else:
        row["time_status"] = "keys_differ"
    origin = r1.get("time_origin") if r1.get("time_origin") == r2.get("time_origin") else "unknown"
    if tk and tk in numeric and not fixed and "person" not in prop and re.search(r"(?i)(^|[^a-z])age", tk.split(":", 1)[1]):
        row["time_status"] = "age_axis_without_person"   # R2026.34 audit: age is a time axis only for repeatedly sampled people
        tk = None
    if tk and tk in numeric:
        u1, u2 = r1.get("time_unit"), r2.get("time_unit")
        if u1 in UNIT_DAYS and u1 == u2:
            offs = (numeric[tk] * UNIT_DAYS[u1]).round(1)
            wi = _within(pser, offs) if pser is not None else None
            row.update(n_time_values=int(numeric[tk].nunique()), time_within=None if wi is None else round(wi, 3))
            if wi is not None and wi < 0.5:
                row["time_status"] = "numeric_not_within_person"
            else:
                row["time_status"] = "proposed_numeric"
                prop.update(time=tk, numeric_unit=u1, origin=origin, days_ok=True)
        else:
            row["time_status"] = "numeric_unit_differs"
    elif tk:
        s = time_[tk]
        m1 = _time_map(r1, s)
        m2 = _time_map(r2, time_[t2])
        if t2 != tk:
            tr = _translate(time_[t2], s)
            m2 = {tr[v]: x for v, x in m2.items() if v in tr}
        agreed, n_site = {}, 0
        for v in _vals(s):
            a_, b_ = m1.get(v), m2.get(v)
            if not a_ or not b_:
                continue
            la, lb = _clean_label(a_.get("label") or v), _clean_label(b_.get("label") or v)
            if (SITE_VALUE.search(f"{v} {la}") and not TIME_WORD.search(la)) or HANDLING.search(f"{la} {lb}"):
                n_site += 1
                continue
            d1, d2 = _num(a_.get("days")), _num(b_.get("days"))
            same = _labels_agree(la, lb)
            lab = (la if len(la) <= len(lb) else lb) if same else la
            if d1 is not None and d2 is not None and abs(d1 - d2) <= max(1.0, 0.05 * max(abs(d1), abs(d2))):
                agreed[v] = dict(label=lab, days=round(d1, 1))
            elif same:
                agreed[v] = dict(label=lab, days=None)
        offs = s.astype(str).map(lambda v: (agreed.get(v) or {}).get("days"))
        nd = len({x["days"] for x in agreed.values() if x["days"] is not None})
        if nd and origin == "unknown" and r1.get("time_origin") not in (None, "unknown"):
            origin = r1.get("time_origin")   # the day values agree; only the origin's name differs
        if not nd and s.astype(str).map(lambda v: bool(DATE_LIKE.search(v))).mean() >= 0.8:
            agreed = {}   # bare collection dates with no origin: the collection date already holds them
        wi = _within(pser, offs.dropna()) if pser is not None else None
        days_ok = nd >= 2 and (wi is None or wi >= 0.5)
        row.update(n_time_values=int(s.nunique()), n_time_agreed=len(agreed), n_time_days=sum(1 for x in agreed.values() if x["days"] is not None),
                   time_within=None if wi is None else round(wi, 3), n_time_site_dropped=n_site)
        if not agreed:
            row["time_status"] = "values_differ"
        else:
            row["time_status"] = "proposed" if days_ok else "labels_only"
            prop.update(time=tk, agreed=agreed, days_ok=days_ok, days_any=nd >= 2, origin=origin)
    return row, prop


def verify_prompt(tx, prop, person, time_, fixed, numeric=None):
    numeric = numeric or {}
    parts = [tx["prompt"][:24000], ""]
    pk = prop.get("person")
    tk = prop.get("time")
    tser = (numeric[tk] if tk in numeric else time_.get(tk)) if tk else None
    if pk:
        v = person[pk]
        st = v["stats"]
        parts.append(f"PERSON VARIABLE: {pk}{' (FIXED: the catalog subject id)' if fixed else ''}: {st['n_persons']} persons over {len(v['series'])} samples, "
                     f"samples per person median {st['per_median']:g}, max {st['per_max']}")
        ex = [f"  sample '{str(v['raw'].get(i))[:60]}' -> person '{v['series'][i]}'" + (f", time value '{tser.get(i)}'" if tser is not None else "")
              for i in list(v["series"].index[:8])]
        parts.append("EXAMPLE SAMPLES:\n" + "\n".join(ex))
    else:
        parts.append("PERSON VARIABLE: none proposed (answer person_ok not_applicable)")
    if tk and tk in numeric:
        num = numeric[tk]
        parts.append(f"NUMERIC TIME VARIABLE: {tk}: {num.nunique()} distinct numbers from {num.min():g} to {num.max():g}; proposed: the numbers are "
                     f"{prop['numeric_unit']} since {prop.get('origin')} (answer numeric_ok; items as an empty list)")
    elif tk:
        vc = time_[tk].astype(str).value_counts()
        parts.append(f"TIME VARIABLE: {tk}; time origin: {prop.get('origin')}\nPROPOSED TIME MAP (numbered):")
        for j, (val, g) in enumerate(prop["agreed"].items(), 1):
            parts.append(f"  {j}. value '{val}' ({int(vc.get(val, 0))} samples) -> label '{g['label']}', days {g['days'] if g['days'] is not None else 'not given'}")
    else:
        parts.append("TIME VARIABLE: none proposed (answer items as an empty list, numeric_ok not_applicable)")
    return "\n".join(parts)


def _det(acc, sk, field, raw, val, conf, src, loc, quote, note):
    return dict(sample_key=sk, field_name=field, study_accession=acc, field_value=str(raw)[:200], value_normalized=str(val)[:200], confidence=conf,
                evidence_source=src, evidence_locator=loc, evidence_quote=str(quote)[:300], evidence_limited_to_abstract=0.0,
                determined_by=f"{PASS}:{MODEL_TAG}x2+verify", route="R1", scope="sample", parse_note=note)


MODEL_TAG = "rubric"


def _vacuous(label, raw):
    """a label that only restates the raw value ('1' -> 'timepoint 1', 'T1' -> 'visit 1', 'I' -> 'phase I', a date -> 'sampling
    <date>'); a label that adds a unit or a meaning ('D7' -> 'day 7', 'A' -> 'before ablation') is not vacuous"""
    L = [t for t in _norm(label).split() if t not in GENERIC_TIME and t not in {"sampling", "sample", "on", "at", "of", "the"}]
    Rw = [t for t in re.findall(r"[a-z]+|\d+", _norm(raw)) if t not in GENERIC_TIME]
    if not L:
        return True
    if L == Rw or "".join(L) == "".join(Rw):
        return True
    if DATE_LIKE.search(str(raw)) and DATE_LIKE.search(str(label)):
        return True
    return False


def _missing(v):
    return v is None or (not isinstance(v, str) and pd.isna(v)) or str(v).strip() == ""


def finalise(acc, tx, row, prop, ver, person, time_, traw, existing_subj, existing_tp, fixed, numeric=None):
    numeric = numeric or {}
    drows, trows = [], []
    if not isinstance(ver, dict):
        row["verify"] = "missing"
        return row, drows, trows
    pok = ver.get("person_ok")
    row["verify_person"] = pok
    pk = prop.get("person")
    pser = person[pk]["series"] if pk else None
    if pk and not fixed and pok == "yes":
        p, raw = person[pk]["series"], person[pk]["raw"]
        uniq_existing = existing_subj.dropna()
        one_per = bool(len(uniq_existing)) and uniq_existing.nunique() == len(uniq_existing)
        src_key = pk.split(":")[1] if pk.startswith("split:") else pk
        src = f"sample.name_rule:{src_key}" if pk.startswith("split:") else f"sample.attr.{pk}"
        n_w = 0
        for sk, val in p.items():
            has = not _missing(existing_subj.get(sk))
            if has and not one_per:
                continue
            conf = 0.92 if has else 0.8
            drows.append(_det(acc, sk, "subject_id", val, val, conf, src, pk, f"{src_key} = {raw.get(sk)}",
                              f"person pass: '{pk}' identifies the person; two readings of the {tx['mode']} text agreed, an Opus check confirmed"
                              + ("; replaces one-per-sample ids" if conf > 0.9 else "")))
            n_w += 1
        row["subject_rows"] = n_w
        row["person_written"] = n_w > 0
    else:
        row["person_written"] = False
        if pk and not fixed:
            pser = None   # a refused person variable is not used for the time checks
    tk = prop.get("time")
    if not tk:
        return row, drows, trows
    n_lab = n_off = n_dec = n_vac = 0
    if tk in numeric:
        nok = ver.get("numeric_ok")
        row["verify_numeric"] = nok
        if nok == "yes":
            u = prop["numeric_unit"]
            src = f"sample.attr.{tk.split(':', 1)[1]}"
            for sk, x in numeric[tk].items():
                d = round(float(x) * UNIT_DAYS[u], 1)
                trows.append(dict(sample_key=sk, study_accession=acc, code=f"{x:g}", code_source=tk, offset_days=d, time_origin=prop.get("origin") or "unknown",
                                  evidence_source=tx["mode"], evidence_locator=src, evidence_quote=str(row.get("repeated_quote") or "")[:300], confidence=0.8,
                                  determined_by=f"{PASS}:{MODEL_TAG}x2+verify"))
                n_off += 1
                if _missing(existing_tp.get(sk)):
                    drows.append(_det(acc, sk, "timepoint_label", x, f"{UNIT_WORD[u]} {x:g}", 0.8, src, tk, f"{tk.split(':', 1)[1]} = {x:g}",
                                      f"time pass: '{tk}' holds {u} since {prop.get('origin')}; two readings agreed, an Opus check confirmed"))
                    n_lab += 1
        row.update(time_label_rows=n_lab, time_offset_rows=n_off)
        return row, drows, trows
    items = ver.get("items") if isinstance(ver.get("items"), list) else []
    ok = {}
    vals = list(prop["agreed"].items())
    for it in items:
        if not isinstance(it, dict):
            continue
        j = it.get("index")
        if not isinstance(j, int) or not (1 <= j <= len(vals)):
            continue
        val, g = vals[j - 1]
        lab_ok = it.get("label_ok") == "yes"
        ok[val] = dict(label=g["label"] if lab_ok else None,
                       days=g["days"] if (lab_ok and it.get("days_ok") == "yes" and g["days"] is not None) else None)
    s = time_[tk]
    # offsets only when >= 2 distinct confirmed days and (with persons) they vary within >= 30 % of the repeated persons
    conf_days = s.astype(str).map(lambda v: (ok.get(v) or {}).get("days")).dropna()
    off_ok = (prop.get("days_ok") if pser is not None else prop.get("days_any")) and conf_days.nunique() >= 2 and \
        (pser is None or _within(pser, conf_days) >= 0.3)
    src_key = tk.split(":")[1] if tk.startswith("split:") else tk
    is_cat = tk.startswith("catalog:")
    src = f"sample.name_rule:{src_key}" if tk.startswith("split:") else "catalog.timepoint_label" if is_cat else f"sample.attr.{tk}"
    raw = traw.get(tk, s)
    for sk, val in s.astype(str).items():
        g = ok.get(val)
        if not g:
            continue
        if g["label"] and _vacuous(g["label"], val):
            n_vac += 1
        elif g["label"]:
            if not is_cat and _missing(existing_tp.get(sk)):
                drows.append(_det(acc, sk, "timepoint_label", val, g["label"], 0.8, src, tk, f"{src_key} = {raw.get(sk, val)}",
                                  f"time pass: '{tk}' value '{val}' = {g['label']}; two readings agreed, an Opus check confirmed"))
                n_lab += 1
            elif is_cat and BARE_CODE.match(val) and _norm(g["label"]) != _norm(val):
                # R2026.34: a confirmed decoding of a bare stored code ('A' -> 'before ablation') replaces the code
                drows.append(_det(acc, sk, "timepoint_label", val, g["label"], 0.91, src, tk, f"timepoint_label = {val}",
                                  f"time pass: the stored code '{val}' means {g['label']}; two readings agreed, an Opus check confirmed (decodes the code)"))
                n_dec += 1
        if g["days"] is not None and off_ok:
            trows.append(dict(sample_key=sk, study_accession=acc, code=str(val)[:80], code_source=tk, offset_days=float(g["days"]),
                              time_origin=prop.get("origin") or "unknown", evidence_source=tx["mode"], evidence_locator=src,
                              evidence_quote=str(row.get("repeated_quote") or "")[:300], confidence=0.8,
                              determined_by=f"{PASS}:{MODEL_TAG}x2+verify"))
            n_off += 1
    row.update(time_label_rows=n_lab, time_decoded_rows=n_dec, time_offset_rows=n_off, time_vacuous_values=n_vac, time_values_confirmed=sum(1 for g in ok.values() if g["label"]),
               time_days_confirmed=sum(1 for g in ok.values() if g["days"] is not None))
    return row, drows, trows


# --------------------------------------------------------------------------------------------------------------------- run
def make_targets(path):
    st = pd.read_parquet(os.path.join(PKG, "catalog_studies.parquet"))
    st = st[st.release_retired.isna()] if "release_retired" in st.columns else st
    t = st[(st.longitudinal_status == "repeated_unresolved") | ((st.longitudinal_status == "longitudinal") & (st.longitudinal_resolved == False))]  # noqa: E712
    out = pd.DataFrame({"study_accession": t.study_accession, "mode": np.where(t.longitudinal_status == "repeated_unresolved", "person", "time")})
    out.to_csv(path, index=False)
    print(json.dumps({"targets": len(out), "by_mode": out["mode"].value_counts().to_dict()}))


def run(targets, out, budget, shard, host=None, verify_budget=None):
    if host is not None:
        M.set_host(host)
    for sub in ("design/logs", "deep", "timing"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    projects = list(targets)
    st = pd.read_parquet(os.path.join(PKG, "catalog_studies.parquet"))
    st = st[st.release_retired.isna()] if "release_retired" in st.columns else st
    st = st.set_index("study_accession")
    papers = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "catalog", "study_papers.parquet"))
    papers = papers[papers.relation == "own_data"]
    w = pd.read_parquet(os.path.join(PKG, "catalog_sample_metadata_wide.parquet"),
                        columns=["study_accession", "sample_key", "sample_type", "subject_id", "timepoint_label", "sex", "release_retired"],
                        filters=[("study_accession", "in", projects)])
    w = w[w.release_retired.isna() & (w.sample_type.fillna("specimen") == "specimen")]
    gen = pd.read_parquet(os.path.join(PKG, "catalog_sample_determinations.parquet"), columns=["sample_key", "field_name", "determined_by"],
                          filters=[("study_accession", "in", projects), ("field_name", "==", "subject_id")])
    gen_keys = set(gen.loc[gen.determined_by.astype(str).str.contains("subject_unique|subject_single"), "sample_key"])
    W = {k: g.set_index("sample_key") for k, g in w.groupby("study_accession")}
    attrs = pd.read_parquet(ATTR, columns=["sample_acc", "study_accession", "attr_key", "attr_value"], filters=[("study_accession", "in", projects)])
    attrs_by = {k: g for k, g in attrs.groupby("study_accession")}
    cache = os.path.expanduser("~/catalog/cache/llm/persontime_r2026_34")
    llm = LLM("rubric", cache_dir=cache, ledger=os.path.join(out, "design", "logs", f"ledger_PT{shard}.csv"), budget_usd=budget,
              pass_name="persontime_r2026_34", max_concurrency=4)
    ver = LLM("adjudicate", cache_dir=cache + "_verify", ledger=os.path.join(out, "design", "logs", f"ledger_verify_PT{shard}.csv"),
              budget_usd=verify_budget if verify_budget is not None else budget, pass_name="persontime_verify_r2026_34", max_concurrency=4)
    global MODEL_TAG
    MODEL_TAG = llm.model
    T, PC, TC, TR, AL, NU, FX, N, P, reqs, keys = {}, {}, {}, {}, {}, {}, {}, {}, {}, [], []
    for acc, mode in targets.items():
        if acc not in st.index or acc not in W:
            continue
        x = W[acc]
        spec = list(x.index)
        subj = x.subject_id.astype("string")
        subj = subj.where(~x.index.isin(gen_keys))
        fixed = mode == "time"
        person, time_, traw, alias, numeric = candidates(attrs_by.get(acc, attrs.iloc[0:0]), spec, fixed_person=subj if fixed else None,
                                                         tp_labels=x.timepoint_label)
        d = CP.load_dossier(acc)
        tx = CP.project_texts(acc, d, st.loc[acc].to_dict(), papers)
        T[acc], PC[acc], TC[acc], TR[acc], AL[acc], NU[acc], FX[acc], N[acc] = tx, person, time_, traw, alias, numeric, fixed, len(spec)
        if not person or (fixed and not time_):
            continue
        P[acc] = tx["prompt"] + f"\n\nSPECIMENS DEPOSITED IN THIS PROJECT: {len(spec)}\n\n" + prompt_block(person, time_, len(spec), fixed, traw, numeric)
        for rep in (1, 2):
            reqs.append({"system": SYSTEM + ("" if rep == 1 else "\n(second, independent reading)"), "prompt": P[acc], "tools": [TOOL],
                         "tool_choice": {"type": "tool", "name": "record"}, "max_tokens": 3000, "thinking": NOTHINK})
            keys.append((acc, rep))
    status = {}
    res = DP._ask(llm, reqs, status)
    R = {}
    for (acc, rep), r in zip(keys, res):
        tu = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        R.setdefault(acc, [None, None])[rep - 1] = valid(tu, PC[acc], TC[acc], AL[acc])
    # one re-ask for a missing reading or one that named an unlisted key (the unlisted key is already coerced to 'none')
    retry = [(acc, rep) for acc, x in R.items() for rep in (1, 2) if x[rep - 1] is None or x[rep - 1].get("_invalid")]
    rq = [{"system": SYSTEM + ("" if rep == 1 else "\n(second, independent reading)") + "\n(retry: give person_key and time_key EXACTLY as listed, or 'none')",
           "prompt": P[acc], "tools": [TOOL], "tool_choice": {"type": "tool", "name": "record"}, "max_tokens": 3000, "thinking": NOTHINK} for acc, rep in retry]
    for (acc, rep), r in zip(retry, DP._ask(llm, rq, status)):
        tu = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        v_ = valid(tu, PC[acc], TC[acc], AL[acc])
        if v_ is not None and (R[acc][rep - 1] is None or v_["_invalid"] <= R[acc][rep - 1]["_invalid"]):
            R[acc][rep - 1] = v_
    # R2026.34 pilot 2: a third reading, shown only the two disputed variables, when the readings chose different ones
    DISP, treq, tkeys = {}, [], []
    for acc, reps in R.items():
        if not all(reps):
            continue
        r1_, r2_ = reps
        dp = {}
        k1_, k2_ = r1_["person_key"], r2_["person_key"]
        if not FX[acc] and "none" not in (k1_, k2_) and k1_ != k2_ and not _partition_agree(PC[acc][k1_]["series"], PC[acc][k2_]["series"]):
            dp["person"] = (k1_, k2_)
        t1_, t2_ = r1_["time_key"], r2_["time_key"]
        if "none" not in (t1_, t2_) and t1_ != t2_ and not (t1_ not in NU[acc] and t2_ not in NU[acc] and _partition_agree(TC[acc][t1_], TC[acc][t2_])):
            dp["time"] = (t1_, t2_)
        if not dp:
            continue
        DISP[acc] = dp
        pr_ = {k_: PC[acc][k_] for k_ in dp["person"]} if "person" in dp else PC[acc]
        tr_ = {k_: TC[acc][k_] for k_ in dp["time"]} if "time" in dp else TC[acc]
        treq.append({"system": SYSTEM + "\n(third, independent reading: two earlier readings chose different variables; only those are listed where they differed)",
                     "prompt": T[acc]["prompt"] + f"\n\nSPECIMENS DEPOSITED IN THIS PROJECT: {N[acc]}\n\n" + prompt_block(pr_, tr_, N[acc], FX[acc], TR[acc], NU[acc]),
                     "tools": [TOOL], "tool_choice": {"type": "tool", "name": "record"}, "max_tokens": 3000, "thinking": NOTHINK})
        tkeys.append(acc)
    R3 = {}
    for acc, r in zip(tkeys, DP._ask(llm, treq, status)):
        tu = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        v_ = valid(tu, PC[acc], TC[acc], AL[acc])
        if v_ is not None:
            R3[acc] = v_
    ROWS, PROPS = {}, {}
    for acc in T:
        base = dict(study_accession=acc, mode="time" if FX[acc] else "person", n_specimens=N[acc], n_person_cand=len(PC[acc]), n_time_cand=len(TC[acc]),
                    text_mode=T[acc]["mode"])
        if acc not in P:
            ROWS[acc] = dict(base, status="no_candidates")
            continue
        reps = R.get(acc, [None, None])
        if sum(1 for x in reps if x) < 2:
            ROWS[acc] = dict(base, status="no_reading" if not any(reps) else "one_reading")
            continue
        third = None
        if acc in DISP:
            third = "third_reading_missing"
            if acc in R3:
                r3_, partner = R3[acc], None
                for kind_, kk in (("person", "person_key"), ("time", "time_key")):
                    if kind_ in DISP[acc]:
                        partner = reps[0] if r3_[kk] == reps[0][kk] else reps[1] if r3_[kk] == reps[1][kk] else None
                        break
                if partner is not None:
                    reps = [partner, r3_]
                    third = "third_reading_majority"
                else:
                    third = "third_reading_none"
        sex = W[acc].sex if "sex" in W[acc].columns else pd.Series(dtype=object)
        row, prop = merge_project(acc, reps, PC[acc], TC[acc], N[acc], sex, FX[acc], NU[acc])
        row.update(text_mode=T[acc]["mode"], status="read", third_reading=third)
        ROWS[acc] = row
        if (prop.get("person") and not FX[acc]) or prop.get("time"):
            PROPS[acc] = prop
    vreqs, vkeys = [], []
    for acc, prop in PROPS.items():
        vreqs.append({"system": VERIFY_SYSTEM, "prompt": verify_prompt(T[acc], prop, PC[acc], TC[acc], FX[acc], NU[acc]), "tools": [VERIFY_TOOL],
                      "tool_choice": {"type": "tool", "name": "verify"}, "max_tokens": 2500, "thinking": NOTHINK})
        vkeys.append(acc)
    vstatus = {}
    DROWS, TROWS = [], []
    for acc, r in zip(vkeys, DP._ask(ver, vreqs, vstatus)):
        tu = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        x = W[acc]
        row, drows, trows = finalise(acc, T[acc], ROWS[acc], PROPS[acc], tu, PC[acc], TC[acc], TR[acc], x.subject_id.where(~x.index.isin(gen_keys)),
                                     x.timepoint_label, FX[acc], NU[acc])
        ROWS[acc] = row
        DROWS += drows
        TROWS += trows
    k = shard
    df = pd.DataFrame(list(ROWS.values()))
    for c_ in df.columns:
        if df[c_].dtype == object and df[c_].map(lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)).any() and df[c_].map(lambda v: isinstance(v, str)).any():
            df[c_] = df[c_].map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v))
    df.to_parquet(os.path.join(out, "design", f"persontime_reading_shard_PT{k}.parquet"), index=False)
    pd.DataFrame(DROWS, columns=DP.DET_COLS).to_parquet(os.path.join(out, "deep", f"deep_determinations_shard_PTD{k}.parquet"), index=False)
    tcols = ["sample_key", "study_accession", "code", "code_source", "offset_days", "time_origin", "evidence_source", "evidence_locator",
             "evidence_quote", "confidence", "determined_by"]
    pd.DataFrame(TROWS, columns=tcols).to_parquet(os.path.join(out, "timing", f"timing_samples_pt_shard_{k}.parquet"), index=False)
    g = lambda c: df[c].value_counts().to_dict() if c in df.columns else {}
    summ = dict(shard=k, projects=len(T), with_candidates=len(P), llm_status=status, verify_status=vstatus, retried_readings=len(retry),
                third_readings=g("third_reading"),
                status=g("status"), person_status=g("person_status"), time_status=g("time_status"), verify_person=g("verify_person"),
                proposals=len(PROPS), projects_person_written=int(df.get("person_written", pd.Series(dtype=bool)).fillna(False).astype(bool).sum()),
                subject_rows=int(sum(1 for d_ in DROWS if d_["field_name"] == "subject_id")),
                subject_rows_override=int(sum(1 for d_ in DROWS if d_["field_name"] == "subject_id" and d_["confidence"] > 0.9)),
                timepoint_label_rows=int(sum(1 for d_ in DROWS if d_["field_name"] == "timepoint_label")), offset_rows=len(TROWS),
                timepoint_label_decoded=int(sum(1 for d_ in DROWS if d_["field_name"] == "timepoint_label" and d_["confidence"] > 0.9)),
                projects_numeric_time=int(sum(1 for p_ in PROPS.values() if p_.get("time", "").startswith("numeric:"))),
                projects_with_offsets=len({t_["study_accession"] for t_ in TROWS}), cost=llm.summary(), cost_verify=ver.summary())
    json.dump(summ, open(os.path.join(out, "design", "logs", f"persontime_summary_PT{k}.json"), "w"), indent=1, default=str)
    print(json.dumps(summ, default=str)[:1800])
    return summ


def merge(out):
    d = os.path.join(out, "design")
    fs = [f for f in sorted(glob.glob(os.path.join(d, "persontime_reading_shard_PT*.parquet"))) if "pilot" not in os.path.basename(f)]
    parts = [pd.read_parquet(f) for f in fs]
    df = pd.concat([p for p in parts if len(p)], ignore_index=True) if parts else pd.DataFrame()
    if len(df):
        df = df.drop_duplicates("study_accession", keep="last")
    df.to_parquet(os.path.join(d, "persontime_reading_all.parquet"), index=False)
    print("persontime_reading", len(fs), len(df))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", help="CSV with study_accession, mode (person | time)")
    ap.add_argument("--make-targets")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--budget", type=float, default=5.0)
    ap.add_argument("--verify-budget", type=float, default=None)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "inputs", "gut"))
    ap.add_argument("--merge", action="store_true")
    a = ap.parse_args(argv)
    if a.make_targets:
        return make_targets(a.make_targets)
    if a.merge:
        return merge(a.out)
    t = pd.read_csv(a.targets)
    t = t.sort_values("study_accession")
    t = t.iloc[[i for i in range(len(t)) if i % a.nshards == a.shard]]
    if a.limit:
        t = t.iloc[:a.limit]
    return run(dict(zip(t.study_accession, t["mode"])), a.out, a.budget, a.shard if not a.limit else f"pilot{a.shard}", host=globals().get("host"),
               verify_budget=a.verify_budget)


if __name__ == "__main__":
    main()
