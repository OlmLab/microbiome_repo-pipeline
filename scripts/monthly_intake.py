#!/usr/bin/env python
"""monthly_intake.py — steps 1-4 of the monthly run in one place (R2026.34).

  select  --delta build/registry_delta_<date> --tag <tag>
          new studies of the registry delta that are neither in the catalog, nor earlier candidates, nor classified;
          writes $L/<tag>_new_projects.txt (all) and $L/<tag>_keep_projects.txt (human signal + ambiguous)
  fold    --delta ... --tag <tag>
          appends the kept studies' universe rows, runs, study meta and BioSample index rows to the s4_enum caches
          (the stage scripts read projects from there) and writes $L/<tag>_runs.txt
  prep    --tag <tag> --date <yyyy-mm-dd>
          read check of the runs, candidate papers, dossiers and classification batches (no model calls)
  samples --tag <tag>
          after classification (classify_all.parquet): the in-scope new projects' samples -> a sample stage
          $L/stage16_delta_samples_<tag>/ (samples_todo.csv, harvested attrs/, attrs_fielded.parquet, pairs_to_map.parquet);
          then map the new pairs (map_attribute_pairs.py -> pairs_out.parquet), run
          assemble_rna_specimens.py $L stage16_delta_samples_<tag> and derive_subject_timepoint.py; prepare_catalog_inputs.py
          reads every stage16_delta_samples* directory

L = ~/catalog/cache/large_update, caches under $CATALOG_CACHE_DIR (default ~/catalog/cache).
"""
import argparse, glob, json, os, re, subprocess, sys
import pandas as pd

CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))
L = os.path.join(CACHE, "large_update")
S4 = os.path.join(CACHE, "s4_enum")
TABLES = ["registry_universe_studies", "registry_runs", "registry_study_meta", "registry_biosample_index"]
KEYS = {"registry_universe_studies": "study_accession", "registry_runs": "run_accession",
        "registry_study_meta": "study_accession", "registry_biosample_index": None}
PATH = re.compile(r"(?i)pathogen|infect|sepsis|septic|bacter(a)?emia|virus|viral|viro(me|mic)|mngs|clinical metagenom|diagnos|"
                  r"tubercul|\bhiv\b|covid|sars|influenza|pneumonia|meningitis|encephalitis|fever|malaria|hepatitis|\bhpv\b|"
                  r"antimicrobial resist|outbreak|cholera|typhoid|shigell|salmonell|clostridi|difficile|candid|fung|parasit|helminth")


def _ids(p):
    return [x.strip() for x in open(p) if x.strip()] if os.path.exists(p) else []


