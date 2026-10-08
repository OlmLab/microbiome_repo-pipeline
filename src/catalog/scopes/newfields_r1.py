"""Route R1 for the gut pack's NEW fields (config/packs/gut.yaml, owner request 2026-09-29; release R2026.12 / package 1.12.0):
collection_date, latitude / longitude, location_region / location_locality / location_site, lifestyle / lifestyle_detail.

Input: the harvested BioSample attribute rows of the catalog studies (sample_acc, study_accession, attr_key_norm, attr_value, …),
joined to sample_key through the package wide table (biosample_accession / secondary_sample; run-unit samples through gut_runs).
Output: data/inputs/gut/gut_r1_newfields_determinations.parquet in the determination schema (route R1, scope sample), plus the
reviewable maps gut_location_map.parquet / gut_lifestyle_map.parquet, the reject table and the conflict table.

Deterministic parsers (dates, coordinates) are pure functions and unit-tested (tests/test_newfields_r1.py). The place-name
normalisation and the lifestyle confirmation use the UTILITY model (catalog.models.resolve_model) on DISTINCT strings only; every
model output is validated token-by-token against the raw string (substring / documented abbreviation expansion / exonym) — a place
the raw string does not contain is never committed. Placeholders stay unknown (no row). infant-curation-rules: verbatim quote ≤ 12
words, labelled source, route + confidence on every row.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
import unicodedata
from datetime import date

import pandas as pd

DET_COLS = ["sample_key", "field_name", "study_accession", "field_value", "value_normalized", "confidence", "evidence_source", "evidence_locator",
            "evidence_quote", "evidence_limited_to_abstract", "determined_by", "route", "scope", "parse_note", "group_audit", "src_track",
            "release_added", "release_retired", "package_added"]
DETERMINED_BY = "gut_newfields_r1_v1"
SRC_TRACK = "gut_all_v1"
MIN_YEAR = 1980  # owner 2026-10-01: archived 1980s cohorts (pre-HIV MACS stool, early CRC biobanks) are real collection dates

# ----------------------------------------------------------------------------------------------------------------------- placeholders
PLACEHOLDER_EXACT = {"", "-", "--", "na", "n/a", "n.a.", "nan", "none", "null", "unknown", "unk", "missing", "not collected", "not applicable",
                     "not provided", "not available", "not determined", "not recorded", "not specified", "unspecified", "restricted access",
                     "restricted", "0", "1900-01-01", "1900", "0000", "0000-00-00", "tbd", "?", "n/d", "nd", "none provided", "no data", "not reported"}
PLACEHOLDER_RE = re.compile(r"^(missing(\s*:.*)?|not\s+(collected|applicable|provided|available|determined|recorded|specified|reported)|"
                            r"n/?a|unknown|restricted\s+access|none|null|nan|-+|0+|1900(-01(-01)?)?|no\s+data|to\s+be\s+(specified|determined))$", re.I)


def is_placeholder(value) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    s = str(value).strip().lower()
    return s in PLACEHOLDER_EXACT or bool(PLACEHOLDER_RE.match(s))


# ----------------------------------------------------------------------------------------------------------------------- dates
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS.update({"sept": 9})
_MON = r"(?P<mon>[A-Za-z]{3,9})\.?"


def _valid_ymd(y: int, m: int | None, d: int | None) -> bool:
    if m is not None and not 1 <= m <= 12:
        return False
    if d is not None:
        if m is None:
            return False
        try:
            date(y, m, d)
        except ValueError:
            return False
    return True


def _iso(y, m=None, d=None) -> str:
    return f"{y:04d}" + (f"-{m:02d}" if m else "") + (f"-{d:02d}" if d else "")


def _parse_single(s: str, order_hint: str | None):
    """One date token → (y, m, d, fmt, conf, note) or None. order_hint: 'MDY' / 'DMY' / None (for slash dates)."""
    s = s.strip()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2}|UTC|GMT)?)?", s)
    if m:
        fmt = "YYYY-MM-DDThh:mm" if ("T" in s or ":" in s) else "YYYY-MM-DD"
        return int(m[1]), int(m[2]), int(m[3]), fmt, 0.9, ""
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", s)
    if m:
        return int(m[1]), int(m[2]), None, "YYYY-MM", 0.9, ""
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        return int(m[1]), None, None, "YYYY", 0.9, ""
    m = re.fullmatch(r"(\d{1,2})[-\s/.]" + _MON + r"[-\s/.,]*(\d{4})", s)          # 12-Mar-2019 / 12 March 2019
    if m and m["mon"].lower() in MONTHS:
        return int(m[3]), MONTHS[m["mon"].lower()], int(m[1]), "DD-Mon-YYYY", 0.9, ""
    m = re.fullmatch(_MON + r"[-\s/.]*(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s)      # March 12, 2019 / Mar 12 2019
    if m and m["mon"].lower() in MONTHS:
        return int(m[3]), MONTHS[m["mon"].lower()], int(m[2]), "Mon-DD-YYYY", 0.9, ""
    m = re.fullmatch(_MON + r"[-\s/.,]*(\d{4})", s)                                    # Mar-2019 / March 2019 / November, 2009
    if m and m["mon"].lower() in MONTHS:
        return int(m[2]), MONTHS[m["mon"].lower()], None, "Mon-YYYY", 0.9, ""
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})", s)                  # 8/13/10 · 12/20/2010 · 08-05-2012 · 5.9.2016
    if m:
        a, b, ys = int(m[1]), int(m[2]), m[3]
        note, conf = "", 0.9
        if len(ys) == 2:
            y = 2000 + int(ys) if 2000 + int(ys) <= date.today().year else 1900 + int(ys)
            note, conf = "two-digit year expanded", 0.8
        else:
            y = int(ys)
        if a > 12 and b <= 12:
            mo, d = b, a
            fmt = "DD/MM/YYYY"
        elif b > 12 and a <= 12:
            mo, d = a, b
            fmt = "MM/DD/YYYY"
        elif a > 12 and b > 12:
            return None
        elif order_hint == "MDY":
            mo, d, fmt, conf = a, b, "MM/DD/YYYY", min(conf, 0.8)
            note = (note + "; " if note else "") + "day/month order inferred from the study's other values"
        elif order_hint == "DMY":
            mo, d, fmt, conf = b, a, "DD/MM/YYYY", min(conf, 0.8)
            note = (note + "; " if note else "") + "day/month order inferred from the study's other values"
        else:
            return y, None, None, "ambiguous D/M (year kept)", conf, (note + "; " if note else "") + "day/month order ambiguous; year only committed"
        return y, mo, d, fmt, conf, note
    return None


def parse_collection_date(value, max_year: int | None = None, order_hint: str | None = None) -> dict:
    """Deterministic collection-date parser.

    Returns {"value": ISO partial string, "confidence": float, "fmt": str, "note": str} on success or
    {"reject": reason, "fmt": str} when the value is a placeholder, unparseable, impossible or out of range
    (year < 1980 or > max_year — the run's first_public year; default = today's year). Intervals 'A/B' (YYYY/YYYY,
    YYYY-MM/YYYY-MM, YYYY-MM-DD/YYYY-MM-DD) keep both ends; start must not be after end.
    """
    if is_placeholder(value):
        return {"reject": "placeholder", "fmt": "placeholder"}
    s = " ".join(str(value).split())
    max_year = max_year or date.today().year
    parts = None
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?\s*/\s*\d{4}(-\d{2}(-\d{2})?)?", s):
        parts = [p.strip() for p in s.split("/")]
    elif re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?\s*(?:to|–|—|-)\s*\d{4}(-\d{2}(-\d{2})?)?", s) and not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", s):
        parts = [p.strip() for p in re.split(r"\s*(?:to|–|—|-)\s*", s) if p.strip()]
        if len(parts) != 2:
            parts = None
    if parts:
        ends = [_parse_single(p, order_hint) for p in parts]
        if any(e is None for e in ends):
            return {"reject": "unparseable interval", "fmt": "interval?"}
        for y, mo, d, *_ in ends:
            if not _valid_ymd(y, mo, d):
                return {"reject": "impossible date", "fmt": "interval"}
            if y < MIN_YEAR:
                return {"reject": f"year < {MIN_YEAR}", "fmt": "interval"}
            if y > max_year:
                return {"reject": "year after first_public", "fmt": "interval"}
        a, b = ends
        if _iso(a[0], a[1], a[2]) > _iso(b[0], b[1], b[2]):
            return {"reject": "interval start after end", "fmt": "interval"}
        return {"value": _iso(a[0], a[1], a[2]) + "/" + _iso(b[0], b[1], b[2]), "confidence": 0.9, "fmt": f"interval {a[3]}/{b[3]}", "note": "stated sampling period"}
    r = _parse_single(s, order_hint)
    if r is None:
        return {"reject": "unparseable", "fmt": "unparseable"}
    y, mo, d, fmt, conf, note = r
    if not _valid_ymd(y, mo, d):
        return {"reject": "impossible date", "fmt": fmt}
    if y < MIN_YEAR:
        return {"reject": f"year < {MIN_YEAR}", "fmt": fmt}
    if y > max_year:
        return {"reject": "year after first_public", "fmt": fmt}
    return {"value": _iso(y, mo, d), "confidence": conf, "fmt": fmt, "note": note}


def infer_dm_order(values) -> str | None:
    """'MDY' / 'DMY' / None from a study's slash-date values: any first field > 12 → DMY, any second field > 12 → MDY (never both)."""
    mdy = dmy = False
    for v in values:
        m = re.fullmatch(r"\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})\s*", str(v))
        if not m:
            continue
        a, b = int(m[1]), int(m[2])
        if a > 12 and b <= 12:
            dmy = True
        elif b > 12 and a <= 12:
            mdy = True
    if mdy and not dmy:
        return "MDY"
    if dmy and not mdy:
        return "DMY"
    return None


