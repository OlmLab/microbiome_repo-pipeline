#!/usr/bin/env python
"""R2026.44: config/condition_terms.csv from the Conditions worker's table (docs/audits/r44/condition_terms_r44.csv).

One change is made to the worker's table: the MONDO id of a condition is the one the Conditions hand-over publishes for that
condition (docs/audits/r44/condition_explorer_handover/condition_explorer.csv). The hand-over leaves the MONDO id empty where
the MONDO term is only a broader match of the EFO term (its `mondo_match` flag); the worker's table could not see that flag
and carried the broader id. Conditions that the hand-over table does not list keep the worker's value.
The result is what build_gut_scope --condition-terms and scripts/build_condition_explorer.py read.
"""
import os

import pandas as pd

SRC = "docs/audits/r44/condition_terms_r44.csv"
EXPLORER = "docs/audits/r44/condition_explorer_handover/condition_explorer.csv"
OUT = "config/condition_terms.csv"
ADDED = "docs/audits/r44/condition_terms_added_r44.csv"


def main():
    t = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    e = pd.read_csv(EXPLORER, dtype=str, keep_default_na=False)
    pub = dict(zip(e.condition_id, e.mondo_id))
    listed = t.condition_id.isin(pub) & (t.condition_id != "")
    new = t.condition_id.map(pub).where(listed, t.mondo_id)
    changed = int((new != t.mondo_id).sum())
    blanked = int(((new == "") & (t.mondo_id != "")).sum())
    t["mondo_id"] = new
    # one EFO id per condition: a string whose row lacks the id that the other strings of the same condition carry takes it
    efo = t[(t.condition_id != "") & (t.efo_id != "")].groupby("condition_id").efo_id.agg(lambda v: v.value_counts().index[0])
    fill = (t.condition_id != "") & (t.efo_id == "") & t.condition_id.isin(efo.index)
    t.loc[fill, "efo_id"] = t.loc[fill, "condition_id"].map(efo)
    # a label that is not a case or a control with a condition (a cohort description, a trial arm, a healthy control) names no
    # condition in the sample table, whatever trait its text mentions
    off = ~t.role.isin(["case", "other_control"]) & ((t.condition_name != "") | (t.efo_id != "") | (t.mondo_id != "") | (t.condition_id != ""))
    for c in ("condition_name", "efo_id", "mondo_id", "condition_id", "efo_legacy_ids"):
        t.loc[off, c] = ""
    # rows added after the worker's table, same columns (a pair that a later fix created; copied from a row of the same condition)
    if os.path.exists(ADDED):
        x = pd.read_csv(ADDED, dtype=str, keep_default_na=False)[list(t.columns)]
        k = ["study_accession", "health_condition", "health_condition_detail"]
        t = pd.concat([t[~t.set_index(k).index.isin(x.set_index(k).index)], x], ignore_index=True)
    t.to_csv(OUT, index=False)
    print({"rows": len(t), "conditions": int(t.condition_id[t.condition_id != ""].nunique()), "rows_listed_in_handover": int(listed.sum()),
           "mondo_changed_rows": changed, "mondo_blanked_rows": blanked, "efo_filled_rows": int(fill.sum()), "rows_not_case_unnamed": int(off.sum()), "conditions_with_mondo": int(t[t.mondo_id != ""].condition_id.nunique())})


if __name__ == "__main__":
    main()
