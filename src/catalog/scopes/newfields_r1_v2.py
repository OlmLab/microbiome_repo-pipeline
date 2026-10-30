"""Route R1 for the gut pack's NEW FIELDS v2 (config/packs/gut.yaml, owner cleanup 2026-09-30; release R2026.13 / package 1.13.0):
diet / diet_detail, smoking_status, medication / medication_detail (vocab_list), stool_consistency_bristol (int 1–7).

Input: the harvested BioSample attribute rows of the catalog studies whose keys match diet / smoking / medication / stool patterns
(sample_acc, acc_resolved, study_accession, attr_key_norm, attr_value, …), joined to sample_key through the package wide table
(catalog.scopes.newfields_r1._sample_key_map; run-unit samples through gut_runs). Output: gut_r1_newfields_v2_determinations.parquet in the
determination schema (route R1, scope sample, determined_by gut_newfields_r1_v2) plus the reviewable maps gut_diet_map / gut_smoking_map /
gut_medication_map, the reject table and the conflict table.

Rules (task 2026-09-30, infant-curation-rules): every committed value carries the raw attribute value as verbatim quote (≤ 12 words),
the labelled source biosample.attribute:<attr_key_norm>, route R1 and a confidence. Deterministic parsers are pure functions
(tests/test_newfields_r1_v2.py). The long tail of DISTINCT raw strings goes to the UTILITY model (catalog.models.resolve_model('utility'
→ 'screen')) under a SUBSTRING GUARD: the model must name the span of the raw value that justifies the code, the span must occur in the
raw value, and for the closed vocabulary codes it must contain a match term of the vocabulary or a documented abbreviation / synonym of
this module — a code the raw value does not state is never committed. Placeholders stay unknown (no row).

* diet: match_terms first; 'omnivore' only when stated; a no-special-diet / none / no-restriction statement → omnivore at 0.7 with a
  parse_note; study-arm codes (control, habitual, A/B, numeric codes) are rejected, never guessed.
* smoking_status: never | former | current. yes/no keys → current/never (0.8); 'former|ex|quit' → former; durations, pack-years and
  multi-level numeric codes (0/1/2) stay unknown; passive exposure is not smoking.
* medication: ';'-joined sorted codes from free-text medication lists (deterministic drug-name synonyms, then the utility model with the
  guard) and per-class yes/no keys (ppi_last_month = yes → ppi); antibiotics are antibiotic_exposure, never a medication code;
  none_reported only for an explicit none.
* stool_consistency_bristol: integers 1–7 as stated (0.9); hard / normal / loose / watery → 2 / 4 / 6 / 7 at 0.7 with a parse_note;
  ranges ('type 3-4'), per-subject means (3.67) and other scales ('grade-1') are rejected.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import pandas as pd

from catalog.scopes.newfields_r1 import DET_COLS, SRC_TRACK, _llm, _parse_json_array, _q12, _sample_key_map, _utility_model, build_gut_runs, is_placeholder

DETERMINED_BY = "gut_newfields_r1_v2"
RELEASE_ID, PACKAGE_VERSION = "R2026.13", "1.13.0"

YES = {"yes", "y", "true", "t", "1", "used", "ja", "positive", "present", "current"}
NO = {"no", "n", "false", "f", "0", "not_used", "not used", "notreatment", "no treatment", "none", "never", "nil", "absent", "negative"}
WINDOW_YES_RE = re.compile(r"^\d+\s*to\s*\d+\s*days?$|^within\b|^last\s+(week|month)$|^<\s*\d+\s*(d|days|weeks?)$", re.I)


def load_vocab(cfg_dir: str, name: str) -> dict:
    import yaml
    return yaml.safe_load(open(os.path.join(cfg_dir, "vocab", f"{name}.yaml"), encoding="utf-8"))["codes"]


def norm_text(value) -> str:
    return " ".join(str(value).split())


def yes_no(value) -> bool | None:
    """True for an affirmative flag value, False for a negative one, None otherwise (free text, placeholders, numeric codes > 1)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    lv = norm_text(value).lower()
    if lv in YES or WINDOW_YES_RE.match(lv):
        return True
    if lv in NO:
        return False
    return None


# vocabulary match terms are partly stems (omnivor, chemotherap, immunosuppress): a term of ≥ 5 letters may carry one of these inflections
_SUFFIXES = ("", "s", "es", "e", "y", "ed", "ant", "ants", "ive", "ion", "ic", "al", "ally", "ism")
_NEG_RE = re.compile(r"(?<![a-z])(no|not|non|never|without|avoid|avoids|avoiding|avoided|limit|limits|limited|limiting|free|exclude|excludes|excluding|little|less|"
                     r"minus|except|rarely|reduce|reduced|reducing|stopped|quit)(?![a-z])")


def _word_re(term: str) -> re.Pattern:
    t = term.lower()
    suf = "(?:" + "|".join(re.escape(s) for s in _SUFFIXES) + ")" if len(re.sub(r"[^a-z]", "", t)) >= 5 else ""
    return re.compile(r"(?<![a-z0-9])" + re.escape(t) + suf + r"(?![a-z0-9])")


def term_in(term: str, text: str) -> bool:
    """Whole-word, case-insensitive containment ('rat' never matches 'ulcerative'); the term itself may hold spaces / hyphens; a term of
    ≥ 5 letters also matches with a plural / adjectival inflection (omnivor → omnivore, statin → statins)."""
    return bool(term) and bool(_word_re(term).search(text.lower()))


def negated(term: str, text: str) -> bool:
    """True when every occurrence of `term` in `text` is preceded (within three words) by a negation / avoidance word or followed by 'free'."""
    lt = text.lower()
    found = False
    for m in _word_re(term).finditer(lt):
        found = True
        before = lt[:m.start()].split()[-3:]
        after = lt[m.end():].split()[:1]
        if not (any(_NEG_RE.fullmatch(w.strip(",;:.()")) for w in before) or (after and after[0].strip(",;:.()") in ("free", "free,", "-free"))):
            return False
    return found


def match_codes(text: str, codes: dict, synonyms: dict | None = None, exclude=("unknown",)) -> list[tuple[str, str]]:
    """(code, term) pairs whose vocabulary match_terms or module synonyms occur as whole words in `text`; vocabulary order, one term per code."""
    hits = []
    for code, spec in codes.items():
        if code in exclude:
            continue
        for t in list(spec.get("match_terms") or []) + list((synonyms or {}).get(code, [])):
            if term_in(t, text) and not negated(t, text):
                hits.append((code, t))
                break
    return hits


def span_in(term, raw) -> bool:
    """Substring guard for model output: the justifying span must occur in the raw value (case-insensitive, whitespace-normalised)."""
    if not isinstance(term, str) or not term.strip():
        return False
    return norm_text(term).lower() in norm_text(raw).lower()


def _llm_batches(items: list[dict], prompt_fn, llm, model: str, batch: int, token_log: dict | None, max_tokens: int = 6000) -> dict:
    """Run the utility model over item batches (each item carries `id`); returns {id: output object}. Missing ids are simply absent."""
    out = {}
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        res = llm(prompt_fn(chunk), model=model, max_tokens=max_tokens)
        if token_log is not None:
            u = res.get("usage") or {}
            token_log["input"] = token_log.get("input", 0) + int(u.get("input_tokens", 0) or 0)
            token_log["output"] = token_log.get("output", 0) + int(u.get("output_tokens", 0) or 0)
            token_log["cache_read"] = token_log.get("cache_read", 0) + int(u.get("cache_read_input_tokens", 0) or 0)
            token_log["calls"] = token_log.get("calls", 0) + 1
        for o in _parse_json_array(res.get("text", "")):
            if isinstance(o, dict) and o.get("id") is not None:
                out[o["id"]] = o
    return out


# ============================================================================================================================ diet
DIET_TEXT_KEYS = ["host_diet", "diet", "special_diet", "diet_type", "special_diet_details", "dietary_regime_biospecimen", "general_diet", "other_diet",
                  "s_specificdiet_v2", "s_specificdiet_v2_v2", "s_diet_v2", "dietary_restrictions_details_m3_biospecimen", "treatment_grp", "dietary_pattern",
                  "diet_pattern", "dietary_habit", "dietary_habits", "eating_habits", "food_habit", "diet_group", "dietary_group", "vegetarian_status"]
