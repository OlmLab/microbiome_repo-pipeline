"""R2026.38: route-restricted corrections, full quotes for checked rows, exact IBD names, dictionary row counts, registry DOI."""
import base64
import hashlib
import json
import os

import pandas as pd

from catalog.release import bitemporal, package_docs
from catalog.scopes import build_gut_scope as g

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _det(rows):
    return pd.DataFrame([g._row(sk, f, st, raw, norm, conf, src, "loc", quote, route, by, scope="sample", release_id="R2026.1", pv="1.0.0")
                         for sk, f, st, raw, norm, conf, src, quote, route, by in rows], columns=g.DET_COLS)


def _corr(tmp_path, rows):
    p = tmp_path / "c.parquet"
    pd.DataFrame(rows).to_parquet(p)
    return str(p)


def test_old_route_limits_a_study_wide_retirement(tmp_path):
    det = _det([("S1", "antibiotic_exposure", "P1", "no", "no", 0.7, "paper.fulltext.methods", "excluded", "R3", "r3_prose_v1"),
                ("S2", "antibiotic_exposure", "P1", "no", "no", 0.7, "paper.fulltext.methods", "excluded", "R3", "r3_prose_v1"),
                ("S2", "antibiotic_exposure", "P1", "yes", "yes", 0.9, "biosample.attribute.antibiotics", "yes", "R1", "r1"),
                ("S3", "antibiotic_exposure", "P2", "no", "no", 0.7, "paper.fulltext.methods", "none", "R3", "r3_prose_v1")])
    c = _corr(tmp_path, [dict(sample_key="", study_accession="P1", field_name="antibiotic_exposure", action="retire", old_value="no", old_route="R3",
                              new_value="", evidence_source="", evidence_locator="", evidence_quote="", confidence=None, rationale="analysis-stage exclusion",
                              determined_by="dq_adjudication_v2:r38")])
    out, stats = g.apply_corrections(det, c, "R2026.38", "1.31.0")
    assert stats["n_dropped"] == 2
    left = out[out.study_accession == "P1"]
    assert left.route.tolist() == ["R1"] and left.value_normalized.tolist() == ["yes"]
    assert len(out[out.study_accession == "P2"]) == 1


def test_checked_rows_keep_the_whole_quote(tmp_path):
    det = _det([("S1", "lifestyle", "P1", "urban_industrialized", "urban_industrialized", 0.7, "paper.fulltext", "urban_industrialized", "R3", "fulltext_setting_v1")])
    quote = "All participants were recruited from the outpatient clinic of a tertiary hospital in central Shanghai and had lived in the city for at least five years"
    assert len(quote.split()) > 12
    base = dict(sample_key="S1", study_accession="P1", field_name="lifestyle", action="recode", old_value="urban_industrialized", old_route="",
                new_value="urban_industrialized", evidence_source="paper.fulltext.methods", evidence_locator="PMC1", evidence_quote=quote, confidence=0.8, rationale="quote found")
    out, _ = g.apply_corrections(det, _corr(tmp_path, [base | dict(determined_by="dq_adjudication_v2:r38")]), "R2026.38", "1.31.0")
    assert len(out) == 1 and out.evidence_quote.iloc[0] == quote and out.route.iloc[0] == "R3"
    out1, _ = g.apply_corrections(det, _corr(tmp_path, [base | dict(determined_by="dq_adjudication_v1")]), "R2026.38", "1.31.0")
    assert len(out1.evidence_quote.iloc[0].split()) <= 13          # the 12-word rule still holds for every other row


