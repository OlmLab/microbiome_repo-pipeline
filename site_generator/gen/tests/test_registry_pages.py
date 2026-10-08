"""Scale-up S1: registry/ landing page, scope pages, registry explorer, home card, methods section.

Spec-level tests always run; built-site tests need CATALOG_SITE_DIR (a build of the MOCK 1.6.0 package) and CATALOG_PACKAGE_DIR:
    python site_generator/gen/tests/make_mock_registry_package.py --src data/inputs/data_package --out build/mock_package_1.6.0
    python site_generator/gen/build_site.py --package build/mock_package_1.6.0 --out build/site_mock --base-url https://olmlab.github.io/microbiome_repo/ --build-date 2026-10-31
    CATALOG_SITE_DIR=build/site_mock CATALOG_PACKAGE_DIR=build/mock_package_1.6.0 python -m pytest site_generator/gen/tests/test_registry_pages.py
"""
import json, os, re, sys
from html.parser import HTMLParser
from pathlib import Path
import pandas as pd
import pytest
import yaml

GEN = Path(__file__).resolve().parents[1]
REPO = GEN.parents[1]
sys.path.insert(0, str(GEN))
import build_site as bs  # noqa: E402

SSPEC = bs.read_scope_spec(REPO / 'config' / 'scope.yaml')
VOCABS = bs.read_vocabs(REPO, SSPEC)
SITE = Path(os.environ['CATALOG_SITE_DIR']) if os.environ.get('CATALOG_SITE_DIR') else None
PKG = Path(os.environ['CATALOG_PACKAGE_DIR']) if os.environ.get('CATALOG_PACKAGE_DIR') else None
needs_site = pytest.mark.skipif(SITE is None or PKG is None or not (SITE / 'registry' / 'index.html').exists(), reason='set CATALOG_SITE_DIR / CATALOG_PACKAGE_DIR to a built mock 1.6.0 site')

# The WHERE-clause shapes emitted by static/registry_explorer.js whereClause(); replayed with python duckdb on the mock table.
REGISTRY_SQL = {
    'count': 'SELECT COUNT(*) AS n, COALESCE(SUM(n_runs),0) AS r FROM registry {where}',
    'page': 'SELECT study_accession, study_title, n_runs, n_samples, body_sites, life_stages, assay, classification_stage, in_infant_catalog FROM registry {where} ORDER BY "n_samples" DESC NULLS LAST, study_accession LIMIT 50 OFFSET 0',
    'detail': "SELECT * FROM registry WHERE study_accession = '{acc}'",
    'list_filter': "(';' || COALESCE(\"{col}\", '') || ';') LIKE '%;{code};%'",
    'field_filter': '"{col}" = \'{v}\'',
    'title_search': "(study_title ILIKE '%{q}%' OR study_accession ILIKE '%{q}%')",
    'export': 'COPY (SELECT * FROM registry {where} ORDER BY study_accession) TO \'{fname}\' (HEADER, DELIMITER \',\')',
}


def test_scope_spec_shape():
    ids = [s['id'] for s in SSPEC['scopes']]
    # S0 spec: the registry scope human_all comes first, then the curated infant scope and the ten registry scopes
    assert ids == ['human_all', 'infant_gut', 'gut_child', 'gut_adult', 'oral', 'skin', 'vaginal_urogenital', 'respiratory', 'milk', 'blood_tissue', 'other_site', 'unknown_site']
    assert SSPEC['registry_columns'][:3] == ['study_accession', 'secondary_study_accession', 'study_title']
    assert SSPEC['registry_columns'][-3:] == ['release_added', 'release_retired', 'package_added']
    for c in ('host_human', 'host_evidence', 'assay', 'access', 'body_sites', 'body_site_primary', 'body_site_evidence', 'life_stages', 'life_stage_primary',
              'life_stage_evidence', 'population_flags', 'classification_stage', 'classification_confidence', 'in_infant_catalog', 'infant_reason_code', 'scope_memberships', 'universe_slice'):
        assert c in SSPEC['registry_columns'], c
    assert SSPEC['classification_stages'] == ['deterministic_prior', 'deterministic_rule', 'sonnet_x2', 'opus_adjudicated', 'pending', 'owner_decision', 'curator_audit']
    assert SSPEC['host_human_values'] == ['yes', 'no', 'mixed', 'unknown'], 'yes/no must be quoted strings in scope.yaml (YAML booleans otherwise)'
    assert SSPEC['files']['studies'] == 'registry_studies.parquet' and SSPEC['release_id'] == 'R2026.4'


