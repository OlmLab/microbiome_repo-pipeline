#!/usr/bin/env python
"""join_ladder_audit.py — how many 'tables read, 0 joined' projects become joinable with normalised sample keys (R2026.28).
For each project: archive keys from ENA (run, sample, secondary sample, sample alias, sample title, library name, experiment alias),
every table in the cached supplements of its papers; per column, the share of catalog samples matched at each ladder rung:
  exact -> casefold+strip punctuation -> stem (text before '.', lane/read suffixes _S12_L001/_R1 removed) -> trailing number.
Writes one row per (project, table, column) with the best rung; no model calls.
usage: python scripts/join_ladder_audit.py --projects ids.txt --out audit.csv
"""
import argparse, glob, io, os, re, sys, zipfile, csv, warnings
import pandas as pd, requests
warnings.filterwarnings("ignore")
CACHE = os.path.expanduser("~/catalog/cache")
def n1(s): return re.sub(r"[^0-9a-z]", "", str(s).lower())
def n2(s):
    s = str(s).strip(); s = re.split(r"\.", s)[0]
    s = re.sub(r"(?i)([_-]S\d+)?([_-]L\d{3})?([_-]R[12])?([_-]00\d)?(\.f(ast)?q(\.gz)?)?$", "", s)
    return n1(s)
def n3(s):
    m = re.findall(r"\d+", str(s)); return m[-1].lstrip("0") if m else ""
RUNGS = [("exact", lambda s: str(s).strip()), ("casefold", n1), ("stem", n2), ("number", n3)]

# table readers (xlsx / csv / docx / pdf) are shared with extract_supp_rows so the audit and the extraction
# always see exactly the same tables
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_supp_rows import tables_of, read_any  # noqa: E402


def ena_keys(acc):
    r = requests.get("https://www.ebi.ac.uk/ena/portal/api/filereport", params=dict(accession=acc, result="read_run", format="tsv", limit=0,
                     fields="run_accession,sample_accession,secondary_sample_accession,sample_alias,sample_title,library_name,experiment_alias"), timeout=120)
    return pd.read_csv(io.StringIO(r.text), sep="\t", dtype=str)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--projects", required=True); ap.add_argument("--out", required=True); ap.add_argument("--pmcids", required=True)
    a = ap.parse_args()
    pm = pd.read_csv(a.pmcids, dtype=str).set_index("study_accession").pmcids.to_dict()
    proj = [l.strip() for l in open(a.projects) if l.strip()]
    done = set(pd.read_csv(a.out, dtype=str).project) if os.path.exists(a.out) else set()
    fh = open(a.out, "a", newline=""); w = csv.writer(fh)
    if not done: w.writerow(["project", "table", "column", "key_type", "rung", "matched_samples", "n_samples", "share"])
    for i, acc in enumerate(proj):
        if acc in done: continue
        try: e = ena_keys(acc)
        except Exception: w.writerow([acc, "", "", "", "ena_error", 0, 0, 0]); continue
        ns = e.sample_accession.nunique() or 1
        best_any = False
        for pmc in str(pm.get(acc, "")).split(";"):
            if not pmc: continue
            for tname, df in tables_of(pmc):
                for ci in range(min(df.shape[1], 60)):
                    col = df.iloc[:, ci].dropna().astype(str)
                    if len(col) < 5: continue
                    hdr = str(col.iloc[0])[:40]
                    best = None
                    for rname, f in RUNGS:
                        vals = {f(v) for v in col if f(v)}
                        if not vals: continue
                        for kt in ["run_accession", "sample_accession", "secondary_sample_accession", "sample_alias", "sample_title", "library_name", "experiment_alias"]:
                            if kt not in e or (rname == "number" and kt.endswith("accession")): continue
                            k = pd.DataFrame({"key": e[kt], "s": e["sample_accession"]}).dropna()
                            if rname == "number" and k.drop_duplicates("s").key.map(f).duplicated().any(): continue   # numbers must be unique per sample
                            hit = int(k[k.key.map(f).isin(vals)].s.nunique())
                            if hit and (best is None or hit > best[2]): best = (kt, rname, hit)
                        if best is not None and best[2] / ns >= 0.5: break
                    if best is not None and best[2] / ns >= 0.2:
                        best_any = True
                        w.writerow([acc, f"{pmc}/{tname}", hdr, best[0], best[1], best[2], ns, round(best[2] / ns, 3)])
        if not best_any: w.writerow([acc, "", "", "", "none", 0, ns, 0])
        fh.flush()
        if i % 25 == 0: print(i, acc, flush=True)
    print("done", flush=True)

if __name__ == "__main__": main()
