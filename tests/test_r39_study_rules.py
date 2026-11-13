"""R2026.39 (catalog deep pass, stage A): kit-text processing rule, linked paper ids, subtype roll-up, Sandpiper gap reasons."""
import os
import subprocess
import sys

import pandas as pd
import pytest
import yaml

from catalog.scopes.study_rules import NEW_CODE, apply_kit_processing, fill_linked_papers, rollup_subtype_from_detail

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _gs(rows):
    return pd.DataFrame(rows).set_index("study_accession")


def test_kit_rule_replaces_wga_mda_only_without_an_mda_chemistry():
    gs = _gs([
        dict(study_accession="A", processing="virome_vlp;wga_mda", library_kit="WTA2 kit (Sigma-Aldrich) or SISPA, followed by tagmentation", extraction_kit=None),
        dict(study_accession="B", processing="virome_vlp;wga_mda", library_kit="REPLI-g WTA Single Cell Kit (QIAGEN) amplification", extraction_kit=None),
        dict(study_accession="C", processing=None, library_kit="Random primer D2-8N amplification with Klenow fragment", extraction_kit=None),
        dict(study_accession="D", processing="host_depletion", library_kit=None, extraction_kit="Random RT-PCR amplification with Transplex WTA kit"),
        dict(study_accession="E", processing="none;wga_mda", library_kit="Nextera XT", extraction_kit="REPLI-g Mini Kit (Qiagen) for WGA"),
        dict(study_accession="F", processing="none", library_kit="random-fragment library", extraction_kit=None),
    ])
    out, n = apply_kit_processing(gs)
    assert out.at["A", "processing"] == f"{NEW_CODE};virome_vlp"
    assert out.at["B", "processing"] == f"{NEW_CODE};virome_vlp;wga_mda"       # REPLI-g is phi29 chemistry: wga_mda stays
    assert out.at["C", "processing"] == NEW_CODE
    assert out.at["D", "processing"] == f"host_depletion;{NEW_CODE}"
    assert out.at["E", "processing"] == "none;wga_mda"                          # an MDA kit alone is untouched
    assert out.at["F", "processing"] == "none"                                  # 'random-fragment' is not random priming
    assert n == 4


def test_new_processing_code_is_in_the_vocabulary():
    v = yaml.safe_load(open(os.path.join(ROOT, "config", "vocab", "processing.yaml")))
    assert NEW_CODE in v["codes"] and "wga_mda" in v["codes"]


def test_linked_papers_filled_for_every_project_and_carried_across_a_retirement():
    gs = _gs([
        dict(study_accession="P1", linked_pmids=None, n_linked_papers=None),          # re-added project: ids only on the retired row
        dict(study_accession="P2", linked_pmids=None, n_linked_papers=None),          # never had the column filled
        dict(study_accession="P3", linked_pmids="111111;222222", n_linked_papers=3),  # infant-catalog row
        dict(study_accession="P4", linked_pmids=None, n_linked_papers=None),          # no paper at all
    ])
    papers = pd.DataFrame([
        dict(study_accession="P1", pmid="33957082", paper_id="MED:33957082", relation="own_data"),
        dict(study_accession="P1", pmid="40210629", paper_id="MED:40210629", relation="reuse"),
        dict(study_accession="P2", pmid=None, paper_id="40617850", relation="own_data"),
        dict(study_accession="P2", pmid=None, paper_id="10.1000/xyz", relation="reuse"),
        dict(study_accession="P3", pmid="222222", paper_id="222222", relation="own_data"),
    ])
    previous = pd.DataFrame([
        dict(study_accession="P1", linked_pmids="33957082;40210629;30763537"),         # retired row
        dict(study_accession="P1", linked_pmids=None),                                 # current row of the previous package
        dict(study_accession="P3", linked_pmids="111111;222222"),
    ])
    out, stats = fill_linked_papers(gs, papers, previous)
    assert out.at["P1", "linked_pmids"] == "33957082;40210629;30763537"             # own-data first, carried ids kept, reuse last (already carried)
    assert out.at["P2", "linked_pmids"] == "40617850" and out.at["P2", "n_linked_papers"] == 1
    assert out.at["P3", "linked_pmids"] == "222222;111111" and out.at["P3", "n_linked_papers"] == 3
    assert pd.isna(out.at["P4", "linked_pmids"]) and pd.isna(out.at["P4", "n_linked_papers"])
    assert stats["filled"] == 2 and stats["carried_from_earlier_row"] == 1


def _det(rows):
    base = dict(study_accession="S", field_value=None, confidence=0.8, determined_by="x", route="R1", scope="sample", parse_note=None, src_track="gut_all")
    return pd.DataFrame([dict(base, **r) for r in rows])


