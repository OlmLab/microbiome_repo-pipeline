import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from catalog.scopes.study_sources import classify_rows, study_source_flags  # noqa: E402


def _det(rows):
    cols = ["study_accession", "sample_key", "field_name", "value_normalized", "evidence_source", "determined_by", "route", "src_track"]
    return pd.DataFrame(rows, columns=cols)


def test_classify_rows_routes_and_external():
    d = _det([
        ("P1", "s1", "sex", "male", "sample.attr.sex", "gut_r1_v1", "R1", "gut_all_v1"),
        ("P1", "s1", "age_at_collection_days", "100", "external.cmd", "external_ingest_v1", "R2", "gut_all_v1"),
        ("P1", "s2", "bmi", "22.1", "paper.supp.t1.xlsx[S1!BMI]", "gut_r2_deep_v1", "R2", "gut_all_v1"),
        ("P1", "s2", "country", "US", "paper.fulltext.methods", "gut_r3_v1", "R3", "gut_all_v1"),
        ("P1", "s2", "country", "US", "study.description", "gut_r4_v1", "R4", "gut_all_v1"),
        ("P1", "s3", "sex", "female", "contribution.issue_12", "contribution_v1", "R2", "gut_all_v1"),
    ])
    assert classify_rows(d).tolist() == ["archive", "external", "supplement", "fulltext", "abstract", "contribution"]


def test_flags_used_checked_none(tmp_path):
    d = _det([
        ("P1", "s1", "sex", "male", "sample.attr.sex", "gut_r1_v1", "R1", "gut_all_v1"),
        ("P1", "s1", "health_condition", "unknown", "paper.fulltext.methods", "gut_r3_v1", "R3", "gut_all_v1"),
        ("P2", "s9", "sex", "female", "sample.attr.sex", "infant", "R1", "infant_catalog"),
    ])
    (tmp_path / "r3").mkdir()
    pd.DataFrame({"study_accession": ["P1"], "fulltext_available": [1], "pmcid": ["PMC1"]}).to_parquet(tmp_path / "r3" / "gut_r3_study_summary_all.parquet")
    f = study_source_flags(d, ["P1", "P2", "P3"], str(tmp_path), {"P2"})
    assert f.loc["P1", "src_archive"] == "used" and f.loc["P1", "src_fulltext"] == "checked"   # 'unknown' is not a value
    assert f.loc["P1", "src_supplement"] == "none" and f.loc["P3", "src_archive"] == "checked"
    assert f.loc["P2", "src_expert"] == "used" and f.loc["P1", "src_expert"] == "none"
    assert json.loads(f.loc["P1", "src_detail"])["fulltext"]["pmcids"] == ["PMC1"]
