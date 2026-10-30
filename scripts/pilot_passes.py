#!/usr/bin/env python
"""pilot_passes.py — the model passes of the large update, run end to end on a project list (R2026.30 pilot).

Passes (all through catalog.llm_backend: on-disk cache, ledger, budget, refusals recorded and never re-sent):
  classify    scope, nucleic acid, body sites, processing, sample types, study shape        (rubric role, 2 readings)
  link_audit  for each candidate paper: generated / reused / unrelated, from title + abstract (screen role)
  fulltext    one combined reading of the generating paper's abstract + methods + data availability: sequencing and
              RNA methods, metadata access, cohort facts, study arms                         (rubric role)
  samples     per-sample values for the dossier's example samples                             (rubric role)
Paper text and model answers stay in files; the script prints only counts.

usage (inside a Claude Science python kernel, so `host` exists):
    import runpy; runpy.run_path("scripts/pilot_passes.py", init_globals={"host": host, "ARGS": [...]}, run_name="__main__")
    ARGS = ["--projects", ids.txt, "--dossiers", DIR, "--out", OUT, "--budget", "30", "--passes", "classify,link_audit,fulltext,samples"]
"""
import argparse, glob, json, os, re, sys, xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catalog import models as M  # noqa: E402
from catalog.llm_backend import LLM, BudgetExceeded  # noqa: E402

M.set_host(globals().get("host"))
CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))

SITES = ["gut_stool", "oral", "nasal_nasopharyngeal", "respiratory_lower", "skin", "vaginal_urogenital", "blood_tissue", "milk",
         "eye_ear", "other_site"]
OUT_REASONS = ["animal_host", "environmental", "amplicon", "human_genome", "human_tissue_rna", "single_cell", "culture_enriched",
               "simulated", "isolate_genomes", "virus_genome_surveillance", "in_vitro", "other"]
PROC = ["none", "virome_vlp", "host_depletion", "targeted_capture", "wga_mda", "sorted_fraction", "rrna_depletion", "other"]
STYPES = ["specimen", "negative_control", "mock_community", "positive_control", "other"]
SHAPES = ["population_survey", "cross_sectional_groups", "case_control", "cohort_longitudinal", "single_arm_intervention", "rct",
          "crossover", "non_randomised_intervention", "method_or_benchmark", "case_series", "unclear"]
NOTHINK = {"type": "disabled"}

SCOPE_RULES = """The catalog holds human-associated microbial communities sequenced by DNA or RNA SHOTGUN sequencing, at any body site,
plus sequenced mock / standard communities used as benchmarks. IN: stool, oral, nasal, airway, skin, vaginal/urogenital, blood
or tissue, milk, eye/ear samples of people; clinical DNA or RNA mNGS of patient samples (incl. virus-aimed); virome (VLP) and
host-depleted or captured libraries of human samples. OUT: non-human hosts (incl. humanized / gnotobiotic animals and FMT
recipients), wastewater, environmental, amplicon sequencing of any gene (16S, ITS, other) even when filed as WGS / RNA-Seq,
human genome / exome / methylation, human tissue or cell RNA-seq without a microbial focus, single-cell genomes, culture-
enriched metagenomes, simulated data, isolate genomes, virus genome surveillance (e.g. SARS-CoV-2 ARTIC). 'partly' = some
samples or runs are in scope and others not. Archive labels are often wrong: the read check (share of reads hitting 16S/23S
rRNA genes, dominant read starts) is direct evidence about the library.

body sites: throat / oropharyngeal / tonsil swabs are oral (as in the Human Microbiome Project); nasal, nasopharyngeal and
sinus samples are nasal_nasopharyngeal; sputum, BAL, tracheal aspirate and lung are respiratory_lower; urine and urethral or
cervical samples are vaginal_urogenital; cerebrospinal fluid, brain and other organs without their own code are other_site.
nucleic_acid: 'both' when the project deposits DNA libraries (WGS / METAGENOMIC) AND RNA libraries (RNA-Seq / METATRANSCRIPTOMIC /
TRANSCRIPTOMIC / VIRAL RNA) of its samples, e.g. paired DNA and RNA mNGS.
processing: WET-LAB steps before sequencing only. Computational removal of human reads after sequencing is NOT host_depletion;
host_depletion means a kit or step that removes human cells / DNA before library preparation (saponin, QIAamp DNA Microbiome,
MolYsis, NEBNext Microbiome enrichment, benzonase, CRISPR depletion). Virus-like-particle purification (filtration, nuclease,
CsCl) is virome_vlp, not host_depletion. List EVERY step: VLP preparations are usually followed by whole-genome / random
amplification (MDA, phi29, SISPA, WTA) -> also wga_mda. Use 'none' when no such step is evident.
study_shape — take the FIRST that applies:
 1 method_or_benchmark: the aim is a method, protocol, sequencing or diagnostic performance (incl. clinical mNGS diagnostic-yield
   studies, kit comparisons, mock communities), not a biological comparison of people;
 2 rct / crossover / single_arm_intervention / non_randomised_intervention: the study assigns an intervention (randomised
   parallel arms / randomised sequence / everyone receives it, before vs after / non-random intervention vs control groups);
 3 cohort_longitudinal: the same people sampled repeatedly over time, no assigned intervention;
 4 case_control: people with a condition compared with people without it;
 5 cross_sectional_groups: other groups compared at one time (lifestyle, geography, age, exposure);
 6 case_series: patients with a condition only, no comparison group;
 7 population_survey: one population, no comparison groups; 8 unclear."""


