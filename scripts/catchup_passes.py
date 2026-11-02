#!/usr/bin/env python
"""catchup_passes.py — the per-project reading passes for projects admitted after those passes ran (R2026.32).

The catalog's study-level passes (R3 full-text cohort statements, R4 abstract / description statements, the interventions
pass, the study-design pass and the generated-subject rule) ran on the projects of R2026.29 and earlier. Projects admitted
by the scope classification in R2026.30 / R2026.31 got the large-update passes (archive attributes, supplementary joins,
methods facts, descriptions) but not these. This script runs them together, in ONE reading per project, read twice
independently (model role `rubric`), of the best text available:
    full text   the JATS of the paper judged to have generated the data (title, abstract, data availability, methods)
    abstracts   else the abstracts of up to 3 such papers (Europe PMC)
    archive     always: the archive title and description and a profile of the sample attributes
Each reading returns statements (cohort facts for the pack fields, with a verbatim quote and its section), the
interventions the study administered, the study design (longitudinal schedule, relatedness) and a one-sample-per-subject
verdict.

Merging (deterministic):
  statement    committed when BOTH readings give the same field and normalised value for the whole cohort and at least one
               quote is a verbatim substring of the text read (whitespace / case / dash folding only). Route R3 when the
               quote is in the paper's full text, R4 when it is in an abstract or the archive record. Confidence: R3
               methods 0.7, full-text abstract 0.65, data availability 0.6; R4 abstract 0.5, archive description 0.45,
               archive title 0.4. Subgroup statements and single-reading statements are kept in side tables, never applied.
  interventions codes given by both readings; the others are kept as interventions_unreplicated.
  design       longitudinal yes/no when both agree (else unclear); relatedness when both agree (else none).
  subjects     subject_id = sample_key for the samples without one, only when both readings say one sample per subject,
               the design is not longitudinal and the project has at most one timepoint label (gut_subject_unique_v1).

Outputs under --out (data/inputs/gut), one file set per shard k:
  r3c/shards/gut_r3c_{determinations,study_summary,subgroups,unreplicated}_shard_{k}.parquet   (merged by --merge)
  r4c/gut_r4c_determinations_shard_{k}.parquet, r4c/gut_r4c_study_summary_shard_{k}.parquet
  interventions/gut_intervention_studies_shard_C{k}.parquet
  design/study_design_shard_C{k}.parquet, design/logs/subject_log_shard_C{k}.parquet
  deep/deep_determinations_shard_SUBJC{k}.parquet
Prints counts only (no project content).

usage (inside a kernel that has `host`, via runpy with init_globals={"host": host}):
  python scripts/catchup_passes.py --projects LIST --shard K --nshards N --budget USD [--out data/inputs/gut]
  python scripts/catchup_passes.py --merge [--out data/inputs/gut]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import unicodedata

import pandas as pd
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)
import pilot_passes as PP  # noqa: E402  (dossier_text, paper_sections, abstract_for, jats_file, tool)
from catalog import models as M  # noqa: E402
from catalog.llm_backend import LLM, BudgetExceeded  # noqa: E402

M.set_host(globals().get("host"))
CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))
LU = os.path.join(CACHE, "large_update")
DOSSIER_DIRS = [os.path.join(LU, d) for d in ("dossiers_all", "dossiers_delta", "dossiers_relisted", "dossiers_gut")] + \
    sorted(glob.glob(os.path.join(LU, "dossiers_delta?*")))   # R2026.34: one dossier directory per monthly intake
PKG = os.environ.get("CATALOG_PKG") or os.path.join(ROOT, "build", "package")   # R2026.34: CATALOG_PKG = an intermediate build
PASS = "catalog_catchup_v1"
NOTE = "catch-up pass R2026.32 (two readings)"
NOTHINK = {"type": "disabled"}
MODEL_TAG = "rubric"
DET_COLS = ["sample_key", "field_name", "study_accession", "field_value", "value_normalized", "confidence", "evidence_source",
            "evidence_locator", "evidence_quote", "evidence_limited_to_abstract", "determined_by", "route", "scope", "parse_note",
            "group_audit", "src_track", "release_added", "release_retired", "package_added"]


def codes(name):
    d = yaml.safe_load(open(os.path.join(ROOT, "config", "vocab", f"{name}.yaml"), encoding="utf-8")) or {}
    c = d.get("codes", d)
    return {k: ((v or {}).get("label") if isinstance(v, dict) else None) or k.replace("_", " ") for k, v in c.items()}


HC, LS, DI, SM, MED, IV = (codes(n) for n in ("health_conditions", "lifestyle", "diet", "smoking", "medication", "interventions"))
for _d in (HC, LS, DI, SM, MED):
    _d.pop("unknown", None)
IV_STUDY = {k: v for k, v in IV.items() if k not in ("placebo", "no_intervention")}
STAGES = {"neonate": "first 28 days of life", "infant": "29 days to about 3 years", "child": "3 to 12 years",
          "adolescent": "12 to 18 years", "adult": "18 to 65 years", "elderly": "65 years and older"}
FIELDS = ["life_stage", "age_at_collection_days", "sex", "country", "health_condition", "antibiotic_exposure", "collection_date",
          "location_region", "location_locality", "location_site", "lifestyle", "diet", "smoking_status", "medication"]
DETAIL = {"health_condition": "health_condition_detail", "lifestyle": "lifestyle_detail", "diet": "diet_detail",
          "medication": "medication_detail"}
MAXLEN = {"location_region": 80, "location_locality": 80, "location_site": 120, "health_condition_detail": 120,
          "lifestyle_detail": 120, "diet_detail": 120, "medication_detail": 160}
SOURCES = ["paper_abstract", "paper_methods", "paper_data_availability", "archive_title", "archive_description"]
IV_DESIGNS = ["none", "randomized_controlled_trial", "non_randomized_controlled", "crossover", "single_arm_before_after",
              "observational_with_procedure", "unknown"]
RELATED = ["none", "mother_infant", "twins", "families", "siblings", "households", "fmt_donor_recipient", "community",
           "hospital_ward", "workplace", "other"]
TIME_ORIGIN = ["birth", "enrolment", "baseline_visit", "intervention_start", "diagnosis", "surgery_or_transplant", "delivery",
               "other", "not_stated"]
NS = {"type": ["string", "null"]}
NI = {"type": ["integer", "null"]}

TOOL = PP.tool("record", {
    "statements": {"type": "array", "items": {"type": "object", "properties": {
        "field": {"type": "string", "enum": FIELDS},
        "value": {"type": "string", "description": "code or value in the field's format (see rules)"},
        "detail": NS,
        "applies_to": {"type": "string", "enum": ["all", "subgroup"]},
        "subgroup": NS,
        "quote": {"type": "string", "description": "verbatim words from the text (at most 25), copied exactly"},
        "source": {"type": "string", "enum": SOURCES}},
        "required": ["field", "value", "applies_to", "quote", "source"]}},
    "interventions": {"type": "object", "properties": {
        "codes": {"type": "array", "items": {"type": "string", "enum": list(IV_STUDY)}},
        "design": {"type": "string", "enum": IV_DESIGNS},
        "detail": NS, "population_condition": NS, "quote": NS, "source": {"type": ["string", "null"], "enum": SOURCES + [None]}},
        "required": ["codes", "design"]},
    "design": {"type": "object", "properties": {
        "longitudinal": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "sampling_schedule": NS,
        "planned_offsets_days": {"type": "array", "items": {"type": "number"}},
        "time_origin": {"type": "string", "enum": TIME_ORIGIN},
        "followup_days_stated": {"type": ["number", "null"]},
        "relatedness": {"type": "string", "enum": RELATED},
        "relatedness_summary": NS, "n_families": NI,
        "quote": NS, "source": {"type": ["string", "null"], "enum": SOURCES + [None]}},
        "required": ["longitudinal", "relatedness"]},
    "subjects": {"type": "object", "properties": {
        "samples_per_subject": {"type": "string", "enum": ["one_sample_per_subject", "several_samples_per_subject", "unclear"]},
        "stated_participants": NI,
        "quote": NS, "source": {"type": ["string", "null"], "enum": SOURCES + [None]}},
        "required": ["samples_per_subject"]},
}, ["statements", "interventions", "design", "subjects"])


def _voc(d):
    return "; ".join(f"{k} ({v})" if v and v != k.replace("_", " ") else k for k, v in d.items())


SYSTEM = f"""You read the record of one human microbiome sequencing project and record study-level facts for a metadata catalog:
the archive's title and description, a profile of the sample attributes, and the text of the paper that generated the data
(its methods, or only its abstract). Use ONLY the text given; never add facts from general knowledge. If the text does not
state something, leave it out (or give 'unclear' / 'none' / null where the schema asks for a value). Always return all four
parts (statements, interventions, design, subjects) in every reply, using [] / 'unclear' / 'none' / null when the text says
nothing — never omit a part.