def test_vocabularies():
    assert list(VOCABS['body_site']) == ['gut_stool', 'oral', 'skin', 'nasal_nasopharyngeal', 'respiratory_lower', 'vaginal_urogenital', 'milk', 'blood_tissue', 'eye_ear', 'other_site', 'unknown_site', 'multi_site']
    assert list(VOCABS['life_stage']) == ['neonate', 'infant', 'child', 'adolescent', 'adult', 'elderly', 'unknown_age', 'mixed_ages']
    assert set(VOCABS['assay']) == set(SSPEC['assay_values'])
    bs_yaml = yaml.safe_load((REPO / 'config' / 'vocab' / 'body_sites.yaml').read_text())
    assert bs_yaml['codes']['gut_stool']['uberon'][0]['id'] == 'UBERON:0001988' and 'gutierrez' in bs_yaml['codes']['gut_stool']['negative_terms']
    for code, spec in bs_yaml['codes'].items():
        for u in spec.get('uberon') or []:
            assert re.fullmatch(r'UBERON:\d{7}', u['id']), (code, u)
    ls_yaml = yaml.safe_load((REPO / 'config' / 'vocab' / 'life_stages.yaml').read_text())
    # S0 vocabulary: inclusive day bins live in age_days_bins (neonate 0-28 d, infant 29-1100 d = the catalog's 0-1,100 d window)
    assert ls_yaml['age_days_bins']['neonate'] == [0, 28] and ls_yaml['age_days_bins']['infant'] == [29, 1100]


def test_mock_builder_has_no_real_looking_synthetic_accessions():
    src = (GEN / 'tests' / 'make_mock_registry_package.py').read_text()
    assert "f'PRJMOCK{i + 1:06d}'" in src, 'synthetic rows must use PRJMOCK accessions (never INSDC-shaped)'
    assert 'signal_human_new' in src


# ---------------------------------------------------------------- built site
def _reg():
    rg = pd.read_parquet(PKG / SSPEC['files']['studies'])
    return rg[rg.release_retired.isna()]


class _Tables(HTMLParser):
    """collect (heading-less) tables as lists of rows of cell text."""
    def __init__(self):
        super().__init__(); self.tables = []; self._row = None; self._cell = None
    def handle_starttag(self, tag, attrs):
        if tag == 'table': self.tables.append([])
        elif tag == 'tr' and self.tables: self._row = []
        elif tag in ('td', 'th') and self._row is not None: self._cell = ''
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._cell is not None: self._row.append(re.sub(r'\s+', ' ', self._cell).strip()); self._cell = None
        elif tag == 'tr' and self._row is not None: self.tables[-1].append(self._row); self._row = None
    def handle_data(self, d):
        if self._cell is not None: self._cell += d


def _tables(path):
    p = _Tables(); p.feed(Path(path).read_text(encoding='utf-8')); return p.tables


def _num(s):
    return int(s.replace(',', ''))


@needs_site
def test_registry_pages_exist():
    assert (SITE / 'registry' / 'index.html').exists()
    for sc in SSPEC['scopes']:
        assert (SITE / 'registry' / 'scopes' / f"{sc['id']}.html").exists(), sc['id']
    assert (SITE / 'data' / 'registry_studies.parquet').exists()
    assert not (SITE / 'data' / 'registry_runs.parquet').exists() and not (SITE / 'data' / 'package' / 'registry_runs.parquet').exists(), 'the run table is a Release asset, never a site file'
    assert (SITE / 'static' / 'registry_explorer.js').exists()
    assert (SITE / 'registry' / 'index.html').stat().st_size < 2_000_000
    for p in (SITE / 'registry' / 'scopes').glob('*.html'):
        assert p.stat().st_size < 2_000_000, p.name


