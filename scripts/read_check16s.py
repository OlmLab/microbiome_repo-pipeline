#!/usr/bin/env python
"""read_check16s.py — shotgun vs amplicon from reads, after PARTIE (Torres et al. 2017, Bioinformatics 33:2389).

PARTIE's two most important features were the share of reads that align to 16S rRNA genes and the share of
"unique" 15-mers (15-mers seen <= 10 times) in a 10,000-read subsample. This script measures both on the first
reads of the run's ENA FASTQ (HTTP range request, ~1-2 MB, no full download):

  frac16s   share of reads aligning to NCBI RefSeq 16S (27,646 genes; minimap2 -x sr)
  uniq15    share of distinct 15-mers seen <= 10 times (PARTIE's statistic)
  distinct20, primer_share   the R2026.27 read_peek statistics, kept for comparison

Call:  adapter_dimer  >= 50 % of reads carry an Illumina adapter in their first 60 nt (failed library)
       amplicon   frac16s >= 0.50
       amplicon_other  ITS primer in >= 10 % of reads, or one 20-nt read start in >= 15 % of non-adapter reads
       shotgun    frac16s <  0.05 and top 20-nt read start < 5 % (shotgun stool metagenomes are ~0.1-1 % 16S)
       rrna_rich  16S + 23S (NCBI LSU_prokaryote_rRNA) >= 20 % of reads but not a 16S amplicon (RNA without rRNA depletion)
       unclear    otherwise (mixed libraries, other targeted genes, rRNA-rich RNA libraries)
Reads that are N-masked, colour-space or homopolymer are removed first (they made deep shotgun runs look like
amplicons in the R2026.27 check). A low distinct20 / uniq15 alone never makes a run amplicon: duplicated or position-sorted shotgun files also have it.

usage: python scripts/read_check16s.py --runs runs.txt --out out.csv [--workers 6] [--reads 4000]
"""
import argparse, csv, os, subprocess, tempfile, zlib, collections, re, concurrent.futures as cf
import requests

MMI = os.path.expanduser('~/catalog/cache/partie/refseq_16S.mmi')
MMI23 = os.path.expanduser('~/catalog/cache/partie/refseq_23S.mmi')   # NCBI LSU_prokaryote_rRNA (4,047 genes)
PRIMERS = ['GTGCCAGCMGCCGCGGTAA', 'GGACTACHVGGGTWTCTAAT', 'CCTACGGGNGGCWGCAG', 'GACTACHVGGGTATCTAATCC',
           'AGAGTTTGATCMTGGCTCAG', 'AGAGTTTGATCCTGGCTCAG', 'ATTAGAWACCCBDGTAGTCC', 'CCGTCAATTCMTTTRAGTTT']
IUPAC = {'M': '[AC]', 'R': '[AG]', 'W': '[AT]', 'S': '[CG]', 'Y': '[CT]', 'K': '[GT]', 'V': '[ACG]', 'H': '[ACT]',
         'D': '[AGT]', 'B': '[CGT]', 'N': '[ACGT]'}
PRX = [re.compile(''.join(IUPAC.get(c, c) for c in p[:15])) for p in PRIMERS]
# fungal ITS primers (ITS1F, ITS1, ITS2, ITS3, ITS4, gITS7, ITS86F, fITS7)
ITS = ['CTTGGTCATTTAGAGGAAGTAA', 'TCCGTAGGTGAACCTGCGG', 'GCTGCGTTCTTCATCGATGC', 'GCATCGATGAAGAACGCAGC', 'TCCTCCGCTTATTGATATGC',
       'GTGARTCATCGARTCTTTG', 'GTGAATCATCGAATCTTTGAA', 'GTGARTCATCGAATCTTTG']
IRX = [re.compile(''.join(IUPAC.get(c, c) for c in p[:15])) for p in ITS]
ADAPTER = re.compile('AGATCGGAAGAGC|GATCGGAAGAGC|CTGTCTCTTATACACATCT')


def fastq_urls(run):
    r = requests.get('https://www.ebi.ac.uk/ena/portal/api/filereport', timeout=60,
                     params=dict(accession=run, result='read_run', fields='fastq_ftp,submitted_ftp', format='json')).json()
    urls = [u for u in (r[0].get('fastq_ftp') or '').split(';') if u] if r else []
    if not urls and r:
        urls = [u for u in (r[0].get('submitted_ftp') or '').split(';') if u.endswith(('.fastq.gz', '.fq.gz'))]
    # paired runs list _1/_2 after an optional unpaired file; prefer _1
    urls.sort(key=lambda u: (0 if u.endswith('_1.fastq.gz') else 1))
    return urls


def head_reads(url, nbytes, nreads):
    c = requests.get('https://' + url, headers={'Range': f'bytes=0-{nbytes}'}, timeout=120).content
    d = zlib.decompressobj(32 + zlib.MAX_WBITS)
    try:
        txt = d.decompress(c)
    except zlib.error:
        return []
    lines = txt.decode('ascii', 'replace').split('\n')
    seqs = [lines[i + 1].strip().upper() for i in range(0, len(lines) - 3, 4) if lines[i].startswith('@')]
    return [s for s in seqs if len(s) >= 40][:nreads]


def usable(s):
    # drop N-masked reads (SRA human-read removal replaces human reads with N), colour-space (SOLiD) reads,
    # and homopolymer / low-complexity reads (poly-A, poly-G); they say nothing about the library type
    if sum(s.count(c) for c in 'ACGT') < 0.9 * len(s):
        return False
    return max(s.count(c) for c in 'ACGT') < 0.8 * len(s)


