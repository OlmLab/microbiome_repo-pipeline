"""R2026.44: two rounds of corrections on the same sample and field (the later round stands); a per-sample withdrawal also holds
against a project-level statement that is expanded to the samples afterwards."""
import pandas as pd

from catalog.scopes import build_gut_scope as g


def _det(rows):
    return pd.DataFrame([g._row(sk, f, st, raw, norm, conf, src, "loc", quote, route, by, scope="sample", release_id="R2026.1", pv="1.0.0")
                         for sk, f, st, raw, norm, conf, src, quote, route, by in rows], columns=g.DET_COLS)


def _c(**kw):
    base = dict(sample_key="S1", study_accession="P1", field_name="health_condition_detail", action="recode", old_value="", old_route="", old_determined_by="",
                new_value="", evidence_source="ena.study_description", evidence_locator="P1", evidence_quote="q", confidence=0.8, rationale="r",
                determined_by="dq_adjudication_v2:r41")
    return base | kw


def _files(tmp_path, *rounds):
    out = []
    for i, rows in enumerate(rounds):
        p = tmp_path / f"round{i}.parquet"
        pd.DataFrame(rows).to_parquet(p)
        out.append(str(p))
    return " ".join(out)


DET = [("S1", "health_condition_detail", "P1", "old text", "old text", 0.7, "paper.abstract", "x", "R4", "r4"),
       ("S2", "health_condition_detail", "P1", "old text", "old text", 0.7, "paper.abstract", "x", "R4", "r4")]


def test_later_round_replaces_the_earlier_one_whatever_the_confidence(tmp_path):
    first = [_c(new_value="first reading", confidence=0.9), _c(sample_key="S2", new_value="first reading", confidence=0.9)]
    second = [_c(new_value="second reading", confidence=0.7, determined_by="dq_adjudication_v2:r44")]
    out, st = g.apply_corrections(_det(DET), _files(tmp_path, first, second), "R2026.44", "1.35.0")
    keep, _ = g.resolve(out)
    v = keep.set_index("sample_key").value_normalized.to_dict()
    assert v == {"S1": "second reading", "S2": "first reading"}
    assert st["n_superseded_by_later_round"] == 1
    assert len(out[out.sample_key == "S1"]) == 1          # the first round's value is not added at all


def test_later_withdrawal_cancels_an_earlier_value(tmp_path):
    first = [_c(new_value="first reading")]
    second = [_c(action="retire", determined_by="dq_adjudication_v2:r44")]
    out, _ = g.apply_corrections(_det(DET), _files(tmp_path, first, second), "R2026.44", "1.35.0")
    assert out[out.sample_key == "S1"].empty and len(out[out.sample_key == "S2"]) == 1


def test_withdrawn_cells(tmp_path):
    first = [_c(action="retire"), _c(sample_key="S2", action="retire"), _c(sample_key="S3", action="retire", old_value="only this value"),
             _c(sample_key="", action="retire")]
    second = [_c(sample_key="S2", new_value="a value after all", determined_by="dq_adjudication_v2:r44")]
    cells = g.withdrawn_cells(_files(tmp_path, first, second))
    assert cells == {("S1", "health_condition_detail")}    # S2 got a value later, S3 names one value, the project-wide row has no sample
    assert g.withdrawn_cells(None) == set()


def test_project_statement_does_not_refill_a_withdrawn_cell(tmp_path):
    cells = g.withdrawn_cells(_files(tmp_path, [_c(action="retire")]))
    study = _det([("", "health_condition_detail", "P1", "cohort text", "cohort text", 0.6, "paper.abstract", "x", "R4", "r4")]).assign(scope="study_all")
    samples = pd.DataFrame(dict(sample_key=["S1", "S2"], study_accession=["P1", "P1"]))
    r4 = g.expand_study_all(pd.DataFrame(columns=g.DET_COLS), study, samples)
    assert set(r4.sample_key) == {"S1", "S2"}
    r4 = r4[~pd.Series([(s, f) in cells for s, f in zip(r4.sample_key, r4.field_name)], index=r4.index)]
    assert set(r4.sample_key) == {"S2"}
