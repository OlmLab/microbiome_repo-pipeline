"""Backfill sequencing depth for runs whose ENA read_run record has no base_count (0 / blank).

ENA reports base_count 0 for runs whose files it does not mirror — dbGaP / controlled-access deposits (e.g. TEDDY PRJNA400115) and
very recent submissions not yet mirrored. NCBI SRA runinfo carries `spots` / `bases` for those runs (metadata is public even when the
reads are protected). Output: one row per run accession with ncbi_spots, ncbi_bases, ncbi_consent, fetched_at; cached and appended, so
re-runs only query new accessions.

Usage: python scripts/fill_run_bases.py --runs build/package/gut_runs.parquet --out data/inputs/registry/run_bases_ncbi.parquet
"""
import argparse, io, os, re, sys, time
from datetime import date
import pandas as pd
import requests

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def fetch_batch(accs, session, tries=4):
    for k in range(tries):
        try:
            r = session.post(EUTILS + "esearch.fcgi", data={"db": "sra", "term": " OR ".join(f"{a}[Accession]" for a in accs), "retmax": 10000, "usehistory": "y"}, timeout=60)
            we = re.search("<WebEnv>(.*?)</WebEnv>", r.text); qk = re.search("<QueryKey>(.*?)</QueryKey>", r.text)
            if not we:
                raise RuntimeError("no WebEnv")
            f = session.get(EUTILS + "efetch.fcgi", params={"db": "sra", "query_key": qk.group(1), "WebEnv": we.group(1), "rettype": "runinfo", "retmode": "text"}, timeout=120)
            txt = f.text.strip()
            if not txt:
                return pd.DataFrame(columns=["Run", "spots", "bases", "Consent"])
            d = pd.read_csv(io.StringIO(txt), low_memory=False)
            d = d[d["Run"] != "Run"]   # runinfo repeats the header between record pages
            return d[["Run", "spots", "bases"] + (["Consent"] if "Consent" in d.columns else [])]
        except Exception as e:  # noqa: BLE001
            time.sleep(2 * (k + 1))
            err = e
    print(f"batch failed ({len(accs)} accs): {err}", file=sys.stderr)
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True); ap.add_argument("--out", required=True); ap.add_argument("--batch", type=int, default=200)
    a = ap.parse_args(argv)
    r = pd.read_parquet(a.runs, columns=["run_accession", "base_count"])
    bc = pd.to_numeric(r.base_count, errors="coerce").fillna(0)
    need = sorted(set(r.loc[bc <= 0, "run_accession"].dropna().astype(str)))
    have = pd.read_parquet(a.out) if os.path.exists(a.out) else pd.DataFrame(columns=["run_accession", "ncbi_spots", "ncbi_bases", "ncbi_consent", "fetched_at"])
    todo = [x for x in need if x not in set(have.run_accession)]
    print(f"runs without ENA bases: {len(need)}; cached {len(need) - len(todo)}; to fetch {len(todo)}", file=sys.stderr)
    s = requests.Session(); rows = []
    for i in range(0, len(todo), a.batch):
        d = fetch_batch(todo[i:i + a.batch], s)
        if d is not None:
            for x in d.itertuples(index=False):
                rows.append(dict(run_accession=str(x.Run), ncbi_spots=pd.to_numeric(x.spots, errors="coerce"), ncbi_bases=pd.to_numeric(x.bases, errors="coerce"),
                                 ncbi_consent=getattr(x, "Consent", None), fetched_at=str(date.today())))
        if (i // a.batch) % 25 == 0:
            print(f"  {i + a.batch}/{len(todo)}", file=sys.stderr)
        time.sleep(0.4)
    out = pd.concat([have, pd.DataFrame(rows)], ignore_index=True).drop_duplicates("run_accession", keep="last")
    out = out[out.run_accession.isin(set(r.run_accession.astype(str))) | out.run_accession.isin(set(have.run_accession))]
    out.to_parquet(a.out, index=False)
    got = out[out.run_accession.isin(need) & (pd.to_numeric(out.ncbi_bases, errors="coerce").fillna(0) > 0)]
    print(f"filled {len(got)} of {len(need)} runs", file=sys.stderr)


if __name__ == "__main__":
    main()
