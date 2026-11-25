"""R2026.44: checked body-site codes per sample (config/body_site_corrections.csv)."""
import os

import pandas as pd
import pytest

from catalog.scopes.build_gut_scope import BODY_SITE_CODES, apply_body_site_corrections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _samples():
    return pd.DataFrame(dict(sample_key=["S1", "S2", "S3"], study_accession=["P1", "P1", "P2"], body_site_code=["gut_stool", "other_site", None]))


def test_sets_code_and_basis_by_source():
    corr = pd.DataFrame(dict(sample_key=["S1", "S3", "S9"], body_site_code=["oral", "gut_stool", "skin"],
                             evidence_source=["biosample.attribute:isolation_source", "study.description", "sample.attr.tissue"]))
    out, basis = apply_body_site_corrections(_samples(), corr)
    assert out.set_index("sample_key").body_site_code.to_dict() == {"S1": "oral", "S2": "other_site", "S3": "gut_stool"}
    assert basis == {"S1": "sample_attribute", "S3": "study_single_site"}   # S9 is not in the catalog: skipped


def test_unknown_code_is_an_error():
    corr = pd.DataFrame(dict(sample_key=["S1"], body_site_code=["saliva"], evidence_source=["biosample.attribute"]))
    with pytest.raises(ValueError):
        apply_body_site_corrections(_samples(), corr)


def test_shipped_file_is_well_formed():
    f = os.path.join(ROOT, "config", "body_site_corrections.csv")
    d = pd.read_csv(f, dtype=str, keep_default_na=False)
    assert not d.sample_key.duplicated().any()
    assert set(d.body_site_code) <= BODY_SITE_CODES
    assert (d.evidence_quote.str.len() > 0).all() and (d.evidence_source.str.len() > 0).all()
    assert (d.body_site_code != d.old_body_site_code).all()


def test_code_list_matches_vocabulary():
    import yaml
    v = yaml.safe_load(open(os.path.join(ROOT, "config", "vocab", "body_sites.yaml")))
    codes = next(x for x in v.values() if isinstance(x, dict) and "gut_stool" in x)
    assert BODY_SITE_CODES == set(codes) - {"unknown_site"}
