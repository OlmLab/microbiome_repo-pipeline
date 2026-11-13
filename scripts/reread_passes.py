#!/usr/bin/env python
"""reread_passes.py — the paper re-read of the catalog deep pass (R2026.40, stage B1): lifestyle and methods, one pass.

ONE request per project: the paper judged to have generated the data (title, abstract, data availability, methods; the
abstracts of up to three such papers when no full text is open), read once on the reasoning tier (`rubric`). Two blocks:

  lifestyle block
    lifestyle        statements about the WAY OF LIFE of the sampled people, for the whole cohort or for a named subgroup, each
                     with a verbatim sentence. Standard of R2026.38: the sentence must be about the sampled people; a city,
                     country, hospital or university name is not a statement of urban living; settlement (urban / rural /
                     village) is another field and is not read here.
    exposures        routine fecal-oral exposure items the paper records for its participants (sanitation, drinking water,
                     livestock or animals, household size or crowding, and the other concepts of pilot_passes.EXPOSURE)
    sampling_period  the period in which the samples were collected, when the paper states it
  methods block
    facility         the company, core or institute that RAN THE SEQUENCING, as the paper names it (not the submitting centre
                     of the archive record)
    extraction_kit, library_kit, platform   as the paper names them, each with its sentence

What is committed (deterministic, then an Opus 5 check):
  * every item needs a quote that is a verbatim substring of the text read (catchup_passes.quote_ok); a facility, kit or
    platform name must itself occur in its quote;
  * each lifestyle statement and sampling period is then checked by the `audit` role (Opus 5) against the passage around
    its quote: supported / not_supported; only supported items are written;
  * lifestyle for the whole cohort -> one cohort row (scope study_all, route R3 full text / R4 abstract);
    lifestyle for a subgroup -> per-sample rows, only when the reading names a sample variable already in the catalog
    (comparison_group, country, location_region, health_condition, age_category) and the values that select the subgroup,
    the Opus check confirms the selector, and the values exist; otherwise the statement is kept in the subgroup side table;
  * two different codes for the whole cohort -> nothing is written for the project (listed as conflicting);
  * sampling period -> collection_date as an ISO interval for the whole cohort (the build fills only empty cells).

Outputs under --out (default data/inputs/gut/reread), shard k:
  reread_reading_shard_{k}.parquet        one row per project (status, text mode, paper, counts)
  reread_determinations_shard_{k}.parquet rows in the build's determination columns (lifestyle, collection_date)
  reread_facts_shard_{k}.parquet          study_accession, fact, value_raw, quote, source (facility / kits / platform)
  reread_exposures_shard_{k}.parquet      study_accession, concept, scale, summary, quote
  reread_subgroups_shard_{k}.parquet      lifestyle statements for subgroups (assigned or not) and rejected items, with reason
  logs/reread_summary_{k}.json, logs/ledger_{k}.csv, logs/ledger_check_{k}.csv
Prints counts only (no paper text).

usage (inside a kernel that has `host`, via runpy with init_globals={"host": host, "ARGS": [...]}):
  --projects LIST.csv (study_accession) --shard K --nshards N --budget USD [--check-budget USD] [--limit N] [--out DIR]
  --merge   concatenate the shard files into reread_{reading,determinations,facts,exposures,subgroups}_all.parquet
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
from catalog import models as M  # noqa: E402
from catalog.llm_backend import LLM, BudgetExceeded  # noqa: E402

M.set_host(globals().get("host"))
import catchup_passes as CP  # noqa: E402
import pilot_passes as PP  # noqa: E402

PKG = os.environ.get("CATALOG_PKG") or os.path.join(ROOT, "build", "package")   # CATALOG_PKG = a frozen or intermediate package
NOTHINK = {"type": "disabled"}
LIFESTYLES = [c for c in PP.LIFESTYLES if c not in ("unknown",)]
EXPOSURE = PP.EXPOSURE
SELECTORS = ["comparison_group", "country", "location_region", "health_condition", "age_category"]
FAC_KINDS = ["company", "university_core_facility", "research_institute_or_genome_centre", "hospital_or_clinical_lab", "authors_own_lab",
             "consortium_or_national_centre", "other"]
DET_COLS = ["sample_key", "field_name", "study_accession", "field_value", "value_normalized", "confidence", "evidence_source", "evidence_locator",
            "evidence_quote", "evidence_limited_to_abstract", "determined_by", "route", "scope", "parse_note", "group_audit", "src_track",
            "release_added", "release_retired", "package_added"]
SRC = "gut_all_v1"
BY = "reread_v1"

SYSTEM = """You read the paper that generated a human microbiome sequencing project and record two things: how the sampled people
live, and how the samples were sequenced. Record only what the text states. Every item needs `quote`: one sentence (or part
of one, at most 60 words) copied character for character from the text. Return empty lists / null when the text does not
say; most papers state no lifestyle at all.