# boolean keys whose TRUE value states the diet (code, detail label, confidence, note)
DIET_FLAG_KEYS = {
    "vegetarian": ("vegetarian", "vegetarian", 0.85, "boolean key vegetarian = yes"),
    "vegan": ("vegan", "vegan", 0.85, "boolean key vegan = yes"),
    "specialized_diet_i_do_not_eat_a_specialized_diet": ("omnivore", "no specialized diet", 0.7, "omnivore read from 'I do not eat a specialized diet' = true"),
    "specialized_diet_kosher": ("other_diet", "kosher", 0.85, "boolean key = true"),
    "specialized_diet_halaal": ("other_diet", "halaal", 0.85, "boolean key = true"),
    "specialized_diet_paleo_diet_or_primal_diet": ("other_diet", "paleo / primal diet", 0.85, "boolean key = true"),
    "specialized_diet_paleodiet_or_primal_diet": ("other_diet", "paleo / primal diet", 0.85, "boolean key = true"),
    "specialized_diet_modified_paleo_diet": ("other_diet", "modified paleo diet", 0.85, "boolean key = true"),
    "specialized_diet_raw_food_diet": ("other_diet", "raw food diet", 0.85, "boolean key = true"),
    "specialized_diet_weston_price_or_other_low_grain_low_processed_food_diet": ("high_fibre_or_whole_food", "Weston Price / low-grain low-processed-food diet", 0.85, "boolean key = true"),
    "specialized_diet_fodmap": ("therapeutic_or_study_diet", "low-FODMAP diet", 0.85, "boolean key = true"),
    "specialized_diet_exclude_dairy": ("other_diet", "excludes dairy", 0.85, "boolean key = true"),
    "specialized_diet_exclude_nightshades": ("other_diet", "excludes nightshades", 0.85, "boolean key = true"),
    "specialized_diet_exclude_refined_sugars": ("other_diet", "excludes refined sugars", 0.85, "boolean key = true"),
}
# boolean keys whose FALSE value states 'no special diet' → omnivore at 0.7 (task rule: 'no special diet' / 'none' → omnivore 0.7 + parse_note)
DIET_NO_SPECIAL_FLAG_KEYS = {"special_diet_yn", "restrictdiet_yn", "dietary_restrictions_m3_biospecimen", "special_diet"}
# order matters: negations and intervention patterns before the plain terms they contain ('fiber free' before 'fiber', 'non-vegetarian' before 'vegetarian')
DIET_SYNONYMS = {
    "omnivore": ["omnivour", "omnivorous", "omnivores", "non_veg", "non-veg", "non veg", "nonvegetarian", "non-vegetarian", "non vegetarian", "meat consumer", "meat eater", "meat-eater",
                 "meat eaters", "eats meat", "no dietary restriction", "no dietary restrictions", "no restrictions", "unrestricted"],
    "vegetarian": ["lacto-vegetarian", "lacto vegetarian", "ovo-vegetarian", "lactovegetarian", "veggie", "vegeterarian", "vegeterian", "vegitarian", "vegetarien"],
    "vegan": ["vegans", "strictly vegan", "plant-based diet", "plant based diet"],
    "pescatarian": ["pescetarian", "pesco-vegetarian", "pesco vegetarian", "eat seafood", "eats seafood", "eats fish", "fish only", "seafood as only meat"],
    "low_carbohydrate_or_ketogenic": ["low-carbohydrate", "low carbohydrate", "lchf", "keto diet", "ketogenic diet"],
    "gluten_free": ["gluten-free diet", "gluten free diet", "no gluten", "avoids gluten"],
    "mediterranean": ["mediterranean diet", "med diet"],
    "high_fibre_or_whole_food": ["fiber rich", "fibre rich", "fiber-rich", "high-fiber", "high fiber diet", "whole foods", "whole-food", "wholefood", "prudent diet"],
    "western_or_processed": ["western diet", "western-type diet", "westernized", "westernised", "high-fat diet", "high fat diet", "standard american diet", "standard american", "processed food",
                             "processed foods", "fast-food"],
    "exclusive_breast_milk": ["exclusively breast-fed", "exclusively breast fed", "breast milk only", "breastmilk only", "mother's milk only"],
    "formula": ["formula-fed", "formula fed", "infant formula"],
    "therapeutic_or_study_diet": ["een", "exclusive enteral nutrition", "vlcd", "very low calorie", "very-low-calorie", "scd", "specific carbohydrate diet", "fiber free", "fibre free",
                                  "fiber-free", "fibre-free", "low fiber", "low-fiber", "low fibre", "low-fibre", "low protein diet", "high protein diet", "low-protein diet", "high-protein diet",
                                  "low-gluten diet", "high-gluten diet", "low gluten", "high gluten", "fasting", "elimination diet", "low fodmap", "low-fodmap", "fodmap", "enriched diet",
                                  "supplemented diet", "controlled diet", "restricted diet", "calorie restriction", "caloric restriction", "intermittent fasting", "fasting mimicking",
                                  "fmd", "enteral nutrition", "parenteral nutrition", "tpn", "ppd", "puld", "dash", "mind diet", "complementary feeding arm", "feeding arm", "formulated diet",
                                  "bioactive diet", "overfeeding", "underfeeding", "protein diet", "test diet", "experimental diet"],
}
# abbreviations the model may resolve for a closed code (the raw value must equal / contain the abbreviation as a word)
DIET_ABBREV = {"med": "mediterranean", "medi": "mediterranean", "scd": "therapeutic_or_study_diet", "een": "therapeutic_or_study_diet", "vlcd": "therapeutic_or_study_diet",
               "gfd": "gluten_free", "lchf": "low_carbohydrate_or_ketogenic", "fmd": "therapeutic_or_study_diet", "mind": "therapeutic_or_study_diet", "dash": "therapeutic_or_study_diet"}
# named restriction / pattern diets without their own code → other_diet (whole value, optional 'diet' suffix); also the only other_diet terms a
# free-text parent narrative (NARRATIVE_KEYS) may yield
NAMED_OTHER_DIETS_RE = re.compile(r"^(kosher|halal|halaal|paleo|paleolithic|primal|lactose[- _]?free|casein[- _]?free|dairy[- _]?free|gaps|fermented( foods?)?|locavore|raw[- ]food|whole ?30|"
                                  r"carnivore|flexitarian|semi[- ]?vegetarian|low[- ]?fat|low[- ]?sodium|low[- ]?salt|low[- ]?sugar|sugar[- ]?free|egg[- ]?free|soy[- ]?free|nut[- ]?free|"
                                  r"grain[- ]?free|fodmap|low[- ]?fodmap|macrobiotic|ayurvedic|jain|hindu vegetarian|organic)( diet)?$", re.I)
NARRATIVE_KEYS = {"general_diet", "other_diet", "s_specificdiet_v2", "s_specificdiet_v2_v2", "s_diet_v2", "dietary_restrictions_details_m3_biospecimen", "special_diet_hx"}
_HEDGE_RE = re.compile(r"semi|flexi|mostly|partly|part[- ]time|occasional|sometimes|try to|tries|trying|limit|reduce|mainly|generally|basically|pretty close|close to|almost", re.I)
DIET_ORDER = ["exclusive_breast_milk", "formula", "therapeutic_or_study_diet", "gluten_free", "low_carbohydrate_or_ketogenic", "pescatarian", "vegan", "vegetarian", "mediterranean",
              "high_fibre_or_whole_food", "western_or_processed", "omnivore", "other_diet"]
DIET_SPECIFICITY = {c: i for i, c in enumerate(DIET_ORDER)}  # lower = more specific (conflict resolution)
DIET_DENY = {"control", "controls", "habitual", "usual", "baseline", "placebo", "intervention", "diet", "a", "b", "c", "d", "other", "other(to be specified)", "others", "yes", "no", "1", "0", "2",
             "see online food log", "very good", "good", "healthy", "normal", "regular", "standard", "typical", "mixed", "varied", "variety", "hplc", "ncd", "wmd", "uf_of", "of_uf", "lf", "hf",
             "control diet", "habitual diet", "usual diet", "baseline diet", "midpoint", "before", "after", "farmer", "fisher", "hunter-gatherer", "hunter gatherer", "pastoralist", "agriculturalist",
             "forager", "veg", "occasional constipation"}
_DIET_DENY_RE = re.compile(r"control|habitual|baseline|placebo|midpoint|rodent|purina|chow|mouse|murine|\bveg\b", re.I)
_NON_HUMAN_RE = re.compile(r"rodent|purina|chow|mouse|murine|\brat\b|piglet|canine|feline", re.I)
NO_SPECIAL_RE = re.compile(r"^(no|none|nil|not really\.?|no\.?|no special(ised|ized)? diet|no specific diet|no special dietary (pattern|habit)s?|no diet(ary)? restrictions?|no restrictions?|"
                           r"no restricted diet|not on a special diet|not on any diet|no particular diet|regular diet|normal diet|usual diet|no restriction)$", re.I)


def diet_code_for(key: str, value, codes: dict) -> dict:
    """Deterministic diet code for (key, raw value): {code, detail, confidence, method, note} or {reject: reason} or {defer: True} (long tail
    for the utility model). Placeholders → reject 'placeholder'. Numeric / arm codes → reject 'arm_or_code'."""
    v = "" if value is None or (isinstance(value, float) and pd.isna(value)) else norm_text(value)
    lv = v.lower()
    yn = yes_no(v) if v else None
    # boolean keys are read before the placeholder test ('0' is a value of a yes/no key, not a placeholder)
    if key in DIET_FLAG_KEYS:
        code, label, conf, note = DIET_FLAG_KEYS[key]
        if yn is True:
            return dict(code=code, detail=label, confidence=conf, method="flag_key", note=note)
        if yn is False:
            return {"reject": "flag_false"}
        return {"reject": "placeholder" if is_placeholder(value) else "flag_value_unrecognised"}
    if key in DIET_NO_SPECIAL_FLAG_KEYS and yn is not None:
        if yn is False:
            return dict(code="omnivore", detail=f"no special diet ({key} = {v})", confidence=0.7, method="no_special_flag", note=f"omnivore read from a no-special-diet flag ({key} = {v})")
        return {"reject": "special_diet_unspecified"}
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", lv):
        return {"reject": "arm_or_code"}
    if re.fullmatch(r"na( \d+ weeks?)?|other\s*\(.*\)", lv) or is_placeholder(value):
        return {"reject": "placeholder"}
    if lv in DIET_DENY:
        return {"reject": "arm_or_code"}
    if _NON_HUMAN_RE.search(lv):
        return {"reject": "non_human_diet"}
    if NO_SPECIAL_RE.match(lv):
        return dict(code="omnivore", detail=v, confidence=0.7, method="no_special_statement", note="omnivore read from a no-special-diet / no-restriction statement")
    if NAMED_OTHER_DIETS_RE.match(lv):
        return dict(code="other_diet", detail=v, confidence=0.9, method="named_other_diet", note="named diet without its own code")
    hits = match_codes(lv, codes, DIET_SYNONYMS, exclude=("unknown", "other_diet"))
    codes_hit = list(dict.fromkeys(c for c, _ in hits))
    # 'non-vegetarian' states omnivore: the contained 'vegetarian' hit is not a second code
    if "omnivore" in codes_hit and re.search(r"non[-_ ]?veg", lv):
        codes_hit = [c for c in codes_hit if c not in ("vegetarian", "vegan")]
    # an intervention / negated pattern (fiber free, low-gluten diet) beats the plain term it contains
    if "therapeutic_or_study_diet" in codes_hit and len(codes_hit) > 1:
        codes_hit = ["therapeutic_or_study_diet"]
    # hedged patterns (semi-vegetarian, flexitarian, mostly …) and long free descriptions (food logs, parent narratives) are judged by the model
    if re.search(r"semi[- ]?veg|flexitarian|mostly|partly|part-time|occasional", lv):
        return {"defer": True, "why": "hedged pattern"}
    if len(lv.split()) > 8:
        return {"defer": True, "why": "long free text"}
    if len(codes_hit) == 1:
        c = codes_hit[0]
        conf = 0.9
        note = ""
        term = dict(hits)[c]
        if c == "omnivore" and term in ("no restriction", "regular diet", "normal diet", "no dietary restriction", "no dietary restrictions", "no restrictions", "unrestricted"):
            conf, note = 0.7, "omnivore read from a no-restriction statement"
        return dict(code=c, detail=v, confidence=conf, method="match_terms", note=note)
    if len(codes_hit) > 1:
        return {"defer": True, "why": "ambiguous: " + ",".join(codes_hit)}
    if lv in DIET_ABBREV:  # documented abbreviation as the whole value that is not a vocabulary synonym (MIND, Med)
        return dict(code=DIET_ABBREV[lv], detail=v, confidence=0.7, method="abbreviation", note=f"abbreviation {v!r} read as {DIET_ABBREV[lv]}")
    if not re.search(r"[a-z]{3}", lv):
        return {"reject": "arm_or_code"}
    return {"defer": True, "why": "no match"}