def tool(name, props, required):
    return {"name": name, "description": f"Return the {name} result.",
            "input_schema": {"type": "object", "properties": props, "required": required}}


CLASSIFY_TOOL = tool("classify", {
    "in_scope": {"type": "string", "enum": ["yes", "no", "partly"]},
    "out_reason": {"type": "string", "enum": OUT_REASONS + ["none"]},
    "nucleic_acid": {"type": "string", "enum": ["dna", "rna", "both", "unclear"]},
    "body_sites": {"type": "array", "items": {"type": "string", "enum": SITES}},
    "processing": {"type": "array", "items": {"type": "string", "enum": PROC}},
    "sample_types_present": {"type": "array", "items": {"type": "string", "enum": STYPES}},
    "study_shape": {"type": "string", "enum": SHAPES},
    "confidence": {"type": "number"},
    "evidence": {"type": "string", "description": "one or two sentences naming the decisive evidence"}},
    ["in_scope", "nucleic_acid", "body_sites", "study_shape", "confidence", "evidence"])

LINK_TOOL = tool("link_audit", {"papers": {"type": "array", "items": {"type": "object", "properties": {
    "paper_id": {"type": "string"}, "relation": {"type": "string", "enum": ["generated", "reused", "unrelated", "unclear"]},
    "reason": {"type": "string"}}, "required": ["paper_id", "relation"]}}}, ["papers"])

NULLSTR = {"type": ["string", "null"]}
import yaml as _yaml
LIFESTYLES = list((_yaml.safe_load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "vocab", "lifestyle.yaml"))) or {}).get("codes", {}).keys()) or [
    "hunter_gatherer", "pastoralist", "traditional_agriculturalist", "rural_non_industrialized", "urban_industrialized", "transitional_or_migrant",
    "isolated_religious_community", "indigenous_community", "nomadic", "vegetarian_or_vegan", "athlete", "military", "spaceflight_or_analog",
    "institutionalized", "extreme_environment", "other_lifestyle", "unknown"]
RECRUIT = ["community_household", "school_or_daycare", "outpatient_clinic", "hospital_inpatient", "intensive_care", "maternity_or_birth_hospital",
           "care_home", "workplace", "other", "not_stated"]
SETTLE = ["urban", "peri_urban", "rural", "village", "informal_settlement", "refugee_or_camp", "remote_or_isolated", "mixed", "not_stated"]
PLACE_LEVELS = ["admin1", "district_or_county", "city_or_town", "village_or_community", "facility"]
EXPOSURE = ["sanitation", "drinking_water", "handwashing", "floor_or_housing", "household_size_or_crowding", "siblings", "daycare",
            "livestock_or_animals", "pets", "farm_living", "socioeconomic", "breastfeeding", "diarrhoea", "parasite_or_pathogen_testing",
            "deworming", "travel", "wash_intervention"]
