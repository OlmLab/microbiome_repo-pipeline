#!/usr/bin/env python
"""Static site generator v3 for Microbiome Repo — all ages, one catalog.

Tiers on the site: the REGISTRY (registry_studies.parquet: every ENA shotgun-metagenome study reached by the enumeration — INSDC
BioProjects from ENA / NCBI SRA / DDBJ — classified for human host, body site, life stage, assay, access; the headline number is
the HUMAN count, host_human in {yes, mixed}) and the CATALOG = the curated gut_all scope (gut_studies / gut_sample_metadata_wide /
gut_sample_determinations: every human gut shotgun study, all ages, per-sample evidence-linked metadata). Navigation (NAV):
Home · Browse ▾ (Studies, Samples, Cohorts, Collections, Authors) · Insights ▾ (Atlas) · Registry · Data ▾ (Downloads, Contribute) ·
About ▾ (Overview, Scope, Methods, Fields, Sources); Scope, Methods and Sources live under about/, Downloads and Releases are one page, old URLs redirect (REDIRECTS). Field tiers (core / key /
infant extension) come from config/packs/gut.yaml; columns the package does not carry yet are omitted, never shown as 0 %.
The former infant-only tables remain in the package as the infant extension and surface only as fields.

Usage:  python build_site.py --package PKG_DIR --out site [--reports DIR] [--package-zip ZIP] [--base-url URL]

Every number, accession and identifier on the site is read from the package tables; nothing is hard-coded.
Deterministic (A2): build date = VERSION.json build_date (never wall clock); every *.csv.gz is written with gzip
mtime=0; all iteration is over sorted keys; JSON is dumped with sort_keys. Two builds from the same package are
byte-identical (tests/test_build_determinism.py).
"""
import argparse, gzip, hashlib, io, json, math, os, re, shutil, sys, time
from pathlib import Path
from urllib.parse import quote
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
import markdown
import yaml

HERE = Path(__file__).resolve().parent
ROUTES = ['R1', 'R2', 'R3', 'R4']
ROUTE_LABELS = {'R1': 'archive attribute', 'R2': 'supplementary table', 'R3': 'paper full text', 'R4': 'abstract / description'}
LABELS = {'age_category': 'Age (exact or life stage)', 'age_at_collection_days': 'Age at collection (days, exact)', 'intervention': 'Intervention (sample arm)', 'interventions': 'Interventions (study)', 'sex': 'Sex', 'bmi': 'BMI', 'country': 'Country', 'health_condition': 'Health condition',
          'health_condition_detail': 'Health condition (detail)', 'antibiotic_exposure': 'Antibiotic exposure', 'subject_id': 'Subject id', 'timepoint_label': 'Timepoint label',
          'delivery_mode': 'Delivery mode', 'feeding_mode': 'Feeding mode', 'preterm_status': 'Preterm status', 'gestational_age_weeks': 'Gestational age (weeks)',
          'birth_weight_grams': 'Birth weight (g)', 'maternal_antibiotics': 'Maternal antibiotics', 'probiotic_exposure': 'Probiotic exposure',
          'hmo_supplementation': 'HMO supplementation', 'nec_status': 'NEC status',
          'collection_date': 'Collection date', 'location_region': 'Location (region)', 'location_locality': 'Location (locality)', 'location_site': 'Location (site)',
          'detailed_location': 'Detailed location', 'latitude': 'Latitude', 'longitude': 'Longitude', 'lifestyle': 'Lifestyle', 'lifestyle_detail': 'Lifestyle (detail)'}
FIELD_CAVEATS = {
    'collection_date': 'As specific as the source gives it (YYYY, YYYY-MM, YYYY-MM-DD or an ISO interval for a stated sampling period); placeholders (missing / not collected / restricted) stay unknown.',
    'location_region': 'First-level administrative region below the country (state, province, prefecture …); English exonym, no country name.',
    'location_locality': 'City, town, village or district where subjects were recruited — not where the laboratory is.',
    'location_site': 'Named recruitment site (hospital, clinic, school, cohort field station, community name) in the source\'s own words.',
    'latitude': 'Decimal degrees from lat_lon / geographic-location attributes (R1/R2 only), rounded to 4 decimals.', 'longitude': 'Decimal degrees; same source row as latitude.',
    'lifestyle': 'Controlled vocabulary (config/vocab/lifestyle.yaml); committed only when the source states it — never inferred from the country.',
    'lifestyle_detail': 'Named population or the source\'s own words behind the lifestyle code (Hadza, Hutterite colony, elite rugby players …).',
    'age_at_collection_days': 'Age at sample collection in days. Archive ages without a unit are read as years for non-infant studies (rule recorded in the field map); ranges and timepoint lists are never values. The age category (neonate … elderly) is derived from this value, else from the sample\'s or study\'s stated life stage (basis column).',
    'sex': 'Archive attribute, table column or single-sex cohort statement (R3); no external truth set.',
    'bmi': 'Body-mass index from archive attributes or tables; plausibility window 10–80; never derived from a cohort mean.',
    'country': 'ISO-2 of the sampling / recruitment country from archive attributes, tables or paper text (not the laboratory or ethics-committee country).',
    'health_condition': 'Controlled vocabulary (config/vocab/health_conditions.yaml). Cohort-wide codes (R3/R4) are committed only when every sequenced subject shares the condition; case–control cohorts stay unknown at sample level unless the table or attribute names the group.',
    'health_condition_detail': 'Free text behind the code (diagnosis, group label, criterion).',
    'antibiotic_exposure': '"yes"/"no". "no" from a source that documents no exposure (attribute, table, or an exclusion window ≥ 1 month in the Methods), never from "no mention".',
    'subject_id': 'Submitter identifier of the individual (host_subject_id, participant, donor …); never a sample id. Not evaluated.',
    'timepoint_label': 'Compact tokens (D6, M4, baseline) are labels, not ages, unless the study confirms the unit.',
    'delivery_mode': 'Infant field: vaginal / caesarean; categories as stated by the source.', 'feeding_mode': 'Infant field: breast / mixed / formula as stated; boolean feeding columns never become a value.',
    'preterm_status': 'Infant field: term requires ≥ 37 weeks when derived from a criterion.', 'gestational_age_weeks': 'Infant field: completed weeks at birth.',
    'birth_weight_grams': 'Infant field; no external truth set.', 'maternal_antibiotics': 'Infant field: intrapartum or pregnancy antibiotics to the mother.',
    'probiotic_exposure': 'Infant field: any probiotic given to the infant.', 'hmo_supplementation': 'Infant field: HMO-supplemented formula.', 'nec_status': 'Infant field: necrotising enterocolitis diagnosis.',
}
FILE_DESC = {
    'gut_studies.parquet': 'Start here (studies). One row per catalog study: registry classification, linked papers, per-field coverage (cov_*), age-category and health-condition distributions, curated depth and source.',
    'gut_sample_metadata_wide.parquet': 'Start here (samples). One row per sample: identifiers, body-site code/class, age_category (+ basis), every field with __route and __confidence, infant extension fields, infant_scope flag.',
    'gut_sample_determinations.parquet': 'Long form: one row per sample × field with route, scope, confidence, evidence_source, evidence_locator and a verbatim evidence quote (≤ 12 words).',
    'registry_studies.parquet': 'Registry tier: one row per ENA study with a human shotgun-metagenome signal — host, body sites (UBERON-anchored codes), life stages, assay, access, classification stage, scope memberships, harvested-sample roll-ups.',
    'registry_biosamples.parquet': 'Registry sample tier: one row per harvested BioSample with normalised body site, life stage / age, sex, country.',
    'registry_study_papers.parquet': 'Study ↔ paper links of the registry studies (PMID/PMCID/DOI, relation own_data/unsure, method, confidence).',
    'registry_authors.parquet': 'Study × author × paper rows for the registry studies (Europe PMC author lists).',
    'registry_bioproject_records.parquet': 'NCBI BioProject records (title, description, umbrella, organisation) for the registry studies.',
    'registry_universe_audit.csv': 'Registry enumeration audit: per ENA slice, archive count vs rows pulled and completeness.',
    'sample_metadata_wide.parquet': 'Infant extension: the deep infant-gut curation (delivery mode, feeding, gestational age, birth weight …) for the infant studies; these rows are also in gut_sample_metadata_wide (curated_source = infant_catalog).',
    'sample_metadata_wide.csv.gz': 'Infant extension table as gzip-compressed CSV.',
    'sample_determinations.parquet': 'Infant extension: evidence rows of the infant-field curation.',
    'sample_determinations_all.parquet': 'Infant extension, bitemporal: current + retired rows with release_added / release_retired.',
    'study_metadata_wide.parquet': 'Infant extension: one row per infant study with infant-field coverage, triage evidence and recoverability tiers.', 'study_metadata_wide.csv': 'Same table as CSV.',
    'runs.parquet': 'Infant extension: run → sample → study for the infant studies.', 'sample_subjects.parquet': 'Infant extension: subject and timepoint resolution per sample.',
    'cohorts.csv': 'Infant extension: curated cohort clusters with unique-infant estimates (the site\'s cohort pages carry these names).',
    'study_paper_links.csv': 'Infant extension: curated study ↔ paper links.', 'universe_studies_all.parquet': 'Infant extension: the 9.6 k studies screened for the infant extension with verdict and reason.',
    'human_review_queue.csv': 'Infant extension: studies the infant triage could not decide.', 'field_coverage_summary.csv': 'Infant extension: coverage per infant field.',
    'study_field_coverage_matrix.csv': 'Infant extension: coverage per infant study × field.', 'extraction_gold_eval_hires.csv': 'Infant extension: precision/recall vs curatedMetagenomicData.',
    'parent_biosamples.parquet': 'Infant extension: parent BioSamples of run-level rows.', 'study_verdict_history.parquet': 'Infant extension: every triage-stage verdict per screened study.',
    'value_history.parquet': 'Infant extension: superseded / rejected values with reason.', 'sample_determinations_superseded.parquet': 'Infant extension: subset of value_history (v1.1 layout).',
    'confidence_tiers.csv': 'Infant extension: engine confidence tiers.', 'tier_field_precision.csv': 'Infant extension: precision per route × tier vs the cMD gold join.',
    'sample_unit_classification.csv': 'Infant extension: per-BioSample multi-run classification.', 'sample_unit_classification_by_study.csv': 'Infant extension: per-study sample-unit class.',
    'sandpiper_sample_summary.parquet': 'Sandpiper/SingleM per-sample summary for the infant studies (sp_* columns, QC flags, URL).',
    'sandpiper_top_genera.parquet': 'Top-15 genera per profiled infant-study sample.', 'sandpiper_study_panels.parquet': 'Per-study mean top phyla / genera (infant studies).', 'gut_sandpiper_study_panels.parquet': 'Per-project mean top-12 genera over profiled gut samples (project-page composition bar).',
    'sandpiper_run_qc.parquet': 'Infant-study runs: profiled or miss reason, QC fields.', 'sandpiper_study_coverage.csv': 'Per infant study: runs/samples profiled.',
    'sandpiper_study_qc_flags.csv': 'Per infant study: flag fractions.', 'sandpiper_flag_definitions.json': "Sandpiper's published QC flag definitions.", 'sandpiper_flag_table.csv': 'QC-flag vocabulary.',
    'authors.parquet': 'Infant extension: study × author × paper rows.', 'study_authors_summary.csv': 'Infant extension: first/last author and organisations per screened study.',
    'authors_index.json': 'Infant extension: author search index.', 'organisations.parquet': 'Infant extension: organisation strings per study.', 'organisations_index.json': 'Infant extension: organisation index.',
    'contribute_worklist.csv': 'Infant extension: contribution worklist of the infant fields.', 'contribute_worklist_fields.csv': 'Infant extension: worklist study × field.',
    'releases.csv': 'Release registry: one row per release id (release date, package version, tags, DOI, headline counts).',
    'VERSION.json': 'Package version, release id, release tag, build date, sha256 + rows per table.',
    'DATA_DICTIONARY.md': 'Every column, every vocabulary.', 'README.md': 'Package overview and how to read a value.',
    'CHANGELOG.md': 'Version history.', 'getting_started.ipynb': 'Notebook: load, filter, join, plot.',
    'SANDPIPER_REPORT.md': 'Sandpiper join: source, method, validation, caveats.', 'AUTHORS_REPORT.md': 'Author index: sources, coverage, caveats.',
    'DATA_MODEL_FIX_REPORT.md': 'v1.2 data-model fix (age scope, parents, history).', 'EXTRACTION_REPORT.md': 'Infant-field extraction report.', 'REGISTRY_REPORT.md': 'Registry build report.',
}
OFFSITE = [dict(name='registry_runs_v{v}.parquet', desc='Registry run tier: one row per sequencing run of every registry study (≈ 2 M rows, ≈ 80 MB) — GitHub Release asset data-v{v}.'),
           dict(name='infant_catalog_v{v}.sqlite', desc='All package tables as one SQLite database — Release asset.'),
           dict(name='sandpiper_profiles.parquet', desc='Full sample × rank × taxon Sandpiper profiles of the infant studies (≈100 MB) — Release asset.'),
           dict(name='gut_sandpiper_sample_genus_v{v}_part*.parquet', desc='Genus-level Sandpiper relative abundances for every profiled catalog sample (long table: sample_key, genus, relabund, coverage; ≈ 185 MB in < 90 MB parts, concatenate the parts) — Release asset.'),
           dict(name='gut_sandpiper_sample_species_v{v}_part*.parquet', desc='Species-level Sandpiper relative abundances (relabund ≥ 0.001; ≈ 200 MB in parts) — Release asset.')]
TAXON_PALETTE = ['#CFB87C', '#565A5C', '#A88B4A', '#8C8F91', '#7A6A3C', '#3C3C3C', '#8A7A48', '#7F7060', '#6E6A5E', '#8F7418', '#6F6D62', '#6B6F73', '#7D7461', '#4A4A4A', '#75604A', '#5F6366']
UNASSIGNED_COLOR = '#D9D9D9'
# R2026.20 project-page genus bar: 12 distinguishable colours (CU gold first, then a colour-blind-aware qualitative set)
SP_PALETTE = ['#CFB87C', '#4477AA', '#EE6677', '#228833', '#CCBB44', '#66CCEE', '#AA3377', '#BB5566', '#004488', '#997700', '#6699CC', '#88CCAA']
ISSUE_REPO = 'https://github.com/OlmLab/microbiome_repo/issues/new'   # default; main() replaces both from config/site.yaml github.issues
ISSUE_TEMPLATE = 'catalog-finding.yml'
SIMPLE_ISSUE_TEMPLATE = 'simple-finding.yml'   # site_generator/gen/issue_templates/simple-finding.yml (installed into the Issues repo)
PUBLIC_REPORT_DOCS = [('CATALOG_REPORT.md', 'Infant-extension catalog report'), ('EXTRACTION_REPORT.md', 'Infant-field extraction report')]
NEVER_PUBLISH_DOCS = {'NEXT_STAGE.md', 'SCALE_UP_PLAN.md', 'RUNBOOK.md', 'STATE_BRIEF.md'}
FRAME_TOKEN_RE = re.compile(r'\s*\((?:frame|session)\s+[0-9a-f]{6,}[^)]*\)|\b(?:frame|session)\s+[0-9a-f]{8,}\b')
ORG_LEAK_RE = re.compile(r'SUB\d{6,}|@')
# Field tiers are NOT hard-coded: config/packs/gut.yaml core_fields / key_fields / derived_fields (read_field_tiers). A study enters the
# contribute worklist when >= CONTRIB_MIN_MISSING of the CORE fields are below the coverage threshold.
CONTRIB_MIN_SAMPLES, CONTRIB_MIN_MISSING = 50, 2   # 2 of the 4 core fields (R2026.15: core age = life stage, sex is a key field; was 3 of 5)
# Top navigation (owner reviews 2026-10-01 / 2026-10-08): Home · Sample sheet · Project sheet · Contribute, then two drop-downs that hold
# everything else. Entries are (key, label, href, children); a child with href None is a section heading inside a menu. Pages pass
# nav=<key>; a group is highlighted when the page's key is one of its children's keys.
NAV = [('home', 'Home', 'index.html', None),
       ('studies', 'Projects', 'studies/index.html', None),
       ('samples', 'Samples', 'samples/index.html', None),
       ('contribute', 'Contribute', 'contribute/index.html', None),
       ('explore', 'Explore', None, [('registry', 'Registry (every screened ENA study)', 'registry/index.html'), ('cohorts', 'Cohorts', 'cohorts/index.html'),
                                     ('collections', 'Collections', 'collections/index.html'), ('authors', 'Authors', 'authors/index.html'),
                                     ('atlas', 'Insights', None), ('atlas', 'Atlas: taxon map', 'atlas/index.html'),
                                     ('atlas', 'Atlas: PCA of community profiles', 'atlas/pca.html'), ('atlas', 'Atlas: observations', 'atlas/observations.html')]),
       ('about', 'About', None, [('about', 'About this resource', 'about/index.html'), ('llms', 'For LLMs & API', 'llms/index.html'),
                                 ('downloads', 'Downloads & releases', 'downloads/index.html'), ('about', 'Scope', 'about/scope.html'),
                                 ('about', 'Methods', 'about/methods.html'), ('about', 'Fields & vocabularies', 'fields/index.html'),
                                 ('about', 'External resources', 'about/external.html'), ('about', 'Sources & acknowledgements', 'about/sources.html')])]