def test_exact_ibd_names_get_the_specific_code():
    rows = [("S1", "health_condition", "P1", "Crohn's disease", "ibd_or_gi_disease", 0.8, "biosample.attribute.host_disease", "x", "R1", "r1"),
            ("S2", "health_condition", "P1", "ulcerative colitis", "ibd_unspecified", 0.8, "biosample.attribute.host_disease", "x", "R1", "r1"),
            ("S3", "health_condition", "P1", "UC", "ibd_or_gi_disease", 0.8, "biosample.attribute.host_disease", "x", "R1", "r1"),
            ("S4", "health_condition", "P1", "CD/UC", "ibd_or_gi_disease", 0.8, "biosample.attribute.host_disease", "x", "R1", "r1"),
            ("S5", "health_condition", "P1", "family history of Crohn's disease", "ibd_or_gi_disease", 0.8, "biosample.attribute.host_disease", "x", "R1", "r1"),
            ("S6", "health_condition", "P1", "Crohn's disease", "healthy", 0.8, "biosample.attribute.host_disease", "x", "R1", "r1"),
            ("S7", "sex", "P1", "CD", "ibd_or_gi_disease", 0.8, "biosample.attribute.sex", "x", "R1", "r1")]
    out, n = g.refine_ibd_codes(_det(rows))
    v = out.set_index("sample_key").value_normalized
    assert n == 3
    assert v["S1"] == "crohns_disease" and v["S2"] == "ulcerative_colitis" and v["S3"] == "ulcerative_colitis"
    assert v["S4"] == "ibd_or_gi_disease" and v["S5"] == "ibd_or_gi_disease" and v["S6"] == "healthy" and v["S7"] == "ibd_or_gi_disease"
    assert "refined" in out.set_index("sample_key").parse_note["S1"]


def test_dictionary_headings_state_current_row_counts(tmp_path):
    pd.DataFrame({"a": range(7)}).to_csv(tmp_path / "catalog_pass_coverage.csv", index=False)
    pd.DataFrame({"a": range(1234)}).to_parquet(tmp_path / "t.parquet")
    (tmp_path / "DATA_DICTIONARY.md").write_text("# d\n\n### catalog_pass_coverage.csv (5 rows) — one row per project\n\n### t.parquet (1,234 rows)\n\n### gone.csv (3 rows)\n")
    assert package_docs.refresh_row_counts(str(tmp_path)) == 1
    s = (tmp_path / "DATA_DICTIONARY.md").read_text()
    assert "catalog_pass_coverage.csv (7 rows) — one row per project" in s and "t.parquet (1,234 rows)" in s and "gone.csv (3 rows)" in s


def test_registry_fills_a_doi_recorded_after_publication(tmp_path):
    cfg = dict(history=[], files=dict(registry="releases.csv", release_notes="RELEASE_NOTES_{release_id}.md"),
               registry_columns=["release_id", "package_version", "release_date", "data_tag", "site_tag", "doi", "n_samples", "notes_file"],
               releases=[dict(release_id="R1", package_version="1.0.0", release_date="2026-01-01", doi="10.5281/zenodo.1"),
                         dict(release_id="R2", package_version="1.1.0", release_date="2026-02-01", doi="10.5281/zenodo.2"),
                         dict(release_id="R3", package_version="1.2.0")])
    prev = pd.DataFrame([dict(release_id="R1", package_version="1.0.0", release_date="2026-01-01", data_tag="", site_tag="", doi=float("nan"), n_samples=5, notes_file="n1"),
                         dict(release_id="R2", package_version="1.1.0", release_date="2026-02-01", data_tag="", site_tag="", doi="10.5281/zenodo.published", n_samples=6, notes_file="n2")])
    reg = bitemporal.build_registry(cfg, str(tmp_path), "R3", "1.2.0", "2026-03-01", {"n_samples": 7}, prev_registry=prev).set_index("release_id")
    assert reg.doi["R1"] == "10.5281/zenodo.1"                  # empty as published -> filled from the config
    assert reg.doi["R2"] == "10.5281/zenodo.published"          # a published value is never overwritten
    assert reg.doi["R3"] == "" and str(reg.n_samples["R1"]) in ("5", "5.0")