# ----------------------------------------------------------------------------------------------------------------------- coordinates
_NUM = r"[-+]?\d+(?:\.\d+)?"
_DEG = r"(?P<d>\d{1,3})\s*[°º:\s]\s*(?P<m>\d{1,2}(?:\.\d+)?)?\s*['′:]?\s*(?P<s>\d{1,2}(?:\.\d+)?)?\s*[\"″]?"


def _dms(d, m, s):
    return float(d) + (float(m) if m else 0) / 60 + (float(s) if s else 0) / 3600


def parse_coord(value, kind: str) -> float | None:
    """Single latitude ('50.65', '-34.6', '93.2 W', '38 54 N', 'N 38.9') → signed decimal degrees, or None. kind = 'lat' | 'lon'."""
    if is_placeholder(value):
        return None
    s = re.sub(r"(\d\.)\s+(\d)", r"\1\2", str(value).strip().replace("\u2212", "-").replace("\ufffd", "°").replace("_", "-"))
    if re.fullmatch(r"[-+]?\d+,\d+", s):          # decimal comma ('11,57')
        s = s.replace(",", ".")
    hemi_pat = "NS" if kind == "lat" else "EW"
    m = re.fullmatch(rf"(?P<h1>[{hemi_pat}])?\s*(?P<n>{_NUM})\s*(?:°|deg|degrees)?\s*(?P<h2>[{hemi_pat}])?", s, re.I)
    if m and (m["h1"] or m["h2"] or True):
        v = float(m["n"])
        h = (m["h1"] or m["h2"] or "").upper()
        if h in ("S", "W"):
            v = -abs(v)
        elif h in ("N", "E"):
            v = abs(v)
    else:
        m = re.fullmatch(rf"(?P<h1>[{hemi_pat}])?\s*{_DEG}\s*(?P<h2>[{hemi_pat}])?", s, re.I)
        if not m:
            return None
        v = _dms(m["d"], m["m"], m["s"])
        h = (m["h1"] or m["h2"] or "").upper()
        if h in ("S", "W"):
            v = -v
    lim = 90 if kind == "lat" else 180
    return round(v, 4) if -lim <= v <= lim else None


def parse_lat_lon(value) -> dict:
    """Combined lat_lon string → {"lat", "lon", "fmt"} or {"reject": reason}.
    Accepts '38.9 N 77.0 W', '23.7N 113.15E', '38.9,-77.0', '38.9 -77.0', "38°54'N 77°02'W", 'N38.9 W77.0', '38.9 N, 77.0 W'."""
    if is_placeholder(value):
        return {"reject": "placeholder"}
    s = " ".join(str(value).replace("\u2212", "-").split())
    # longitude written first ('108.93 E 34.27 N'): the hemisphere letters decide, so swap the halves
    ml = re.fullmatch(rf"(?P<h1>[EW])?\s*(?P<a>{_NUM})\s*°?\s*(?P<h2>[EW])?\s*[,;]?\s*(?P<h3>[NS])?\s*(?P<b>{_NUM})\s*°?\s*(?P<h4>[NS])?", s, re.I)
    if ml and (ml["h1"] or ml["h2"]) and (ml["h3"] or ml["h4"]):
        s = f"{ml['h3'] or ''}{ml['b']}{ml['h4'] or ''} {ml['h1'] or ''}{ml['a']}{ml['h2'] or ''}"
    # decimal + hemisphere letters, letters either side
    m = re.fullmatch(rf"(?P<h1>[NS])?\s*(?P<a>{_NUM})\s*°?\s*(?P<h2>[NS])?\s*[,;]?\s*(?P<h3>[EW])?\s*(?P<b>{_NUM})\s*°?\s*(?P<h4>[EW])?", s, re.I)
    if m and (m["h1"] or m["h2"]) and (m["h3"] or m["h4"]):
        lat, lon = abs(float(m["a"])), abs(float(m["b"]))
        if (m["h1"] or m["h2"]).upper() == "S":
            lat = -lat
        if (m["h3"] or m["h4"]).upper() == "W":
            lon = -lon
        fmt = "DD.dd H DD.dd H"
    else:
        m = re.fullmatch(rf"(?P<a>{_NUM})\s*[,;]\s*(?P<b>{_NUM})|(?P<c>{_NUM})\s+(?P<d>{_NUM})", s)
        if m:
            lat, lon = float(m["a"] or m["c"]), float(m["b"] or m["d"])
            fmt = "DD.dd, DD.dd (signed)"
        else:
            m = re.fullmatch(rf"(?P<h1>[NS])?\s*{_DEG}\s*(?P<h2>[NS])?\s*[,;]?\s*(?P<h3>[EW])?\s*{_DEG.replace('?P<d>', '?P<d2>').replace('?P<m>', '?P<m2>').replace('?P<s>', '?P<s2>')}\s*(?P<h4>[EW])?", s, re.I)
            if not m or not ((m["h1"] or m["h2"]) and (m["h3"] or m["h4"])):
                return {"reject": "unparseable"}
            lat, lon = _dms(m["d"], m["m"], m["s"]), _dms(m["d2"], m["m2"], m["s2"])
            if (m["h1"] or m["h2"]).upper() == "S":
                lat = -lat
            if (m["h3"] or m["h4"]).upper() == "W":
                lon = -lon
            fmt = "DMS"
    if not (-90 <= lat <= 90):
        return {"reject": "latitude out of range"}
    if not (-180 <= lon <= 180):
        return {"reject": "longitude out of range"}
    if lat == 0 and lon == 0:
        return {"reject": "(0,0)"}
    return {"lat": round(lat, 4), "lon": round(lon, 4), "fmt": fmt}


# ----------------------------------------------------------------------------------------------------------------------- places
US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
             "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
             "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
             "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
             "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
             "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
             "WY": "Wyoming", "DC": "District of Columbia", "PR": "Puerto Rico"}
