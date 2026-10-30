"""Apply the adjudications to a merged stage-1 classification and write the final table (R2026.30).

Deterministic; no LLM calls. For every project in ADJ_DIR the adjudicated answer replaces the merged values and
status becomes 'adjudicated'. Projects whose two readings disagreed but were never adjudicated keep status
'disagree' (detail-only disagreements: in_scope agreed, a detail field did not). Projects where both readings
refused keep status 'refused' and in_scope None — they are listed, never silently dropped.

usage: python scripts/finalise_classify.py MERGED.parquet ADJ_DIR[,ADJ_DIR2,...] FINAL.parquet
"""
import glob, json, os, sys

import pandas as pd

FIELDS = {"in_scope": "in_scope", "out_reason": "out_reason", "nucleic_acid": "nucleic_acid",
          "study_shape": "study_shape"}
LIST_FIELDS = {"body_sites": "body_sites", "processing": "processing", "sample_types_present": "sample_types"}


def main(merged, adj_dirs, final):
    df = pd.read_parquet(merged)
    adj = {}
    for d in adj_dirs.split(","):
        for f in glob.glob(f"{d.strip()}/*.json"):
            p = os.path.basename(f)[:-5]
            if p == "ledger":
                continue
            a = (json.load(open(f)) or {}).get("adjudicated")
            if isinstance(a, dict) and a.get("in_scope"):
                adj[p] = a
    idx = df.set_index("study_accession")
    for p, a in adj.items():
        if p not in idx.index:
            continue
        for col, key in FIELDS.items():
            if a.get(key) is not None and col in idx.columns:
                idx.loc[p, col] = a[key]
        for col, key in LIST_FIELDS.items():
            v = a.get(key)
            if isinstance(v, list) and col in idx.columns:
                idx.loc[p, col] = ";".join(sorted(set(v)))
        if "confidence" in a and "confidence_min" in idx.columns:
            idx.loc[p, "confidence_min"] = a["confidence"]
        idx.loc[p, "status"] = "adjudicated"
        idx.loc[p, "needs_adjudication"] = False
    out = idx.reset_index()
    out.loc[out["out_reason"].astype(str).str.lower().isin(["none", "nan"]), "out_reason"] = None
    out.to_parquet(final, index=False)
    print(json.dumps({"projects": len(out), "adjudications_applied": sum(1 for p in adj if p in idx.index),
                      "status": out["status"].value_counts().to_dict(),
                      "in_scope": out["in_scope"].value_counts(dropna=False).to_dict()}))


if __name__ == "__main__":
    main(*sys.argv[1:4])