# Old URLs → new homes (item 5/6): every entry is written as a redirect stub so bookmarks keep working.
REDIRECTS = {'scope.html': 'about/scope.html', 'methods.html': 'about/methods.html', 'sources.html': 'about/sources.html', 'downloads.html': 'downloads/index.html',
             'releases/index.html': '../downloads/index.html#releases', 'gut/index.html': '../samples/index.html', 'universe.html': 'about/scope.html'}
ARCHIVE_URLS = {'SAMN': 'https://www.ncbi.nlm.nih.gov/biosample/', 'SAMD': 'https://ddbj.nig.ac.jp/resource/biosample/', 'SAME': 'https://www.ebi.ac.uk/ena/browser/view/',
                'RUN': 'https://www.ebi.ac.uk/ena/browser/view/'}


def archive_url(acc):
    """Archive record of a BioSample / run accession (item 10): SAMN → NCBI BioSample, SAME/SAMEA → ENA browser, SAMD → DDBJ, runs → ENA browser."""
    if isnull(acc) or not acc:
        return None
    a = str(acc)
    if re.match(r'^[SED]RR\d+$', a):
        return ARCHIVE_URLS['RUN'] + a
    return ARCHIVE_URLS.get(a[:4], ARCHIVE_URLS['SAME']) + a


def read_field_tiers(pack):
    """core_fields / key_fields / derived_fields / infant fields from config/packs/gut.yaml (item 7). Nothing hard-coded."""
    core = list(pack.get('core_fields') or [])
    key = list(pack.get('key_fields') or [])
    derived = dict(pack.get('derived_fields') or {})
    assert core and key, 'config/packs/gut.yaml must define core_fields and key_fields'
    infant = [f for f, v in list(pack['fields'].items()) + list(derived.items()) if isinstance(v, dict) and v.get('infant_only')]
    return core, key, derived, infant


def field_series(df, f, derived):
    """Column f of the wide table, or the composed derived field (detailed_location = site, locality, region); None when absent."""
    spec = derived.get(f) or {}
    if f in df.columns:
        if spec.get('life_stage'):   # age_category: 'unknown' means not covered
            return df[f].where(df[f].fillna('unknown') != 'unknown', None)
        return df[f]
    parts = [p for p in (spec.get('compose') or []) if p in df.columns]
    if parts:
        sep = spec.get('sep', ', ')
        s = df[parts].apply(lambda r: sep.join(str(x) for x in r if not isnull(x) and str(x) != ''), axis=1)
        return s.where(s != '', None)
    if spec.get('from') and spec['from'] in df.columns:
        return df[spec['from']].map(lambda v: None if isnull(v) else str(v)[:4])
    return None



def mini_bars_svg(pairs, width=260, height=34, label_fmt=str, tip_unit='samples'):
    """Tiny inline SVG bar strip (timeline / histogram) for project pages: [(label, n), ...] → SVG string with <title> tips."""
    pairs = [(k, int(n)) for k, n in pairs if n]
    if not pairs:
        return ''
    m = max(n for _, n in pairs)
    bw = width / len(pairs)
    out = [f'<svg class="spark" viewBox="0 0 {width} {height + 12}" width="{width}" height="{height + 12}" role="img">']
    for i, (k, n) in enumerate(pairs):
        h_ = max(1.5, height * n / m)
        out.append(f'<rect x="{i * bw + 0.5:.1f}" y="{height - h_:.1f}" width="{max(1.0, bw - 1):.1f}" height="{h_:.1f}"><title>{label_fmt(k)}: {n:,} {tip_unit}</title></rect>')
    out.append(f'<text x="0" y="{height + 10}" class="spark-l">{label_fmt(pairs[0][0])}</text>')
    out.append(f'<text x="{width}" y="{height + 10}" class="spark-l" text-anchor="end">{label_fmt(pairs[-1][0])}</text></svg>')
    return ''.join(out)


DEPTH_EDGES = [0, 0.03, 0.1, 0.3, 1, 3, 10, 30, 100, 300, float('inf')]
DEPTH_REPO = {}


def depth_bin_counts(gb):
    """Counts of per-sample sequencing depth (Gbp) in the fixed repo-wide bins DEPTH_EDGES."""
    return pd.cut(gb, DEPTH_EDGES, right=False).value_counts(sort=False).astype(int).tolist()


def depth_svg(proj, repo, median, width=260, height=40):
    """Fixed-axis depth histogram: the whole catalog's distribution as grey bars, this project's samples as gold bars on
    the same bins (each scaled to its own maximum), and a tick at the project median — so one project's depth reads
    against the repository even when it has a single sample."""
    nb = len(DEPTH_EDGES) - 1
    bw = width / nb
    pm = max(proj) or 1
    rm = max(repo) if repo else 0
    lab = lambda i: (f"{DEPTH_EDGES[i]:g}–{DEPTH_EDGES[i + 1]:g} Gbp" if DEPTH_EDGES[i + 1] != float('inf') else f"≥ {DEPTH_EDGES[i]:g} Gbp")
    out = [f'<svg class="spark depth" viewBox="0 0 {width} {height + 12}" width="{width}" height="{height + 12}" role="img">']
    for i in range(nb):
        if rm:
            hr = height * repo[i] / rm
            out.append(f'<rect class="repo" x="{i * bw + 0.5:.1f}" y="{height - hr:.1f}" width="{bw - 1:.1f}" height="{hr:.1f}"><title>all projects, {lab(i)}: {repo[i]:,} samples ({100 * repo[i] / max(1, sum(repo)):.0f} %)</title></rect>')
        if proj[i]:
            hp = max(2.0, height * proj[i] / pm)
            out.append(f'<rect class="proj" x="{i * bw + 0.5:.1f}" y="{height - hp:.1f}" width="{bw - 1:.1f}" height="{hp:.1f}"><title>this project, {lab(i)}: {proj[i]:,} samples</title></rect>')
    import math
    if median > 0:
        i = next(k for k in range(nb) if DEPTH_EDGES[k] <= median < DEPTH_EDGES[k + 1])
        l0, l1 = DEPTH_EDGES[i], DEPTH_EDGES[i + 1]
        f = 0.5 if (l0 == 0 or l1 == float('inf')) else (math.log10(median) - math.log10(l0)) / (math.log10(l1) - math.log10(l0))
        x = (i + f) * bw
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{height}" class="med"><title>project median {median:.2f} Gbp per sample</title></line>')
    for k, t in ((0, '0'), (4, '1'), (7, '30'), (nb, 'Gbp')):
        anchor = 'start' if k == 0 else ('end' if k == nb else 'middle')
        out.append(f'<text x="{k * bw:.1f}" y="{height + 10}" class="spark-l" text-anchor="{anchor}">{t}</text>')
    out.append('</svg>')
    return ''.join(out)


def study_glance(g):
    """Globe spec + collection-year timeline + depth histogram for one project's wide rows."""
    import math
    countries = {str(k): int(v) for k, v in g['country'].dropna().value_counts().items()} if 'country' in g.columns else {}
    pts = []
    if 'latitude' in g.columns and 'longitude' in g.columns:
        ll = g[['latitude', 'longitude']].apply(pd.to_numeric, errors='coerce').dropna()
        if len(ll):
            agg = ll.assign(la=(ll.latitude * 2).round() / 2, lo=(ll.longitude * 2).round() / 2).groupby(['lo', 'la']).size().sort_values(ascending=False).head(300)
            pts = [[float(lo), float(la), int(n)] for (lo, la), n in agg.items()]
    years = []
    yc = None
    if 'collection_date' in g.columns:
        yc = pd.to_numeric(g['collection_date'].astype(str).str[:4], errors='coerce').dropna().astype(int)
        yc = yc[(yc >= 1980) & (yc <= 2030)]
    if yc is not None and len(yc):
        vc = yc.value_counts()
        years = [(y, int(vc.get(y, 0))) for y in range(int(yc.min()), int(yc.max()) + 1)]
    depth = ''
    if 'seq_gbp' in g.columns:
        gb = pd.to_numeric(g['seq_gbp'], errors='coerce')
        gb = gb[gb > 0]
        if len(gb):
            depth = depth_svg(depth_bin_counts(gb), DEPTH_REPO.get('counts') or [], float(gb.median()))
    return dict(globe=dict(countries=countries, points=pts) if (countries or pts) else None,
                timeline=mini_bars_svg(years) if len(years) >= 2 else '', years=(years[0][0], years[-1][0]) if years else None,
                depth=depth)

def read_about(cfg_path):
    a = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8')).get('about', {}) or {}
    for k in ('lab_name', 'lab_url', 'funder_name', 'funder_url'):
        assert a.get(k), f'config/site.yaml about.{k} is required'
    return a


def read_issue_cfg(cfg_path):
    g = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8')).get('github', {})
    iss = g.get('issues', {}) or {}
    repo = f"https://github.com/{g.get('org')}/{iss.get('repo') or g.get('repos', {}).get('site')}/issues/new"
    return repo, iss.get('template', SIMPLE_ISSUE_TEMPLATE), iss.get('label', 'finding')


def verify_issue_url(repo, accession, release_id):
    """Prefilled 'Verify a project's metadata' form (issue_templates/verify-metadata.yml)."""
    q = [('template', 'verify-metadata.yml'), ('labels', 'metadata-verified'), ('title', f'[verified] {accession}'), ('accession', accession), ('release_id', release_id)]
    return repo + '?' + '&'.join(f'{k}={quote(str(v), safe="")}' for k, v in q)


def simple_issue_url(repo, template, label, accession, release_id, page_url, kind='study'):
    """Item 8: the simple finding form asks two things; accession, release and page URL are prefilled (field ids in issue_templates/simple-finding.yml)."""
    q = [('template', template), ('labels', label), ('title', f'[finding] {accession}'), ('accession', accession), ('release_id', release_id), ('page_url', page_url)]
    return repo + '?' + '&'.join(f'{k}={quote(str(v), safe="")}' for k, v in q)


def read_collections(cfg_path):
    """config/collections.yaml (another track) → [{id, name, u, k}] for the home search; [] when absent or unreadable."""
    p = Path(cfg_path)
    if not p.exists():
        return []
    y = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
    items = y.get('collections') if isinstance(y, dict) else y
    out = []
    for c in items or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get('id') or c.get('collection_id') or '')
        name = str(c.get('label') or c.get('name') or c.get('title') or cid)
        if not cid:
            continue
        studies = c.get('studies') or c.get('study_accessions') or []
        out.append(dict(id=cid, name=name, u=f'collections/index.html#{cid}', k=f"{cid} {name} {c.get('description', '')} {' '.join(map(str, studies))}".lower()))
    return out


def registry_summary(rgr, site_labels, stage_labels_short, assay_labels):
    """Item 9: one plain-language sentence from the registry classification row (labels from the vocabularies; nothing invented)."""
    host = {'yes': 'human', 'mixed': 'human and non-human (mixed)', 'no': 'non-human', 'unknown': 'unknown-host'}.get(str(rgr.get('host_human')), str(rgr.get('host_human') or 'unclassified'))
    lc = lambda s: (s[:1].lower() + s[1:]) if s else s   # vocabulary labels start with a capital; the sentence runs on
    sites = [lc(site_labels.get(b, b)) for b in split_list(rgr.get('body_sites'))]
    assay = lc(assay_labels.get(str(rgr.get('assay')), str(rgr.get('assay') or '')))
    stages = [lc(stage_labels_short.get(b, b)) for b in split_list(rgr.get('life_stages'))]
    parts = [f'Classified as {host} ' + (' / '.join(sites) if sites else 'body site unknown') + (f' {assay}' if assay else '')]
    if stages:
        parts.append('of ' + ' and '.join(stages))
    stage = rgr.get('classification_stage')
    if stage:
        parts.append(f'by {stage}')
    if not isnull(rgr.get('classification_confidence')):
        parts.append(f"with confidence {float(rgr['classification_confidence']):.2f}")
    return ' '.join(parts) + '.'


def ev_rows(v, limit=6):
    lst = jl(v, default=[])
    return [dict(source=str(x.get('source', '')), quote=str(x.get('quote', ''))) for x in (lst if isinstance(lst, list) else [])[:limit] if isinstance(x, dict)]


def _search_terms(s, iv_voc, hc_labels):
    """Extra home-search words for a study: intervention codes + labels + detail, top health-condition codes + labels, design."""
    w = []
    for c in [x for x in str(s.get('interventions') or '').split(';') if x and x != 'nan']:
        w += [c.replace('_', ' '), (iv_voc.get(c) or {}).get('label', '')]
    if s.get('intervention_detail') and not isnull(s.get('intervention_detail')):
        w.append(str(s['intervention_detail']))
    for c in list((s.get('health_conditions_d') or {}).keys())[:4] + [s.get('population_condition') or '']:
        if c and not isnull(c):
            w += [str(c).replace('_', ' '), hc_labels.get(c, '')]
    return ' '.join(x for x in w if x)


def _top_key(v):
    """top_country is stored as a JSON count map ({"United Kingdom": 12353}); the table shows the most frequent name (+N more)."""
    if isnull(v) or v == '':
        return ''
    t = str(v)
    if t.startswith('{'):
        try:
            d = json.loads(t)
        except ValueError:
            return t
        if not d:
            return ''
        top = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
        return top[0][0] + (f' +{len(top) - 1}' if len(top) > 1 else '')
    return t


def _gbp_per_sample(g):
    """Median Gbp per SAMPLE (bases summed over a sample's runs; lane / re-sequencing splits make per-run depth misleading)."""
    if 'sample_key' not in g.columns:
        return None
    per = g.dropna(subset=['gbp']).groupby(g['sample_key'].fillna(g['run_accession']))['gbp'].sum()
    return float(per.median()) if len(per) else None


def gbp_stats(runs):
    """Item 11: per-study sequencing summary from gut_runs.parquet (base_count / 1e9 per run)."""
    r = runs.copy()
    r['gbp'] = pd.to_numeric(r['base_count'], errors='coerce') / 1e9
    r['sp'] = r['sandpiper_profiled'].fillna(False).astype(bool) if 'sandpiper_profiled' in r.columns else False
    out = {}
    for acc, g in r.groupby('study_accession', sort=True):
        gb = g['gbp'].dropna()
        out[acc] = dict(n_runs=int(len(g)), n_samples=int(g['sample_key'].nunique()) if 'sample_key' in g.columns else None,
                        gbp_mean=float(gb.mean()) if len(gb) else None, gbp_median=float(gb.median()) if len(gb) else None, gbp_total=float(gb.sum()) if len(gb) else None,
                        gbp_per_sample=_gbp_per_sample(g),
                        n_with_bases=int(len(gb)), layouts=counts_sorted(g['library_layout'].dropna()), platforms=counts_sorted(g['instrument_platform'].dropna()),
                        models=counts_sorted(g['instrument_model'].dropna())[:6], sandpiper_share=float(g['sp'].mean()) if len(g) else None,
                        first_public=(str(g['first_public'].dropna().min())[:10] if g['first_public'].notna().any() else None, str(g['first_public'].dropna().max())[:10] if g['first_public'].notna().any() else None))
    return out


def strip_frame_tokens(text):
    if text is None or (isinstance(text, float) and math.isnan(text)):
        return text
    return FRAME_TOKEN_RE.sub('', str(text)).strip()


def human(n):
    n = float(n)
    for u in ['B', 'KB', 'MB', 'GB']:
        if n < 1024 or u == 'GB':
            return f'{n:.0f} {u}' if u == 'B' else f'{n:.1f} {u}'
        n /= 1024


def isnull(v):
    return v is None or (isinstance(v, float) and math.isnan(v)) or (not isinstance(v, (list, dict, tuple)) and pd.isna(v))


def f_fmt(v):
    if isnull(v):
        return '—'
    if isinstance(v, (int,)) or (isinstance(v, float) and float(v).is_integer()):
        return f'{int(v):,}'
    if isinstance(v, float):
        return f'{v:,.1f}'
    return str(v)


def f_pct(v):
    return '—' if isnull(v) else f'{100 * float(v):.0f}%'


def f_pct1(v):
    return '—' if isnull(v) else f'{100 * float(v):.1f}%'


def f_num2(v):
    return '—' if isnull(v) else f'{float(v):.2f}'


def f_numint(v):
    try:
        return '—' if isnull(v) else f'{int(round(float(v))):,}'
    except (TypeError, ValueError):
        return str(v)


def clean(d):
    return {k: (None if isnull(v) else (v.item() if hasattr(v, 'item') else v)) for k, v in d.items()}


def write_csv_gz(df, path):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as gz:
        gz.write(df.to_csv(index=False).encode('utf-8'))
    Path(path).write_bytes(buf.getvalue())


