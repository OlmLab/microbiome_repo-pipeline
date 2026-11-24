"""Collections: curated, named sets of catalog studies / samples with pre-entered explorer filters (config/collections.yaml,
owner request 2026-09-29). Called by build_site.py as ``build(cfg_path, render, wide, studies, out_dir, hc_labels, ls_labels)``.

Semantics (from the yaml `note`): `studies` restricts to the listed accessions; `filters` restrict samples (list = any-of,
{min, max} = inclusive range; `min_samples_per_subject` = subjects with at least that many samples in the same study);
when both are present the filters apply within the listed studies. Membership is evaluated at build time on the wide
table, so every count on the pages is the count the explorer will show. Writes collections/index.html,
collections/<id>.html and data/collections.json (id → {title, studies, filters}) which static/explorer.js reads for
``samples/index.html?collection=<id>``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

KIND_LABELS = {'benchmark': 'Benchmark and reference cohorts', 'population': 'Population cohorts', 'lifestyle': 'Lifestyle',
               'design': 'Study design', 'disease': 'Disease reference sets', 'age': 'Age bands', 'other': 'Other'}
KIND_ORDER = ['benchmark', 'population', 'lifestyle', 'disease', 'design', 'age', 'other']


def load(cfg_path):
    p = Path(cfg_path)
    if not p.exists():
        return []
    y = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
    items = y.get('collections') if isinstance(y, dict) else y
    return [c for c in (items or []) if isinstance(c, dict) and c.get('id')]


def member_mask(c, wide: pd.DataFrame):
    """Boolean mask over `wide` for collection `c`; unknown filter keys / absent columns select nothing (recorded in notes)."""
    m = pd.Series(True, index=wide.index)
    notes = []
    studies = [str(s) for s in (c.get('studies') or [])]
    if studies:
        m &= wide.study_accession.isin(studies)
    for k, v in (c.get('filters') or {}).items():
        if k == 'min_samples_per_subject':
            mn = int(v.get('min', 1)) if isinstance(v, dict) else int(v)
            if 'subject_id' not in wide.columns:
                notes.append('subject_id column absent'); m &= False; continue
            sub = wide[m & wide.subject_id.notna()]
            n = sub.groupby(['study_accession', 'subject_id']).size()
            ok = set(n[n >= mn].index)
            keys = list(zip(wide.study_accession, wide.subject_id))
            m &= pd.Series([kk in ok for kk in keys], index=wide.index)
            continue
        col = 'collection_year' if k == 'collection_year' else k
        if col not in wide.columns:
            notes.append(f'{k}: column not in this package (0 samples until it is curated)'); m &= False; continue
        if isinstance(v, dict):
            s = pd.to_numeric(wide[col], errors='coerce')
            if 'min' in v: m &= s >= float(v['min'])
            if 'max' in v: m &= s <= float(v['max'])
        else:
            vals = [str(x) for x in (v if isinstance(v, list) else [v])]
            m &= wide[col].astype('string').isin(vals)
    return m, notes


def build(cfg_path, render, wide: pd.DataFrame, studies: pd.DataFrame, out_dir, hc_labels=None, ls_labels=None):
    cols = load(cfg_path)
    out = Path(out_dir)
    if not cols:
        return dict(n_collections=0)
    st = studies.set_index('study_accession')
    hc_labels = hc_labels or {}
    ls_labels = ls_labels or {}
    cards, detail_ctx, expo = [], [], {}
    empty = []
    for c in cols:
        m, notes = member_mask(c, wide)
        sub = wide[m]
        per_study = sub.groupby('study_accession').size().sort_values(ascending=False)
        rows = []
        for acc, n in per_study.items():
            r = st.loc[acc] if acc in st.index else None
            rows.append(dict(acc=acc, n=int(n), title=(r.study_title if r is not None else '') or '', first_public=str(r.first_public_min)[:4] if r is not None and pd.notna(r.first_public_min) else ''))
        listed = [str(s) for s in (c.get('studies') or [])]
        missing = [s for s in listed if s not in set(per_study.index)]
        in_catalog = set(st.index)
        listed_links = [dict(acc=s, linked=s in in_catalog) for s in listed]
        def facet(col, labels=None, top=6):
            if col not in sub.columns or not len(sub):
                return []
            vc = sub[col].value_counts().head(top)
            return [dict(v=str(k), label=(labels or {}).get(str(k), ''), n=int(v)) for k, v in vc.items()]
        d = dict(id=c['id'], title=c.get('title') or c['id'], kind=c.get('kind') or 'other', kind_label=KIND_LABELS.get(c.get('kind') or 'other', 'Other'),
                 blurb=c.get('blurb') or '', notes=c.get('notes') or '', refs=c.get('references') or [], filters=c.get('filters') or {}, listed=listed, listed_links=listed_links,
                 n_samples=int(m.sum()), n_studies=int(per_study.shape[0]), rows=rows, missing=missing, build_notes=notes,
                 age=facet('age_category'), country=facet('country'), condition=facet('health_condition', hc_labels), lifestyle=facet('lifestyle', ls_labels),
                 explorer=f"../samples/index.html?collection={c['id']}")
        if d['n_samples'] == 0:
            # R2026.43: a rule that matches no sample in this release gets no card, no page and no explorer entry;
            # its old address forwards to the index so bookmarks keep working.
            empty.append(d['id'])
            continue
        cards.append(d)
        expo[c['id']] = dict(title=d['title'], studies=listed, filters=d['filters'])
    (out / 'data').mkdir(parents=True, exist_ok=True)
    (out / 'data' / 'collections.json').write_text(json.dumps(expo, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    groups = [(KIND_LABELS.get(k, k), [d for d in cards if d['kind'] == k]) for k in KIND_ORDER]
    groups = [(lab, ds) for lab, ds in groups if ds]
    crumbs = [dict(label='Home', href='../index.html'), dict(label='Collections')]
    render('collections_index.html', 'collections/index.html', '../', nav='collections', groups=groups, n=len(cards), crumbs=crumbs,
           n_samples_total=int(sum(d['n_samples'] for d in cards)))
    for d in cards:
        render('collection.html', f"collections/{d['id']}.html", '../', nav='collections', c=d, use_datatables=True,
               crumbs=[dict(label='Home', href='../index.html'), dict(label='Collections', href='index.html'), dict(label=d['title'])])
    return dict(n_collections=len(cards), n_samples=int(sum(d['n_samples'] for d in cards)), empty=empty)
