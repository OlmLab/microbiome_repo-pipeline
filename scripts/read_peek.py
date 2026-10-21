#!/usr/bin/env python
"""read_peek.py — is a run amplicon or shotgun? (R2026.27, owner request 2026-10-20)

Downloads only the first ~150-400 kB of a run's FASTQ from ENA (HTTP range request on fastq_ftp; no full download) and looks at
the first ~1,000-3,000 reads: amplicon libraries start almost every read with the same primer, so the share of distinct
20-nt read starts is tiny (0.01-0.1) and a 16S primer (515F, 341F, 806R, 27F ...) matches most reads; shotgun reads start
anywhere (distinct share 0.7-1.0). verdict = amplicon when distinct20 < 0.3 or a primer matches > 30 % of reads.
usage: python scripts/read_peek.py RUN [RUN ...]
"""
import pandas as pd, requests, zlib, collections, re, io, sys, json
PRIM={'16S 515F':'GTG[CT]CAGC[AC]GCCGCGGTAA','16S 341F':'CCTACGGG[ACGT]GG[ACT][GT]CAG','16S 806R':'GGACTAC[ACT][ACG]GGGT[AT]TCTAAT','16S 27F':'AG[AG]GTT[CT]GAT[CT][AC]TGGCTCAG','16S 926F':'AAACT[CT]AAA[GT]GAATTG[AG]CGG','ITS1F':'CTTGGTCATTTAGAGGAAGTAA','16S 338F':'ACTCCTACGGGAGGCAGCAG','16S V1V2 27F-alt':'AGAGTTTGATCCTGGCTCAG'}
def peek(run, nbytes=400000):
    r=requests.get('https://www.ebi.ac.uk/ena/portal/api/filereport',params=dict(accession=run,result='read_run',fields='fastq_ftp,submitted_ftp',format='json'),timeout=60).json()
    urls=[u for u in (r[0].get('fastq_ftp') or '').split(';') if u] or [u for u in (r[0].get('submitted_ftp') or '').split(';') if u.endswith('.gz')]
    if not urls: return None
    c=requests.get('https://'+urls[0],headers={'Range':f'bytes=0-{nbytes}'},timeout=90).content
    d=zlib.decompressobj(16+zlib.MAX_WBITS).decompress(c).decode(errors='ignore').split('\n')
    s=[x for x in d[1::4][:-1] if x]
    n=len(s)
    if not n: return None
    pre=collections.Counter(x[:20] for x in s)
    prim=max(((k, sum(1 for x in s if re.match(p,x))/n) for k,p in PRIM.items()), key=lambda t:t[1])
    return dict(run=run, reads=n, len=collections.Counter(len(x) for x in s).most_common(1)[0][0], distinct20=round(len(pre)/n,2), top_prefix=round(pre.most_common(1)[0][1]/n,2), primer=prim[0] if prim[1]>0.05 else '', primer_share=round(prim[1],2),
                verdict='amplicon' if (len(pre)/n<0.3 or prim[1]>0.3) else 'shotgun')


if __name__ == "__main__":
    import sys as _s
    for run in _s.argv[1:]:
        print(peek(run))