def dumps(o):
    return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def issue_url(**fields):
    """B4: prefilled GitHub issue-form URL. Field names = ids in .github/ISSUE_TEMPLATE/catalog-finding.yml."""
    q = [('template', ISSUE_TEMPLATE), ('labels', 'finding')]
    title = fields.pop('title', None)
    if title:
        q.append(('title', title[:200]))
    for k in sorted(fields):
        v = fields[k]
        if v is None or v == '':
            continue
        v = str(v)
        if k == 'current_state':
            v = v[:2500]
            while len(quote(v, safe='')) > 4000:
                v = v[:-50]
        q.append((k, v))
    url = ISSUE_REPO + '?' + '&'.join(f'{k}={quote(v, safe="")}' for k, v in q)
    assert len(url) <= 6000, 'issue URL over 6 kB'
    return url


def read_base_url(cfg_path):
    cfg = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8'))
    s = cfg.get('site', {})
    return s.get('base_url'), s.get('placeholder_base_url', 'https://USERNAME.github.io/REPOSITORY/')


def read_release_spec(cfg_path):
    spec = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8'))
    for k in ('columns', 'fact_tables', 'registry_columns', 'files', 'site_pages', 'release_id'):
        assert k in spec, f'config/releases.yaml lacks {k}'
    return spec


def read_contribute_spec(cfg_path):
    spec = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8'))
    for k in ('contribution_types', 'missing_threshold', 'issue_form'):
        assert k in spec, f'config/contribute.yaml lacks {k}'
    return spec


def contribute_issue_url(spec, acc, ctype, release_id):
    f = spec['issue_form']
    title = quote(f['title'].format(study_accession=acc, contribution_type=ctype), safe='')
    return f['url_template'].format(repo=f['repo'], template=f['template'], label=f['label'], title=title, study_accession=acc, contribution_type=ctype, release_id=release_id)


def read_scope_spec(cfg_path):
    spec = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8'))
    for k in ('scopes', 'registry_columns', 'classification_stages', 'host_human_values', 'access_values', 'assay_values', 'in_infant_catalog_values', 'files', 'vocab_dir', 'release_id', 'registry_columns_added'):
        assert k in spec, f'config/scope.yaml lacks {k}'
    return spec


def read_vocabs(repo_root, spec):
    vd = repo_root / spec['vocab_dir']
    out = {}
    for name, fn in (('body_site', 'body_sites.yaml'), ('life_stage', 'life_stages.yaml'), ('assay', 'assay.yaml')):
        y = yaml.safe_load((vd / fn).read_text(encoding='utf-8'))
        out[name] = {code: (d.get('label') if isinstance(d, dict) else str(d)) for code, d in y['codes'].items()}
    return out


def split_list(v):
    return [] if isnull(v) or v == '' else [x for x in str(v).split(';') if x]


def read_analytics(cfg_path):
    """config/site.yaml analytics: {provider: goatcounter|ga4|plausible, id}; empty id = no tracking script."""
    a = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8')).get('analytics') or {}
    prov, ident = str(a.get('provider') or '').strip(), str(a.get('id') or '').strip()
    return dict(provider=prov, id=ident) if prov in ('goatcounter', 'ga4', 'plausible') and ident else None


def read_site_names(cfg_path):
    s = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8')).get('site', {})
    names = dict(title=s.get('title'), short_title=s.get('short_title') or s.get('title'), tagline=s.get('tagline') or '')
    assert names['title'], 'config/site.yaml site.title is required'
    return names


def read_github_repo(cfg_path, key):
    g = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8')).get('github', {})
    return f"{g.get('org')}/{g.get('repos', {}).get(key)}"


def read_concept_doi(cfg_path):
    g = (yaml.safe_load(open(cfg_path)) or {}).get('github') or {}
    return g.get('zenodo_concept_doi') or ''


def read_data_repo_id(cfg_path):
    g = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8')).get('github', {})
    return g.get('data_repo_id')


def release_order_key(spec):
    pat = re.compile(spec['release_id']['pattern']) if spec['release_id'].get('pattern') else re.compile(r'^R(\d{4})\.(\d+)$')

    def key(rid):
        m = pat.match(str(rid))
        if m and len(m.groups()) >= 2:
            return (1, int(m.group(1)), int(m.group(2)))
        parts = [int(x) for x in re.findall(r'\d+', str(rid))]
        return (0, *(parts + [0] * (3 - len(parts)))[:3])
    return key


def counts_sorted(series):
    vc = series.value_counts()
    return sorted(((str(k), int(v)) for k, v in vc.items()), key=lambda x: (-x[1], x[0]))


def ev_quotes(v, limit=4):
    """registry evidence column (JSON list of {source, quote}) → '“quote” (source) · …' text."""
    lst = jl(v, default=[])
    if not isinstance(lst, list):
        return str(v)[:300] if not isnull(v) else ''
    return ' · '.join(f"“{x.get('quote', '')}” ({x.get('source', '')})" for x in lst[:limit] if isinstance(x, dict))