@needs_site
def test_registry_explorer_references_parquet_and_no_placeholder():
    html = (SITE / 'registry' / 'index.html').read_text(encoding='utf-8')
    assert "parquet:'../data/registry_studies.parquet'" in html
    assert 'static/registry_explorer.js' in html
    js = (SITE / 'static' / 'registry_explorer.js').read_text(encoding='utf-8')
    for shape in ('SELECT COUNT(*) AS n, COALESCE(SUM(n_runs),0) AS r FROM registry', "LIKE '%;", 'COPY (SELECT * FROM registry', 'ORDER BY study_accession'):
        assert shape in js, shape
    for p in [SITE / 'registry' / 'index.html'] + sorted((SITE / 'registry' / 'scopes').glob('*.html')):
        t = p.read_text(encoding='utf-8')
        assert 'USERNAME' not in t and 'REPOSITORY' not in t and 'https://USERNAME' not in t, p.name
        assert not re.search(r'\b(?:frame|session)\s+[0-9a-f]{8}\b', t) and '@' not in re.sub(r'<script.*?</script>', '', t, flags=re.S), f'F7 leak in {p.name}'


@needs_site
def test_summary_cards_match_table():
    rg = _reg()
    html = (SITE / 'registry' / 'index.html').read_text(encoding='utf-8')
    txt = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', re.sub(r'<script.*?</script>', '', html, flags=re.S)))
    assert f"{len(rg):,} registry studies" in txt
    assert f"{int(rg.n_runs.fillna(0).sum()):,} sequencing runs" in txt
    assert f"{int(rg.n_biosamples.fillna(0).sum()):,} BioSamples" in txt
    assert f"{int((rg.host_human == 'yes').sum()):,} host human = yes" in txt
    n_pending = int((rg.classification_stage == 'pending').sum())
    assert f"{len(rg) - n_pending:,} classified" in txt and f"{n_pending:,} pending" in txt
    assert f"{int((rg.in_infant_catalog == 'include').sum()):,} studies in the curated infant gut scope" in txt


@needs_site
def test_facet_tables_equal_pandas_groupby():
    rg = _reg()
    tables = _tables(SITE / 'registry' / 'index.html')
    by_header = {}
    for t in tables:
        if t and t[0] == ['value', 'studies', 'runs']:
            by_header.setdefault('facets', []).append(t[1:])
    facets = by_header['facets']
    assert len(facets) == 7, 'body_site_primary, life_stage_primary, assay, scope_memberships, classification_stage, host_human, access'
    def expect(col, explode=False):
        d = rg[[col, 'n_runs']].copy()
        d[col] = d[col].map(bs.split_list) if explode else d[col].map(lambda v: [] if bs.isnull(v) or v == '' else [str(v)])
        d = d.explode(col).dropna(subset=[col])
        g = d.groupby(col).agg(n_studies=('n_runs', 'size'), n_runs=('n_runs', lambda x: int(x.fillna(0).sum())))
        return {str(k): (int(r.n_studies), int(r.n_runs)) for k, r in g.iterrows()}
    for rows, (col, explode) in zip(facets, [('body_site_primary', False), ('life_stage_primary', False), ('assay', False), ('scope_memberships', True), ('classification_stage', False), ('host_human', False), ('access', False)]):
        got = {r[0].split(' ')[0]: (_num(r[1]), _num(r[2])) for r in rows}
        assert got == expect(col, explode), col
    # scope table on the landing page: studies / runs / BioSamples per scope
    scope_tbl = next(t for t in tables if t and t[0][:2] == ['scope', 'definition'])
    for row, sc in zip(scope_tbl[1:], SSPEC['scopes']):
        m = rg[rg.scope_memberships.map(lambda v: sc['id'] in bs.split_list(v))]
        assert (_num(row[2]), _num(row[3]), _num(row[4])) == (len(m), int(m.n_runs.fillna(0).sum()), int(m.n_biosamples.fillna(0).sum())), sc['id']


@needs_site
def test_scope_pages_counts_and_links():
    rg = _reg()
    included = set(pd.read_parquet(PKG / 'study_metadata_wide.parquet', columns=['study_accession']).study_accession)
    for sc in SSPEC['scopes']:
        p = SITE / 'registry' / 'scopes' / f"{sc['id']}.html"
        html = p.read_text(encoding='utf-8')
        m = rg[rg.scope_memberships.map(lambda v: sc['id'] in bs.split_list(v))]
        txt = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))
        assert f"{len(m):,} studies" in txt and f"{int(m.n_runs.fillna(0).sum()):,} runs" in txt, sc['id']
        assert f'href="../index.html?scope={sc["id"]}#explorer"' in html, 'pre-filtered explorer link'
        top = m.sort_values(['n_samples', 'study_accession'], ascending=[False, True], na_position='last').head(25)
        for acc, v in zip(top.study_accession, top.in_infant_catalog):
            assert acc in html, (sc['id'], acc)
            if v == 'include' and acc in included:
                assert f'href="../../studies/{acc}.html"' in html, (sc['id'], acc)
                assert (SITE / 'studies' / f'{acc}.html').exists()
        if sc['id'] == 'infant_gut':
            assert set(m.study_accession) == included, 'infant_gut scope must equal the included studies'


