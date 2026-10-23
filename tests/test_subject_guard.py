import pandas as pd
from catalog.scopes.build_gut_scope import guard_subject_ids


def _det(rows):
    return pd.DataFrame(rows, columns=["study_accession", "sample_key", "field_name", "value_normalized", "evidence_quote", "determined_by"])


def test_accession_values_and_per_sample_columns_rejected():
    d = _det([
        ("P1", "s1", "subject_id", "ERR1", "sequence_id=ERR1", "r2"),
        ("P2", "s1", "subject_id", "A-1", "Sample=A-1", "r2"),
        ("P2", "s2", "subject_id", "A-2", "Sample=A-2", "r2"),
        ("P3", "s1", "subject_id", "F1", "family_id=F1", "r2"),
        ("P3", "s2", "subject_id", "F1", "family_id=F1", "r2"),
    ])
    out, n = guard_subject_ids(d)
    assert n == 5 and out.empty


def test_shared_values_under_sample_header_kept():
    d = _det([
        ("P4", "s1", "subject_id", "P42", "Manifest=P42", "r2"),
        ("P4", "s2", "subject_id", "P42", "Manifest=P42", "r2"),
        ("P4", "s3", "subject_id", "P41", "Manifest=P41", "r2"),
        ("P5", "s1", "subject_id", "x", "subject=x", "r2"),
    ])
    out, n = guard_subject_ids(d)
    assert n == 0 and len(out) == 4
