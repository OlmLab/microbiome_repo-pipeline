import json

import pandas as pd
import pytest

from catalog.scopes.longitudinal import parse_timepoint_days, relatedness, sample_longitudinal, study_longitudinal


@pytest.mark.parametrize("label,days", [("day 7", 7), ("D14", 14), ("week 2", 14), ("month 12", 365.25), ("baseline", 0), ("visit 3", None), ("T1", None)])
def test_parse_timepoint_days(label, days):
    assert parse_timepoint_days(label) == days


def _wide():
    return pd.DataFrame(dict(
        study_accession=["P1"] * 6 + ["P2"] * 3,
        sample_key=[f"S{i}" for i in range(9)],
        body_site_class=["primary"] * 9,
        subject_id=["a", "a", "a", "b", "b", "m", "x", "y", "z"],
        age_at_collection_days=[10, 40, 100, None, None, None, None, None, None],
        collection_date=[None, None, None, "2020-01-01", "2020-03-01", None, None, None, None],
        timepoint_label=[None] * 9,
        family_id=["F1", "F1", "F1", None, None, "F1", None, None, None],
        family_role=["child", "child", "child", None, None, "mother", None, None, None],
        shared_setting_type=[None] * 9, shared_setting_id=[None] * 9,
    ))


def test_sample_and_study_longitudinal():
    w = sample_longitudinal(_wide(), generated_keys={"S6", "S7", "S8"})
    a = w[w.subject_id == "a"]
    assert list(a.days_since_first_sample) == [0, 30, 90] and set(a.time_axis) == {"age"}
    b = w[w.subject_id == "b"]
    assert list(b.days_since_first_sample) == [0, 60] and set(b.time_axis) == {"date"}
    s = study_longitudinal(w, {"S6", "S7", "S8"})
    assert s.loc["P1", "n_subjects"] == 3 and s.loc["P1", "n_subjects_repeated"] == 2
    assert s.loc["P1", "longitudinal_status"] == "longitudinal" and s.loc["P1", "followup_days_max"] == 90
    assert s.loc["P2", "longitudinal_status"] == "cross_sectional"   # generated one-sample-per-subject ids count as covered


def test_relatedness_family_motif():
    w = sample_longitudinal(_wide(), generated_keys=set())
    G, S = relatedness(w)
    assert len(G) == 1 and G.iloc[0].n_people == 2 and G.iloc[0].motif == "mother + child"
    assert S.loc["P1", "n_mother_child_families"] == 1 and "mother_infant" in S.loc["P1", "relatedness_designs"]
    assert json.loads(S.loc["P1", "family_motifs"]) == {"mother + child": 1}