CN_PROVINCES = {"BJ": "Beijing", "SH": "Shanghai", "TJ": "Tianjin", "CQ": "Chongqing", "HE": "Hebei", "SX": "Shanxi", "NM": "Inner Mongolia", "LN": "Liaoning",
                "JL": "Jilin", "HL": "Heilongjiang", "JS": "Jiangsu", "ZJ": "Zhejiang", "AH": "Anhui", "FJ": "Fujian", "JX": "Jiangxi", "SD": "Shandong", "HA": "Henan",
                "HB": "Hubei", "HN": "Hunan", "GD": "Guangdong", "GX": "Guangxi", "HI": "Hainan", "SC": "Sichuan", "GZ": "Guizhou", "YN": "Yunnan", "XZ": "Tibet",
                "SN": "Shaanxi", "GS": "Gansu", "QH": "Qinghai", "NX": "Ningxia", "XJ": "Xinjiang", "HK": "Hong Kong", "MO": "Macau", "TW": "Taiwan"}
OTHER_ABBREV = {"KL": "Kuala Lumpur", "NYC": "New York City", "UK": "United Kingdom", "NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "WA_AU": "Western Australia",
                "SA": "South Australia", "TAS": "Tasmania", "ACT": "Australian Capital Territory", "NT": "Northern Territory", "BC": "British Columbia", "ON": "Ontario",
                "QC": "Quebec", "AB": "Alberta", "MB": "Manitoba", "SK": "Saskatchewan", "NS": "Nova Scotia", "NB": "New Brunswick", "NL": "Newfoundland and Labrador"}
COUNTRY_PREFIX_KEYS = {"geo_loc_name", "geographic_location", "geographical_location", "geographic_location_region_and_locality",
                       "geographic_location_country_and_or_sea_region", "geographic_location_country_region_area", "geographic_location_country_and_or_sea"}
# keys whose values are places (recruitment geography), in the order they were inventoried on the 1.11.0 attribute rows (report lists them)
LOCATION_KEYS = ["geo_loc_name", "geographic_location_region_and_locality", "geographic_location_country_and_or_sea_region", "geographic_location",
                 "geographical_location", "geographic_location_country_region_area", "collection_site", "hospital", "city", "town", "village", "village_grouping",
                 "states", "state", "province", "district", "county", "prefecture", "location_within_the_united_kingdom", "site", "study_site", "region",
                 "census_region", "locality", "name_of_the_sampling_site", "sampling_site", "recruitment_site", "clinic", "host_country_of_residence"]
LIFESTYLE_KEYS = ["lifestyle", "urban", "urban_rural", "rural_urban", "rural_urban_status", "rural", "community_type", "subsistence", "subsistence_strategy",
                  "host_diet", "diet", "special_diet", "diet_type", "population", "community", "tribe", "ethnic_group_lifestyle"]
DIET_ONLY_KEYS = {"host_diet", "diet", "special_diet", "diet_type"}
POPULATION_KEYS = {"population", "community", "tribe"}


def split_country_prefix(raw: str, key: str) -> tuple[str | None, str]:
    """INSDC 'country: region, locality' values → (country token, remainder). Only for the geo_loc_name-style keys."""
    s = " ".join(str(raw).split())
    if key in COUNTRY_PREFIX_KEYS and ":" in s:
        c, _, rest = s.partition(":")
        return c.strip(), rest.strip(" ,;:")
    return None, s


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _fold(s)) if t]


def validate_place(raw: str, out: str | None, exonyms: dict, key_hint: str = "") -> tuple[bool, str]:
    """Every token of a model output must be a substring of the raw string, a documented abbreviation expansion of a raw token,
    or an exonym declared for a raw token. Returns (ok, reason)."""
    if not out:
        return True, ""
    fr = _fold(raw)
    expansions = {}
    for tok in re.findall(r"[A-Za-z]{2,4}", raw):
        for table in (US_STATES, CN_PROVINCES, OTHER_ABBREV):
            if tok.upper() in table:
                expansions[_fold(table[tok.upper()])] = tok
    # a declared exonym must resemble its raw token (SequenceMatcher ratio >= 0.5 on the folded strings): the model may not smuggle in a
    # different place through the exonym channel ('TUW' -> 'Tuvalu' is rejected; 'Kraków' <- 'Cracow', 'Ulaanbaatar' <- 'Ulan Bator' pass)
    import difflib
    ex_folded = {_fold(v): _fold(k) for k, v in exonyms.items() if difflib.SequenceMatcher(None, _fold(k), _fold(v)).ratio() >= 0.5}  # english → raw token
    fo = _fold(out)
    if fo in fr:
        return True, "substring"
    if fo in expansions:
        return True, "abbreviation"
    if fo in ex_folded and ex_folded[fo] in fr:
        return True, "exonym"
    # word-wise: every word must be covered by one of the three
    for w in _tokens(out):
        if w in fr:
            continue
        if any(w in e for e in expansions):
            continue
        if any(w in e and ex_folded[e] in fr for e in ex_folded):
            continue
        return False, f"token {w!r} not in raw"
    return True, "tokens"


def build_location_prompt(items: list[dict]) -> str:
    return ("You normalise raw sample-location strings from public sequencing archives into three fields for a metadata catalog.\n"
            "For EVERY item return one JSON object with keys: id, raw, region, locality, site, is_placeholder, exonyms, note.\n"
            "- region: first-level administrative division below the country (state, province, prefecture, canton, county for UK/Ireland). English exonym.\n"
            "- locality: city, town, village or district where subjects were recruited. English exonym.\n"
            "- site: a named recruitment site (hospital, clinic, university, cohort field station, tribe/community name) exactly as written.\n"
            "- Use ONLY places that appear in the raw string. You MAY expand two-letter US state or Chinese province abbreviations (CA -> California) and\n"
            "  render non-English names as their English exonym (Koebenhavn -> Copenhagen); list every such rendering in exonyms as {raw_token: english}.\n"
            "- NEVER add a country, and NEVER guess a region or city that the raw string does not name. Anatomical sites, sample codes, numbers,\n"
            "  'control', 'missing', 'not applicable' are placeholders: set is_placeholder true and leave the fields null.\n"
            "- A bare country name (after the country prefix was removed) has no region/locality: leave them null.\n"
            "- country_hint is the sample's country (ISO2) if known — use it only to pick the exonym, never to add a place.\n"
            "Return ONLY a JSON array, no prose.\n\nITEMS:\n" + json.dumps(items, ensure_ascii=False))


def _parse_json_array(text: str) -> list:
    t = text.strip()
    a, b = t.find("["), t.rfind("]")
    if a < 0 or b < 0:
        return []
    try:
        return json.loads(t[a:b + 1])
    except json.JSONDecodeError:
        return []


