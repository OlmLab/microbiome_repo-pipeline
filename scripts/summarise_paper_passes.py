"""summarise_paper_passes.py — turn the per-project JSON of scripts/pilot_passes.py (link_audit / fulltext) into the
three tables the scope build reads: link_audit.parquet, fulltext_summary.parquet and exposure_items.parquet.

Deterministic; no model calls. Appending is safe: pass one or more output directories and, with --merge-into, the
existing tables; a project present in both keeps the NEW reading (the later pass), and nothing else is touched.

usage: python scripts/summarise_paper_passes.py --out-dirs DIR [DIR ...] --out-prefix PREFIX
                                                [--merge-into LINK.parquet FULLTEXT.parquet EXPOSURE.parquet]
"""
import argparse, glob, json, os

import pandas as pd


def _rows(out_dirs):
    links, facts, items = [], [], []
    for d in out_dirs:
        for f in sorted(glob.glob(f"{d}/*.json")) + sorted(glob.glob(f"{d}/*/*.json")):
            b = os.path.basename(f)
            if b.startswith(("stats_", "ledger")):
                continue
            acc = b[:-5]
            try:
                j = json.load(open(f))
            except ValueError:
                continue
            la = j.get("link_audit") if isinstance(j.get("link_audit"), dict) else {}
            for pr in (la.get("papers") or []):
                if isinstance(pr, dict) and pr.get("paper_id") and pr.get("relation"):
                    links.append({"study_accession": acc, "relation": pr["relation"],
                                  "paper_id": str(pr["paper_id"]), "n": 1})
            ft = j.get("fulltext") or {}
            if not isinstance(ft, dict) or ft.get("status"):
                continue
            _d = lambda v: v if isinstance(v, dict) else {}   # a schema-violating sub-object counts as missing
            seq, rna, co, st = _d(ft.get("sequencing")), _d(ft.get("rna_methods")), _d(ft.get("cohort")), _d(ft.get("setting"))
            facts.append({
                "study_accession": acc,
                "nucleic_acids": ft.get("nucleic_acids_sequenced"),
                "metadata_access": ft.get("metadata_access"),
                "platform": seq.get("platform"),
                "extraction_kit": seq.get("extraction_kit") or rna.get("extraction_kit"),
                "library_kit": seq.get("library_kit") or rna.get("library_kit"),
                "n_participants": co.get("n_participants"),
                "study_shape": co.get("study_shape"),
                "rna_methods": any(v for k, v in rna.items() if k != "target"),
                "recruitment": (st.get("recruitment_setting") or [None])[0]
                if isinstance(st.get("recruitment_setting"), list) else st.get("recruitment_setting"),
                "settlement": (st.get("settlement_type") or [None])[0]
                if isinstance(st.get("settlement_type"), list) else st.get("settlement_type"),
                "n_places": len(st.get("places") or []),
                "lifestyle": st.get("lifestyle"),
            })
            for ev in (st.get("exposure_variables") or []):
                if isinstance(ev, dict) and ev.get("concept"):
                    items.append({"study_accession": acc, "concept": ev["concept"], "scale": ev.get("scale"),
                                  "summary": ev.get("summary"), "quote": ev.get("quote")})
    return (pd.DataFrame(links), pd.DataFrame(facts), pd.DataFrame(items))


def _merge(new, old_path, key="study_accession"):
    if not old_path or not os.path.exists(old_path) or new.empty:
        return new if not new.empty else (pd.read_parquet(old_path) if old_path and os.path.exists(old_path) else new)
    old = pd.read_parquet(old_path)
    old = old[~old[key].isin(set(new[key]))]
    return pd.concat([old, new.reindex(columns=[c for c in old.columns] +
                                       [c for c in new.columns if c not in old.columns])], ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dirs", nargs="+", required=True)
    ap.add_argument("--out-prefix", required=True)
    ap.add_argument("--merge-into", nargs=3, default=None,
                    help="existing link_audit / fulltext_summary / exposure_items parquets to append to")
    a = ap.parse_args()
    links, facts, items = _rows(a.out_dirs)
    old = a.merge_into or [None, None, None]
    links = _merge(links.groupby(["study_accession", "relation", "paper_id"], as_index=False)["n"].sum()
                   if not links.empty else links, old[0])
    facts = _merge(facts, old[1])
    items = _merge(items, old[2])
    os.makedirs(os.path.dirname(a.out_prefix) or ".", exist_ok=True)
    links.to_parquet(f"{a.out_prefix}link_audit.parquet", index=False)
    facts.to_parquet(f"{a.out_prefix}fulltext_summary.parquet", index=False)
    items.to_parquet(f"{a.out_prefix}exposure_items.parquet", index=False)
    print(json.dumps({"link_rows": len(links), "projects_with_links": int(links["study_accession"].nunique()) if len(links) else 0,
                      "fulltext_projects": len(facts), "exposure_items": len(items),
                      "generating_papers": int((links["relation"] == "generated").sum()) if len(links) else 0}))


if __name__ == "__main__":
    main()