def jl(v, default=None):
    """json.loads that tolerates None / non-JSON strings."""
    if isnull(v) or v == '':
        return default if default is not None else {}
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return default if default is not None else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--package', required=True)
    ap.add_argument('--out', default='site')
    ap.add_argument('--reports', default=None, help='dir with public report documents (internal planning documents are never published — F7)')
    ap.add_argument('--package-zip', default=None, help='path to the whole-package zip to copy into data/package/')
    ap.add_argument('--config', default=str(HERE.parent.parent / 'config' / 'site.yaml'))
    ap.add_argument('--base-url', default=None, help='overrides config/site.yaml site.base_url')
    ap.add_argument('--releases-config', default=str(HERE.parent.parent / 'config' / 'releases.yaml'))
    ap.add_argument('--contribute-config', default=str(HERE.parent.parent / 'config' / 'contribute.yaml'))
    ap.add_argument('--scope-config', default=str(HERE.parent.parent / 'config' / 'scope.yaml'))
    ap.add_argument('--pack-config', default=str(HERE.parent.parent / 'config' / 'packs' / 'gut.yaml'))
    ap.add_argument('--sources-config', default=str(HERE.parent.parent / 'config' / 'sources.yaml'))
    ap.add_argument('--collections-config', default=str(HERE.parent.parent / 'config' / 'collections.yaml'), help='optional (another track); searched from the home page when present')
    ap.add_argument('--atlas-data', default=None, help='precomputed Atlas payload directory (taxa.json, matrix_*.bin, observations.json, obs/); optional')
    ap.add_argument('--allow-placeholder-base-url', action='store_true', help='test builds only')
    ap.add_argument('--build-date', default=None, help='overrides VERSION.json build_date (tests only)')
    ap.add_argument('--max-rows-html', type=int, default=2000)
    ap.add_argument('--show-rows-html', type=int, default=300)
    a = ap.parse_args()
    t0 = time.time()
    pkg, out = Path(a.package), Path(a.out)
    cfg_base, placeholder = read_base_url(a.config)
    base_url = a.base_url or cfg_base or placeholder
    if not base_url.endswith('/'):
        base_url += '/'
    if base_url == placeholder and not a.allow_placeholder_base_url:
        sys.exit(f'refusing to build: base_url is the placeholder {placeholder!r} (A15). Set site.base_url in config/site.yaml or pass --base-url.')
    if out.exists():
        shutil.rmtree(out)
    for d in ['studies', 'cohorts', 'samples', 'fields', 'authors/idx', 'static/vendor', 'docs', 'data/studies', 'data/cohorts', 'data/package', 'releases', 'changes', 'contribute', 'registry/scopes', 'gut', 'about', 'downloads']:
        (out / d).mkdir(parents=True, exist_ok=True)
    repo_root = HERE.parent.parent

    # ---------- load ----------
    vj = json.loads((pkg / 'VERSION.json').read_text())
    version = vj['package_version']
    build_date = a.build_date or vj['build_date']
    readme = (pkg / 'README.md').read_text(encoding='utf-8')
    m = re.search(r'data package v(\d+(?:\.\d+)*)', readme)
    readme_version_warning = None
    if not m or m.group(1) != version:
        if m and m.group(1) == vj.get('previous_version'):
            readme_version_warning = f'README heading says v{m.group(1)} while VERSION.json is {version} (package-side defect; site uses VERSION.json)'
            print('WARNING: ' + readme_version_warning, file=sys.stderr)
        else:
            sys.exit(f'README heading version {m.group(1) if m else None!r} != VERSION.json {version!r} (A7)')
    rspec = read_release_spec(a.releases_config)
    RA, RR, PA = rspec['columns']['release_added'], rspec['columns']['release_retired'], rspec['columns']['package_added']
    rel_key = release_order_key(rspec)
    reg = pd.read_csv(pkg / rspec['files']['registry'], dtype=str, keep_default_na=False)
    assert list(reg.columns) == list(rspec['registry_columns']), f"releases.csv columns {list(reg.columns)} != spec {rspec['registry_columns']}"
    assert reg.release_id.is_unique and len(reg), 'releases.csv: one row per release id'
    reg = reg.assign(_k=reg.release_id.map(rel_key)).sort_values('_k', kind='mergesort').drop(columns='_k').reset_index(drop=True)
    release_id = vj.get('release_id')
    assert release_id and release_id in set(reg.release_id), f'VERSION.json release_id {release_id!r} must be a releases.csv row'
    assert release_id == reg.release_id.iloc[-1], f'VERSION.json release_id {release_id!r} must be the newest releases.csv row ({reg.release_id.iloc[-1]!r})'
    cur_rel = reg[reg.release_id == release_id].iloc[0].to_dict()
    assert cur_rel['package_version'] == version, f'releases.csv package_version {cur_rel["package_version"]} != VERSION.json {version}'
    assert cur_rel['data_tag'] == vj['release_tag'], 'releases.csv data_tag must equal VERSION.json release_tag'
    known_ids = set(reg.release_id)
    data_repo_id = read_data_repo_id(a.config)
    assert data_repo_id, 'config/site.yaml github.data_repo_id is required'
    notes_by_release = {}
    for r in reg.itertuples(index=False):
        nf = r.notes_file
        if nf and nf.startswith('RELEASE_NOTES_') and (pkg / nf).exists():
            notes_by_release[r.release_id] = (pkg / nf).read_text(encoding='utf-8')
    assert release_id in notes_by_release, f"release notes of {release_id} missing from the package"

    # catalog = curated scope gut_all (all ages)
    pack = yaml.safe_load(Path(a.pack_config).read_text(encoding='utf-8'))
    hcv = yaml.safe_load((repo_root / 'config' / 'vocab' / 'health_conditions.yaml').read_text(encoding='utf-8'))['codes']
    hc_labels = {k: (v.get('label', '') if isinstance(v, dict) else '') for k, v in hcv.items()}
    cs = pd.read_parquet(pkg / 'gut_studies.parquet')
    cw = pd.read_parquet(pkg / 'gut_sample_metadata_wide.parquet')
    cd = pd.read_parquet(pkg / 'gut_sample_determinations.parquet')
    cs = cs[cs[RR].isna()].reset_index(drop=True) if RR in cs.columns else cs
    cw = cw[cw[RR].isna()].reset_index(drop=True) if RR in cw.columns else cw
    cd = cd[cd[RR].isna()].reset_index(drop=True) if RR in cd.columns else cd
    assert cs.study_accession.is_unique and cw.sample_key.is_unique, 'catalog tables: one row per study / per sample'
    assert set(cw.study_accession) <= set(cs.study_accession), 'every catalog sample belongs to a listed study'
    assert not cd.duplicated(['sample_key', 'field_name']).any(), 'gut_sample_determinations: one current row per sample × field'
    for c in ('study_title', 'description', 'description_short', 'health_context'):
        if c in cs.columns:
            cs[c] = cs[c].map(strip_frame_tokens)
    cs = cs.sort_values(['n_samples_curated', 'study_accession'], ascending=[False, True], kind='mergesort').reset_index(drop=True)
    included = sorted(set(cs.study_accession))
    inc_set = set(included)
    infant_studies = sorted(set(cs.loc[cs.curated_source == 'infant_catalog', 'study_accession']))
    age_cats = list(pack['age_categories'].keys()) + ['unknown']
    CORE_FIELDS, KEY_FIELDS, DERIVED, infant_fields = read_field_tiers(pack)
    CONTRIB_FIELDS = CORE_FIELDS
    iv_voc = (yaml.safe_load((repo_root / 'config' / 'vocab' / 'interventions.yaml').read_text()) or {}).get('codes', {}) if (repo_root / 'config' / 'vocab' / 'interventions.yaml').exists() else {}
    all_fields = [f for f in list(pack['fields'].keys()) + [d for d in DERIVED if d not in pack['fields']] if f not in infant_fields] + infant_fields
    ls_path = repo_root / 'config' / 'vocab' / 'lifestyle.yaml'
    ls_labels = {k: (v.get('label', '') if isinstance(v, dict) else '') for k, v in (yaml.safe_load(ls_path.read_text(encoding='utf-8'))['codes'].items() if ls_path.exists() else {})}
    # item 7: the new columns may be absent from the wide table — every use goes through has_col / field_series (present → render, absent → omit)
    has_col = {f: (field_series(cw, f, DERIVED) is not None) for f in set(all_fields) | set(DERIVED) | {'lifestyle_detail', 'latitude', 'longitude', 'location_locality', 'location_region', 'location_site'}}
    n_by_study = cw.groupby('study_accession').size().to_dict()
    # item 11: run-level table (optional) → per-study sequencing block + Gbp/run on the studies index
    runs_path = pkg / 'gut_runs.parquet'
    seq_by_study = gbp_stats(pd.read_parquet(runs_path)) if runs_path.exists() else {}

    # registry tier
    sspec = read_scope_spec(a.scope_config)
    vocabs = read_vocabs(repo_root, sspec)
    reg_path = pkg / sspec['files']['studies']
    rg = pd.read_parquet(reg_path)
    for _c in (sspec.get('registry_columns_added') or {}):
        if _c not in rg.columns:
            rg[_c] = 0 if _c.startswith('n_') else ''
    rg = rg[[c for c in sspec['registry_columns'] if c in rg.columns] + [c for c in rg.columns if c not in sspec['registry_columns']]]
    assert list(rg.columns) == list(sspec['registry_columns']), f"{reg_path.name} columns differ from config/scope.yaml registry_columns: {sorted(set(rg.columns) ^ set(sspec['registry_columns']))}"
    assert rg.study_accession.is_unique, 'registry_studies: one row per study'
    rg = rg[rg[RR].isna()].reset_index(drop=True)
    for col, allowed in (('classification_stage', sspec['classification_stages']), ('host_human', sspec['host_human_values']), ('access', sspec['access_values']),
                         ('assay', sspec['assay_values']), ('in_infant_catalog', sspec['in_infant_catalog_values'])):
        bad = set(rg[col].dropna().astype(str)) - set(map(str, allowed))
        assert not bad, f'registry_studies.{col} outside the vocabulary: {sorted(bad)[:5]}'
    _codes = set(vocabs['body_site']); _bad = {c for v in rg.body_sites.dropna() for c in split_list(v)} - _codes
    assert not _bad, f'registry_studies.body_sites codes outside config/vocab/body_sites.yaml: {sorted(_bad)[:5]}'
    _codes = set(vocabs['life_stage']); _bad = {c for v in rg.life_stages.dropna() for c in split_list(v)} - _codes
    assert not _bad, f'registry_studies.life_stages codes outside config/vocab/life_stages.yaml: {sorted(_bad)[:5]}'
    assert (set(rg[RA].dropna().astype(str)) | set(rg[RR].dropna().astype(str))) <= known_ids, 'registry_studies: release ids missing from releases.csv'
    assert inc_set <= set(rg.study_accession), 'F13: every catalog study is a registry study'
    for c in ('health_context', 'description_short', 'study_title'):
        rg[c] = rg[c].map(strip_frame_tokens)
    rg_by = rg.set_index('study_accession')

    # papers + authors: registry tables (all studies) ∪ the curated infant links/authors
    rp = pd.read_parquet(pkg / 'registry_study_papers.parquet') if (pkg / 'registry_study_papers.parquet').exists() else pd.DataFrame(columns=['study_accession', 'paper_id', 'pmid', 'pmcid', 'doi', 'title', 'relation', 'year', 'journal'])
    rp = rp[rp[RR].isna()] if RR in rp.columns else rp
    papers = rp[['study_accession', 'paper_id', 'pmid', 'pmcid', 'doi', 'title', 'relation']].copy()
    papers['year'] = rp['year'] if 'year' in rp.columns else None
    papers['journal'] = rp['journal'] if 'journal' in rp.columns else None
    if (pkg / 'study_paper_links.csv').exists():
        spl = pd.read_csv(pkg / 'study_paper_links.csv', dtype={'paper_id': str, 'pmid': str, 'pmcid': str})
        spl = spl[spl[RR].isna()] if RR in spl.columns else spl
        spl = spl.assign(relation='curated', year=None, journal=None)[['study_accession', 'paper_id', 'pmid', 'pmcid', 'doi', 'title', 'relation', 'year', 'journal']]
        papers = pd.concat([papers, spl], ignore_index=True)
    papers['paper_id'] = papers.paper_id.astype(str)
    papers = papers[papers.study_accession.isin(inc_set)].drop_duplicates(['study_accession', 'paper_id']).reset_index(drop=True)
    papers['title'] = papers.title.map(strip_frame_tokens).map(lambda t: None if isnull(t) else re.sub(r'<[^>]+>', '', str(t)))
    au_parts = []
    if (pkg / 'registry_authors.parquet').exists():
        ra = pd.read_parquet(pkg / 'registry_authors.parquet')
        ra = ra[ra[RR].isna()] if RR in ra.columns else ra
        au_parts.append(ra[['study_accession', 'pmid', 'position', 'author_display', 'author_key', 'is_first', 'is_last']])
    if (pkg / 'authors.parquet').exists():
        ia = pd.read_parquet(pkg / 'authors.parquet')
        au_parts.append(ia[['study_accession', 'pmid', 'position', 'author_display', 'author_key', 'is_first', 'is_last']])
    authors = pd.concat(au_parts, ignore_index=True) if au_parts else pd.DataFrame(columns=['study_accession', 'pmid', 'position', 'author_display', 'author_key', 'is_first', 'is_last'])
    authors = authors[authors.study_accession.isin(inc_set)].dropna(subset=['author_key']).drop_duplicates(['study_accession', 'author_key']).reset_index(drop=True)
    authors['pmid'] = authors.pmid.astype(str)
    authors['author_display'] = authors.author_display.astype(str)
    leaks = [v for v in authors.author_display if ORG_LEAK_RE.search(v)]
    assert not leaks, f'F1: author strings leak submission ids or e-mail addresses: {leaks[:3]}'

    # curated infant cohorts (names + membership) — used to name the all-age cohorts
    coh = pd.read_csv(pkg / 'cohorts.csv', dtype=str, keep_default_na=False) if (pkg / 'cohorts.csv').exists() else pd.DataFrame(columns=['cohort_id', 'cohort_name', 'study_accessions'])

    # Sandpiper panels where they exist (infant studies) — optional
    # R2026.20: one genus panel for EVERY profiled catalog project (gut_sandpiper_study_panels); the infant-only panels are the fallback
    panels = pd.read_parquet(pkg / 'gut_sandpiper_study_panels.parquet') if (pkg / 'gut_sandpiper_study_panels.parquet').exists() else (
        pd.read_parquet(pkg / 'sandpiper_study_panels.parquet') if (pkg / 'sandpiper_study_panels.parquet').exists() else pd.DataFrame())

    # ---------- stats (all from tables) ----------
    n_reg_gut_candidates = int(rg.body_sites.map(lambda v: 'gut_stool' in split_list(v)).sum())
    stats = dict(
        n_studies=len(cs), n_samples=len(cw), n_determinations=len(cd), n_infant_studies=len(infant_studies),
        n_infant_scope=int(cw.infant_scope.fillna(False).astype(bool).sum()),
        n_papers=int(papers.paper_id.nunique()), n_links=len(papers), n_studies_with_paper=int(papers.study_accession.nunique()),
        n_authors=int(authors.author_key.nunique()), n_author_rows=len(authors), n_studies_with_author=int(authors.study_accession.nunique()),
        n_registry=len(rg), n_registry_runs=int(rg.n_runs.fillna(0).sum()), n_registry_human=int(rg.host_human.isin(['yes', 'mixed']).sum()), n_registry_scopes=len(sspec['scopes']),
        n_registry_host={k: int((rg.host_human == k).sum()) for k in sspec['host_human_values']}, n_registry_host_yes=int((rg.host_human == 'yes').sum()),
        n_curated_samples=int((pd.to_numeric(cw.n_fields_with_value, errors='coerce').fillna(0) >= 1).sum()) if 'n_fields_with_value' in cw.columns else int(cw[[f for f in CORE_FIELDS + KEY_FIELDS if f in cw.columns]].notna().any(axis=1).sum()),
        n_registry_biosamples=int(pd.to_numeric(rg.get('n_biosamples_harvested', pd.Series(dtype=float)), errors='coerce').fillna(0).sum()),
        n_reg_gut_candidates=n_reg_gut_candidates, n_runs=int(cs.n_runs.fillna(0).sum()), n_runs_sandpiper=int(cs.n_runs_sandpiper.fillna(0).sum()),
        n_studies_sandpiper=int((cs.n_runs_sandpiper.fillna(0) > 0).sum()),
        age_counts=[(c, int((cw.age_category == c).sum())) for c in age_cats if (cw.age_category == c).any()],
        coverage_all={f: round(float(field_series(cw, f, DERIVED).notna().mean()), 4) for f in CORE_FIELDS + KEY_FIELDS if has_col.get(f)},
        routes={r: int((cd.route == r).sum()) for r in ROUTES}, n_countries=int(cw.country.nunique()), n_conditions=int(cw.health_condition.nunique()),
        n_gut_primary=int((cw.body_site_class == 'primary').sum()), n_site_unknown=int((cw.body_site_class == 'unknown').sum()), n_site_excluded=int(cw.body_site_class.isin(['excluded', 'linked']).sum()),
        has_registry=True,
    )
    gen_sha = vj.get('generator_git_sha', 'nogit')
    doi = cur_rel.get('doi') or ''
    concept_doi = read_concept_doi(a.config)
    names = read_site_names(a.config)
    cspec = read_contribute_spec(a.contribute_config)
    about = read_about(a.config)
    issue_repo, issue_template, issue_label = read_issue_cfg(a.config)
    collections = read_collections(a.collections_config)

    def flag_url(acc, page):
        return simple_issue_url(issue_repo, issue_template, issue_label, acc, release_id, base_url + page)
    site = dict(title=names['title'], short_title=names['short_title'], tagline=names['tagline'], version=version, release_tag=vj['release_tag'], build_date=build_date,
                sha8=gen_sha[:8], base_url=base_url, issue_repo=issue_repo, issue_template=issue_template, issue_label=issue_label, about=about, nav=NAV,
                core_fields=CORE_FIELDS, key_fields=KEY_FIELDS, has_collections=bool(collections),
                release_id=release_id, previous_release_id=vj.get('previous_release_id'), release_date=cur_rel['release_date'], doi=doi, concept_doi=concept_doi,
                data_release_url=rspec['site_pages']['data_release_url'].format(data_tag=cur_rel['data_tag']) if cur_rel['data_tag'] else None,
                zenodo_badge=f'https://zenodo.org/badge/{data_repo_id}.svg', zenodo_latest=f'https://zenodo.org/badge/latestdoi/{data_repo_id}', data_repo_id=data_repo_id,
                releases_page=rspec['site_pages']['releases_index'], changes_page=rspec['site_pages']['changes_index'],
                description=f"{names['title']}: every public human shotgun-metagenome study (registry) and a curated, evidence-linked catalog of human gut metagenomes of all ages with per-sample metadata.",
                citation=f'{names["title"]}, release {release_id} (data package {version}), OlmLab, {cur_rel["release_date"]}.' + (f' doi:{doi}' if doi else ''),
                sri=json.loads((HERE / 'static' / 'vendor' / 'SRI.json').read_text()), has_contribute=True, contribute_page='contribute/index.html',
                has_registry=True, registry_page='registry/index.html', data_repo=read_github_repo(a.config, 'data'))

    env = Environment(loader=FileSystemLoader(HERE / 'templates'), autoescape=select_autoescape(['html']))
    env.filters.update(fmt=f_fmt, pct=f_pct, pct1=f_pct1, num2=f_num2, numint=f_numint)
    site['analytics'] = read_analytics(a.config)
    env.globals.update(site=site)
    written = []

    def render(tpl, path, root, nav=None, crumbs=None, **ctx):
        html = env.get_template(tpl).render(root=root, nav=nav, crumbs=crumbs, page_path=path, **ctx)
        (out / path).parent.mkdir(parents=True, exist_ok=True)
        (out / path).write_text(html, encoding='utf-8')
        written.append(path)

    # ---------- static ----------
    for f in sorted((HERE / 'static').rglob('*')):
        if f.is_file():
            rel = f.relative_to(HERE / 'static')
            (out / 'static' / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, out / 'static' / rel)
    (out / '.nojekyll').write_text('')
    rep = Path(a.reports) if a.reports else None

    # ---------- data files ----------
    IN_DATA = ['gut_sample_metadata_wide.parquet', 'gut_studies.parquet', 'gut_sample_determinations.parquet', sspec['files']['studies']]
    for name in ('registry_biosamples.parquet', 'registry_study_papers.parquet', 'registry_authors.parquet'):
        if (pkg / name).exists():
            IN_DATA.append(name)
    for name in IN_DATA:
        shutil.copyfile(pkg / name, out / 'data' / name)
    pkg_files = []
    for f in sorted(pkg.iterdir()):
        if not f.is_file() or f.name == 'build_counts.json':
            continue
        if f.name not in IN_DATA:
            shutil.copyfile(f, out / 'data' / 'package' / f.name)
        meta = vj['tables'].get(f.name, {})
        pkg_files.append(dict(name=f.name, size=human(f.stat().st_size), bytes=f.stat().st_size, rows=meta.get('rows'),
                              sha256=meta.get('sha256', ''), href=('data/' if f.name in IN_DATA else 'data/package/') + f.name,
                              desc=FILE_DESC.get(f.name, ''), group=('catalog' if f.name.startswith('gut_') else 'registry' if f.name.startswith('registry_') else
                                                                     'release' if f.name in ('releases.csv', 'VERSION.json', 'README.md', 'DATA_DICTIONARY.md', 'CHANGELOG.md', 'getting_started.ipynb') or f.name.startswith('RELEASE_NOTES_') else 'infant')))
    zip_name, zip_size, zip_href = None, None, None
    if a.package_zip:
        zip_name = f'data_package_v{version}.zip'
        _zb = Path(a.package_zip).stat().st_size
        zip_size = human(_zb)
        if _zb > 90 * 1024 * 1024:  # GitHub Pages refuses files > 100 MB
            zip_href = f"https://github.com/{site['data_repo']}/releases/download/data-v{version}/{zip_name}"
        else:
            shutil.copyfile(a.package_zip, out / 'data' / 'package' / zip_name)
            zip_href = f'data/package/{zip_name}'

    cw_sorted = cw.sort_values(['study_accession', 'subject_id', 'timepoint_label', 'sample_key'], na_position='last', kind='mergesort')
    study_dl = {}
    for acc, g in cw_sorted.groupby('study_accession', sort=True):
        write_csv_gz(g, out / 'data' / 'studies' / f'{acc}.csv.gz')
        g.to_parquet(out / 'data' / 'studies' / f'{acc}.parquet', index=False)
        study_dl[acc] = dict(csv_size=human((out / 'data' / 'studies' / f'{acc}.csv.gz').stat().st_size), parquet_size=human((out / 'data' / 'studies' / f'{acc}.parquet').stat().st_size))
    cd_sorted = cd.sort_values(['study_accession', 'sample_key', 'field_name'], kind='mergesort')
    for acc, g in cd_sorted.groupby('study_accession', sort=True):
        p = out / 'data' / 'studies' / f'{acc}_determinations.csv.gz'
        write_csv_gz(g, p)
        study_dl.setdefault(acc, dict(csv_size='0 B', parquet_size='0 B'))
        study_dl[acc].update(det_size=human(p.stat().st_size), det_rows=len(g))
    for acc in included:
        if acc not in study_dl:  # catalog study without harvested samples: empty slices keep every study page's links valid
            write_csv_gz(cw.iloc[0:0], out / 'data' / 'studies' / f'{acc}.csv.gz')
            cw.iloc[0:0].to_parquet(out / 'data' / 'studies' / f'{acc}.parquet', index=False)
            study_dl[acc] = dict(csv_size=human((out / 'data' / 'studies' / f'{acc}.csv.gz').stat().st_size), parquet_size=human((out / 'data' / 'studies' / f'{acc}.parquet').stat().st_size))
        if 'det_rows' not in study_dl[acc]:  # studies without a single committed value still get a header-only evidence file (no broken link)
            p = out / 'data' / 'studies' / f'{acc}_determinations.csv.gz'
            write_csv_gz(cd.iloc[0:0], p)
            study_dl[acc].update(det_size=human(p.stat().st_size), det_rows=0)
    print(f'[{time.time()-t0:.0f}s] data files written', file=sys.stderr)

    # ---------- docs (markdown -> html) ----------
    def md_to_html(text):
        html = markdown.markdown(text, extensions=['tables', 'fenced_code', 'toc'])
        return re.sub(r'<a href="([^"]+)">', lambda m: m.group(0) if m.group(1).startswith(('http', '#')) else '<a>', html)
    dictionary = (pkg / 'DATA_DICTIONARY.md').read_text(encoding='utf-8')
    docs = [('README.md', readme, 'README'), ('DATA_DICTIONARY.md', dictionary, 'Data dictionary'), ('CHANGELOG.md', (pkg / 'CHANGELOG.md').read_text(encoding='utf-8'), 'Changelog')]
    for name, title in [('REGISTRY_REPORT.md', 'Registry report'), ('SANDPIPER_REPORT.md', 'Sandpiper report'), ('AUTHORS_REPORT.md', 'Authors report (infant extension)'), ('DATA_MODEL_FIX_REPORT.md', 'Data-model fix report (infant extension)')]:
        if (pkg / name).exists():
            docs.append((name, (pkg / name).read_text(encoding='utf-8'), title))
    for name, title in PUBLIC_REPORT_DOCS:
        if rep and (rep / name).exists() and name not in [d[0] for d in docs]:
            docs.append((name, (rep / name).read_text(encoding='utf-8'), title))
    for name, title in [('COLLECTIONS.md', 'Collections: how membership rules work and how to propose one'), ('GUT_SANDPIPER_REPORT.md', 'Sandpiper profiles for the whole catalog (report)')]:
        cand = [q for q in ((pkg / name), (HERE.parent.parent / 'docs' / name)) if q.exists()]
        if cand and name not in [d[0] for d in docs]:
            docs.append((name, cand[0].read_text(encoding='utf-8'), title))
    doc_list = []
    for name, text, title in docs:
        (out / 'docs' / name).write_text(text, encoding='utf-8')
        render('doc.html', f'docs/{name[:-3]}.html', '../', nav='about', doc_title=title, md_name=name, body=md_to_html(text),
               crumbs=[dict(label='Home', href='../index.html'), dict(label='About', href='../about/index.html'), dict(label='Methods', href='../about/methods.html'), dict(label=title)])
        doc_list.append(dict(name=name, title=title, href=f'docs/{name[:-3]}.html'))

    # ---------- per-study helpers ----------
    papers_by_study = {}
    for r in papers.sort_values(['study_accession', 'relation', 'paper_id'], kind='mergesort').itertuples(index=False):
        papers_by_study.setdefault(r.study_accession, []).append(dict(
            pmid=None if isnull(r.pmid) else str(r.pmid).split('.')[0], pmcid=None if isnull(r.pmcid) else r.pmcid,
            doi=None if isnull(r.doi) else r.doi, title=None if isnull(r.title) else r.title, relation=r.relation, year=None if isnull(r.year) else str(r.year)[:4]))
    authors_by_study = {}
    for acc, g in authors.groupby('study_accession'):
        g = g.sort_values(['pmid', 'position'], kind='mergesort')
        seen, lst = set(), []
        for r in g.itertuples(index=False):
            if r.author_display in seen:
                continue
            seen.add(r.author_display)
            lst.append(dict(name=r.author_display, key=r.author_key, first=bool(r.is_first), last=bool(r.is_last)))
        authors_by_study[acc] = lst
    panel_by_study = {}
    if len(panels):
        for acc, g in panels.groupby('study_accession'):
            if acc not in inc_set:
                continue
            d = {}
            r0 = g.iloc[0]
            for rank in ['genus']:
                gg = g[g['rank'] == rank].sort_values(['rank_order', 'taxon'], kind='mergesort')
                segs, left, ci = [], 0.0, 0
                for r in gg.itertuples(index=False):
                    w = 100 * float(r.mean_rel_abundance)
                    unassigned = str(r.taxon).startswith('unassigned')
                    color = UNASSIGNED_COLOR if unassigned else SP_PALETTE[ci % len(SP_PALETTE)]
                    if not unassigned:
                        ci += 1
                    segs.append(dict(taxon=r.taxon, label=('not assigned to a genus' if unassigned else re.sub(r'^[a-z]__', '', r.taxon)), pct=round(w, 1), left=round(left, 2), w=round(w, 2), color=color))
                    left += w
                other = max(0.0, 100 - left)
                if other > 0.05:
                    segs.append(dict(taxon='other', label='other genera', pct=round(other, 1), left=round(left, 2), w=round(other, 2), color='#FFFFFF'))
                d[rank] = dict(segs=segs, n=int(gg.n_samples_panel.iloc[0]) if len(gg) else int(r0.n_samples_panel))
            d.update(n=int(r0.n_samples_panel), taxonomy=f"{r0.taxonomy_db} {r0.taxonomy_version}", definition=str(r0.panel_definition))
            panel_by_study[acc] = d

    # per-study determination summaries: route × field counts, sample evidence rows
    det_by_study = {acc: g for acc, g in cd_sorted.groupby('study_accession', sort=True)}
    cw_by_study = {acc: g for acc, g in cw_sorted.groupby('study_accession', sort=True)}
    if 'seq_gbp' in cw.columns:
        _gb = pd.to_numeric(cw['seq_gbp'], errors='coerce'); DEPTH_REPO['counts'] = depth_bin_counts(_gb[_gb > 0])
    SAMPLE_COLS = ['sample_key', 'biosample_accession', 'age_category', 'age_at_collection_days', 'age_at_collection_days__route', 'sex', 'bmi', 'country', 'health_condition',
                   'health_condition__route', 'intervention', 'antibiotic_exposure', 'subject_id', 'timepoint_label', 'body_site_class', 'seq_gbp', 'n_runs']

    _pf = list(pack['fields'].keys()) + [f for f in DERIVED if f not in pack['fields']]
    PAGE_FIELDS = list(dict.fromkeys(CORE_FIELDS + KEY_FIELDS + [f for f in _pf if f not in ('collection_year',)] + infant_fields))
    SHORT_LABELS = {'age_category': 'age cat.', 'age_at_collection_days': 'age (d)', 'health_condition': 'condition', 'health_condition_detail': 'condition detail',
                    'antibiotic_exposure': 'antibiotics', 'subject_id': 'subject', 'timepoint_label': 'timepoint', 'collection_date': 'date', 'location_region': 'region',
                    'location_locality': 'locality', 'location_site': 'site', 'detailed_location': 'location', 'latitude': 'lat', 'longitude': 'lon',
                    'lifestyle_detail': 'lifestyle detail', 'diet_detail': 'diet detail', 'smoking_status': 'smoking', 'medication_detail': 'medication detail',
                    'intervention_detail': 'intervention detail', 'stool_consistency_bristol': 'Bristol', 'delivery_mode': 'delivery', 'feeding_mode': 'feeding',
                    'preterm_status': 'preterm', 'gestational_age_weeks': 'GA (wk)', 'birth_weight_grams': 'birth wt (g)', 'maternal_antibiotics': 'maternal abx',
                    'probiotic_exposure': 'probiotic', 'hmo_supplementation': 'HMO', 'nec_status': 'NEC'}

    # ---------- cohorts (all ages): studies linked by a shared paper (own_data / curated links) or a curated cohort record ----------
    parent = {acc: acc for acc in included}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)
    # a paper links studies into one cohort only when it describes the data (own_data / curated) and covers at most MAX_COHORT_PAPER_STUDIES
    # catalog studies — larger paper fan-outs are re-analyses / resource papers that would chain unrelated cohorts together
    MAX_COHORT_PAPER_STUDIES = 4
    # registry links only (the infant curated links include re-analysis papers that would chain cohorts): own_data papers, plus
    # 'unsure' papers whose 2–4 catalog studies were all submitted by the same centre (a study series of one group)
    _reg_links = papers[papers.relation.isin(['own_data', 'unsure'])]
    _fan = _reg_links.groupby('paper_id').study_accession.nunique()
    _center = cs.set_index('study_accession').center_name.fillna('').astype(str)
    _same_center = _reg_links.assign(_c=_reg_links.study_accession.map(_center)).groupby('paper_id')._c.agg(lambda x: x.nunique() == 1 and (x.iloc[0] != ''))
    _ok_own = _reg_links.groupby('paper_id').relation.agg(lambda x: 'own_data' in set(x))
    _keep = _fan.index[(_fan >= 2) & (_fan <= MAX_COHORT_PAPER_STUDIES) & (_ok_own.astype(bool) | _same_center.astype(bool))]   # astype: an empty papers table yields str/object dtypes
    link_papers = _reg_links[_reg_links.paper_id.isin(_keep)]
    paper_rule = {pid: ('shared own-data paper' if _ok_own.get(pid) else 'shared paper, same submitting centre') for pid in _keep}
    curated_members = {}
    in_curated = set()
    for r in coh.itertuples(index=False):
        accs = sorted(x for x in str(getattr(r, 'included_study_accessions', '') or getattr(r, 'study_accessions', '') or '').split('|') if x in inc_set and x not in in_curated)
        if len(accs) >= 2:  # curated records are fixed groups; a study belongs to at most one, and paper links never merge two records
            curated_members[r.cohort_id] = (r.cohort_name, accs)
            in_curated.update(accs)
            for x in accs[1:]:
                union(accs[0], x)
    for pid, g in link_papers.groupby('paper_id'):
        accs = sorted(set(g.study_accession) - in_curated)
        for x in accs[1:]:
            union(accs[0], x)
    comps = {}
    for acc in included:
        comps.setdefault(find(acc), []).append(acc)
    title_by_acc = cs.set_index('study_accession').study_title.to_dict()
    cohorts = []
    used_ids = set()
    for root_acc, accs in sorted(comps.items()):
        if len(accs) < 2:
            continue
        accs = sorted(accs)
        cur = [(cid, nm) for cid, (nm, mem) in curated_members.items() if set(mem) & set(accs)]
        if cur:
            cid, name = sorted(cur)[0]
            cid = str(cid)
        else:
            cid = 'PC' + hashlib.sha1(('|'.join(accs)).encode()).hexdigest()[:8].upper()
            shared = link_papers[link_papers.study_accession.isin(accs)].groupby('paper_id').agg(n=('study_accession', 'nunique'), title=('title', 'first')).sort_values(['n', 'title'], ascending=[False, True])
            name = (shared.title.dropna().iloc[0] if len(shared.title.dropna()) else f'Studies sharing papers ({accs[0]} …)')
        while cid in used_ids:
            cid += '_'
        used_ids.add(cid)
        pids = sorted(set(link_papers.loc[link_papers.study_accession.isin(accs), 'paper_id']))
        m = cw[cw.study_accession.isin(accs)]
        rule = 'curated cohort record' if cur else ('shared own-data paper' if any(paper_rule.get(pid) == 'shared own-data paper' for pid in pids) else 'shared paper, same submitting centre')
        cohorts.append(dict(cohort_id=cid, cohort_name=str(name)[:160], curated=bool(cur), rule=rule, studies=accs, n_studies=len(accs), n_samples=int(len(m)), paper_ids=pids, n_papers=len(pids),
                            age_categories=counts_sorted(m.age_category.fillna('unknown')), countries=counts_sorted(m.country.dropna())[:6],
                            conditions=counts_sorted(m.health_condition.dropna())[:6], n_subjects=int(m.subject_id.nunique())))
    cohorts.sort(key=lambda c: (-c['n_samples'], c['cohort_id']))
    cohort_of_study = {acc: c['cohort_id'] for c in cohorts for acc in c['studies']}
    cohort_name_of = {c['cohort_id']: c['cohort_name'] for c in cohorts}
    cohort_dl = {}
    for c in cohorts:
        g = cw_sorted[cw_sorted.study_accession.isin(c['studies'])]
        write_csv_gz(g, out / 'data' / 'cohorts' / f"{c['cohort_id']}.csv.gz")
        g.to_parquet(out / 'data' / 'cohorts' / f"{c['cohort_id']}.parquet", index=False)
        cohort_dl[c['cohort_id']] = dict(csv_size=human((out / 'data' / 'cohorts' / f"{c['cohort_id']}.csv.gz").stat().st_size), parquet_size=human((out / 'data' / 'cohorts' / f"{c['cohort_id']}.parquet").stat().st_size))
    pd.DataFrame([dict(cohort_id=c['cohort_id'], cohort_name=c['cohort_name'], rule=c['rule'], n_studies=c['n_studies'], n_samples=c['n_samples'], study_accessions='|'.join(c['studies']), paper_ids='|'.join(c['paper_ids'])) for c in cohorts]).to_csv(out / 'data' / 'catalog_cohorts.csv', index=False)
    stats['n_cohorts'] = len(cohorts)
    stats['n_studies_in_cohorts'] = len(cohort_of_study)

    # ---------- contribute worklist (all ages, computed from gut_studies coverage) ----------
    thr = float(cspec['missing_threshold'])
    TLABEL = cspec.get('contribution_type_labels', cspec['contribution_types'])
    wl_rows = []
    for r in cs.itertuples(index=False):
        n = int(0 if pd.isna(r.n_samples_curated) else r.n_samples_curated)
        if n < CONTRIB_MIN_SAMPLES:
            continue
        cov_of = {f: float(getattr(r, f'cov_{f}', 0) or 0) for f in CONTRIB_FIELDS}   # gut_studies.cov_<field>; a field without a cov_ column counts as 0 coverage
        missing = [f for f in CONTRIB_FIELDS if cov_of[f] < thr]
        if len(missing) < CONTRIB_MIN_MISSING:
            continue
        n_pap = int(0 if pd.isna(r.n_linked_papers) else r.n_linked_papers)
        ctype = 'per_sample_table' if n_pap else 'paper_pointer'
        blocker = ('no_linked_paper' if not n_pap else ('partial_coverage' if (r.curated_depth or '') and 'R2' in str(r.curated_depth) else 'no_supplement_found'))
        wl_rows.append(dict(acc=r.study_accession, title=(r.study_title or '')[:140], n=n, missing=missing, n_missing=len(missing), coverage=cov_of,
                            papers=n_pap, ctype=ctype, type_label=TLABEL.get(ctype, ctype), blocker=blocker, blocker_label=cspec.get('blocker_labels', {}).get(blocker, blocker),
                            depth=r.curated_depth or '', ages=' '.join(f'{k}:{v}' for k, v in sorted(jl(r.age_categories).items(), key=lambda kv: -kv[1])[:3]),
                            issue_url=contribute_issue_url(cspec, r.study_accession, ctype, release_id), score=n * len(missing),
                            cohort=cohort_name_of.get(cohort_of_study.get(r.study_accession), ''),
                            # R2026.18: source access — a person can often open what we could not (paywalled / publisher-only supplements)
                            supp=str(getattr(r, 'src_supplement', '') or 'none'), ft=str(getattr(r, 'src_fulltext', '') or 'none'),
                            paper_url=next((('https://doi.org/' + x['doi']) if x.get('doi') else (f"https://europepmc.org/article/MED/{x['pmid']}" if x.get('pmid') else '')
                                            for x in papers_by_study.get(r.study_accession, []) if x.get('doi') or x.get('pmid')), '')))
    wl_rows.sort(key=lambda r: (-r['score'], r['acc']))
    for i, r in enumerate(wl_rows, start=1):
        r['rank'] = i
    help_by_study = {r['acc']: r for r in wl_rows}
    stats['n_contribute'] = len(wl_rows)
    stats['n_contribute_samples'] = int(sum(r['n'] for r in wl_rows))
    stats['has_contribute'] = True

    # ---------- per-project source checkmarks (R2026.18; gut_studies.src_* from catalog.scopes.study_sources) ----------
    SRC_ORDER = ('archive', 'abstract', 'fulltext', 'supplement', 'external', 'contribution', 'verified')
    SRC_LABELS = {'archive': 'Sequence archive', 'abstract': 'Abstract / description', 'fulltext': 'Full text',
                  'supplement': 'Supplementary tables', 'external': 'External resources', 'contribution': 'User contribution',
                  'verified': 'Human verified'}
    SRC_SHORT = {'archive': 'Archive', 'abstract': 'Abstract', 'fulltext': 'Full text', 'supplement': 'Supp.', 'external': 'External',
                 'contribution': 'User', 'verified': 'Verified'}
    SRC_NONE = {'archive': 'no usable values in the archive records', 'abstract': 'no abstract or project description',
                'fulltext': 'no open-access full text linked', 'supplement': 'no supplementary tables available (or none open access)',
                'external': 'not covered by the external resources we ingest', 'contribution': 'nothing uploaded yet',
                'verified': 'not yet checked by a person'}
    SRC_CHECKED = {'archive': 'records read; no usable sample values', 'abstract': 'read; no usable statement',
                   'fulltext': 'read; no usable statement or table', 'supplement': 'files read; no table could be matched to the samples',
                   'external': '', 'contribution': '', 'verified': ''}
    _srcy = yaml.safe_load(Path(a.sources_config).read_text(encoding='utf-8')) if Path(a.sources_config).exists() else {}
    RES_NAME = {r['resource_id']: r['name'] for r in (_srcy.get('related_efforts') or []) if r.get('resource_id')}
    def src_view(d):
        try:
            det = json.loads(d.get('src_detail') or '{}')
        except (TypeError, ValueError):
            det = {}
        out = []
        for k in SRC_ORDER:
            st = d.get(f'src_{k}')
            st = st if st in ('used', 'checked', 'none') else 'none'
            x = det.get(k) or {}
            if k == 'verified' and st == 'used':
                who = x.get('by') or []
                text = 'checked against the web and confirmed complete by ' + ', '.join(f"{w.get('by')} ({w.get('date')})" for w in who[:3])
            elif st == 'used':
                fl = ', '.join(f.replace('_at_collection_days', '').replace('_label', '').replace('_', ' ') for f in (x.get('fields') or [])[:8])
                text = f"{int(x.get('n_values') or 0):,} sample values" + (f" ({fl})" if fl else '')
            elif st == 'checked':
                text = SRC_CHECKED[k]
            else:
                text = SRC_NONE[k]
            res = [dict(id=r_ if r_ in RES_NAME else '', name=RES_NAME.get(r_, 'supplementary tables of other papers that re-analysed this project' if r_ == 'paper' else r_)) for r_ in (x.get('resources') or [])] if k == 'external' else []
            extra = ', '.join(x.get('pmcids') or []) if k == 'fulltext' else (', '.join(r_['name'] for r_ in res) if k == 'external' else '')
            out.append(dict(key=k, label=SRC_LABELS[k], short=SRC_SHORT[k], status=st, text=text, extra=extra, links=res))
        return out

    # ---------- studies ----------
    def study_row(r):
        d = clean(r)
        d['age_categories_d'] = jl(d.get('age_categories'))
        d['health_conditions_d'] = jl(d.get('health_conditions'))
        d['ages_short'] = ' '.join(f'{k}:{v:,}' for k, v in sorted(d['age_categories_d'].items(), key=lambda kv: (-kv[1], kv[0]))[:3])
        d['top_condition'] = (sorted(d['health_conditions_d'].items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if d['health_conditions_d'] else '')
        d['body_sites_l'] = split_list(d.get('body_sites'))
        d['life_stages_l'] = split_list(d.get('life_stages'))
        d['cohort_id'] = cohort_of_study.get(d['study_accession'])
        d['cohort_name'] = cohort_name_of.get(d['cohort_id'], '') if d['cohort_id'] else ''
        d['n_papers'] = len(papers_by_study.get(d['study_accession'], []))
        d['sources'] = src_view(d)
        d['first_author'] = next((x['name'] for x in authors_by_study.get(d['study_accession'], []) if x['first']), (authors_by_study.get(d['study_accession']) or [{}])[0].get('name', '') if authors_by_study.get(d['study_accession']) else '')
        return d
    studies = [study_row(r) for r in cs.to_dict('records')]
    # Project-sheet filters (R2026.16): per-study condition codes present among samples, sample-level intervention arms, and two
    # "cases and controls" flags — condition (≥ 3 healthy_control AND ≥ 3 samples with a disease code) and arms (≥ 3 samples in an active
    # arm AND ≥ 3 in placebo / no_intervention)
    NON_DISEASE = {'healthy_control', 'unknown', 'intervention_cohort'}
    hc_by_study = cw.dropna(subset=['health_condition']).groupby(['study_accession', 'health_condition']).size()
    arm_by_study = (cw.dropna(subset=['intervention']).assign(_c=lambda d: d.intervention.str.split(';')).explode('_c').groupby(['study_accession', '_c']).size()
                    if 'intervention' in cw.columns else pd.Series(dtype=int))
    def _codes(series, acc):
        try:
            return series.loc[acc]
        except KeyError:
            return pd.Series(dtype=int)
    def study_filters(acc):
        hcs = _codes(hc_by_study, acc); arms = _codes(arm_by_study, acc)
        cc = int(hcs.get('healthy_control', 0) >= 3 and hcs[[c for c in hcs.index if c not in NON_DISEASE]].sum() >= 3) if len(hcs) else 0
        ctrl = int(arms.get('placebo', 0)) + int(arms.get('no_intervention', 0))
        act = int(arms[[c for c in arms.index if c not in ('placebo', 'no_intervention')]].sum()) if len(arms) else 0
        return dict(hcs=sorted(hcs.index.tolist()), arms=sorted(arms.index.tolist()), ccc=cc, cca=int(ctrl >= 3 and act >= 3))
    # Studies table (owner review 2026-10-01): narrow — no depth / per-field coverage columns, samples only, Gbp per sample
    _cc_by = (cw.dropna(subset=['country']).groupby(['study_accession', 'country']).size() if 'country' in cw.columns else pd.Series(dtype=int))
    _cc = {}
    for (acc_, iso_), n_ in _cc_by.items():
        _cc.setdefault(acc_, []).append(iso_)
    sidx_rows = [dict(**study_filters(s['study_accession']), ctry=';'.join(sorted(_cc.get(s['study_accession'], []))[:12]), pc=(s.get('population_condition') or '') if not isnull(s.get('population_condition')) else '',
                      dz=(s.get('intervention_design') or '') if not isnull(s.get('intervention_design')) else '', ags=sorted(k for k in s['age_categories_d'] if k != 'unknown'),
                      a=s['study_accession'], t=((s.get('short_title') if not isnull(s.get('short_title')) else None) or s['study_title'] or '')[:160], tf=(s['study_title'] or '')[:300], n=int(s['n_samples_curated'] or 0), ag=s['ages_short'], hc=s['top_condition'],
                      iv=(s.get('interventions') or '') if not isnull(s.get('interventions')) else '', co=_top_key(s.get('top_country')), ls=s.get('life_stage_primary') or '',
                      src=s['curated_source'], sf=''.join({'used': 'u', 'checked': 'c'}.get(x['status'], 'n') for x in s['sources']), fa=s['first_author'], p=s['n_papers'], y=(s.get('first_public_min') or '')[:4],
                      gs=(round(seq_by_study[s['study_accession']]['gbp_per_sample'], 2) if seq_by_study.get(s['study_accession'], {}).get('gbp_per_sample') is not None else None))
                 for s in studies]
    (out / 'data' / 'studies_index.json').write_text(dumps(sidx_rows), encoding='utf-8')
    sys.path.insert(0, str(HERE)) if str(HERE) not in sys.path else None
    from pages import api as api_page   # R2026.16: static API + llms.txt + For-LLMs page
    api_stats = api_page.build(render, out, dict(studies=studies, sidx_rows=sidx_rows, cw=cw, papers_by_study=papers_by_study, base_url=base_url,
                                                 site_title=names['title'], version=version, release_id=release_id,
                                                 hc_labels=hc_labels, iv_voc=iv_voc, age_cats=age_cats, stats=stats, citation=env.globals['site'].get('citation', '')))
    print(f'[{time.time()-t0:.0f}s] api: {api_stats}', file=sys.stderr)
    _ivc = {}
    for r_ in sidx_rows:
        for c_ in set(x for x in r_['iv'].split(';') if x) | set(x for x in r_['arms'] if x not in ('placebo', 'no_intervention')):
            _ivc[c_] = _ivc.get(c_, 0) + 1
    _hcs = sorted({c for r_ in sidx_rows for c in r_['hcs']} | {r_['pc'] for r_ in sidx_rows if r_['pc']})
    render('studies_index.html', 'studies/index.html', '../', nav='studies', use_datatables=True, stats=stats, n_rows=len(studies), has_seq=bool(seq_by_study),
           hc_opts=[(c, hc_labels.get(c, '')) for c in _hcs if c not in ('unknown',)], iv_opts=[(c, (iv_voc.get(c) or {}).get('label', c), _ivc[c]) for c in iv_voc if c in _ivc],
           design_opts=[d for d in ('randomized_controlled_trial', 'non_randomized_controlled', 'crossover', 'single_arm_before_after', 'observational_with_procedure') if any(r_['dz'] == d for r_ in sidx_rows)],
           age_opts=[c for c in age_cats if c != 'unknown'],
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Studies')])
    for s in studies:
        acc = s['study_accession']
        g = cw_by_study.get(acc, cw.iloc[0:0])
        n_total = len(g)
        shown = g if n_total <= a.max_rows_html else g.head(a.show_rows_html)
        dg = det_by_study.get(acc, cd.iloc[0:0])
        # Field coverage (owner 2026-10-09): the core fields always, then EVERY other field this project has values for, by coverage;
        # fields without any value are only named in one grey line
        cov, cov_missing = [], []
        for f in PAGE_FIELDS:
            ser = field_series(g, f, DERIVED)
            if ser is None:
                continue
            has = ser.notna() & (ser.astype(str) != 'unknown')
            n_ = int(has.sum())
            tier = 'core' if f in CORE_FIELDS else ('infant' if f in infant_fields else ('key' if f in KEY_FIELDS else 'more'))
            if n_ == 0 and tier != 'core':
                cov_missing.append(f)
                continue
            rc = dg.loc[dg.field_name == f, 'route'].value_counts() if len(dg) else pd.Series(dtype=int)
            cov.append(dict(field=f, label=LABELS.get(f, f), n=n_, frac=float(has.mean()) if n_total else 0.0, routes={r: int(rc.get(r, 0)) for r in ROUTES}, infant=tier == 'infant', tier=tier))
        cov = [c for c in cov if c['tier'] == 'core'] + sorted([c for c in cov if c['tier'] != 'core'], key=lambda c: (-c['n'], c['field']))
        # Sample table: every field with a value (core first, then by coverage), then the empty ones as thin strips
        have_cols = [c['field'] for c in cov if c['n'] > 0]
        empty_cols = [c['field'] for c in cov if c['n'] == 0] + cov_missing
        tcols = [dict(f=f, label=SHORT_LABELS.get(f, f.replace('_', ' ')), empty=False) for f in have_cols] + \
                [dict(f=f, label=SHORT_LABELS.get(f, f.replace('_', ' ')), empty=True) for f in empty_cols]
        group_rows = dg[dg.scope == 'study_all'].drop_duplicates(['field_name', 'value_normalized']).sort_values(['route', 'field_name'], kind='mergesort') if len(dg) else dg
        group_stmts = [clean(x) for x in group_rows[['field_name', 'value_normalized', 'route', 'confidence', 'evidence_source', 'evidence_locator', 'evidence_quote']].head(40).to_dict('records')]
        ev_sample = dg[dg.scope != 'study_all'].assign(_src=lambda d: d.evidence_source.astype(str).str.split('.').str[:2].str.join('.')).drop_duplicates(['field_name', 'route', '_src']).sort_values(['field_name', 'route'], kind='mergesort').head(40) if len(dg) else dg
        ev_rows_study = [clean(x) for x in ev_sample[['sample_key', 'field_name', 'value_normalized', 'route', 'confidence', 'evidence_source', 'evidence_locator', 'evidence_quote']].to_dict('records')]
        ages = counts_sorted(g.age_category.fillna('unknown'))
        sites = counts_sorted(g.body_site_class.fillna('unknown'))
        conds = counts_sorted(g.health_condition.dropna())[:8]
        countries = counts_sorted(g.country.dropna())[:8]
        sexes = counts_sorted(g.sex.dropna())
        flag = flag_url(acc, f'studies/{acc}.html')   # item 8: one simple form; the 'Confirm correct' button is gone
        rgr = clean(rg_by.loc[acc].to_dict()) if acc in rg_by.index else {}
        rg_evidence = [dict(kind=lab, **e) for k, lab in (('host_evidence', 'host'), ('body_site_evidence', 'body site'), ('life_stage_evidence', 'life stage')) for e in ev_rows(rgr.get(k))]
        rg_summary = registry_summary(rgr, vocabs['body_site'], vocabs['life_stage'], vocabs['assay']) if rgr else ''
        srows = []   # R2026.18: the sample table is rendered client-side from data/studies/<acc>.csv.gz (static/study_table.js)
        for c in tcols:
            c['num'] = c['f'] in ('age_at_collection_days', 'bmi', 'latitude', 'longitude', 'gestational_age_weeks', 'birth_weight_grams', 'stool_consistency_bristol')
        render('study.html', f'studies/{acc}.html', '../', nav='studies', use_datatables=True, s=s, rg=rgr, rg_summary=rg_summary, rg_evidence=rg_evidence, seq=seq_by_study.get(acc),
               papers=papers_by_study.get(acc, []), study_authors=authors_by_study.get(acc, []), cov=cov, ages=ages, sites=sites, conds=conds, countries=countries, sexes=sexes,
               group_stmts=group_stmts, iv_labels={c: (v or {}).get('label', c) for c, v in iv_voc.items()}, ev_rows=ev_rows_study, n_det=int(len(dg)), panel=panel_by_study.get(acc), help=help_by_study.get(acc), flag_url=flag, n_core=len(CORE_FIELDS),
               samples=srows, sample_cols=SAMPLE_COLS, tcols=tcols, glance=study_glance(g), verify_url=verify_issue_url(issue_repo, acc, release_id), cov_missing=[LABELS.get(f, f) for f in cov_missing], thr_pct=int(round(100 * thr)), n_total=n_total, n_shown=len(shown), dl=study_dl[acc], hc_labels=hc_labels,
               site_labels=vocabs['body_site'], stage_labels=vocabs['life_stage'],
               crumbs=[dict(label='Home', href='../index.html'), dict(label='Studies', href='index.html'), dict(label=acc)])
    print(f'[{time.time()-t0:.0f}s] {len(studies)} study pages', file=sys.stderr)

    # ---------- cohort pages ----------
    render('cohorts_index.html', 'cohorts/index.html', '../', nav='cohorts', use_datatables=True, cohorts=cohorts, stats=stats,
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Cohorts')])
    papers_by_id = {}
    for r in papers.sort_values(['paper_id', 'study_accession']).itertuples(index=False):
        d = papers_by_id.setdefault(str(r.paper_id), dict(pmid=None if isnull(r.pmid) else str(r.pmid).split('.')[0], pmcid=None, doi=None, title=None, studies=[]))
        d['pmcid'] = d['pmcid'] or (None if isnull(r.pmcid) else r.pmcid)
        d['doi'] = d['doi'] or (None if isnull(r.doi) else r.doi)
        d['title'] = d['title'] or (None if isnull(r.title) else r.title)
        if r.study_accession not in d['studies']:
            d['studies'].append(r.study_accession)
    for c in cohorts:
        cid = c['cohort_id']
        members = [dict(acc=acc, title=title_by_acc.get(acc) or '', n_samples=int(n_by_study.get(acc, 0)), source=cs.loc[cs.study_accession == acc, 'curated_source'].iloc[0]) for acc in c['studies']]
        members.sort(key=lambda m_: (-m_['n_samples'], m_['acc']))
        cpapers = [papers_by_id[pid] for pid in c['paper_ids'] if pid in papers_by_id]
        flag = flag_url(cid, f'cohorts/{cid}.html')
        render('cohort.html', f'cohorts/{cid}.html', '../', nav='cohorts', c=c, members=members, papers=cpapers, dl=cohort_dl.get(cid, {}), flag_url=flag, hc_labels=hc_labels,
               crumbs=[dict(label='Home', href='../index.html'), dict(label='Cohorts', href='index.html'), dict(label=c['cohort_name'])])
    print(f'[{time.time()-t0:.0f}s] {len(cohorts)} cohort pages', file=sys.stderr)

    # ---------- sample explorer ----------
    hc_rows = cw.dropna(subset=['health_condition']).groupby('health_condition').agg(ns=('sample_key', 'size'), nst=('study_accession', 'nunique')).sort_values(['ns'], ascending=False)
    ls_codes = [(k, ls_labels.get(k, '')) for k, _n in counts_sorted(cw['lifestyle'].dropna())] if has_col.get('lifestyle') else []
    years = sorted({int(y) for y in field_series(cw, 'collection_year', DERIVED).dropna().astype(str).str[:4] if y.isdigit()}) if has_col.get('collection_year') else []
    # intervention filters (R2026.15): study-level list from gut_studies.interventions, sample arms from the wide `intervention` column
    iv_counts = {}
    if 'interventions' in cs.columns:
        for v in cs.interventions.dropna():
            for c in [x for x in str(v).split(';') if x]:
                iv_counts[c] = iv_counts.get(c, 0) + 1
    iv_codes = [(c, (iv_voc.get(c) or {}).get('label', c), iv_counts[c]) for c in iv_voc if c in iv_counts] + [(c, c, n) for c, n in sorted(iv_counts.items()) if c not in iv_voc]
    iv_sample_codes = []
    if 'intervention' in cw.columns and cw.intervention.notna().any():
        present = set(c for v in cw.intervention.dropna() for c in str(v).split(';') if c)
        iv_sample_codes = [(c, (iv_voc.get(c) or {}).get('label', c)) for c in iv_voc if c in present]
    render('explorer.html', 'samples/index.html', '../', nav='samples', stats=stats, age_cats=[c for c in age_cats if (cw.age_category == c).any()], iv_codes=iv_codes, iv_sample_codes=iv_sample_codes,
           hc_codes=list(hc_rows.index), countries=sorted(cw.country.dropna().unique().tolist()), n_cols=int(cw.shape[1]), has_col=has_col, ls_codes=ls_codes,
           year_min=(years[0] if years else None), year_max=(years[-1] if years else None), n_fields_max=len(CORE_FIELDS) + len(KEY_FIELDS),
           parquet_size=human((pkg / 'gut_sample_metadata_wide.parquet').stat().st_size), det_size=human((pkg / 'gut_sample_determinations.parquet').stat().st_size),
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Samples')])

    # ---------- collections (config/collections.yaml) and Atlas › PCA (Sandpiper scores in the package) ----------
    sys.path.insert(0, str(HERE))
    from pages import collections as _collections, pca as _pca
    coll_stats = _collections.build(a.collections_config, render, cw, cs, out, hc_labels=hc_labels, ls_labels=ls_labels)
    print(f'[{time.time()-t0:.0f}s] collections: {coll_stats}', file=sys.stderr)
    from pages import atlas as _atlas
    atlas_dir = Path(a.atlas_data) if a.atlas_data else None
    if atlas_dir and (atlas_dir / 'taxa.json').exists():   # precomputed taxon-map payload + observation cards (data/inputs/gut/atlas; make atlas-precompute)
        _atlas.build(env, render, dict(atlas_data_dir=atlas_dir, stats=stats, gut_studies=cs), out)
        print(f'[{time.time()-t0:.0f}s] atlas/index + observations from {atlas_dir}', file=sys.stderr)
    if (pkg / 'gut_sandpiper_pca_scores.parquet').exists():   # after the atlas copy (atlas.build replaces data/atlas/)
        pca_stats = _pca.build(env, render, dict(wide=cw, studies=cs, pkg=pkg), out)
        print(f'[{time.time()-t0:.0f}s] atlas/pca: {pca_stats.get("n_points") if isinstance(pca_stats, dict) else pca_stats}', file=sys.stderr)

    # ---------- fields ----------
    vocab_rows = {}
    in_vocab = False
    for line in dictionary.splitlines():
        if line.startswith('## Field vocabularies'):
            in_vocab = True
            continue
        if in_vocab and line.startswith('## '):
            in_vocab = False
        if in_vocab and line.startswith('| `'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            vocab_rows[cells[0].strip('`').strip()] = ' | '.join(c.strip() for c in cells[1:]).replace('` ', '').strip()
    route_by_field = cd.groupby(['field_name', 'route']).size()
    fields = []
    for f in all_fields:
        ser = field_series(cw, f, DERIVED)
        if ser is None:   # item 7: absent column → omitted (never shown as 0 %)
            continue
        has = ser.notna()
        nR = {r: int(route_by_field.get((f, r), 0)) for r in ROUTES}
        if f == 'age_category' and 'age_category_basis' in cw.columns:   # derived: route of the basis (exact age → its own route)
            bas = cw.loc[has, 'age_category_basis']
            age_r = cw.loc[has & (bas == 'age_at_collection_days'), 'age_at_collection_days__route'] if 'age_at_collection_days__route' in cw.columns else pd.Series(dtype=str)
            nR = {r: int((age_r == r).sum()) for r in ROUTES}
            nR['R1'] += int(bas.isin(['sample_life_stage', 'infant_catalog_age_scope']).sum()); nR['R3'] += int((bas == 'r3_fulltext_life_stage').sum())
            nR['R4'] += int(bas.isin(['r4_abstract_life_stage', 'study_life_stage']).sum())
        tot = max(1, sum(nR.values()))
        left, segs = 0.0, []
        for r in ROUTES:
            w = 100 * nR[r] / tot
            segs.append(dict(r=r, left=round(left, 2), w=round(w, 2), n=nR[r]))
            left += w
        spec = pack['fields'].get(f) or DERIVED.get(f) or {}
        vocab_txt = vocab_rows.get(f, '')
        if f == 'health_condition':
            vocab_txt = ', '.join(f'{k}' for k in hcv.keys())
        elif f == 'lifestyle' and ls_labels:
            vocab_txt = ', '.join(ls_labels.keys())
        elif spec.get('type') == 'enum' and spec.get('values'):
            vocab_txt = ', '.join(map(str, spec['values']))
        elif spec.get('compose'):
            vocab_txt = 'derived: ' + ', '.join(spec['compose'])
        elif spec.get('life_stage'):
            vocab_txt = ', '.join(spec.get('values') or [])
        by_cat = {c: (float(has[cw.age_category == c].mean()) if (cw.age_category == c).any() else None) for c in age_cats}
        if f == 'age_category' and 'age_category_basis' in cw.columns:
            # coverage of age_category inside its own categories is 100 % by definition (owner question 2026-10-08); show instead how
            # much of each category rests on an exact age at collection rather than a life-stage statement
            exact = cw.age_category_basis == 'age_at_collection_days'
            by_cat = {c: (float(exact[cw.age_category == c].mean()) if (cw.age_category == c).any() and c != 'unknown' else None) for c in age_cats}
        tier = 'core' if f in CORE_FIELDS else 'key' if f in KEY_FIELDS else 'infant' if f in infant_fields else 'other'
        fields.append(dict(name=f, label=LABELS.get(f, f), type=spec.get('type', 'derived' if spec.get('compose') or spec.get('from') else ''), infant=tier == 'infant', tier=tier,
                           routes_allowed=spec.get('routes', ROUTES if tier != 'infant' else []), vocab=vocab_txt, caveat=(FIELD_CAVEATS.get(f, spec.get('note', '')) + (' Per-category cells for this row: share of the category whose life stage comes from an exact age (the rest rests on a sample life-stage attribute or a cohort statement).' if f == 'age_category' else '')),
                           samples=int(has.sum()), frac=float(has.mean()), studies=int(cw.loc[has, 'study_accession'].nunique()), frac_studies=int(cw.loc[has, 'study_accession'].nunique()) / max(1, len(cs)),
                           route_segs=segs, nR=nR, by_cat=by_cat))
    TIER_ORDER = {'core': 0, 'key': 1, 'other': 2, 'infant': 3}
    fields.sort(key=lambda f: (TIER_ORDER[f['tier']], (CORE_FIELDS + KEY_FIELDS).index(f['name']) if f['name'] in CORE_FIELDS + KEY_FIELDS else 99, -f['samples'], f['name']))
    field_groups = [(t_, lab, [f for f in fields if f['tier'] == t_]) for t_, lab in (('core', 'Core fields'), ('key', 'Key fields'), ('other', 'Further fields'), ('infant', 'Infant extension'))]
    field_groups = [g_ for g_ in field_groups if g_[2]]
    conf_rows = []
    cbins = [(0.0, 0.5, '< 0.5'), (0.5, 0.7, '0.5–0.69'), (0.7, 0.85, '0.7–0.84'), (0.85, 1.01, '≥ 0.85')]
    for r in ROUTES:
        m_ = cd[cd.route == r]
        conf_rows.append(dict(route=r, label=ROUTE_LABELS[r], n=len(m_), bins=[int(((m_.confidence >= lo) & (m_.confidence < hi)).sum()) for lo, hi, _ in cbins], n_group=int((m_.scope == 'study_all').sum())))
    render('fields.html', 'fields/index.html', '../', nav='about', contrib_min_missing=CONTRIB_MIN_MISSING, fields=fields, field_groups=field_groups, age_cats=age_cats, conf_rows=conf_rows, cbins=[b[2] for b in cbins], stats=stats,
           dictionary_html=md_to_html(dictionary), hc_rows=[(k, hc_labels.get(k, ''), int(v.ns), int(v.nst)) for k, v in hc_rows.iterrows()],
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Fields')])

    # ---------- scope (how the catalog is cut from the registry) ----------
    gut_cand = rg[rg.body_sites.map(lambda v: 'gut_stool' in split_list(v))]
    funnel = [('registry studies (every ENA shotgun-metagenome study reached by the enumeration)', len(rg)), ('host human = yes or mixed', int(rg.host_human.isin(['yes', 'mixed']).sum())),
              ('… with gut / stool among the body sites', len(gut_cand)), ('… host human AND assay shotgun (catalog rule)', len(gut_cand[gut_cand.host_human.isin(['yes', 'mixed']) & (gut_cand.assay.astype(str).str.startswith('shotgun') | gut_cand.study_accession.isin(inc_set))])),
              ('catalog studies', len(cs))]
    not_in = gut_cand[~gut_cand.study_accession.isin(inc_set)]
    excl_reasons = []
    for (h, asy, stg), g_ in not_in.groupby([not_in.host_human.fillna(''), not_in.assay.fillna(''), not_in.classification_stage.fillna('')]):
        excl_reasons.append(dict(host=h, assay=asy, stage=stg, n=len(g_), n_runs=int(g_.n_runs.fillna(0).sum())))
    excl_reasons.sort(key=lambda d: (-d['n'], d['host'], d['assay'], d['stage']))
    excl_rows = [clean(x) for x in not_in.sort_values(['n_runs', 'study_accession'], ascending=[False, True], kind='mergesort').head(300)[['study_accession', 'study_title', 'host_human', 'assay', 'body_sites', 'life_stages', 'classification_stage', 'n_runs', 'n_samples']].to_dict('records')]
    other_scopes = []
    for sc in sspec['scopes']:
        m_ = rg[rg.scope_memberships.map(lambda v: sc['id'] in split_list(v))]
        other_scopes.append(dict(id=sc['id'], label=sc['label'], n_studies=len(m_), n_runs=int(m_.n_runs.fillna(0).sum()), curated=bool(sc.get('curated'))))
    render('scope.html', 'about/scope.html', '../', nav='about', use_datatables=True, funnel=funnel, excl_reasons=excl_reasons, excl_rows=excl_rows, n_excl=len(not_in), scopes=other_scopes, stats=stats,
           pack=dict(study_rule=pack.get('study_rule', ''), sample_rule=pack.get('sample_rule', ''), age_categories=pack['age_categories']), site_labels=vocabs['body_site'],
           crumbs=[dict(label='Home', href='../index.html'), dict(label='About', href='index.html'), dict(label='Scope')])

    # ---------- authors ----------
    aidx = {}
    for r in authors.sort_values(['author_key', 'study_accession'], kind='mergesort').itertuples(index=False):
        d = aidx.setdefault(r.author_key, dict(n=r.author_display, s=[], f=0))
        if r.study_accession not in d['s']:
            d['s'].append(r.study_accession)
        d['f'] += int(bool(r.is_first))
    shards = {}
    for key in sorted(aidx):
        letter = key[:1].lower() if key[:1].isalpha() and key[:1].isascii() else '_'
        shards.setdefault(letter, {})[key] = aidx[key]
    for letter in sorted(shards):
        (out / 'authors' / 'idx' / f'{letter}.json').write_text(dumps(shards[letter]), encoding='utf-8')
    render('authors.html', 'authors/index.html', '../', nav='authors', stats=stats, shard_letters=sorted(shards), n_index=len(aidx),
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Authors')])

    # ---------- releases ----------
    releases = []
    for r in reg.itertuples(index=False):
        d = {k: (v if v != '' else None) for k, v in r._asdict().items()}
        d['data_url'] = rspec['site_pages']['data_release_url'].format(data_tag=d['data_tag']) if d['data_tag'] else None
        d['site_url'] = rspec['site_pages']['site_release_url'].format(site_tag=d['site_tag']) if d['site_tag'] else None
        d['notes_html'] = md_to_html(notes_by_release[d['release_id']]) if d['release_id'] in notes_by_release else None
        d['changes_href'] = rspec['site_pages']['changes_release'].format(release_id=d['release_id']).split('/')[-1]
        d['current'] = d['release_id'] == release_id
        releases.append(d)
    releases_desc = list(reversed(releases))
    for d in releases:
        if d['notes_html'] is not None:
            (out / 'releases' / f"RELEASE_NOTES_{d['release_id']}.md").write_text(notes_by_release[d['release_id']], encoding='utf-8')
    # item 6: the release list lives on downloads/index.html (rendered below, after the file inventory); the old releases page redirects there

    # ---------- changes ("what changed in <release>") — catalog determinations, catalog studies, registry studies by release_added ----------
    def by_field(df_):
        return [dict(field=f, label=LABELS.get(f, f), n=int(n), n_samples=int(ns), n_studies=int(nst)) for f, n, ns, nst in
                sorted(((f, len(g), g.sample_key.nunique(), g.study_accession.nunique()) for f, g in df_.groupby('field_name')), key=lambda x: (-x[1], x[0]))]

    def changes_for(rid):
        added = cd[cd[RA].astype(str) == rid]
        st_added = cs[cs[RA].astype(str) == rid] if RA in cs.columns else cs.iloc[0:0]
        rg_added = rg[rg[RA].astype(str) == rid]
        per_study = []
        if len(added):
            agg = added.groupby('study_accession').agg(n_rows=('sample_key', 'size'), n_samples=('sample_key', 'nunique'), fields=('field_name', lambda x: ', '.join(sorted(set(x)))), routes=('route', lambda x: ', '.join(sorted(set(x)))))
            agg = agg.sort_values(['n_rows'], ascending=False, kind='mergesort')
            for acc, r in agg.head(25).iterrows():
                per_study.append(dict(acc=acc, title=(title_by_acc.get(acc) or '')[:100], n_rows=int(r.n_rows), n_samples=int(r.n_samples), n_samples_study=int(n_by_study.get(acc, 0)), fields=r.fields, routes=r.routes))
        return dict(release_id=rid, n_added=len(added), n_samples_added=int(added.sample_key.nunique()), n_studies_changed=int(added.study_accession.nunique()), fields_added=by_field(added),
                    routes_added=counts_sorted(added.route) if len(added) else [], per_study=per_study, n_studies_added=len(st_added), n_registry_added=len(rg_added),
                    studies_added=[dict(acc=x.study_accession, title=(x.study_title or '')[:100], n=int(x.n_samples_curated or 0)) for x in st_added.sort_values('n_samples_curated', ascending=False).head(25).itertuples(index=False)],
                    nothing=(len(added) == 0 and len(st_added) == 0 and len(rg_added) == 0))
    changes = []
    for d in releases_desc:
        c = changes_for(d['release_id'])
        c.update(release_date=d['release_date'], package_version=d['package_version'], current=d['current'], href=d['changes_href'])
        changes.append(c)
        render('changes_release.html', rspec['site_pages']['changes_release'].format(release_id=d['release_id']), '../', nav='downloads', c=c, rel=d,
               crumbs=[dict(label='Home', href='../index.html'), dict(label='Downloads & releases', href='../downloads/index.html#releases'), dict(label='Changes', href='index.html'), dict(label=d['release_id'])])
    render('changes_index.html', rspec['site_pages']['changes_index'], '../', nav='downloads', changes=changes,
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Downloads & releases', href='../downloads/index.html#releases'), dict(label='Changes')])
    print(f'[{time.time()-t0:.0f}s] releases + {len(changes)} changes pages', file=sys.stderr)

    # ---------- contribute ----------
    n_missing_by_field = {f: int(sum(1 for r in wl_rows if f in r['missing'])) for f in CONTRIB_FIELDS}
    cs_ = dict(n_open=len(wl_rows), n_samples=stats['n_contribute_samples'], threshold=thr, min_samples=CONTRIB_MIN_SAMPLES, min_missing=CONTRIB_MIN_MISSING, fields=[dict(name=f, label=LABELS.get(f, f), n_missing=n_missing_by_field[f]) for f in CONTRIB_FIELDS],
               types=[dict(code=c, label=TLABEL.get(c, c), n=int(sum(1 for r in wl_rows if r['ctype'] == c))) for c in cspec['contribution_types'] if any(r['ctype'] == c for r in wl_rows)],
               repo=cspec['issue_form']['repo'], issues_list_url=f"https://github.com/{cspec['issue_form']['repo']}/issues?q=is%3Aissue+label%3A{cspec['issue_form']['label']}")
    cs_['core_fields'] = CORE_FIELDS
    cs_['repo'] = cspec['issue_form']['repo']
    render('contribute.html', site['contribute_page'], '../', nav='contribute', rows=wl_rows, cs=cs_, stats=stats, field_labels={'age_category': 'age', 'health_condition': 'health condition', 'subject_id': 'subject', 'country': 'country'},
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Contribute')])
    (out / 'data' / 'contribute_worklist.json').write_text(dumps([dict(rank=r['rank'], acc=r['acc'], title=r['title'], n=r['n'], missing=r['missing'], coverage=r['coverage'], type=r['ctype'], papers=r['papers'], issue_url=r['issue_url']) for r in wl_rows]), encoding='utf-8')
    assert (out / site['contribute_page']).stat().st_size < 3_000_000, 'contribute/index.html over the 3 MB budget'

    # ---------- registry tier pages ----------
    def facet(df_, col, labels=None, explode=False):
        d = df_[[col, 'n_runs']].copy()
        d[col] = d[col].map(split_list) if explode else d[col].map(lambda v: [] if isnull(v) or v == '' else [str(v)])
        d = d.explode(col).dropna(subset=[col])
        g = d.groupby(col).agg(n_studies=('n_runs', 'size'), n_runs=('n_runs', lambda x: int(x.fillna(0).sum())))
        rows_ = sorted(((str(k), int(r.n_studies), int(r.n_runs)) for k, r in g.iterrows()), key=lambda x: (-x[1], x[0]))
        return dict(column=col, rows=rows_, labels=labels or {})
    stage_labels = {'deterministic_prior': 'prior carried over from an earlier triage (reason code / body-site call) or deterministic term match',
                    'deterministic_rule': 'ENA-field rule (host taxon 9606, library fields, numeric age with unit)', 'sonnet_x2': 'two replicate model classifications in agreement',
                    'opus_adjudicated': 'replicate disagreement adjudicated by the stronger model', 'pending': 'not yet classified', 'curator_audit': 'study-level host/assay audit of the archive records (config/registry_overrides.yaml)', 'owner_decision': 'study-level decision by the catalog owner (config/registry_overrides.yaml)'}
    host_counts = {k: int((rg.host_human == k).sum()) for k in sspec['host_human_values']}
    n_pending = int((rg.classification_stage == 'pending').sum())
    _sp = rg['n_runs_sandpiper'].fillna(0) if 'n_runs_sandpiper' in rg.columns else pd.Series(0, index=rg.index)

    def _sample_tier(frame):
        if 'n_biosamples_harvested' not in frame.columns:
            return dict(n_harvested=0, n_studies_harvested=0, n_with_site=0, n_with_age=0, n_with_sex=0)
        h = pd.to_numeric(frame['n_biosamples_harvested'], errors='coerce').fillna(0)
        return dict(n_harvested=int(h.sum()), n_studies_harvested=int((h > 0).sum()),
                    n_with_site=int(pd.to_numeric(frame['n_biosamples_with_site'], errors='coerce').fillna(0).sum()),
                    n_with_age=int(pd.to_numeric(frame['n_biosamples_with_age'], errors='coerce').fillna(0).sum()),
                    n_with_sex=int(pd.to_numeric(frame['n_biosamples_with_sex'], errors='coerce').fillna(0).sum()))
    rstats = dict(n_studies=len(rg), n_runs=int(rg.n_runs.fillna(0).sum()), n_biosamples=int(rg.n_biosamples.fillna(0).sum()), host=host_counts,
                  n_human=int(rg.host_human.isin(['yes', 'mixed']).sum()), n_runs_human=int(rg.loc[rg.host_human.isin(['yes', 'mixed']), 'n_runs'].fillna(0).sum()),
                  n_runs_sandpiper=int(_sp.sum()), n_studies_sandpiper=int((_sp > 0).sum()), has_biosamples=(pkg / 'registry_biosamples.parquet').exists(), **_sample_tier(rg),
                  n_pending=n_pending, n_classified=len(rg) - n_pending, pct_classified=int(round(100 * (len(rg) - n_pending) / max(1, len(rg)))),
                  n_catalog=len(cs), n_scopes=len(sspec['scopes']))
    scope_rows, scope_pages = [], []
    for sc in sspec['scopes']:
        m_ = rg[rg.scope_memberships.map(lambda v: sc['id'] in split_list(v))]
        _sr = sspec.get('scope_rules', {}).get(sc['id'], {})
        d = dict(id=sc['id'], label=sc['label'], definition=sc['definition'], rule=sc.get('rule') or _sr.get('rule') or '', curated=bool(sc.get('curated')),
                 n_studies=len(m_), n_runs=int(m_.n_runs.fillna(0).sum()), n_biosamples=int(m_.n_biosamples.fillna(0).sum()), n_runs_sandpiper=int(m_['n_runs_sandpiper'].fillna(0).sum()) if 'n_runs_sandpiper' in m_.columns else 0, **_sample_tier(m_),
                 n_pending=int((m_.classification_stage == 'pending').sum()), n_in_catalog=int(m_.study_accession.isin(inc_set).sum()))
        d['n_classified'] = d['n_studies'] - d['n_pending']
        scope_rows.append(d)
        scope_pages.append((d, m_))
    facets = [facet(rg, 'body_site_primary', vocabs['body_site']), facet(rg, 'life_stage_primary', vocabs['life_stage']), facet(rg, 'assay', vocabs['assay']),
              facet(rg, 'scope_memberships', {sc['id']: sc['label'] for sc in sspec['scopes']}, explode=True), facet(rg, 'classification_stage', stage_labels),
              facet(rg, 'host_human'), facet(rg, 'access')]
    for f_, lab in zip(facets, ('Primary body site', 'Primary life stage', 'Assay', 'Scope membership (a study counts once per scope)', 'Classification stage', 'Host human', 'Access')):
        f_['label'] = lab
    assert sum(n for _, n, _ in facets[0]['rows']) == int(rg.body_site_primary.notna().sum()), 'F13: body-site facet must sum to the rows with a primary site'
    assert sum(n for _, n, _ in facets[4]['rows']) == len(rg), 'F13: classification-stage facet must sum to the registry rows'
    rvocab = dict(body_site=list(vocabs['body_site'].items()), life_stage=list(vocabs['life_stage'].items()), assay=list(vocabs['assay'].items()),
                  access=list(sspec['access_values']), host_human=list(sspec['host_human_values']), classification_stage=list(sspec['classification_stages']))
    render('registry.html', site['registry_page'], '../', nav='registry', rs=rstats, scopes=scope_rows, facets=facets, vocab=rvocab,
           parquet_size=human(reg_path.stat().st_size), n_cols=int(rg.shape[1]), audit_name=sspec['files']['audit'],
           package_version=version, runs_asset_url=f"https://github.com/{site['data_repo']}/releases/download/data-v{version}/registry_runs_v{version}.parquet",
           scope_labels={sc['id']: sc['label'] for sc in sspec['scopes']}, site_labels=vocabs['body_site'], stage_labels=stage_labels, included_accs=included, assay_labels=dict(vocabs['assay']), study_rule=str(pack.get('study_rule', '')),
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Registry')])
    TOP_COLS = ['study_accession', 'study_title', 'n_samples', 'n_runs', 'body_sites', 'life_stages', 'assay', 'classification_stage']
    for d, m_ in scope_pages:
        top = m_.sort_values(['n_samples', 'study_accession'], ascending=[False, True], kind='mergesort', na_position='last').head(25)
        top_rows = [dict(clean(r), has_page=r['study_accession'] in inc_set) for r in top[TOP_COLS].to_dict('records')]
        sf = [facet(m_, 'body_site_primary'), facet(m_, 'life_stage_primary'), facet(m_, 'assay'), facet(m_, 'classification_stage')]
        for f_, lab in zip(sf, ('Primary body site', 'Primary life stage', 'Assay', 'Classification stage')):
            f_['label'] = lab
        render('registry_scope.html', f"registry/scopes/{d['id']}.html", '../../', nav='registry', s=d, top=top_rows, facets=sf,
               crumbs=[dict(label='Home', href='../../index.html'), dict(label='Registry', href='../index.html'), dict(label=d['label'])])
    assert (out / site['registry_page']).stat().st_size < 2_000_000, 'registry/index.html over the 2 MB budget'
    _html = (out / site['registry_page']).read_text(encoding='utf-8')
    assert "data/registry_studies.parquet" in _html and 'USERNAME' not in _html and 'REPOSITORY' not in _html, 'registry page must reference the registry parquet and carry no placeholder'
    _vd = repo_root / sspec['vocab_dir']
    _bs = yaml.safe_load((_vd / 'body_sites.yaml').read_text(encoding='utf-8'))['codes']
    _ls = yaml.safe_load((_vd / 'life_stages.yaml').read_text(encoding='utf-8'))['codes']
    reg_methods = dict(stats=rstats, stage_labels=stage_labels, assay=vocabs['assay'], scopes=scope_rows, facets=facets,
                       n_uberon=sum(len(d_.get('uberon') or []) for d_ in _bs.values()),
                       uberon_rows=[(c, d_['label'], ', '.join(f"{u['id']} ({u['label']})" for u in (d_.get('uberon') or [])), ', '.join(map(str, d_.get('terms') or []))) for c, d_ in _bs.items()],
                       life_rows=[(c, d_['label'], (f"{d_['days'][0]}–{d_['days'][1] - 1} d" if d_.get('days') and d_['days'][1] else (f"≥ {d_['days'][0]} d" if d_.get('days') else '—')), ', '.join(map(str, d_.get('terms') or []))) for c, d_ in _ls.items()])
    print(f'[{time.time()-t0:.0f}s] registry: {len(rg)} studies, {len(scope_rows)} scope pages', file=sys.stderr)

    # ---------- downloads + manifest ----------
    def dirstat(sub, pattern='*'):
        fs = [f for f in (out / sub).glob(pattern) if f.is_file()]
        return dict(n=len(fs), size=human(sum(f.stat().st_size for f in fs)), bytes=sum(f.stat().st_size for f in fs))
    sitedata = [
        dict(path='data/gut_sample_metadata_wide.parquet', desc='Sample table loaded by the explorer (one row per catalog sample)', **dirstat('data', 'gut_sample_metadata_wide.parquet')),
        dict(path='data/gut_studies.parquet', desc='Study table loaded by the explorer', **dirstat('data', 'gut_studies.parquet')),
        dict(path='data/gut_sample_determinations.parquet', desc='Evidence rows (sample × field with verbatim quote)', **dirstat('data', 'gut_sample_determinations.parquet')),
        dict(path='data/registry_studies.parquet', desc='Registry tier, loaded by the registry explorer', **dirstat('data', 'registry_studies.parquet')),
        dict(path='data/studies/<PRJ>.csv.gz|.parquet', desc='Per-study sample slices (gzip CSV + parquet)', **dirstat('data/studies', '*[0-9].csv.gz')),
        dict(path='data/studies/<PRJ>_determinations.csv.gz', desc='Per-study evidence rows (gzip CSV, mtime 0)', **dirstat('data/studies', '*_determinations.csv.gz')),
        dict(path='data/cohorts/<COH>.csv.gz|.parquet', desc='Per-cohort sample slices (multi-study cohorts)', **dirstat('data/cohorts')),
        dict(path='data/catalog_cohorts.csv', desc='Cohort list (id, name, studies, papers)', **dirstat('data', 'catalog_cohorts.csv')),
        dict(path='data/studies_index.json', desc='Study index used by the Studies table', **dirstat('data', 'studies_index.json')),
        dict(path='data/contribute_worklist.json', desc='Contribution worklist (one object per open study)', **dirstat('data', 'contribute_worklist.json')),
        dict(path='data/package/', desc='The complete package, file by file, plus the zip', **dirstat('data/package')),
        dict(path='authors/idx/<letter>.json', desc='Author index shards', **dirstat('authors/idx')),
    ]
    offsite = [dict(o, name=o['name'].replace('{v}', version), desc=o['desc'].replace('{v}', version)) for o in OFFSITE]
    groups = [('catalog', 'Catalog tables (human gut, all ages)'), ('registry', 'Registry tables (all human shotgun metagenomes)'), ('release', 'Release documents'), ('infant', 'Infant extension tables (deep infant-field curation of the infant studies; also present in the catalog tables)')]
    render('downloads.html', 'downloads/index.html', '../', nav='downloads', files=pkg_files, groups=groups, zip_name=zip_name, zip_size=zip_size, zip_href=zip_href, sitedata=sitedata, offsite=offsite, vj=vj,
           releases=releases_desc, n_releases=len(releases), first_numbered=rspec['release_id']['first_numbered'],
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Downloads & releases')])
    manifest = dict(site=site['title'], package_version=version, release_tag=vj['release_tag'], release_id=release_id, build_date=build_date, generator_git_sha=gen_sha, base_url=base_url,
                    files=[dict(path=str(p.relative_to(out)), bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted((out / 'data').rglob('*')) if p.is_file()],
                    tables={k: dict(rows=v.get('rows'), sha256=v.get('sha256')) for k, v in sorted(vj['tables'].items())})
    (out / 'data' / 'manifest.json').write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding='utf-8')
    (out / 'data' / 'VERSION.json').write_text(json.dumps(vj, indent=1, sort_keys=True), encoding='utf-8')

    # ---------- methods ----------
    depth_counts = counts_sorted(cs.curated_depth.fillna('none'))
    route_field = {f: {r: int(route_by_field.get((f, r), 0)) for r in ROUTES} for f in CORE_FIELDS + KEY_FIELDS if has_col.get(f)}
    tier_cov = []
    if 'curated_source' in cw.columns:
        for f in ('age_at_collection_days', 'subject_id', 'timepoint_label', 'sex', 'health_condition', 'antibiotic_exposure'):
            if f in cw.columns:
                tier_cov.append(dict(field=f, **{src: round(100 * float(cw.loc[cw.curated_source == src, f].notna().mean()), 1) for src in ('infant_catalog', 'gut_all_v1')}))
    src_avail = []
    for k in SRC_ORDER:
        c_ = f'src_{k}'
        if c_ in cs.columns:
            vc = cs[c_].fillna('none').value_counts()
            src_avail.append(dict(label=SRC_LABELS[k], used=int(vc.get('used', 0)), checked=int(vc.get('checked', 0)), none=int(vc.get('none', 0))))
    render('methods.html', 'about/methods.html', '../', nav='about', tier_cov=tier_cov, src_avail=src_avail, reg=reg_methods, stats=stats, depth_counts=depth_counts, route_field=route_field, docs=doc_list, pack=pack,
           crumbs=[dict(label='Home', href='../index.html'), dict(label='About', href='index.html'), dict(label='Methods')])

    # ---------- sources and acknowledgements (config/sources.yaml) ----------
    src = yaml.safe_load(Path(a.sources_config).read_text(encoding='utf-8'))
    for k in ('funding', 'data_sources', 'software', 'related_efforts'):
        assert k in src, f'config/sources.yaml lacks {k}'
    _ing = {'used', 'candidate_high', 'candidate_medium', 'candidate_low', 'not_applicable'}
    assert all(r.get('ingestion') in _ing for r in src['related_efforts']), 'sources.yaml: ingestion outside the vocabulary'
    order = ['used', 'candidate_high', 'candidate_medium', 'candidate_low', 'not_applicable']
    src['related_efforts'] = sorted(src['related_efforts'], key=lambda r: (order.index(r['ingestion']), r['name'].lower()))
    # External resources page (R2026.20): one section per incorporated resource with what it is, what we took, and the projects using it
    _ce = cd[['study_accession', 'sample_key', 'evidence_source']].copy() if 'evidence_source' in cd.columns else pd.DataFrame(columns=['study_accession', 'sample_key', 'evidence_source'])
    _ce = _ce[_ce.evidence_source.fillna('').str.startswith('external.')]
    _ce['res'] = _ce.evidence_source.str.replace(r'^external\.', '', regex=True).str.split(r'[.:\[]').str[0]
    ext_stats = {}
    for rid, gg in _ce.groupby('res'):
        ext_stats[rid] = dict(n_values=int(len(gg)), n_samples=int(gg.sample_key.nunique()), studies=sorted(set(gg.study_accession) & inc_set))
    ext_list = [dict(r, stats=ext_stats.get(r['resource_id'], dict(n_values=0, n_samples=0, studies=[]))) for r in src['related_efforts'] if r.get('ingestion') == 'used']
    render('external.html', 'about/external.html', '../', nav='about', ext=ext_list, others=[r for r in src['related_efforts'] if r.get('ingestion') != 'used'],
           crumbs=[dict(label='Home', href='../index.html'), dict(label='About', href='index.html'), dict(label='External resources')])
    render('sources.html', 'about/sources.html', '../', nav='about', src=src, crumbs=[dict(label='Home', href='../index.html'), dict(label='About', href='index.html'), dict(label='Sources & acknowledgements')])
    render('about.html', 'about/index.html', '../', nav='about', stats=stats, rs=rstats, funnel=funnel, docs=doc_list, src=src, n_fields=len(fields),
           crumbs=[dict(label='Home', href='../index.html'), dict(label='About')])

    # ---------- home, search index, sitemap ----------
    # item 1: one clean horizontal bar list per facet (top 8), counts straight from the wide table, links into the explorer with the filter applied
    def bar_rows(series_, labels, n=8):
        vc = series_.dropna()
        g_ = cw.loc[vc.index].groupby(vc).agg(ns=('sample_key', 'size'), nst=('study_accession', 'nunique')).sort_values('ns', ascending=False, kind='mergesort').head(n)
        top = int(g_.ns.max()) if len(g_) else 1
        return [dict(code=str(k), label=labels.get(str(k), ''), n=int(v.ns), n_studies=int(v.nst), pct=round(100 * int(v.ns) / top, 1)) for k, v in g_.iterrows()]
    country_labels = {}
    try:   # optional: ISO-3166 names for the country codes (pycountry); codes alone when unavailable
        import pycountry
        for code in cw.country.dropna().unique():
            c_ = pycountry.countries.get(alpha_2=str(code))
            if c_ is not None:
                country_labels[str(code)] = c_.name
    except ImportError:
        pass
    home = dict(hc_bars=bar_rows(cw.health_condition, hc_labels), country_bars=bar_rows(cw.country, country_labels), top_studies=studies[:12], scopes=other_scopes)
    assert sum(r['n'] for r in home['hc_bars']) <= int(cw.health_condition.notna().sum()) and all(r['n'] > 0 for r in home['hc_bars'] + home['country_bars'])
    sidx = [dict(t='study', id=s['study_accession'], n=s['study_title'] or '', u=f"studies/{s['study_accession']}.html",
                 k=(f"{s['study_accession']} {s['secondary_study_accession'] or ''} {s['study_title'] or ''} {s['cohort_name'] or ''} {s['first_author'] or ''} "
                    f"{_search_terms(s, iv_voc, hc_labels)}").lower()) for s in studies]
    sidx += [dict(t='cohort', id=c['cohort_id'], n=c['cohort_name'], u=f"cohorts/{c['cohort_id']}.html", k=f"{c['cohort_id']} {c['cohort_name']} {' '.join(c['studies'])}".lower()) for c in cohorts]
    sidx += [dict(t='collection', id=c['id'], n=c['name'], u=c['u'], k=c['k']) for c in collections]
    sidx += [dict(t='scope', id=d['id'], n=d['label'], u=f"registry/scopes/{d['id']}.html", k=f"{d['id']} {d['label']} registry scope".lower()) for d in scope_rows]
    (out / 'search_index.json').write_text(dumps(sidx), encoding='utf-8')
    # R2026.20: registry projects outside the catalog (human or unknown host) are searchable too, with the main reason they are out
    def _why(r):
        if r.host_human not in ('yes', 'mixed'):
            return 'human host not established' if r.host_human != 'no' else 'host not human'
        if r.assay not in ('shotgun_dna', 'mixed'):
            return f'not a shotgun metagenome ({r.assay})'
        if 'gut_stool' not in str(r.body_sites or '').split(';'):
            return 'no gut samples'
        return {'owner_decision': 'owner decision', 'curator_audit': 'curator audit', 'pending': 'classification pending'}.get(r.classification_stage, 'not in this release')
    _rs = rg[~rg.study_accession.isin(inc_set) & (rg.host_human != 'no')]
    reg_idx = [[r.study_accession, r.secondary_study_accession if isinstance(r.secondary_study_accession, str) else '', str(r.study_title or '')[:110], _why(r)] for r in _rs.itertuples()]
    (out / 'registry_search.json').write_text(dumps(reg_idx), encoding='utf-8')
    render('index.html', 'index.html', '', nav='home', stats=stats, home=home, readme_version_warning=readme_version_warning, n_releases=len(releases), shard_letters=sorted(shards))
    # item 5: Collections and Atlas pages are produced by other tracks; the nav entries must never be dead links
    for sec, lab, txt in (('collections', 'Collections', 'No collections are configured in this build (config/collections.yaml).'),
                          ('atlas', 'Atlas', 'The taxon atlas is being prepared. ' + ('The interactive <a href="pca.html">PCA of Sandpiper community profiles</a> is available.' if (out / 'atlas' / 'pca.html').exists() else ''))):
        if not (out / sec / 'index.html').exists():
            (out / sec).mkdir(parents=True, exist_ok=True)
            render('placeholder.html', f'{sec}/index.html', '../', nav=sec, heading=lab, text=txt, crumbs=[dict(label='Home', href='../index.html'), dict(label=lab)])
    # R2026.15: every drop-down entry must resolve — sub-pages a build does not produce (e.g. atlas/pca.html without Sandpiper PCA
    # tables) get a short placeholder instead of a dead menu link
    for _k, _lab, _href, _children in NAV:
        for _ck, _cl, _ch in (_children or []):
            if _ch and not (out / _ch).exists():
                (out / _ch).parent.mkdir(parents=True, exist_ok=True)
                _root = '../' * _ch.count('/')
                render('placeholder.html', _ch, _root, nav=_ck, heading=_cl, text='This page is not part of this build.', crumbs=[dict(label='Home', href=_root + 'index.html'), dict(label=_cl)])
    # items 5/6: old URLs keep working as redirects to their new homes
    for old_, new_ in REDIRECTS.items():
        (out / old_).parent.mkdir(parents=True, exist_ok=True)
        (out / old_).write_text(f'<!DOCTYPE html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={new_}"><link rel="canonical" href="{base_url}{new_.replace("../", "")}"><title>moved</title><p>This page moved to <a href="{new_}">{new_}</a>.</p>', encoding='utf-8')
    urls = ''.join(f'<url><loc>{base_url}{p}</loc></url>' for p in sorted(written))
    (out / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + urls + '</urlset>', encoding='utf-8')
    (out / 'robots.txt').write_text(f'User-agent: *\nAllow: /\nSitemap: {base_url}sitemap.xml\n', encoding='utf-8')
    for name in NEVER_PUBLISH_DOCS:
        assert not (out / 'docs' / name).exists() and not (out / 'docs' / (name[:-3] + '.html')).exists(), f'F7: {name} must not be published'
    leak_pages = []
    for p in sorted(out.rglob('*.html')):
        t = p.read_text(encoding='utf-8')
        if re.search(r'\b(?:frame|session)\s+[0-9a-f]{8}\b', t) or re.search(r'Organisations: [^<]*(?:SUB\d{6,}|@)', t):
            leak_pages.append(str(p.relative_to(out)))
    assert not leak_pages, f'F7/F1 leak check failed on {leak_pages[:5]}'
    total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    largest = max((p for p in out.rglob('*') if p.is_file()), key=lambda p: (p.stat().st_size, str(p)))
    print(json.dumps(dict(pages=len(written), total_bytes=total, total_human=human(total), largest=str(largest.relative_to(out)),
                          largest_bytes=largest.stat().st_size, seconds=round(time.time() - t0, 1), version=version, build_date=build_date,
                          stats={k: v for k, v in stats.items() if not isinstance(v, (list, dict))}), sort_keys=True), file=sys.stderr)


if __name__ == '__main__':
    main()
