"""Atlas › PCA page: an interactive Canvas2D scatter of every catalog sample with a Sandpiper genus profile
(CLR-PCA scores, package table catalog_sandpiper_pca_scores.parquet) coloured by curated metadata.

Called by build_site.py as ``build(env, render, ctx, out_dir)``:
  env      the generator's Jinja Environment (unused here; render() owns it)
  render   render(tpl, path, root, nav, **ctx) — the generator's helper (writes out_dir/path, records it)
  ctx      dict with package tables: ctx['wide'] (gut_sample_metadata_wide), ctx['studies'] (gut_studies) and EITHER
           ctx['pca_scores'], ctx['pca_loadings'], ctx['pca_variance'], ctx['sp_summary'] (DataFrames) OR ctx['pkg']
           (a package directory holding catalog_sandpiper_pca_scores.parquet, catalog_sandpiper_pca_loadings.parquet,
           catalog_sandpiper_pca_variance.csv, catalog_sandpiper_sample_summary.parquet)
  out_dir  site output root (Path or str)

Writes  atlas/pca.html (root='../', nav='atlas'), data/atlas/pca_points.bin, data/atlas/pca_codes.bin,
data/atlas/pca_meta.json. Returns a dict of counts and byte sizes (also stored in the meta file).

Binary layout (little-endian, documented in the page footer):
  pca_points.bin  Float32[n_points * n_pcs], row-major: point i, component j at (i * n_pcs + j); values are PCA scores.
  pca_codes.bin   one column per categorical field, concatenated in the order of meta['fields']; each column is
                  Uint16[n_points] (study) or Uint8[n_points] (the others); meta['fields'][k]['offset'] gives the byte
                  offset and meta['fields'][k]['dtype'] the element type; code = index into meta['fields'][k]['labels'],
                  the last two labels are always 'other' (values beyond the per-field cap) and '(no value)'.
  pca_meta.json   n_points, pcs [{name, explained_variance_ratio}], fields, keys (sample keys joined by '\\n', in point
                  order), loadings (top ±12 genera per component), sizes, generated_from.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

PAGE_PATH = 'atlas/pca.html'
DATA_DIR = 'data/atlas'
N_PCS = 5
# categorical fields carried per point: (field name in wide/summary, label, cap on distinct labels, dtype)
FIELDS = [
    ('study_accession', 'Study', 65000, 'uint16'),
    ('age_category', 'Age category', 250, 'uint8'),
    ('country', 'Country', 250, 'uint8'),
    ('health_condition', 'Health condition', 250, 'uint8'),
    ('body_site_class', 'Body site class', 250, 'uint8'),
    ('top_genus', 'Top genus', 250, 'uint8'),
    ('lifestyle', 'Lifestyle', 250, 'uint8'),
]
TOP_LOADINGS = 12
MAX_TOTAL_BYTES = 15 * 1024 * 1024


def _read_inputs(ctx):
    if all(k in ctx for k in ('pca_scores', 'pca_loadings', 'pca_variance', 'sp_summary')):
        return ctx['pca_scores'], ctx['pca_loadings'], ctx['pca_variance'], ctx['sp_summary']
    pkg = Path(ctx['pkg'])
    return (pd.read_parquet(pkg / 'catalog_sandpiper_pca_scores.parquet'), pd.read_parquet(pkg / 'catalog_sandpiper_pca_loadings.parquet'),
            pd.read_csv(pkg / 'catalog_sandpiper_pca_variance.csv'), pd.read_parquet(pkg / 'catalog_sandpiper_sample_summary.parquet'))


def encode_field(values: pd.Series, cap: int):
    """Map a categorical Series to codes; the `cap` most frequent labels keep their own code, the rest -> 'other',
    missing -> '(no value)'. Returns (codes ndarray, labels list, counts list)."""
    v = values.astype('object').where(values.notna(), None)
    vc = pd.Series([x for x in v if x is not None]).value_counts() if v.notna().any() else pd.Series(dtype=int)
    keep = [str(x) for x in vc.index[:cap]]
    labels = keep + ['other', '(no value)']
    idx = {l: i for i, l in enumerate(keep)}
    other, nov = len(keep), len(keep) + 1
    codes = np.fromiter((nov if x is None else idx.get(str(x), other) for x in v), dtype=np.int64, count=len(v))
    counts = [int(vc.get(l, 0)) for l in keep] + [int((codes == other).sum()), int((codes == nov).sum())]
    return codes, labels, counts


def build(env, render, ctx, out_dir):
    out = Path(out_dir)
    scores, loadings, variance, summ = _read_inputs(ctx)
    wide = ctx['wide']
    studies = ctx.get('studies')
    pc_cols = [f'pc{i}' for i in range(1, N_PCS + 1)]
    pc_cols = [c for c in pc_cols if c in scores.columns]
    assert pc_cols, 'pca_scores needs pc1..pcN columns'
    pts = scores[['sample_key'] + pc_cols].dropna(subset=pc_cols).drop_duplicates('sample_key').reset_index(drop=True)
    meta_cols = [c for c in ('sample_key', 'study_accession', 'age_category', 'country', 'health_condition', 'body_site_class', 'lifestyle') if c in wide.columns]
    w = wide[meta_cols].drop_duplicates('sample_key')
    pts = pts.merge(w, on='sample_key', how='left')
    if 'top_genus' in summ.columns:
        pts = pts.merge(summ[['sample_key', 'top_genus']].drop_duplicates('sample_key'), on='sample_key', how='left')
    n = len(pts)
    # points
    arr = np.ascontiguousarray(pts[pc_cols].to_numpy(dtype=np.float32))
    (out / DATA_DIR).mkdir(parents=True, exist_ok=True)
    (out / 'atlas').mkdir(parents=True, exist_ok=True)
    arr.tofile(out / DATA_DIR / 'pca_points.bin')
    # codes
    fields, chunks, offset = [], [], 0
    for col, label, cap, dtype in FIELDS:
        if col not in pts.columns:
            continue
        codes, labels, counts = encode_field(pts[col], cap)
        a = codes.astype(np.dtype('<' + {'uint8': 'u1', 'uint16': 'u2'}[dtype]))
        chunks.append(a.tobytes())
        fields.append(dict(field=col, label=label, labels=labels, counts=counts, dtype=dtype, offset=offset, n_labels=len(labels)))
        offset += len(chunks[-1])
    with open(out / DATA_DIR / 'pca_codes.bin', 'wb') as fh:
        for c in chunks:
            fh.write(c)
    # study titles for tooltips (only studies present)
    titles = {}
    if studies is not None and 'study_title' in studies.columns:
        present = set(pts['study_accession'].dropna().astype(str))
        st = studies[studies['study_accession'].astype(str).isin(present)]
        titles = {str(a): (str(t)[:120] if pd.notna(t) else '') for a, t in zip(st['study_accession'], st['study_title'])}
    # explained variance + loadings
    var = variance.sort_values('pc') if 'pc' in variance.columns else variance
    pcs = [dict(name=str(r['pc']).upper(), explained_variance_ratio=float(r['explained_variance_ratio'])) for _, r in var.iterrows()][:len(pc_cols)]
    load = {}
    for c in pc_cols:
        if c in loadings.columns:
            s = loadings.set_index('genus')[c].astype(float)
            load[c.upper()] = dict(pos=[(g, round(float(v), 4)) for g, v in s.nlargest(TOP_LOADINGS).items()],
                                  neg=[(g, round(float(v), 4)) for g, v in s.nsmallest(TOP_LOADINGS).items()])
    meta = dict(n_points=int(n), n_pcs=len(pc_cols), pcs=pcs, fields=fields, keys='\n'.join(pts['sample_key'].astype(str)),
                study_titles=titles, loadings=load,
                generated_from=dict(scores='catalog_sandpiper_pca_scores.parquet', loadings='catalog_sandpiper_pca_loadings.parquet',
                                    metadata='catalog_sample_metadata_wide.parquet', summary='catalog_sandpiper_sample_summary.parquet'),
                layout=dict(points='Float32[n_points*n_pcs] row-major (point i, pc j at i*n_pcs+j), little-endian',
                            codes='per field: dtype[n_points] at byte offset `offset`; code indexes `labels`; last two labels are other and (no value)'))
    sizes = {'pca_points.bin': (out / DATA_DIR / 'pca_points.bin').stat().st_size, 'pca_codes.bin': (out / DATA_DIR / 'pca_codes.bin').stat().st_size}
    meta['sizes'] = dict(sizes)
    mp = out / DATA_DIR / 'pca_meta.json'
    mp.write_text(json.dumps(meta, separators=(',', ':')), encoding='utf-8')
    sizes['pca_meta.json'] = mp.stat().st_size
    total = sum(sizes.values())
    assert total <= MAX_TOTAL_BYTES, f'atlas PCA data files total {total} bytes > {MAX_TOTAL_BYTES}'
    n_studies = int(pts['study_accession'].nunique()) if 'study_accession' in pts else 0
    field_names = [f['field'] for f in fields]
    render('atlas_pca.html', PAGE_PATH, '../', nav='atlas',
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Atlas', href='index.html'), dict(label='PCA')],
           n_points=n, n_studies=n_studies, pcs=pcs, fields=fields, field_names=field_names, loadings=load,
           sizes={k: v for k, v in sizes.items()}, total_bytes=total, total_mb=round(total / 1048576, 1),
           n_genera=int(loadings['genus'].nunique()) if 'genus' in loadings.columns else None,
           n_samples_catalog=int(wide['sample_key'].nunique()), method=ctx.get('pca_method', {}))
    return dict(n_points=n, n_studies=n_studies, sizes=sizes, total_bytes=total, fields=field_names)
