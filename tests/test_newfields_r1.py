"""Route R1 for the 1.12.0 pack fields: deterministic date / coordinate parsers, place validator, lifestyle rules, gut_runs and the
wide-table derivations of catalog.scopes.build_gut_scope (pack-driven field lists, detailed_location, collection_year, sequencing summary)."""
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from catalog.scopes import build_gut_scope as bgs  # noqa: E402
from catalog.scopes import newfields_r1 as nf  # noqa: E402

CFG = os.path.join(os.path.dirname(__file__), "..", "config")

# ------------------------------------------------------------------------------------------------------------------ collection_date
DATE_OK = [
    ("2017", "2017", 0.9), ("2019-12-29", "2019-12-29", 0.9), ("2023-04", "2023-04", 0.9), ("2020/2022", "2020/2022", 0.9),
    ("2023-10-01/2025-04-30", "2023-10-01/2025-04-30", 0.9), ("2012-08/2015-12", "2012-08/2015-12", 0.9), ("2017-12-18T00:00:00Z", "2017-12-18", 0.9),
    ("2017-12-18T09:30", "2017-12-18", 0.9), ("2017-12-18 09:30:00", "2017-12-18", 0.9), ("2017-12-18T09:30:00+02:00", "2017-12-18", 0.9),
    ("8/13/10", "2010-08-13", 0.8), ("12/20/10", "2010-12-20", 0.8), ("12/20/2010", "2010-12-20", 0.9), ("20/12/2010", "2010-12-20", 0.9),
    ("March 2012", "2012-03", 0.9), ("November, 2009", "2009-11", 0.9), ("Mar-2019", "2019-03", 0.9), ("Sept 2015", "2015-09", 0.9),
    ("12-Mar-2019", "2019-03-12", 0.9), ("12 March 2019", "2019-03-12", 0.9), ("March 12, 2019", "2019-03-12", 0.9), ("Mar 12 2019", "2019-03-12", 0.9),
    ("2019.05.06", "2019-05-06", 0.9), ("2019/05/06", "2019-05-06", 0.9), ("2019-5-6", "2019-05-06", 0.9), ("2019/05", "2019-05", 0.9),
    (" 2018 ", "2018", 0.9), ("1990", "1990", 0.9), ("2019-2021", "2019/2021", 0.9), ("2019 to 2021", "2019/2021", 0.9),
    ("2019-01/2019-03", "2019-01/2019-03", 0.9), ("2016-02-29", "2016-02-29", 0.9), ("31/01/2020", "2020-01-31", 0.9), ("01/31/2020", "2020-01-31", 0.9),
    ("5.9.2016", "2016", 0.9), ("3/3/11", "2011", 0.8), ("08-05-2012", "2012", 0.9), ("2021-06-15T00:00:00", "2021-06-15", 0.9),
    ("Dec-2020", "2020-12", 0.9), ("jan 2021", "2021-01", 0.9), ("2022-10-05/2022-10-05", "2022-10-05/2022-10-05", 0.9), ("2004", "2004", 0.9),
]
DATE_REJECT = [
    "missing", "not collected", "not applicable", "restricted access", "NA", "N/A", "unknown", "0", "1900-01-01", "missing: data agreement established pre-2023",
    "missing: control sample", "not provided", "-", "", None, "1975", "1974/1975", "1905-07-13", "2225", "2019-02-30", "2021-13", "2019-13-01", "2020-04-01/2020-01-22",
    "2022-11-18/2021-06-01", "stock", "22", "abc", "13/13/2019", "2019-00-01", "2019-01-00", "1975-07-20/1975-10-20", "0000", "none", "Not Applicable",
    "not determined", "unk", "n/a", "1979", "1900", "NaN", "2027",
]


@pytest.mark.parametrize("raw,expected,conf", DATE_OK)
def test_parse_collection_date_accepts(raw, expected, conf):
    r = nf.parse_collection_date(raw, max_year=2026)
    assert "reject" not in r, r
    assert r["value"] == expected
    assert r["confidence"] == conf


@pytest.mark.parametrize("raw", DATE_REJECT)
def test_parse_collection_date_rejects(raw):
    r = nf.parse_collection_date(raw, max_year=2026)
    assert "reject" in r, r


