"""R2026.1 site history views: releases/, changes/, footer release line, Cite box, explorer value timeline SQL.

Two layers:
  * spec-level tests (always run): config/releases.yaml shape, release ordering, Zenodo URL construction, templates reference no placeholder URL.
  * built-site tests (run when CATALOG_SITE_DIR points at a build and CATALOG_PACKAGE_DIR at the package it was built from):
      python site_generator/gen/tests/make_mock_release_package.py --src data/inputs/data_package --out build/mock_package_1.3.0
      cd site_generator/gen && python build_site.py --package ../../build/mock_package_1.3.0 --out ../../build/site --base-url https://microbiomerepo.com/ --build-date 2026-09-26
      CATALOG_SITE_DIR=build/site CATALOG_PACKAGE_DIR=build/mock_package_1.3.0 python -m pytest site_generator/gen/tests/test_release_pages.py
"""
import json, os, re, sys
from pathlib import Path
import pytest
import yaml

GEN = Path(__file__).resolve().parents[1]
REPO = GEN.parents[1]
sys.path.insert(0, str(GEN))
import build_site as bs

SPEC = yaml.safe_load((REPO / 'config' / 'releases.yaml').read_text())
SITE = Path(os.environ['CATALOG_SITE_DIR']) if os.environ.get('CATALOG_SITE_DIR') else None
PKG = Path(os.environ['CATALOG_PACKAGE_DIR']) if os.environ.get('CATALOG_PACKAGE_DIR') else None
needs_site = pytest.mark.skipif(SITE is None or PKG is None or not (SITE / 'index.html').exists(), reason='set CATALOG_SITE_DIR and CATALOG_PACKAGE_DIR to a built mock site')


def test_release_spec_shape():
    assert SPEC['columns']['release_added'] == 'release_added' and SPEC['columns']['release_retired'] == 'release_retired' and SPEC['columns']['package_added'] == 'package_added'
    assert SPEC['release_id']['first_numbered'] == 'R2026.1'
    assert SPEC['release_id']['historical'] == ['1.0.0', '1.1.0', '1.2.0', '1.2.1', '1.2.2']
    # fact_tables entries are dicts ({file, key, default_release_added, ...}) in the data-track spec; plain names in the mock
    ft = [t['file'] if isinstance(t, dict) else t for t in SPEC['fact_tables']]
    assert 'sample_determinations.parquet' in ft and 'sample_metadata_wide.parquet' not in ft
    assert SPEC['files']['determinations_all'] == 'sample_determinations_all.parquet' and SPEC['files']['registry'] == 'releases.csv'
    assert SPEC['registry_columns'][0] == 'release_id' and 'doi' in SPEC['registry_columns'] and SPEC['registry_columns'][-1] == 'notes_file'
    assert SPEC['site_pages']['releases_index'] == 'releases/index.html' and SPEC['site_pages']['changes_release'] == 'changes/{release_id}.html'


def test_release_order_key():
    key = bs.release_order_key(SPEC)
    ids = ['R2027.1', '1.2.2', 'R2026.10', '1.0.0', 'R2026.2', '1.2.0']
    assert sorted(ids, key=key) == ['1.0.0', '1.2.0', '1.2.2', 'R2026.2', 'R2026.10', 'R2027.1']


def test_data_repo_id_and_zenodo_urls():
    rid = bs.read_data_repo_id(REPO / 'config' / 'site.yaml')
    assert rid == 1389758854  # GET https://api.github.com/repos/OlmLab/microbiome_repo-data -> id
    assert f'https://zenodo.org/badge/{rid}.svg' and f'https://zenodo.org/badge/latestdoi/{rid}'


def test_templates_have_no_placeholder_url_and_reference_release_id():
    base, _ = bs.read_base_url(REPO / 'config' / 'site.yaml')
    assert 'USERNAME' not in base
    for t in ['_cite.html', 'downloads.html', 'changes_index.html', 'changes_release.html', 'base.html']:   # releases.html merged into downloads.html (1.12.0)
        txt = (GEN / 'templates' / t).read_text()
        assert 'USERNAME.github.io' not in txt and 'REPOSITORY' not in txt
    assert 'site.release_id' in (GEN / 'templates' / 'base.html').read_text()
    assert 'zenodo_badge' in (GEN / 'templates' / '_cite.html').read_text() and 'pending Zenodo' in (GEN / 'templates' / '_cite.html').read_text()
    for u in bs.read_upstream(REPO / 'config' / 'site.yaml') if hasattr(bs, 'read_upstream') else []:
        assert u['url'].startswith('https://')


def test_explorer_timeline_wiring():
    pytest.skip('explorer value-history timeline removed in site v3 (1.10.0); value/verdict history is rendered on study pages')
    js = (GEN / 'static' / 'explorer.js').read_text()
    assert 'ensureSda' in js and 'CFG.sdall' in js and 'release_retired' in js
    tpl = (GEN / 'templates' / 'explorer.html').read_text()
    assert "sdall:'../data/{{ sda_name }}'" in tpl and 'releaseCols' in tpl
    cl = (GEN / 'check_links.py').read_text()
    assert 'sdall' in cl  # the link checker verifies the on-demand file exists


