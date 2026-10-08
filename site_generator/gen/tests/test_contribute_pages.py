"""R2026.2 contribute/ worklist page, 'Help complete this study' panel, home stat card, issue_url construction.

Spec-level tests always run; built-site tests need CATALOG_SITE_DIR (a build of the MOCK 1.4.0 package) and CATALOG_PACKAGE_DIR:
    python site_generator/gen/tests/make_mock_contribute_package.py --src data/inputs/data_package --out build/mock_package_1.4.0
    python site_generator/gen/build_site.py --package build/mock_package_1.4.0 --out build/site_mock --base-url https://olmlab.github.io/microbiome_repo/ --build-date 2026-10-24
    CATALOG_SITE_DIR=build/site_mock CATALOG_PACKAGE_DIR=build/mock_package_1.4.0 python -m pytest site_generator/gen/tests/test_contribute_pages.py
"""
import json, os, re, sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote
import pandas as pd
import pytest
import yaml

GEN = Path(__file__).resolve().parents[1]
REPO = GEN.parents[1]
sys.path.insert(0, str(GEN))
import build_site as bs  # noqa: E402

CSPEC = bs.read_contribute_spec(REPO / 'config' / 'contribute.yaml')
SITE = Path(os.environ['CATALOG_SITE_DIR']) if os.environ.get('CATALOG_SITE_DIR') else None
PKG = Path(os.environ['CATALOG_PACKAGE_DIR']) if os.environ.get('CATALOG_PACKAGE_DIR') else None
needs_site = pytest.mark.skipif(SITE is None or PKG is None or not (SITE / 'contribute' / 'index.html').exists(), reason='set CATALOG_SITE_DIR / CATALOG_PACKAGE_DIR to a built mock 1.4.0 site')


def test_contribute_spec_shape():
    assert set(CSPEC['blocker_codes']) == {'no_linked_paper', 'paywalled_abstract_only', 'no_supplement_found', 'tables_unjoinable_need_key', 'pdf_only_supplement',
                                            'controlled_access', 'archive_only_uncertain', 'unitless_age_needs_curator', 'partial_coverage', 'complete'}
    assert list(CSPEC['contribution_types']) == ['per_sample_table', 'id_key', 'paper_pointer', 'age_schedule', 'verdict_evidence']
    assert list(CSPEC['fields']) == ['age', 'delivery', 'feeding', 'preterm', 'antibiotics', 'probiotic']
    assert CSPEC['field_weights'] == {'age': 3, 'delivery': 2, 'feeding': 2, 'preterm': 1.5, 'antibiotics': 1, 'probiotic': 0.5}
    assert CSPEC['worklist_columns'][:3] == ['rank', 'study_accession', 'study_title'] and CSPEC['worklist_columns'][-3:] == ['release_added', 'release_retired', 'package_added']
    assert 'issue_url' in CSPEC['worklist_columns'] and 'priority_score' in CSPEC['worklist_columns']
    assert CSPEC['fields_columns'] == ['study_accession', 'field', 'coverage', 'n_with_value', 'n_catalog_scope', 'best_tier', 'blocker_code', 'evidence', 'release_added', 'release_retired', 'package_added']


def test_issue_form_ids_match_config():
    form = yaml.safe_load((REPO / '.github' / 'ISSUE_TEMPLATE' / 'catalog-contribution.yml').read_text())
    assert form['name'] == 'Catalog contribution' and form['labels'] == [CSPEC['issue_form']['label']]
    ids = [b.get('id') for b in form['body'] if b.get('id')]
    for fid in CSPEC['issue_form']['field_ids']:
        assert fid in ids, fid
    by_id = {b.get('id'): b for b in form['body'] if b.get('id')}
    assert by_id['contribution_type']['type'] == 'dropdown' and by_id['contribution_type']['attributes']['options'] == list(CSPEC['contribution_types'])
    assert by_id['source']['attributes']['options'] == CSPEC['issue_form']['sources']
    assert by_id['licence']['type'] == 'checkboxes' and by_id['licence']['attributes']['options'][0]['required'] is True
    for req in ('study_accession', 'contribution_type', 'source'):
        assert by_id[req]['validations']['required'] is True
    assert 'user-attachments' in by_id['attachment']['attributes']['value'] and 'e-mail' in by_id['attachment']['attributes']['value']