def test_collection_date_first_public_bound():
    assert nf.parse_collection_date("2019", max_year=2018)["reject"] == "year after first_public"
    assert nf.parse_collection_date("2018", max_year=2018)["value"] == "2018"


def test_dm_order_inference_and_hint():
    assert nf.infer_dm_order(["3/3/11", "8/13/10"]) == "MDY"
    assert nf.infer_dm_order(["3/3/11", "13/8/10"]) == "DMY"
    assert nf.infer_dm_order(["3/3/11", "13/8/10", "8/13/10"]) is None
    assert nf.infer_dm_order(["2019-01-01"]) is None
    r = nf.parse_collection_date("3/4/2011", order_hint="DMY")
    assert r["value"] == "2011-04-03" and r["confidence"] == 0.8 and "inferred" in r["note"]
    r = nf.parse_collection_date("3/4/2011", order_hint="MDY")
    assert r["value"] == "2011-03-04" and r["confidence"] == 0.8
    r = nf.parse_collection_date("3/4/2011")
    assert r["value"] == "2011" and "ambiguous" in r["note"]


# ------------------------------------------------------------------------------------------------------------------ coordinates
LATLON_OK = [
    ("38.9 N 77.0 W", 38.9, -77.0), ("31.23 N 121.47 E", 31.23, 121.47), ("23.7N 113.15E", 23.7, 113.15), ("38.9,-77.0", 38.9, -77.0),
    ("38.9, -77.0", 38.9, -77.0), ("38.9 -77.0", 38.9, -77.0), ("-34.6, -58.4", -34.6, -58.4), ("N38.9 W77.0", 38.9, -77.0),
    ("38°54'N 77°02'W", 38.9, -77.0333), ("38°54'12\"N 77°02'05\"W", 38.9033, -77.0347), ("13.518840 S 71.975040 W", -13.5188, -71.975),
    ("1.3521 N 103.8198 E", 1.3521, 103.8198), ("108.93 E 34.27 N", 34.27, 108.93), ("55.3781 N 3.4360 W", 55.3781, -3.436), ("41 N 88 W", 41.0, -88.0),
    ("1.25 S 36.89 E", -1.25, 36.89), ("52.14 N 21 E", 52.14, 21.0), ("38.9 N, 77.0 W", 38.9, -77.0), ("38.9N, 77.0W", 38.9, -77.0), ("44.02275279 N 92.46684038 W", 44.0228, -92.4668),
    ("50.85 N 5.69 E", 50.85, 5.69), ("19.0366 S 68.0889 W", -19.0366, -68.0889), ("0.5 N 100 E", 0.5, 100.0), ("90 N 180 E", 90.0, 180.0), ("-90, -180", -90.0, -180.0),
    ("22.3 N 114.2 E", 22.3, 114.2), ("48.7 N 1.4 W", 48.7, -1.4), ("S 33.87 E 151.21", -33.87, 151.21), ("33.87 S 151.21 E", -33.87, 151.21), ("37.7749 N 122.4194 W", 37.7749, -122.4194),
]
LATLON_REJECT = ["missing", "not collected", "not applicable", "Missing", "restricted access", "0 N 0 E", "0, 0", "0 0", "116.46 N,39.92 E", "95 N 10 E", "38.9 N 190 W",
                 "abc", "38.9", "38.9 N", "77.0 W", "", None, "N/A", "unknown", "38.9 X 77.0 Y", "1900-01-01", "not provided", "none", "-", "91.0, 10.0", "10.0, 181.0"]


@pytest.mark.parametrize("raw,lat,lon", LATLON_OK)
def test_parse_lat_lon_accepts(raw, lat, lon):
    r = nf.parse_lat_lon(raw)
    assert "reject" not in r, r
    assert r["lat"] == pytest.approx(lat, abs=1e-4) and r["lon"] == pytest.approx(lon, abs=1e-4)


@pytest.mark.parametrize("raw", LATLON_REJECT)
def test_parse_lat_lon_rejects(raw):
    assert "reject" in nf.parse_lat_lon(raw)