# ---------- built-site layer ----------
@needs_site
def test_new_pages_exist_and_footer_shows_release_id():
    vj = json.loads((PKG / 'VERSION.json').read_text())
    rid = vj['release_id']
    for p in ['releases/index.html', 'changes/index.html', f'changes/{rid}.html', 'data/sample_determinations_all.parquet', f'releases/RELEASE_NOTES_{rid}.md']:
        assert (SITE / p).exists(), p
    for page in ['index.html', 'releases/index.html', 'studies/index.html', 'samples/index.html']:
        t = (SITE / page).read_text()
        assert f'release <a' in t and f'>{rid}</span>' in t and f'package <span class="mono">{vj["package_version"]}</span>' in t and 'build <span class="mono">' in t, page
        assert f'<meta name="catalog-release-id" content="{rid}">' in t
        assert 'USERNAME.github.io' not in t
    idx = (SITE / 'index.html').read_text()
    assert 'zenodo.org/badge/1389758854.svg' in idx and 'zenodo.org/badge/latestdoi/1389758854' in idx
    assert f'release {rid} (data package {vj["package_version"]}), OlmLab, ' in idx


@needs_site
def test_releases_page_lists_every_registry_row_and_changes_pages_exist():
    import pandas as pd
    reg = pd.read_csv(PKG / 'releases.csv', dtype=str, keep_default_na=False)
    t = (SITE / 'releases/index.html').read_text()
    for r in reg.itertuples(index=False):
        assert f'id="rel-{r.release_id}"' in t, r.release_id
        assert (SITE / 'changes' / f'{r.release_id}.html').exists(), r.release_id
        if r.data_tag:
            assert f'https://github.com/OlmLab/microbiome_repo-data/releases/tag/{r.data_tag}' in t
        if not r.doi:
            assert 'pending' in t
    ci = (SITE / 'changes/index.html').read_text()
    assert all(f'>{r}</a>' in ci for r in reg.release_id)
    for p in (SITE / 'changes').glob('*.html'):
        assert p.stat().st_size < 2_000_000, p


@needs_site
def test_changes_counts_match_tables():
    import pandas as pd
    C = SPEC['columns']
    sda = pd.read_parquet(PKG / SPEC['files']['determinations_all'], columns=[C['release_added'], C['release_retired']])
    uni = pd.read_parquet(PKG / 'universe_studies_all.parquet', columns=[C['release_added']])
    for rid in pd.read_csv(PKG / 'releases.csv', dtype=str, keep_default_na=False).release_id:
        n_add = int((sda[C['release_added']].astype(str) == rid).sum())
        n_ret = int((sda[C['release_retired']].astype(str) == rid).sum())
        n_v = int((uni[C['release_added']].astype(str) == rid).sum())
        t = (SITE / 'changes' / f'{rid}.html').read_text()
        if n_add == 0 and n_ret == 0 and n_v == 0:
            assert 'Nothing changed in this release' in t, rid
        else:
            nums = re.findall(r'<div class="num">([^<]*)</div><div class="lbl">([^<]*)', t)
            d = {lbl.split(' (')[0]: int(n.replace(',', '')) for n, lbl in nums}
            assert d['determinations added'] == n_add and d['determinations retired'] == n_ret and d['study verdict rows added'] == n_v, (rid, d)


@needs_site
def test_value_timeline_sql_replays_in_duckdb():
    """The explorer's timeline SQL (static/explorer.js) replayed with the python duckdb package against the package table."""
    import duckdb
    C = SPEC['columns']
    js = (GEN / 'static' / 'explorer.js').read_text()
    m = re.search(r'conn\.query\(`(SELECT field_name, value_normalized, field_value[^`]+FROM sdall[^`]+)`\)', js)
    assert m, 'timeline SQL not found in explorer.js'
    sql = m.group(1)
    for k, v in dict(added=C['release_added'], retired=C['release_retired'], reason=C['retired_reason'], stage=C['retired_change_stage']).items():
        sql = sql.replace('${RC.%s}' % k, v)
    con = duckdb.connect()
    con.execute(f"CREATE VIEW sdall AS SELECT * FROM read_parquet('{(PKG / SPEC['files']['determinations_all']).as_posix()}')")
    key = con.execute(f"SELECT sample_key FROM sdall WHERE {C['release_retired']} IS NOT NULL ORDER BY sample_key LIMIT 1").fetchone()[0]
    rows = con.execute(sql.replace("${esc(key)}", key.replace("'", "''"))).fetchall()
    assert rows, 'the timeline must return rows for a sample with a retired value'
    cols = [d[0] for d in con.description]
    assert cols[:2] == ['field_name', 'value_normalized'] and 'release_added' in cols and 'release_retired' in cols and 'retired_reason' in cols
    ri = cols.index('release_retired')
    # within each field: current (NULL release_retired) rows come first
    by_field = {}
    for r in rows:
        by_field.setdefault(r[0], []).append(r[ri] is None)
    for f, flags in by_field.items():
        assert flags == sorted(flags, reverse=True), f'current rows must precede retired rows for {f}'
    n_ret = sum(1 for r in rows if r[ri] is not None)
    assert n_ret >= 1


@needs_site
def test_study_page_has_verdict_timeline():
    import pandas as pd
    st = pd.read_parquet(PKG / 'study_metadata_wide.parquet', columns=['study_accession'])
    acc = sorted(st.study_accession)[0]
    t = (SITE / 'studies' / f'{acc}.html').read_text()
    assert 'Verdict timeline across releases' in t and 'Decision history' in t
    assert re.search(r'id="timeline".*?<td class="mono"><a href="\.\./changes/[^"]+\.html">', t, re.S)