SETTING_SCHEMA = {"type": "object", "properties": {
    "recruitment_setting": {"type": "array", "items": {"type": "string", "enum": RECRUIT}},
    "settlement_type": {"type": "array", "items": {"type": "string", "enum": SETTLE}},
    "places": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "level": {"type": "string", "enum": PLACE_LEVELS}, "country": NULLSTR,
        "is_residence": {"type": "string", "enum": ["participants_live_here", "study_site_only", "unclear"]}}, "required": ["name", "level"]}},
    "lifestyle": {"type": "string", "enum": LIFESTYLES},
    "exposure_variables": {"type": "array", "items": {"type": "object", "properties": {
        "concept": {"type": "string", "enum": EXPOSURE},
        "scale": {"type": "string", "enum": ["per_person", "per_group", "whole_cohort"]},
        "where": {"type": "string", "enum": ["methods_text", "paper_table", "supplementary_table", "results_text"]},
        "summary": {"type": "string", "description": "what is recorded, e.g. 'number of older siblings per child' or 'all households used pit latrines'"},
        "quote": {"type": "string"}}, "required": ["concept", "scale", "summary", "quote"]}}},
    "required": ["recruitment_setting", "settlement_type", "places", "lifestyle", "exposure_variables"]}
SETTING_RULES = (" setting describes where and how participants live and were recruited, from the text only. recruitment_setting: where people "
    "were enrolled (community_household = from homes/communities; maternity_or_birth_hospital = recruited at birth). settlement_type: only when "
    "the text says so (e.g. 'rural villages', 'urban slum', 'urban vs rural residence'); not_stated otherwise, never inferred from the country. "
    "places: named places below country level (region, district, city, village, or a facility); is_residence = participants_live_here when the "
    "text says participants live there, study_site_only for a hospital or lab location. lifestyle: a code only when the text describes the "
    "population's way of life (e.g. hunter-gatherers, rural subsistence farmers, industrialized urban residents); unknown otherwise. "
    "exposure_variables: MOST PAPERS RECORD NONE — then return an empty list. Never add an item to say something is not reported, not "
    "applicable or absent. Add an item only when the paper records, for its human participants, one of these exact concepts: "
    "sanitation = toilet, latrine or sewage facilities; drinking_water = water source or treatment; handwashing = handwashing or hygiene practice "
    "as an exposure (not instructions before sampling); floor_or_housing = floor, wall or roof material, housing type; household_size_or_crowding "
    "= people or children per household or room, residence type (home, care home); siblings = siblings or birth order; daycare = daycare or "
    "nursery attendance; livestock_or_animals = contact with livestock or other animals; pets = pet ownership or contact; farm_living = living or "
    "working on a farm; socioeconomic = education, income, occupation, wealth or asset index, low-income area (NOT race or ethnicity); "
    "breastfeeding; diarrhoea = diarrhoea episodes, symptoms or an exclusion based on diarrhoea; parasite_or_pathogen_testing = testing "
    "participants for enteric parasites or enteric pathogens (helminths, protozoa such as Giardia or Cryptosporidium, enteric bacteria or "
    "viruses), not other infections; deworming = anthelmintic drugs only (NOT antibiotics); travel = travel only (NOT antibiotics); "
    "wash_intervention = a water, sanitation or hygiene intervention given to participants. Antibiotic use is recorded elsewhere — never list it "
    "here. scale: per_person (a value for each participant, e.g. a questionnaire item or a column), per_group (summary by arm/group, e.g. "
    "Table 1 percentages), whole_cohort (applies to everyone, e.g. an inclusion or exclusion criterion). Do not list laboratory uses of these "
    "words or statements about other studies. Animal or non-human studies: empty list. Each item needs a verbatim quote from the text.")
PLACEHOLDER = re.compile(r"(?i)^\s*(not[ _]?(reported|applicable|stated|recorded)|n/?a|none|no (explicit|exposure|data))")