COORD_SINGLE = [
    ("50.651137", "lat", 50.6511), ("-117.25", "lon", -117.25), ("93.2 W", "lon", -93.2), ("32.5", "lat", 32.5), ("N 38.9", "lat", 38.9), ("38 54 N", "lat", 38.9),
    ("22.3193� N", "lat", 22.3193), ("133.7751� E", "lon", 133.7751), ("25.2744� S", "lat", -25.2744), ("11,57", "lon", 11.57), ("-3,70", "lon", -3.7), ("39N", "lat", 39.0),
    ("116. 3 E", "lon", 116.3), ("45°25′N", "lat", 45.4167), ("123", "lat", None), ("181", "lon", None), ("not provided", "lat", None), ("44.9 N", "lon", None),
    ("113E", "lat", None), ("abc", "lat", None), ("-90", "lat", -90.0), ("180", "lon", 180.0), ("0", "lat", None), ("_121.744339", "lon", -121.7443),
]


@pytest.mark.parametrize("raw,kind,expected", COORD_SINGLE)
def test_parse_coord_single(raw, kind, expected):
    v = nf.parse_coord(raw, kind)
    if expected is None:
        assert v is None
    else:
        assert v == pytest.approx(expected, abs=1e-4)


# ------------------------------------------------------------------------------------------------------------------ places / lifestyle
def test_split_country_prefix():
    assert nf.split_country_prefix("USA: CA: San Diego", "geo_loc_name") == ("USA", "CA: San Diego")
    assert nf.split_country_prefix("China:Shanghai", "geo_loc_name") == ("China", "Shanghai")
    assert nf.split_country_prefix("Malmö", "geographic_location_region_and_locality") == (None, "Malmö")
    assert nf.split_country_prefix("Buenos Aires", "collection_site") == (None, "Buenos Aires")
    assert nf.split_country_prefix("USA", "geo_loc_name") == (None, "USA")


def test_validate_place_rules():
    ok, why = nf.validate_place("CA: San Diego", "San Diego", {})
    assert ok and why == "substring"
    ok, why = nf.validate_place("CA: San Diego", "California", {})
    assert ok and why == "abbreviation"
    ok, why = nf.validate_place("København", "Copenhagen", {"København": "Copenhagen"})
    assert ok and why == "exonym"
    ok, why = nf.validate_place("Adelaide", "South Australia", {})
    assert not ok
    ok, why = nf.validate_place("Wuhan", "Hubei", {})
    assert not ok
    ok, why = nf.validate_place("Malmö", "Malmo", {})
    assert ok  # accent-folded substring
    assert nf.validate_place("Anything", None, {}) == (True, "")


def test_lifestyle_rules():
    codes = nf.load_lifestyle_vocab(CFG)
    assert nf.lifestyle_code_for("host_diet", "Vegan", codes)[0] == "vegetarian_or_vegan"
    assert nf.lifestyle_code_for("host_diet", "Omnivore", codes)[0] is None
    assert nf.lifestyle_code_for("host_diet", "vegetarian but eat seafood", codes)[0] is None
    assert nf.lifestyle_code_for("urban", "non-urban", codes)[0] == "rural_non_industrialized"
    assert nf.lifestyle_code_for("urban", "urban", codes)[0] == "urban_industrialized"
    assert nf.lifestyle_code_for("urban", "not applicable", codes) == (None, None, "placeholder")
    assert nf.lifestyle_code_for("tribe", "Warli", codes)[0] == "indigenous_community"
    assert nf.lifestyle_code_for("population", "Hadza", codes)[0] == "hunter_gatherer"
    assert nf.lifestyle_code_for("population", "Hutterite colony", codes)[0] == "isolated_religious_community"
    assert nf.lifestyle_code_for("population", "human", codes)[0] is None
    assert "ethnicity" not in nf.LIFESTYLE_KEYS and "race" not in nf.LIFESTYLE_KEYS


def test_is_placeholder():
    for v in ["missing", "Not Collected", "not applicable", "NA", "n/a", "restricted access", "0", "1900-01-01", "missing: third party data", "", None, "-", "unknown"]:
        assert nf.is_placeholder(v), v
    for v in ["2019", "Boston", "38.9 N 77.0 W", "urban", "0.5"]:
        assert not nf.is_placeholder(v), v


