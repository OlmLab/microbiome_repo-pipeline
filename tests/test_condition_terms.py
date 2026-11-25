"""R2026.44: condition name and ontology ids beside the condition label (build_gut_scope.add_condition_terms)."""
import pandas as pd

from catalog.scopes import build_gut_scope as bgs


def test_lookup_order_and_empty_values(tmp_path):
    t = pd.DataFrame([
        dict(study_accession="", health_condition="ibd_crohns", health_condition_detail="CD", condition_name="Crohn's disease", efo_id="EFO:1", mondo_id="MONDO:1"),
        dict(study_accession="PRJ2", health_condition="ibd_crohns", health_condition_detail="CD", condition_name="ileal Crohn's disease", efo_id="EFO:2", mondo_id=""),
        dict(study_accession="", health_condition="obesity", health_condition_detail="*", condition_name="obesity", efo_id="EFO:3", mondo_id="MONDO:3"),
        dict(study_accession="", health_condition="healthy_control", health_condition_detail="control", condition_name="", efo_id="", mondo_id=""),
    ])
    f = tmp_path / "terms.csv"
    t.to_csv(f, index=False)
    w = pd.DataFrame(dict(sample_key=list("abcdefg"), study_accession=["PRJ1", "PRJ2", "PRJ1", "PRJ1", "PRJ1", "PRJ1", "PRJ1"],
                          health_condition=["ibd_crohns", "ibd_crohns", "obesity", "healthy_control", "ibs", None, "obesity"],
                          health_condition_detail=["CD", "CD", "BMI over 30", "control", "IBS-D", None, None], other=1))
    out, st = bgs.add_condition_terms(w, str(f))
    cols = list(out.columns)
    i = cols.index("health_condition_detail")
    assert cols[i + 1:i + 4] == bgs.CONDITION_TERM_COLS
    n = out.set_index("sample_key").health_condition_name
    assert n["a"] == "Crohn's disease" and n["b"] == "ileal Crohn's disease" and n["c"] == "obesity" and n["g"] == "obesity"
    assert pd.isna(n["d"]) and pd.isna(n["e"]) and pd.isna(n["f"])
    assert pd.isna(out.set_index("sample_key").health_condition_mondo_id["b"])
    assert st["labelled"] == 6 and st["pair_found"] == 5 and st["named"] == 4 and st["pairs_not_in_table"] == 1


def test_without_a_file_the_columns_exist_and_are_empty():
    w = pd.DataFrame(dict(sample_key=["a"], study_accession=["P"], health_condition=["ibs"], health_condition_detail=["x"]))
    out, st = bgs.add_condition_terms(w, None)
    assert all(c in out.columns and out[c].isna().all() for c in bgs.CONDITION_TERM_COLS) and st["file"] is False
