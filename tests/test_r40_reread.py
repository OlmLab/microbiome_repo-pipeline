"""R2026.40: paper re-read methods facts, exposure item check merge, dictionary rows for new columns, release notices."""
import importlib.util, json, sys
from pathlib import Path
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_platform_comparison_with_the_archive():
    B = _load('brm', 'scripts/build_reread_methods.py')
    assert B.compare('Illumina NovaSeq 6000', ['Illumina NovaSeq 6000']) == 'agree'
    assert B.compare('NextSeq 550Dx', ['NextSeq 550']) == 'agree'                      # same instrument, diagnostic variant
    assert B.compare('HiSeq X Ten', ['Illumina HiSeq X']) == 'agree'
    assert B.compare('MGISEQ-2000', ['DNBSEQ-G400']) == 'agree'                        # renamed by the maker
    assert B.compare('Illumina HiSeq 2500 | Illumina MiSeq', ['Illumina MiSeq']) == 'agree'
    assert B.compare('Illumina HiSeq (model not specified)', ['Illumina HiSeq 4000']) == 'paper_less_specific'
    assert B.compare('Illumina (model not specified)', ['NextSeq 500']) == 'paper_less_specific'
    assert B.compare('Illumina HiSeq (model not specified)', ['Illumina NovaSeq 6000']) == 'differ'
    assert B.compare('Illumina MiSeq', ['Illumina NovaSeq 6000']) == 'differ'
    assert B.compare('Illumina MiSeq', ['unspecified']) == 'not_compared'
    assert B.compare('unclear', ['Illumina MiSeq']) == 'not_compared'


def test_methods_maps_cover_one_wording_once():
    for f, col in [('extraction_kit', 'extraction_kit_norm'), ('library_kit', 'library_kit_norm'), ('platform', 'platform_norm'), ('sequencing_facility', 'facility_norm')]:
        m = pd.read_csv(REPO / 'config' / 'methods_names' / f'{f}_map.csv')
        assert m.value_raw.is_unique and m[col].notna().all()
        names = m[col][m[col] != 'unclear'].str.split(' | ', regex=False).explode()
        assert names.str.lower().str.replace(r'[^a-z0-9+]+', '', regex=True).nunique() == names.nunique(), f   # no two names differing by case or punctuation only ('+' is part of a model name)


def test_dictionary_gets_rows_for_columns_added_to_a_documented_table():
    from catalog.release import package_docs as PD
    d = "## Tables\n### t.parquet (3 rows) — x\n\n| column | dtype | meaning |\n|---|---|---|\n| `a` | int | first |\n\n### u.parquet\n\n| column | dtype | meaning |\n|---|---|---|\n| `z` | int | z |\n"
    schema = {'t.parquet': {'columns': [{'name': 'a', 'type': 'int', 'description': 'first'}, {'name': 'b', 'type': 'string', 'description': 'second — R2026.40'},
                                        {'name': 'c', 'type': 'string', 'description': 'third'}]}, 'v.parquet': {'columns': [{'name': 'q'}]}}
    d2, n = PD.add_missing_columns(d, schema, 'R2026.40')
    assert n == 2 and '| `b` | string | second — R2026.40 |' in d2 and '| `c` | string | third — documented R2026.40 |' in d2
    assert d2.index('| `c` |') < d2.index('### u.parquet')            # inside the table's own block
    assert PD.add_missing_columns(d2, schema, 'R2026.40')[1] == 0      # idempotent


