"""Merge the two classification readings per project (stage 1 of R2026.30). Deterministic; prints counts only.

Agreement rules:
  in_scope      readings equal -> that value; else 'needs_adjudication'
  nucleic_acid  readings equal -> that value; dna vs both / rna vs both -> 'both' only after adjudication; else adjudicate
  body_sites    union of both readings (sites are per sample later; the project list is a filter hint)
  processing / sample_types_present  union
  study_shape   equal -> value; else 'suggested:<r1>|<r2>' (adjudicated later from the full text)
A project with one reading (the other refused) keeps it but is marked single_reading and goes to adjudication when it says in scope.
A project with no reading (both refused) is 'refused' and keeps archive-only metadata.

usage: python scripts/merge_classify.py OUT_DIR(stage1/out) MERGED.parquet ADJ_IDS.txt
"""
import glob, json, os, sys
import pandas as pd


def reading(d, k):
    v = d.get(k)
    return v if isinstance(v, dict) and "status" not in v and v.get("in_scope") else None


def main(out_dir, merged, adj_ids):
    rows = []
    for f in sorted(glob.glob(f"{out_dir}/*/*.json")):
        if os.path.basename(f).startswith("stats_"):
            continue
        p = os.path.basename(f)[:-5]
        d = json.load(open(f))
        a, b = reading(d, "classify_1"), reading(d, "classify_2")
        r = {"study_accession": p, "batch": f.split("/")[-2], "n_readings": (a is not None) + (b is not None)}
        if a is None and b is None:
            r.update(status="refused", in_scope=None, needs_adjudication=False)
            rows.append(r); continue
        x, y = (a, b) if a and b else (a or b, None)
        r["in_scope_1"], r["in_scope_2"] = x.get("in_scope"), (y or {}).get("in_scope")
        r["na_1"], r["na_2"] = x.get("nucleic_acid"), (y or {}).get("nucleic_acid")
        r["out_reason"] = x.get("out_reason") if not y or x.get("out_reason") == y.get("out_reason") else f"{x.get('out_reason')}|{y.get('out_reason')}"
        r["body_sites"] = ";".join(sorted(set(x.get("body_sites") or []) | set((y or {}).get("body_sites") or [])))
        r["processing"] = ";".join(sorted(set(x.get("processing") or []) | set((y or {}).get("processing") or [])))
        r["sample_types"] = ";".join(sorted(set(x.get("sample_types_present") or []) | set((y or {}).get("sample_types_present") or [])))
        s1, s2 = x.get("study_shape"), (y or {}).get("study_shape")
        r["study_shape"] = s1 if not y or s1 == s2 else f"suggested:{s1}|{s2}"
        r["confidence_min"] = min(v.get("confidence") or 0 for v in (x, y) if v)
        if y is None:
            r["status"] = "single_reading"
            r["in_scope"] = r["in_scope_1"]; r["nucleic_acid"] = r["na_1"]
            r["needs_adjudication"] = r["in_scope_1"] in ("yes", "partly")
        else:
            agree_scope = r["in_scope_1"] == r["in_scope_2"]
            agree_na = r["na_1"] == r["na_2"]
            r["in_scope"] = r["in_scope_1"] if agree_scope else None
            r["nucleic_acid"] = r["na_1"] if agree_na else None
            r["status"] = "agreed" if agree_scope and agree_na else "disagree"
            # nucleic acid only matters for in-scope projects
            r["needs_adjudication"] = (not agree_scope) or (not agree_na and "no" not in (r["in_scope_1"], r["in_scope_2"]))
        rows.append(r)
    df = pd.DataFrame(rows)
    df.to_parquet(merged, index=False)
    adj = df[df.needs_adjudication.fillna(False)].study_accession.tolist()
    open(adj_ids, "w").write("\n".join(adj) + ("\n" if adj else ""))
    ag = df[df.status == "agreed"]
    print(json.dumps({
        "projects": len(df), "status": df.status.value_counts().to_dict(),
        "agreed_in_scope": ag.in_scope.value_counts().to_dict(),
        "agreed_nucleic_acid_in_scope": ag[ag.in_scope.isin(["yes", "partly"])].nucleic_acid.value_counts().to_dict(),
        "needs_adjudication": len(adj),
        "disagree_scope": int((df.status == "disagree").sum() and (df[df.status == "disagree"].in_scope_1 != df[df.status == "disagree"].in_scope_2).sum()),
        "agreed_out_reasons": ag[ag.in_scope == "no"].out_reason.value_counts().head(12).to_dict(),
    }))


if __name__ == "__main__":
    main(*sys.argv[1:4])
