#!/usr/bin/env python3
"""Context for an independent audit of items the paper re-read (scripts/reread_passes.py) committed.

  python scripts/reread_audit_context.py --items ~/catalog/cache/deep_pass/b1/audit/audit_items.csv --reader 3            # lists that reader's items
  python scripts/reread_audit_context.py --items ... --item 17                                                         # one item with its context
  python scripts/reread_audit_context.py --items ... --item 17 --full paper_methods                                    # a whole text segment

For one item it prints: the item (kind, value, selector, quote), the project record (title, archive description), the sample
table summarised by the variables a selector may use, and the passages of the texts in which the quote occurs.
"""
import argparse, json, os, sys
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, HERE)
import catchup_passes as CP  # noqa: E402
PKG = os.path.expanduser(os.environ.get('CATALOG_PKG', '~/catalog/cache/deep_pass/pkg_r39'))
SEL = ['comparison_group', 'country', 'location_region', 'health_condition', 'age_category', 'lifestyle']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--items', required=True); ap.add_argument('--reader', type=int); ap.add_argument('--item', type=int); ap.add_argument('--full')
    ap.add_argument('--window', type=int, default=2500)
    a = ap.parse_args()
    it = pd.read_csv(os.path.expanduser(a.items)).fillna('')
    if a.item is None:
        x = it[it.reader == a.reader] if a.reader is not None else it
        print(x[['item_id', 'item_kind', 'study_accession']].to_string(index=False))
        return
    r = it[it.item_id == a.item].iloc[0]
    acc = r.study_accession
    st = pd.read_parquet(os.path.join(PKG, 'catalog_studies.parquet'), filters=[('study_accession', '==', acc)])
    st = (st[st.release_retired.isna()] if 'release_retired' in st.columns else st).iloc[0].to_dict()
    papers = pd.read_parquet(os.path.join(ROOT, 'data', 'inputs', 'catalog', 'study_papers.parquet'))
    papers = papers[(papers.relation == 'own_data') & (papers.study_accession == acc)]
    tx = CP.project_texts(acc, CP.load_dossier(acc), st, papers)
    segs = tx.get('segs') or {}
    if a.full:
        print(segs.get(a.full) or f'(no segment {a.full}; segments: {list(segs)})')
        return
    print('=' * 100)
    print(json.dumps({k: (str(r[k]) if k != 'item_id' else int(r[k])) for k in it.columns if str(r[k]) != ''}, indent=1, ensure_ascii=False))
    print('-' * 40, 'project record')
    print('title:', st.get('study_title')); print('archive description:', (segs.get('archive_description') or '')[:1500])
    print('text mode:', tx.get('mode'), '| own-data papers:', [(p.pmid, p.pmcid) for p in papers.itertuples()][:6], '| segments:', {k: len(v or '') for k, v in segs.items()})
    have = set(__import__('pyarrow.parquet', fromlist=['x']).ParquetFile(os.path.join(PKG, 'catalog_sample_metadata_wide.parquet')).schema_arrow.names)
    w = pd.read_parquet(os.path.join(PKG, 'catalog_sample_metadata_wide.parquet'), columns=[c for c in ['study_accession', 'sample_key', 'sample_type', 'release_retired', 'body_site'] + SEL if c in have],
                        filters=[('study_accession', '==', acc)])
    w = w[w.release_retired.isna()]
    print('-' * 40, f'samples in the deposit: {len(w)}')
    for c in ['sample_type', 'body_site'] + SEL:
        if c in w.columns:
            vc = w[c].fillna('(blank)').value_counts()
            print(f'  {c}: ' + '; '.join(f'{k} x{v}' for k, v in vc.head(10).items()) + (f' … (+{len(vc) - 10} more values)' if len(vc) > 10 else ''))
    if r.item_kind == 'lifestyle' and str(r.get('selector_field', '')):
        try:
            vals = json.loads(r.selector_values) if str(r.selector_values).startswith('[') else [r.selector_values]
            print(f'  selector {r.selector_field} in {vals}: {int(w[r.selector_field].isin(vals).sum())} samples')
        except Exception as e:
            print('  selector could not be evaluated:', e)
    print('-' * 40, 'passages holding the quote')
    q = CP.norm(str(r.quote)).strip(" .,;:\"'")
    found = False
    for name, t in segs.items():
        t = t or ''
        i = t.find(q) if q else -1
        if i >= 0:
            found = True
            print(f'[{name}] …' + t[max(0, i - a.window): i + len(q) + a.window] + '…\n')
    if not found:
        print('(the quote was not found verbatim in the texts loaded now — report this as not_supported unless a segment read with --full shows it)')


if __name__ == '__main__':
    main()
