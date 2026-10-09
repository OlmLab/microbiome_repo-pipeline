"""Site generator v3 — R2026.12 restructure (owner's site review 2026-09-29).

Builds the site from a TINY synthetic package (two variants: with and without the new gut-pack columns collection_date /
location_* / latitude / longitude / lifyle) and asserts the restructure items: nav order, redirects, wording, registry human
count (yes + mixed), contribute worklist driven by config/packs/gut.yaml core_fields, sample links, About block from
config/site.yaml, sequencing block from gut_runs.parquet, column-presence-tolerant explorer / fields / study pages.
Nothing here is a curated value: every synthetic row is labelled SYNTHETIC and accessions are PRJTEST… / SYN… shapes.
"""
import html as htmlmod, json, os, re, subprocess, sys
from pathlib import Path
import pandas as pd
import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
GEN = REPO / 'site_generator' / 'gen'
sys.path.insert(0, str(GEN))
import build_site as bs  # noqa: E402

PACK = yaml.safe_load((REPO / 'config' / 'packs' / 'gut.yaml').read_text())
SCOPE = yaml.safe_load((REPO / 'config' / 'scope.yaml').read_text())
RSPEC = yaml.safe_load((REPO / 'config' / 'releases.yaml').read_text())
SITE_CFG = yaml.safe_load((REPO / 'config' / 'site.yaml').read_text())
CORE, KEY = PACK['core_fields'], PACK['key_fields']
RID, VER, DATE = 'R2026.12', '1.12.0', '2026-09-30'
NEW_COLS = ['collection_date', 'location_region', 'location_locality', 'location_site', 'latitude', 'longitude', 'lifestyle', 'lifestyle_detail']


def _studies():
    # A: 60 samples, poor coverage on 3 core fields (→ contribute worklist); B: 12 samples, full coverage (→ not in worklist)
    return [dict(acc='PRJTEST000001', n=60, poor=True, title='SYNTHETIC study A: adult stool metagenomes'),
            dict(acc='PRJTEST000002', n=12, poor=False, title='SYNTHETIC study B: infant stool metagenomes')]