def test_exposure_check_merge_rules(tmp_path):
    import types
    for name in ('catchup_passes', 'reread_passes'):                    # the merge needs neither; keep the import light
        sys.modules.setdefault(name, types.SimpleNamespace(PP=None, norm=lambda s: s, locate=lambda q, s: (None, None)))
    sys.path.insert(0, str(REPO / 'scripts'))
    X = _load('xic', 'scripts/exposure_item_check.py')
    base = dict(scale='per_group', summary='s', quote='q', located='paper_methods', check_status='ok', chk_supported=True, chk_about_deposit_people=True,
                chk_scale='per_group', chk_summary_ok=True, chk_summary_revised='', chk_note=None, requoted=False, quote_original=None)
    rows = [dict(base, study_accession='P1', concept='pets', origin='catalog', chk_concept='pets'),
            dict(base, study_accession='P1', concept='livestock_or_animals', origin='reread', chk_concept='pets'),                      # second item of the concept
            dict(base, study_accession='P2', concept='travel', origin='reread', chk_concept='travel', chk_supported=False),
            dict(base, study_accession='P3', concept='diarrhoea', origin='reread', chk_concept='diarrhoea', chk_about_deposit_people=False),
            dict(base, study_accession='P4', concept='sanitation', origin='catalog', chk_concept='none'),
            dict(base, study_accession='P5', concept='siblings', origin='catalog', chk_concept='siblings', chk_scale='whole_cohort', chk_summary_ok=False, chk_summary_revised='Only one sibling statement is supported.'),
            dict(base, study_accession='P6', concept='daycare', origin='catalog', chk_concept='daycare', chk_summary_ok=False, chk_summary_revised=''),
            dict(base, study_accession='P7', concept='deworming', origin='catalog', chk_concept=None, check_status='quote_not_found', chk_supported=None)]
    pd.DataFrame(rows).to_parquet(tmp_path / 'exposure_check_shard_0.parquet', index=False)
    X.merge(str(tmp_path))
    k = pd.read_parquet(tmp_path / 'exposure_items_checked.parquet')
    assert sorted(zip(k.study_accession, k.concept, k.scale)) == [('P1', 'pets', 'per_group'), ('P5', 'siblings', 'whole_cohort')]
    assert k.set_index('study_accession').summary['P5'] == 'Only one sibling statement is supported.'
    a = pd.read_parquet(tmp_path / 'exposure_check_all.parquet').set_index('study_accession').outcome
    assert a['P2'].startswith('dropped: the passage') and a['P3'].startswith('dropped: not about') and a['P4'].startswith('dropped: no listed')
    assert a['P6'].startswith('dropped: summary') and a['P7'] == 'dropped: quote_not_found'


def test_release_notice_file_exists_for_r2026_40_and_generator_reads_notices():
    src = (REPO / 'src' / 'catalog' / 'release' / 'release_notes.py').read_text()
    assert 'release_notices' in src and '## Notices' in src
    t = (REPO / 'docs' / 'release_notices' / 'R2026.40.md').read_text()
    assert 'd5f56cba23415d8dcaa8ff8702319035e71d3810c3f45f2dba04d86015c6a966' in t


def test_methods_strip_shows_controlled_names_facility_and_instrument_flag():
    sys.path.insert(0, str(REPO / 'site_generator' / 'gen'))
    from pages import project_facts as PF
    row = dict(extraction_kit='QIAamp kit (Qiagen)', extraction_kit_norm='Qiagen QIAamp DNA Stool Mini Kit', library_kit='Nextera XT', instrument_models_top='{"Illumina HiSeq 2500": 12}',
               platform='MiSeq', platform_norm='Illumina MiSeq', platform_vs_archive='differ', sequencing_facility='Novogene Co.', sequencing_facility_norm='Novogene',
               sequencing_facility_country='CN', sequencing_facility_type='company', nucleic_acid='dna', paper_ids_generated='1',
               methods_evidence=json.dumps({'extraction_kit': {'quote': 'DNA was extracted with X.', 'source': 'paper_methods'}}))
    html = PF.methods_strip(PF.facts_of(row))
    assert 'class="mstrip m5"' in html and 'Qiagen QIAamp DNA Stool Mini Kit' in html and 'as written: QIAamp kit (Qiagen)' in html
    assert 'Sequenced at' in html and 'Novogene' in html and 'paper names another instrument: Illumina MiSeq' in html
    plain = PF.methods_strip(PF.facts_of(dict(extraction_kit='X kit', nucleic_acid='dna')))
    assert 'class="mstrip"' in plain and 'Sequenced at' not in plain
