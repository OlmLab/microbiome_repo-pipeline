"""Precedence in catalog.scopes.build_gut_scope.resolve(): infant catalog rows rank first; checked person / time rows (R2026.34)
share that rank and win on confidence; other rows keep route order."""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from catalog.scopes import build_gut_scope as bgs  # noqa: E402


def _row(sk, value, norm, conf, by, route="R1", track=None, field="timepoint_label"):
    r = dict.fromkeys(bgs.DET_COLS)
    r.update(sample_key=sk, field_name=field, study_accession="PRJX1", field_value=value, value_normalized=norm,
             confidence=conf, determined_by=by, route=route, src_track=track)
    return r


def test_persontime_row_beats_infant_parser_row_on_confidence():
    det = pd.DataFrame([_row("S1", "BL", "BL", 0.8, "deterministic_r1", track="infant_catalog"),
                        _row("S1", "BL", "baseline", 0.91, "catalog_persontime_v1_label")])
    keep, conflicts = bgs.resolve(det)
    assert keep.value_normalized.tolist() == ["baseline"]
    assert conflicts.value_normalized.tolist() == ["BL"]


def test_infant_row_with_higher_confidence_still_wins():
    det = pd.DataFrame([_row("S1", "BL", "baseline visit", 0.95, "manual", track="infant_catalog"),
                        _row("S1", "BL", "baseline", 0.91, "catalog_persontime_v1_label")])
    keep, _ = bgs.resolve(det)
    assert keep.value_normalized.tolist() == ["baseline visit"]


def test_other_rows_keep_route_order():
    det = pd.DataFrame([_row("S1", "BL", "BL", 0.7, "deterministic_r1", track="infant_catalog"),
                        _row("S1", "BL", "baseline", 0.99, "catalog_r2_supp_v1", route="R2")])
    keep, _ = bgs.resolve(det)
    assert keep.value_normalized.tolist() == ["BL"]