def make_package(out: Path, new_cols: bool):
    out.mkdir(parents=True, exist_ok=True)
    RA, RR, PA = RSPEC['columns']['release_added'], RSPEC['columns']['release_retired'], RSPEC['columns']['package_added']
    studies = _studies()
    wide, det, runs = [], [], []
    for st in studies:
        for i in range(st['n']):
            key = f"SAMN{9000000 + i:08d}" if st['acc'].endswith('1') else (f"SAMEA{8000000 + i:07d}" if i % 2 else f"SAMD{700000 + i:08d}")
            poor = st['poor'] and i >= 6            # study A: only 6/60 samples carry the poor fields → coverage 10 % < threshold
            row = dict(sample_key=key, study_accession=st['acc'], biosample_accession=key, secondary_sample=None, sample_unit='biosample', body_site_code='gut_stool',
                       sample_life_stage=None, curated_source='gut_all' if st['poor'] else 'infant_catalog', in_infant_catalog='not_screened' if st['poor'] else 'include',
                       age_at_collection_days=(None if poor else (12000.0 if st['poor'] else 90.0)), age_at_collection_days__confidence=0.9, age_at_collection_days__route='R1',
                       sex=('female' if i % 2 else 'male'), sex__confidence=0.9, sex__route='R1', bmi=None, bmi__confidence=None, bmi__route=None,
                       country=(None if poor else 'US'), country__confidence=0.9, country__route='R1',
                       health_condition=(None if poor else 'healthy'), health_condition__confidence=0.9, health_condition__route='R1', health_condition_detail=None,
                       health_condition_detail__confidence=None, health_condition_detail__route=None, antibiotic_exposure='no', antibiotic_exposure__confidence=0.8, antibiotic_exposure__route='R2',
                       subject_id=f'SUBJ{i}', subject_id__confidence=0.9, subject_id__route='R1', timepoint_label=None, timepoint_label__confidence=None, timepoint_label__route=None,
                       delivery_mode=(None if st['poor'] else 'vaginal'), feeding_mode=None, preterm_status=None, gestational_age_weeks=None, birth_weight_grams=None, maternal_antibiotics=None,
                       probiotic_exposure=None, hmo_supplementation=None, nec_status=None, age_category=('adult' if st['poor'] else 'infant'), age_category_basis='numeric_age',
                       body_site_class='primary', body_site_basis='attribute', infant_scope=not st['poor'], n_fields_with_value=(2 if poor else 5))
            if new_cols:
                row.update(collection_date=('2015-03' if i % 3 else '2016'), collection_date__route='R1', collection_date__confidence=0.9,
                           location_region='Colorado', location_locality='Boulder', location_site=('SYNTHETIC clinic' if i % 2 else None),
                           latitude=40.015, longitude=-105.2705, latitude__route='R1', latitude__confidence=0.9, longitude__route='R1', longitude__confidence=0.9,
                           lifestyle=('athlete' if i % 4 == 0 else None), lifestyle__route='R3', lifestyle__confidence=0.7, lifestyle_detail=('SYNTHETIC rugby players' if i % 4 == 0 else None))
            row.update({RA: RID, RR: None, PA: RID})
            wide.append(row)
            for f in ('sex', 'subject_id', 'antibiotic_exposure') + (() if poor else ('age_at_collection_days', 'country', 'health_condition')):
                det.append(dict(sample_key=key, field_name=f, study_accession=st['acc'], field_value=str(row[f]), value_normalized=str(row[f]), confidence=0.9, evidence_source='sample.attr.synthetic',
                                evidence_locator=f, evidence_quote='SYNTHETIC quote', evidence_limited_to_abstract=0, determined_by='test', route=row[f + '__route'], scope='sample', parse_note=None,
                                group_audit=None, src_track='test', **{RA: RID, RR: None, PA: RID}))
            runs.append(dict(run_accession=f'SRR{7000000 + len(runs):07d}', study_accession=st['acc'], sample_accession=key, secondary_sample_accession=None, experiment_accession=None, library_name=None,
                             library_strategy='WGS', library_source='METAGENOMIC', library_layout=('PAIRED' if i % 3 else 'SINGLE'), instrument_platform='ILLUMINA', instrument_model='Illumina NovaSeq 6000',
                             read_count=str(1000000 * (i + 1)), base_count=str(150000000 * (i + 1)), first_public=f'2020-01-{(i % 28) + 1:02d}', sandpiper_profiled=bool(i % 2), sample_key=key))
    cw = pd.DataFrame(wide)
    cd = pd.DataFrame(det)
    cw.to_parquet(out / 'gut_sample_metadata_wide.parquet', index=False)
    cd.to_parquet(out / 'gut_sample_determinations.parquet', index=False)
    pd.DataFrame(runs).to_parquet(out / 'gut_runs.parquet', index=False)
    cs_rows = []
    for st in studies:
        g = cw[cw.study_accession == st['acc']]
        r = dict(study_accession=st['acc'], secondary_study_accession=None, study_title=st['title'], description='SYNTHETIC description', description_short='SYNTHETIC', center_name='SYNTHETIC CENTRE',
                 first_public_min='2020-01-01', first_public_max='2020-01-28', n_runs=len(g), n_samples=len(g), n_biosamples=len(g), n_runs_sandpiper=int(len(g) // 2), host_human='yes',
                 body_sites='gut_stool', life_stages=('adult' if st['poor'] else 'infant'), life_stage_primary=('adult' if st['poor'] else 'infant'), assay='shotgun_dna', access='open',
                 top_country='US', linked_pmids=None, n_linked_papers=0, is_infant_curated=not st['poor'], n_samples_curated=len(g), curated_depth='R1' if st['poor'] else 'R1+R2',
                 curated_source='gut_all' if st['poor'] else 'infant_catalog', health_context=None,
                 age_categories=json.dumps({'adult' if st['poor'] else 'infant': len(g)}), health_conditions=json.dumps({'healthy': int(g.health_condition.notna().sum())}))
        for f in list(PACK['fields']) + NEW_COLS:
            r[f'cov_{f}'] = float(g[f].notna().mean()) if f in g.columns else 0.0
        r.update({RA: RID, RR: None, PA: RID})
        cs_rows.append(r)
    pd.DataFrame(cs_rows).to_parquet(out / 'gut_studies.parquet', index=False)
    # registry: the two catalog studies + one per other host_human value (yes counted with mixed = "human")
    reg_rows = []
    hosts = [('PRJTEST000001', 'yes'), ('PRJTEST000002', 'yes'), ('PRJTEST000003', 'mixed'), ('PRJTEST000004', 'unknown'), ('PRJTEST000005', 'no'), ('PRJTEST000006', 'no')]
    for acc, hh in hosts:
        r = {c: None for c in SCOPE['registry_columns']}
        r.update(study_accession=acc, study_title=f'SYNTHETIC registry study {acc} ({hh})', description_short='SYNTHETIC', center_name='SYNTHETIC CENTRE', first_public_min='2020-01-01', first_public_max='2020-01-28',
                 n_runs=10, n_samples=10, n_biosamples=10, n_runs_sandpiper=5, library_strategies='WGS', library_sources='METAGENOMIC', instrument_platforms='ILLUMINA', scientific_names_top='human gut metagenome',
                 host_tax_ids='9606', n_runs_host_9606=10, n_runs_nonhuman_host=0, human_signal=True, human_signal_rule='host_taxon', ambiguous=False, host_human=hh,
                 host_evidence=json.dumps([{'source': 'sample.attr.host', 'quote': 'Homo sapiens'}]), assay='shotgun_dna', access='open', body_sites='gut_stool', body_site_primary='gut_stool',
                 body_site_evidence=json.dumps([{'source': 'study.title', 'quote': 'stool metagenomes'}]), life_stages='adult', life_stage_primary='adult', life_stage_evidence=json.dumps([]),
                 population_flags=None, health_context=None, classification_stage='deterministic_rule', classification_confidence=0.9, classification_model=None,
                 in_infant_catalog='not_screened', infant_reason_code=None, scope_memberships='human_all;gut_adult' if hh in ('yes', 'mixed') else '', universe_slice='synthetic',
                 n_biosamples_harvested=10, n_biosamples_with_site=10, n_biosamples_with_age=5, n_biosamples_with_sex=5, sample_body_sites='gut_stool', sample_life_stages='adult', sample_countries='US',
                 sample_age_days_median=12000.0)
        r.update({RA: RID, RR: None, PA: RID})
        reg_rows.append(r)
    pd.DataFrame(reg_rows)[SCOPE['registry_columns']].to_parquet(out / 'registry_studies.parquet', index=False)
    reg = pd.DataFrame([dict(release_id=RID, package_version=VER, release_date=DATE, data_tag=f'data-v{VER}', site_tag=f'site-v{VER}', doi='', notes_file=f'RELEASE_NOTES_{RID}.md')],
                       columns=RSPEC['registry_columns']).fillna('')
    reg.to_csv(out / RSPEC['files']['registry'], index=False)
    pd.DataFrame(dict(slice=['synthetic'], archive_count=[6], rows_pulled=[6], complete=[True])).to_csv(out / SCOPE['files']['audit'], index=False)
    pd.DataFrame(dict(biosample_accession=cw.sample_key, study_accession=cw.study_accession, body_site_code='gut_stool', life_stage=cw.age_category, sex=cw.sex, country=cw.country, **{RA: RID, RR: None, PA: RID})).to_parquet(out / 'registry_biosamples.parquet', index=False)
    (out / f'RELEASE_NOTES_{RID}.md').write_text(f'# Release notes — {RID} (SYNTHETIC test package)\n', encoding='utf-8')
    (out / 'README.md').write_text(f'# SYNTHETIC data package v{VER}\n', encoding='utf-8')
    (out / 'DATA_DICTIONARY.md').write_text('# Data dictionary (SYNTHETIC)\n\n## Field vocabularies\n\n| `sex` | female, male |\n', encoding='utf-8')
    (out / 'CHANGELOG.md').write_text('# Changelog (SYNTHETIC)\n', encoding='utf-8')
    tables = {f.name: dict(rows=None, sha256='') for f in out.iterdir() if f.is_file()}
    (out / 'VERSION.json').write_text(json.dumps(dict(package_version=VER, build_date=DATE, release_id=RID, release_tag=f'data-v{VER}', previous_version='1.11.0', previous_release_id='R2026.11',
                                                       generator_git_sha='synthetic', tables=tables), indent=1), encoding='utf-8')
    return out


def build(pkg: Path, out: Path):
    cmd = [sys.executable, str(GEN / 'build_site.py'), '--package', str(pkg), '--out', str(out), '--base-url', 'https://olmlab.github.io/microbiome_repo/', '--build-date', DATE]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-4000:]
    return out


