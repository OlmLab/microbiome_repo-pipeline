#!/usr/bin/env python3
"""Context for finding per-sample case / control labels of one project (R2026.41, owner's request (a), pilot).

  python scripts/label_hunt_context.py --project PRJNA000000                 # record, texts, attribute profile, sample summary
  python scripts/label_hunt_context.py --project PRJNA000000 --full paper_methods
  python scripts/label_hunt_context.py --project PRJNA000000 --samples out.csv   # one row per specimen with every archive attribute and run field
"""
import argparse, os, sys
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, HERE)
import catchup_passes as CP  # noqa: E402
PKG = os.path.expanduser(os.environ.get('CATALOG_PKG', '~/catalog/cache/deep_pass/pkg_r39'))
ATTR = os.path.join(ROOT, 'data', 'inputs', 'registry', 'registry_biosample_attributes.parquet')
REG_RUNS = os.path.join(ROOT, 'data', 'inputs', 'registry', 'registry_runs.parquet')
RUN_FIELDS = ['library_name', 'sample_title', 'sample_alias', 'host_phenotype', 'host_status', 'disease', 'dev_stage', 'experimental_factor', 'isolation_source']
WCOLS = ['sample_key', 'release_retired', 'sample_type', 'body_site_code', 'subject_id', 'health_condition', 'health_condition_detail', 'comparison_group', 'age_category', 'sex', 'timepoint_label']


def samples(acc):
    w = pd.read_parquet(os.path.join(PKG, 'catalog_sample_metadata_wide.parquet'), filters=[('study_accession', '==', acc)], columns=WCOLS)
    w = w[w.release_retired.isna()].drop(columns=['release_retired'])
    a = pd.read_parquet(ATTR, columns=['sample_acc', 'attr_key', 'attr_value'], filters=[('study_accession', '==', acc)])
    if len(a):
        p = a.drop_duplicates(['sample_acc', 'attr_key']).pivot(index='sample_acc', columns='attr_key', values='attr_value')
        p.columns = [f'attr:{c}' for c in p.columns]
        w = w.merge(p, left_on=w.sample_key.astype(str).str.split('|').str[0], right_index=True, how='left').drop(columns=['key_0'], errors='ignore')
    import pyarrow.parquet as pq
    have = set(pq.ParquetFile(REG_RUNS).schema_arrow.names)
    rf = [c for c in RUN_FIELDS if c in have]
    rr = pd.read_parquet(REG_RUNS, columns=['sample_accession'] + rf, filters=[('study_accession', '==', acc)])
    if len(rr):
        g = rr.groupby('sample_accession')[rf].agg(lambda x: ' | '.join(sorted(set(str(v).strip() for v in x.dropna() if str(v).strip()))[:4]))
        g.columns = [f'run:{c}' for c in g.columns]
        w = w.merge(g, left_on=w.sample_key.astype(str).str.split('|').str[0], right_index=True, how='left').drop(columns=['key_0'], errors='ignore')
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', required=True); ap.add_argument('--full'); ap.add_argument('--samples'); ap.add_argument('--chars', type=int, default=12000)
    a = ap.parse_args(); acc = a.project
    st = pd.read_parquet(os.path.join(PKG, 'catalog_studies.parquet'), filters=[('study_accession', '==', acc)])
    st = (st[st.release_retired.isna()] if 'release_retired' in st.columns else st).iloc[0].to_dict()
    papers = pd.read_parquet(os.path.join(ROOT, 'data', 'inputs', 'catalog', 'study_papers.parquet'))
    papers = papers[(papers.relation == 'own_data') & (papers.study_accession == acc)]
    tx = CP.project_texts(acc, CP.load_dossier(acc), st, papers)
    segs = tx.get('segs') or {}
    if a.full:
        print(segs.get(a.full) or f'(no segment {a.full}; segments: {list(segs)})'); return
    w = samples(acc)
    if a.samples:
        w.to_csv(a.samples, index=False); print(f'{len(w)} samples, {len(w.columns)} columns -> {a.samples}'); return
    print('=' * 100); print('project', acc, '| samples:', len(w), '| persons (subject_id):', w.subject_id.nunique())
    for c in ('body_site_code', 'health_condition', 'comparison_group', 'age_category'):
        print(f'{c}: ' + '; '.join(f'{k} x{v}' for k, v in w[c].fillna('(blank)').value_counts().head(10).items()))
    print('-' * 40, 'archive attributes and run fields (column [samples with a value, distinct values]: most frequent values)')
    for c in w.columns:
        if c.startswith(('attr:', 'run:')):
            s = w[c].dropna().astype(str); s = s[s.str.strip() != '']
            if len(s):
                vc = s.value_counts()
                print(f'  {c} [{len(s)}, {len(vc)}]: ' + '; '.join(f'{str(k)[:50]} x{v}' for k, v in vc.head(8).items()))
    print('-' * 40, 'project record'); print('title:', st.get('study_title')); print('design statement:', st.get('design_statement') if 'design_statement' in st else '')
    print('archive description:', (segs.get('archive_description') or '')[:3000])
    print('text mode:', tx.get('mode'), '| segments:', {k: len(v or '') for k, v in segs.items()})
    if segs.get('paper_abstract'):
        print('-' * 40, 'paper abstract'); print(segs['paper_abstract'][:4000])
    if segs.get('paper_methods'):
        print('-' * 40, f'paper methods (first {a.chars} characters; --full paper_methods for all)'); print(segs['paper_methods'][:a.chars])


if __name__ == '__main__':
    main()