def _delta(d, t):
    parts = [pd.read_parquet(f) for f in (os.path.join(d, k, f"{t}.parquet") for k in ("dna", "rna")) if os.path.exists(f)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def select(a):
    prev = set()
    for f in os.listdir(L):
        if f == "new_projects.txt" or f.endswith("_new_projects.txt"):
            if f != f"{a.tag}_new_projects.txt":
                prev |= set(_ids(os.path.join(L, f)))
    cat = set(pd.read_parquet("build/package/catalog_studies.parquet", columns=["study_accession"]).study_accession)
    ca = os.path.join(L, "stage1", "classify_all.parquet")
    clf = set(pd.read_parquet(ca, columns=["study_accession"]).study_accession) if os.path.exists(ca) else set()
    u = _delta(a.delta, "registry_universe_studies").drop_duplicates("study_accession")
    g = u[~u.study_accession.isin(prev | cat | clf)]
    keep = g[g.candidate_class.isin(["signal_human_new", "ambiguous_new"])]
    g.study_accession.to_csv(os.path.join(L, f"{a.tag}_new_projects.txt"), index=False, header=False)
    keep.study_accession.to_csv(os.path.join(L, f"{a.tag}_keep_projects.txt"), index=False, header=False)
    print(json.dumps({"delta_studies": len(u), "earlier_candidates": len(set(u.study_accession) & prev), "in_catalog": len(set(u.study_accession) & cat),
                      "classified": len(set(u.study_accession) & clf), "new": len(g), "new_by_class": g.candidate_class.value_counts().to_dict(),
                      "keep": len(keep)}))


def fold(a):
    kids = set(_ids(os.path.join(L, f"{a.tag}_keep_projects.txt")))
    out = {}
    for t in TABLES:
        d = _delta(a.delta, t)
        if not len(d):
            continue
        old = pd.read_parquet(os.path.join(S4, f"{t}.parquet"))
        if "study_accession" in d.columns:
            d = d[d.study_accession.isin(kids)]
        elif t == "registry_biosample_index":
            runs = _delta(a.delta, "registry_runs")
            sams = set(runs[runs.study_accession.isin(kids)].sample_accession)
            col = [c for c in d.columns if "sample" in c][0]
            d = d[d[col].isin(sams)]
        d = d[[c for c in old.columns if c in d.columns]]
        k = KEYS[t]
        if k:
            new = pd.concat([old[~old[k].isin(set(d[k]))], d], ignore_index=True)
        else:
            new = pd.concat([old, d], ignore_index=True).drop_duplicates()
        new.to_parquet(os.path.join(S4, f"{t}.parquet"), index=False)
        out[t] = {"added": len(d), "now": len(new)}
    runs = _delta(a.delta, "registry_runs")
    runs = runs[runs.study_accession.isin(kids)].drop_duplicates("run_accession")
    runs.run_accession.to_csv(os.path.join(L, f"{a.tag}_runs.txt"), index=False, header=False)
    out["runs_txt"] = len(runs)
    print(json.dumps(out))


def prep(a):
    env = dict(os.environ, CATALOG_CACHE_DIR=CACHE)
    keep = os.path.join(L, f"{a.tag}_keep_projects.txt")
    rc = os.path.join(CACHE, "read_check", f"r16_{a.tag}_{a.date}.csv")
    pc = os.path.join(L, f"{a.tag}_paper_candidates.parquet")
    steps = [[sys.executable, "scripts/read_check16s.py", "--runs", os.path.join(L, f"{a.tag}_runs.txt"), "--out", rc, "--workers", "6"],
             [sys.executable, "scripts/find_papers.py", "--projects", keep, "--meta", os.path.join(S4, "registry_universe_studies.parquet"),
              "--out", pc, "--workers", "6"]]
    for s in steps:
        if (s[1].endswith("read_check16s.py") and os.path.exists(rc)) or (s[1].endswith("find_papers.py") and os.path.exists(pc)):
            continue
        r = subprocess.run(s, env=env, capture_output=True, text=True)
        print(os.path.basename(s[1]), "exit", r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1:] if (r.stdout + r.stderr).strip() else "")
    pa = pd.read_parquet(os.path.join(L, "paper_candidates_new.parquet")); b = pd.read_parquet(pc)
    b = b[[c for c in b.columns if c in pa.columns]]
    pd.concat([pa, b], ignore_index=True).drop_duplicates(["study_accession", "paper_id"]).to_parquet(os.path.join(L, "paper_candidates_new.parquet"), index=False)
    dd = os.path.join(L, f"dossiers_{a.tag}")
    r = subprocess.run([sys.executable, "scripts/build_dossiers.py", "--projects", keep, "--out", dd], env=env, capture_output=True, text=True)
    print("build_dossiers exit", r.returncode)
    ids = _ids(keep)
    pa_, no_ = [], []
    for p in ids:
        f = os.path.join(dd, f"{p}.json")
        if not os.path.exists(f):
            continue
        d = json.load(open(f)); ar = d.get("archive", {})
        txt = " ".join(str(ar.get(k) or "") for k in ("study_title", "description_short", "study_description")) + " " + \
            " ".join(str(x.get("title") or "") for x in d.get("papers", [])[:4])
        (pa_ if PATH.search(txt) else no_).append(p)
    bd = os.path.join(L, "stage1", f"{a.tag}_batches"); os.makedirs(bd, exist_ok=True)
    bat = [("p", pa_[i:i + 10]) for i in range(0, len(pa_), 10)] + [("g", no_[i:i + 25]) for i in range(0, len(no_), 25)]
    for i, (k, b_) in enumerate(bat):
        open(os.path.join(bd, f"{a.tag}_b{i:03d}{k}.txt"), "w").write("\n".join(b_) + "\n")
    print(json.dumps({"dossiers": len(pa_) + len(no_), "pathogen_flagged": len(pa_), "batches": len(bat), "batch_dir": bd}))