@pytest.fixture(scope='module')
def site_new(tmp_path_factory):
    d = tmp_path_factory.mktemp('with_cols')
    return build(make_package(d / 'pkg', True), d / 'site')


@pytest.fixture(scope='module')
def site_old(tmp_path_factory):
    d = tmp_path_factory.mktemp('without_cols')
    return build(make_package(d / 'pkg', False), d / 'site')


def _html(site, path):
    return (site / path).read_text(encoding='utf-8')


def _all_html(site):
    return {str(p.relative_to(site)): p.read_text(encoding='utf-8') for p in site.rglob('*.html')}


# ---------------------------------------------------------------- item 5: nav + about
def test_nav_order_and_about(site_new):
    html = _html(site_new, 'index.html')
    nav = html[html.index('<nav class="topnav">'):html.index('</nav>')]
    # R2026.15: grouped drop-down navigation (owner review 2026-10-01)
    top = re.findall(r'(?:<a href="[^"]*" [^>]*>([^<]+)</a>|<button type="button" class="navbtn"[^>]*>([^<]+?) <span)', nav)
    assert [a or b for a, b in top] == ['Home', 'Sample sheet', 'Project sheet', 'Contribute', 'Explore', 'About']   # R2026.16
    items = re.findall(r'role="menuitem" href="([^"]+)"', nav)
    for page in ('registry/index.html', 'atlas/index.html', 'atlas/pca.html', 'downloads/index.html', 'llms/index.html', 'about/methods.html', 'fields/index.html'):
        assert page in items, page
    about = _html(site_new, 'about/index.html')
    lead = htmlmod.unescape(about[about.index('<p class="lead">'):about.index('</p>', about.index('<p class="lead">'))])
    a = SITE_CFG['about']
    assert a['lab_url'] in lead and a['funder_url'] in lead and a['lab_name'] in lead and a['funder_name'] in lead
    assert 'https://www.colorado.edu/lab/olm/' in lead and 'https://www.anthropic.com/news/ai-for-science-program' in lead
    for sub in ('about/scope.html', 'about/methods.html', 'about/sources.html', 'fields/index.html'):
        assert (site_new / sub).exists()
    assert 'fields/index.html' in about and 'id="cite"' in about
    # Collections / Atlas nav targets exist even though no track produced them (placeholders)
    assert (site_new / 'collections' / 'index.html').exists() and (site_new / 'atlas' / 'index.html').exists()