def test_det_row_schema_and_quote():
    r = nf.det_row("SAMN1", "collection_date", "PRJNA1", "2019-01-02", "2019-01-02", 0.9, "collection_date", "SAMN1", " ".join(["w"] * 20))
    assert list(r) == nf.DET_COLS
    assert r["route"] == "R1" and r["scope"] == "sample" and r["evidence_source"] == "biosample.attribute:collection_date"
    assert len(r["evidence_quote"].split()) <= 12 and r["determined_by"] == nf.DETERMINED_BY


# ------------------------------------------------------------------------------------------------------------------ gut_runs + wide derivations
def _wide():
    return pd.DataFrame(dict(sample_key=["SAMN1", "SAMN2", "SRR9"], study_accession=["P1", "P1", "P2"], biosample_accession=["SAMN1", "SAMN2", "SAMN3"],
                             secondary_sample=[None, "SRS2", None], sample_unit=["biosample", "biosample", "run"]))


def test_build_gut_runs_keys_and_sandpiper():
    rr = pd.DataFrame(dict(run_accession=["SRR1", "SRR2", "SRR9", "SRR5"], study_accession=["P1", "P1", "P2", "P3"], sample_accession=["SAMN1", "SAMNX", "SAMN3", "SAMN9"],
                           secondary_sample_accession=["SRS1", "SRS2", "SRS3", "SRS9"], experiment_accession=["SRX1", "SRX2", "SRX9", "SRX5"], library_name=list("abcd"),
                           library_strategy=["WGS"] * 4, library_source=["METAGENOMIC"] * 4, library_layout=["PAIRED", "PAIRED", "SINGLE", "PAIRED"], instrument_platform=["ILLUMINA"] * 4,
                           instrument_model=["NovaSeq 6000", "NovaSeq 6000", "MiSeq", "HiSeq"], read_count=["10", "20", "30", "40"], base_count=["1000000000", "3000000000", "500000000", "1"],
                           first_public=["2020-01-01", "2020-01-01", "2021-05-05", "2019-01-01"]))
    sp = pd.DataFrame(dict(run_accession=["SRR1", "SRR9"], study_accession=["P1", "P2"], sandpiper_profiled=[True, True]))
    g = nf.build_gut_runs(rr, sp, {"P1", "P2"}, _wide())
    assert list(g.run_accession) == ["SRR1", "SRR2", "SRR9"]           # P3 is not a catalog study
    assert list(g.sample_key) == ["SAMN1", "SAMN2", "SRR9"]           # biosample, secondary-sample and run-unit keys
    assert list(g.sandpiper_profiled) == [True, False, True]
    s = bgs.sequencing_summary(g)
    assert s.loc["P1", "n_runs_total"] == 2 and s.loc["P1", "gbp_per_run_mean"] == pytest.approx(2.0) and s.loc["P1", "gbp_per_run_median"] == pytest.approx(2.0)
    assert json.loads(s.loc["P1", "instrument_models_top"]) == {"NovaSeq 6000": 2} and json.loads(s.loc["P2", "library_layouts"]) == {"SINGLE": 1}
    assert s.loc["P1", "sandpiper_profiled_share"] == 0.5
    assert nf._first_public_year(g) == {"SAMN1": 2020, "SAMN2": 2020, "SRR9": 2021}