_LS_TERMS = {k: [t.lower() for t in (c or {}).get("match_terms", [])] for k, c in ((_yaml.safe_load(open(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config", "vocab", "lifestyle.yaml"))) or {}).get("codes", {}) or {}).items()}


def coerce_setting(st):
    """the model sometimes returns list items as plain strings: places 'Kampala' -> {'name': 'Kampala'}; non-dict exposure items dropped."""
    if not isinstance(st, dict):
        return st
    st = dict(st)
    st["places"] = [x if isinstance(x, dict) else {"name": str(x), "level": None} for x in (st.get("places") or []) if x]
    st["exposure_variables"] = [x for x in (st.get("exposure_variables") or []) if isinstance(x, dict)]
    for k in ("recruitment_setting", "settlement_type"):
        if isinstance(st.get(k), str):
            st[k] = [st[k]]
    return st


def guard_setting(st, text, countries=()):
    """Deterministic checks on a setting block against the paper text it was read from.
    lifestyle: kept only when one of the code's match terms (config/vocab/lifestyle.yaml) occurs in the text, else 'unknown'.
    places: names that are a country (or equal the place's own country field) are dropped.
    Returns (guarded copy, list of changes)."""
    if not isinstance(st, dict):
        return st, []
    st, changes, low = coerce_setting(json.loads(json.dumps(st))), [], (text or "").lower()
    ls = st.get("lifestyle")
    terms = [t for t in _LS_TERMS.get(ls, []) if not (ls == "urban_industrialized" and t == "urban")]   # 'urban' alone also describes informal settlements
    if ls and ls not in ("unknown", "other_lifestyle") and not any(re.search(r"\b" + re.escape(t) + r"\b", low) for t in terms):
        changes.append(f"lifestyle {ls} -> unknown (no match term in text)"); st["lifestyle"] = "unknown"
    cn = {c.lower() for c in countries}
    keep = []
    for x in st.get("places") or []:
        n = str(x.get("name") or "").strip().lower()
        if n in cn or (x.get("country") and n == str(x["country"]).strip().lower()):
            changes.append(f"place '{x.get('name')}' dropped (a country)")
        else:
            keep.append(x)
    st["places"] = keep
    ev = []
    for x in st.get("exposure_variables") or []:
        rx = EXPOSURE_TERMS.get(x.get("concept"))
        if rx and not re.search(rx, str(x.get("quote") or ""), re.I):
            changes.append(f"exposure {x.get('concept')} dropped (quote lacks a {x.get('concept')} term)")
        else:
            ev.append(x)
    st["exposure_variables"] = ev
    return st, changes


# an exposure item is kept only when its quote contains a term of its concept (pilot round 2: removed exactly the 4 wrong of 13 items)
EXPOSURE_TERMS = {
    "sanitation": r"latrine|toilet|sanitation|defecat|sewer|sewage|septic",
    "drinking_water": r"drinking|water source|source of water|tap water|well water|borehole|piped|bottled|boil|water treatment|surface water|river",
    "handwashing": r"hand ?wash|hand hygiene|wash(?:ing)? hands|soap",
    "floor_or_housing": r"floor|roof|wall|housing|house type|dwelling",
    "household_size_or_crowding": r"household|crowd|per room|rooms|resid|living arrangement|lives? with|care home|nursing home|assisted living",
    "siblings": r"sibling|brother|sister|birth order",
    "daycare": r"day ?care|childcare|child care|nursery|kindergarten|cr[eè]che|preschool",
    "livestock_or_animals": r"livestock|cattle|cow|poultry|chicken|goat|sheep|pig|animal",
    "pets": r"\bpets?\b|dog|\bcats?\b",
    "farm_living": r"farm|agricultur|rural",
    "socioeconomic": r"socio|income|wealth|asset|poverty|educat|school|occupation|employ|social class|deprivation|students?\b",
    "breastfeeding": r"breast|human milk|formula",
    "diarrhoea": r"diarrh|loose|watery stool|gastroenteritis",
    "parasite_or_pathogen_testing": r"helminth|ascaris|trichuris|hookworm|schistosom|giardia|cryptospor|entamoeba|blastocystis|parasit|\bova\b|kato|enteric|rotavirus|norovirus|salmonella|shigella|campylobacter|e\.? coli|clostridi|stool (?:culture|test|microscopy)",
    "deworming": r"deworm|albendazole|mebendazole|anthelmint|praziquantel|ivermectin",
    "travel": r"travel",
    "wash_intervention": r"\bWASH\b|water, sanitation|sanitation|handwashing|water treatment",
}


def clean_setting(v):
    """drop placeholder exposure items the model sometimes adds despite the instructions."""
    st = (v or {}).get("setting") if isinstance(v, dict) else None
    if isinstance(st, str):                      # the model occasionally returns the object JSON-encoded as a string
        try:
            st = v["setting"] = json.loads(st)
        except ValueError:
            st = None
    if isinstance(st, dict) and isinstance(st.get("exposure_variables"), list):
        keep = [x for x in st["exposure_variables"] if isinstance(x, dict) and x.get("quote") and not PLACEHOLDER.search(str(x.get("quote")))
                and not PLACEHOLDER.search(str(x.get("summary") or ""))]
        st["n_dropped_placeholders"] = len(st["exposure_variables"]) - len(keep)
        st["exposure_variables"] = keep
    return v
FULLTEXT_TOOL = tool("fulltext", {
    "paper_describes_project": {"type": "string", "enum": ["yes", "no", "unclear"]},
    "nucleic_acids_sequenced": {"type": "string", "enum": ["dna", "rna", "both", "unclear"],
                                "description": "which nucleic acids this paper's libraries were made from (both = DNA and RNA libraries, e.g. DNA and RNA mNGS)"},
    "sequencing": {"type": "object", "properties": {k: NULLSTR for k in [
        "platform", "read_layout", "preservation", "extraction_kit", "library_kit", "host_depletion_wetlab", "host_read_removal_insilico",
        "negative_control", "positive_control"]}},
    "rna_methods": {"type": ["object", "null"], "properties": {k: NULLSTR for k in [
        "preservation", "extraction_kit", "dnase_treatment", "rrna_depletion", "host_rna_depletion", "poly_a_selection",
        "library_kit", "strandedness"]} | {"target": {"type": ["string", "null"], "enum": ["whole_community", "virus_enriched", "dual_host_microbe", "targeted_amplicon", None]}}},
    "processing": {"type": "array", "items": {"type": "string", "enum": PROC}},
    "metadata_access": {"type": "string", "enum": ["open", "on_request", "application_required", "partly_application", "not_stated"]},
    "metadata_access_quote": NULLSTR,
    "cohort": {"type": "object", "properties": {
        "countries": {"type": "array", "items": {"type": "string"}}, "age_description": NULLSTR,
        "health_conditions": {"type": "array", "items": {"type": "string"}}, "study_shape": {"type": "string", "enum": SHAPES},
        "arms": {"type": "array", "items": {"type": "string"}}, "n_participants": {"type": ["integer", "null"]}}},
    "setting": SETTING_SCHEMA,
    "quotes": {"type": "object", "description": "field -> verbatim quote of <= 25 words supporting it"}},
    ["paper_describes_project", "sequencing", "metadata_access", "cohort", "setting"])

SAMPLE_PROPS = {k: NULLSTR for k in ["sample", "body_site_detail", "age", "age_unit", "sex", "health_condition", "country",
                                     "subject_id", "timepoint"]} | {
    "body_site": {"type": ["string", "null"], "enum": SITES + [None]}, "sample_type": {"type": ["string", "null"], "enum": STYPES + [None]}}
SAMPLES_TOOL = tool("samples", {"samples": {"type": "array", "items": {"type": "object", "properties": SAMPLE_PROPS | {
    "sources": {"type": "object"}}, "required": ["sample"]}}}, ["samples"])


# ------------------------------------------------------------------ evidence helpers
def dossier_text(d, max_profile=35, max_runs=12):
    a = d["archive"]
    L = [f"Project {d['study_accession']}", *(f"{k}: {v}" for k, v in a.items() if v)]
    L.append("Read check (per checked run): " + "; ".join(
        f"{c['run']} {c.get('verdict')} 16S={c.get('frac16s')} 23S={c.get('frac23s')} top_read_start={c.get('top1_share')} len={c.get('len')}"
        for c in d["read_check"][:6]) if d["read_check"] else "Read check: none")
    L.append(f"BioSample attributes ({d['n_samples_with_attributes']} samples):")
    for p in d["attribute_profile"][:max_profile]:
        L.append(f"  {p['key']} [{p['n_samples']} samples, {p['n_distinct']} distinct]: " + "; ".join(f"{k} x{v}" for k, v in list(p["top_values"].items())[:5]))
    L.append("Runs: " + " | ".join(", ".join(f"{k}={v}" for k, v in r.items() if v is not None and k in (
        "run_accession", "library_name", "library_strategy", "library_source", "library_selection", "instrument_model", "base_count"))
        for r in d["runs_first40"][:max_runs]))
    return "\n".join(L)


def example_samples_text(d, n=3):
    return "\n".join(f"- {e['sample']}: " + "; ".join(f"{k}={v}" for k, v in e["attributes"].items()) for e in d["example_samples"][:n])


def jats_file(pmcid):
    if not pmcid:
        return None
    c = sorted(glob.glob(f"{CACHE}/pmc_oa/{pmcid}.*/{pmcid}.*.xml")) + sorted(glob.glob(f"{CACHE}/pmc_oa/{pmcid}.epmc/{pmcid}.xml"))
    return c[-1] if c else None


METH = re.compile(r"(?i)method|material|experimental|procedure|sample collection|sampl|sequenc|study design|participant|subject|cohort|patients|population|ethic")
DAV = re.compile(r"(?i)data availab|availability of data|accession|deposit|data access|code availab")


def paper_sections(pmcid, max_chars=40000):
    f = jats_file(pmcid)
    if not f:
        return None
    try:
        root = ET.parse(f).getroot()
    except ET.ParseError:
        return None
    txt = lambda e: re.sub(r"\s+", " ", "".join(e.itertext())).strip()
    title = root.findtext(".//article-title") or ""
    abstract = " ".join(txt(a) for a in root.iter("abstract"))
    meth, da = [], []
    for sec in root.iter("sec"):
        t = sec.findtext("title") or ""
        if DAV.search(t):
            da.append(f"[{t}] {txt(sec)}")
        elif METH.search(t) and not any(sec is x for x in []):
            meth.append(f"[{t}] {txt(sec)}")
    for n in root.iter("notes"):
        t = n.findtext("title") or n.get("notes-type") or ""
        if DAV.search(t) or DAV.search(n.get("notes-type") or ""):
            da.append(f"[{t}] {txt(n)}")
    # de-duplicate nested sections: keep the longest text that contains a shorter one
    meth = sorted(set(meth), key=len, reverse=True)
    kept = []
    for m in meth:
        if not any(m[len(m.split(']')[0]) + 2:][:200] in k for k in kept):
            kept.append(m)
    body = f"TITLE: {title}\nABSTRACT: {abstract}\nDATA AVAILABILITY: {' '.join(da)[:4000]}\nMETHODS: " + "\n".join(kept)
    return body[:max_chars]


def abstract_for(paper):
    """Europe PMC abstract for a pmid / doi (cached through find_papers' cache)."""
    import find_papers as FP
    q = f"EXT_ID:{paper['pmid']} AND SRC:MED" if paper.get("pmid") else (f'DOI:"{paper["doi"]}"' if paper.get("doi") else None)
    if not q:
        return ""
    r = FP.epmc(q, ttl_days=365)
    return (r[0].get("abstractText") or "")[:3000] if r else ""


def validate(obj, enums):
    """Replace values outside the controlled vocabularies (the model sometimes ignores the schema enum): body sites -> other_site,
    other enumerated fields -> None. Returns (clean, n_fixed)."""
    n = 0
    if not isinstance(obj, dict):
        return obj, 0
    out = dict(obj)
    for k, allowed in enums.items():
        v = out.get(k)
        if isinstance(v, list):
            fixed = [x if x in allowed else ("other_site" if allowed is SITES else None) for x in v]
            n += sum(1 for a, b in zip(v, fixed) if a != b)
            out[k] = sorted({x for x in fixed if x})
        elif v is not None and v not in allowed:
            n += 1
            out[k] = "other_site" if allowed is SITES else None
    return out, n


# ------------------------------------------------------------------ passes
def run(projects, dossier_dir, out, budget, passes, host=None):
    os.makedirs(out, exist_ok=True)
    if host is not None:
        M.set_host(host)
    led = os.path.join(out, "ledger.csv")
    rubric = lambda name, b: LLM("rubric", cache_dir=f"{CACHE}/llm/pilot_{name}", ledger=led, budget_usd=b, pass_name=f"pilot_{name}", max_concurrency=8)
    D = {p: json.load(open(os.path.join(dossier_dir, f"{p}.json"))) for p in projects if os.path.exists(os.path.join(dossier_dir, f"{p}.json"))}
    res = {p: {} for p in D}
    stats = {}
    share = {"classify": 0.25, "link_audit": 0.1, "fulltext": 0.4, "samples": 0.25}

    if "classify" in passes:
        llm = rubric("classify", budget * share["classify"])
        reqs, keys = [], []
        for p, d in D.items():
            titles = "\n".join(f"- [{x.get('methods')}] {x.get('title')} ({x.get('year')})" for x in d["papers"][:8])
            for rep in (1, 2):
                reqs.append({"system": "You classify sequencing projects for a human microbiome catalog. " + SCOPE_RULES + f"\n(reading {rep})",
                             "prompt": dossier_text(d) + "\n\nCandidate papers (titles only):\n" + titles + "\n\nExample samples:\n" + example_samples_text(d),
                             "tools": [CLASSIFY_TOOL], "tool_choice": {"type": "tool", "name": "classify"}, "max_tokens": 1500, "thinking": NOTHINK})
                keys.append((p, rep))
        try:
            out_ = llm(reqs)
        except BudgetExceeded as e:
            out_ = []; stats["classify_budget"] = str(e)
        for (p, rep), r in zip(keys, out_):
            v = r["tool_use"] if r["status"] in ("ok", "cached") else {"status": r.get("status_original") or r["status"]}
            v, nf = validate(v, {"body_sites": SITES, "processing": PROC, "sample_types_present": STYPES, "study_shape": SHAPES, "out_reason": OUT_REASONS + ["none"]})
            stats["vocab_fixed"] = stats.get("vocab_fixed", 0) + nf
            res[p][f"classify_{rep}"] = v
        stats["classify"] = llm.summary()

    if "link_audit" in passes:
        llm = LLM("screen", cache_dir=f"{CACHE}/llm/pilot_link", ledger=led, budget_usd=budget * share["link_audit"], pass_name="pilot_link", max_concurrency=8)
        reqs, keys = [], []
        for p, d in D.items():
            cands = [x for x in d["papers"] if x.get("paper_id")][:8]
            if not cands:
                continue
            body = "\n\n".join(f"paper_id={x['paper_id']} [{x.get('methods')}; linked to {x.get('n_projects_for_paper')} projects]\nTITLE: {x.get('title')}\nABSTRACT: {abstract_for(x)}" for x in cands)
            reqs.append({"system": "Decide for each candidate paper whether it GENERATED the sequencing data of this project (the authors collected and sequenced these samples), REUSED it (re-analysis, meta-analysis, method paper using public data), or is UNRELATED (a different study). A paper that cites the accession in its data-availability statement as its own deposit generated it.",
                         "prompt": "Project:\n" + "\n".join(f"{k}: {v}" for k, v in d["archive"].items() if v and k in ("study_title", "description_short", "center_name", "first_public_min", "n_samples")) + "\n\nCandidates:\n" + body,
                         "tools": [LINK_TOOL], "tool_choice": {"type": "tool", "name": "link_audit"}, "max_tokens": 1500, "thinking": NOTHINK})
            keys.append(p)
        try:
            out_ = llm(reqs)
        except BudgetExceeded as e:
            out_ = []; stats["link_budget"] = str(e)
        for p, r in zip(keys, out_):
            res[p]["link_audit"] = r["tool_use"] if r["status"] in ("ok", "cached") else {"status": r.get("status_original") or r["status"]}
        stats["link_audit"] = llm.summary()

    for rep, pname in ((1, "fulltext"), (2, "fulltext2")):
      if pname not in passes:
        continue
      if True:
        llm = rubric("fulltext", budget * share["fulltext"])
        reqs, keys = [], []
        for p, d in D.items():
            la = res[p].get("link_audit") or {}
            raw_papers = la.get("papers") or []
            if not isinstance(raw_papers, list):       # whole answer unusable (e.g. a string instead of an array)
                raw_papers = []
            la_papers = [x for x in raw_papers if isinstance(x, dict)]   # schema-violating entries carry no relation: drop, never repair
            if len(la_papers) < len(la.get("papers") or []) or not isinstance(la.get("papers") or [], list):
                stats["link_unusable_projects"] = stats.get("link_unusable_projects", 0) + 1
            gen = [x["paper_id"] for x in la_papers if x.get("relation") == "generated" and x.get("paper_id")]
            if la_papers:                              # audited: read only a paper judged to have generated the data
                order = [x for x in d["papers"] if x.get("paper_id") in gen]
            else:                                      # not audited (no candidates / refused / unusable shape): direct links only
                order = [x for x in d["papers"] if x.get("methods") != "title_similar"]
            paper = next((x for x in order if jats_file(x.get("pmcid"))), None)
            if not paper:
                res[p][pname] = {"status": "no_generating_paper_text" if (la_papers or order) else "no_paper"}
                continue
            text = paper_sections(paper["pmcid"])
            if not text:
                res[p][pname] = {"status": "unparsable"}; continue
            reqs.append({"system": "You read the methods of the paper that generated a human microbiome sequencing project and record how the samples were collected and sequenced, and what the paper states about the cohort. Record only what the text states; use null or 'not_reported' otherwise. Quotes verbatim, at most 25 words. rna_methods only for RNA libraries (metatranscriptomes, RNA viromes, RNA mNGS); null for DNA-only studies. metadata_access describes the PARTICIPANT METADATA (not the reads): application_required = a formal application or data-access agreement (dbGaP, EGA, a cohort's data access committee, a named restricted-access procedure); on_request = 'available from the authors on (reasonable) request' only; partly_application = some participant data public and some behind an application; open = metadata stated to be public (in the archive or supplements); not_stated otherwise. sequencing.host_depletion_wetlab = a kit or step that removes human cells/DNA before library preparation (null if none; VLP filtration / nuclease steps and cell sorting are not host depletion); sequencing.host_read_removal_insilico = computational removal of human reads. rna_methods.target: whole_community, virus_enriched, dual_host_microbe (host and microbial transcripts analysed together) or targeted_amplicon." + SETTING_RULES + ("" if rep == 1 else " (reading 2)"),
                         "prompt": f"Project: {d['study_accession']} — {d['archive'].get('study_title')}\nLibrary source/strategy: {d['archive'].get('library_sources')} / {d['archive'].get('library_strategies')}\n\nPAPER {paper.get('pmcid')}:\n{text}",
                         "tools": [FULLTEXT_TOOL], "tool_choice": {"type": "tool", "name": "fulltext"}, "max_tokens": 6000, "thinking": NOTHINK})
            keys.append((p, paper["paper_id"]))
        try:
            out_ = llm(reqs)
        except BudgetExceeded as e:
            out_ = []; stats["fulltext_budget"] = str(e)
        for (p, pid), r in zip(keys, out_):
            res[p][pname] = clean_setting(r["tool_use"] | {"paper_id": pid}) if r["status"] in ("ok", "cached") and r["tool_use"] else {"status": r.get("status_original") or r["status"], "paper_id": pid}
        stats[pname] = llm.summary()

    if "samples" in passes:
        llm = rubric("samples", budget * share["samples"])
        reqs, keys = [], []
        for p, d in D.items():
            if not d["example_samples"]:
                continue
            ft = res[p].get("fulltext") or {}
            cohort = json.dumps(ft.get("cohort") or {}, default=str)[:1500]
            reqs.append({"system": "Give the metadata of each listed sample of a human microbiome sequencing project. body_site and sample_type are codes (specimen = a sample of a person; controls and mock communities are not specimens); put the source's own words in body_site_detail. Use the sample's own archive attributes first; use a cohort statement from the paper only when it applies to every sample (say so in sources). health_condition: a disease only when this sample's own attributes indicate it, or the paper states that EVERY participant has it; in a case-control or multi-group study, a sample whose group is not given stays null, and controls get 'healthy control' (or their stated condition) — never the study's disease of interest. Never use a run, sample, library or accession id as subject_id: subject_id identifies a PERSON (several samples of one person share it). age with its unit as stated. null when unknown. sources: field -> 'attribute <key>' or 'paper cohort statement'.",
                         "prompt": dossier_text(d, max_profile=40, max_runs=6) + "\n\nPaper cohort summary (may be empty): " + cohort + "\n\nSamples:\n" + example_samples_text(d),
                         "tools": [SAMPLES_TOOL], "tool_choice": {"type": "tool", "name": "samples"}, "max_tokens": 2500, "thinking": NOTHINK})
            keys.append(p)
        try:
            out_ = llm(reqs)
        except BudgetExceeded as e:
            out_ = []; stats["samples_budget"] = str(e)
        for p, r in zip(keys, out_):
            v = r["tool_use"] if r["status"] in ("ok", "cached") else {"status": r.get("status_original") or r["status"]}
            if isinstance(v, dict) and isinstance(v.get("samples"), list):
                cl = []
                for smp in v["samples"]:
                    c_, nf = validate(smp, {"body_site": SITES, "sample_type": STYPES})
                    stats["vocab_fixed"] = stats.get("vocab_fixed", 0) + nf
                    cl.append(c_)
                v = dict(v, samples=cl)
            res[p]["samples"] = v
        stats["samples"] = llm.summary()

    for p, v in res.items():
        prev = os.path.join(out, f"{p}.json")
        old = json.load(open(prev)) if os.path.exists(prev) else {}
        json.dump(old | v, open(prev, "w"), indent=1, default=str)
    json.dump(stats, open(os.path.join(out, f"stats_{'_'.join(passes)}.json"), "w"), indent=1, default=str)
    refused = sum(1 for v in res.values() for k, x in v.items() if isinstance(x, dict) and x.get("status") == "refused")
    print(json.dumps({"projects": len(res), "refused_items": refused, **{k: v for k, v in stats.items() if isinstance(v, dict)}}, default=str)[:1500])
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", required=True); ap.add_argument("--dossiers", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--budget", type=float, default=30); ap.add_argument("--passes", default="classify,link_audit,fulltext,samples")
    a = ap.parse_args(argv)
    ids = [l.strip().split(",")[0] for l in open(a.projects) if l.strip() and not l.startswith("study_accession")]
    return run(ids, a.dossiers, a.out, a.budget, a.passes.split(","), host=globals().get("host"))


if __name__ == "__main__":
    main(globals().get("ARGS"))