def test_redirects_exist(site_new):
    for old, new in bs.REDIRECTS.items():
        t = _html(site_new, old)
        assert 'http-equiv="refresh"' in t and new in t, old
        target = (site_new / old).parent / new.split('#')[0]
        assert target.resolve().exists(), (old, new)
    assert (site_new / 'downloads' / 'index.html').exists() and 'id="releases"' in _html(site_new, 'downloads/index.html')


# ---------------------------------------------------------------- items 3, 4, 8, 12: wording / removed widgets
def test_removed_strings(site_new):
    pages = _all_html(site_new)
    for name, t in pages.items():
        assert 'Every ENA BioProject' not in t, name
        assert 'Most-linked' not in t, name
        assert 'Confirm correct' not in t, name
        assert 'infant-scope' not in t.lower() or 'infant extension' in t.lower(), name
    home = pages['index.html']
    assert 'coverage by age category' not in home.lower() and 'id="coverage"' not in home
    assert 'INSDC BioProject' in pages['studies/index.html'] and 'INSDC' in pages['registry/index.html'] and 'INSDC' in pages['about/methods.html']
    assert SITE_CFG['site']['title'] in home


def test_home_tiles_search_and_bars(site_new):
    # R2026.16 minimalist home (owner review 2026-10-08): description, one search box, example queries, links — no tiles or bars
    home = _html(site_new, 'index.html')
    cw = pd.read_parquet(site_new / 'data' / 'gut_sample_metadata_wide.parquet')
    assert 'id="q"' in home and 'class="hero"' in home and 'cards tiles' not in home and 'hbars' not in home
    assert f'{cw.study_accession.nunique():,} projects</a>' in home and f'{len(cw):,} samples</a>' in home and 'hero-credit' in home
    for href in ('samples/index.html', 'studies/index.html', 'registry/index.html', 'llms/index.html'):
        assert f'href="{href}' in home, href
    assert 'authors/idx/' in home and 'search_index.json' in home
    idx = json.loads((site_new / 'search_index.json').read_text())
    assert {x['t'] for x in idx} >= {'study'}