LIFESTYLE BLOCK
lifestyle: a statement about the WAY OF LIFE of the people who were sampled — hunter-gatherers, pastoralists, subsistence
  farmers, people living a rural non-industrialized (subsistence) life, an isolated religious community, an indigenous
  community, nomads, a cohort defined by vegetarian or vegan diet, competitive athletes, military personnel, spaceflight or
  analog crews, residents of a nursing home or prison (institutionalized), high-altitude / polar / expedition residents,
  recent migrants or an urbanising population.
  NOT a lifestyle statement (record nothing):
  - SETTLEMENT: where people live — urban, rural, village, town, kebele, district, 'rural residents', 'urban areas'. That is
    another field. rural_non_industrialized needs a statement about subsistence or non-industrialized living, not the word
    rural; a rural cohort in a high-income country is not non-industrialized. urban_industrialized ONLY when the text
    characterises the sampled people's way of life as industrialized or urbanized (usually set against a traditional
    population in the same paper); the name of a city, country, hospital or university, 'healthy volunteers' or 'Western
    diet' is not such a statement.
  - OCCUPATION, except competitive athletes, military personnel and spaceflight or analog crews: office workers, students,
    health-care staff, e-sports players, farm or dairy workers are not a way of life (contact with livestock belongs in
    exposures).
  - a lifestyle inferred from the country, or named only for a comparison data set taken from other studies.
  - the way of life of an ancient or historical population (archaeological samples, coprolites, dental calculus of
    skeletons).
  Decide the SCOPE first. If the sentence states the way of life of one named group and the project also holds other
  people (other groups, visitors, controls, staff — look at the design and at SAMPLE VARIABLES IN THE CATALOG), the item is
  a subgroup item carrying that sentence (applies_to = subgroup, group_as_named = the group as the paper names it).
  applies_to = all only when every sampled person of this project lives that way. For a subgroup, look at SAMPLE VARIABLES
  IN THE CATALOG: if one variable has values that select exactly the people of this subgroup and nobody else, give
  selector_field and selector_values (copied exactly); a variable that also covers other people (a country that visitors
  share) is not a selector; otherwise selector_field = none (always none when no variable is listed; never invent a
  variable or a value). One item per group and code; do not give one group two codes
  (choose the more specific).
  The quote should come from the paper text; a sentence of the ARCHIVE DESCRIPTION counts only when the paper text has none.
exposures: MOST PAPERS RECORD NONE. Add an item only when the paper records, for its human participants, one of the listed
  concepts (sanitation = toilet, latrine or sewage facilities; drinking_water = water source or treatment;
  livestock_or_animals = contact with livestock or other animals; household_size_or_crowding = people per household or
  room; and the other listed concepts as named). scale: per_person (a value for each participant), per_group (summary by
  group, e.g. a Table 1 percentage), whole_cohort (true of everyone, e.g. an inclusion criterion). Never add an item to say
  something is not reported. Laboratory uses of these words and statements about other studies do not count.
sampling_period: the period in which THIS study's samples were collected, for the whole project, as start and end in YYYY
  or YYYY-MM. An enrolment or recruitment window counts only when the samples were taken at enrolment and sampling did not
  continue afterwards (a cohort enrolled in 2015-2016 and followed for a year was sampled until 2017). null when the text
  gives only a start ('collection began in 2018', 'from 2019 onward'), a window for one group only, the dates of a parent
  cohort, or the publication or sequencing date.

