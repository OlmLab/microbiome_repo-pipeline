#!/usr/bin/env python
"""read_scan.py — resumable batch of read_peek checks (R2026.28).
Picks up to K runs per project (distinct samples, deterministic order), skips runs already in --out, appends one CSV row per run.
usage: python scripts/read_scan.py --projects ids.txt --out results.csv [--k 3] [--workers 8] [--catalog-only]
"""
import argparse, csv, os, sys, concurrent.futures as cf, hashlib
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from read_peek import peek

ap = argparse.ArgumentParser(); ap.add_argument('--projects', required=True); ap.add_argument('--out', required=True)
ap.add_argument('--k', type=int, default=3); ap.add_argument('--workers', type=int, default=8)
ap.add_argument('--runs', default='data/inputs/registry/registry_runs.parquet'); ap.add_argument('--samples', default=None, help='parquet with sample_key/biosample_accession to restrict to catalog samples')
a = ap.parse_args()
proj = [l.strip() for l in open(a.projects) if l.strip()]
rr = pd.read_parquet(a.runs, columns=['run_accession', 'study_accession', 'sample_accession', 'library_selection', 'base_count'], filters=[('study_accession', 'in', proj)])
if a.samples:
    w = pd.read_parquet(a.samples, columns=['sample_key', 'biosample_accession', 'release_retired'])
    w = w[w.release_retired.isna()]; keep = set(w.sample_key) | set(w.biosample_accession.dropna())
    rr = rr[rr.sample_accession.isin(keep) | rr.run_accession.isin(keep)]
rr['h'] = [hashlib.md5(r.encode()).hexdigest() for r in rr.run_accession]
rr = rr.sort_values(['study_accession', 'h']).drop_duplicates('sample_accession').groupby('study_accession').head(a.k)
done = set()
if os.path.exists(a.out):
    done = {r['run'] for r in csv.DictReader(open(a.out))}
todo = rr[~rr.run_accession.isin(done)]
print(f'{len(rr)} runs picked in {rr.study_accession.nunique()} projects; {len(todo)} to check', flush=True)
F = ['project', 'sample_accession', 'run', 'library_selection', 'gbp', 'reads', 'len', 'distinct20', 'top_prefix', 'primer', 'primer_share', 'verdict']
new = not os.path.exists(a.out)
fh = open(a.out, 'a', newline=''); wr = csv.DictWriter(fh, fieldnames=F, extrasaction='ignore')
if new: wr.writeheader()
def go(r):
    try: res = peek(r.run_accession, nbytes=150000) or {'run': r.run_accession, 'verdict': 'no_fastq'}
    except Exception as e: res = {'run': r.run_accession, 'verdict': 'error'}
    bc = pd.to_numeric(r.base_count, errors='coerce')
    res.update(project=r.study_accession, sample_accession=r.sample_accession, library_selection=r.library_selection, gbp=None if pd.isna(bc) else round(bc / 1e9, 4)); return res
n = 0
with cf.ThreadPoolExecutor(a.workers) as ex:
    for res in ex.map(go, list(todo.itertuples())):
        wr.writerow(res); n += 1
        if n % 500 == 0: fh.flush(); print(n, flush=True)
fh.close(); print('done', n, flush=True)