def stats(seqs):
    n_all = len(seqs)
    seqs = [s for s in seqs if usable(s)]
    n = len(seqs)
    if n < 200:
        return dict(reads=n_all, usable=n, masked_share=round(1 - n / max(1, n_all), 4))
    with tempfile.NamedTemporaryFile('w', suffix='.fa', delete=False) as fh:
        for i, s in enumerate(seqs):
            fh.write(f'>r{i}\n{s}\n')
        fa = fh.name
    def mapped(idx):
        out = subprocess.run(['minimap2', '-x', 'sr', '-t', '1', '--secondary=no', idx, fa], capture_output=True, text=True).stdout
        return {l.split('\t', 1)[0] for l in out.splitlines() if l}
    hit = mapped(MMI)
    hit23 = mapped(MMI23) if os.path.exists(MMI23) else set()
    os.unlink(fa)
    km = collections.Counter(s[i:i + 15] for s in seqs for i in range(0, len(s) - 14))
    uniq15 = sum(1 for v in km.values() if v <= 10) / max(1, len(km))
    pre = collections.Counter(s[:20] for s in seqs)
    distinct20 = len(pre) / n
    primer = sum(1 for s in seqs if any(p.search(s[:40]) for p in PRX)) / n
    its = sum(1 for s in seqs if any(p.search(s[:40]) for p in IRX)) / n
    ad = [bool(ADAPTER.search(s[:60])) for s in seqs]
    clean = [s for s, x in zip(seqs, ad) if not x]
    top1 = (collections.Counter(s[:20] for s in clean).most_common(1)[0][1] / len(clean)) if clean else 0.0
    return dict(reads=n_all, usable=n, masked_share=round(1 - n / n_all, 4), len=int(sum(map(len, seqs)) / n), frac16s=round(len(hit) / n, 4), frac23s=round(len(hit23) / n, 4),
                frac_rrna=round(len(hit | hit23) / n, 4), uniq15=round(uniq15, 4),
                distinct20=round(distinct20, 4), primer_share=round(primer, 4), its_share=round(its, 4),
                adapter_share=round(sum(ad) / n, 4), top1_share=round(top1, 4))


def call(r):
    if r.get('usable', 0) < 200:
        return 'too_few_usable_reads' if r['reads'] >= 200 else 'too_few_reads'
    if r['frac16s'] >= 0.5:
        return 'amplicon'               # 16S amplicon
    if r['adapter_share'] >= 0.5:
        return 'adapter_dimer'          # failed library: most reads are adapter-adapter products
    if r['its_share'] >= 0.1 or r['top1_share'] >= 0.15:
        return 'amplicon_other'         # ITS / other gene amplicon: a few read starts dominate
    if r.get('frac_rrna', 0) >= 0.2:
        return 'rrna_rich'              # 16S+23S >= 20 % without being a 16S amplicon: typical of RNA libraries without rRNA depletion
    if r['frac16s'] < 0.05 and r['top1_share'] < 0.05:
        return 'shotgun'                # calibration: shotgun runs > 1 Gbp have top1 median 0.001, 99th pct 0.13
    return 'unclear'


def check(run, nbytes=600_000, nreads=4000):
    urls = fastq_urls(run)
    if not urls:
        return {'run': run, 'verdict': 'no_fastq'}
    seqs = head_reads(urls[0], nbytes, nreads)
    if not seqs:
        return {'run': run, 'verdict': 'unreadable'}
    if sum(1 for x in seqs if re.search(r'[0-3]{10}', x)) > 0.5 * len(seqs):
        return {'run': run, 'reads': len(seqs), 'verdict': 'colour_space'}
    if sum(1 for x in seqs if usable(x)) < 1000:
        # head of file is N-masked (human reads removed) or low-complexity: read deeper, up to 4 MB; if still
        # < 200 usable reads the run is mostly host (masked) reads -> 'mostly_masked'
        deep = head_reads(urls[0], 4_000_000, 20000)
        masked = 1 - sum(1 for x in deep if usable(x)) / max(1, len(deep))
        seqs = [x for x in deep if usable(x)][:nreads] or seqs
        if len(seqs) < 200:
            return {'run': run, 'reads': len(deep), 'usable': len(seqs), 'masked_share': round(masked, 4), 'verdict': 'mostly_masked'}
    r = stats(seqs)
    r.update(run=run, verdict=call(r))
    return r


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--workers', type=int, default=6); ap.add_argument('--reads', type=int, default=4000)
    a = ap.parse_args()
    runs = [l.split(',')[0].strip() for l in open(a.runs) if l.strip() and not l.startswith('run')]
    done = {r['run'] for r in csv.DictReader(open(a.out))} if os.path.exists(a.out) else set()
    todo = [r for r in dict.fromkeys(runs) if r not in done]
    print(len(runs), 'runs;', len(todo), 'to check', flush=True)
    F = ['run', 'reads', 'usable', 'masked_share', 'len', 'frac16s', 'frac23s', 'frac_rrna', 'uniq15', 'distinct20', 'primer_share', 'its_share', 'adapter_share', 'top1_share', 'verdict']
    new = not os.path.exists(a.out)
    fh = open(a.out, 'a', newline=''); wr = csv.DictWriter(fh, fieldnames=F, extrasaction='ignore')
    if new:
        wr.writeheader()

    def go(run):
        try:
            return check(run, nreads=a.reads)
        except Exception:
            return {'run': run, 'verdict': 'error'}
    n = 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for res in ex.map(go, todo):
            wr.writerow(res); n += 1
            if n % 200 == 0:
                fh.flush(); print(n, flush=True)
    fh.close(); print('done', n, flush=True)