def test_pack_fields_from_config():
    pack = bgs._load_pack(CFG)
    pf = bgs.pack_fields(pack)
    for f in ["collection_date", "location_region", "location_locality", "location_site", "latitude", "longitude", "lifestyle", "lifestyle_detail", "age_at_collection_days", "sex"]:
        assert f in pf["fields"]
    assert "detailed_location" in pf["compose"] and pf["compose"]["detailed_location"]["compose"] == ["location_site", "location_locality", "location_region"]
    assert pf["year_of"] == {"collection_year": "collection_date"}
    assert pf["core"] == ["body_site_code", "age_category", "country", "health_condition", "subject_id"]   # R2026.15: core age = life stage; 2026-10-31: body site core
    assert "detailed_location" in pf["key"] and "lifestyle" in pf["key"] and "collection_date" in pf["key"]
    assert set(pf["infant_only"]) >= {"delivery_mode", "feeding_mode", "nec_status"}
    # 1.13.0: the pack also declares diet / smoking_status (vocab), medication (vocab_list) and stool_consistency_bristol (int 1-7)
    assert {k: pf["vocab"][k] for k in ("health_condition", "lifestyle")} == {"health_condition": "config/vocab/health_conditions.yaml", "lifestyle": "config/vocab/lifestyle.yaml"}
    assert pf["vocab"]["diet"] == "config/vocab/diet.yaml" and pf["vocab"]["smoking_status"] == "config/vocab/smoking.yaml"
    assert pf["vocab_list"] == {"medication": "config/vocab/medication.yaml", "intervention": "config/vocab/interventions.yaml"} and pf["int_range"] == {"stool_consistency_bristol": (1, 7)}
    assert "hunter_gatherer" in bgs.load_vocab_codes(CFG, pf["vocab"]["lifestyle"])


@pytest.mark.parametrize("parts,expected", [
    (("Mt. Sinai", "New York", "New York"), "Mt. Sinai, New York, New York"), ((None, "San Diego", "California"), "San Diego, California"),
    ((None, None, "Selangor"), "Selangor"), ((None, None, None), None), ((pd.NA, "Malmö", pd.NA), "Malmö"), ((float("nan"), "", "Kedah"), "Kedah"),
    (("DIMAMO", None, None), "DIMAMO"), (("", "", ""), None),
])
def test_compose_detailed_location(parts, expected):
    assert bgs.compose_detailed_location(parts) == expected


@pytest.mark.parametrize("value,expected", [("2019-01-02", 2019.0), ("2019", 2019.0), ("2020/2022", 2020.0), ("2012-08/2015-12", 2012.0), (None, None), (float("nan"), None), ("abc", None), ("", None)])
def test_collection_year(value, expected):
    assert bgs.collection_year(value) == expected


def test_validate_vocab_fields_drops_unknown_codes():
    pack = bgs._load_pack(CFG)
    pf = bgs.pack_fields(pack)
    det = pd.DataFrame([dict(field_name="lifestyle", value_normalized="urban_industrialized", src_track="gut_all_v1"),
                        dict(field_name="lifestyle", value_normalized="city_dweller", src_track="gut_all_v1"),
                        dict(field_name="health_condition", value_normalized="nec", src_track="infant_catalog"),
                        dict(field_name="health_condition", value_normalized="made_up", src_track="gut_all_v1"),
                        dict(field_name="sex", value_normalized="anything", src_track="gut_all_v1")])
    out, dropped = bgs.validate_vocab_fields(det, pf, CFG)
    assert dropped["health_condition"] == 1 and dropped["lifestyle"] == 1 and sum(dropped.values()) == 2
    assert list(out.value_normalized) == ["urban_industrialized", "nec", "anything"]


def test_resolve_conflicts_prefers_confidence_then_specificity():
    rows = [nf.det_row("S1", "location_region", "P", "CA", "California", 0.75, "state", "S1", "CA"),
            nf.det_row("S1", "location_region", "P", "california", "California", 0.85, "site", "S1", "california"),
            nf.det_row("S1", "collection_date", "P", "2019", "2019", 0.9, "collection_date", "S1", "2019"),
            nf.det_row("S1", "collection_date", "P", "2019-03-02", "2019-03-02", 0.9, "sampling_date", "S1", "2019-03-02")]
    keep, conflicts = nf._resolve_conflicts(pd.DataFrame(rows, columns=nf.DET_COLS))
    assert len(keep) == 2 and set(keep.value_normalized) == {"California", "2019-03-02"}
    assert len(conflicts) == 1 and conflicts.iloc[0].value_normalized == "2019" and conflicts.iloc[0].winner_value == "2019-03-02"


def test_module_field_lists_match_pack():
    pack = bgs._load_pack(CFG)
    pf = bgs.pack_fields(pack)
    assert bgs.PACK_FIELDS == pf["fields"]
    assert bgs.INFANT_ONLY == pf["infant_only"]