def test_issue_url_builder():
    u = bs.contribute_issue_url(CSPEC, 'PRJNA294605', 'id_key', 'R2026.2')
    p = urlparse(u)
    assert p.scheme == 'https' and p.netloc == 'github.com' and p.path == '/OlmLab/microbiome_repo/issues/new'
    q = parse_qs(p.query)
    # R2026.17: one simple form (share-metadata.yml) prefilled with the accession and release
    assert q['template'] == ['share-metadata.yml'] and q['labels'] == ['contribution'] and q['accession'] == ['PRJNA294605']
    assert q['release_id'] == ['R2026.2'] and q['title'] == ['[metadata] PRJNA294605']
    assert 'USERNAME' not in u and 'REPOSITORY' not in u


def test_templates_reference_contribute():
    base = (GEN / 'templates' / 'base.html').read_text()
    assert 'site.contribute_page' in base and 'Contribute' in base
    # R2026.16: Contribute is a top-level nav entry (base.html NAV), the minimalist home has no card for it
    study = (GEN / 'templates' / 'study.html').read_text()
    assert '{% if help %}' in study and 'help.issue_url' in study   # 1.12.0: the help block lost its id/heading; the issue link remains
    ct = (GEN / 'templates' / 'contribute.html').read_text()
    # R2026.17: three plain steps + one 'Share metadata' button per project (contribution types live in the form's dropdown)
    assert 'id="q"' in ct and 'Share metadata' in ct and 'r.issue_url' in ct   # 1.12.0: the worklist is a card list with one text search; licence text lives on About/Sources
    assert 'USERNAME.github.io' not in ct and 'REPOSITORY' not in ct


@needs_site
def test_contribute_page_rows_and_buttons():
    wl = pd.read_csv(PKG / CSPEC['files']['worklist'])
    html = (SITE / 'contribute' / 'index.html').read_text(encoding='utf-8')
    assert (SITE / 'contribute' / 'index.html').stat().st_size < 2_000_000
    rows = re.findall(r'<tr data-blocker="([^"]+)" data-fields="([^"]*)"', html)
    assert len(rows) == len(wl), (len(rows), len(wl))
    assert html.count('class="btn xs contribute"') == len(wl)
    urls = re.findall(r'class="btn xs contribute" href="([^"]+)"', html)
    assert len(urls) == len(wl)
    for u in urls[:50]:
        u = u.replace('&amp;', '&')
        q = parse_qs(urlparse(u).query)
        assert q['template'] == ['catalog-contribution.yml'] and q['study_accession'][0].startswith('PRJ') and q['contribution_type'][0] in CSPEC['contribution_types']
        assert q['release_tag'] == [wl.release_added.iloc[0]]
    for acc in wl.study_accession.head(20):
        assert acc in html
    assert 'USERNAME' not in html and 'REPOSITORY' not in html and '@' not in re.sub(r'https?://\S+', '', html.split('<main>')[1].split('</main>')[0]).replace('&#64;', '')
    for code in set(wl.blocker_code):
        assert f'<option value="{code}">' in html
    j = json.loads((SITE / 'data' / 'contribute_worklist.json').read_text())
    assert len(j) == len(wl) and [r['acc'] for r in j] == wl.sort_values('rank').study_accession.tolist()
    n_open = re.search(r'<h1>Contribute: ([\d,]+) studies need metadata</h1>', html).group(1).replace(',', '')
    assert int(n_open) == len(wl)


@needs_site
def test_study_panel_only_for_worklist_studies():
    wl = pd.read_csv(PKG / CSPEC['files']['worklist'])
    st = pd.read_parquet(PKG / 'study_metadata_wide.parquet', columns=['study_accession'])
    in_wl = [a for a in wl.sort_values('rank').study_accession if (SITE / 'studies' / f'{a}.html').exists()]
    complete = sorted(set(st.study_accession) - set(wl.study_accession))
    assert in_wl and complete, 'the mock package must contain both worklist and complete studies'
    acc = in_wl[0]
    h = (SITE / 'studies' / f'{acc}.html').read_text(encoding='utf-8')
    row = wl[wl.study_accession == acc].iloc[0]
    assert row.issue_url.replace('&', '&amp;') in h
    assert f'#{int(row["rank"])}' in h
    for a in complete:
        assert 'help-complete' not in (SITE / 'studies' / f'{a}.html').read_text(encoding='utf-8')


@needs_site
def test_home_card_and_nav():
    wl = pd.read_csv(PKG / CSPEC['files']['worklist'])
    home = (SITE / 'index.html').read_text(encoding='utf-8')
    assert 'href="contribute/index.html"' in home
    for p in ['studies/index.html', 'methods.html', 'releases/index.html']:
        t = (SITE / p).read_text(encoding='utf-8')
        assert re.search(r'href="(\.\./)?contribute/index.html"[^>]*>Contribute</a>', t), p
    assert 'contribute/index.html' in (SITE / 'sitemap.xml').read_text()
