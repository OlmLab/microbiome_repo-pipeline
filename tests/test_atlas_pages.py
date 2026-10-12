"""R2026.12 atlas (R2026.13: official HDI + observations script): precompute the payload from a synthetic 300-sample input, run
scripts/atlas_observations.py on it and build atlas/index.html + atlas/observations.html.

Runs without the real package or network: synthetic Sandpiper long tables, a synthetic wide table and a two-card
observations.json (with 1x1 PNG figures) are generated in tmp_path.
"""
import json
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

REPO = Path(__file__).resolve().parents[1]
GEN = REPO / 'site_generator' / 'gen'
sys.path.insert(0, str(GEN))
from pages import atlas  # noqa: E402

GENERA = [('g__Bifidobacterium', 'Root; d__Bacteria; p__Actinomycetota; c__Actinomycetes; o__Actinomycetales; f__Bifidobacteriaceae; g__Bifidobacterium'),
          ('g__Bacteroides', 'Root; d__Bacteria; p__Bacteroidota; c__Bacteroidia; o__Bacteroidales; f__Bacteroidaceae; g__Bacteroides'),
          ('g__Prevotella', 'Root; d__Bacteria; p__Bacteroidota; c__Bacteroidia; o__Bacteroidales; f__Bacteroidaceae; g__Prevotella'),
          ('g__Escherichia', 'Root; d__Bacteria; p__Pseudomonadota; c__Gammaproteobacteria; o__Enterobacterales; f__Enterobacteriaceae; g__Escherichia'),
          ('g__Faecalibacterium', 'Root; d__Bacteria; p__Bacillota_A; c__Clostridia; o__Oscillospirales; f__Ruminococcaceae; g__Faecalibacterium')]
COUNTRIES = ['US', 'GB', 'BD', 'CN', 'HK', 'NE']
AGES = ['neonate', 'infant', 'child', 'adult', 'elderly', 'unknown']


