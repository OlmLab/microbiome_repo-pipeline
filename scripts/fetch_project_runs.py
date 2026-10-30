"""fetch_project_runs.py — every sequencing run of a list of projects from the ENA portal (all library sources, so RNA
libraries the DNA-era registry enumeration never listed are included). Resumable: one parquet per project in OUT_DIR,
skipped when present; a combined parquet is written at the end. No model calls.

usage: python scripts/fetch_project_runs.py --projects ids.txt --out-dir DIR --combined OUT.parquet [--workers 8]
"""
import argparse, concurrent.futures as cf, io, json, os, time

import pandas as pd
import requests

FIELDS = ("run_accession,study_accession,sample_accession,secondary_sample_accession,experiment_accession,"
          "library_strategy,library_source,library_selection,library_layout,library_name,instrument_platform,"
          "instrument_model,read_count,base_count,first_public")


def one(acc, out_dir):
    p = os.path.join(out_dir, f"{acc}.parquet")
    if os.path.exists(p):
        return "cached"
    for k in range(4):
        try:
            r = requests.get("https://www.ebi.ac.uk/ena/portal/api/filereport",
                             params=dict(accession=acc, result="read_run", fields=FIELDS, format="tsv", limit=0),
                             timeout=180)
            if r.status_code == 200:
                df = pd.read_csv(io.StringIO(r.text), sep="\t", dtype=str) if r.text.strip() else pd.DataFrame(columns=FIELDS.split(","))
                df.to_parquet(p, index=False)
                return "ok"
            if r.status_code in (204, 404):
                pd.DataFrame(columns=FIELDS.split(",")).to_parquet(p, index=False)
                return "empty"
        except Exception:
            pass
        time.sleep(2 * (k + 1))
    return "error"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", required=True); ap.add_argument("--out-dir", required=True)
    ap.add_argument("--combined", required=True); ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    ids = [l.strip() for l in open(a.projects) if l.strip()]
    st = {}
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for i, s in enumerate(ex.map(lambda x: one(x, a.out_dir), ids)):
            st[s] = st.get(s, 0) + 1
            if i % 200 == 0:
                print(i, st, flush=True)
    parts = [pd.read_parquet(os.path.join(a.out_dir, f"{x}.parquet")) for x in ids
             if os.path.exists(os.path.join(a.out_dir, f"{x}.parquet"))]
    comb = pd.concat([p for p in parts if len(p)], ignore_index=True).drop_duplicates("run_accession")
    comb.to_parquet(a.combined, index=False)
    print(json.dumps({"projects": len(ids), "status": st, "runs": len(comb)}), flush=True)


if __name__ == "__main__":
    main()