# ---------------------------------------------------------------- item 2: registry
def test_registry_human_count_is_yes_plus_mixed(site_new):
    rg = pd.read_parquet(site_new / 'data' / 'registry_studies.parquet')
    n_h = int(rg.host_human.isin(['yes', 'mixed']).sum()); n_all = len(rg)
    assert n_h == 3 and n_all == 6
    reg = _html(site_new, 'registry/index.html')
    assert 'Is the registry all human? No.' in reg
    assert f'<b>{n_h:,} human studies</b>' in reg and f'{n_all:,} studies in release' in reg
    assert reg.index('id="explorer"') < reg.index('id="scopes"') < reg.index('id="about"'), 'explorer first, prose below'
    for v, checked in (('yes', True), ('mixed', True), ('unknown', False), ('no', False)):
        m = re.search(rf'<input type="checkbox" id="f-host_{v}"( checked)?>', reg)
        assert m and bool(m.group(1)) == checked, v
    assert 'f-host_human' not in reg
    js = (site_new / 'static' / 'registry_explorer.js').read_text()
    assert "get('study')" in js and js.index("get('study')") < js.index('await run()') and 'scrollIntoView' in js and 'host_human IN' in js


# ---------------------------------------------------------------- items 7, 9, 10, 11: study page
def test_study_page_sections(site_new):
    t = _html(site_new, 'studies/PRJTEST000001.html')
    # R2026.18 redesign: registry entry in a collapsed Details section; coverage = core fields always, then every field with values
    assert 'How this project entered the registry' in t and 'Registry classification</h2>' not in t
    assert 'Classified as human' in t and 'by deterministic_rule with confidence 0.90' in t
    assert 'sample.attr.host' in t and '“Homo sapiens”' in t
    assert 'registry/index.html?study=PRJTEST000001' in t
    cov_rows = re.findall(r'<tr( class="core")?><td><a class="mono" href="../fields/index.html#([a-z_]+)"', t)
    assert [f for c, f in cov_rows if c] == CORE
    assert all(f not in CORE for c, f in cov_rows if not c) and len(cov_rows) > len(CORE)
    # sequencing summary from gut_runs.parquet
    runs = pd.read_parquet(site_new / 'data' / 'package' / 'gut_runs.parquet')
    g = runs[runs.study_accession == 'PRJTEST000001']
    gbp = pd.to_numeric(g.base_count) / 1e9
    assert 'id="sequencing"' in t and f'{len(g):,} runs' in t and f'mean {gbp.mean():.2f} Gbp per run' in t and f'median {gbp.median():.2f}' in t
    assert 'PAIRED' in t and 'ILLUMINA' in t and 'Illumina NovaSeq 6000' in t and f'{100 * g.sandpiper_profiled.mean():.0f}%' in t
    # sample links: explorer with the sample filter, archive record per accession type
    # R2026.18: the sample table is built in the browser from the per-project CSV (static/study_table.js builds the same links)
    assert "csv:'../data/studies/PRJTEST000001.csv.gz'" in t and 'static/study_table.js' in t
    js = (site_new / 'static' / 'study_table.js').read_text()
    assert '../samples/index.html?sample=' in js and 'https://www.ncbi.nlm.nih.gov/biosample/' in js
    t2 = _html(site_new, 'studies/PRJTEST000002.html')
    assert 'https://www.ebi.ac.uk/ena/browser/view/' in js and 'https://ddbj.nig.ac.jp/resource/biosample/' in js
    assert 'Confirm correct' not in t and 'template=simple-finding.yml' in t and 'accession=PRJTEST000001' in t and 'release_id=' in t and 'page_url=' in t
    assert 'infant extension' in t2 and 'infant field' not in t2.replace('infant fields', '')