def normalise_locations(distinct: pd.DataFrame, llm, model: str, batch: int = 40, token_log: dict | None = None) -> pd.DataFrame:
    """distinct: columns raw, attr_key_norm, remainder (country prefix removed), country_hint, n_samples. Adds region / locality / site /
    is_placeholder / note / valid / exonyms via the utility model in batches; outputs failing validate_place are dropped (valid=False)."""
    rows = []
    recs = distinct.to_dict("records")
    for i in range(0, len(recs), batch):
        chunk = recs[i:i + batch]
        items = [dict(id=j, raw=r["remainder"], key=r["attr_key_norm"], country_hint=r.get("country_hint") or "") for j, r in enumerate(chunk)]
        res = llm(build_location_prompt(items), model=model, max_tokens=8000)
        if token_log is not None:
            u = res.get("usage") or {}
            token_log["input"] = token_log.get("input", 0) + int(u.get("input_tokens", 0) or 0)
            token_log["output"] = token_log.get("output", 0) + int(u.get("output_tokens", 0) or 0)
            token_log["calls"] = token_log.get("calls", 0) + 1
        out = {o.get("id"): o for o in _parse_json_array(res.get("text", "")) if isinstance(o, dict)}
        for j, r in enumerate(chunk):
            o = out.get(j)
            rec = dict(r, model=model, region=None, locality=None, site=None, is_placeholder=None, note=None, valid=False, exonyms="{}", rejected_fields="")
            if o is None:
                rec["note"] = "no model output (batch dropped the id)"
                rows.append(rec)
                continue
            ex = o.get("exonyms") if isinstance(o.get("exonyms"), dict) else {}
            rec.update(is_placeholder=bool(o.get("is_placeholder")), note=str(o.get("note") or "")[:200], exonyms=json.dumps(ex, ensure_ascii=False))
            if rec["is_placeholder"]:
                rec["valid"] = True
                rows.append(rec)
                continue
            bad = []
            for f in ("region", "locality", "site"):
                v = o.get(f)
                v = " ".join(str(v).split()) if v not in (None, "", "null") else None
                if v is not None and len(v) > (120 if f == "site" else 80):
                    v = v[:120 if f == "site" else 80]
                ok, why = validate_place(r["remainder"], v, ex)
                if ok:
                    rec[f] = v
                    if v is not None and why in ("abbreviation", "exonym", "tokens"):
                        rec["note"] = (rec["note"] + "; " if rec["note"] else "") + f"{f}: {why}"
                else:
                    bad.append(f"{f}={v!r} ({why})")
            # a field the validator rejects is dropped on its own; the other fields of the string stay usable
            rec["valid"] = True
            rec["rejected_fields"] = "; ".join(bad)
            if bad:
                rec["note"] = (rec["note"] + "; " if rec["note"] else "") + "validator rejected: " + "; ".join(bad)
            rows.append(rec)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------------------------------------------------- lifestyle
def load_lifestyle_vocab(cfg_dir: str) -> dict:
    import yaml
    return yaml.safe_load(open(os.path.join(cfg_dir, "vocab", "lifestyle.yaml"), encoding="utf-8"))["codes"]


def lifestyle_code_for(key: str, value: str, codes: dict) -> tuple[str | None, str | None, str]:
    """Deterministic lifestyle code from (key, value): (code, detail, method) or (None, None, reason). Diet keys only map an EXACT
    vegan / vegetarian value; urban/rural keys map their two values; other keys match the vocabulary's match_terms on whole words."""
    if is_placeholder(value):
        return None, None, "placeholder"
    v = " ".join(str(value).split())
    lv = v.lower()
    if key in DIET_ONLY_KEYS:
        if re.fullmatch(r"(strict(ly)?\s+)?(vegan|vegetarian|lacto-?vegetarian|ovo-?vegetarian|lacto-?ovo-?vegetarian|plant-?based)( diet)?", lv):
            return "vegetarian_or_vegan", v, "diet_exact"
        return None, None, "diet value is not a lifestyle"
    if key in ("urban", "urban_rural", "rural_urban", "rural_urban_status", "community_type", "rural"):
        if lv in ("urban", "city", "metropolitan"):
            return "urban_industrialized", v, "urban_rural_key"
        if lv in ("rural", "non-urban", "nonurban", "village", "remote"):
            return "rural_non_industrialized", v, "urban_rural_key"
        if lv in ("semi-urban", "peri-urban", "periurban", "suburban", "transitional"):
            return "transitional_or_migrant", v, "urban_rural_key"
        return None, None, "urban/rural value not recognised"
    if key == "tribe":  # the key itself states tribal membership; the value is the tribe's name (lifestyle_detail)
        return "indigenous_community", v, "tribe_key"
    hits = []
    for code, spec in codes.items():
        for t in spec.get("match_terms") or []:
            if re.search(r"(?<![a-z0-9])" + re.escape(t.lower()) + r"(?![a-z0-9])", lv):
                hits.append(code)
                break
    hits = list(dict.fromkeys(hits))
    if len(hits) == 1:
        return hits[0], v, "match_terms"
    if len(hits) > 1:
        return None, None, "ambiguous: " + ",".join(hits)
    return None, None, "no match"


def build_lifestyle_prompt(items: list[dict], codes: dict) -> str:
    voc = {c: s["label"] for c, s in codes.items()}
    return ("Confirm lifestyle codes for population / community / tribe / lifestyle attribute values from a human gut microbiome catalog.\n"
            "For EVERY item return a JSON object {id, code, confirm, note}. `code` must be one of the vocabulary codes below or null when the value does not\n"
            "STATE a lifestyle (an ethnicity, a study arm, a sample code or a city is NOT a lifestyle -> null). `confirm` true only when the value itself\n"
            "names a population known for that lifestyle (Hadza -> hunter_gatherer, Hutterite -> isolated_religious_community) or states it in words.\n"
            "Vocabulary: " + json.dumps(voc) + "\nReturn ONLY a JSON array.\n\nITEMS:\n" + json.dumps(items, ensure_ascii=False))


def confirm_lifestyle(cands: pd.DataFrame, codes: dict, llm, model: str, batch: int = 40, token_log: dict | None = None) -> pd.DataFrame:
    """Utility-model confirmation of the deterministic match_terms hits on population-style keys (and a code proposal for unmatched values).
    A code is kept only when the model confirms it AND (deterministic hit == model code, or there was no deterministic hit and the model's code
    has a match_term in the value or the value is a named population)."""
    rows = []
    recs = cands.to_dict("records")
    for i in range(0, len(recs), batch):
        chunk = recs[i:i + batch]
        items = [dict(id=j, key=r["attr_key_norm"], value=r["attr_value"], deterministic_code=r.get("code")) for j, r in enumerate(chunk)]
        res = llm(build_lifestyle_prompt(items, codes), model=model, max_tokens=6000)
        if token_log is not None:
            u = res.get("usage") or {}
            token_log["input"] = token_log.get("input", 0) + int(u.get("input_tokens", 0) or 0)
            token_log["output"] = token_log.get("output", 0) + int(u.get("output_tokens", 0) or 0)
            token_log["calls"] = token_log.get("calls", 0) + 1
        out = {o.get("id"): o for o in _parse_json_array(res.get("text", "")) if isinstance(o, dict)}
        for j, r in enumerate(chunk):
            o = out.get(j) or {}
            mc = o.get("code") if o.get("code") in codes else None
            confirmed = bool(o.get("confirm")) and mc is not None and mc not in ("unknown",)
            det = r.get("code")
            det = det if isinstance(det, str) and det else None
            final = None
            method = r.get("method")
            if det and det == mc:
                final, method = det, f"{method}+utility_agrees"
            elif confirmed and not det:
                final, method = mc, "utility_named_population"
            elif det and not mc:
                method = f"{method}; utility proposed no code"
            elif det and mc and det != mc:
                method = f"{method} {det} vs utility {mc}: disagreement"
            rows.append(dict(r, code=final, method=method, model=model, model_code=mc, model_note=str(o.get("note") or "")[:200]))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------------------------------------------------- assembly
def _q12(v):
    s = "" if v is None else str(v)
    return " ".join(s.split()[:12])[:200]


def det_row(sample, field, study, value, norm, conf, key, locator, quote, note="", release_id="R2026.12", pv="1.12.0"):
    return dict(sample_key=sample, field_name=field, study_accession=study, field_value=str(value), value_normalized=None if norm is None else str(norm),
                confidence=float(conf), evidence_source=f"biosample.attribute:{key}", evidence_locator=locator, evidence_quote=_q12(quote),
                evidence_limited_to_abstract=0.0, determined_by=DETERMINED_BY, route="R1", scope="sample", parse_note=note or "", group_audit=None,
                src_track=SRC_TRACK, release_added=release_id, release_retired=None, package_added=pv)


