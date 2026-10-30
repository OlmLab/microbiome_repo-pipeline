#!/usr/bin/env python
"""Validate config/collections.yaml against a package directory and write collections_preview.csv (+ collections_studies.csv).

usage: python scripts/build_collections.py --package <dir with catalog_studies.parquet, catalog_sample_metadata_wide.parquet>
                                           [--config config/collections.yaml] [--out <dir>] [--check]
--check exits 1 on any problem (unknown accession, bad vocabulary value, blurb > 60 words, filter-only set < 100 samples).
Semantics: `studies` restricts to listed accessions; `filters` restrict samples (list = any-of, {min,max} = range);
both present → filters apply within the listed studies. `lifestyle` / `collection_year` are skipped (with a note)
when the column is absent from the package (pre-1.12.0).
"""
import argparse, json, re, sys, pathlib
import pandas as pd, yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIST_KEYS = {'age_category', 'country', 'health_condition', 'lifestyle', 'antibiotic_exposure', 'sex', 'study_accession', 'body_site_class'}
RANGE_KEYS = {'collection_year', 'min_samples_per_subject'}
KINDS = {'benchmark', 'population', 'disease', 'design', 'age', 'lifestyle', 'other'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--package', required=True); ap.add_argument('--config', default=str(ROOT / 'config' / 'collections.yaml'))
    ap.add_argument('--out', default='.'); ap.add_argument('--check', action='store_true')
    a = ap.parse_args(); P = pathlib.Path(a.package); out = pathlib.Path(a.out)
    st = pd.read_parquet(P / 'catalog_studies.parquet'); wide = pd.read_parquet(P / 'catalog_sample_metadata_wide.parquet')
    ver = json.load(open(P / 'VERSION.json'))['package_version'] if (P / 'VERSION.json').exists() else 'unknown'
    pack = yaml.safe_load(open(ROOT / 'config' / 'packs' / 'gut.yaml'))
    allowed = {'age_category': set(pack['age_categories']) | {'unknown'},
               'health_condition': set(yaml.safe_load(open(ROOT / 'config' / 'vocab' / 'health_conditions.yaml'))['codes']),
               'lifestyle': set(yaml.safe_load(open(ROOT / 'config' / 'vocab' / 'lifestyle.yaml'))['codes']),
               'antibiotic_exposure': {'yes', 'no', 'unknown'}, 'sex': {'female', 'male', 'unknown'},
               'body_site_class': set(wide.body_site_class.dropna().unique()), 'country': set(wide.country.dropna().unique()),
               'study_accession': set(st.study_accession)}
    exists = set(st.study_accession)
    subj = wide.dropna(subset=['subject_id']).groupby(['study_accession', 'subject_id']).size()
    cfg = yaml.safe_load(open(a.config)); problems, rows, srows, ids = [], [], [], set()
    for c in cfg['collections']:
        cid = c.get('id', '?')
        if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', str(cid)) or cid in ids: problems.append(f'{cid}: bad or duplicate id')
        ids.add(cid)
        if c.get('kind') not in KINDS: problems.append(f'{cid}: kind {c.get("kind")!r} not allowed')
        if len(str(c.get('blurb', '')).split()) > 60: problems.append(f'{cid}: blurb > 60 words')
        studies = c.get('studies') or []; keep = [s for s in studies if s in exists]
        for s in studies:
            if s not in exists: problems.append(f'{cid}: accession {s} not in catalog_studies.parquet ({ver})')
        filters = c.get('filters') or {}; mask = pd.Series(True, index=wide.index); notes = []
        for k, v in filters.items():
            if k in LIST_KEYS:
                if not isinstance(v, list) or not v: problems.append(f'{cid}: filter {k} must be a non-empty list'); continue
                bad = [x for x in v if x not in allowed[k]]
                if bad: problems.append(f'{cid}: filter {k} values not in vocabulary: {bad}')
                if k not in wide.columns: notes.append(f'{k} absent in {ver}; not evaluated'); continue
                mask &= wide[k].isin(v)
            elif k in RANGE_KEYS:
                if not isinstance(v, dict) or not v or not set(v) <= {'min', 'max'}: problems.append(f'{cid}: filter {k} must be {{min,max}}'); continue
                if k == 'min_samples_per_subject':
                    ok = subj[(subj >= v.get('min', 0)) & (subj <= v.get('max', 10 ** 9))].index
                    mask &= pd.MultiIndex.from_frame(wide[['study_accession', 'subject_id']]).isin(ok)
                elif 'collection_year' in wide.columns:
                    cy = pd.to_numeric(wide['collection_year'], errors='coerce'); mask &= (cy >= v.get('min', -1e9)) & (cy <= v.get('max', 1e9))
                else: notes.append(f'collection_year absent in {ver}; not evaluated')
            else: problems.append(f'{cid}: unknown filter key {k}')
        if keep: mask &= wide.study_accession.isin(keep)
        if not keep and not filters: problems.append(f'{cid}: neither studies nor filters')
        sel = wide[mask]
        if not keep and filters and len(sel) < 100: problems.append(f'{cid}: filter-only collection returns {len(sel)} < 100 samples')
        rows.append(dict(id=cid, title=c.get('title'), kind=c.get('kind'), n_listed_studies=len(keep), n_studies=int(sel.study_accession.nunique()),
                         n_samples=int(len(sel)), n_samples_primary_gut=int((sel.body_site_class == 'primary').sum()),
                         n_subjects_with_id=int(sel.dropna(subset=['subject_id']).groupby(['study_accession', 'subject_id']).ngroups),
                         filters=json.dumps(filters, sort_keys=True) if filters else '', notes='; '.join(notes)))
        sti = st.set_index('study_accession')
        for s in keep:
            srows.append(dict(collection=cid, study_accession=s, study_title=sti.loc[s, 'study_title'], n_samples_in_collection=int((sel.study_accession == s).sum()),
                              ena_url=f'https://www.ebi.ac.uk/ena/browser/view/{s}', verified_in=f'catalog_studies.parquet {ver}'))
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / 'collections_preview.csv', index=False); pd.DataFrame(srows).to_csv(out / 'collections_studies.csv', index=False)
    print(pd.DataFrame(rows)[['id', 'kind', 'n_listed_studies', 'n_studies', 'n_samples']].to_string(index=False))
    for p in problems: print('PROBLEM:', p, file=sys.stderr)
    if a.check and problems: sys.exit(1)


if __name__ == '__main__':
    main()