@needs_site
def test_home_nav_methods_carry_registry():
    home = (SITE / 'index.html').read_text(encoding='utf-8')
    assert 'href="registry/index.html"' in home and 'Registry: all human shotgun metagenomes' in home
    rg = _reg()
    assert f'{len(rg):,}' in home
    base_nav = (SITE / 'universe.html').read_text(encoding='utf-8')
    assert re.search(r'<a href="registry/index.html"[^>]*>Registry</a>', base_nav), 'nav link'
    methods = (SITE / 'methods.html').read_text(encoding='utf-8')
    assert 'id="registry"' in methods and 'UBERON:0001988' in methods and 'deterministic_prior' in methods and 'config/vocab/life_stages.yaml' in methods
    sidx = json.loads((SITE / 'search_index.json').read_text(encoding='utf-8'))
    assert {e['id'] for e in sidx if e['t'] == 'scope'} == {sc['id'] for sc in SSPEC['scopes']}


@needs_site
def test_explorer_sql_replays_with_duckdb():
    duckdb = pytest.importorskip('duckdb')
    rg = _reg()
    con = duckdb.connect()
    con.execute(f"CREATE VIEW registry AS SELECT * FROM read_parquet('{(SITE / 'data' / 'registry_studies.parquet').as_posix()}')")
    n, r = con.execute(REGISTRY_SQL['count'].format(where='')).fetchone()
    assert (int(n), int(r)) == (len(rg), int(rg.n_runs.fillna(0).sum()))
    # scope + body-site + stage filters as the explorer builds them (pre-filtered scope link: ?scope=oral)
    where = 'WHERE ' + ' AND '.join([REGISTRY_SQL['list_filter'].format(col='scope_memberships', code='oral'),
                                     REGISTRY_SQL['list_filter'].format(col='body_sites', code='oral'),
                                     REGISTRY_SQL['field_filter'].format(col='classification_stage', v='sonnet_x2')])
    n, _ = con.execute(REGISTRY_SQL['count'].format(where=where)).fetchone()
    exp = rg[rg.scope_memberships.map(lambda v: 'oral' in bs.split_list(v)) & rg.body_sites.map(lambda v: 'oral' in bs.split_list(v)) & (rg.classification_stage == 'sonnet_x2')]
    assert int(n) == len(exp)
    rows = con.execute(REGISTRY_SQL['page'].format(where=where)).fetchall()
    assert len(rows) == min(50, len(exp)) and all(len(row) == 9 for row in rows)
    if len(exp):
        assert rows[0][3] == exp.n_samples.max()
    # title search + detail + export
    n, _ = con.execute(REGISTRY_SQL['count'].format(where='WHERE ' + REGISTRY_SQL['title_search'].format(q='saliva'))).fetchone()
    assert int(n) == int((rg.study_title.fillna('').str.contains('saliva', case=False) | rg.study_accession.str.contains('saliva', case=False)).sum())
    acc = rg.sort_values('n_samples', ascending=False).study_accession.iloc[0]
    det = con.execute(REGISTRY_SQL['detail'].format(acc=acc)).fetchdf()
    assert len(det) == 1 and list(det.columns) == list(SSPEC['registry_columns'])
    for col in ('host_evidence', 'body_site_evidence', 'life_stage_evidence'):
        ev = json.loads(det[col].iloc[0] or '[]')
        assert isinstance(ev, list) and all(set(e) >= {'source', 'quote'} and len(str(e['quote']).split()) <= 12 for e in ev), col
    out = SITE.parent / 'registry_slice_test.csv'
    con.execute(REGISTRY_SQL['export'].format(where=where, fname=out.as_posix()))
    exported = pd.read_csv(out)
    assert len(exported) == len(exp) and list(exported.columns) == list(SSPEC['registry_columns'])
    out.unlink()