METHODS BLOCK
facility: the organisation that ran the SHOTGUN sequencing of this project's samples (and, if stated, library preparation): a
  company, a university core facility, a genome centre, a hospital laboratory. name_raw exactly as written. Only when the
  text says the sequencing was done at / by / in it, or that samples or libraries were sent, submitted or outsourced to it
  for sequencing. Not the place where 16S amplicon sequencing, isolates or host genotyping were done. The manufacturer of the sequencer (Illumina, MGI, PacBio, Oxford Nanopore) is NOT the facility
  unless the text says the company's own service laboratory ran the samples. The authors' own institute counts only when
  the text names the unit that sequenced (e.g. 'the Genomics Core of ...'). An organisation must be NAMED ('a commercial
  provider' is not a name). Not the ethics committee, the biobank or storage site, the place samples were shipped to for
  processing, or the supplier of a kit or capture workflow. Several facilities -> several items.
extraction_kit / library_kit / platform: the DNA or RNA extraction kit, the library preparation kit, and the sequencing
  instrument, name_raw exactly as written, each with its sentence. For the SHOTGUN sequencing of this project's samples
  (not 16S amplicon work, not isolates). null when not stated."""

NULLSTR = {"type": ["string", "null"]}
NAMED = {"type": ["object", "null"], "properties": {"name_raw": {"type": "string"}, "quote": {"type": "string"}}, "required": ["name_raw", "quote"]}
TOOL = {"name": "reread", "description": "Record the lifestyle and methods facts.", "input_schema": {"type": "object", "properties": {
    "lifestyle": {"type": "array", "items": {"type": "object", "properties": {
        "code": {"type": "string", "enum": LIFESTYLES}, "applies_to": {"type": "string", "enum": ["all", "subgroup"]},
        "group_as_named": NULLSTR, "selector_field": {"type": "string", "enum": ["none"] + SELECTORS},
        "selector_values": {"type": "array", "items": {"type": "string"}}, "detail": NULLSTR, "quote": {"type": "string"}},
        "required": ["code", "applies_to", "selector_field", "quote"]}},
    "exposures": {"type": "array", "items": {"type": "object", "properties": {
        "concept": {"type": "string", "enum": EXPOSURE}, "scale": {"type": "string", "enum": ["per_person", "per_group", "whole_cohort"]},
        "summary": {"type": "string"}, "quote": {"type": "string"}}, "required": ["concept", "scale", "summary", "quote"]}},
    "sampling_period": {"type": ["object", "null"], "properties": {"start": {"type": "string"}, "end": {"type": "string"}, "quote": {"type": "string"}},
                        "required": ["start", "end", "quote"]},
    "facility": {"type": "array", "items": {"type": "object", "properties": {
        "name_raw": {"type": "string"}, "kind": {"type": "string", "enum": FAC_KINDS}, "country": NULLSTR, "quote": {"type": "string"}},
        "required": ["name_raw", "kind", "quote"]}},
    "extraction_kit": NAMED, "library_kit": NAMED, "platform": NAMED},
    "required": ["lifestyle", "exposures", "sampling_period", "facility", "extraction_kit", "library_kit", "platform"]}}

CHECK_SYSTEM = """You check proposed catalog values against the passage they were taken from. For each item answer supported or
not_supported.

lifestyle item: supported when the passage states this way of life for the people sampled in THIS study, for the scope
  given (all sampled people, or the named subgroup). The sentence may stand in the methods, the abstract or the
  introduction, as long as it says that the study's participants are such people ('residents of long-term aged care
  facilities were recruited' supports institutionalized for the cohort). not_supported when the passage only names a city, country, hospital or
  university (that is not 'urban_industrialized'); when it describes a comparison data set from other studies, the general
  population or the motivation of the study; when the code does not fit what is said; or when the scope is wrong (said of
  one group, proposed for all). For a subgroup item with a selector, also answer selector_ok: do the listed values of the
  sample variable select exactly the people the passage speaks of?
  Settlement is not a way of life: rural / urban / village / kebele / 'rural residents' alone -> not_supported;
  rural_non_industrialized needs subsistence or non-industrialized living to be stated; a rural cohort in a high-income
  country is not non-industrialized. Occupation is not a way of life, except competitive athletes, military personnel and
  spaceflight or analog crews.
  selector_ok = true only when the listed values pick the people the passage speaks of AND NOBODY ELSE. The item shows how
  many samples the values select and how those samples spread over the other sample variables: if that spread shows other
  kinds of people among them (another group, visitors, controls), or the count is far from the group size the passage
  gives, answer false.
facility item: supported only when the passage says that this NAMED organisation performed the shotgun (metagenomic) sequencing
  of this study's samples, or that the samples or libraries were sent or submitted to it for sequencing. not_supported for
  an ethics committee, a biobank or storage site, a place samples were shipped to for processing only, the supplier of a
  kit or capture workflow, the maker of the instrument, 16S amplicon or isolate sequencing only, or a phrase that names no
  organisation.
sampling_period item: supported only when the passage gives the period in which this study's samples were collected, for the
  whole project, and the proposed start and end are what it says. Enrolment or recruitment dates count only when samples
  were taken at enrolment and sampling did not continue afterwards. not_supported for an open start ('began in', 'from X
  onward'), a window stated for one group only, or dates of a parent cohort."""
CHECK_TOOL = {"name": "check", "description": "Return a verdict per item.", "input_schema": {"type": "object", "properties": {
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "integer"}, "verdict": {"type": "string", "enum": ["supported", "not_supported"]},
        "selector_ok": {"type": ["boolean", "null"]}, "why": {"type": "string"}}, "required": ["id", "verdict"]}}}, "required": ["items"]}}

