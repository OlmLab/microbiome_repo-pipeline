"""R2026.39 paper-link inputs: one row per project and paper; the one-pair audit merge."""
import importlib.util
import os
import subprocess
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_dedupe_papers_one_row_per_paper_and_own_data_wins(monkeypatch):
    P = _load("prepare_catalog_inputs")
    fp = type(sys)("find_papers"); fp.pmids_meta = lambda ids: {"555555": {"title": "T5", "journal": "J", "year": "2020", "pmcid": None, "doi": None}}
    monkeypatch.setitem(sys.modules, "find_papers", fp)
    cols = ["study_accession", "paper_id", "pmid", "pmcid", "doi", "title", "relation", "year", "journal"]
    pp = pd.DataFrame([
        ("P1", "MED:111111", None, None, None, None, "own_data", None, None),
        ("P1", "111111", "111111", "PMC1", "10.1/x", "T1", "reuse", "2019", "J"),     # same paper, with metadata, weaker relation
        ("P1", "10.1000/abc", None, None, "10.1000/abc", "T2", "reuse", "2021", "J"),
        ("P2", "MED:555555", None, None, None, None, "own_data", None, None),         # metadata only from Europe PMC
        ("P2", "PPR:9", None, None, None, None, "reuse", None, None),
    ], columns=cols)
    out = P.dedupe_papers(pp).set_index(["study_accession", "paper_id"])
    assert len(out) == 4
    assert out.loc[("P1", "111111"), "relation"] == "own_data" and out.loc[("P1", "111111"), "title"] == "T1"
    assert out.loc[("P2", "555555"), "title"] == "T5" and out.loc[("P2", "555555"), "pmid"] == "555555"
    assert ("P2", "PPR:9") in out.index and ("P1", "10.1000/abc") in out.index


def test_merge_link_audit_commits_and_keeps_earlier_rows(tmp_path):
    L = tmp_path / "large_update"; (L / "stage11_merged").mkdir(parents=True); out = tmp_path / "audit_out"; out.mkdir()
    pd.DataFrame([("P1", "unclear", "MED:111", 2), ("P1", "reused", "MED:222", 2), ("P9", "generated", "MED:999", 1)],
                 columns=["study_accession", "relation", "paper_id", "n"]).to_parquet(L / "stage11_merged" / "link_audit.parquet")
    base = dict(pmid=None, pmcid=None, doi=None, title="t", journal="j", year="2020", relation_first="generated", relation_check="generated",
                basis="accession_deposit_statement", basis_check="accession_deposit_statement", quote="q", quote_verbatim=True, quote_source="s",
                text_mode="fulltext", accession_in_text=True, confidence=0.9, status="ok", status_check="ok", methods="epmc_accession")
    rows = [dict(base, study_accession="P1", paper_id="111", relation="generated"),                       # decides an earlier 'unclear'
            dict(base, study_accession="P1", paper_id="333", relation="unclear"),                         # new, undecided
            dict(base, study_accession="P2", paper_id="444", relation="generated", basis="design_match", basis_check="design_match", year="2021"),
            dict(base, study_accession="P2", paper_id="445", relation="generated", basis="design_match", basis_check="design_match", year="2019"),
            dict(base, study_accession="P3", paper_id="446", relation="refused", status="refused")]
    pd.DataFrame(rows).to_parquet(out / "pairs_0.parquet")
    (out / "scope").mkdir()
    pd.DataFrame([("P1", "111", "whole", "record_is_this_study", "ok"), ("P2", "444", "part", "umbrella_or_resource_accession", "ok"),
                  ("P2", "445", "whole", "numbers_and_dates_match", "ok")],
                 columns=["study_accession", "paper_id", "link_scope", "scope_reason", "scope_status"]).to_parquet(out / "scope" / "scope_0.parquet")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "merge_link_audit_fulltext.py"), "--audit-out", str(out), "--cache", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    la = pd.read_parquet(L / "stage17_deep_pass" / "link_audit.parquet")
    d = {(a, p): rel for a, p, rel in zip(la.study_accession, la.paper_id, la.relation)}
    assert d[("P1", "MED:111")] == "generated"            # the earlier row's id is kept, its verdict replaced
    assert d[("P1", "MED:222")] == "reused"               # untouched earlier row stays (prepare replaces a project's rows wholesale)
    assert d[("P1", "333")] == "unclear"
    assert d[("P2", "445")] == "generated" and d[("P2", "444")] == "generated_part_not_committed"   # scope check: part of an umbrella
    assert d[("P3", "446")] == "unclear"                  # a refusal is recorded, never a link
    assert "P9" not in set(la.study_accession)            # projects the audit did not touch are not rewritten
    ev = pd.read_parquet(L / "stage17_deep_pass" / "link_evidence.parquet")
    assert set(zip(ev.study_accession, ev.paper_id)) == {("P1", "MED:111"), ("P2", "445")} and ev.evidence_quote.notna().all()