The profile of the sample attributes is background only: it is a summary of the archive's per-sample fields (already read by
another pass) and it is NOT quotable text. Every quote must be copied from the paper text or from the archive title or
description; never quote or cite the attribute profile.

STATEMENTS — facts about the sampled people of THIS project.
- applies_to = all only when the fact is true of EVERY sequenced participant (e.g. 'healthy adult volunteers', 'all women',
  'recruited in Kenya', 'samples collected between 2019 and 2021'). An inclusion or exclusion criterion that fixes a value for
  everyone counts (e.g. 'no antibiotics in the 3 months before sampling' -> antibiotic_exposure = no).
- applies_to = subgroup when it is true of only part of the cohort (cases or controls, one arm, a percentage); name the
  subgroup in a few words. A mixture ('45 % female', 'patients and healthy controls') is never a statement for all.
- Facts about a different cohort (data re-used from elsewhere, a validation cohort not deposited here, animals, cell lines)
  are not statements.
- quote: copy the words exactly as they appear (at most 25 words, no ellipses, no paraphrase); source: the section they come
  from — paper_abstract, paper_methods, paper_data_availability, archive_title, archive_description. The quote must itself
  state the fact: a background sentence that only mentions the topic ('amniotic fluid is a complex environment') is not
  evidence for a value, and a hedged or partial statement ('to the best of our knowledge', 'before therapy for the main
  diagnosis') does not establish one. If no quote states the fact, leave the field out.
Field formats:
- life_stage: one of {', '.join(f'{k} ({v})' for k, v in STAGES.items())} — only when every participant falls in it. A
  cohort label ('adult patients', 'children') is not enough when the text also gives ages or age brackets that cross the
  band: check them and leave the field out when they do (e.g. 'adult patients' whose stated ages reach 70 is not adult).
- age_at_collection_days: a single age shared by every participant at sampling, in days (e.g. 'at birth' = 0, '6-week-old
  infants' = 42). Never for a range.
- sex: female or male — only when every participant has that sex.
- country: ISO 3166-1 alpha-2 code of the country where the participants were recruited or sampled (never the laboratory's).
  Only when there is a single country.
- health_condition: one code — {_voc(HC)}. 'healthy_control' only when every participant is described as healthy. Choose
  the code for the condition the text names, not the one nearest to gut microbiome work: gi_infection_or_diarrhoea is only
  for gastrointestinal infection or diarrhoea, and an infection of another organ (urinary, respiratory, skin, bloodstream)
  is other_infection. Put the text's own wording in detail.
- antibiotic_exposure: yes or no (recent or current antibiotic use, for everyone). 'no' only when the text says the
  participants had no antibiotics; being untreated for the study's main diagnosis, or being sampled before one particular
  therapy, does not mean no antibiotics.
- collection_date: YYYY, YYYY-MM or YYYY-MM-DD, or an interval START/END in those forms (sampling dates, not publication).
- location_region / location_locality / location_site: the single named place below the country where every participant
  was sampled (region or state / city, town or district / hospital, village or site), as written. A geographic place only —
  a body site or anatomical location (nasopharynx, gut, skin, lung, airway) is never a location.
- lifestyle: {_voc(LS)}; detail = the text's wording.
- diet: {_voc(DI)}; detail = the text's wording.
- smoking_status: {_voc(SM)}.
- medication: one or more codes joined with ';' — {_voc(MED)}; a medication every participant takes; detail = wording.

INTERVENTIONS — what the study itself administered to participants: codes from {_voc(IV_STUDY)}; [] when nothing was
administered (observational studies, routine clinical care that the study only observed). Background about how the disease
is normally treated, or about a treatment or vaccination programme the study did not run, is NOT evidence that this study
administered anything: give codes only when the text says these participants were given the agent as part of the study.
design: {', '.join(IV_DESIGNS)}.
detail: the agents / doses / durations in a few words. population_condition: the condition of the treated population in a
few words. quote + source for the strongest statement.

DESIGN — longitudinal = yes when the same people were sampled at more than one time point, no when each person was
sampled once, unclear otherwise. sampling_schedule: the stated schedule in a few words; planned_offsets_days: the planned
sampling times in days from time_origin (e.g. [0, 7, 30]); followup_days_stated: total follow-up in days. relatedness:
whether the sampled people include related or co-living groups — mother_infant, twins, families, siblings, households,
fmt_donor_recipient, community, hospital_ward, workplace, other — or none; n_families when stated. quote + source.

SUBJECTS — samples_per_subject: one_sample_per_subject when the text makes clear each participant contributed exactly one
sequenced sample (e.g. 'a single stool sample from each of 120 participants', or the stated number of participants equals
the number of samples and nothing suggests repeated sampling); several_samples_per_subject when people were sampled
repeatedly or at several body sites; unclear otherwise. A count of samples, specimens, swabs, extracts, libraries or
sequencing runs is NOT a count of participants ('DNA was extracted from 62 samples' says nothing about how many people
were sampled): the quote must speak about people, and when it does not, answer unclear. stated_participants: the number of
participants stated. quote + source."""


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = re.sub(r"[\u2018\u2019\u201a\u201b\u2032]", "'", s)
    s = re.sub(r"[\u201c\u201d\u201e\u2033]", '"', s)
    return re.sub(r"\s+", " ", s).strip()


def quote_ok(q, text):
    q = norm(q).strip(" .,;:\"'")
    return len(q.split()) >= 3 and q in text


def n_catalog_samples(st):
    """the study row's sample count; for projects the registry tables do not cover yet, the build's curated count (R2026.34)"""
    for c in ("n_samples", "n_samples_curated"):
        v = st.get(c)
        if v is not None and pd.notna(v):
            return int(v)
    return 0


def load_dossier(acc):
    for d in DOSSIER_DIRS:
        f = os.path.join(d, f"{acc}.json")
        if os.path.exists(f):
            return json.load(open(f))
    return None


_UNI = {}


def uni_desc():
    if not _UNI:
        f = os.path.join(ROOT, "data", "inputs", "registry", "registry_universe_studies.parquet")
        if os.path.exists(f):
            u = pd.read_parquet(f, columns=["study_accession", "description"]).dropna().drop_duplicates("study_accession")
            _UNI.update(zip(u.study_accession, u.description))
        _UNI.setdefault("__loaded__", "")
    return _UNI


def project_texts(acc, d, st, papers):
    """-> dict(mode, prompt, segs={source: normalised text}, pmcid, pmids, n_abstracts, abstract_available, fulltext_available)."""
    title = str(st.get("study_title") or (d or {}).get("archive", {}).get("study_title") or "")
    cands = [str(st.get("description") or ""), str((d or {}).get("archive", {}).get("description_short") or ""), str(uni_desc().get(acc) or "")]
    desc = max(cands, key=len)[:4000]   # the archive's full description (the study tables cut it at 300 characters)
    segs = {"archive_title": norm(title), "archive_description": norm(desc)}
    prof = ""
    if d:
        prof = "\n".join(f"  {p['key']} [{p['n_samples']} samples, {p['n_distinct']} distinct]: " +
                         "; ".join(f"{k} x{v}" for k, v in list(p["top_values"].items())[:4]) for p in (d.get("attribute_profile") or [])[:30])
    own = papers[papers.study_accession == acc]
    ft_paper = next((r for r in own.itertuples() if isinstance(r.pmcid, str) and PP.jats_file(r.pmcid)), None)
    out = dict(pmcid=None, pmids=[], n_abstracts=0, abstract_available=False, fulltext_available=False)
    head = (f"PROJECT {acc}\nARCHIVE TITLE: {title}\nARCHIVE DESCRIPTION: {desc}\n"
            f"SAMPLES IN THE CATALOG: {n_catalog_samples(st)}\nSAMPLE ATTRIBUTES (key [samples, distinct values]: top values):\n{prof}\n")
    if ft_paper is not None:
        body = PP.paper_sections(ft_paper.pmcid, max_chars=30000)
        if body:
            m = re.search(r"TITLE: (.*?)\nABSTRACT: (.*?)\nDATA AVAILABILITY: (.*?)\nMETHODS: (.*)", body, re.S)
            if m:
                segs.update(paper_abstract=norm(m.group(2)), paper_data_availability=norm(m.group(3)), paper_methods=norm(m.group(4)))
            else:
                segs.update(paper_methods=norm(body))
            out.update(mode="fulltext", pmcid=ft_paper.pmcid, fulltext_available=True, abstract_available=True, n_abstracts=1,
                       pmids=[str(ft_paper.pmid)] if isinstance(ft_paper.pmid, str) else [])
            out["prompt"] = head + f"\nPAPER THAT GENERATED THE DATA ({ft_paper.pmcid}):\n{body}"
            out["segs"] = segs
            return out
    abstracts = []
    for r in own.itertuples():
        if len(abstracts) >= 3:
            break
        p = {"pmid": r.pmid if isinstance(r.pmid, str) else None, "doi": r.doi if isinstance(r.doi, str) else None}
        try:
            a = PP.abstract_for(p) if (p["pmid"] or p["doi"]) else ""
        except Exception:  # noqa: BLE001 — Europe PMC unavailable counts as no abstract
            a = ""
        if a:
            abstracts.append((p["pmid"] or p["doi"], a))
    if abstracts:
        segs["paper_abstract"] = norm(" ".join(a for _, a in abstracts))
        out.update(mode="abstract", abstract_available=True, n_abstracts=len(abstracts), pmids=[k for k, _ in abstracts])
        out["prompt"] = head + "".join(f"\nABSTRACT of a paper that generated the data ({k}):\n{a}\n" for k, a in abstracts)
    else:
        out.update(mode="archive")
        out["prompt"] = head + "\n(No paper text is available for this project; use the archive record only.)"
    out["segs"] = segs
    return out


ISO2 = re.compile(r"^[A-Z]{2}$")
DATE = re.compile(r"^(19[89]\d|20[0-2]\d)(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?(/(19[89]\d|20[0-2]\d)(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?)?$")


BODY_SITE = re.compile(
    r"(the )?((upper|lower|anterior|posterior|human)\s+)?(nasopharyn\w*|oropharyn\w*|nasal\w*|nose|nares|oral\w*|mouth|"
    r"tongue|saliva\w*|dental\w*|tooth|teeth|throat|pharyn\w*|lung\w*|airway\w*|respiratory( tract| axis)?|bronch\w*|"
    r"bronchoalveolar( lavage)?|sputum|gut|intestin\w*|colon\w*|caecum|cecum|rect\w*|stool|faec\w*|fec\w*|meconium|"
    r"skin|scalp|axilla\w*|forearm|groin|vagin\w*|cervi(x|cal)|uterus|uterine|amnio\w*|placenta\w*|breast ?milk|"
    r"urin\w*|urinary( tract)?|bladder|blood|plasma|serum|stomach|gastric|oesophag\w*|esophag\w*|duoden\w*|ileum|"
    r"ileal|jejun\w*|bile|liver|conjunctiv\w*|ear|eye)"
    r"(\s+(tract|site|sites|swab|swabs|sample|samples|microbiome|microbiota|mucosa|cavity|fluid|region|canal))*"
    r"(\s*\([^)]*\))?", re.I)


def norm_value(field, v):
    """Validated, normalised value or None."""
    v = str(v or "").strip()
    if not v or v.lower() in ("unknown", "unclear", "not stated", "none", "null", "n/a"):
        return None
    if field == "life_stage":
        return v.lower() if v.lower() in STAGES else None
    if field == "age_at_collection_days":
        try:
            x = float(v)
        except ValueError:
            return None
        return f"{x:g}" if 0 <= x <= 40000 else None
    if field == "sex":
        return v.lower() if v.lower() in ("female", "male") else None
    if field == "country":
        v = v.upper()
        return v if ISO2.match(v) else None
    if field == "antibiotic_exposure":
        return v.lower() if v.lower() in ("yes", "no") else None
    if field == "collection_date":
        return v if DATE.match(v) else None
    if field in ("location_region", "location_locality", "location_site"):
        if BODY_SITE.fullmatch(norm(v).strip(" .,;:")):   # a body site is not a place (R2026.32 audit)
            return None
        return v[:MAXLEN[field]] if len(v) <= MAXLEN[field] else None
    voc = {"health_condition": HC, "lifestyle": LS, "diet": DI, "smoking_status": SM}.get(field)
    if voc is not None:
        return v if v in voc else None
    if field == "medication":
        cs = sorted({c.strip() for c in v.split(";") if c.strip()})
        return ";".join(cs) if cs and all(c in MED for c in cs) else None
    return None


def match_key(field, v):
    return re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"\s+", " ", v.lower())).strip() if field.startswith("location_") else v


def fold_locations(a, b):
    """Readings that name the same place at different length agree on the shorter one.

    a, b: {(field, match_key): [rows]} for the two readings. One reading's place name containing the other's
    (word-boundary containment of the normalised keys, e.g. 'research center borstel' inside 'research center borstel
    medical clinic') is the same place written longer; keep the shorter, more conservative value in both readings so the
    usual agreement rule commits it. Only location_* fields; other fields must match exactly.
    """
    for f in {k[0] for k in a} & {k[0] for k in b}:
        if not f.startswith("location_"):
            continue
        for ka in [k for k in list(a) if k[0] == f]:
            for kb in [k for k in list(b) if k[0] == f]:
                if ka[1] == kb[1] or not (ka[1] and kb[1]) or ka not in a or kb not in b:
                    continue
                (sd, sk), (ld, lk) = sorted(((a, ka), (b, kb)), key=lambda t: len(t[1][1]))
                if re.search(rf"(^|\s){re.escape(sk[1])}(\s|$)", lk[1]):
                    ld.setdefault(sk, []).extend(dict(r, value=sd[sk][0]["value"]) for r in ld.pop(lk, []))
    return a, b


SRC_MAP = {"paper_methods": ("paper.fulltext.methods", "R3", 0.7), "paper_data_availability": ("paper.fulltext.data_availability", "R3", 0.6),
           "archive_description": ("study.description", "R4", 0.45), "archive_title": ("study.title", "R4", 0.4)}


def evidence(src, mode):
    if src == "paper_abstract":
        return ("paper.fulltext.abstract", "R3", 0.65) if mode == "fulltext" else ("paper.abstract", "R4", 0.5)
    return SRC_MAP.get(src)


def find_quote(q, segs, prefer=None):
    """-> source key whose text contains the quote (the stated one first), or None."""
    order = ([prefer] if prefer in segs else []) + [k for k in segs if k != prefer]
    for k in order:
        if quote_ok(q, segs[k]):
            return k
    return None


def _dict(v):
    """A schema-violating sub-object (a string or list where an object is expected) counts as missing."""
    return v if isinstance(v, dict) else {}


def merge_project(acc, tx, reps, n_samples):
    """reps: [reading1, reading2] tool outputs (dict or None). Returns (dets, subgroups, unreplicated, iv_row, design_row, verdicts)."""
    mode, segs = tx["mode"], tx["segs"]
    locator = {"fulltext": tx.get("pmcid"), "abstract": ";".join(tx.get("pmids") or []), "archive": acc}
    per_rep = []
    sub, unrep = [], []
    for ri, r in enumerate(reps):
        got = {}
        sts = (r or {}).get("statements") if isinstance(r, dict) else None
        for s in (sts if isinstance(sts, list) else []):
            if not isinstance(s, dict) or s.get("field") not in FIELDS:
                continue
            f = s["field"]
            if f == "sex" and mode != "fulltext":
                continue   # sex is an R3-only field in the pack
            v = norm_value(f, s.get("value"))
            if v is None:
                continue
            src = find_quote(s.get("quote"), segs, s.get("source"))
            row = dict(field=f, value=v, detail=s.get("detail"), quote=s.get("quote"), src=src, reading=ri + 1, subgroup=s.get("subgroup"))
            if s.get("applies_to") == "subgroup":
                sub.append(row)
            else:
                got.setdefault((f, match_key(f, v)), []).append(row)
        per_rep.append(got)
    dets = []
    if len(per_rep) == 2:
        per_rep = list(fold_locations(*per_rep))
    agreed = set(per_rep[0]) & set(per_rep[1]) if len(per_rep) == 2 else set()
    # one value per field: a field with two different agreed values is contradictory and is dropped
    by_field = {}
    for f, k in agreed:
        by_field.setdefault(f, []).append(k)
    for f, ks in by_field.items():
        if len(ks) != 1:
            for k in ks:
                unrep.extend(per_rep[0][(f, k)][:1] + per_rep[1][(f, k)][:1])
            continue
        rows = per_rep[0][(f, ks[0])] + per_rep[1][(f, ks[0])]
        ok = [x for x in rows if x["src"]]
        if not ok:
            unrep.extend(rows[:2])
            continue
        ev = sorted((evidence(x["src"], mode) + (x,) for x in ok if evidence(x["src"], mode)), key=lambda t: (t[1] != "R3", -t[2]))
        if not ev:
            continue
        if f == "health_condition":   # R4 condition evidence must name the participants and never rests on the title alone (pilot audit)
            ev = [e for e in ev if e[1] == "R3" or (e[0] != "study.title" and PERSON.search(str(e[3]["quote"] or "")))]
            if not ev:
                unrep.extend(rows[:2])
                continue
        esrc, route, conf, best = ev[0]
        loc = locator["fulltext"] if esrc.startswith("paper.fulltext") else (locator["abstract"] if esrc == "paper.abstract" else acc)
        base = dict(sample_key="", study_accession=acc, confidence=conf, evidence_source=esrc, evidence_locator=loc,
                    evidence_quote=str(best["quote"])[:400], evidence_limited_to_abstract=route != "R3",
                    determined_by=f"{PASS}:{MODEL_TAG}x2", route=route, scope="study_all",
                    parse_note=NOTE, group_audit=None, src_track="catalog_v1", release_added=None, release_retired=None, package_added=None)
        dets.append(dict(base, field_name=f, field_value=best["value"], value_normalized=best["value"]))
        dtl = next((str(x["detail"]).strip() for x in rows if x.get("detail") and str(x["detail"]).strip()), None)
        if f in DETAIL and dtl:
            df = DETAIL[f]
            dets.append(dict(base, field_name=df, field_value=dtl[:MAXLEN[df]], value_normalized=dtl[:MAXLEN[df]]))
    for i, got in enumerate(per_rep):
        for key, rows in got.items():
            if key not in agreed:
                unrep.extend(rows[:1])
    # interventions
    ivs = [_dict((r or {}).get("interventions")) for r in reps]
    sets = [set(c for c in (x.get("codes") or []) if c in IV_STUDY) for x in ivs]
    both = sets[0] & sets[1] if len(sets) == 2 else set()
    union = set().union(*sets) if sets else set()
    des = [x.get("design") for x in ivs]
    if len(des) == 2 and des[0] == des[1] and des[0] in IV_DESIGNS and (both or des[0] == "none"):
        design = des[0]
    else:
        design = "none" if (not union and all(d_ in ("none", None) for d_ in des)) else "unknown"
    q_iv, s_iv = None, None
    for x in ivs:
        s_ = find_quote(x.get("quote"), segs, x.get("source")) if x.get("quote") else None
        if s_:
            q_iv, s_iv = x.get("quote"), s_
            break
    ev_iv = evidence(s_iv, mode) if s_iv else None
    iv_row = dict(study_accession=acc, interventions=";".join(sorted(both)), interventions_unreplicated=";".join(sorted(union - both)),
                  intervention_design=design if both or design in ("none", "unknown") else "unknown",
                  intervention_detail=(ivs[0].get("detail") or "")[:300] if both else "",
                  population_condition=(ivs[0].get("population_condition") or "")[:200] if both else "",
                  evidence_source=ev_iv[0] if ev_iv else "", evidence_quote=str(q_iv)[:400] if q_iv else "",
                  confidence=ev_iv[2] if ev_iv else 0.0, route=ev_iv[1] if ev_iv else ("R3" if mode == "fulltext" else "R4"),
                  determined_by=PASS, abstract_available=bool(tx.get("abstract_available")), n_abstracts=int(tx.get("n_abstracts") or 0),
                  note=NOTE + ("" if len(reps) == 2 and all(reps) else "; one reading missing"))
    # design
    dz = [_dict((r or {}).get("design")) for r in reps]
    lon = [x.get("longitudinal") for x in dz]
    long_ = lon[0] if len(lon) == 2 and lon[0] == lon[1] and lon[0] in ("yes", "no") else "unclear"
    rel = [x.get("relatedness") for x in dz]
    rel_ = rel[0] if len(rel) == 2 and rel[0] == rel[1] and rel[0] in RELATED else "none"
    d0 = dz[0] if dz else {}
    offs = [int(round(float(o))) for o in (d0.get("planned_offsets_days") or []) if isinstance(o, (int, float)) and -400 <= o <= 40000] if long_ == "yes" else []
    qd = [(x.get("quote"), find_quote(x.get("quote"), segs, x.get("source"))) for x in dz if x.get("quote")]
    qd = [(q, s_) for q, s_ in qd if s_]
    evd = lambda s_: (evidence(s_, mode) or ("", "", 0))[0]
    screen = "both" if long_ == "yes" and rel_ != "none" else ("longitudinal" if long_ == "yes" else ("related" if rel_ != "none" else "none"))
    design_row = dict(study_accession=acc, screen=screen, relatedness_design=rel_,
                      relatedness_summary=(d0.get("relatedness_summary") or "")[:300] if rel_ != "none" else "",
                      n_families=d0.get("n_families") if rel_ != "none" and isinstance(d0.get("n_families"), int) else None,
                      longitudinal_design=long_, sampling_schedule=(d0.get("sampling_schedule") or "")[:300] if long_ == "yes" else "",
                      planned_offsets_days=";".join(str(o) for o in sorted(set(offs))), time_origin=(d0.get("time_origin") or "") if long_ == "yes" else "",
                      followup_days_stated=d0.get("followup_days_stated") if long_ == "yes" and isinstance(d0.get("followup_days_stated"), (int, float)) else None,
                      evidence_source=evd(qd[0][1]) if qd else "", evidence_quote=str(qd[0][0])[:400] if qd else "",
                      evidence_source_2=evd(qd[1][1]) if len(qd) > 1 else "", evidence_quote_2=str(qd[1][0])[:400] if len(qd) > 1 else "",
                      notes=NOTE + ("" if long_ != "unclear" or (len(lon) == 2 and lon[0] == lon[1]) else f"; readings disagree on longitudinal ({lon[0]} / {lon[1] if len(lon) > 1 else None})"))
    sj = [_dict((r or {}).get("subjects")) for r in reps]
    verdicts = dict(v1=(sj[0].get("samples_per_subject") if sj else None), v2=(sj[1].get("samples_per_subject") if len(sj) > 1 else None),
                    stated=next((x.get("stated_participants") for x in sj if isinstance(x.get("stated_participants"), int)), None),
                    quote=next(((x.get("quote"), find_quote(x.get("quote"), segs, x.get("source"))) for x in sj if x.get("quote") and find_quote(x.get("quote"), segs, x.get("source"))), (None, None)),
                    longitudinal=long_)
    return dets, sub, unrep, iv_row, design_row, verdicts


PERSON = re.compile(r"\b(participants?|patients?|subjects?|individuals?|donors?|volunteers?|people|persons?|children|child|"
                    r"infants?|neonates?|newborns?|babies|baby|mothers?|women|woman|men|man|adults?|cases?|controls?|"
                    r"household members|twins?|families|recipients?)\b", re.I)
SAMPLE_COUNT = re.compile(r"\b\d+\s+(?:\S+\s+){0,2}?(samples?|specimens?|swabs?|extracts?|libraries|library|isolates?|runs?|"
                          r"sequences?|datasets?|metagenomes?|genomes?|reads?|aliquots?|biopsies|biopsy)\b", re.I)


def people_quote(q):
    """The one-sample-per-subject evidence must speak about people, not count samples (R2026.32 audit)."""
    q = str(q or "")
    return bool(PERSON.search(q)) and not SAMPLE_COUNT.search(q)


def subject_rows(acc, verdicts, samples, mode):
    """Generated subject ids (gut_subject_unique_v1) and the subject-log row."""
    sp = samples[samples.study_accession == acc]
    spec = sp[sp.sample_type.fillna("specimen").eq("specimen")] if "sample_type" in sp.columns else sp
    without = spec[spec.subject_id.isna() | (spec.subject_id.astype(str).str.strip() == "")]
    ntp = spec.timepoint_label.dropna().astype(str).nunique() if "timepoint_label" in spec.columns else 0
    q, qsrc = verdicts["quote"]
    log = dict(study_accession=acc, n_samples=len(spec), n_without_subject=len(without), generated_n=0,
               design_verdict_r1=verdicts["v1"], design_verdict_r2=verdicts["v2"], generation_basis="", stated_participants=verdicts["stated"],
               evidence_quote=str(q)[:400] if q else "", evidence_source=(evidence(qsrc, mode) or ("",))[0] if qsrc else "", blocked_reason="",
               note=NOTE)
    if not len(without):
        log["blocked_reason"] = "every sample already names its subject"
        return [], log
    if not (verdicts["v1"] == verdicts["v2"] == "one_sample_per_subject"):
        log["blocked_reason"] = "readings do not both say one sample per subject"
        return [], log
    if verdicts["longitudinal"] == "yes":
        log["blocked_reason"] = "longitudinal design"
        return [], log
    if ntp > 1:
        log["blocked_reason"] = f"{ntp} distinct timepoint labels"
        return [], log
    if not people_quote(q):
        log["blocked_reason"] = "evidence counts samples, not participants"
        return [], log
    basis = {"paper_methods": "fulltext", "paper_data_availability": "fulltext", "paper_abstract": "fulltext" if mode == "fulltext" else "abstract",
             "archive_description": "description", "archive_title": "description"}.get(qsrc or "", "readings")
    conf = {"fulltext": 0.75, "abstract": 0.65, "description": 0.65}.get(basis, 0.55)
    route = "R3" if basis == "fulltext" else "R4"
    rows = [dict(sample_key=k, field_name="subject_id", study_accession=acc, field_value=k, value_normalized=k, confidence=conf,
                 evidence_source=f"design.one_sample_per_subject:{basis}", evidence_locator=acc, evidence_quote=str(q)[:400] if q else "",
                 evidence_limited_to_abstract=route != "R3", determined_by="gut_subject_unique_v1", route=route, scope="sample",
                 parse_note=f"generated: one sample per subject (two readings agree; {basis}; catch-up pass R2026.32)", group_audit=None,
                 src_track="catalog_v1", release_added=None, release_retired=None, package_added=None) for k in without.sample_key.astype(str)]
    log.update(generated_n=len(rows), generation_basis=basis)
    return rows, log


def run(projects, out, budget, shard, host=None):
    if host is not None:
        M.set_host(host)
    for sub in ("r3c/shards", "r4c", "interventions", "design/logs", "deep"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    st = pd.read_parquet(os.path.join(PKG, "catalog_studies.parquet"))
    st = st[st.release_retired.isna()] if "release_retired" in st.columns else st
    st = st.set_index("study_accession")
    papers = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "catalog", "study_papers.parquet"))
    papers = papers[papers.relation == "own_data"]
    cols = ["study_accession", "sample_key", "subject_id", "timepoint_label", "sample_type", "release_retired"]
    samples = pd.read_parquet(os.path.join(PKG, "catalog_sample_metadata_wide.parquet"), columns=cols,
                              filters=[("study_accession", "in", list(projects))])
    samples = samples[samples.release_retired.isna()]
    led = os.path.join(out, "r3c", "shards", f"ledger_{shard}.csv")
    llm = LLM("rubric", cache_dir=f"{CACHE}/llm/catchup_r2026_32", ledger=led, budget_usd=budget, pass_name="catchup_r2026_32", max_concurrency=4)
    global MODEL_TAG
    MODEL_TAG = llm.model
    T, reqs, keys = {}, [], []
    for acc in projects:
        if acc not in st.index:
            continue
        d = load_dossier(acc)
        tx = project_texts(acc, d, st.loc[acc].to_dict(), papers)
        T[acc] = tx
        for rep in (1, 2):
            reqs.append({"system": SYSTEM + ("" if rep == 1 else "\n(second, independent reading)"), "prompt": tx["prompt"],
                         "tools": [TOOL], "tool_choice": {"type": "tool", "name": "record"}, "max_tokens": 3000, "thinking": NOTHINK})
            keys.append((acc, rep))
    status = {}
    try:
        res = llm(reqs)
    except BudgetExceeded as e:
        res = []
        status["budget"] = str(e)[:200]
    R = {}
    for (acc, rep), r in zip(keys, res):
        ok = r.get("status") in ("ok", "cached") and isinstance(r.get("tool_use"), dict)
        R.setdefault(acc, [None, None])[rep - 1] = r["tool_use"] if ok else None
        status[r.get("status")] = status.get(r.get("status"), 0) + 1
    D3, D4, SUB, UNR, IVR, DZR, SJR, SLOG, S3, S4 = [], [], [], [], [], [], [], [], [], []
    for acc, tx in T.items():
        reps = R.get(acc, [None, None])
        n_ok = sum(1 for x in reps if x)
        if n_ok == 0:
            S4.append(dict(study_accession=acc, design="", n_subjects_stated=None, abstract_available=tx["abstract_available"],
                           n_abstracts=tx["n_abstracts"], n_pmids_linked=len(tx["pmids"]), n_statements_raw=0, llm_returned=False,
                           n_statements_final=0, text_mode=tx["mode"], status="no_reading", has_description=bool(tx["segs"].get("archive_description"))))
            if tx["mode"] == "fulltext":
                S3.append(dict(study_accession=acc, pmcid=tx["pmcid"], fulltext_available=True, n_statements_agreed=0, n_statements_unreplicated=0,
                               n_subgroup=0, n_replicates_run=0, status="no_reading", note=NOTE))
            continue
        if n_ok == 1:
            reps = [x or {} for x in reps]
        dets, sub, unrep, iv_row, dz_row, verd = merge_project(acc, tx, reps, None)
        for x in sub:
            SUB.append(dict(study_accession=acc, **{k: (str(v) if v is not None else None) for k, v in x.items()}))
        for x in unrep:
            UNR.append(dict(study_accession=acc, **{k: (str(v) if v is not None else None) for k, v in x.items()}))
        d3 = [x for x in dets if x["route"] == "R3"]
        d4 = [x for x in dets if x["route"] == "R4"]
        D3 += d3
        D4 += d4
        IVR.append(iv_row)
        DZR.append(dz_row)
        rows, log = subject_rows(acc, verd, samples, tx["mode"])
        if n_ok == 1:
            rows, log = [], dict(log, generated_n=0, blocked_reason="only one reading returned")
        SJR += rows
        SLOG.append(log)
        S4.append(dict(study_accession=acc, design=iv_row["intervention_design"], n_subjects_stated=verd["stated"], abstract_available=tx["abstract_available"],
                       n_abstracts=tx["n_abstracts"], n_pmids_linked=len(tx["pmids"]), n_statements_raw=sum(len((r or {}).get("statements") or []) for r in reps),
                       llm_returned=True, n_statements_final=len(d4), text_mode=tx["mode"], status="done" if n_ok == 2 else "one_reading",
                       has_description=bool(tx["segs"].get("archive_description"))))
        if tx["mode"] != "fulltext":   # logged as attempted: no open full text of a paper that generated the data
            S3.append(dict(study_accession=acc, pmcid=None, fulltext_available=False, n_statements_agreed=0, n_statements_unreplicated=0,
                           n_subgroup=0, n_replicates_run=0, status="no_generating_fulltext", note=NOTE))
        if tx["mode"] == "fulltext":
            S3.append(dict(study_accession=acc, pmcid=tx["pmcid"], fulltext_available=True, n_statements_agreed=len(d3),
                           n_statements_unreplicated=sum(1 for x in unrep), n_subgroup=len(sub), n_replicates_run=n_ok,
                           status="done" if n_ok == 2 else "one_reading", note=NOTE))
    k = shard
    W = lambda rows, cols_, path: pd.DataFrame(rows, columns=cols_).to_parquet(os.path.join(out, path), index=False)
    W(D3, DET_COLS, f"r3c/shards/gut_r3c_determinations_shard_{k}.parquet")
    W(S3, ["study_accession", "pmcid", "fulltext_available", "n_statements_agreed", "n_statements_unreplicated", "n_subgroup", "n_replicates_run", "status", "note"],
      f"r3c/shards/gut_r3c_study_summary_shard_{k}.parquet")
    W(SUB, ["study_accession", "field", "value", "detail", "quote", "src", "reading", "subgroup"], f"r3c/shards/gut_r3c_subgroups_shard_{k}.parquet")
    W(UNR, ["study_accession", "field", "value", "detail", "quote", "src", "reading", "subgroup"], f"r3c/shards/gut_r3c_unreplicated_shard_{k}.parquet")
    W(D4, DET_COLS, f"r4c/gut_r4c_determinations_shard_{k}.parquet")
    W(S4, ["study_accession", "design", "n_subjects_stated", "abstract_available", "n_abstracts", "n_pmids_linked", "n_statements_raw", "llm_returned",
           "n_statements_final", "text_mode", "status", "has_description"], f"r4c/gut_r4c_study_summary_shard_{k}.parquet")
    W(IVR, ["study_accession", "interventions", "interventions_unreplicated", "intervention_design", "intervention_detail", "population_condition",
            "evidence_source", "evidence_quote", "confidence", "route", "determined_by", "abstract_available", "n_abstracts", "note"],
      f"interventions/gut_intervention_studies_shard_C{k}.parquet")
    W(DZR, ["study_accession", "screen", "relatedness_design", "relatedness_summary", "n_families", "longitudinal_design", "sampling_schedule",
            "planned_offsets_days", "time_origin", "followup_days_stated", "evidence_source", "evidence_quote", "evidence_source_2", "evidence_quote_2", "notes"],
      f"design/study_design_shard_C{k}.parquet")
    W(SLOG, ["study_accession", "n_samples", "n_without_subject", "generated_n", "design_verdict_r1", "design_verdict_r2", "generation_basis",
             "stated_participants", "evidence_quote", "evidence_source", "blocked_reason", "note"], f"design/logs/subject_log_shard_C{k}.parquet")
    W(SJR, DET_COLS, f"deep/deep_determinations_shard_SUBJC{k}.parquet")
    summ = dict(shard=k, projects=len(T), modes=pd.Series([t["mode"] for t in T.values()]).value_counts().to_dict(), llm_status=status,
                r3_rows=len(D3), r4_rows=len(D4), subgroup_rows=len(SUB), unreplicated_rows=len(UNR),
                interventions_with_codes=sum(1 for x in IVR if x["interventions"]), design_longitudinal_yes=sum(1 for x in DZR if x["longitudinal_design"] == "yes"),
                design_related=sum(1 for x in DZR if x["relatedness_design"] != "none"), subjects_generated=len(SJR),
                projects_with_generated_subjects=sum(1 for x in SLOG if x["generated_n"]), cost=llm.summary())
    json.dump(summ, open(os.path.join(out, "r3c", "shards", f"summary_shard_{k}.json"), "w"), indent=1, default=str)
    print(json.dumps(summ, default=str)[:1500])
    return summ


def merge(out):
    """Concatenate the R3 catch-up shards into the files the build and the coverage check read."""
    d = os.path.join(out, "r3c")
    for kind, name in (("determinations", "gut_r3c_determinations_merged.parquet"), ("study_summary", "gut_r3c_study_summary_all.parquet"),
                       ("subgroups", "gut_r3c_subgroups_all.parquet"), ("unreplicated", "gut_r3c_unreplicated_all.parquet")):
        fs = sorted(glob.glob(os.path.join(d, "shards", f"gut_r3c_{kind}_shard_*.parquet")))
        parts = [pd.read_parquet(f) for f in fs]
        parts = [p for p in parts if len(p)]
        df = pd.concat(parts, ignore_index=True) if parts else pd.read_parquet(fs[0]) if fs else pd.DataFrame()
        df.to_parquet(os.path.join(d, name), index=False)
        print(kind, len(fs), len(df))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=os.path.join(ROOT, "config", "pass_coverage_exceptions.csv"))
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="first N projects of the shard only (pilot)")
    ap.add_argument("--budget", type=float, default=5.0)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "inputs", "gut"))
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--tag", default="", help="shard name prefix (R2026.34: a monthly run's shards must not overwrite earlier ones, e.g. R34_)")
    a = ap.parse_args(argv)
    if a.merge:
        return merge(a.out)
    ids = sorted({l.strip().split(",")[0] for l in open(a.projects) if l.strip() and not l.startswith("study_accession")})
    mine = [p for i, p in enumerate(ids) if i % a.nshards == a.shard]
    if a.limit:
        mine = mine[:a.limit]
    return run(mine, a.out, a.budget, f"{a.tag}{a.shard}" if not a.limit else f"pilot{a.tag}{a.shard}", host=globals().get("host"))


if __name__ == "__main__":
    main()