def test_base_map_matches_its_integrity_hash_and_draws_crimea_in_ukraine():
    v = os.path.join(ROOT, "site_generator", "gen", "static", "vendor")
    raw = open(os.path.join(v, "countries-110m.json"), "rb").read()
    sri = json.load(open(os.path.join(v, "SRI.json")))
    want = "sha384-" + base64.b64encode(hashlib.sha384(raw).digest()).decode()
    assert want in json.dumps(sri)
    topo = json.loads(raw)
    geo = {x["properties"]["name"]: x for x in topo["objects"]["countries"]["geometries"]}

    def arcs(x):
        polys = x["arcs"] if x["type"] == "MultiPolygon" else [x["arcs"]]
        return {a if a >= 0 else ~a for p in polys for ring in p for a in ring}
    # the Crimea ring is made of arcs 163 and 164 in this file; arc 164 used to be the Russia/Ukraine border across Perekop
    assert 163 not in arcs(geo["Russia"]) and 164 not in arcs(geo["Russia"])


def test_old_determined_by_limits_a_retirement_to_one_pass(tmp_path):
    det = _det([("S1", "lifestyle", "P1", "urban_industrialized", "urban_industrialized", 0.7, "paper.fulltext.methods", "urban_industrialized", "R3", "fulltext_setting_v1"),
                ("S2", "lifestyle", "P1", "urban_industrialized", "urban_industrialized", 0.7, "paper.fulltext.methods", "residents of Shanghai city", "R3", "catalog_r3b_v1:x")])
    c = _corr(tmp_path, [dict(sample_key="", study_accession="P1", field_name="lifestyle", action="retire", old_value="urban_industrialized", old_route="R3",
                              old_determined_by="fulltext_setting_v1", new_value="", evidence_source="", evidence_locator="", evidence_quote="", confidence=None,
                              rationale="no statement", determined_by="dq_adjudication_v2:r38")])
    out, stats = g.apply_corrections(det, c, "R2026.38", "1.31.0")
    assert stats["n_dropped"] == 1 and out.sample_key.tolist() == ["S2"]


def test_a_checked_value_outranks_automatic_routes_but_not_the_infant_catalog():
    det = _det([("S1", "health_condition", "P1", "x", "other_disease", 0.9, "biosample.attribute.disease", "x", "R1", "r1"),
                ("S1", "health_condition", "P1", "y", "other_infection", 0.8, "ena.study_description", "COVID-19 inpatients", "R4", "dq_adjudication_v2:r38"),
                ("S2", "health_condition", "P1", "y", "other_infection", 0.8, "ena.study_description", "COVID-19 inpatients", "R4", "dq_adjudication_v2:r38"),
                ("S2", "health_condition", "P1", "z", "preterm_nicu", 0.9, "curated", "z", "R1", "infant")])
    det.loc[3, "src_track"] = "infant_catalog"
    keep, conflicts = g.resolve(det)
    v = keep.set_index("sample_key").value_normalized
    assert v["S1"] == "other_infection" and v["S2"] == "preterm_nicu" and len(conflicts) == 2


def test_a_checked_statement_settles_a_source_disagreement():
    det = _det([("S1", "country", "P1", "Sweden", "SE", 0.8, "paper.supp.table", "Country=SWE", "R2", "supp_join_v1")])
    stmt = g._row("", "country", "P1", "SE", "SE", 0.8, "paper.abstract", "PMID:1", "a large cohort of Swedish infants and their mothers", "R4",
                  "dq_adjudication_v2:r38", scope="study_all", release_id="R2026.38", pv="1.31.0")
    det = pd.concat([det, pd.DataFrame([stmt], columns=g.DET_COLS)], ignore_index=True)
    contra = pd.DataFrame([dict(sample_key="S1", study_accession="P1", field_name="country", kind="sources_disagree", value_a="SE", value_b="DK"),
                           dict(sample_key="S1", study_accession="P1", field_name="sex", kind="sources_disagree", value_a="male", value_b="female"),
                           dict(sample_key="S2", study_accession="P2", field_name="country", kind="sources_disagree", value_a="SE", value_b="DK"),
                           dict(sample_key="S3", study_accession="P1", field_name="country", kind="sources_disagree", value_a="DK", value_b="SE")])
    left, n = g.settle_checked_contradictions(contra, det)
    assert n == 1 and len(left) == 3 and not ((left.sample_key == "S1") & (left.field_name == "country")).any()