def build_diet_prompt(items: list[dict], codes: dict) -> str:
    voc = {c: s["label"] for c, s in codes.items() if c != "unknown"}
    return ("Classify HABITUAL DIET PATTERN attribute values (key + raw value) from human gut microbiome BioSamples into ONE vocabulary code each.\n"
            "For EVERY item return a JSON object {id, code, term, detail, is_placeholder}. Rules:\n"
            "- `code` is one of the vocabulary codes below or null when the value does not STATE a diet pattern (a study arm such as control / habitual / A / B, "
            "a numeric code, a food log with no pattern word, an infant feeding history → null).\n"
            "- `term` is the EXACT substring of the raw value that states the pattern (the shortest decisive span, e.g. 'gluten free', 'vegan diet', 'EEN'); "
            "the code is discarded unless `term` occurs verbatim in the raw value.\n"
            "- omnivore ONLY when the value states it (omnivore, non-vegetarian, eats meat, no special diet / no restrictions).\n"
            "- Diet interventions (high/low protein, fibre-free, EEN, SCD, fasting, enriched / elimination diets) → therapeutic_or_study_diet; "
            "named restriction diets without a code (paleo, kosher, dairy-free, casein-free) → other_diet; a broad description that names a pattern "
            "(e.g. 'standard american diet') → the matching code.\n"
            "- `detail` ≤ 100 characters: the diet in the source's own words. `is_placeholder` true for none / NA / not collected.\n"
            "Vocabulary: " + json.dumps(voc) + "\nReturn ONLY a JSON array.\n\nITEMS:\n" + json.dumps(items, ensure_ascii=False))


def guard_diet(o: dict, raw: str, codes: dict, key: str = "") -> tuple[str | None, str, str]:
    """Validate one model output against the raw value: (code, method, note) — code None when rejected."""
    code, term = o.get("code"), o.get("term")
    if o.get("is_placeholder") or code in (None, "", "null", "unknown"):
        return None, "utility_null", str(o.get("detail") or "")[:100]
    if code not in codes:
        return None, "utility_rejected", f"code {code!r} not in vocabulary"
    if not span_in(term, raw):
        return None, "utility_rejected", f"term {term!r} not in raw value"
    lt = norm_text(term).lower()
    narrative = key in NARRATIVE_KEYS
    if code in ("other_diet", "therapeutic_or_study_diet"):
        if lt in DIET_DENY or not re.search(r"[a-z]{4}", lt) or NO_SPECIAL_RE.match(lt) or _DIET_DENY_RE.search(lt):
            return None, "utility_rejected", f"term {term!r} is not a stated diet"
        if narrative and not NAMED_OTHER_DIETS_RE.match(lt):
            return None, "utility_rejected", f"narrative key: {term!r} is not a named diet"
        return code, "utility_open_code", ""
    if negated(norm_text(term), raw):
        return None, "utility_rejected", f"term {term!r} is negated in the raw value"
    if _HEDGE_RE.search(lt) or (narrative and _HEDGE_RE.search(norm_text(raw).lower())):
        return None, "utility_rejected", f"hedged statement {term!r} does not state the pattern"
    if narrative and len(lt.split()) > 4:
        return None, "utility_rejected", f"narrative key: {term!r} is a consumption description, not a stated pattern"
    terms = list(codes[code].get("match_terms") or []) + DIET_SYNONYMS.get(code, [])
    if any(term_in(t, lt) for t in terms):
        return code, "utility_match_term", ""
    if lt in DIET_ABBREV and DIET_ABBREV[lt] == code:
        return code, "utility_abbreviation", f"abbreviation {lt!r} → {code}"
    return None, "utility_rejected", f"term {term!r} contains no match term of {code}"


def normalise_diet(distinct: pd.DataFrame, codes: dict, llm, model: str, batch: int = 40, token_log: dict | None = None) -> pd.DataFrame:
    recs = distinct.to_dict("records")
    items = [dict(id=i, key=r["attr_key_norm"], value=r["attr_value"]) for i, r in enumerate(recs)]
    out = _llm_batches(items, lambda ch: build_diet_prompt(ch, codes), llm, model, batch, token_log)
    rows = []
    for i, r in enumerate(recs):
        o = out.get(i)
        rec = dict(r, model=model, code=None, detail=None, confidence=None, method="utility_missing", note="no model output (batch dropped the id)", model_code=None, model_term=None)
        if o is not None:
            code, method, note = guard_diet(o, r["attr_value"], codes, r["attr_key_norm"])
            rec.update(code=code, method=method, note=note, model_code=o.get("code"), model_term=o.get("term"),
                       detail=(str(o.get("detail") or r["attr_value"])[:120] if code else None),
                       confidence=(0.75 if method == "utility_match_term" else 0.7) if code else None)
            if code and method == "utility_abbreviation":
                rec["confidence"] = 0.7
        rows.append(rec)
    return pd.DataFrame(rows)


# ============================================================================================================================ smoking
SMOKING_KEYS = ["smoking_status", "smoking", "smoker", "smokingstatus", "smoking_history", "smoking_frequency", "do_you_smoke", "smoke", "tobacco_use", "nicotine_consumption",
                "tobacco", "smoking_habit", "cigarette_smoking", "current_smoker", "smoke_status", "smoking_habits", "tobacco_smoking", "smokes"]
SMOKING_BOOL_KEYS = {"do_you_smoke", "smoke", "smoker", "tobacco_use", "smoking", "current_smoker", "smokes", "tobacco", "cigarette_smoking", "tobacco_smoking"}
SMOKING_KEY_PRIORITY = {k: i for i, k in enumerate(SMOKING_KEYS)}
_PASSIVE_RE = re.compile(r"passive|second[- ]?hand|household|environment|exposure|in utero|maternal", re.I)
_DURATION_RE = re.compile(r"pack[- ]?years?|\d+\s*\+?\s*(years?|yrs?|y)\b|less than \d+|more than \d+|since \d{4}|\bago\b", re.I)
_NEVER_RE = re.compile(r"(?<![a-z])(never|non[- ]?smok\w*|nonsmoker|no smoking|not a smoker|does not smoke|doesn't smoke|never smoked|never smoker)(?![a-z])", re.I)
_FORMER_RE = re.compile(r"(?<![a-z])(former|formerly|ex[- ]?smok\w*|ex[- ]?smoker|quit\w*|past smok\w*|previous(ly)?( smok\w*)?|stopped|used to smoke|gave up)(?![a-z])", re.I)
_CURRENT_RE = re.compile(r"(?<![a-z])(current(ly)?( smok\w*)?|smoker|smokes|smoking|daily|every ?day|occasional(ly)?|regular(ly)?|light smoker|heavy smoker|social smoker|cig\w*|"
                         r"cigar\w*|tobacco|vap\w*|e-cig\w*)(?![a-z])|\d+\s*(-\s*\d+\s*)?(cigarettes?|cigs?|packs?)\s*(/|per|a)\s*(day|week)|(\d+(-\d+)?|\w+)\s*times? (a|per) (day|week)|>\s*\d+\s*cig", re.I)