def samples(a):
    env = dict(os.environ, CATALOG_CACHE_DIR=CACHE)
    keep = set(_ids(os.path.join(L, f"{a.tag}_keep_projects.txt")))
    c = pd.read_parquet(os.path.join(L, "stage1", "classify_all.parquet"))
    ins = sorted(set(c[c.in_scope.isin(["yes", "partly"])].study_accession) & keep)
    S = os.path.join(L, f"stage16_delta_samples_{a.tag}")
    os.makedirs(S, exist_ok=True)
    r = pd.read_parquet(os.path.join(S4, "registry_runs.parquet"), columns=["study_accession", "sample_accession"])
    todo = r[r.study_accession.isin(ins)].drop_duplicates("sample_accession")[["sample_accession", "study_accession"]]
    todo.to_csv(os.path.join(S, "samples_todo.csv"), index=False)
    h = subprocess.run([sys.executable, "scripts/harvest_biosamples.py", "--samples", os.path.join(S, "samples_todo.csv"), "--out", os.path.join(S, "attrs"),
                        "--chunk", "100", "--workers", "2"], env=env, capture_output=True, text=True)
    print("harvest exit", h.returncode, (h.stdout.strip().splitlines() or [""])[-1])
    fs = glob.glob(os.path.join(S, "attrs", "*.parquet"))
    att = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()
    reg = pd.read_parquet("data/inputs/registry/registry_biosample_attributes.parquet", columns=["attr_key_norm", "attr_value", "field", "is_placeholder"])
    km = reg.dropna(subset=["field"]).groupby("attr_key_norm").field.agg(lambda x: x.value_counts().index[0])
    ph = set(reg[reg.is_placeholder.astype(bool)].attr_value.astype(str).str.strip().str.lower())
    att["field"] = att.attr_key_norm.map(km)
    att["is_placeholder"] = att.attr_value.astype(str).str.strip().str.lower().isin(ph)
    att.to_parquet(os.path.join(S, "attrs_fielded.parquet"), index=False)
    att.drop(columns=["field", "is_placeholder"]).to_parquet(os.path.join(S, "attrs_plain.parquet"), index=False)
    f = att[att.field.notna() & ~att.is_placeholder]
    known = [pd.read_parquet("data/inputs/registry/registry_pairs_normalised.parquet", columns=["field", "attr_key_norm", "attr_value"])]
    for g in [os.path.join(L, "stage5", "pairs_normalised_combined.parquet")] + glob.glob(os.path.join(L, "stage1[5-9]*", "pairs_out.parquet")):
        if os.path.exists(g) and not g.startswith(S):
            known.append(pd.read_parquet(g, columns=["field", "attr_key_norm", "attr_value"]))
    kn = pd.concat(known, ignore_index=True).drop_duplicates()
    pairs = f[["field", "attr_key_norm", "attr_value"]].drop_duplicates().merge(kn, how="left", indicator=True)
    new = pairs[pairs._merge == "left_only"].drop(columns="_merge")
    new.to_parquet(os.path.join(S, "pairs_to_map.parquet"), index=False)
    print(json.dumps({"projects": len(ins), "samples": len(todo), "samples_with_attributes": int(att.sample_key.nunique()) if len(att) else 0,
                      "attribute_rows": len(att), "fielded_rows": len(f), "pairs": int(len(pairs)), "pairs_to_map": len(new), "stage": S}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["select", "fold", "prep", "samples"])
    ap.add_argument("--delta"); ap.add_argument("--tag", required=True); ap.add_argument("--date")
    a = ap.parse_args()
    {"select": select, "fold": fold, "prep": prep, "samples": samples}[a.cmd](a)
