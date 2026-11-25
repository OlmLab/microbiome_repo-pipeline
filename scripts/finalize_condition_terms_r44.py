#!/usr/bin/env python
"""R2026.44: config/condition_terms.csv from the Conditions worker's table (docs/audits/r44/condition_terms_r44.csv).

One change is made to the worker's table: the MONDO id of a condition is the one the Conditions hand-over publishes for that
condition (docs/audits/r44/condition_explorer_handover/condition_explorer.csv). The hand-over leaves the MONDO id empty where
the MONDO term is only a broader match of the EFO term (its `mondo_match` flag); the worker's table could not see that flag
and carried the broader id. Conditions that the hand-over table does not list keep the worker's value.
The result is what build_gut_scope --condition-terms and scripts/build_condition_explorer.py read.
"""
import pandas as pd

SRC = "docs/audits/r44/condition_terms_r44.csv"
EXPLORER = "docs/audits/r44/condition_explorer_handover/condition_explorer.csv"
OUT = "config/condition_terms.csv"


def main():
    t = pd.read_csv(SRC, dtype=str, keep_default_na=False)
    e = pd.read_csv(EXPLORER, dtype=str, keep_default_na=False)
    pub = dict(zip(e.condition_id, e.mondo_id))
    listed = t.condition_id.isin(pub) & (t.condition_id != "")
    new = t.condition_id.map(pub).where(listed, t.mondo_id)
    changed = int((new != t.mondo_id).sum())
    blanked = int(((new == "") & (t.mondo_id != "")).sum())
    t["mondo_id"] = new
    t.to_csv(OUT, index=False)
    print({"rows": len(t), "conditions": int(t.condition_id[t.condition_id != ""].nunique()), "rows_listed_in_handover": int(listed.sum()),
           "mondo_changed_rows": changed, "mondo_blanked_rows": blanked, "conditions_with_mondo": int(t[t.mondo_id != ""].condition_id.nunique())})


if __name__ == "__main__":
    main()