def parse_smoking(value, key: str = "smoking", binary_ok: bool = False) -> dict:
    """never | former | current from a smoking attribute value: {code, confidence, note} or {reject: reason}. `binary_ok` = the (study, key)
    only holds 0/1 on a yes/no-style key (then 0 → never, 1 → current at 0.7); otherwise numeric codes are rejected (do not infer)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return {"reject": "placeholder"}
    if is_placeholder(value) and str(value).strip().lower() not in ("none", "0", "no"):
        return {"reject": "placeholder"}
    v = norm_text(value)
    lv = v.lower()
    if lv in ("", "nan", "na", "n/a", "unknown", "missing", "not collected", "not provided", "not applicable"):
        return {"reject": "placeholder"}
    if _PASSIVE_RE.search(lv):
        return {"reject": "passive_exposure"}
    if _NEVER_RE.search(lv):
        return dict(code="never", confidence=0.9, note="")
    if _FORMER_RE.search(lv):
        return dict(code="former", confidence=0.9, note="")
    # dated smoking histories ('10 cigs (1983-1993)', '2 cigarettes QD (2009-pres)'): an ended window is a former smoker, '-pres' is current
    if re.search(r"(\d{4}|unk)\s*[-–]\s*(\d{1,2}/)?\d{4}\)?", lv) and not re.search(r"\b(pres|present|current)\b", lv) and re.search(r"cig|pack|smok", lv):
        return dict(code="former", confidence=0.8, note=f"smoking window ended: '{v}'")
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", lv):
        if lv in ("0", "1") and binary_ok:
            return dict(code="never" if lv == "0" else "current", confidence=0.7, note=f"binary code {lv} on key {key} read as {'no' if lv == '0' else 'yes'}")
        return {"reject": "coded_scale" if lv in ("0", "1", "2", "3", "9") else "numeric_only"}
    if "history" in key and lv in YES:  # 'smoking history: yes' = ever-smoker (former or current) — not inferred
        return {"reject": "history_yes_ambiguous"}
    if _DURATION_RE.search(lv) and not _CURRENT_RE.search(lv):
        return {"reject": "duration_only"}
    if _DURATION_RE.search(lv) and not re.search(r"cig|smok|tobacco|pack", lv):
        return {"reject": "duration_only"}
    if lv in ("smoked",):
        return {"reject": "ambiguous_tense"}
    if _CURRENT_RE.search(lv):
        if _DURATION_RE.search(lv) and re.fullmatch(r"[\d\s+<>-]+(years?|yrs?)", lv):
            return {"reject": "duration_only"}
        return dict(code="current", confidence=0.9, note="")
    if lv in YES:
        return dict(code="current", confidence=0.8, note=f"'{v}' on smoking key {key} read as current")
    if lv in NO or lv == "none":
        return dict(code="never", confidence=0.8, note=f"'{v}' on smoking key {key} read as never")
    return {"reject": "unrecognised"}


# ============================================================================================================================ medication
# per-class yes/no keys (affirmative → the class); windows longer than "shortly before sampling" (prior_*, *_day_365, *_at_any_time*, *_brf) are excluded
MED_FLAG_KEYS = {
    "ppi": "ppi", "ppi_use": "ppi", "ppi_last_month": "ppi", "ppi_use_at_time_of_collection": "ppi", "proton_pump_inhibitor": "ppi", "ppi_1to3days_rf": "ppi", "ppi_4to14days_rf": "ppi",
    "ppi_15to30days_rf": "ppi", "med_ppi_oral_omeprazole": "ppi", "ppi_drug": "ppi", "current_ppi": "ppi", "ppi_current": "ppi",
    "metformin": "metformin", "med_metformin_oral": "metformin",
    "statins": "statin", "statines": "statin", "statin": "statin", "statin_use": "statin",
    "laxatives": "laxative", "laxative": "laxative", "supp_laxative_oral_miralax": "laxative", "laxative_use": "laxative",
    "steroids": "corticosteroid", "steroid": "corticosteroid", "corticosteroids": "corticosteroid", "corticosteroid_use": "corticosteroid", "current_steroid_use": "corticosteroid",
    "current_steroids": "corticosteroid", "steroid_used": "corticosteroid", "steroid_exposure": "corticosteroid", "steroid_course": "corticosteroid", "topical_steroids": "corticosteroid",
    "med_steroid_inhalation_any": "corticosteroid", "med_steroid_inhalation_pulmicort": "corticosteroid", "med_steroid_inhalation_beclomethasone": "corticosteroid",
    "med_steroid_topical_fluocinolone_oil": "corticosteroid",
    "immunosuppressant": "immunosuppressant_or_biologic", "immunosuppressive_use": "immunosuppressant_or_biologic", "other_immunosuppression": "immunosuppressant_or_biologic",
    "med_immunosuppressant_topical_tacrolimus": "immunosuppressant_or_biologic", "biologic_therapy": "immunosuppressant_or_biologic", "immunotherapy": "immunosuppressant_or_biologic",
    "immunotherapy_drug": "immunosuppressant_or_biologic", "biologics": "immunosuppressant_or_biologic", "immunosuppressants": "immunosuppressant_or_biologic",
    "nsaids": "nsaid_or_aspirin", "nsaid": "nsaid_or_aspirin", "nsaid_1to3days_rf": "nsaid_or_aspirin", "nsaid_4to14days_rf": "nsaid_or_aspirin", "nsaid_15to30days_rf": "nsaid_or_aspirin",
    "anti_inflammatory_drugs": "nsaid_or_aspirin", "aspirin": "nsaid_or_aspirin", "nsaid_use": "nsaid_or_aspirin",
    # (vocabulary match term "anti-inflammatory drug" is extended below with its plural)
    "chemo": "chemotherapy", "chemo_1to3days_rf": "chemotherapy", "chemo_4to14days_rf": "chemotherapy", "chemo_15to30days_rf": "chemotherapy", "chemotherapy": "chemotherapy",
    "antiretroviral_treatment": "antiretroviral", "antiretroviral_therapy": "antiretroviral", "art": "antiretroviral", "on_art": "antiretroviral",
    "probiotics": "probiotic_or_prebiotic", "probiotic_use": "probiotic_or_prebiotic", "probiotic": "probiotic_or_prebiotic",
    "antidepressants": "antidepressant_or_antipsychotic", "antihypertensives": "antihypertensive", "insulin": "antidiabetic_other",
    "other_medications": "other_medication", "medical_marijuana_oil": "other_medication", "acne_medication": "other_medication", "acne_medication_otc": "other_medication",
    "ant_diarrhoeal_drug_use": "other_medication", "indigestion_drugs": "other_medication", "anti_inflammatories_non_nsaid": "other_medication", "h2ra_drug": "other_medication",
    "other_nonbiologic_therapy": "other_medication", "medication_use": "other_medication", "on_medication": "other_medication", "takes_medication": "other_medication",
}
MED_NONE_KEYS = {"no_previous_medication", "no_medication", "medication_free", "no_current_medication"}  # yes → none_reported
# free-text medication lists (drug names → codes); study-arm values (control, placebo, FMT, numeric) never match
MED_TEXT_KEYS = ["medications", "medication", "ihmc_medication_code", "drug_usage", "other_drugs", "treatment", "treatment_type", "therapy", "etiology_drugs", "treatment_status",
                 "treatment_group", "current_medication", "current_medications", "medication_list", "drugs", "drug", "medicine", "medicines", "meds", "concomitant_medication",
                 "medication_history", "regular_medication", "prescribed_medication"]
MED_KEY_PRIORITY = {k: i for i, k in enumerate(MED_TEXT_KEYS + list(MED_FLAG_KEYS) + list(MED_NONE_KEYS))}
# documented drug-name synonyms (generic + brand) for the closed classes; abbreviations of biologics as used in IBD deposits
MED_SYNONYMS = {
    "ppi": ["omeprazol", "prilosec", "nexium", "protonix", "losec", "pantoprazol", "esomeprazol", "lansoprazol", "dexlansoprazole", "protein-pump inhibitor", "proton-pump inhibitor", "ppis"],
    "metformin": ["metformine"],
    "antidiabetic_other": ["glyburide", "glipizide", "glimepiride", "pioglitazone", "dulaglutide", "exenatide", "canagliflozin", "linagliptin", "saxagliptin", "glibenclamide", "acarbose", "insuline"],
    "statin": ["pravastatin", "lovastatin", "fluvastatin", "pitavastatin", "lipitor", "crestor", "zocor", "statins", "statines"],
    "antihypertensive": ["lisinopril", "enalapril", "ramipril", "perindopril", "captopril", "valsartan", "candesartan", "irbesartan", "telmisartan", "olmesartan", "nifedipine", "labetalol",
                         "metoprolol", "atenolol", "bisoprolol", "propranolol", "propanolol", "carvedilol", "nebivolol", "hydrochlorothiazide", "hctz", "chlorthalidone", "diltiazem", "verapamil",
                         "doxazosin", "prazosin", "clonidine", "spironolactone", "benazepril", "felodipine", "blood pressure medication", "antihypertensives"],
    "nsaid_or_aspirin": ["nsaids", "motrin", "advil", "aleve", "celecoxib", "celebrex", "meloxicam", "indomethacin", "ketoprofen", "ketorolac", "etoricoxib", "trombyl", "asa 81", "baby aspirin",
                         "acetylsalicylic", "diclofenac", "nurofen", "ibuprufen", "ibuprofene", "anti-inflammatory drugs", "anti-inflammatories"],
    "laxative": ["laxatives", "movicol", "miralax", "macrogol", "peg 3350", "colace", "docusate", "senna", "sennosides", "bisacodyl", "dulcolax", "picosulfate", "linaclotide", "lubiprostone",
                 "prucalopride", "milk of magnesia", "magnesium citrate", "fleet enema", "enema", "psyllium", "metamucil", "fybogel"],
    "corticosteroid": ["steroids", "corticosteroids", "corticosteroid", "pred", "prednison", "prednisona", "methylprednisolone", "medrol", "entocort", "cortiment", "uceris", "beclomethasone",
                       "beclometasone", "fluticasone", "flonase", "flovent", "mometasone", "triamcinolone", "fluocinolone", "clobetasol", "betamethasone", "cortisone", "pulmicort", "symbicort",
                       "advair", "deflazacort", "prednizon"],
    "immunosuppressant_or_biologic": ["mycophenolate", "mmf", "cellcept", "6-mp", "6mp", "mercaptopurine", "purinethol", "imuran", "azathioprin", "azathioprine", "azithioprine", "tacrolimus",
                                      "prograf", "cyclosporin", "ciclosporin", "sirolimus", "everolimus", "cyclophosphamide", "hydroxychloroquine", "plaquenil", "leflunomide", "remicade", "inflectra",
                                      "renflexis", "ifx", "humira", "ada", "adalimumab", "golimumab", "simponi", "certolizumab", "cimzia", "etanercept", "enbrel", "entyvio", "vdz", "vedolizumab",
                                      "stelara", "ust", "ustekinumab", "risankizumab", "tofacitinib", "xeljanz", "upadacitinib", "rinvoq", "filgotinib", "ozanimod", "natalizumab", "tysabri",
                                      "rituximab", "rituxan", "tocilizumab", "actemra", "abatacept", "orencia", "secukinumab", "ixekizumab", "dupilumab", "omalizumab", "fingolimod", "gilenya",
                                      "dimethyl fumarate", "tecfidera", "glatiramer", "copaxone", "interferon beta", "rebif", "avonex", "betaseron", "plegridy", "ocrelizumab", "ocrevus",
                                      "teriflunomide", "aubagio", "cladribine", "alemtuzumab", "anti-pd1", "anti-pd-1", "anti pd1", "anti-pd-l1", "anti pd-l1", "pd1", "pd-1", "pd-l1", "ctla4",
                                      "ctla-4", "atezolizumab", "durvalumab", "avelumab", "cemiplimab", "immune checkpoint", "icb", "ici", "immunotherapy", "anti-tnf", "tnf inhibitor", "biologic", "biologics", "mtx",
                                      "immunosuppressant", "immunosuppressants", "immunosuppressive", "belimumab", "anakinra", "basiliximab", "thymoglobulin"],
    "chemotherapy": ["chemotherapy", "oxaliplatin", "carboplatin", "paclitaxel", "docetaxel", "gemcitabine", "5-fu", "fluorouracil", "irinotecan", "folfiri", "doxorubicin",
                     "vincristine", "etoposide", "temozolomide", "asparaginase", "cytarabine", "bendamustine", "azacitidine", "decitabine", "pemetrexed", "capox", "xelox", "r-chop"],
    "antiretroviral": ["arv", "arvs", "tenofovir", "emtricitabine", "emtricelabini", "ftc/tdf", "tdf", "efavirenz", "dolutegravir", "lamivudine", "zidovudine", "abacavir", "nevirapine",
                       "lopinavir", "atazanavir", "darunavir", "raltegravir", "rilpivirine", "bictegravir", "biktarvy", "truvada", "atripla", "triumeq", "genvoya", "on art", "cart"],
    "antidepressant_or_antipsychotic": ["ssris", "snri", "citalopram", "celexa", "escitalopram", "lexapro", "sertralin", "zoloft", "fluoxetin", "prozac", "paroxetine", "paxil", "venlafaxine",
                                        "effexor", "desvenlafaxine", "pristiq", "duloxetine", "cymbalta", "bupropion", "wellbutrin", "mirtazapine", "trazodone", "amitriptyline", "nortriptyline",
                                        "quetiapine", "seroquel", "aripiprazole", "abilify", "haloperidol", "lithium", "lamotrigine", "vortioxetine", "fluvoxamine", "antidepressants",
                                        "antipsychotics", "neuroleptic"],
    "probiotic_or_prebiotic": ["probiotics", "prebiotics", "probiotika", "vsl#3", "vsl 3", "culturelle", "florastor", "saccharomyces boulardii", "lactobacillus", "bifidobacterium",
                               "inulin", "yakult", "symbiotic"],
    "hormonal_contraceptive_or_hrt": ["oral contraceptive", "oral contraceptives", "contraceptives", "the pill", "mirena", "nuvaring", "depo-provera", "norethindrone", "levonorgestrel",
                                      "hormone therapy", "hormonal therapy", "hrt"],
    "other_medication": ["medications", "drugs", "medicine", "medicines", "meds", "tablets", "prescription"],
    "none_reported": ["no medications", "no drugs", "no meds", "not on medication", "not on any medication", "no medication", "no current medication", "none reported", "no regular medication",
                      "medication free"],
}
# antibiotics are antibiotic_exposure, never a medication code: names the text pipeline ignores
ANTIBIOTIC_RE = re.compile(r"antibiot|amoxicillin|amoxycillin|augmentin|penicillin|ampicillin|clindamycin|azithromycin|erythromycin|erymax|clarithromycin|ciprofloxacin|levofloxacin|moxifloxacin|"
                           r"metronidazole|flagyl|vancomycin|rifaximin|rifampi|doxycycline|tetracycline|minocycline|cephalexin|cefuroxime|ceftriaxone|cefdinir|cefixime|cef[a-z]+|nitrofurantoin|"
                           r"macrobid|trimethoprim|sulfamethoxazole|bactrim|cotrimoxazole|gentamicin|tobramycin|amikacin|meropenem|imipenem|piperacillin|tazobactam|linezolid|fidaxomicin|"
                           r"neomycin|colistin|fosfomycin|selexid|pivmecillinam|flucloxacillin|dicloxacillin|isoniazid|ethambutol|pyrazinamide|crs3123|ridinilazole|tigecycline|daptomycin|"
                           r"chloramphenicol|streptomycin|nystatin|fluconazol|flukonazol|itraconazole|antifungal|antimalari|malarone|antiparasit|albendazole|mebendazole|ivermectin|praziquantel",
                           re.I)
MED_DENY = {"control", "controls", "placebo", "active", "fmt", "donor", "baseline", "sham", "off", "on", "naive", "treated", "treatment", "no_glycan", "glycan", "fiber mix", "fibre mix", "responder",
            "non_response", "uninfected", "healthy", "case", "cases", "yes", "no", "a", "b", "c", "cannabis", "marijuana", "weed", "alcohol", "smoking", "smt", "lf", "pb", "mag", "hf", "uc vlp",
            "healthy vlp", "pbs control", "uc stock", "healthy stock", "vlp", "stock", "surgery", "surgery only", "radiation", "radiotherapy", "diet", "exercise", "1", "0"}
# note: cannabis is recreational use, not medication; a bare 'probiotic' treatment arm IS a probiotic taken (vocabulary match term)
_HISTORY_OK_RE = re.compile(r"\b(pres|present|ongoing|current(ly)?|now|continu\w*)\b", re.I)
_HISTORY_RANGE_RE = re.compile(r"\d{4}\s*[-–]\s*(\d{1,2}/)?\d{4}|\bd/c\b|discontinued|stopped|withdrawn|\d{4}\s*[-–]\s*unk|\bprior\b|none current|previously|in the past|\bpast\b|failed", re.I)
_HISTORY_DATE_RE = re.compile(r"\(?\b(\d{1,2}/)?(19|20)\d{2}\b\)?")
_ABX_STOP = {"daily", "twice", "tablet", "tablets", "course", "weeks", "months", "since", "before", "after", "during", "with", "oral", "orally", "dose", "hospital", "infection",
             "pneumonia", "tonsilitis", "tonsillitis", "pyelonefritis", "pyelonephritis", "cough", "whooping", "sepsis", "prophylaxis", "treatment", "days", "week", "month", "year"}
_NONE_MED_RE = re.compile(r"^(no|none|nil|nothing|n/a|-|no medication(s)?|no drugs?|no meds?|not taking( any)?( medication(s)?)?|not on (any )?medication(s)?|none reported|no current medication(s)?|"
                          r"no regular medication(s)?|medication free)\.?$", re.I)
# keys whose explicit 'none' is a no-medication statement (a 'treatment: untreated' / 'therapy: none' speaks about the disease's therapy, not about medication)
MED_NONE_OK_KEYS = {"medications", "medication", "ihmc_medication_code", "drug_usage", "other_drugs", "current_medication", "current_medications", "medication_list", "drugs", "drug",
                    "medicine", "medicines", "meds", "concomitant_medication", "regular_medication", "prescribed_medication"}
_LIST_SEP_RE = re.compile(r"[,;/+]|\band\b")


def med_codes_for_text(value, codes: dict) -> dict:
    """Deterministic medication codes from a free-text value: {codes: [(code, term)], antibiotics: [terms], none: bool} or {reject: reason} / {defer: True}."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return {"reject": "placeholder"}
    if is_placeholder(value) and str(value).strip().lower() not in ("none", "no"):
        return {"reject": "placeholder"}
    v = norm_text(value)
    lv = v.lower()
    if _NONE_MED_RE.match(lv):
        return {"codes": [("none_reported", v)], "antibiotics": [], "none": True}
    if lv in MED_DENY or re.fullmatch(r"[-+]?\d+(\.\d+)?", lv) or re.fullmatch(r"person\d+\.\w+|\d{4,5}-\d{3,4}", lv):
        return {"reject": "arm_or_code"}
    # dated medication histories of IBD deposits ('ada 40 mg (3/2015-7/2015)', 'pred unk (1999-2006; d/c intoler)'): a window that ended, or a bare
    # past date, is not medication at / shortly before sampling — only '-pres' / ongoing statements are kept
    if re.search(r"(none|no|not|nothing) current(ly)?", lv):
        return {"reject": "historical_window"}
    if not _HISTORY_OK_RE.search(lv):
        if _HISTORY_RANGE_RE.search(lv):
            return {"reject": "historical_window"}
        if _HISTORY_DATE_RE.search(lv):
            return {"reject": "dated_window_unclear"}
    abx = [m.group(0) for m in ANTIBIOTIC_RE.finditer(lv)]
    hits = [(c, t) for c, t in match_codes(lv, codes, MED_SYNONYMS, exclude=("unknown", "none_reported", "other_medication")) if not ANTIBIOTIC_RE.search(t)]
    if hits:
        covered = ANTIBIOTIC_RE.sub(" ", lv)
        for _, t in hits:
            covered = _word_re(t).sub(" ", covered)
        leftover = [w for w in re.findall(r"[a-z]{4,}", covered) if w not in _ABX_STOP and w not in ("dose", "unk", "pres", "daily", "once", "twice", "weekly", "tablet", "capsule", "oral", "inhaler")]
        # a comma / semicolon list with unrecognised drug names also goes to the utility model (its codes are unioned under the guard)
        return {"codes": hits, "antibiotics": abx, "none": False, "defer_extra": bool(leftover and _LIST_SEP_RE.search(lv))}
    if abx:
        rest = [w for w in re.findall(r"[a-z]{4,}", ANTIBIOTIC_RE.sub(" ", lv)) if w not in _ABX_STOP]
        if not rest:
            return {"reject": "antibiotic_only"}
    if not re.search(r"[a-z]{3}", lv):
        return {"reject": "arm_or_code"}
    return {"defer": True, "why": "no match"}


