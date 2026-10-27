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
    "quotes": {"type": "object", "description": "field -> verbatim quote of <= 25 words supporting it"}},
    ["paper_describes_project", "sequencing", "metadata_access", "cohort"])

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

    if "fulltext" in passes:
        llm = rubric("fulltext", budget * share["fulltext"])
        reqs, keys = [], []
        for p, d in D.items():
            la = res[p].get("link_audit") or {}
            gen = [x["paper_id"] for x in la.get("papers", []) if x.get("relation") == "generated"]
            if la.get("papers"):                       # audited: read only a paper judged to have generated the data
                order = [x for x in d["papers"] if x.get("paper_id") in gen]
            else:                                      # not audited (no candidates / refused): direct links only
                order = [x for x in d["papers"] if x.get("methods") != "title_similar"]
            paper = next((x for x in order if jats_file(x.get("pmcid"))), None)
            if not paper:
                res[p]["fulltext"] = {"status": "no_generating_paper_text" if (la.get("papers") or order) else "no_paper"}
                continue
            text = paper_sections(paper["pmcid"])
            if not text:
                res[p]["fulltext"] = {"status": "unparsable"}; continue
            reqs.append({"system": "You read the methods of the paper that generated a human microbiome sequencing project and record how the samples were collected and sequenced, and what the paper states about the cohort. Record only what the text states; use null or 'not_reported' otherwise. Quotes verbatim, at most 25 words. rna_methods only for RNA libraries (metatranscriptomes, RNA viromes, RNA mNGS); null for DNA-only studies. metadata_access describes the PARTICIPANT METADATA (not the reads): application_required = a formal application or data-access agreement (dbGaP, EGA, a cohort's data access committee, a named restricted-access procedure); on_request = 'available from the authors on (reasonable) request' only; partly_application = some participant data public and some behind an application; open = metadata stated to be public (in the archive or supplements); not_stated otherwise. sequencing.host_depletion_wetlab = a kit or step that removes human cells/DNA before library preparation (null if none; VLP filtration / nuclease steps and cell sorting are not host depletion); sequencing.host_read_removal_insilico = computational removal of human reads. rna_methods.target: whole_community, virus_enriched, dual_host_microbe (host and microbial transcripts analysed together) or targeted_amplicon.",
                         "prompt": f"Project: {d['study_accession']} — {d['archive'].get('study_title')}\nLibrary source/strategy: {d['archive'].get('library_sources')} / {d['archive'].get('library_strategies')}\n\nPAPER {paper.get('pmcid')}:\n{text}",
                         "tools": [FULLTEXT_TOOL], "tool_choice": {"type": "tool", "name": "fulltext"}, "max_tokens": 3000, "thinking": NOTHINK})
            keys.append((p, paper["paper_id"]))
        try:
            out_ = llm(reqs)
        except BudgetExceeded as e:
            out_ = []; stats["fulltext_budget"] = str(e)
        for (p, pid), r in zip(keys, out_):
            res[p]["fulltext"] = (r["tool_use"] | {"paper_id": pid}) if r["status"] in ("ok", "cached") and r["tool_use"] else {"status": r.get("status_original") or r["status"], "paper_id": pid}
        stats["fulltext"] = llm.summary()

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
