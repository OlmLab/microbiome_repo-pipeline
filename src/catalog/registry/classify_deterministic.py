"""Deterministic (no-LLM) registry classification of ONE aggregated study record.

``classify_study(rec) -> dict`` produces host_human / assay / body_sites / life_stages / population_flags, each with
EVIDENCE rows ``{source, quote}`` (labelled source from the curation-skill list, quote ≤ 12 words taken from a field of
the record itself — never from general knowledge), a per-component confidence, an overall confidence and a
``needs_llm`` flag with reasons. Every emitted evidence row passes ``catalog.triage.curation_kernel.validate_evidence``.

Input record (study-level aggregates; multi-valued strings joined by ' | ' or ';', as written by
enumerate_universe_v3 / aggregate_studies; JSON dict-of-counts accepted for library_strategies):
    study_accession, study_title, description,
    scientific_names, tax_ids, host_tax_ids, host_scientific_names, host, host_body_sites, environment_material,
    sample_titles_sample, isolation_sources, environmental_medium, body_site, tissue,
    ages, dev_stages, life_stages_attr,
    library_strategies, library_sources, library_selections, library_names, target_genes, instrument_models,
    base_count_median, read_length_median (or read_count_median to derive it), n_runs, n_runs_host_9606, n_runs_nonhuman_host,
    serovars, sub_species, strains,
    infant_verdict, infant_reason_code, infant_body_site_call      (priors from universe_studies_all; optional)

Deterministic priors (task spec): host_nonhuman / host_environmental / host_synthetic → host_human=no;
age_adult_only → adult; age_child_over_36m → child; age_maternal_only → adult + pregnant; site_excluded / site_unknown →
body site from infant_body_site_call; include → infant + gut_stool. Prior-driven values carry source
``external_curation.infant_catalog`` (the universe table IS a record we were shown) and stage ``deterministic_prior``.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from catalog.triage.curation_kernel import validate_evidence
from catalog.registry import vocab as V
from catalog.registry.vocab import matcher

# --------------------------------------------------------------------------- constants
# Verbatim from src/catalog/enumeration/resweep_universe.py (frame-free sweep rule, 2026-09-24) — copied rather than
# imported because that module opens the harvest cache at import time.
HSPEC_RE = re.compile(r"\bhuman|\bhomo sapiens|\binfant|\bneonat|\bnewborn|\bpreterm|\bchild|"
                      r"\bpatient|\bvolunteer|breast milk|\bdonor|\bcohort", re.I)
ANIMAL_ENV_RE = re.compile(
    r"\b(mouse|mice|murine|rats?|pigs?|piglets?|swine|porcine|chickens?|poultry|broilers?|cattle|"
    r"bovine|cows?|calf|calves|dogs?|canine|cats?|feline|fish|zebrafish|soil|sediments?|wastewater|"
    r"sewage|marine|reactor|bioreactor|compost|plants?|rumen|insects?|termites?|sheep|ovine|goats?|"
    r"invertebrates?|bees?|honeybee|birds?|avian|bats?|primates?|macaques?|shrimps?|manure|horses?|"
    r"equine|rabbits?|drosophila|mosquito|aquaculture|seawater|freshwater|lake|river|sludge|ocean|"
    r"glacier|permafrost|hot spring|biofilm|fermentation|fermented|cheese|kimchi|sourdough|wine|kefir|"
    r"silage|phyllosphere|rhizosphere|aquifer|groundwater|estuary|hydrothermal|dust|indoor|leopards?|"
    r"tigers?|lions?|pandas?|monkeys?|deer|elephants?|whales?|dolphins?|seals?|penguins?|frogs?|"
    r"turtles?|lizards?|snakes?|corals?|sponges?|oysters?|mussels?|snails?|squid|fly|flies|larvae?|"
    r"beetles?|moths?|worms?|nematodes?|wild|captive|zoo|livestock|animals?|veterinary|yaks?|camels?|"
    r"buffalo|donkeys?|ruminants?|ducks?|geese|goose|turkeys?|quail|salmon|trout|tilapia|carp|crabs?|"
    r"lobsters?|earthworms?|silkworms?|aphids?|wasps?|ants?|cockroach|ticks?|spiders?|koala|kangaroo|"
    r"hamsters?|guinea pig|ferrets?|mink|fox|wolf|bears?|boars?|gorillas?|chimpanzees?|orangutans?|"
    r"lemurs?|marmosets?|baboons?|mus musculus|gallus gallus|sus scrofa|rattus|bos taurus|canis lupus|"
    r"felis catus|danio rerio|apis mellifera|ovis aries|capra hircus|macaca)\b", re.I)
SYNTHETIC_RE = re.compile(r"\b(mock community|synthetic community|simulated|in silico|spike-?in|zymo|positive control|"
                          r"negative control|blank control|reagent control|standard community)\b", re.I)
INFANTIS_RE = re.compile(r"\binfantis\b", re.I)
HUMAN_TAXON_RE = re.compile(r"^human\b", re.I)
NULLS = {"", "missing", "not collected", "not applicable", "na", "n/a", "none", "unknown", "not provided", "nan", "null",
         "-", "missing: not provided", "missing: not collected", "missing: not applicable", "unspecified", "not determined",
         "nd", "restricted access", "missing: restricted access", "not available"}

PRIOR_NONHUMAN = {"host_nonhuman", "host_environmental", "host_synthetic"}
PRIOR_HUMAN = {"age_adult_only", "age_child_over_36m", "age_maternal_only", "age_unknown_no_evidence", "site_excluded",
               "site_unknown", "fp_salmonella_infantis", "fp_bifido_infantis"}
PRIOR_LIFE_STAGE = {"age_adult_only": "adult", "age_child_over_36m": "child", "age_maternal_only": "adult"}
BODY_SITE_CALL_MAP = {  # universe_studies_all.body_site_call → body-site code (values observed in the 1.5.0 table)
    "gut": "gut_stool", "stool": "gut_stool", "feces": "gut_stool", "faeces": "gut_stool", "gut/stool": "gut_stool",
    "stool/gut": "gut_stool", "gut/feces": "gut_stool", "meconium": "gut_stool", "intestinal": "gut_stool",
    "oral": "oral", "saliva": "oral", "skin": "skin", "nasal": "nasal_nasopharyngeal", "nasopharyngeal": "nasal_nasopharyngeal",
    "sputum": "respiratory_lower", "lung": "respiratory_lower", "vaginal": "vaginal_urogenital", "urine": "vaginal_urogenital",
    "milk": "milk", "breast milk": "milk", "blood": "blood_tissue", "tissue": "blood_tissue",
}
STRONG_SITE_SOURCES = ("study.title", "sample.title", "sample.attr.isolation_source", "sample.attr.host_body_site",
                       "sample.attr.environmental_medium", "sample.attr.body_site", "sample.attr.tissue",
                       "sample.attr.environment_material")
CONF_FLOOR = 0.8
STAGES = ("deterministic_prior", "deterministic_rule", "sonnet_x2", "opus_adjudicated", "pending", "owner_decision", "curator_audit")

# --------------------------------------------------------------------------- helpers


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v)


def split_multi(v: Any) -> list[str]:
    """' | ' / ';' joined aggregate → distinct non-null values, order preserved."""
    s = _s(v).strip()
    if not s:
        return []
    if s.startswith("{"):
        try:
            return [str(k) for k in json.loads(s).keys()]
        except json.JSONDecodeError:
            pass
    if s.startswith("[") and s.endswith("]"):
        try:
            return [str(x) for x in json.loads(s)]
        except json.JSONDecodeError:
            pass
    out, seen = [], set()
    for part in re.split(r"\s*[|;]\s*", s):
        p = part.strip()
        if p and p.lower() not in NULLS and p not in seen:
            out.append(p)
            seen.add(p)
    return out


def strategy_counts(v: Any) -> dict[str, int]:
    s = _s(v).strip()
    if s.startswith("{"):
        try:
            return {str(k): int(n) for k, n in json.loads(s).items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return {x: 1 for x in split_multi(v)}


def _trim_words(text: str, n: int = 12) -> str:
    w = str(text).split()
    return " ".join(w[:n])


def quote_around(text: str, start: int, end: int, ctx: int = 3) -> str:
    """≤ 12-word quote: the matched span with up to `ctx` words of context on each side."""
    before = text[:start].split()
    after = text[end:].split()
    span = text[start:end]
    words = before[-ctx:] + [span] + after[:ctx]
    q = " ".join(words)
    return _trim_words(q, 12)


def ev(source: str, quote: str) -> dict[str, str]:
    row = {"source": source, "quote": _trim_words(str(quote).strip(), 12)}
    ok, msg = validate_evidence([row])
    if not ok:
        raise ValueError(f"evidence row failed validation: {msg}: {row}")
    return row


def _first_regex_quote(rx: re.Pattern, text: str) -> str:
    m = rx.search(text)
    return quote_around(text, *m.span()) if m else ""


def text_sources(rec: dict) -> list[tuple[str, str]]:
    """(source_label, text) pairs in the order the classifier reads them."""
    pairs = [("study.title", _s(rec.get("study_title"))), ("study.description", _s(rec.get("description")))]
    for key, label in (("sample_titles_sample", "sample.title"), ("isolation_sources", "sample.attr.isolation_source"),
                       ("host_body_sites", "sample.attr.host_body_site"), ("environmental_medium", "sample.attr.environmental_medium"),
                       ("environment_material", "sample.attr.environment_material"), ("body_site", "sample.attr.body_site"),
                       ("tissue", "sample.attr.tissue"), ("host", "sample.attr.host"), ("dev_stages", "sample.attr.dev_stage"),
                       ("life_stages_attr", "sample.attr.host_life_stage")):
        vals = split_multi(rec.get(key))
        if vals:
            pairs.append((label, " | ".join(vals[:40])))
    return [(l, t) for l, t in pairs if t.strip()]


def _read_length(rec: dict) -> float | None:
    rl = rec.get("read_length_median")
    try:
        if rl not in (None, "") and not (isinstance(rl, float) and math.isnan(rl)):
            return float(rl)
    except (TypeError, ValueError):
        pass
    try:
        bc, rc = float(rec.get("base_count_median")), float(rec.get("read_count_median"))
        if rc > 0:
            layouts = [x.upper() for x in split_multi(rec.get("library_layouts"))]
            return bc / rc / (2 if layouts == ["PAIRED"] else 1)
    except (TypeError, ValueError):
        pass
    return None


# --------------------------------------------------------------------------- components


def classify_host(rec: dict) -> dict:
    """host_human ∈ yes|no|mixed|unknown with evidence, confidence, stage and llm reasons."""
    reasons: list[str] = []
    prior = _s(rec.get("infant_reason_code"))
    verdict = _s(rec.get("infant_verdict"))
    if prior in PRIOR_NONHUMAN:
        return {"host_human": "no", "confidence": 0.95, "stage": "deterministic_prior", "reasons": reasons,
                "evidence": [ev("external_curation.infant_catalog", f"infant triage reason_code {prior}")]}
    if verdict == "include" or prior in PRIOR_HUMAN:
        label = f"infant triage verdict {verdict}" if verdict == "include" else f"infant triage reason_code {prior}"
        return {"host_human": "yes", "confidence": 0.9, "stage": "deterministic_prior", "reasons": reasons,
                "evidence": [ev("external_curation.infant_catalog", label)]}

    ids = []
    for x in split_multi(rec.get("host_tax_ids")):
        m = re.match(r"^\s*(\d+)", x)
        if m:
            ids.append(int(m.group(1)))
    n_h = rec.get("n_runs_host_9606")
    n_nh = rec.get("n_runs_nonhuman_host")
    has_h = 9606 in ids or (n_h is not None and _num(n_h) > 0)
    has_nh = any(i != 9606 for i in ids) or (n_nh is not None and _num(n_nh) > 0)
    host_names = split_multi(rec.get("host_scientific_names"))
    if has_h and not has_nh:
        return {"host_human": "yes", "confidence": 0.95, "stage": "deterministic_rule", "reasons": reasons,
                "evidence": [ev("run.host_tax_id", "host_tax_id 9606")]}
    if has_nh and not has_h:
        nh_ids = [str(i) for i in ids if i != 9606][:3]
        rows = [ev("run.host_tax_id", "host_tax_id " + ", ".join(nh_ids or ["non-9606"]))]
        if host_names:
            rows.append(ev("run.host_scientific_name", host_names[0]))
        return {"host_human": "no", "confidence": 0.9, "stage": "deterministic_rule", "reasons": reasons, "evidence": rows}
    if has_h and has_nh:
        reasons.append("host_mixed_tax_ids")
        return {"host_human": "mixed", "confidence": 0.6, "stage": "deterministic_rule", "reasons": reasons,
                "evidence": [ev("run.host_tax_id", "host_tax_id 9606 and non-human ids")]}

    # no host taxon: text signals
    sci = split_multi(rec.get("scientific_names"))
    human_taxon = [x for x in sci if HUMAN_TAXON_RE.match(x)]
    all_text = " ".join(t for _, t in text_sources(rec))
    synth = SYNTHETIC_RE.search(all_text)
    animal_hits = [(lab, m) for lab, t in text_sources(rec) for m in [ANIMAL_ENV_RE.search(t)] if m]
    human_hits = [(lab, t, m) for lab, t in text_sources(rec) for m in [HSPEC_RE.search(t)] if m]
    if synth and not human_hits:
        return {"host_human": "no", "confidence": 0.8, "stage": "deterministic_rule", "reasons": reasons,
                "evidence": [ev("study.title" if SYNTHETIC_RE.search(_s(rec.get("study_title"))) else "study.description",
                                _first_regex_quote(SYNTHETIC_RE, all_text))]}
    if human_taxon and not animal_hits:
        rows = [ev("run.scientific_name", human_taxon[0])]
        if human_hits:
            lab, t, m = human_hits[0]
            rows.append(ev(lab, quote_around(t, *m.span())))
        return {"host_human": "yes", "confidence": 0.85, "stage": "deterministic_rule", "reasons": reasons, "evidence": rows}
    if animal_hits and not human_hits and not human_taxon:
        lab, m = animal_hits[0]
        t = dict(text_sources(rec))[lab]
        return {"host_human": "no", "confidence": 0.75, "stage": "deterministic_rule", "reasons": reasons,
                "evidence": [ev(lab, quote_around(t, *m.span()))]}
    if human_hits and not animal_hits:
        lab, t, m = human_hits[0]
        return {"host_human": "yes", "confidence": 0.7, "stage": "deterministic_rule", "reasons": ["host_text_only"],
                "evidence": [ev(lab, quote_around(t, *m.span()))]}
    if human_hits and animal_hits:
        lab, t, m = human_hits[0]
        alab, am = animal_hits[0]
        at = dict(text_sources(rec))[alab]
        return {"host_human": "unknown", "confidence": 0.4, "stage": "deterministic_rule", "reasons": ["host_conflicting_signals"],
                "evidence": [ev(lab, quote_around(t, *m.span())), ev(alab, quote_around(at, *am.span()))]}
    return {"host_human": "unknown", "confidence": 0.0, "stage": "deterministic_rule", "reasons": ["host_no_evidence"], "evidence": []}


def _num(v: Any) -> float:
    try:
        f = float(v)
        return 0.0 if math.isnan(f) else f
    except (TypeError, ValueError):
        return 0.0


def classify_assay(rec: dict) -> dict:
    """assay ∈ shotgun_dna|amplicon_misfiled|amplicon|rna|isolate_genome|assembly_only|other|mixed|unknown."""
    acfg = V.load_vocab("assay")
    sig = acfg["shotgun_depth_signature"]
    tells = re.compile(acfg["amplicon_tells_regex"], re.I)
    strategies = strategy_counts(rec.get("library_strategies"))
    sources = {x.upper() for x in split_multi(rec.get("library_sources"))}
    target_genes = [x for x in split_multi(rec.get("target_genes")) if x.lower() not in NULLS]
    lib_names = " ".join(split_multi(rec.get("library_names"))[:50])
    selections = {x.upper() for x in split_multi(rec.get("library_selections"))}
    instruments = " ".join(split_multi(rec.get("instrument_models")))
    sci = split_multi(rec.get("scientific_names"))
    metagenome_taxon = bool(sci) and all(x.lower().endswith("metagenome") or "metagenome" in x.lower() for x in sci)
    named_microbe = bool(sci) and any(re.match(r"^[A-Z][a-z]+ [a-z]+", x) and "metagenome" not in x.lower() for x in sci)
    reasons: list[str] = []
    n_runs = _num(rec.get("n_runs"))
    if not strategies and not sources:
        if n_runs == 0:
            return {"assay": "assembly_only", "confidence": 0.7, "reasons": reasons, "evidence": [ev("run.library_strategy", "no read_run rows")]}
        return {"assay": "unknown", "confidence": 0.0, "reasons": ["assay_no_fields"], "evidence": []}

    per: dict[str, int] = {}
    evid: dict[str, list[dict]] = {}
    conf: dict[str, float] = {}
    for strat, n in strategies.items():
        su = strat.upper()
        if su in ("RNA-SEQ", "SSRNA-SEQ", "MIRNA-SEQ") or sources & {"METATRANSCRIPTOMIC", "TRANSCRIPTOMIC", "TRANSCRIPTOMIC SINGLE CELL"}:
            code, c, e = "rna", 0.95, [ev("run.library_strategy", strat)]
        elif su == "AMPLICON":
            code, c, e = "amplicon", 0.95, [ev("run.library_strategy", strat)]
        elif su in ("WGS", "WXS") and (target_genes or (selections == {"PCR"} and tells.search(lib_names))):
            q = target_genes[0] if target_genes else tells.search(lib_names).group(0)
            code, c, e = "amplicon_misfiled", 0.85, [ev("run.library_strategy", strat), ev("run.target_gene" if target_genes else "run.library_name", q)]
            reasons.append("assay_amplicon_tell_on_wgs")
        elif su in ("WGS", "WXS") and "METAGENOMIC" in sources:
            code, c, e = "shotgun_dna", 0.95, [ev("run.library_strategy", strat), ev("run.library_source", "METAGENOMIC")]
        elif su in ("WGS", "WXS") and "GENOMIC" in sources and metagenome_taxon:
            code, c, e = "shotgun_dna", 0.7, [ev("run.library_source", "GENOMIC on metagenome taxon"), ev("run.scientific_name", sci[0])]
            reasons.append("assay_misfiled_genomic")
        elif "GENOMIC" in sources and named_microbe:
            code, c, e = "isolate_genome", 0.9, [ev("run.library_source", "GENOMIC"), ev("run.scientific_name", next(x for x in sci if "metagenome" not in x.lower()))]
        elif su in ("OTHER", "TARGETED-CAPTURE"):
            rl = _read_length(rec)
            deep = (_num(rec.get("base_count_median")) >= sig["min_bases"] and rl is not None and sig["read_len_min"] <= rl <= sig["read_len_max"]
                    and not target_genes and re.search(sig["instrument_regex"], instruments or "", re.I))
            if deep:
                code, c, e = "shotgun_dna", 0.6, [ev("run.library_strategy", strat), ev("run.base_count", f"median {int(_num(rec.get('base_count_median')))} bases"),
                                                  ev("run.instrument_model", split_multi(rec.get("instrument_models"))[0])]
                reasons.append("assay_other_depth_adjudication")
            else:
                code, c, e = "other", 0.7, [ev("run.library_strategy", strat)]
        elif su in ("WGS", "WXS"):
            code, c, e = "shotgun_dna", 0.6, [ev("run.library_strategy", strat)]
            reasons.append("assay_source_unclear")
        else:
            code, c, e = "other", 0.8, [ev("run.library_strategy", strat)]
        per[code] = per.get(code, 0) + max(int(n), 1)
        evid.setdefault(code, e)
        conf[code] = max(conf.get(code, 0), c)
    total = sum(per.values()) or 1
    ranked = sorted(per.items(), key=lambda kv: -kv[1])
    top, ntop = ranked[0]
    if len(ranked) > 1 and (total - ntop) / total >= 0.2:
        rows = []
        for code, _ in ranked[:3]:
            rows.extend(evid[code][:1])
        reasons.append("assay_mixed")
        return {"assay": "mixed", "confidence": 0.7, "reasons": reasons, "evidence": rows, "assay_run_shares": {k: round(v / total, 3) for k, v in per.items()}}
    return {"assay": top, "confidence": conf[top], "reasons": reasons, "evidence": evid[top], "assay_run_shares": {k: round(v / total, 3) for k, v in per.items()}}


def _uberon_id_hits(text: str) -> list[tuple[str, str]]:
    """UBERON:000xxxx ids embedded in attribute values → (code, id)."""
    out = []
    bs = V.load_vocab("body_sites")["codes"]
    for m in re.finditer(r"UBERON:\d{7}", text):
        for code, spec in bs.items():
            if any(u["id"] == m.group(0) for u in spec.get("uberon") or []):
                out.append((code, m.group(0)))
    return out


def classify_body_site(rec: dict, host_human: str) -> dict:
    reasons: list[str] = []
    bm = matcher("body_sites")
    scores: dict[str, float] = {}
    evid: dict[str, list[dict]] = {}
    strong: dict[str, bool] = {}

    def add(code: str, w: float, row: dict, is_strong: bool):
        scores[code] = scores.get(code, 0.0) + w
        evid.setdefault(code, [])
        if len(evid[code]) < 3:
            evid[code].append(row)
        strong[code] = strong.get(code, False) or is_strong

    for label, text in text_sources(rec):
        if label in ("sample.attr.dev_stage", "sample.attr.host_life_stage", "sample.attr.host"):
            continue
        is_strong = label in STRONG_SITE_SOURCES
        w = 1.0 if is_strong else 0.6
        for code, spans in bm.match_spans(text).items():
            if code in ("unknown_site", "multi_site"):
                continue
            s, e = spans[0][1], spans[0][2]
            add(code, w, ev(label, quote_around(text, s, e)), is_strong)
        for code, uid in _uberon_id_hits(text):
            add(code, 1.0, ev(label, uid), True)
    # prior from the infant triage (site_excluded / site_unknown / include)
    prior_code = None
    verdict = _s(rec.get("infant_verdict"))
    call = _s(rec.get("infant_body_site_call")).strip().lower()
    if verdict == "include":
        prior_code = "gut_stool"
        add("gut_stool", 1.0, ev("external_curation.infant_catalog", "infant triage verdict include (gut/stool scope)"), True)
    elif call and call in BODY_SITE_CALL_MAP:
        prior_code = BODY_SITE_CALL_MAP[call]
        add(prior_code, 1.0, ev("external_curation.infant_catalog", f"body_site_call {call}"), True)
    # weak taxon prior — TAXON IS NOT BODY SITE
    taxon_codes = []
    for sn in split_multi(rec.get("scientific_names")):
        tc = V.taxon_body_site_prior(sn)
        if tc and tc != "unknown_site":
            taxon_codes.append((tc, sn))
    for tc, sn in taxon_codes[:3]:
        if tc not in scores:
            add(tc, 0.5, ev("run.scientific_name", sn), False)

    if host_human == "no":
        return {"body_sites": [], "body_site_primary": None, "confidence": 1.0, "reasons": reasons, "evidence": []}
    kept = [c for c, sc in sorted(scores.items(), key=lambda kv: -kv[1]) if sc >= 0.5]
    if not kept:
        reasons.append("site_unknown")
        return {"body_sites": ["unknown_site"], "body_site_primary": "unknown_site", "confidence": 0.0, "reasons": reasons, "evidence": []}
    strong_codes = [c for c in kept if strong.get(c)]
    primary = kept[0]
    conf = 0.9 if strong.get(primary) else (0.7 if scores[primary] >= 0.6 else 0.5)
    if not strong_codes:
        reasons.append("site_weak_terms_only" if conf >= 0.6 else "site_taxon_prior_only")
    if len(strong_codes) >= 3:
        primary = "multi_site"
        reasons.append("site_multi")
        conf = min(conf, 0.75)
    elif len(strong_codes) == 2 and abs(scores[strong_codes[0]] - scores[strong_codes[1]]) < 0.5 and prior_code is None:
        reasons.append("site_two_strong_tie")
        conf = min(conf, 0.7)
    if verdict == "include":  # curated verdict wins: infant catalog == gut/stool scope (task spec prior)
        primary, conf = "gut_stool", max(conf, 0.9)
        reasons = [r for r in reasons if not r.startswith("site_")]
    rows = []
    for c in kept[:4]:
        rows.extend(evid[c][:2])
    return {"body_sites": kept, "body_site_primary": primary, "confidence": conf, "reasons": reasons, "evidence": rows,
            "body_site_scores": {k: round(v, 2) for k, v in scores.items()}}


def _parse_age_days(value: str) -> float | None:
    """Numeric age with an explicit unit → days; unitless numbers → None (curation Rule 10 / rule 5)."""
    try:
        from catalog.enumeration.infant_rule import parse_age_months
    except ImportError:  # pragma: no cover
        return None
    months, tag, _ = parse_age_months(value)
    if months is None or tag in ("none", "unparsed", "bare_assumed_years", "word"):
        return None
    return months * 30.4375


def classify_life_stage(rec: dict, host_human: str) -> dict:
    reasons: list[str] = []
    if host_human == "no":
        return {"life_stages": [], "life_stage_primary": None, "confidence": 1.0, "reasons": reasons, "evidence": [], "stage": "deterministic_rule"}
    lm = matcher("life_stages")
    scores: dict[str, float] = {}
    evid: dict[str, list[dict]] = {}
    strong: dict[str, bool] = {}
    stage = "deterministic_rule"

    def add(code, w, row, is_strong):
        scores[code] = scores.get(code, 0.0) + w
        evid.setdefault(code, [])
        if len(evid[code]) < 3:
            evid[code].append(row)
        strong[code] = strong.get(code, False) or is_strong

    prior = _s(rec.get("infant_reason_code"))
    verdict = _s(rec.get("infant_verdict"))
    if verdict == "include":
        add("infant", 1.5, ev("external_curation.infant_catalog", "infant triage verdict include (0-3 y scope)"), True)
        stage = "deterministic_prior"
    elif prior in PRIOR_LIFE_STAGE:
        add(PRIOR_LIFE_STAGE[prior], 1.5, ev("external_curation.infant_catalog", f"infant triage reason_code {prior}"), True)
        stage = "deterministic_prior"

    # "Infantis" trap: a Salmonella/Bifidobacterium infantis record must not read as a human infant
    infantis = any(INFANTIS_RE.search(_s(rec.get(k))) for k in ("serovars", "sub_species", "strains", "scientific_names"))
    for label, text in text_sources(rec):
        is_strong = label in ("study.title", "sample.title", "sample.attr.dev_stage", "sample.attr.host_life_stage")
        w = 1.0 if is_strong else 0.6
        for code, spans in lm.match_spans(text).items():
            if code in ("unknown_age",):
                continue
            if infantis and code in ("infant", "neonate") and label != "study.title":
                reasons.append("infantis_name_collision")
                continue
            s, e = spans[0][1], spans[0][2]
            add(code, w, ev(label, quote_around(text, s, e)), is_strong)
    # numeric ages (unit required)
    for key, label in (("ages", "sample.attr.age"), ("dev_stages", "sample.attr.dev_stage"), ("host_ages", "sample.attr.host_age")):
        for val in split_multi(rec.get(key))[:60]:
            d = _parse_age_days(val)
            code = V.age_days_to_life_stage(d)
            if code:
                add(code, 1.0, ev(label, val), True)
    kept = [c for c, sc in sorted(scores.items(), key=lambda kv: -kv[1]) if sc >= 0.6]
    kept = [c for c in kept if c != "mixed_ages"] or ([] if "mixed_ages" not in scores else ["mixed_ages"])
    if not kept:
        reasons.append("life_stage_unknown")
        return {"life_stages": ["unknown_age"], "life_stage_primary": "unknown_age", "confidence": 0.0, "reasons": reasons, "evidence": [], "stage": stage}
    strong_codes = [c for c in kept if strong.get(c)]
    primary = kept[0]
    conf = 0.9 if strong.get(primary) else 0.65
    if not strong_codes:
        reasons.append("life_stage_weak_terms_only")
    if len(strong_codes) >= 3 or "mixed_ages" in scores:
        primary = "mixed_ages"
        reasons.append("life_stage_mixed")
        conf = min(conf, 0.75)
    if verdict == "include":  # curated verdict wins: 0-3 y scope → primary infant (neonate/child kept in the list)
        primary, conf = "infant", max(conf, 0.9)
        reasons = [r for r in reasons if not r.startswith("life_stage_")]
    elif prior in PRIOR_LIFE_STAGE and PRIOR_LIFE_STAGE[prior] in kept:
        primary, conf = PRIOR_LIFE_STAGE[prior], max(conf, 0.85)
        reasons = [r for r in reasons if not r.startswith("life_stage_")]
    rows = []
    for c in kept[:4]:
        rows.extend(evid.get(c, [])[:2])
    return {"life_stages": kept, "life_stage_primary": primary, "confidence": conf, "reasons": reasons, "evidence": rows,
            "stage": stage, "life_stage_scores": {k: round(v, 2) for k, v in scores.items()}}


def classify_population(rec: dict, host_human: str) -> dict:
    if host_human == "no":
        return {"population_flags": [], "evidence": []}
    pm = matcher("population_flags")
    flags: list[str] = []
    rows: list[dict] = []
    if _s(rec.get("infant_reason_code")) == "age_maternal_only":
        flags.append("pregnant")
        rows.append(ev("external_curation.infant_catalog", "infant triage reason_code age_maternal_only"))
    for label, text in text_sources(rec):
        if label in ("sample.attr.dev_stage", "sample.attr.host_life_stage", "sample.attr.host"):
            continue
        for code, spans in pm.match_spans(text).items():
            if code not in flags:
                flags.append(code)
                s, e = spans[0][1], spans[0][2]
                rows.append(ev(label, quote_around(text, s, e)))
    return {"population_flags": flags, "evidence": rows}


# --------------------------------------------------------------------------- study-level driver


def classify_study(rec: dict) -> dict:
    """Full deterministic classification of one aggregated study record (see module docstring)."""
    host = classify_host(rec)
    hh = host["host_human"]
    assay = classify_assay(rec)
    site = classify_body_site(rec, hh)
    life = classify_life_stage(rec, hh)
    pop = classify_population(rec, hh)
    reasons = list(dict.fromkeys(host["reasons"] + assay["reasons"] + site["reasons"] + life["reasons"]))
    comps = [host["confidence"], assay["confidence"]]
    if hh != "no":
        comps += [site["confidence"], life["confidence"]]
    confidence = round(min(comps), 3)
    needs_llm = bool(reasons) or hh in ("mixed", "unknown") or confidence < CONF_FLOOR
    stage = "deterministic_prior" if (host["stage"] == "deterministic_prior" or life.get("stage") == "deterministic_prior") else "deterministic_rule"
    for rows in (host["evidence"], assay["evidence"], site["evidence"], life["evidence"], pop["evidence"]):
        if rows:
            ok, msg = validate_evidence(rows)
            assert ok, msg
    return {
        "study_accession": rec.get("study_accession"),
        "host_human": hh, "host_evidence": host["evidence"], "host_confidence": host["confidence"],
        "assay": assay["assay"], "assay_evidence": assay["evidence"], "assay_confidence": assay["confidence"],
        "assay_run_shares": assay.get("assay_run_shares", {}),
        "body_sites": site["body_sites"], "body_site_primary": site["body_site_primary"],
        "body_site_evidence": site["evidence"], "body_site_confidence": site["confidence"],
        "life_stages": life["life_stages"], "life_stage_primary": life["life_stage_primary"],
        "life_stage_evidence": life["evidence"], "life_stage_confidence": life["confidence"],
        "population_flags": pop["population_flags"], "population_evidence": pop["evidence"],
        "classification_stage": stage, "classification_confidence": confidence, "classification_model": "deterministic",
        "needs_llm": needs_llm, "needs_llm_reasons": reasons,
    }


def classify_frame(df, priors=None):
    """Classify every row of a pandas DataFrame of aggregated study records; returns a DataFrame with the output columns,
    list-valued fields ';'-joined and evidence JSON-encoded (registry_studies conventions)."""
    import pandas as pd

    if priors is not None:
        pri = priors.rename(columns={"triage_verdict": "infant_verdict", "reason_code": "infant_reason_code",
                                     "body_site_call": "infant_body_site_call"})
        pri = pri[[c for c in ("study_accession", "infant_verdict", "infant_reason_code", "infant_body_site_call") if c in pri.columns]]
        df = df.merge(pri, on="study_accession", how="left", suffixes=("", "_prior"))
    out = []
    for rec in df.to_dict("records"):
        r = classify_study(rec)
        out.append({
            "study_accession": r["study_accession"], "host_human": r["host_human"], "host_evidence": json.dumps(r["host_evidence"]),
            "assay": r["assay"], "assay_evidence": json.dumps(r["assay_evidence"]),
            "body_sites": ";".join(r["body_sites"]), "body_site_primary": r["body_site_primary"],
            "body_site_evidence": json.dumps(r["body_site_evidence"]),
            "life_stages": ";".join(r["life_stages"]), "life_stage_primary": r["life_stage_primary"],
            "life_stage_evidence": json.dumps(r["life_stage_evidence"]),
            "population_flags": ";".join(r["population_flags"]), "population_evidence": json.dumps(r["population_evidence"]),
            "classification_stage": r["classification_stage"], "classification_confidence": r["classification_confidence"],
            "classification_model": r["classification_model"], "needs_llm": r["needs_llm"],
            "needs_llm_reasons": ";".join(r["needs_llm_reasons"]),
        })
    return pd.DataFrame(out)