def build_med_prompt(items: list[dict], codes: dict) -> str:
    voc = {c: s["label"] for c, s in codes.items() if c not in ("unknown",)}
    return ("Map free-text MEDICATION attribute values (key + raw value) from human gut microbiome BioSamples to medication CLASS codes.\n"
            "For EVERY item return a JSON object {id, codes, none_stated, is_arm}. `codes` is a list of {code, term} pairs: `code` from the vocabulary below, `term` the EXACT "
            "substring of the raw value naming the drug / class (brand or generic name as written, e.g. 'levaxin', 'lisinopril', 'prenatal vitamins'). A pair is discarded unless "
            "`term` occurs verbatim in the raw value. Rules:\n"
            "- ANTIBIOTICS, antifungals, antiparasitics and antimalarials are NOT medication codes here (they are curated as antibiotic_exposure): skip them.\n"
            "- Vitamins, minerals, supplements, thyroid hormone, antihistamines, asthma inhalers (non-steroid), analgesics that are not NSAIDs (acetaminophen / tylenol), "
            "antiemetics, anticonvulsants, opioids, antivirals other than HIV → other_medication with the drug name as term.\n"
            "- Recreational substances (cannabis, alcohol), study arms (control, placebo, FMT, donor, active, numeric codes), procedures (surgery, radiation) and diets → no codes; "
            "set is_arm true for study-arm / non-medication values.\n"
            "- none_stated true ONLY when the value explicitly says no medication (none / no meds / not taking).\n"
            "- Never infer a class from a diagnosis; classify only what is named.\n"
            "Vocabulary: " + json.dumps(voc) + "\nReturn ONLY a JSON array.\n\nITEMS:\n" + json.dumps(items, ensure_ascii=False))