def test_archive_url_helper():
    assert bs.archive_url('SAMN04161034') == 'https://www.ncbi.nlm.nih.gov/biosample/SAMN04161034'
    assert bs.archive_url('SAMEA104142130') == 'https://www.ebi.ac.uk/ena/browser/view/SAMEA104142130'
    assert bs.archive_url('SAMD00012345') == 'https://ddbj.nig.ac.jp/resource/biosample/SAMD00012345'
    assert bs.archive_url('SRR1234567') == 'https://www.ebi.ac.uk/ena/browser/view/SRR1234567'
    assert bs.archive_url(None) is None


# ---------------------------------------------------------------- item 7: contribute worklist uses pack core_fields
def test_contribute_worklist_uses_pack_core_fields(site_new):
    wl = json.loads((site_new / 'data' / 'contribute_worklist.json').read_text())
    assert [r['acc'] for r in wl] == ['PRJTEST000001']
    assert set(wl[0]['missing']) <= set(CORE) and 'country' in wl[0]['missing'] and 'health_condition' in wl[0]['missing']   # R2026.15: core = age_category, country, health_condition, subject_id
    assert set(wl[0]['coverage']) == set(CORE)
    page = _html(site_new, 'contribute/index.html')
    # R2026.17: simplified page — one row per project, the missing core fields as chips, one 'Share metadata' button to the issue form
    assert 'Share metadata' in page and 'share-metadata.yml' in page and 'PRJTEST000001' in page
    assert 'five core fields' not in page and 'antibiotic exposure.' not in page
    study = _html(site_new, 'studies/PRJTEST000001.html')
    assert f'of the {len(CORE)} core fields are below' in study


# ---------------------------------------------------------------- item 7: new columns present vs absent
def test_new_columns_present(site_new):
    exp = _html(site_new, 'samples/index.html')
    for fid in ('f-lifestyle', 'f-year_min', 'f-year_max', 'f-detailed_location', 'f-location_locality'):
        assert f'id="{fid}"' in exp, fid
    assert 'value="athlete"' in exp and 'placeholder="2015"' in exp and 'placeholder="2016"' in exp
    fields = _html(site_new, 'fields/index.html')
    assert fields.index('id="tier-core"') < fields.index('id="tier-key"') < fields.index('id="tier-infant"')
    for f in ('lifestyle', 'collection_date', 'detailed_location', 'latitude', 'delivery_mode'):
        assert f'id="{f}"' in fields, f
    study = _html(site_new, 'studies/PRJTEST000001.html')
    assert 'fields/index.html#detailed_location' in study and 'fields/index.html#lifestyle' in study
    assert 'openstreetmap.org' in (site_new / 'static' / 'explorer.js').read_text()


