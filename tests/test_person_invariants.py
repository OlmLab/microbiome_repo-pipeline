"""R2026.36: sex / country propagation within people (catalog.scopes.longitudinal.propagate_person_invariants)."""
import pandas as pd

from catalog.scopes.longitudinal import propagate_person_invariants


def _w():
    return pd.DataFrame({
        "sample_key": ["a1", "a2", "b1", "b2", "c1", "c2", "c3", "d1", "d2", "e1", "e2"],
        "study_accession": ["PA", "PA", "PB", "PB", "PA", "PA", "PA", "PA", "PA", "PC", "PC"],
        # 'P1' recurs in PA and PB: different people
        "person_key": ["P1", "P1", "P1", "P1", "P2", "P2", "P2", "sample:d1", "P3", "P4", "P4"],
        "sample_type": ["specimen"] * 8 + ["negative_control", "specimen", "specimen"],
        "sex": ["female", None, "male", None, "male", "female", None, "female", None, "male", None],
        "sex__confidence": [0.9, None, 0.8, None, 0.9, 0.9, None, 0.9, None, 0.9, None],
        "sex__route": ["R1", None, "R2", None, "R1", "R1", None, "R1", None, "R1", None],
    })


def _det(w):
    d = w[w.sex.notna()][["sample_key", "study_accession", "sex", "sex__confidence", "sex__route"]]
    return pd.DataFrame({"sample_key": d.sample_key, "field_name": "sex", "study_accession": d.study_accession,
                         "field_value": d.sex, "value_normalized": d.sex, "confidence": d.sex__confidence,
                         "evidence_source": "sample.attr.sex", "evidence_locator": d.sample_key, "evidence_quote": d.sex,
                         "determined_by": "x", "route": d.sex__route, "scope": "sample", "parse_note": None, "src_track": "t",
                         "release_added": "R0", "release_retired": None, "package_added": "0"})


def test_within_project_only_and_conflicts_untouched():
    w = _w()
    w2, rows, rep = propagate_person_invariants(w, _det(w), "R9", "9.9", fields=("sex",))
    got = dict(zip(w2.sample_key, w2.sex))
    assert got["a2"] == "female"          # PA/P1 is female
    assert got["b2"] == "male"            # PB/P1 is male — not mixed with PA/P1
    assert got["c3"] is None or pd.isna(got["c3"])   # PA/P2 disagrees: never filled
    assert pd.isna(got["d2"])             # control sample does not receive values
    assert got["e2"] == "male"
    assert rep["sex"]["filled"] == 3 and rep["sex"]["conflicting_people"] == 1
    assert set(rows.sample_key) == {"a2", "b2", "e2"}
    assert (rows.determined_by == "person_invariant_v1").all() and (rows.src_track == "person_invariant").all()
    r = rows.set_index("sample_key")
    assert r.loc["a2", "confidence"] == round(0.9 * 0.95, 3) and r.loc["b2", "route"] == "R2"
    assert "a1" in r.loc["a2", "parse_note"]
    assert w2.loc[w2.sample_key == "a2", "sex__route"].item() == "R1"
    # existing values never change
    keep = w.sex.notna()
    assert (w2.loc[keep, "sex"] == w.loc[keep, "sex"]).all()


def test_exclusion_list():
    w = _w()
    w2, rows, rep = propagate_person_invariants(w, _det(w), "R9", "9.9", fields=("sex",), exclude_projects={"sex": {"PC"}})
    assert pd.isna(w2.loc[w2.sample_key == "e2", "sex"].item())
    assert set(rows.sample_key) == {"a2", "b2"} and rep["sex"]["excluded_projects"] == ["PC"]