def guard_med(o: dict, raw: str, codes: dict) -> tuple[list[tuple[str, str]], list[str], bool]:
    """Validate one model output: (accepted (code, term) pairs, rejection notes, none_stated). Antibiotic terms are dropped."""
    acc, notes = [], []
    if o.get("none_stated") and _NONE_MED_RE.match(norm_text(raw).lower()):
        return [("none_reported", raw)], [], True
    for p in o.get("codes") or []:
        if not isinstance(p, dict):
            continue
        code, term = p.get("code"), p.get("term")
        if code not in codes or code in ("unknown", "none_reported"):
            notes.append(f"code {code!r} not in vocabulary")
            continue
        if not span_in(term, raw):
            notes.append(f"term {term!r} not in raw value")
            continue
        lt = norm_text(term).lower()
        if ANTIBIOTIC_RE.search(lt):
            notes.append(f"antibiotic {term!r} skipped")
            continue
        if lt in MED_DENY or not re.search(r"[a-z]{3}", lt):
            notes.append(f"term {term!r} is not a drug name")
            continue
        acc.append((code, norm_text(term)))
    return acc, notes, False


def normalise_medication(distinct: pd.DataFrame, codes: dict, llm, model: str, batch: int = 30, token_log: dict | None = None) -> pd.DataFrame:
    recs = distinct.to_dict("records")
    items = [dict(id=i, key=r["attr_key_norm"], value=r["attr_value"]) for i, r in enumerate(recs)]
    out = _llm_batches(items, lambda ch: build_med_prompt(ch, codes), llm, model, batch, token_log, max_tokens=8000)
    rows = []
    for i, r in enumerate(recs):
        o = out.get(i)
        rec = dict(r, model=model, codes=None, terms=None, confidence=None, method="utility_missing", note="no model output (batch dropped the id)", model_raw=None)
        if o is not None:
            acc, notes, none = guard_med(o, r["attr_value"], codes)
            rec.update(model_raw=json.dumps(o.get("codes"), ensure_ascii=False)[:300], note="; ".join(notes)[:300])
            if acc:
                rec.update(codes=";".join(sorted({c for c, _ in acc})), terms="; ".join(dict.fromkeys(t for _, t in acc)), confidence=0.75, method="utility_guarded")
            elif o.get("is_arm"):
                rec.update(method="utility_arm")
            else:
                rec.update(method="utility_no_code")
        rows.append(rec)
    return pd.DataFrame(rows)


# ============================================================================================================================ bristol
BRISTOL_KEYS = ["stool_consistency_bristol_scale", "bristol_score", "bristol_stool_scale", "bristol_stool_chart", "bristol_type", "bss_stool_type", "bristol", "bss", "bsfs", "bristol_stool_score",
                "bristol_stool_form_scale", "bristol_stool_type", "stool_consistency", "stool_type", "stool_form", "bristol_scale", "stool_bristol"]
BRISTOL_KEY_RE = re.compile(r"bristol|^bss|bsfs|stool_consistency|stool_type|stool_form")
BRISTOL_KEY_PRIORITY = {k: i for i, k in enumerate(BRISTOL_KEYS)}
_BRISTOL_TEXT = {"hard": 2, "constipated": 2, "normal": 4, "formed": 4, "loose": 6, "watery": 7, "liquid": 7}


def parse_bristol(value) -> dict:
    """Bristol stool form 1–7: {value:int, confidence, note} or {reject: reason}. Integers (also '4.0', 'type 4', 'BSS 4') at 0.9; hard / normal /
    loose / watery (+ constipated, formed, liquid) → 2 / 4 / 6 / 7 at 0.7; ranges, non-integer means, 'grade-N' and other text are rejected."""
    if is_placeholder(value):
        return {"reject": "placeholder"}
    v = norm_text(value)
    lv = v.lower()
    if re.search(r"\b[1-7]\s*(-|–|to|/|or)\s*[1-7]\b", lv):
        return {"reject": "range"}
    m = re.fullmatch(r"(?:(?:bristol|bss|bsfs|type|score|scale|stool type)\s*[:=]?\s*)?(\d+)(?:\.0+)?", lv)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 7:
            return dict(value=n, confidence=0.9, note="")
        return {"reject": "out_of_range"}
    if re.fullmatch(r"\d+\.\d+", lv):
        return {"reject": "non_integer_mean"}
    if re.match(r"grade", lv):
        return {"reject": "unknown_scale"}
    words = set(re.findall(r"[a-z]+", lv))
    hits = {_BRISTOL_TEXT[w] for w in words if w in _BRISTOL_TEXT}
    if len(hits) == 1:
        return dict(value=hits.pop(), confidence=0.7, note=f"textual form '{v}' mapped to Bristol type per pack rule (hard 2 / normal 4 / loose 6 / watery 7)")
    if len(hits) > 1:
        return {"reject": "textual_ambiguous"}
    return {"reject": "textual_unmapped" if words else "unrecognised"}


# ============================================================================================================================ assembly
def det_row(sample, field, study, value, norm, conf, key, locator, quote, note="", release_id=RELEASE_ID, pv=PACKAGE_VERSION):
    return dict(sample_key=sample, field_name=field, study_accession=study, field_value=str(value), value_normalized=None if norm is None else str(norm),
                confidence=float(conf), evidence_source=f"biosample.attribute:{key}", evidence_locator=locator, evidence_quote=_q12(quote),
                evidence_limited_to_abstract=0.0, determined_by=DETERMINED_BY, route="R1", scope="sample", parse_note=(note or "")[:300], group_audit=None,
                src_track=SRC_TRACK, release_added=release_id, release_retired=None, package_added=pv)