def test_new_columns_absent(site_old):
    exp = _html(site_old, 'samples/index.html')
    for fid in ('f-lifestyle', 'f-year_min', 'f-detailed_location', 'f-location_locality'):
        assert f'id="{fid}"' not in exp, fid
    fields = _html(site_old, 'fields/index.html')
    for f in ('lifestyle', 'collection_date', 'detailed_location', 'latitude'):
        assert f'id="{f}"' not in fields, f
    assert 'id="tier-core"' in fields and 'id="tier-key"' in fields and 'id="antibiotic_exposure"' in fields
    study = _html(site_old, 'studies/PRJTEST000001.html')
    assert 'fields/index.html#detailed_location' not in study and 'fields/index.html#lifestyle' not in study and 'fields/index.html#antibiotic_exposure' in study
    # everything else identical in shape
    for p in ('index.html', 'registry/index.html', 'about/index.html', 'downloads/index.html', 'authors/index.html', 'contribute/index.html'):
        assert (site_old / p).exists(), p


# ---------------------------------------------------------------- link check (make verify equivalent) + authors + explorer contract
def test_links_resolve_and_explorer_contract(site_new, site_old):
    for site in (site_new, site_old):
        r = subprocess.run([sys.executable, str(GEN / 'check_links.py'), str(site)], capture_output=True, text=True)
        rep = json.loads(r.stdout)
        assert rep['broken'] == 0 and rep['root_absolute'] == 0 and rep['bad_fragments'] == 0, rep
    js = (site_new / 'static' / 'explorer.js').read_text()
    assert 'samples match' in js, 'verify.yml Playwright test depends on the count text'
    assert "get('sample')" in js and js.index("get('sample')") < js.index('await run()')
    assert 'archiveUrl' in js and 'archiveLink' in js
    exp = _html(site_new, 'samples/index.html')
    assert 'https://www.ncbi.nlm.nih.gov/biosample/' in exp and 'https://ddbj.nig.ac.jp/resource/biosample/' in exp and 'https://www.ebi.ac.uk/ena/browser/view/' in exp
    authors = _html(site_new, 'authors/index.html')
    assert 'Most-linked' not in authors and 'studies_index.json' in authors and 'scard' in authors
    sidx = json.loads((site_new / 'data' / 'studies_index.json').read_text())
    assert {'a', 't', 'n', 'co', 'hc', 'y', 'gs', 'iv'} <= set(sidx[0]) and not ({'d', 'ca', 'ch', 'cc', 'nr', 'gb'} & set(sidx[0]))   # R2026.15 narrow table


def test_issue_template_and_config():
    tpl = yaml.safe_load((GEN / 'issue_templates' / 'simple-finding.yml').read_text())
    ids = [b['id'] for b in tpl['body'] if 'id' in b]
    assert ids == ['problem', 'details', 'accession', 'release_id', 'page_url']
    dd = [b for b in tpl['body'] if b['type'] == 'dropdown']
    assert len(dd) == 1 and dd[0]['attributes']['options'] == ['a value is wrong', 'a value is missing', 'the study should not be in the catalog', 'something else']
    assert sum(1 for b in tpl['body'] if b['type'] == 'textarea') == 1
    assert SITE_CFG['github']['issues']['template'] == 'simple-finding.yml'
    url = bs.simple_issue_url('https://github.com/OlmLab/microbiome_repo/issues/new', 'simple-finding.yml', 'finding', 'PRJTEST000001', 'R2026.12', 'https://x/studies/PRJTEST000001.html')
    assert 'template=simple-finding.yml' in url and 'accession=PRJTEST000001' in url and 'release_id=R2026.12' in url and 'page_url=https%3A%2F%2Fx' in url


def test_field_tiers_from_pack():
    core, key, derived, infant = bs.read_field_tiers(PACK)
    assert core == CORE and key == KEY and 'detailed_location' in derived and 'delivery_mode' in infant and 'lifestyle' not in infant
    df = pd.DataFrame(dict(location_site=['A', None, None], location_locality=['B', 'C', None], location_region=['D', 'E', None]))
    s = bs.field_series(df, 'detailed_location', derived)
    assert list(s.fillna('')) == ['A, B, D', 'C, E', '']
    assert bs.field_series(df, 'lifestyle', derived) is None