PERIOD = re.compile(r"^(19[89]\d|20[0-2]\d)(-(0[1-9]|1[0-2]))?$")
GENERIC_FAC = re.compile(r"(?i)^(an? |the )?(commercial|external|outside|local|central|private|third[- ]party|sequencing|core|service|contract)[a-z ,/-]*"
                         r"(provider|company|facility|laboratory|lab|centre|center|service|vendor)s?$")
MAKERS = re.compile(r"(?i)^(illumina|mgi|bgi[- ]?seq|pacbio|pacific biosciences|oxford nanopore( technologies)?|thermo ?fisher|ion torrent|life technologies)\b[ ,.]*(inc\.?|ltd\.?)?$")


def row(sample, field, study, raw, norm, conf, src, loc, route, quote, scope="sample", note=""):
    return {"sample_key": sample, "field_name": field, "study_accession": study, "field_value": raw, "value_normalized": norm,
            "confidence": conf, "evidence_source": src, "evidence_locator": loc, "evidence_quote": quote,
            "evidence_limited_to_abstract": src.endswith("abstract"), "determined_by": BY, "route": route, "scope": scope,
            "parse_note": note or None, "group_audit": None, "src_track": SRC, "release_added": None, "release_retired": None, "package_added": None}


def locate(q, segs):
    """-> (segment name, passage of about 1,600 characters around the quote) or (None, None)."""
    qn = CP.norm(q).strip(" .,;:\"'")
    if len(qn.split()) < 3:
        return None, None
    for name in ("paper_methods", "paper_abstract", "paper_data_availability", "archive_description", "archive_title"):
        t = segs.get(name) or ""
        i = t.find(qn)
        if i >= 0:
            return name, t[max(0, i - 700): i + len(qn) + 700]
    return None, None


AMPLICON = re.compile(r"(?i)\b16S\b|amplicon|V3[-–]?V4|\bV4\b|\bITS\b")
SHOTGUN = re.compile(r"(?i)shotgun|metagenom|whole[- ]genome|\bWGS\b|metatranscriptom")


def name_in_quote(name, quote):
    """every word of the name (3+ characters) occurs in the quote: 'QIAamp DNA Stool Mini Kit (Qiagen)' against a sentence that
    puts the maker elsewhere would fail on exact matching although the kit is named."""
    qn = CP.norm(quote)
    toks = [t for t in re.findall(r"[a-z0-9]+", CP.norm(name)) if len(t) >= 3]
    return bool(toks) and all(t in qn for t in toks)


def spread(sel, field, vals, w):
    """how the samples a selector picks spread over the other sample variables (for the check)."""
    sks = {s_ for v in vals for s_ in sel[field].get(v, [])}
    sub = w[w.sample_key.isin(sks)]
    out = []
    for f in SELECTORS:
        if f == field or f not in sub.columns:
            continue
        vc = sub[f].astype("string").fillna("(no value)").value_counts()
        if len(vc) >= 2 or (len(vc) == 1 and vc.index[0] != "(no value)"):
            out.append(f"{f}: " + "; ".join(f"{k} x{c}" for k, c in list(vc.items())[:8]))
    return len(sks), " | ".join(out)


def sample_variables(w):
    """lines for the prompt and {field: {value: [sample_key]}} over the project's specimens."""
    out, lines = {}, []
    for f in SELECTORS:
        if f not in w.columns:
            continue
        v = w[f].astype("string")
        v = v[v.notna() & ~v.isin(["unknown", ""])]
        vc = v.value_counts()
        if len(vc) < 2 or len(vc) > 30:
            continue
        out[f] = {str(k): list(w.loc[v.index[v == k], "sample_key"]) for k in vc.index}
        lines.append(f"  {f} [{len(v)} of {len(w)} samples]: " + "; ".join(f"{k} x{c}" for k, c in vc.items()))
    return lines, out


