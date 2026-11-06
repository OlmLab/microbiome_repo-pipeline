"""plausibility.py — value gates and checked corrections applied to the determinations before the wide table (R2026.36).

Gates (deterministic, every release):
  age_negative      age_at_collection_days < 0
  age_over_122y     age_at_collection_days > 122 years (the oldest documented human; a 111-year-old in a centenarian cohort,
                    PRJNA542578, is real — values in 110-122 years are checked by reading, not cut)
Collection dates before 1980 are already refused by newfields_r1 (MIN_YEAR); dates 1980-1989 are possible (archived
specimens sequenced later) and are left to the checked corrections.

Corrections (data/inputs/gut/plausibility_corrections.csv; written from the Opus check of implausible values):
  study_accession, field, sample_keys ('all' or ';'-joined), action ('clear' | 'set'), new_value, rule_id, reason,
  evidence_source, evidence_quote
  'clear' removes that field's current determination rows for those samples (e.g. subject_id when it is a household code,
  so the samples stop being grouped into one person; collection_date when it is a birth date).
  'set' replaces the value of the listed samples' current row with new_value (e.g. a two-digit year typo 2002 -> 2022); the
  original row is kept in the rejected table and the new row names the correction in parse_note.

Removed rows are never lost: they go to catalog_rejected_values.parquet with rejection_rule / rejection_reason.
"""
from __future__ import annotations

import pandas as pd

AGE_MAX_DAYS = 122 * 365.25


def apply_value_gates(det: pd.DataFrame, corrections: pd.DataFrame | None = None):
    """Returns (kept determinations, rejected rows, report)."""
    cur = det.release_retired.isna() if "release_retired" in det.columns else pd.Series(True, index=det.index)
    rule = pd.Series(None, index=det.index, dtype=object)
    reason = pd.Series(None, index=det.index, dtype=object)
    age = det.field_name.eq("age_at_collection_days") & cur
    v = pd.to_numeric(det.value_normalized.where(age), errors="coerce")
    m = age & (v < 0)
    rule[m], reason[m] = "age_negative", "age at collection below 0 days"
    m = age & (v > AGE_MAX_DAYS) & rule.isna()
    rule[m], reason[m] = "age_over_122y", "age at collection above 122 years"
    report = {"age_negative": int((rule == "age_negative").sum()), "age_over_122y": int((rule == "age_over_122y").sum())}
    det = det.copy()
    set_rows = []
    if corrections is not None and len(corrections):
        n_corr = {}
        for r in corrections.itertuples(index=False):
            act = str(r.action)
            if act not in ("clear", "set"):
                raise ValueError(f"unknown correction action {act!r} ({r.study_accession} {r.field})")
            sel = cur & det.study_accession.eq(r.study_accession) & det.field_name.eq(r.field) & rule.isna()
            keys = str(r.sample_keys or "all").strip()
            if keys != "all":
                sel &= det.sample_key.isin(set(k.strip() for k in keys.split(";") if k.strip()))
            rule[sel], reason[sel] = str(r.rule_id), str(r.reason)
            if act == "set":
                nv = str(getattr(r, "new_value", "") or "")
                if not nv:
                    raise ValueError(f"set without new_value ({r.study_accession} {r.field})")
                new = det[sel].copy()
                new["field_value"] = nv; new["value_normalized"] = nv
                new["parse_note"] = (new.parse_note.fillna("").astype(str) + f" | corrected by checked reading ({r.rule_id}): {r.reason}").str.lstrip(" |")
                new["determined_by"] = new.determined_by.astype(str) + "+plausibility_correction"
                set_rows.append(new)
            k_ = f"{r.study_accession}:{r.field}:{act}"
            n_corr[k_] = n_corr.get(k_, 0) + int(sel.sum())
        report["corrections"] = n_corr
    rej = rule.notna()
    rejected = det[rej].assign(rejection_rule=rule[rej], rejection_reason=reason[rej])
    report["rejected_rows"] = int(rej.sum())
    kept = det[~rej]
    if set_rows:
        kept = pd.concat([kept] + set_rows, ignore_index=True)
        report["set_rows"] = int(sum(len(x) for x in set_rows))
    return kept.copy(), rejected, report