def build_gut_runs(registry_runs: pd.DataFrame, sandpiper: pd.DataFrame | None, gut_studies: set, wide: pd.DataFrame,
                   infant_runs: pd.DataFrame | None = None, ncbi_bases: pd.DataFrame | None = None, est_bases: pd.DataFrame | None = None) -> pd.DataFrame:
    """gut_runs: one row per run of a catalog study (registry_runs ∩ gut studies) with sample_key from the wide table
    (run-unit samples are keyed by the run accession; biosample-unit samples by biosample_accession / secondary_sample).

    R2026.16 (owner: every sample must have a depth): (1) a catalog sample with no run in its own study takes the registry runs of the
    same BioSample filed under another study (run_study_accession keeps the archive's study); (2) samples still without a run take
    their runs from the curated infant runs table; (3) runs whose ENA base_count is 0 / blank (dbGaP-protected or not yet mirrored)
    take NCBI SRA runinfo bases (base_count_source = ncbi_sra)."""
    cols = ["run_accession", "study_accession", "sample_accession", "secondary_sample_accession", "experiment_accession", "library_name", "library_strategy",
            "library_source", "library_layout", "instrument_platform", "instrument_model", "read_count", "base_count", "first_public"]
    m1 = dict(zip(wide.biosample_accession, wide.sample_key))
    sec = wide[wide.secondary_sample.notna()]
    m2 = dict(zip(sec.secondary_sample, sec.sample_key))
    runk = set(wide.loc[wide.sample_unit == "run", "sample_key"])
    wstudy = dict(zip(wide.sample_key, wide.study_accession))

    def keyed(df):
        df = df.copy()
        sk = df.sample_accession.map(m1)
        if "secondary_sample_accession" in df.columns:
            sk = sk.where(sk.notna(), df.secondary_sample_accession.map(m2))
        df["sample_key"] = df.run_accession.where(df.run_accession.isin(runk), sk)
        return df

    rr = registry_runs[[c for c in cols if c in registry_runs.columns]]
    r = keyed(rr[rr.study_accession.isin(gut_studies)])
    r["run_study_accession"] = r["study_accession"]
    have = set(r.sample_key.dropna())
    # (1) same BioSample, run filed under another study
    other = rr[~rr.study_accession.isin(gut_studies) & (rr.sample_accession.isin(m1) | rr.get("secondary_sample_accession", pd.Series(dtype=str)).isin(m2))]
    if len(other):
        o = keyed(other)
        o = o[o.sample_key.notna() & ~o.sample_key.isin(have)]
        o["run_study_accession"] = o["study_accession"]
        o["study_accession"] = o.sample_key.map(wstudy)
        r = pd.concat([r, o], ignore_index=True)
        have |= set(o.sample_key)
    # (2) curated infant runs table
    if infant_runs is not None and len(infant_runs):
        ir = keyed(infant_runs[[c for c in cols if c in infant_runs.columns]])
        ir = ir[ir.sample_key.notna() & ~ir.sample_key.isin(have) & ~ir.run_accession.isin(set(r.run_accession))]
        if len(ir):
            ir["run_study_accession"] = ir["study_accession"]
            ir["study_accession"] = ir.sample_key.map(wstudy)
            r = pd.concat([r, ir], ignore_index=True)
    r = r.drop_duplicates("run_accession", keep="first")
    # (3) NCBI SRA bases where ENA has none
    bc = pd.to_numeric(r.base_count, errors="coerce")
    r["base_count_source"] = "ena"
    if ncbi_bases is not None and len(ncbi_bases):
        nb = ncbi_bases.drop_duplicates("run_accession", keep="last").set_index("run_accession")
        nbb = pd.to_numeric(r.run_accession.map(nb.ncbi_bases), errors="coerce")
        fill = (bc.fillna(0) <= 0) & (nbb.fillna(0) > 0)
        bc = bc.where(~fill, nbb)
        r.loc[fill, "base_count_source"] = "ncbi_sra"
        if "read_count" in r.columns:
            rc = pd.to_numeric(r.read_count, errors="coerce")
            nbs = pd.to_numeric(r.run_accession.map(nb.ncbi_spots), errors="coerce")
            r["read_count"] = rc.where(~(fill & (rc.fillna(0) <= 0)), nbs)
    # (4) last resort: an estimate from the submitted file size (ENA has not processed the files; NCBI has no record) — flagged
    if est_bases is not None and len(est_bases):
        eb = pd.to_numeric(r.run_accession.map(est_bases.drop_duplicates("run_accession").set_index("run_accession").est_bases), errors="coerce")
        fill = (bc.fillna(0) <= 0) & (eb.fillna(0) > 0)
        bc = bc.where(~fill, eb)
        r.loc[fill, "base_count_source"] = "estimate_submitted_bytes"
    r["base_count"] = bc
    r.loc[bc.fillna(0) <= 0, "base_count_source"] = "none"
    if sandpiper is not None and len(sandpiper):
        sp = sandpiper.drop_duplicates("run_accession").set_index("run_accession").sandpiper_profiled
        r["sandpiper_profiled"] = r.run_accession.map(sp).fillna(False).astype(bool)
    else:
        r["sandpiper_profiled"] = False
    return r.reset_index(drop=True)


def sample_depth(gut_runs: pd.DataFrame) -> pd.DataFrame:
    """Per-sample sequencing summary: n_runs, seq_gbp (bases summed over the sample's runs / 1e9), seq_reads, seq_depth_source."""
    g = gut_runs.dropna(subset=["sample_key"]).assign(_b=lambda d: pd.to_numeric(d.base_count, errors="coerce"), _r=lambda d: pd.to_numeric(d.read_count, errors="coerce"))
    agg = g.groupby("sample_key").agg(n_runs=("run_accession", "size"), seq_gbp=("_b", lambda s: s.sum(min_count=1) / 1e9), seq_reads=("_r", lambda s: s.sum(min_count=1)),
                                      seq_depth_source=("base_count_source", lambda s: ";".join(sorted(set(s) - {"none"})) or "none"))
    agg.loc[agg.seq_gbp.fillna(0) <= 0, "seq_gbp"] = None
    return agg


