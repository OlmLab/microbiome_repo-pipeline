#!/usr/bin/env python
"""build_dossiers.py — one evidence dossier per project (free, deterministic). Input to hand checks (gold set) and to the
model passes of the large update, so every reader sees the same evidence.

Per project: archive record (title, description, library fields, instruments, counts, dates), the read-check result per
checked run, a BioSample attribute profile (each key with its most common values and how many samples carry it), up to 8
example samples with all their attributes, the first 40 runs (library name, strategy, source, selection, bases), and the
candidate papers with link method and the local path of the downloaded full text and supplements.

usage: python scripts/build_dossiers.py --projects ids.txt --out DIR
Writes DIR/<accession>.json and DIR/<accession>.md (readable form).
"""
import argparse, glob, json, os
import pandas as pd

HOME = os.path.expanduser("~/catalog")
CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))
PIPE = os.path.join(HOME, "catalog-pipeline")


def load_inputs(ids):
    ids = set(ids)
    st = []
    for f in [f"{PIPE}/build/package/registry_studies.parquet", f"{CACHE}/s4_enum/registry_universe_studies.parquet"]:
        d = pd.read_parquet(f)
        st.append(d[d.study_accession.isin(ids)])
    studies = pd.concat(st).drop_duplicates("study_accession").set_index("study_accession")
    runs = pd.concat([pd.read_parquet(f, filters=[("study_accession", "in", list(ids))]) for f in
                      [f"{PIPE}/data/inputs/registry/registry_runs.parquet", f"{CACHE}/s4_enum/registry_runs.parquet"]]).drop_duplicates("run_accession")
    a1 = pd.read_parquet(f"{PIPE}/data/inputs/registry/registry_biosample_attributes.parquet", filters=[("study_accession", "in", list(ids))],
                         columns=["sample_acc", "study_accession", "attr_key", "attr_value"]).rename(columns={"sample_acc": "sample"})
    a2 = pd.read_parquet(f"{CACHE}/large_update/new_biosample_attributes.parquet", filters=[("study_accession", "in", list(ids))],
                         columns=["sample_key", "study_accession", "attr_key", "attr_value"]).rename(columns={"sample_key": "sample"})
    attrs = pd.concat([a1, a2]).drop_duplicates()
    rc = pd.concat([pd.read_csv(f) for f in glob.glob(f"{CACHE}/read_check/r16_*.csv") if os.path.getsize(f) > 0], ignore_index=True)
    rc = rc.drop_duplicates("run", keep="last").set_index("run")
    pap = [pd.read_parquet(f"{CACHE}/large_update/paper_candidates_new.parquet")]
    rp = pd.read_parquet(f"{PIPE}/build/package/registry_study_papers.parquet").rename(columns={"source": "methods"})
    pap.append(rp)
    papers = pd.concat(pap, ignore_index=True)
    papers = papers[papers.study_accession.isin(ids)]
    return studies, runs, attrs, rc, papers


def local_files(pmcid):
    if not isinstance(pmcid, str):
        return []
    hits = sorted(glob.glob(f"{CACHE}/pmc_oa/{pmcid}.*/*"))
    return [h for h in hits if not h.endswith((".jpg", ".gif", ".png", ".tif", ".jpeg"))]