def synthetic(n=300, seed=0):
    rng = np.random.default_rng(seed)
    keys = [f'SAMEA{i:07d}' for i in range(n)]
    study = [f'PRJEB{1000 + i % 10}' for i in range(n)]
    country = [COUNTRIES[i % 10 % len(COUNTRIES)] if i % 17 else None for i in range(n)]   # ~6 % missing country
    age = [AGES[(i // 10) % len(AGES)] for i in range(n)]
    rows, srows = [], []
    for i, k in enumerate(keys):
        w = rng.dirichlet(np.ones(len(GENERA)) * 0.5) * 0.9
        for (g, lin), ra in zip(GENERA, w):
            if ra > 2e-5:
                rows.append(dict(sample_key=k, genus=g, lineage=lin, relabund=float(ra), coverage=float(ra * 100)))
                if ra > 1e-3:
                    srows.append(dict(sample_key=k, species=g.replace('g__', 's__') + ' sp1', lineage=lin + '; ' + g.replace('g__', 's__') + ' sp1', relabund=float(ra)))
        rows.append(dict(sample_key=k, genus='unassigned_at_genus', lineage='unassigned_at_genus', relabund=0.1, coverage=10.0))
    genus_long = pd.DataFrame(rows); species_long = pd.DataFrame(srows)
    summary = pd.DataFrame(dict(sample_key=keys, study_accession=study, body_site_class=['primary'] * (n - 5) + ['excluded'] * 5,
                                qc_non_metagenome=[False] * n, qc_synthetic=[False] * n, qc_rna=[False] * n, qc_predicted_ecological=[False] * n,
                                qc_low_depth=[i % 50 == 0 for i in range(n)], qc_no_genus_assigned=[False] * n,
                                richness_genus=rng.integers(3, 6, n), root_coverage_sum=rng.uniform(50, 3000, n), age_category=age))
    wide = pd.DataFrame(dict(sample_key=keys, country=country, study_accession=study))
    studies = pd.DataFrame(dict(study_accession=sorted(set(study)), study_title=[f'Study {i}' for i in range(10)], n_samples=[30] * 10))
    return genus_long, species_long, summary, wide, studies


def png1x1():
    raw = b'\x00\x00\x00\x00\x00'
    def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('atlas')
    genus_long, species_long, summary, wide, studies = synthetic()
    data_dir = tmp / 'payload'
    meta = atlas.precompute(genus_long, species_long, summary, wide, data_dir, n_species=3)
    obs_dir = data_dir / 'obs'; obs_dir.mkdir()
    cards = []
    for cid in ('a_test_card', 'i_exploratory_card'):
        (obs_dir / f'{cid}.png').write_bytes(png1x1()); pd.DataFrame(dict(x=[1, 2])).to_csv(obs_dir / f'{cid}.csv', index=False)
        cards.append(dict(id=cid, title=cid.replace('_', ' '), figure=f'{cid}.png', csv=f'{cid}.csv', description='desc', definition='def', confounder='conf',
                          n_samples=100, n_studies=3, numbers={}, tag='exploratory' if cid.startswith('i_') else None))
    (data_dir / 'observations.json').write_text(json.dumps(dict(meta=dict(n_samples=meta['n_samples']), cards=cards)))
    env = Environment(loader=FileSystemLoader(GEN / 'templates'), autoescape=select_autoescape(['html']))
    env.filters.update(fmt=lambda v: v, pct=lambda v: v, pct1=lambda v: v, num2=lambda v: v, numint=lambda v: f'{int(v):,}')
    sri = json.loads((GEN / 'static' / 'vendor' / 'SRI.json').read_text())
    env.globals.update(site=dict(nav=[('home','Home','index.html',None),('atlas','Atlas','atlas/index.html',None)], issue_template='', issue_label='', about=dict(lab_name='Lab', lab_url='https://example.org/lab', funder_name='Funder', funder_url='https://example.org/f'), title='T', short_title='T', tagline='', version='1.12.0-test', release_tag='data-v1.12.0', build_date='2026-10-01', sha8='deadbeef', base_url='https://example.org/',
                                 issue_repo='OlmLab/microbiome_repo', release_id='R2026.12', previous_release_id='R2026.11', release_date='2026-10-01', doi='', data_release_url=None,
                                 zenodo_badge='', zenodo_latest='', data_repo_id='0', releases_page='releases/index.html', changes_page='changes/index.html', description='', citation='',
                                 sri=sri, has_contribute=True, contribute_page='contribute/index.html', has_registry=True, registry_page='registry/index.html', data_repo='OlmLab/x'))
    out = tmp / 'site'; out.mkdir(); written = []

    def render(tpl, path, root, nav=None, crumbs=None, **ctx):
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_text(env.get_template(tpl).render(root=root, nav=nav, crumbs=crumbs, page_path=path, **ctx), encoding='utf-8'); written.append(path)
    figs = tmp / 'figs'; figs.mkdir()
    (figs / 'figure1.png').write_bytes(b'\x89PNG\r\n\x1a\n'); (figs / 'figure1_web.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    (figs / 'figures.json').write_text(json.dumps(dict(figures=[dict(n=1, title='A test figure.', caption='(a) panel a.', file='figure1.png')])))
    pages = atlas.build(env, render, dict(atlas_data_dir=data_dir, stats={}, gut_studies=studies, figures_dir=str(figs)), out)
    return dict(out=out, meta=meta, pages=pages, written=written, summary=summary, wide=wide, genus_long=genus_long)


def test_precompute_payload_shape(built):
    out, meta = built['out'], built['meta']
    d = out / 'data' / 'atlas'
    assert {'taxa.json', 'countries.json', 'studies.json', 'country_hdi.csv', 'observations.json'} <= {p.name for p in d.iterdir()}
    for r in atlas.RANKS:
        assert (d / f'matrix_{r}.bin').exists()
    tj = json.loads((d / 'taxa.json').read_text())
    # 300 - 5 excluded body site - 6 low depth (i % 50 == 0 → 6 hits) = 289
    assert meta['n_samples'] == 289 and tj['meta']['n_samples'] == 289
    assert meta['ranks_n']['genus'] == 5 and meta['ranks_n']['phylum'] == 4 and meta['ranks_n']['species'] == 3
    assert meta['n_taxa'] == len(tj['taxa']) == sum(meta['ranks_n'].values())
    assert set(meta['ranks_n']) == set(atlas.RANKS) and 'format' in tj['meta'] and 'hdi_source' in tj['meta']
    countries = json.loads((d / 'countries.json').read_text())
    assert [c['iso2'] for c in countries] == sorted(COUNTRIES)
    hk = next(c for c in countries if c['iso2'] == 'HK'); assert hk['ll'] and hk['in_map'] is False
    us = next(c for c in countries if c['iso2'] == 'US'); assert us['num'] == '840' and us['hdi'] == 0.938 and us['band'] == 'high'   # official UNDP HDR 2025 value (HDI 2023)
    ne = next(c for c in countries if c['iso2'] == 'NE'); assert ne['band'] == 'low'
    hk = next(c for c in countries if c['iso2'] == 'HK'); assert hk['hdi'] is not None and hk['band'] == 'high'   # HK is in the official table
    assert 'Report 2025' in tj['meta']['hdi_source'] and 'memory' not in tj['meta']['hdi_source'] and tj['meta']['hdi_year'] == 2023
    assert tj['meta']['hdi_n_countries_with_value'] == 6
    hdi_csv = pd.read_csv(d / 'country_hdi.csv'); assert 'hdi_2023' in hdi_csv.columns and 'hdi_2022' not in hdi_csv.columns and len(hdi_csv) == 6
    studies = json.loads((d / 'studies.json').read_text())
    assert len(studies) == 10 and all(len(s['ages']) == len(atlas.AGE_CATS) for s in studies) and studies[0]['title'] == 'Study 0'
    assert sum(s['n'] for s in studies) == 289


def test_binary_matrix_roundtrip(built):
    """Decode matrix_genus.bin and check one taxon against a direct pandas computation."""
    out, S = built['out'], built['summary']
    d = out / 'data' / 'atlas'
    tj = json.loads((d / 'taxa.json').read_text()); genus_taxa = [t for t in tj['taxa'] if t['rank'] == 'genus']
    countries = json.loads((d / 'countries.json').read_text())
    u = np.fromfile(d / 'matrix_genus.bin', dtype='<u4'); p = 0; decoded = []
    for _ in genus_taxa:
        nC, nS = int(u[p]), int(u[p + 1]); p += 2
        c = u[p:p + nC * 3].reshape(-1, 3); p += nC * 3
        s = u[p:p + nS * 3].reshape(-1, 3); p += nS * 3
        decoded.append((c, s))
    assert p == len(u), 'stream fully consumed'
    keep = atlas.analysis_samples(S).merge(built['wide'][['sample_key', 'country']], on='sample_key')
    gl = built['genus_long']; gl = gl[gl.sample_key.isin(keep.sample_key)]
    i = [t['name'] for t in genus_taxa].index('g__Prevotella')
    c, s = decoded[i]
    for cidx, n_present, mean_e6 in c:
        iso = countries[cidx]['iso2']; ks = keep.loc[keep.country == iso, 'sample_key']
        x = gl[(gl.genus == 'g__Prevotella') & gl.sample_key.isin(ks)]
        assert n_present == int((x.relabund >= atlas.PRESENCE).sum())
        assert abs(mean_e6 / 1e6 - x.relabund.sum() / len(ks)) < 2e-6
    assert (c[:, 1] > 0).all() and len(s) == 10  # every study carries Prevotella in the synthetic data
    t = genus_taxa[i]; assert t['n'] == sum(int(x[1]) for x in s) and t['parent'] == 'f__Bacteroidaceae'


def test_pages_render(built):
    out = built['out']
    assert built['pages'] == ['atlas/index.html', 'atlas/figures.html'] == built['written']
    a = (out / 'atlas' / 'index.html').read_text()
    assert 'id="map"' in a and 'static/atlas.js' in a and 'd3@7.9.0' in a and 'topojson-client@3.1.0' in a and 'integrity="sha384-' in a
    assert 'About this view' in a and 'master confounder' in a and 'Repeated measures' in a and '100 Mbp' in a
    assert '289' in a and 'class="active"' in a and 'atlas/index.html' in a  # nav link present + active
    f = (out / 'atlas' / 'figures.html').read_text()   # R2026.25: Observations replaced by the manuscript Figures page
    assert 'Figure 1. A test figure.' in f and 'figures/figure1_web.png' in f and 'href="figures/figure1.png"' in f
    assert (out / 'atlas' / 'figures' / 'figure1.png').exists()
    assert (GEN / 'static' / 'vendor' / 'countries-110m.json').exists() and 'countries-110m.json' in json.loads((GEN / 'static' / 'vendor' / 'SRI.json').read_text())


def test_build_without_observations(tmp_path):
    genus_long, species_long, summary, wide, _ = synthetic(n=60, seed=1)
    data_dir = tmp_path / 'payload'; atlas.precompute(genus_long, species_long, summary, wide, data_dir, n_species=2)
    env = Environment(loader=FileSystemLoader(GEN / 'templates'), autoescape=select_autoescape(['html']))
    env.filters.update(fmt=lambda v: v, pct=lambda v: v, pct1=lambda v: v, num2=lambda v: v, numint=lambda v: f'{int(v):,}')
    env.globals.update(site=dict(nav=[('home','Home','index.html',None),('atlas','Atlas','atlas/index.html',None)], issue_template='', issue_label='', contribute_page='', about=dict(lab_name='Lab', lab_url='https://example.org/lab', funder_name='Funder', funder_url='https://example.org/f'), title='T', short_title='T', version='x', release_tag='x', build_date='x', sha8='x', base_url='', issue_repo='', release_id='x', release_date='x', doi='',
                                 sri={}, has_contribute=False, has_registry=False, citation='', description=''))
    out = tmp_path / 'site'; out.mkdir()
    def render(tpl, path, root, **ctx):
        (out / path).parent.mkdir(parents=True, exist_ok=True); (out / path).write_text(env.get_template(tpl).render(root=root, page_path=path, **ctx))
    pages = atlas.build(env, render, dict(atlas_data_dir=data_dir), out)
    assert pages == ['atlas/index.html'] and not (out / 'atlas' / 'observations.html').exists()   # no figures dir: map page only


def test_official_hdi_table():
    """The shipped UNDP table is the HDR 2025 Statistical Annex Table 1 (HDI 2023): 193 countries, values in (0, 1], no from-memory fallback left."""
    hdi = atlas.load_hdi()
    assert len(hdi) == 193 and all(0 < v <= 1 for v in hdi.values()) and hdi['NO'] == 0.970 and hdi['US'] == 0.938 and hdi['NE'] < 0.70
    assert not hasattr(atlas, 'HDI_2022') and atlas.hdi_band(hdi['TH']) == 'middle' and atlas.hdi_band(hdi['GA']) == 'middle'
    assert atlas.hdi_band(None) is None and atlas.hdi_band(float('nan')) is None and atlas.hdi_band(0.80) == 'high' and atlas.hdi_band(0.699) == 'low'


def test_observations_script_on_synthetic(tmp_path):
    """scripts/atlas_observations.py recomputes the observation cards from a synthetic 300-sample input and yields >= 1 card with figure + CSV."""
    sys.path.insert(0, str(REPO / 'scripts'))
    import atlas_observations as ao
    genus_long, species_long, summary, wide, _ = synthetic()
    wide = wide.assign(health_condition=['healthy_control' if i % 3 else 'crohns_disease' for i in range(len(wide))], collection_year=[2015 + (i % 6) for i in range(len(wide))])
    meta, cards = ao.run(genus_long, species_long, summary, wide, None, None, tmp_path)
    assert meta['n_samples'] == 289 and meta['n_studies'] == 10 and 'Report 2025' in meta['hdi_source'] and meta['hdi_countries_with_value'] == 6
    assert len(cards) >= 1 and meta['n_cards'] == len(cards)
    obs = json.loads((tmp_path / 'observations.json').read_text()); assert [c['id'] for c in obs['cards']] == [c['id'] for c in cards]
    for c in cards:
        assert {'id', 'title', 'figure', 'csv', 'description', 'definition', 'confounder', 'n_samples', 'n_studies', 'numbers'} <= set(c)
        assert (tmp_path / 'obs' / c['figure']).read_bytes()[:4] == b'\x89PNG' and len(pd.read_csv(tmp_path / 'obs' / c['csv'])) >= 1
        assert c['n_samples'] <= 289 and 1 <= c['n_studies'] <= 10
    ids = {c['id'] for c in cards}
    assert {'a_age_trajectories', 'g_depth_vs_richness', 'i_dominant_genus_by_age'} <= ids   # computable from the synthetic fixture
    assert all(s['card'] not in ids for s in meta['skipped'])   # skipped cards are declared, not silently dropped
    # the within-study helper: identical groups give a zero median effect
    S = ao.Data(genus_long, species_long, summary, wide).S
    a = (np.arange(len(S)) % 2 == 0); b = ~a
    c, per = ao.within_study(S, a, b, np.zeros(len(S)))
    assert c['n_studies'] == 0 or c['median_effect'] == 0.0