def _sample_key_map(attrs: pd.DataFrame, wide: pd.DataFrame, gut_runs: pd.DataFrame | None) -> pd.DataFrame:
    """attribute rows → (sample_key, study_accession) — a BioSample that backs several run-unit samples maps to each of them."""
    m1 = dict(zip(wide.biosample_accession, wide.sample_key))
    sec = wide[wide.secondary_sample.notna()]
    m2 = dict(zip(sec.secondary_sample, sec.sample_key))
    acc = attrs.acc_resolved.where(attrs.acc_resolved.notna(), attrs.sample_acc).astype(str)
    sk = acc.map(m1)
    sk = sk.where(sk.notna(), attrs.sample_acc.astype(str).map(m1))
    sk = sk.where(sk.notna(), acc.map(m2))
    a = attrs.assign(_acc=acc, sample_key=sk)
    out = [a[a.sample_key.notna()]]
    if gut_runs is not None and len(gut_runs):
        runs = gut_runs[gut_runs.sample_key.isin(set(wide.loc[wide.sample_unit == "run", "sample_key"]))]
        bs = runs[["sample_accession", "sample_key"]].dropna().drop_duplicates()
        miss = a[a.sample_key.isna()].drop(columns=["sample_key"])
        j = miss.merge(bs, left_on="_acc", right_on="sample_accession", how="inner").drop(columns=["sample_accession"])
        if len(j):
            out.append(j)
    res = pd.concat(out, ignore_index=True)
    wstudy = dict(zip(wide.sample_key, wide.study_accession))
    res["study_accession"] = res.sample_key.map(wstudy).fillna(res.study_accession)
    return res.drop(columns=["_acc"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--attributes", required=True, help="harvested BioSample attribute rows (parquet)")
    ap.add_argument("--wide", required=True, help="package gut_sample_metadata_wide.parquet (sample_key join)")
    ap.add_argument("--gut-runs", help="gut_runs.parquet (first_public year per sample; run-unit sample keys)")
    ap.add_argument("--registry-runs"), ap.add_argument("--registry-sandpiper"), ap.add_argument("--gut-studies", help="build gut_runs when --gut-runs is absent")
    ap.add_argument("--out-dir", required=True), ap.add_argument("--release-id", default="R2026.12"), ap.add_argument("--package-version", default="1.12.0")
    ap.add_argument("--no-llm", action="store_true", help="skip the utility-model steps (location map / lifestyle confirmation) — deterministic rows only")
    ap.add_argument("--location-map", help="reuse an existing gut_location_map.parquet instead of calling the model")
    ap.add_argument("--lifestyle-map", help="reuse an existing gut_lifestyle_map.parquet instead of calling the model")
    ap.add_argument("--reverse-geocode", action="store_true", help="fill locality/region from lat/lon (GeoNames cities1000 via reverse_geocoder) at confidence 0.6")
    ap.add_argument("--max-tokens", type=int, default=1_500_000)
    a = ap.parse_args(argv)
    return run(a)


# ----------------------------------------------------------------------------------------------------------------------- run
def _utility_model():
    from catalog.models import resolve_model, ModelResolutionError
    try:
        return resolve_model("utility")
    except ModelResolutionError:
        return resolve_model("screen")  # models.yaml has no `utility` role yet: `screen` is the Haiku-class utility role


def _llm():
    import builtins
    h = getattr(builtins, "host", None) or getattr(sys.modules.get("__main__"), "host", None)
    if h is None:
        raise RuntimeError("host.llm not available: run inside a Claude Science kernel or pass --no-llm / --location-map / --lifestyle-map")
    return h.llm


DATE_KEYS = ["collection_date", "collection date", "sampling_date", "date_of_collection", "sample_collection_date", "collection_time", "sampling_time",
             "date_collected", "date_of_sampling", "sample_date", "collection_year", "year_of_collection", "sampling_year"]
COORD_PAIR_KEYS = [("latitude", "longitude"), ("geographic_location_latitude", "geographic_location_longitude"), ("lat", "lon"), ("lat", "long"),
                   ("geographic_location_latitude_", "geographic_location_longitude_")]
COORD_COMBINED_KEYS = ["lat_lon", "latitude_and_longitude", "lat_long", "latitude_longitude", "geographic_location_latitude_and_longitude", "coordinates", "gps_coordinates"]
KEY_PRIORITY = {k: i for i, k in enumerate(LOCATION_KEYS)}


def _first_public_year(gut_runs: pd.DataFrame | None) -> dict:
    if gut_runs is None or "first_public" not in gut_runs.columns:
        return {}
    y = pd.to_numeric(gut_runs.first_public.astype(str).str[:4], errors="coerce")
    return gut_runs.assign(_y=y).dropna(subset=["sample_key", "_y"]).groupby("sample_key")._y.max().astype(int).to_dict()


def _resolve_conflicts(det: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One row per (sample, field): most specific (longest normalised value for dates / text), then confidence, then key priority."""
    if det.empty:
        return det, det
    d = det.copy()
    d["_len"] = d.value_normalized.astype(str).str.len()
    d["_prio"] = d.evidence_source.str.replace("biosample.attribute:", "", regex=False).map(KEY_PRIORITY).fillna(99)
    d = d.sort_values(["sample_key", "field_name", "confidence", "_len", "_prio"], ascending=[True, True, False, False, True], kind="mergesort")
    keep = d.drop_duplicates(["sample_key", "field_name"], keep="first")
    lost = d.loc[d.index.difference(keep.index)]
    win = keep.set_index(["sample_key", "field_name"]).value_normalized
    idx = pd.MultiIndex.from_arrays([lost.sample_key, lost.field_name])
    lost = lost.assign(winner_value=win.reindex(idx).values)
    conflicts = lost[lost.value_normalized.astype(str) != lost.winner_value.astype(str)].drop(columns=["_len", "_prio"])
    return keep.drop(columns=["_len", "_prio"]).reset_index(drop=True), conflicts.reset_index(drop=True)


def run(a) -> int:
    os.makedirs(a.out_dir, exist_ok=True)
    rid, pv = a.release_id, a.package_version
    wide = pd.read_parquet(a.wide, columns=["sample_key", "study_accession", "biosample_accession", "secondary_sample", "sample_unit", "country"])
    attrs = pd.read_parquet(a.attributes)
    gut_runs = None
    if a.gut_runs and os.path.exists(a.gut_runs):
        gut_runs = pd.read_parquet(a.gut_runs)
    elif a.registry_runs and a.gut_studies:
        gs = set(pd.read_parquet(a.gut_studies, columns=["study_accession"]).study_accession)
        sp = pd.read_parquet(a.registry_sandpiper) if a.registry_sandpiper and os.path.exists(a.registry_sandpiper) else None
        gut_runs = build_gut_runs(pd.read_parquet(a.registry_runs), sp, gs, wide)
        gut_runs.to_parquet(os.path.join(a.out_dir, "gut_runs.parquet"), index=False)
    fp_year = _first_public_year(gut_runs)
    A = _sample_key_map(attrs, wide, gut_runs)
    A = A[A.attr_value.notna()]
    A["attr_value"] = A.attr_value.astype(str).str.strip()
    country = dict(zip(wide.sample_key, wide.country))
    tokens = {"input": 0, "output": 0, "calls": 0}
    rows, rejects, summary = [], [], {"keys_used": {}}

    # ---- collection_date
    dk = [k for k in DATE_KEYS if k in set(A.attr_key_norm)]
    summary["keys_used"]["collection_date"] = dk
    D = A[A.attr_key_norm.isin(dk)]
    hints = {}
    for (st, k), grp in D.groupby(["study_accession", "attr_key_norm"]):
        h = infer_dm_order(grp.attr_value.unique())
        if h:
            hints[(st, k)] = h
    fmt_counts, rej_counts = {}, {}
    cache = {}
    for r in D.itertuples(index=False):
        h = hints.get((r.study_accession, r.attr_key_norm))
        my = fp_year.get(r.sample_key)
        ck = (r.attr_value, h, my)
        res = cache.get(ck)
        if res is None:
            res = cache[ck] = parse_collection_date(r.attr_value, max_year=my, order_hint=h)
        fmt_counts[res["fmt"]] = fmt_counts.get(res["fmt"], 0) + 1
        if "reject" in res:
            rej_counts[res["reject"]] = rej_counts.get(res["reject"], 0) + 1
            if res["reject"] != "placeholder":
                rejects.append(dict(sample_key=r.sample_key, study_accession=r.study_accession, field_name="collection_date", attr_key_norm=r.attr_key_norm, attr_value=r.attr_value, reason=res["reject"], first_public_year=my))
            continue
        rows.append(det_row(r.sample_key, "collection_date", r.study_accession, r.attr_value, res["value"], res["confidence"], r.attr_key_norm, r.sample_acc, r.attr_value, res.get("note", ""), rid, pv))
    summary["collection_date_formats"] = dict(sorted(fmt_counts.items(), key=lambda x: -x[1]))
    summary["collection_date_rejects"] = rej_counts
    summary["dm_order_hints"] = {f"{k[0]}|{k[1]}": v for k, v in hints.items()}

    # ---- latitude / longitude
    try:
        import reverse_geocoder as rg
        rg_ok = True
    except Exception:  # noqa: BLE001
        rg, rg_ok = None, False
    ck_keys = [k for k in COORD_COMBINED_KEYS if k in set(A.attr_key_norm)]
    pair_keys = [(la, lo) for la, lo in COORD_PAIR_KEYS if la in set(A.attr_key_norm) and lo in set(A.attr_key_norm)]
    summary["keys_used"]["latitude_longitude"] = ck_keys + [f"{la}+{lo}" for la, lo in pair_keys]
    coords = []  # (sample_key, study, lat, lon, key, locator, quote, fmt)
    cfmt = {}
    for r in A[A.attr_key_norm.isin(ck_keys)].itertuples(index=False):
        res = parse_lat_lon(r.attr_value)
        cfmt[res.get("fmt") or res["reject"]] = cfmt.get(res.get("fmt") or res["reject"], 0) + 1
        if "reject" in res:
            if res["reject"] != "placeholder":
                rejects.append(dict(sample_key=r.sample_key, study_accession=r.study_accession, field_name="latitude/longitude", attr_key_norm=r.attr_key_norm, attr_value=r.attr_value, reason=res["reject"], first_public_year=None))
            continue
        coords.append((r.sample_key, r.study_accession, res["lat"], res["lon"], r.attr_key_norm, r.sample_acc, r.attr_value, res["fmt"]))
    for la, lo in pair_keys:
        P = A[A.attr_key_norm.isin([la, lo])].pivot_table(index=["sample_key", "study_accession", "sample_acc"], columns="attr_key_norm", values="attr_value", aggfunc="first").reset_index()
        if la not in P.columns or lo not in P.columns:
            continue
        for r in P.itertuples(index=False):
            vla, vlo = getattr(r, la, None), getattr(r, lo, None)
            if vla is None or vlo is None or (isinstance(vla, float) and pd.isna(vla)) or (isinstance(vlo, float) and pd.isna(vlo)):
                continue
            if is_placeholder(vla) or is_placeholder(vlo):
                cfmt["placeholder"] = cfmt.get("placeholder", 0) + 1
                continue
            lat, lon = parse_coord(vla, "lat"), parse_coord(vlo, "lon")
            if lat is None or lon is None:
                cfmt["pair unparseable/out of range"] = cfmt.get("pair unparseable/out of range", 0) + 1
                rejects.append(dict(sample_key=r.sample_key, study_accession=r.study_accession, field_name="latitude/longitude", attr_key_norm=f"{la}+{lo}", attr_value=f"{vla} | {vlo}", reason="unparseable or out of range", first_public_year=None))
                continue
            if lat == 0 and lon == 0:
                cfmt["(0,0)"] = cfmt.get("(0,0)", 0) + 1
                rejects.append(dict(sample_key=r.sample_key, study_accession=r.study_accession, field_name="latitude/longitude", attr_key_norm=f"{la}+{lo}", attr_value=f"{vla} | {vlo}", reason="(0,0)", first_public_year=None))
                continue
            cfmt["pair DD.dd + DD.dd"] = cfmt.get("pair DD.dd + DD.dd", 0) + 1
            coords.append((r.sample_key, r.study_accession, lat, lon, f"{la}+{lo}", r.sample_acc, f"{vla} {vlo}", "pair"))
    summary["coordinate_formats"] = cfmt
    # country consistency (nearest GeoNames cities1000 place, reverse_geocoder, offline): a pair whose SWAP lands in the sample's
    # country while the pair itself does not is rejected as swapped; other mismatches are kept with a note (coastal / border points)
    cc_check = {"checked": 0, "swapped_rejected": 0, "mismatch_noted": 0, "available": rg_ok}
    if rg_ok and coords:
        uniq = sorted({(c[2], c[3]) for c in coords})
        res = rg.search(uniq, mode=1)
        near = {u: r for u, r in zip(uniq, res)}
        need_swap = sorted({(c[3], c[2]) for c in coords if -90 <= c[3] <= 90})
        near_sw = {u: r for u, r in zip(need_swap, rg.search(need_swap, mode=1))} if need_swap else {}
    else:
        near, near_sw = {}, {}
    for sk, st, lat, lon, key, loc, quote, fmt in coords:
        note = ""
        cty = country.get(sk)
        if near and isinstance(cty, str) and len(cty) == 2:
            cc_check["checked"] += 1
            cc = near[(lat, lon)]["cc"]
            if cc != cty:
                sw = near_sw.get((lon, lat))
                if sw is not None and sw["cc"] == cty:
                    cc_check["swapped_rejected"] += 1
                    rejects.append(dict(sample_key=sk, study_accession=st, field_name="latitude/longitude", attr_key_norm=key, attr_value=quote, reason=f"swapped pair: nearest place cc={cc}, swapped cc={cty} = sample country", first_public_year=None))
                    continue
                cc_check["mismatch_noted"] += 1
                note = f"nearest GeoNames place in {cc}, sample country {cty}"
        conf = 0.7 if note else 0.9
        rows.append(det_row(sk, "latitude", st, quote, f"{lat:.4f}", conf, key, loc, quote, note, rid, pv))
        rows.append(det_row(sk, "longitude", st, quote, f"{lon:.4f}", conf, key, loc, quote, note, rid, pv))
    summary["coordinate_country_check"] = cc_check

    # ---- location strings → utility model map
    lk = [k for k in LOCATION_KEYS if k in set(A.attr_key_norm)]
    summary["keys_used"]["location"] = lk
    L = A[A.attr_key_norm.isin(lk) & ~A.attr_value.map(is_placeholder)].copy()
    parts = [split_country_prefix(v, k) for v, k in zip(L.attr_value, L.attr_key_norm)]
    L["country_prefix"] = [p[0] for p in parts]
    L["remainder"] = [p[1] for p in parts]
    L = L[(L.remainder != "") & ~L.remainder.map(is_placeholder) & ~L.remainder.str.fullmatch(r"[\d\W_]+")]
    L["country_hint"] = L.sample_key.map(country)
    distinct = L.groupby(["attr_key_norm", "attr_value"]).agg(remainder=("remainder", "first"), country_prefix=("country_prefix", "first"), n_samples=("sample_key", "nunique"),
                                                               country_hint=("country_hint", lambda s: s.dropna().mode().iloc[0] if s.notna().any() else "")).reset_index().rename(columns={"attr_value": "raw"})
    summary["n_location_strings"] = int(len(distinct))
    if a.location_map and os.path.exists(a.location_map):
        lmap = pd.read_parquet(a.location_map)
    elif a.no_llm:
        lmap = distinct.assign(region=None, locality=None, site=None, is_placeholder=None, note="not normalised (--no-llm)", valid=False, model=None, exonyms="{}")
    else:
        lmap = normalise_locations(distinct, _llm(), _utility_model(), batch=40, token_log=tokens)
        if tokens["input"] + tokens["output"] > a.max_tokens:
            raise RuntimeError(f"token ceiling {a.max_tokens} exceeded: {tokens}")
    lmap = lmap.reindex(columns=["raw", "attr_key_norm", "remainder", "country_prefix", "country_hint", "region", "locality", "site", "is_placeholder", "valid", "rejected_fields", "n_samples", "model", "note", "exonyms"])
    lmap.to_parquet(os.path.join(a.out_dir, "gut_location_map.parquet"), index=False)
    good = lmap[(lmap.valid == True) & (lmap.is_placeholder != True)]  # noqa: E712
    summary["n_location_mapped"] = int(good[["region", "locality", "site"]].notna().any(axis=1).sum())
    ex_map = {}
    for e in good.exonyms.dropna():
        try:
            ex_map.update(json.loads(e))
        except json.JSONDecodeError:
            pass
    json.dump(ex_map, open(os.path.join(a.out_dir, "gut_location_exonym_map.json"), "w"), ensure_ascii=False, indent=1)
    gk = good.set_index(["attr_key_norm", "raw"])
    LJ = L.merge(good[["attr_key_norm", "raw", "region", "locality", "site", "note"]], left_on=["attr_key_norm", "attr_value"], right_on=["attr_key_norm", "raw"], how="inner")
    for r in LJ.itertuples(index=False):
        for f, v in (("location_region", r.region), ("location_locality", r.locality), ("location_site", r.site)):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            inexact = _fold(v) not in _fold(r.attr_value)
            conf = 0.75 if inexact else 0.85
            note = "utility-model normalisation of the distinct location string" + ("; exonym / abbreviation expansion" if inexact else "")
            rows.append(det_row(r.sample_key, f, r.study_accession, r.attr_value, v, conf, r.attr_key_norm, r.sample_acc, r.attr_value, note, rid, pv))

    # ---- lifestyle
    cfg_dir = os.environ.get("CATALOG_CONFIG_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "..", "config"))
    codes = load_lifestyle_vocab(cfg_dir)
    lsk = [k for k in LIFESTYLE_KEYS if k in set(A.attr_key_norm)]
    summary["keys_used"]["lifestyle"] = lsk
    S = A[A.attr_key_norm.isin(lsk) & ~A.attr_value.map(is_placeholder)]
    sd = S.groupby(["attr_key_norm", "attr_value"]).sample_key.nunique().reset_index(name="n_samples")
    det_ = [lifestyle_code_for(k, v, codes) for k, v in zip(sd.attr_key_norm, sd.attr_value)]
    sd["code"], sd["detail"], sd["method"] = [d[0] for d in det_], [d[1] for d in det_], [d[2] for d in det_]
    if a.lifestyle_map and os.path.exists(a.lifestyle_map):
        smap = pd.read_parquet(a.lifestyle_map)
    else:
        pop = sd[sd.attr_key_norm.isin(POPULATION_KEYS | {"lifestyle", "subsistence", "subsistence_strategy"}) & ~sd.method.isin(["placeholder"])]
        rest = sd.drop(pop.index)
        if len(pop) and not a.no_llm:
            pop = confirm_lifestyle(pop, codes, _llm(), _utility_model(), batch=40, token_log=tokens)
        elif len(pop):
            pop = pop.assign(code=None, method=pop.method + "; not confirmed (--no-llm)")
        smap = pd.concat([rest, pop], ignore_index=True)
        smap["detail"] = smap.attr_value.where(smap.code.notna(), None)
    smap = smap.reindex(columns=["attr_key_norm", "attr_value", "code", "detail", "method", "n_samples", "model", "model_code", "model_note"])
    smap.to_parquet(os.path.join(a.out_dir, "gut_lifestyle_map.parquet"), index=False)
    sg = smap[smap.code.notna() & (smap.code != "unknown")]
    SJ = S.merge(sg[["attr_key_norm", "attr_value", "code", "detail", "method"]], on=["attr_key_norm", "attr_value"], how="inner")
    for r in SJ.itertuples(index=False):
        rows.append(det_row(r.sample_key, "lifestyle", r.study_accession, r.attr_value, r.code, 0.85, r.attr_key_norm, r.sample_acc, r.attr_value, f"code from config/vocab/lifestyle.yaml ({r.method})", rid, pv))
        rows.append(det_row(r.sample_key, "lifestyle_detail", r.study_accession, r.attr_value, str(r.detail)[:120], 0.85, r.attr_key_norm, r.sample_acc, r.attr_value, "", rid, pv))

    det = pd.DataFrame(rows, columns=DET_COLS)
    det, conflicts = _resolve_conflicts(det)

    # ---- optional reverse geocoding (GeoNames cities1000 via reverse_geocoder, offline): REGION (admin1) only, for samples with
    # coordinates of >= 2 decimals and no text-derived region. Nearest-place lookup is too coarse for a locality in dense metro areas
    # (Edgewater NJ for a Manhattan hospital) and country centroids fall on villages (UK centroid -> Moffat), so: locality is never
    # filled from coordinates; a point is skipped when its nearest place is > 10 km away, or > 3 km away while the same exact point
    # is used by >= 3 studies (a centroid shared by submitters); the nearest place's cc must equal the sample's country when known.
    n_rg, rg_stats = 0, {}
    if a.reverse_geocode and rg_ok and not det.empty:
        import math

        def _hav(a1, b1, a2, b2):
            a1, b1, a2, b2 = map(math.radians, (a1, b1, a2, b2))
            return 6371 * 2 * math.asin(math.sqrt(math.sin((a2 - a1) / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin((b2 - b1) / 2) ** 2))
        have_reg = set(det.loc[det.field_name == "location_region", "sample_key"])
        ll = det[det.field_name.isin(["latitude", "longitude"])].pivot_table(index="sample_key", columns="field_name", values="value_normalized", aggfunc="first").dropna()
        prec = ll.latitude.str.split(".").str[-1].str.rstrip("0").str.len().ge(2) & ll.longitude.str.split(".").str[-1].str.rstrip("0").str.len().ge(2)
        ll = ll[prec & ~ll.index.isin(have_reg)]
        rg_stats["candidate_samples"] = int(len(ll))
        if len(ll):
            study_of = dict(zip(det.sample_key, det.study_accession))
            ll = ll.assign(_lat=ll.latitude.astype(float), _lon=ll.longitude.astype(float), _st=ll.index.map(study_of))
            pts = ll.groupby(["_lat", "_lon"])._st.nunique().reset_index(name="n_studies")
            found = rg.search(list(zip(pts._lat, pts._lon)), mode=1)
            ok_pts = {}
            skipped = {"far": 0, "centroid": 0}
            for (la, lo, nst), g in zip(pts.itertuples(index=False), found):
                km = _hav(la, lo, float(g["lat"]), float(g["lon"]))
                if km > 10:
                    skipped["far"] += 1
                elif nst >= 3 and km > 3:
                    skipped["centroid"] += 1
                else:
                    ok_pts[(la, lo)] = g
            rg_stats.update(points=int(len(pts)), points_skipped=skipped, points_used=len(ok_pts))
            src_row = det[det.field_name == "latitude"].drop_duplicates("sample_key").set_index("sample_key")
            extra = []
            for sk, r in ll.iterrows():
                g = ok_pts.get((r._lat, r._lon))
                if g is None or not g.get("admin1"):
                    continue
                cty = country.get(sk)
                if isinstance(cty, str) and len(cty) == 2 and g["cc"] != cty:
                    continue
                s0 = src_row.loc[sk]
                key = s0.evidence_source.replace("biosample.attribute:", "")
                extra.append(det_row(sk, "location_region", s0.study_accession, s0.field_value, g["admin1"], 0.6, key, s0.evidence_locator, s0.evidence_quote,
                                     "reverse-geocoded from lat_lon (GeoNames cities1000)", rid, pv))
            n_rg = len(extra)
            det = pd.concat([det, pd.DataFrame(extra, columns=DET_COLS)], ignore_index=True)
    summary["reverse_geocode"] = rg_stats
    summary["n_reverse_geocoded_rows"] = n_rg

    det = det.reindex(columns=DET_COLS)
    det.to_parquet(os.path.join(a.out_dir, "gut_r1_newfields_determinations.parquet"), index=False)
    conflicts.to_parquet(os.path.join(a.out_dir, "gut_r1_newfields_conflicts.parquet"), index=False)
    pd.DataFrame(rejects, columns=["sample_key", "study_accession", "field_name", "attr_key_norm", "attr_value", "reason", "first_public_year"]).to_parquet(os.path.join(a.out_dir, "gut_r1_newfields_rejects.parquet"), index=False)
    summary.update(n_rows=int(len(det)), n_conflicts=int(len(conflicts)), n_rejects=len(rejects), llm_tokens=tokens,
                   rows_per_field=det.field_name.value_counts().to_dict(),
                   samples_per_field={f: int(g.sample_key.nunique()) for f, g in det.groupby("field_name")},
                   studies_per_field={f: int(g.study_accession.nunique()) for f, g in det.groupby("field_name")},
                   lifestyle_counts=det[det.field_name == "lifestyle"].value_normalized.value_counts().to_dict())
    json.dump(summary, open(os.path.join(a.out_dir, "gut_r1_newfields_summary.json"), "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("dm_order_hints",)}, default=str)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