def dossier(acc, studies, runs, attrs, rc, papers):
    s = studies.loc[acc] if acc in studies.index else pd.Series(dtype=object)
    keep = ["study_title", "description_short", "center_name", "first_public_min", "n_runs", "n_samples", "library_strategies",
            "library_sources", "library_selections", "instrument_platforms", "instrument_models_top", "scientific_names_top",
            "host_tax_ids", "host_human", "assay", "body_sites", "body_site_primary", "classification_stage", "site_terms"]
    rec = {k: (None if pd.isna(s.get(k)) else str(s.get(k))[:1500]) for k in keep if k in s.index}
    r = runs[runs.study_accession == acc]
    rr = r.head(40)
    run_rows = [{k: (None if pd.isna(v) else v) for k, v in row.items()} for row in
                rr[[c for c in ["run_accession", "sample_accession", "library_name", "library_strategy", "library_source",
                                "library_selection", "library_layout", "instrument_model", "base_count", "scientific_name"] if c in rr]].to_dict("records")]
    checked = [dict(run=x, **{k: (None if pd.isna(v) else v) for k, v in rc.loc[x].items()}) for x in r.run_accession if x in rc.index]
    a = attrs[attrs.study_accession == acc]
    nsamp = a["sample"].nunique()
    prof = []
    for k, g in a.groupby("attr_key"):
        vc = g.attr_value.astype(str).value_counts()
        prof.append({"key": k, "n_samples": int(g["sample"].nunique()), "n_distinct": int(len(vc)),
                     "top_values": {str(i)[:80]: int(v) for i, v in vc.head(6).items()}})
    prof.sort(key=lambda x: -x["n_samples"])
    ex = []
    for smp in list(dict.fromkeys(a["sample"]))[:8]:
        g = a[a["sample"] == smp]
        ex.append({"sample": smp, "attributes": {str(k)[:60]: str(v)[:120] for k, v in zip(g.attr_key, g.attr_value)}})
    p = papers[papers.study_accession == acc]
    pl = []
    for row in p.drop_duplicates("paper_id").to_dict("records"):
        pl.append({k: (None if (not isinstance(row.get(k), (list, dict)) and pd.isna(row.get(k))) else row.get(k))
                   for k in ["paper_id", "pmid", "pmcid", "doi", "title", "year", "journal", "methods", "is_open_access", "n_projects_for_paper", "candidate_rank"]}
                  | {"local_files": local_files(row.get("pmcid"))})
    return {"study_accession": acc, "archive": rec, "n_runs_listed": int(len(r)), "n_samples_with_attributes": int(nsamp),
            "read_check": checked, "attribute_profile": prof[:80], "example_samples": ex, "runs_first40": run_rows, "papers": pl}


def to_md(d):
    L = [f"# {d['study_accession']}", "", "## Archive record"]
    L += [f"- **{k}**: {v}" for k, v in d["archive"].items()]
    L += ["", f"Runs listed: {d['n_runs_listed']}; samples with attributes: {d['n_samples_with_attributes']}", "", "## Read check"]
    for c in d["read_check"]:
        L.append(f"- {c['run']}: {c.get('verdict')} (16S {c.get('frac16s')}, 23S {c.get('frac23s')}, top start {c.get('top1_share')}, len {c.get('len')})")
    L += ["", "## BioSample attribute profile (key: samples carrying it; top values)"]
    for p in d["attribute_profile"]:
        L.append(f"- {p['key']} ({p['n_samples']}; {p['n_distinct']} distinct): " + "; ".join(f"{k} ×{v}" for k, v in p["top_values"].items()))
    L += ["", "## Example samples"]
    for e in d["example_samples"]:
        L.append(f"- {e['sample']}: " + "; ".join(f"{k}={v}" for k, v in e["attributes"].items()))
    L += ["", "## Runs (first 40)"]
    for r in d["runs_first40"]:
        L.append("- " + ", ".join(f"{k}={v}" for k, v in r.items() if v is not None))
    L += ["", "## Candidate papers"]
    for p in d["papers"]:
        L.append(f"- {p.get('pmid')} / {p.get('pmcid')} / {p.get('doi')} ({p.get('year')}) [{p.get('methods')}; shared by {p.get('n_projects_for_paper')} projects] {p.get('title')}")
        for f in p["local_files"][:30]:
            L.append(f"    - file: {f}")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--projects", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    ids = [l.strip().split(",")[0] for l in open(a.projects) if l.strip() and not l.startswith("study_accession")]
    os.makedirs(a.out, exist_ok=True)
    S, R, A, RC, P = load_inputs(ids)
    for acc in ids:
        d = dossier(acc, S, R, A, RC, P)
        json.dump(d, open(os.path.join(a.out, f"{acc}.json"), "w"), default=str, indent=1)
        open(os.path.join(a.out, f"{acc}.md"), "w").write(to_md(d))
    print(f"{len(ids)} dossiers -> {a.out}")


if __name__ == "__main__":
    main()
