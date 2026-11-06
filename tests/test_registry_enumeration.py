"""Registry enumeration (scale-up S1a): aggregation + rule columns on a 3-study fixture. No network."""
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import catalog  # noqa: E402,F401
sys.path.insert(0, os.path.join(HERE, "..", "src", "catalog", "registry"))
import enumerate_registry as ER  # noqa: E402

FIX = os.path.join(HERE, "data", "registry_fixture")


def _load():
    runs = pd.read_csv(os.path.join(FIX, "runs.csv"), dtype=str, keep_default_na=False)
    meta = pd.read_csv(os.path.join(FIX, "study_meta.csv"), dtype=str, keep_default_na=False)
    infant = pd.read_csv(os.path.join(FIX, "infant_universe.csv"), dtype=str, keep_default_na=False)
    return runs, meta, infant


def test_pull_fields_contract():
    assert len(ER.PULL_FIELDS) == 45
    assert ER.RUNS_OUT_COLS == ER.PULL_FIELDS + ["found_by"]


def test_aggregation_columns_and_counts():
    runs, meta, infant = _load()
    st = ER.build_universe(runs, meta, infant, lambda s: None).set_index("study_accession")
    assert list(st.index) == ["PRJTEST01", "PRJTEST02", "PRJTEST03"]
    a = st.loc["PRJTEST01"]
    assert a.n_runs == 3 and a.n_samples == 2 and a.n_biosamples == 2
    assert a.n_runs_host_9606 == 3 and a.n_runs_nonhuman_host == 0
    assert a.library_strategies == "WGS" and a.library_sources == "METAGENOMIC"
    assert a.scientific_names_top == "human gut metagenome(3)"
    assert a.host_tax_ids == "9606"
    assert a.first_public_min == "2024-05-01" and a.first_public_max == "2024-05-01"
    assert len(a.description_short) <= 300 and a.has_ena_study_record
    assert a.median_base_count == 3e9 and a.median_read_len_proxy == 300.0
    assert '"infant stool month 3": 2' in a.top_sample_title
    b = st.loc["PRJTEST02"]
    assert b.n_runs_nonhuman_host == 2 and b.host_tax_ids == "10090"
    c = st.loc["PRJTEST03"]
    assert not c.has_ena_study_record and c.study_title == "Oral microbiome of adult volunteers"
    assert c.library_strategies == "OTHER;WGS" and c.found_by == "S1;S3"
    for col in ER.UNIVERSE_COL_ORDER:
        assert col == "study_accession" or col in st.columns, col
    assert st.top_tissue.eq("").all()


def test_rule_columns_and_candidate_class():
    runs, meta, infant = _load()
    st = ER.build_universe(runs, meta, infant, lambda s: None).set_index("study_accession")
    assert bool(st.loc["PRJTEST01", "human_signal"]) and st.loc["PRJTEST01", "human_signal_rule"] == "A"
    assert not bool(st.loc["PRJTEST02", "human_signal"]) and st.loc["PRJTEST02", "human_signal_rule"] == "none"
    assert not bool(st.loc["PRJTEST02", "ambiguous"])
    assert bool(st.loc["PRJTEST03", "human_signal"]) and bool(st.loc["PRJTEST03", "taxon_name_rule"])
    assert st.loc["PRJTEST01", "in_infant_catalog"] == "include"
    assert st.loc["PRJTEST02", "in_infant_catalog"] == "not_screened"
    assert st.candidate_class.to_dict() == {"PRJTEST01": "prior_human", "PRJTEST02": "nosignal_new",
                                            "PRJTEST03": "signal_human_new"}


def test_biosample_index_only_human_candidates():
    runs, meta, infant = _load()
    st = ER.build_universe(runs, meta, infant, lambda s: None)
    bi = ER.biosample_index(runs, st)
    assert set(bi.study_accession) == {"PRJTEST01", "PRJTEST03"}
    assert len(bi) == 4 and int(bi.n_runs.sum()) == 5
    assert bi.loc[bi.sample_accession == "SAMTEST001", "n_runs"].iloc[0] == 2


def test_candidate_class_prior_nonhuman():
    st = pd.DataFrame({"infant_screened": [True, True, False, False],
                       "infant_reason_code": ["host_nonhuman", "age_adult_only", "", ""],
                       "human_signal_any": [True, False, False, False],
                       "ambiguous": [False, False, True, False]})
    assert ER.candidate_class(st).tolist() == ["prior_nonhuman", "prior_human", "ambiguous_new", "nosignal_new"]


def test_slice_queries_exclude_metatranscriptomic():
    # DNA slices S1-S3 never pull metatranscriptomes; RNA projects come only from their own slice (S4, kept apart from DNA since R2026.31)
    for sid, tag, q, parts in ER.build_slices():
        if sid == "S4":
            assert tag == "rna_human" and 'library_source="METATRANSCRIPTOMIC"' in q
        else:
            assert "METATRANSCRIPTOMIC" not in q
    s1 = [s for s in ER.build_slices() if s[0] == "S1"][0]
    assert s1[3] and s1[3][0][0] == "y2010"
    assert 'library_source="METAGENOMIC"' in s1[2]