def run(projects, out, budget, check_budget, shard, host=None):
    if host is not None:
        M.set_host(host)
    os.makedirs(os.path.join(out, "logs"), exist_ok=True)
    st = pd.read_parquet(os.path.join(PKG, "catalog_studies.parquet"), filters=[("study_accession", "in", list(projects))])
    st = (st[st.release_retired.isna()] if "release_retired" in st.columns else st).set_index("study_accession")
    papers = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "catalog", "study_papers.parquet"))
    papers = papers[(papers.relation == "own_data") & papers.study_accession.isin(projects)]
    wcols = ["study_accession", "sample_key", "sample_type", "release_retired"] + SELECTORS
    have = set(__import__("pyarrow.parquet", fromlist=["x"]).ParquetFile(os.path.join(PKG, "catalog_sample_metadata_wide.parquet")).schema_arrow.names)
    w = pd.read_parquet(os.path.join(PKG, "catalog_sample_metadata_wide.parquet"), columns=[c for c in wcols if c in have],
                        filters=[("study_accession", "in", list(projects))])
    w = w[w.release_retired.isna() & (w.sample_type.fillna("specimen") == "specimen")]
    w_by = {k: g for k, g in w.groupby("study_accession")}
    cache = os.path.expanduser("~/catalog/cache/llm/reread_r2026_40")
    llm = LLM("rubric", cache_dir=cache, ledger=os.path.join(out, "logs", f"ledger_{shard}.csv"), budget_usd=budget, pass_name="reread_r2026_40", max_concurrency=6)
    chk = LLM("audit", cache_dir=cache + "_check", ledger=os.path.join(out, "logs", f"ledger_check_{shard}.csv"),
              budget_usd=check_budget if check_budget is not None else budget, pass_name="reread_check_r2026_40", max_concurrency=6)
    T, V, reqs, keys, reading = {}, {}, [], [], {}
    for acc in projects:
        if acc not in st.index:
            reading[acc] = {"status": "not_in_catalog"}
            continue
        tx = CP.project_texts(acc, CP.load_dossier(acc), st.loc[acc].to_dict(), papers)
        if tx.get("mode") not in ("fulltext", "abstract"):
            reading[acc] = {"status": "no_paper_text", "text_mode": tx.get("mode")}
            continue
        lines, sel = sample_variables(w_by.get(acc, w.iloc[0:0]))
        T[acc], V[acc] = tx, sel
        prompt = tx["prompt"] + "\n\nSAMPLE VARIABLES IN THE CATALOG (field [samples with a value]: value x samples):\n" + (
            "\n".join(lines) if lines else "  (none with 2-30 distinct values)")
        reqs.append({"system": SYSTEM, "prompt": prompt, "tools": [TOOL], "tool_choice": {"type": "tool", "name": "reread"},
                     "max_tokens": 3000, "thinking": NOTHINK})
        keys.append(acc)
    try:
        res = llm(reqs)
    except BudgetExceeded as e:
        res = []
        print("budget:", e)
    dets, facts, exps, subs, creqs, ckeys = [], [], [], [], [], []
    pending = {}
    for i, acc in enumerate(keys):
        r = res[i] if i < len(res) else {"status": "not_run"}
        tx = T[acc]
        base = {"status": "ok", "text_mode": tx["mode"], "pmcid": tx.get("pmcid"), "pmids": ";".join(map(str, tx.get("pmids") or [])), "model": r.get("model")}
        if r.get("status") not in ("ok", "cached") or not isinstance(r.get("tool_use"), dict):
            reading[acc] = dict(base, status=r.get("status_original") or r.get("status") or "error")      # refusals recorded, never re-sent
            continue
        v, segs = r["tool_use"], tx["segs"]
        alltext = " ".join(segs.values())
        src_of = lambda name, _m=tx["mode"]: {"paper_methods": "paper.fulltext.methods", "paper_abstract": "paper.fulltext.abstract" if _m == "fulltext" else "paper.abstract",
                               "paper_data_availability": "paper.fulltext.data_availability", "archive_description": "archive.description",
                               "archive_title": "archive.title"}.get(name, "paper")
        pid = tx.get("pmcid") or ";".join(map(str, tx.get("pmids") or []))
        n = {"lifestyle_proposed": 0, "exposures": 0, "facility": 0, "kits_platform": 0, "period_proposed": 0, "dropped_no_verbatim_quote": 0}
        items = []
        for it in (v.get("lifestyle") or []):
            if not isinstance(it, dict) or it.get("code") not in LIFESTYLES:
                continue
            seg, passage = locate(it.get("quote"), segs)
            if not seg or seg == "archive_title":
                n["dropped_no_verbatim_quote"] += 1
                subs.append({"study_accession": acc, "kind": "lifestyle", "code": it.get("code"), "applies_to": it.get("applies_to"),
                             "group_as_named": it.get("group_as_named"), "outcome": "dropped: quote not verbatim in the text read", "quote": None, "source": pid})
                continue
            n["lifestyle_proposed"] += 1
            if it.get("applies_to") == "subgroup":
                sel_ = V[acc].get(it.get("selector_field") or "none")
                vals_ = [str(s_) for s_ in (it.get("selector_values") or [])]
                if not sel_ or not vals_ or any(s_ not in sel_ for s_ in vals_) or len(vals_) >= len(sel_):
                    subs.append({"study_accession": acc, "kind": "lifestyle", "code": it["code"], "applies_to": "subgroup", "group_as_named": it.get("group_as_named"),
                                 "selector_field": it.get("selector_field"), "selector_values": json.dumps(it.get("selector_values") or []),
                                 "quote": str(it.get("quote"))[:500], "source": pid, "check": "not_checked", "n_samples": 0,
                                 "outcome": "not committed: no sample variable selects the subgroup"})
                    continue
            items.append(dict(kind="lifestyle", it=it, seg=seg, passage=passage))
        sp = v.get("sampling_period")
        if isinstance(sp, dict) and PERIOD.match(str(sp.get("start") or "")) and PERIOD.match(str(sp.get("end") or "")) and str(sp["start"]) <= str(sp["end"]):
            seg, passage = locate(sp.get("quote"), segs)
            if seg and not seg.startswith("archive"):
                n["period_proposed"] += 1
                items.append(dict(kind="period", it=sp, seg=seg, passage=passage))
            else:
                n["dropped_no_verbatim_quote"] += 1
        for it in (v.get("exposures") or []):
            if isinstance(it, dict) and it.get("concept") in EXPOSURE and CP.quote_ok(it.get("quote"), alltext) and not PP.PLACEHOLDER.match(str(it.get("summary") or "")):
                exps.append({"study_accession": acc, "concept": it["concept"], "scale": it.get("scale"), "summary": str(it.get("summary"))[:300],
                             "quote": str(it.get("quote"))[:500], "source": pid})
                n["exposures"] += 1
            elif isinstance(it, dict):
                n["dropped_no_verbatim_quote"] += 1
        for it in (v.get("facility") or []):
            if not isinstance(it, dict):
                continue
            name = str(it.get("name_raw") or "").strip()
            q_ = str(it.get("quote") or "")
            if AMPLICON.search(q_) and not SHOTGUN.search(q_):
                n["dropped_facility_amplicon_sentence"] = n.get("dropped_facility_amplicon_sentence", 0) + 1
                continue
            seg_f, passage_f = locate(it.get("quote"), segs)
            if name and not MAKERS.match(name) and not GENERIC_FAC.match(name) and seg_f and not seg_f.startswith("archive") and name_in_quote(name, it.get("quote")):
                items.append(dict(kind="facility", it=it, seg=seg_f, passage=passage_f))
                n["facility_proposed"] = n.get("facility_proposed", 0) + 1
            elif name:
                n["dropped_no_verbatim_quote"] += 1
        for f in ("extraction_kit", "library_kit", "platform"):
            it = v.get(f)
            if isinstance(it, dict) and str(it.get("name_raw") or "").strip():
                name = str(it["name_raw"]).strip()
                if CP.quote_ok(it.get("quote"), alltext) and name_in_quote(name, it.get("quote")):
                    facts.append({"study_accession": acc, "fact": f, "value_raw": name[:200], "kind": None, "country": None,
                                  "quote": str(it.get("quote"))[:500], "source": pid, "text_mode": tx["mode"]})
                    n["kits_platform"] += 1
                else:
                    n["dropped_no_verbatim_quote"] += 1
        reading[acc] = dict(base, **n)
        if items:
            body = f"PROJECT {acc}: {st.loc[acc].get('study_title')}\n\n"
            for j, x in enumerate(items):
                it = x["it"]
                if x["kind"] == "lifestyle":
                    scope = "all sampled people" if it.get("applies_to") == "all" else f"subgroup '{it.get('group_as_named')}'"
                    selv = ""
                    if it.get("applies_to") == "subgroup" and it.get("selector_field") in V[acc]:
                        vals_ = [str(s_) for s_ in (it.get("selector_values") or []) if str(s_) in V[acc][it["selector_field"]]]
                        n_sel, spr = spread(V[acc], it["selector_field"], vals_, w_by.get(acc, w.iloc[0:0]))
                        allv = {k_: len(v_) for k_, v_ in V[acc][it["selector_field"]].items()}
                        selv = (f"\n  selector: sample variable {it['selector_field']} with values {vals_} -> {n_sel} samples "
                                f"(all values of that variable with sample counts: {allv})\n  the selected samples spread over the other variables as: {spr or '(no other variable)'}")
                    body += f"ITEM {j} — lifestyle = {it['code']} for {scope}{selv}\n  PASSAGE: …{x['passage']}…\n\n"
                elif x["kind"] == "facility":
                    body += f"ITEM {j} — facility = {it['name_raw']} (said to have run the shotgun sequencing)\n  PASSAGE: …{x['passage']}…\n\n"
                else:
                    body += f"ITEM {j} — sampling_period = {it['start']} to {it['end']}\n  PASSAGE: …{x['passage']}…\n\n"
            creqs.append({"system": CHECK_SYSTEM, "prompt": body, "tools": [CHECK_TOOL], "tool_choice": {"type": "tool", "name": "check"},
                          "max_tokens": 800, "thinking": NOTHINK})
            ckeys.append(acc)
            pending[acc] = (items, src_of, pid)
    try:
        cres = chk(creqs)
    except BudgetExceeded as e:
        cres = []
        print("budget:", e)
    for i, acc in enumerate(ckeys):
        items, src_of, pid = pending[acc]
        r = cres[i] if i < len(cres) else {"status": "not_run"}
        ok = r.get("status") in ("ok", "cached") and isinstance(r.get("tool_use"), dict)
        verd = {int(x["id"]): x for x in (r["tool_use"].get("items") or []) if isinstance(x, dict) and str(x.get("id", "")).lstrip("-").isdigit()} if ok else {}
        reading[acc]["check_status"] = "ok" if ok else (r.get("status_original") or r.get("status"))
        tmode = T[acc]["mode"]
        route0 = "R3" if tmode == "fulltext" else "R4"
        whole = [(j, x) for j, x in enumerate(items) if x["kind"] == "lifestyle" and x["it"].get("applies_to") == "all" and verd.get(j, {}).get("verdict") == "supported"]
        conflict = len({x["it"]["code"] for _, x in whole}) > 1
        kept = 0
        n0 = len(dets)
        for j, x in enumerate(items):
            it, vd = x["it"], verd.get(j, {})
            sup = vd.get("verdict") == "supported"
            src = src_of(x["seg"])
            route = "R4" if src.startswith("archive") else route0
            conf = {"paper.fulltext.methods": 0.7, "paper.fulltext.abstract": 0.65, "paper.fulltext.data_availability": 0.6, "archive.description": 0.45}.get(src, 0.5)
            if src.startswith("archive") and x["kind"] == "period":
                continue                                             # a sampling period is taken from the paper only
            q = str(it.get("quote"))[:500]
            if x["kind"] == "facility":
                if sup:
                    facts.append({"study_accession": acc, "fact": "sequencing_facility", "value_raw": str(it["name_raw"]).strip()[:200], "kind": it.get("kind"),
                                  "country": it.get("country"), "quote": q, "source": pid, "text_mode": tmode})
                    reading[acc]["facility"] = reading[acc].get("facility", 0) + 1
                continue
            if x["kind"] == "period":
                if sup:
                    val = it["start"] if it["start"] == it["end"] else f"{it['start']}/{it['end']}"
                    dets.append(row(None, "collection_date", acc, val, val, conf - 0.1, src, pid, route, q, scope="study_all",
                                    note="sampling period stated for the cohort; Opus 5 check: supported"))
                    reading[acc]["period_committed"] = 1
                continue
            rec = {"study_accession": acc, "kind": "lifestyle", "code": it["code"], "applies_to": it.get("applies_to"), "group_as_named": it.get("group_as_named"),
                   "selector_field": it.get("selector_field"), "selector_values": json.dumps(it.get("selector_values") or []), "quote": q, "source": pid,
                   "check": vd.get("verdict") or "not_checked", "n_samples": 0}
            if not sup:
                rec["outcome"] = "not committed: the check did not support it" if vd else "not committed: not checked"
            elif it.get("applies_to") == "all":
                if conflict:
                    rec["outcome"] = "not committed: two different codes for the whole cohort"
                else:
                    dets.append(row(None, "lifestyle", acc, it.get("detail") or it["code"], it["code"], conf, src, pid, route, q, scope="study_all",
                                    note="way of life stated for the sampled people; Opus 5 check: supported"))
                    rec["outcome"] = "committed for the cohort"
                    kept += 1
            else:
                sel = V[acc].get(it.get("selector_field") or "none")
                vals = [str(s_) for s_ in (it.get("selector_values") or [])]
                if not sel or not vals or any(s_ not in sel for s_ in vals) or len(vals) >= len(sel):
                    rec["outcome"] = "not committed: no sample variable selects the subgroup"
                elif vd.get("selector_ok") is not True:
                    rec["outcome"] = "not committed: the check did not confirm the selector"
                else:
                    sks = sorted({s_ for val in vals for s_ in sel[val]})
                    for sk in sks:
                        dets.append(row(sk, "lifestyle", acc, it.get("detail") or it["code"], it["code"], conf, src, pid, route, q,
                                        note=f"stated for the subgroup '{it.get('group_as_named')}'; assigned through {it['selector_field']} in {vals}; Opus 5 check: supported"))
                    rec["outcome"] = "committed per sample"
                    rec["n_samples"] = len(sks)
                    kept += 1
            subs.append(rec)
        # a sample must not receive two different lifestyle codes from this pass: such samples get none
        mine = [d_ for d_ in dets[n0:] if d_["field_name"] == "lifestyle" and d_["scope"] != "study_all"]
        codes_by = {}
        for d_ in mine:
            codes_by.setdefault(d_["sample_key"], set()).add(d_["value_normalized"])
        bad = {k_ for k_, c_ in codes_by.items() if len(c_) > 1}
        if bad:
            dets[n0:] = [d_ for d_ in dets[n0:] if not (d_["field_name"] == "lifestyle" and d_["sample_key"] in bad)]
            reading[acc]["samples_with_two_codes_dropped"] = len(bad)
        reading[acc]["lifestyle_committed"] = kept
    rd = pd.DataFrame([dict(study_accession=k, **v) for k, v in reading.items()])
    rd.to_parquet(os.path.join(out, f"reread_reading_shard_{shard}.parquet"), index=False)
    pd.DataFrame(dets, columns=DET_COLS).to_parquet(os.path.join(out, f"reread_determinations_shard_{shard}.parquet"), index=False)
    pd.DataFrame(facts, columns=["study_accession", "fact", "value_raw", "kind", "country", "quote", "source", "text_mode"]).to_parquet(
        os.path.join(out, f"reread_facts_shard_{shard}.parquet"), index=False)
    pd.DataFrame(exps, columns=["study_accession", "concept", "scale", "summary", "quote", "source"]).to_parquet(
        os.path.join(out, f"reread_exposures_shard_{shard}.parquet"), index=False)
    pd.DataFrame(subs).to_parquet(os.path.join(out, f"reread_subgroups_shard_{shard}.parquet"), index=False)
    dd = pd.DataFrame(dets, columns=DET_COLS)
    summ = {"shard": shard, "projects": len(projects), "status": rd.status.value_counts().to_dict() if len(rd) else {},
            "text_mode": rd.text_mode.value_counts().to_dict() if "text_mode" in rd else {},
            "lifestyle_cohort_rows": int(((dd.field_name == "lifestyle") & (dd.scope == "study_all")).sum()),
            "lifestyle_sample_rows": int(((dd.field_name == "lifestyle") & (dd.scope != "study_all")).sum()),
            "collection_period_rows": int((dd.field_name == "collection_date").sum()),
            "facility_items": int(sum(1 for f in facts if f["fact"] == "sequencing_facility")),
            "kit_platform_items": int(sum(1 for f in facts if f["fact"] != "sequencing_facility")), "exposure_items": len(exps),
            "first": llm.summary(), "check": chk.summary()}
    json.dump(summ, open(os.path.join(out, "logs", f"reread_summary_{shard}.json"), "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in summ.items() if k not in ("first", "check")}, default=str))
    return summ


def merge(out):
    for name in ("reading", "determinations", "facts", "exposures", "subgroups"):
        fs = sorted(glob.glob(os.path.join(out, f"reread_{name}_shard_*.parquet")))
        parts = [pd.read_parquet(f) for f in fs]
        parts = [p for p in parts if len(p)]
        if parts:
            pd.concat(parts, ignore_index=True).to_parquet(os.path.join(out, f"reread_{name}_all.parquet"), index=False)
        print(name, len(fs), "shards", sum(len(p) for p in parts), "rows")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects"); ap.add_argument("--shard", type=int, default=0); ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--budget", type=float, default=12.0)
    ap.add_argument("--check-budget", type=float, default=None); ap.add_argument("--out", default=os.path.join(ROOT, "data", "inputs", "gut", "reread"))
    ap.add_argument("--merge", action="store_true")
    a = ap.parse_args(argv)
    if a.merge:
        return merge(a.out)
    pr = pd.read_csv(a.projects, dtype=str)
    pr = pr[pr.shard.astype(int) == a.shard] if "shard" in pr.columns else pr.iloc[a.shard::a.nshards]
    projects = list(dict.fromkeys(pr.study_accession))
    if a.limit:
        projects = projects[:a.limit]
    return run(projects, a.out, a.budget, a.check_budget, a.shard, host=globals().get("host"))


if __name__ == "__main__":
    main(globals().get("ARGS"))