def test_subtype_rollup_uses_the_samples_own_detail_and_leaves_checked_rows():
    table = pd.read_csv(os.path.join(ROOT, "config", "health_subtype_rollup.csv"), dtype=str)
    assert set(table.new_code) == {"crohns_disease", "ulcerative_colitis"} and set(table.parent_code) == {"ibd_unspecified"}
    det = _det([
        dict(sample_key="s1", field_name="health_condition", value_normalized="ibd_unspecified"),
        dict(sample_key="s1", field_name="health_condition_detail", value_normalized="UC"),
        dict(sample_key="s2", field_name="health_condition", value_normalized="ibd_unspecified"),
        dict(sample_key="s2", field_name="health_condition_detail", value_normalized="CD"),
        dict(sample_key="s3", field_name="health_condition", value_normalized="ibd_unspecified"),
        dict(sample_key="s3", field_name="health_condition_detail", value_normalized="CD/UC"),          # two subtypes: left
        dict(sample_key="s4", field_name="health_condition", value_normalized="ibd_unspecified", determined_by="dq_adjudication_v2:r38"),
        dict(sample_key="s4", field_name="health_condition_detail", value_normalized="UC"),             # a checker chose the parent: left
        dict(sample_key="s5", field_name="health_condition", value_normalized="healthy_control"),
        dict(sample_key="s5", field_name="health_condition_detail", value_normalized="UC"),             # not a parent code: left
        dict(sample_key="s6", field_name="health_condition", value_normalized="ibd_unspecified"),       # no detail: left
    ])
    out, n = rollup_subtype_from_detail(det, table, "R2026.39")
    hc = out[out.field_name.eq("health_condition")].set_index("sample_key").value_normalized.to_dict()
    assert hc == {"s1": "ulcerative_colitis", "s2": "crohns_disease", "s3": "ibd_unspecified", "s4": "ibd_unspecified",
                  "s5": "healthy_control", "s6": "ibd_unspecified"}
    assert n == 2
    note = out[(out.sample_key == "s1") & out.field_name.eq("health_condition")].parse_note.iloc[0]
    assert "health_condition_detail" in note and "was ibd_unspecified" in note


def test_sandpiper_gap_reasons(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    pk = tmp_path / "pkg"; pk.mkdir()
    pd.DataFrame(dict(sample_key=[f"s{i}" for i in range(1, 9)], study_accession="P", release_retired=None)).to_parquet(pk / "catalog_sample_metadata_wide.parquet")
    pd.DataFrame(dict(sample_key=["s1"], n_runs_profiled=[1])).to_parquet(pk / "catalog_sandpiper_sample_summary.parquet")
    runs = [  # run, sample, platform, selection, source, bases, first_public, nucleic acid
        ("R1", "s1", "ILLUMINA", "RANDOM", "METAGENOMIC", 5e9, "2024-01-01", "dna"),      # profiled
        ("R2", "s2", "ILLUMINA", "RANDOM", "METAGENOMIC", 5e9, "2024-01-01", "dna"),      # listed, no profile
        ("R3", "s3", "ILLUMINA", "RANDOM", "METAGENOMIC", 5e9, "2026-05-01", "dna"),      # after snapshot
        ("R4", "s4", "ILLUMINA", "PCR", "METAGENOMIC", 0.6e9, "2024-01-01", "dna"),       # size rule (<= 1 Gbp, not RANDOM)
        ("R5", "s5", "OXFORD_NANOPORE", "RANDOM", "METAGENOMIC", 5e9, "2024-01-01", "dna"),
        ("R6", "s6", "ILLUMINA", "RANDOM", "METAGENOMIC", 5e9, "2024-01-01", "dna"),      # meets the rule, absent
        ("R7", "s7", "ILLUMINA", "RANDOM", "METATRANSCRIPTOMIC", 5e9, "2024-01-01", "rna"),
        ("R8a", "s8", "ILLUMINA", "RANDOM", "METAGENOMIC", 0.05e9, "2024-01-01", "dna"),  # two runs: the closer one decides
        ("R8b", "s8", "ILLUMINA", "RANDOM", "METAGENOMIC", 5e9, "2024-01-01", "dna"),
    ]
    pd.DataFrame(runs, columns=["run_accession", "sample_key", "instrument_platform", "library_selection", "library_source", "base_count",
                                "first_public", "nucleic_acid"]).assign(library_strategy="WGS").to_parquet(pk / "catalog_runs.parquet")
    (tmp_path / "pa.csv").write_text("sample,root_coverage\nR1,10\n")
    (tmp_path / "kf.tsv").write_text("run\tpublished\nR1\t2024-01-02 00:00:00\nR2\t2024-01-02 00:00:00\nRX\t2026-03-02 10:00:00\n")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "sandpiper_gap.py"), "--package", str(pk), "--per-acc", str(tmp_path / "pa.csv"),
                        "--run-list", str(tmp_path / "kf.tsv"), "--version", "test"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = pd.read_parquet(pk / "catalog_sandpiper_unprofiled.parquet").set_index("sample_key")
    assert "s1" not in out.index
    assert out.reason.to_dict() == {"s2": "listed_no_profile", "s3": "after_snapshot", "s4": "size_rule", "s5": "platform", "s6": "unexplained",
                                    "s7": "rna", "s8": "unexplained"}
    assert out.sandpiper_snapshot_date.iloc[0] == "2026-03-02"
    by = pd.read_csv(pk / "catalog_sandpiper_gap_by_cause.csv")
    assert by.samples.sum() == 7
