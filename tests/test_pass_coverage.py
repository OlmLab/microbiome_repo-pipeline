import importlib.util
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cpc", os.path.join(HERE, "..", "scripts", "check_pass_coverage.py"))
cpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpc)


def _w(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_parquet(path, index=False)


def test_coverage_statuses(tmp_path):
    g = str(tmp_path / "gut")
    st = pd.DataFrame(dict(study_accession=["P1", "P2", "P3"], cov_subject_id=[1.0, 0.5, 0.2], release_retired=[None, None, None]))
    _w(st, str(tmp_path / "studies.parquet"))
    _w(pd.DataFrame(dict(study_accession=["P1", "P2"], pmcid=["PMC1", "PMC2"], contested=[False, False])), str(tmp_path / "papers.parquet"))
    _w(pd.DataFrame(dict(study_accession=["P1", "P2"], fulltext_available=[True, False])), g + "/r3/gut_r3_study_summary_all.parquet")
    _w(pd.DataFrame(dict(study_accession=["P1", "P2", "P3"])), g + "/r4/gut_r4_study_summary_shard_0.parquet")
    _w(pd.DataFrame(dict(study_accession=["P1", "P2"])), g + "/interventions/gut_intervention_studies_shard_0.parquet")
    _w(pd.DataFrame(dict(study_accession=["P1", "P2", "P3"], pmcids_tried=["PMC1", "", ""], fulltext_available=[True, False, False], supp_files_found=["2", None, None])), g + "/deep/deep_sources_shard_0.parquet")
    _w(pd.DataFrame(dict(study_accession=["P2"])), g + "/design/logs/subject_log_shard_S0.parquet")
    _w(pd.DataFrame(dict(study_accession=["P1", "P2", "P3"])), g + "/design/study_design_shard_F0.parquet")
    ex = tmp_path / "ex.csv"
    ex.write_text("study_accession,pass,reason\nP3,subject,content filter\n")
    T, passes = cpc.coverage(g, str(tmp_path / "studies.parquet"), str(tmp_path / "papers.parquet"), str(ex))
    T = T.set_index("study_accession")
    assert T.at["P1", "fulltext"] == "done" and T.at["P2", "fulltext"] == "attempted_failed" and T.at["P3", "fulltext"] == "not_applicable"
    assert T.at["P3", "interventions"] == "missing" and T.at["P2", "supplementary"] == "missing" and T.at["P1", "supplementary"] == "done"
    assert T.at["P1", "subject"] == "not_applicable" and T.at["P2", "subject"] == "done" and T.at["P3", "subject"].startswith("exempt")
    assert T.n_missing.to_dict() == {"P1": 0, "P2": 1, "P3": 1}
