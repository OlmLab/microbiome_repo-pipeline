#!/usr/bin/env python3
"""Context for deciding what a health-condition label of a project's samples means (R2026.41, owner's request (a)).

  python scripts/condition_string_context.py --items ITEMS.csv --reader 2 --set A        # that reader's items
  python scripts/condition_string_context.py --items ITEMS.csv --item 3012               # one item with its context
  python scripts/condition_string_context.py --items ITEMS.csv --item 3012 --full paper_methods

For one item (a project and a (health_condition code, detail) pair carried by some of its samples) it prints: the item; how
the value was determined for an example sample (route, source, the raw archive value); the archive attributes of up to four
samples that carry the pair and of two that do not; the project's sample table by health condition and comparison group;
and the project's texts (archive record, abstract, the start of the methods).
"""
import argparse, json, os, sys
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src')); sys.path.insert(0, HERE)
import catchup_passes as CP  # noqa: E402
PKG = os.path.expanduser(os.environ.get('CATALOG_PKG', '~/catalog/cache/deep_pass/pkg_r39'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--items', required=True); ap.add_argument('--reader', type=int); ap.add_argument('--set', default='A'); ap.add_argument('--item', type=int)
    ap.add_argument('--full'); ap.add_argument('--chars', type=int, default=9000)
    a = ap.parse_args()
    it = pd.read_csv(os.path.expanduser(a.items), dtype=str).fillna('')
    if a.item is None:
        x = it[it[f'reader_{a.set}'].astype(int) == a.reader] if a.reader is not None else it
        print(x[['item_id', 'study_accession', 'code']].to_string(index=False))
        return
    r = it[it.item_id.astype(int) == a.item].iloc[0]
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
    print(json.dumps({k: r[k] for k in ['item_id', 'study_accession', 'code', 'detail', 'n', 'n_analysis_samples']}, indent=1, ensure_ascii=False))
    w = pd.read_parquet(os.path.join(PKG, 'catalog_sample_metadata_wide.parquet'), filters=[('study_accession', '==', acc)],
                        columns=['sample_key', 'release_retired', 'body_site_code', 'health_condition', 'health_condition_detail', 'comparison_group', 'subject_id', 'age_category', 'country'])
    w = w[w.release_retired.isna()]
    has = (w.health_condition.fillna('') == r.code) & (w.health_condition_detail.fillna('') == r.detail)
    print('-' * 40, f'samples in the deposit: {len(w)}; carrying this pair: {int(has.sum())}')
    t = w.assign(pair=(w.health_condition.fillna('(none)') + ' | ' + w.health_condition_detail.fillna(''))).groupby('pair').size().sort_values(ascending=False)
    print('health_condition | detail (samples):'); print('\n'.join(f'  {k}: {v}' for k, v in t.head(14).items()) + (f'\n  … (+{len(t) - 14} more pairs)' if len(t) > 14 else ''))
    for c in ('comparison_group', 'body_site_code', 'age_category', 'country'):
        vc = w[c].fillna('(blank)').value_counts()
        print(f'{c}: ' + '; '.join(f'{k} x{v}' for k, v in vc.head(8).items()))
        if c == 'comparison_group' and has.any():
            vc2 = w[has][c].fillna('(blank)').value_counts()
            print('  among the samples carrying the pair: ' + '; '.join(f'{k} x{v}' for k, v in vc2.head(8).items()))
    ex = list(w[has].sample_key.head(4)) + list(w[~has].sample_key.head(2))
    d = pd.read_parquet(os.path.join(PKG, 'catalog_sample_determinations.parquet'), filters=[('study_accession', '==', acc), ('sample_key', 'in', ex)],
                        columns=['sample_key', 'field_name', 'field_value', 'value_normalized', 'route', 'evidence_source', 'determined_by', 'release_retired'])
    d = d[d.release_retired.isna()]
    print('-' * 40, 'example samples (field: raw value -> stored value [route, source])')
    for k in ex:
        print(('CARRIES THE PAIR ' if k in set(w[has].sample_key) else 'does not carry it ') + k)
        for x in d[d.sample_key == k].itertuples():
            print(f'    {x.field_name}: {str(x.field_value)[:120]} -> {str(x.value_normalized)[:60]} [{x.route}, {str(x.evidence_source)[:60]}]')
    dos = CP.load_dossier(acc) or {}
    prof = dos.get('attribute_profile') or []
    if prof:
        print('-' * 40, 'archive attributes of the project (key [samples, distinct]: top values)')
        for p in prof[:30]:
            print(f"  {p['key']} [{p['n_samples']}, {p['n_distinct']}]: " + '; '.join(f'{k} x{v}' for k, v in list(p['top_values'].items())[:6]))
    print('-' * 40, 'project record')
    print('title:', st.get('study_title')); print('archive description:', (segs.get('archive_description') or '')[:2500])
    print('text mode:', tx.get('mode'), '| segments:', {k: len(v or '') for k, v in segs.items()})
    if segs.get('paper_abstract'):
        print('-' * 40, 'paper abstract'); print(segs['paper_abstract'][:3500])
    if segs.get('paper_methods'):
        print('-' * 40, f'paper methods (first {a.chars} characters; --full paper_methods for all)'); print(segs['paper_methods'][:a.chars])


if __name__ == '__main__':
    main()