def _pick(det: pd.DataFrame, field: str, rank_fn) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One row per sample for `field`: sort by rank_fn (lower wins), keep first; losers with a different value are conflicts."""
    d = det[det.field_name == field].copy()
    if d.empty:
        return d, d
    d["_rank"] = [rank_fn(r) for r in d.to_dict("records")]
    d = d.sort_values(["sample_key", "_rank"], kind="mergesort")
    keep = d.drop_duplicates("sample_key", keep="first")
    lost = d.loc[d.index.difference(keep.index)]
    win = keep.set_index("sample_key").value_normalized
    lost = lost.assign(winner_value=lost.sample_key.map(win))
    conflicts = lost[lost.value_normalized.astype(str) != lost.winner_value.astype(str)].drop(columns=["_rank"])
    return keep.drop(columns=["_rank"]), conflicts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--attributes", required=True, help="harvested BioSample attribute rows (parquet; diet / smoking / medication / stool keys)")
    ap.add_argument("--wide", required=True, help="package catalog_sample_metadata_wide.parquet (sample_key join)")
    ap.add_argument("--gut-runs", help="catalog_runs.parquet (run-unit sample keys)")
    ap.add_argument("--registry-runs"), ap.add_argument("--registry-sandpiper"), ap.add_argument("--gut-studies", help="build gut_runs when --gut-runs is absent")
    ap.add_argument("--out-dir", required=True), ap.add_argument("--release-id", default=RELEASE_ID), ap.add_argument("--package-version", default=PACKAGE_VERSION)
    ap.add_argument("--no-llm", action="store_true", help="skip the utility-model steps — deterministic rows only")
    ap.add_argument("--diet-map", help="reuse an existing gut_diet_map.parquet (utility rows) instead of calling the model")
    ap.add_argument("--medication-map", help="reuse an existing gut_medication_map.parquet instead of calling the model")
    ap.add_argument("--max-tokens", type=int, default=1_500_000)
    a = ap.parse_args(argv)
    return run(a)


def run(a) -> int:
    os.makedirs(a.out_dir, exist_ok=True)
    rid, pv = a.release_id, a.package_version
    cfg_dir = os.environ.get("CATALOG_CONFIG_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "..", "config"))
    diet_codes, smoking_codes, med_codes = load_vocab(cfg_dir, "diet"), load_vocab(cfg_dir, "smoking"), load_vocab(cfg_dir, "medication")
    wide = pd.read_parquet(a.wide, columns=["sample_key", "study_accession", "biosample_accession", "secondary_sample", "sample_unit", "country"])
    attrs = pd.read_parquet(a.attributes)
    gut_runs = None
    if a.gut_runs and os.path.exists(a.gut_runs):
        gut_runs = pd.read_parquet(a.gut_runs)
    elif a.registry_runs and a.gut_studies:
        gs = set(pd.read_parquet(a.gut_studies, columns=["study_accession"]).study_accession)
        sp = pd.read_parquet(a.registry_sandpiper) if a.registry_sandpiper and os.path.exists(a.registry_sandpiper) else None
        gut_runs = build_gut_runs(pd.read_parquet(a.registry_runs), sp, gs, wide)
    A = _sample_key_map(attrs, wide, gut_runs)
    A = A[A.attr_value.notna()].copy()
    A["attr_value"] = A.attr_value.astype(str).str.strip()
    A = A[A.attr_value != ""]
    keyset = set(A.attr_key_norm)
    tokens = {"input": 0, "output": 0, "cache_read": 0, "calls": 0}
    llm, model = (None, None)
    if not a.no_llm:
        llm, model = _llm(), _utility_model()
    rows, rejects, summary = [], [], {"keys_used": {}}

    def reject(r, field, reason):
        rejects.append(dict(sample_key=r.sample_key, study_accession=r.study_accession, field_name=field, attr_key_norm=r.attr_key_norm, attr_value=r.attr_value, reason=reason))

    # ---------------------------------------------------------------------------------------------------------------- diet
    dkeys = [k for k in DIET_TEXT_KEYS + list(DIET_FLAG_KEYS) + sorted(DIET_NO_SPECIAL_FLAG_KEYS) if k in keyset]
    summary["keys_used"]["diet"] = dkeys
    D = A[A.attr_key_norm.isin(dkeys)]
    det_cache, deferred = {}, {}
    for r in D.itertuples(index=False):
        ck = (r.attr_key_norm, r.attr_value)
        res = det_cache.get(ck)
        if res is None:
            res = det_cache[ck] = diet_code_for(r.attr_key_norm, r.attr_value, diet_codes)
        if "defer" in res:
            deferred[ck] = deferred.get(ck, 0) + 1
    diet_map_rows = [dict(attr_key_norm=k, attr_value=v, n_rows=0, code=res.get("code"), detail=res.get("detail"), confidence=res.get("confidence"), method=res.get("method") or ("reject:" + res["reject"] if "reject" in res else "deferred"),
                          note=res.get("note") or res.get("why") or "", model=None, model_code=None, model_term=None) for (k, v), res in det_cache.items()]
    diet_map = pd.DataFrame(diet_map_rows)
    n_by_kv = D.groupby(["attr_key_norm", "attr_value"]).size()
    diet_map["n_rows"] = [int(n_by_kv.get((k, v), 0)) for k, v in zip(diet_map.attr_key_norm, diet_map.attr_value)]
    util = None
    if deferred:
        dist = pd.DataFrame([dict(attr_key_norm=k, attr_value=v, n_rows=n) for (k, v), n in deferred.items()])
        if a.diet_map and os.path.exists(a.diet_map):
            prev = pd.read_parquet(a.diet_map)
            prev = prev[prev.method.astype(str).str.startswith("utility")]
            util = dist.merge(prev.drop(columns=["n_rows"], errors="ignore"), on=["attr_key_norm", "attr_value"], how="left")
        elif llm is not None:
            util = normalise_diet(dist, diet_codes, llm, model, token_log=tokens)
        if util is not None:
            util_idx = {(r["attr_key_norm"], r["attr_value"]): r for r in util.to_dict("records")}
            diet_map = diet_map[~diet_map.method.eq("deferred")]
            diet_map = pd.concat([diet_map, util.reindex(columns=diet_map.columns)], ignore_index=True)
            for ck, r in util_idx.items():
                det_cache[ck] = dict(code=r["code"], detail=r.get("detail"), confidence=r.get("confidence"), method=r.get("method"), note=r.get("note") or "") if isinstance(r.get("code"), str) and r["code"] else {"reject": str(r.get("method") if isinstance(r.get("method"), str) and r.get("method") else "utility_not_run")}
    for r in D.itertuples(index=False):
        res = det_cache[(r.attr_key_norm, r.attr_value)]
        if "reject" in res:
            if res["reject"] not in ("placeholder", "flag_false"):
                reject(r, "diet", res["reject"])
            continue
        if "defer" in res:
            reject(r, "diet", "utility_not_run")
            continue
        note = res.get("note") or ""
        note = (f"{res['method']}; " if res.get("method") not in ("match_terms",) else "") + note
        rows.append(det_row(r.sample_key, "diet", r.study_accession, r.attr_value, res["code"], res["confidence"], r.attr_key_norm, r.sample_acc, r.attr_value, note.strip("; "), rid, pv))
        detail = norm_text(res.get("detail") or r.attr_value)[:120]
        rows.append(det_row(r.sample_key, "diet_detail", r.study_accession, r.attr_value, detail, res["confidence"], r.attr_key_norm, r.sample_acc, r.attr_value, "raw diet statement (see diet)", rid, pv))
    diet_map.to_parquet(os.path.join(a.out_dir, "gut_diet_map.parquet"), index=False)

    # ---------------------------------------------------------------------------------------------------------------- smoking
    skeys = [k for k in SMOKING_KEYS if k in keyset]
    summary["keys_used"]["smoking_status"] = skeys
    S = A[A.attr_key_norm.isin(skeys)]
    binary_ok = {}
    for (st, k), grp in S.groupby(["study_accession", "attr_key_norm"]):
        vals = set(grp.attr_value.str.strip().str.lower())
        binary_ok[(st, k)] = k in SMOKING_BOOL_KEYS and vals <= {"0", "1"} and len(vals) == 2
    smap = {}
    for r in S.itertuples(index=False):
        b = binary_ok.get((r.study_accession, r.attr_key_norm), False)
        ck = (r.attr_key_norm, r.attr_value, b)
        res = smap.get(ck)
        if res is None:
            res = smap[ck] = parse_smoking(r.attr_value, r.attr_key_norm, binary_ok=b)
        if "reject" in res:
            if res["reject"] != "placeholder":
                reject(r, "smoking_status", res["reject"])
            continue
        rows.append(det_row(r.sample_key, "smoking_status", r.study_accession, r.attr_value, res["code"], res["confidence"], r.attr_key_norm, r.sample_acc, r.attr_value, res.get("note", ""), rid, pv))
    n_s = S.groupby(["attr_key_norm", "attr_value"]).size()
    pd.DataFrame([dict(attr_key_norm=k, attr_value=v, binary_key=b, n_rows=int(n_s.get((k, v), 0)), code=res.get("code"), confidence=res.get("confidence"),
                       method=("reject:" + res["reject"]) if "reject" in res else "parse_smoking", note=res.get("note", "")) for (k, v, b), res in smap.items()]).to_parquet(os.path.join(a.out_dir, "gut_smoking_map.parquet"), index=False)

    # ---------------------------------------------------------------------------------------------------------------- medication
    mkeys_text = [k for k in MED_TEXT_KEYS if k in keyset]
    mkeys_flag = [k for k in MED_FLAG_KEYS if k in keyset]
    mkeys_none = [k for k in MED_NONE_KEYS if k in keyset]
    summary["keys_used"]["medication"] = dict(text=mkeys_text, flag=mkeys_flag, none=mkeys_none)
    M = A[A.attr_key_norm.isin(mkeys_text + mkeys_flag + mkeys_none)]
    # per-sample accumulation: codes, contributing (key, raw, codes, conf), detail texts
    per_sample: dict[str, dict] = {}
    text_cache, deferred_m = {}, {}

    def add(r, codes_set, conf, detail_text, method):
        s = per_sample.setdefault(r.sample_key, dict(study=r.study_accession, locator=r.sample_acc, codes=set(), conf=1.0, parts=[], details=[], keys=[]))
        s["codes"] |= set(codes_set)
        s["conf"] = min(s["conf"], conf)
        s["parts"].append((MED_KEY_PRIORITY.get(r.attr_key_norm, 99), r.attr_key_norm, r.attr_value, ";".join(sorted(codes_set)), method))
        if detail_text:
            s["details"].append(detail_text)

    # text keys — deterministic first, long tail to the utility model
    T = M[M.attr_key_norm.isin(mkeys_text)]
    for r in T.itertuples(index=False):
        ck = (r.attr_key_norm, r.attr_value)
        res = text_cache.get(ck)
        if res is None:
            res = text_cache[ck] = med_codes_for_text(r.attr_value, med_codes)
        if "defer" in res or res.get("defer_extra"):
            deferred_m[ck] = deferred_m.get(ck, 0) + 1
    # flag keys whose value is free text (steroid_used = prednisone; steroids = 'budesonide 9 mg (…)') also go through the text parser
    F = M[M.attr_key_norm.isin(mkeys_flag)]
    flag_text = F[[yes_no(v) is None and not is_placeholder(v) for v in F.attr_value]]
    for r in flag_text.itertuples(index=False):
        ck = (r.attr_key_norm, r.attr_value)
        if ck not in text_cache:
            text_cache[ck] = med_codes_for_text(r.attr_value, med_codes)
        if "defer" in text_cache[ck] or text_cache[ck].get("defer_extra"):
            deferred_m[ck] = deferred_m.get(ck, 0) + 1
    med_map_rows = [dict(attr_key_norm=k, attr_value=v, n_rows=0, codes=(";".join(sorted({c for c, _ in res["codes"]})) if "codes" in res else None), terms=("; ".join(dict.fromkeys(t for _, t in res["codes"])) if "codes" in res else None),
                         antibiotics_skipped=("; ".join(res.get("antibiotics") or []) if "codes" in res else None), confidence=(0.9 if "codes" in res else None),
                         method=("match_terms+deferred" if res.get("defer_extra") else "match_terms") if "codes" in res else ("reject:" + res["reject"] if "reject" in res else "deferred"), note=res.get("why") or "", model=None, model_raw=None)
                    for (k, v), res in text_cache.items()]
    med_map = pd.DataFrame(med_map_rows)
    n_m = pd.concat([T, flag_text]).groupby(["attr_key_norm", "attr_value"]).size()
    med_map["n_rows"] = [int(n_m.get((k, v), 0)) for k, v in zip(med_map.attr_key_norm, med_map.attr_value)]
    if deferred_m:
        dist = pd.DataFrame([dict(attr_key_norm=k, attr_value=v, n_rows=n) for (k, v), n in deferred_m.items()])
        utilm = None
        if a.medication_map and os.path.exists(a.medication_map):
            prev = pd.read_parquet(a.medication_map)
            prev = prev[prev.method.astype(str).str.contains("utility")]
            utilm = dist.merge(prev.drop(columns=["n_rows"], errors="ignore"), on=["attr_key_norm", "attr_value"], how="left")
        elif llm is not None:
            utilm = normalise_medication(dist, med_codes, llm, model, token_log=tokens)
        if utilm is not None:
            med_map = med_map[~med_map.method.isin(["deferred", "match_terms+deferred"])]
            utilm_recs = utilm.to_dict("records")
            for r in utilm_recs:
                ck = (r["attr_key_norm"], r["attr_value"])
                prev_res = text_cache.get(ck) or {}
                det_codes = [(c, t) for c, t in prev_res.get("codes", [])] if "codes" in prev_res else []
                model_codes = [(c, r.get("terms") or "") for c in r["codes"].split(";")] if isinstance(r.get("codes"), str) and r["codes"] else []
                model_codes = [(c, t) for c, t in model_codes if c != "none_reported" or not det_codes]
                if det_codes or model_codes:
                    text_cache[ck] = {"codes": det_codes + [(c, t) for c, t in model_codes if c not in {d for d, _ in det_codes}], "antibiotics": prev_res.get("antibiotics", []),
                                      "none": (not det_codes) and bool(model_codes) and model_codes[0][0] == "none_reported", "utility": not det_codes, "mixed": bool(det_codes and model_codes)}
                    r["method"] = ("match_terms+" + str(r["method"])) if det_codes and not str(r["method"]).startswith("match_terms") else r["method"]
                    r["codes"] = ";".join(sorted({c for c, _ in text_cache[ck]["codes"]}))
                else:
                    text_cache[ck] = {"reject": str(r.get("method") if isinstance(r.get("method"), str) and r.get("method") else "utility_not_run")}
            utilm = pd.DataFrame(utilm_recs)
            med_map = pd.concat([med_map, utilm.reindex(columns=med_map.columns)], ignore_index=True)
    for r in T.itertuples(index=False):
        res = text_cache[(r.attr_key_norm, r.attr_value)]
        if "reject" in res:
            if res["reject"] not in ("placeholder",):
                reject(r, "medication", res["reject"])
            continue
        if "defer" in res:
            reject(r, "medication", "utility_not_run")
            continue
        if res["none"] and r.attr_key_norm not in MED_NONE_OK_KEYS:
            reject(r, "medication", "none_on_treatment_key")
            continue
        codes_set = {c for c, _ in res["codes"]}
        conf = 0.75 if res.get("utility") else (0.85 if res.get("mixed") else 0.9)
        add(r, codes_set, conf, None if res["none"] else norm_text(r.attr_value), "utility_guarded" if res.get("utility") else ("match_terms+utility" if res.get("mixed") else "match_terms"))
    for r in F.itertuples(index=False):
        code = MED_FLAG_KEYS[r.attr_key_norm]
        yn = yes_no(r.attr_value)
        if yn is True:
            add(r, {code}, 0.9, None, "flag_key")
        elif yn is False or is_placeholder(r.attr_value):
            continue
        else:  # free text under a class key: the key's class + the drug names in the text
            res = text_cache.get((r.attr_key_norm, r.attr_value), {"reject": "utility_not_run"})
            if "codes" in res and not res["none"]:
                text_codes = {c for c, _ in res["codes"]}
                # a generic class key (other_medication) defers to the specific classes named in the text
                add(r, (text_codes if code == "other_medication" else {code} | text_codes), 0.75 if res.get("utility") else 0.85, norm_text(r.attr_value), "flag_key+text")
            elif "reject" in res and res["reject"] in ("arm_or_code", "antibiotic_only"):
                reject(r, "medication", res["reject"])
            elif "reject" in res and res["reject"].startswith("utility"):
                # the model found no drug name in the text, but the class key itself states the class (value is not a negation)
                add(r, {code}, 0.8, norm_text(r.attr_value), "flag_key_text_unparsed")
            elif "codes" in res and res["none"]:
                continue
    for r in M[M.attr_key_norm.isin(mkeys_none)].itertuples(index=False):
        if yes_no(r.attr_value) is True:
            add(r, {"none_reported"}, 0.7, None, "none_key")
    for sk, s in per_sample.items():
        codes_set = set(s["codes"])
        if len(codes_set) > 1:
            codes_set.discard("none_reported")
        if not codes_set:
            continue
        s["parts"].sort()
        _, key, raw, _, method = s["parts"][0]
        note = "; ".join(f"{k}={norm_text(v)[:40]}→{c or '-'} ({m})" for _, k, v, c, m in s["parts"])
        rows.append(det_row(sk, "medication", s["study"], raw, ";".join(sorted(codes_set)), s["conf"], key, s["locator"], raw, note, rid, pv))
        if s["details"]:
            detail = "; ".join(dict.fromkeys(s["details"]))[:160]
            rows.append(det_row(sk, "medication_detail", s["study"], raw, detail, s["conf"], key, s["locator"], raw, "raw medication text (see medication)", rid, pv))
    med_map.to_parquet(os.path.join(a.out_dir, "gut_medication_map.parquet"), index=False)

    # ---------------------------------------------------------------------------------------------------------------- bristol
    bkeys = [k for k in keyset if k in BRISTOL_KEYS or BRISTOL_KEY_RE.search(k)]
    bkeys = sorted(bkeys, key=lambda k: BRISTOL_KEY_PRIORITY.get(k, 99))
    summary["keys_used"]["stool_consistency_bristol"] = bkeys
    B = A[A.attr_key_norm.isin(bkeys)]
    bcache = {}
    for r in B.itertuples(index=False):
        res = bcache.get(r.attr_value)
        if res is None:
            res = bcache[r.attr_value] = parse_bristol(r.attr_value)
        if "reject" in res:
            if res["reject"] != "placeholder":
                reject(r, "stool_consistency_bristol", res["reject"])
            continue
        rows.append(det_row(r.sample_key, "stool_consistency_bristol", r.study_accession, r.attr_value, res["value"], res["confidence"], r.attr_key_norm, r.sample_acc, r.attr_value, res.get("note", ""), rid, pv))

    # ---------------------------------------------------------------------------------------------------------------- conflicts → one row per (sample, field)
    det = pd.DataFrame(rows, columns=DET_COLS)
    keep, confl = [], []
    key_of = lambda r: r["evidence_source"].replace("biosample.attribute:", "")
    for field, rank in (("diet", lambda r: (-r["confidence"], DIET_SPECIFICITY.get(r["value_normalized"], 99), DIET_TEXT_KEYS.index(key_of(r)) if key_of(r) in DIET_TEXT_KEYS else 50)),
                        ("smoking_status", lambda r: (-r["confidence"], SMOKING_KEY_PRIORITY.get(key_of(r), 99))),
                        ("stool_consistency_bristol", lambda r: (-r["confidence"], BRISTOL_KEY_PRIORITY.get(key_of(r), 99))),
                        ("medication", lambda r: 0), ("medication_detail", lambda r: 0)):
        k, c = _pick(det, field, rank)
        keep.append(k), confl.append(c)
    # diet_detail follows the winning diet row (same evidence key)
    dw = keep[0].set_index("sample_key").evidence_source
    dd = det[det.field_name == "diet_detail"]
    dd = dd[dd.sample_key.map(dw).eq(dd.evidence_source)].drop_duplicates("sample_key")
    keep.append(dd)
    det = pd.concat(keep, ignore_index=True).reindex(columns=DET_COLS)
    conflicts = pd.concat(confl, ignore_index=True) if confl else pd.DataFrame(columns=DET_COLS + ["winner_value"])
    det.to_parquet(os.path.join(a.out_dir, "gut_r1_newfields_v2_determinations.parquet"), index=False)
    conflicts.to_parquet(os.path.join(a.out_dir, "gut_r1_newfields_v2_conflicts.parquet"), index=False)
    rej = pd.DataFrame(rejects, columns=["sample_key", "study_accession", "field_name", "attr_key_norm", "attr_value", "reason"])
    rej.to_parquet(os.path.join(a.out_dir, "gut_r1_newfields_v2_rejects.parquet"), index=False)
    summary.update(n_rows=int(len(det)), n_conflicts=int(len(conflicts)), n_rejects=int(len(rej)), llm_tokens=tokens, model=model,
                   rows_per_field=det.field_name.value_counts().to_dict(),
                   samples_per_field={f: int(g.sample_key.nunique()) for f, g in det.groupby("field_name")},
                   studies_per_field={f: int(g.study_accession.nunique()) for f, g in det.groupby("field_name")},
                   code_counts={f: det[det.field_name == f].value_normalized.value_counts().to_dict() for f in ("diet", "smoking_status", "stool_consistency_bristol")},
                   medication_code_counts=det[det.field_name == "medication"].value_normalized.str.split(";").explode().value_counts().to_dict(),
                   rejects_by_reason={f"{f}|{why}": int(n) for (f, why), n in rej.groupby(["field_name", "reason"]).size().items()} if len(rej) else {})
    json.dump(summary, open(os.path.join(a.out_dir, "gut_r1_newfields_v2_summary.json"), "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("rejects_by_reason",)}, default=str)[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
