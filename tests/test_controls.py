import pandas as pd
from catalog.scopes.controls import detect_controls


def test_detect_controls():
    rows = [("S1", "blank.extraction.3", "", "", "human gut metagenome"), ("S2", "ZymoBIOMICS standard D6300 rep 2", "", "", "metagenome"),
            ("S3", "positive control", "", "human feces", "human gut metagenome"), ("S4", "mock FMT recipient day 7", "", "stool", "human gut metagenome"),
            ("S5", "rhizobium_100x_rep1", "", "rhizosphere", "synthetic metagenome"), ("S6", "P12 stool week 4", "lib12", "stool", "human gut metagenome"),
            ("S7", "12201.tissue.mouse.p.1.zymo.h", "", "", "metagenome"), ("S8", "NTC_plate2", "", "", "metagenome")]
    df = pd.DataFrame(rows, columns=["sample_accession", "sample_title", "library_name", "isolation_source", "scientific_name"]).assign(study_accession="P")
    d = detect_controls(df).set_index("sample_accession")
    assert d.loc["S1", "sample_type"] == "negative_control" and d.loc["S8", "sample_type"] == "negative_control"
    assert d.loc["S2", "sample_type"] == "mock_community" and d.loc["S3", "sample_type"] == "positive_control"
    assert d.loc["S5", "action"] == "exclude"
    assert "S4" not in d.index and "S6" not in d.index and "S7" not in d.index   # mock FMT, ordinary stool, kit name 'zymo'
