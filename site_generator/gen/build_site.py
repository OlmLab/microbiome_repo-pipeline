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
import posixpath
import argparse, gzip, hashlib, io, json, math, os, re, shutil, sys, time
from collections import Counter
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
    'catalog_runs.parquet': 'One row per sequencing run of a catalog sample: library strategy / source / layout, platform, reads and bases, nucleic_acid (dna / rna) and the measured rRNA read share where a read check ran.',
    'catalog_exposure_items.parquet': 'Routine exposure items read from the papers (concept, scale person / group / cohort / place, summary and a verbatim quote).',
    'catalog_study_papers.parquet': 'Papers judged by the link audit to have generated (own_data) or reused (reuse) a project\'s data.',
    'catalog_relatedness.parquet': 'One row per family or shared-setting unit with two or more sampled people.',
    'catalog_relations.parquet': 'Explicit relations between sampled people (mother–child, twins, partners, donor–recipient) with evidence.',
    'catalog_sample_determinations_conflicts.parquet': 'Values that lost a precedence decision against the published value (kept for audit).',
    'catalog_metadata_contradictions.parquet': 'Samples whose sources disagree; neither value is published.',
    'catalog_pass_coverage.csv': 'Per project: which curation passes ran and which were not applicable.',
    'catalog_sandpiper_sample_summary.parquet': 'Per profiled sample: Sandpiper QC flags, richness and the metadata used by the Atlas.',
    'catalog_sandpiper_pca_scores.parquet': 'CLR-PCA scores of the Sandpiper genus profiles (Atlas › PCA).',
    'catalog_sandpiper_pca_loadings.parquet': 'Genus loadings of the CLR-PCA.', 'catalog_sandpiper_pca_variance.csv': 'Variance explained per principal component.',
    'catalog_sandpiper_study_coverage.csv': 'Per project: samples with a Sandpiper profile.',
    'CATALOG_SANDPIPER_REPORT.md': 'How the Sandpiper profiles of the catalog samples were assembled and filtered.',
    'catalog_studies.parquet': 'Start here (projects). One row per catalog project: body sites, nucleic acid (DNA / RNA) of its data sets, registry classification, linked papers, methods and setting read from the paper, per-field coverage (cov_*), age-category and health-condition distributions, curated depth and source.',
    'catalog_sample_metadata_wide.parquet': 'Start here (samples). One row per sample: identifiers, body-site code/class, DNA / RNA data sets (has_dna, has_rna), age_category (+ basis), every field with __route and __confidence, infant extension fields, infant_scope flag.',
    'catalog_sample_determinations.parquet': 'Long form: one row per sample × field with route, scope, confidence, evidence_source, evidence_locator and a verbatim evidence quote (≤ 12 words).',
    'registry_studies.parquet': 'Registry tier: one row per screened ENA study with a human-associated metagenome signal — host, body sites (UBERON-anchored codes), life stages, assay, access, classification stage, scope memberships, harvested-sample roll-ups.',
    'registry_biosamples.parquet': 'Registry sample tier: one row per harvested BioSample with normalised body site, life stage / age, sex, country.',
    'registry_study_papers.parquet': 'Study ↔ paper links of the registry studies (PMID/PMCID/DOI, relation own_data/unsure, method, confidence).',
    'registry_authors.parquet': 'Study × author × paper rows for the registry studies (Europe PMC author lists).',
    'registry_bioproject_records.parquet': 'NCBI BioProject records (title, description, umbrella, organisation) for the registry studies.',
    'registry_universe_audit.csv': 'Registry enumeration audit: per ENA slice, archive count vs rows pulled and completeness.',
    'sample_metadata_wide.parquet': 'Infant extension: the deep infant-gut curation (delivery mode, feeding, gestational age, birth weight …) for the infant studies; these rows are also in catalog_sample_metadata_wide (curated_source = infant_catalog).',
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
    'sandpiper_top_genera.parquet': 'Top-15 genera per profiled infant-study sample.', 'sandpiper_study_panels.parquet': 'Per-study mean top phyla / genera (infant studies).', 'catalog_sandpiper_study_panels.parquet': 'Per-project mean top-12 genera over profiled samples (project-page composition bar).',
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
           dict(name='catalog_sandpiper_sample_genus_v{v}_part*.parquet', desc='Genus-level Sandpiper relative abundances for every profiled catalog sample (long table: sample_key, genus, relabund, coverage; ≈ 185 MB in < 90 MB parts, concatenate the parts) — Release asset.'),
           dict(name='catalog_sandpiper_sample_species_v{v}_part*.parquet', desc='Species-level Sandpiper relative abundances (relabund ≥ 0.001; ≈ 200 MB in parts) — Release asset.')]
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
                                     ('atlas', 'Atlas: PCA of community profiles', 'atlas/pca.html'), ('atlas', 'Figures (resource paper)', 'atlas/figures.html')]),
       ('about', 'About', None, [('about', 'About this resource', 'about/index.html'), ('llms', 'For LLMs & API', 'llms/index.html'),
                                 ('downloads', 'Downloads & releases', 'downloads/index.html'), ('about', 'Scope', 'about/scope.html'),
                                 ('about', 'Methods', 'about/methods.html'), ('about', 'Fields & vocabularies', 'fields/index.html'),
                                 ('about', 'Where values come from', 'about/value_sources.html'), ('about', 'External resources', 'about/external.html'), ('about', 'Sources & acknowledgements', 'about/sources.html'),
                                 ('about', 'Accessibility, privacy & security', 'about/accessibility.html')])]
# Old URLs → new homes (item 5/6): every entry is written as a redirect stub so bookmarks keep working.
REDIRECTS = {'scope.html': 'about/scope.html', 'methods.html': 'about/methods.html', 'sources.html': 'about/sources.html', 'downloads.html': 'downloads/index.html',
             'releases/index.html': '../downloads/index.html#releases', 'gut/index.html': '../samples/index.html', 'human/index.html': '../studies/index.html', 'human/projects/index.html': '../../studies/index.html', 'universe.html': 'about/scope.html', 'atlas/observations.html': 'figures.html'}
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


def html_escape(x):
    return str(x).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


ROLE_COLOR = {'mother': '#CFB87C', 'father': '#228833', 'parent': '#88AA55', 'partner': '#88AA55', 'grandparent': '#997700', 'child': '#4477AA', 'twin': '#AA3377',
              'sibling': '#66CCEE', 'other_relative': '#8C8F91', 'household_member': '#BBBBBB'}
ROLE_ABBR = {'mother': 'M', 'father': 'F', 'parent': 'P', 'partner': 'Pt', 'grandparent': 'G', 'child': 'C', 'twin': 'T', 'sibling': 'S', 'other_relative': 'R', 'household_member': 'H', 'unknown': '?'}


def _days_label(d):
    d = float(d)
    if d >= 730:
        return f"{d / 365.25:.1f} y"
    if d >= 60:
        return f"{d / 30.4375:.0f} mo"
    return f"{d:.0f} d"


def timeline_svg(people, width=640, row_h=11):
    """R2026.23 swimlane: one row per person (list of dicts {label, role, days: [..]}), dots at days since the person's first
    sample; rows of one family are adjacent; dot colour = family role."""
    if not people:
        return ''
    xmax = max(max(p['days']) for p in people) or 1.0
    left, right, top = 92, 12, 6
    h = top + row_h * len(people) + 22
    sx = lambda d: left + (width - left - right) * (float(d) / xmax)
    out = [f'<svg class="tline" viewBox="0 0 {width} {h}" width="100%" role="img" aria-label="Sampling timeline of {len(people)} people">']
    prev_fam = None
    for i, p in enumerate(people):
        y = top + row_h * i + row_h / 2
        if p.get('family') and p['family'] != prev_fam and i:
            out.append(f'<line x1="2" x2="{width - 2}" y1="{y - row_h / 2:.1f}" y2="{y - row_h / 2:.1f}" stroke="#e5e5e5" stroke-width="0.6"/>')
        prev_fam = p.get('family')
        col = ROLE_COLOR.get(p.get('role') or '', '#565A5C')
        out.append(f'<text x="{left - 6}" y="{y + 3:.1f}" text-anchor="end" font-size="8" fill="#565A5C">{html_escape(str(p["label"])[:16])}</text>')
        ds = sorted(p['days'])
        out.append(f'<line x1="{sx(ds[0]):.1f}" x2="{sx(ds[-1]):.1f}" y1="{y:.1f}" y2="{y:.1f}" stroke="{col}" stroke-opacity="0.45" stroke-width="1.5"/>')
        for d in ds:
            out.append(f'<circle cx="{sx(d):.1f}" cy="{y:.1f}" r="2.6" fill="{col}"><title>{html_escape(str(p["label"]))}{(" (" + str(p["role"]) + ")") if isinstance(p.get("role"), str) and p.get("role") else ""}: day {float(d):.0f}</title></circle>')
    yb = top + row_h * len(people) + 4
    for k in range(5):
        d = xmax * k / 4
        out.append(f'<text x="{sx(d):.1f}" y="{yb + 10}" text-anchor="middle" font-size="8" fill="#565A5C">{_days_label(d)}</text>')
    out.append('</svg>')
    return ''.join(out)


def motif_svg(motif, width=None):
    """Family structure glyph: parents on top, children below, lines parent -> child (partners joined)."""
    parts = [x.strip() for x in str(motif).split('+') if x.strip()]
    roles = []
    for p_ in parts:
        r_, _, n_ = p_.partition('×')
        roles += [r_] * (int(n_) if n_.isdigit() else 1)
    top = [r for r in roles if r in ('mother', 'father', 'parent', 'partner', 'grandparent')]
    bot = [r for r in roles if r not in top]
    n = max(len(top), len(bot), 1)
    W = width or (24 * n + 8)
    pos = lambda k, m, y: (W / 2 + (k - (m - 1) / 2) * 22, y)
    out = [f'<svg class="motif" viewBox="0 0 {W} 44" width="{W}" height="44" role="img" aria-label="{html_escape(motif)}">']
    tp = [pos(k, len(top), 11) for k in range(len(top))]
    bp = [pos(k, len(bot), 33) for k in range(len(bot))]
    for (x1, y1) in tp:
        for (x2, y2) in bp:
            out.append(f'<line x1="{x1:.1f}" y1="{y1 + 6}" x2="{x2:.1f}" y2="{y2 - 6}" stroke="#999" stroke-width="0.8"/>')
    if len(tp) >= 2:
        out.append(f'<line x1="{tp[0][0]:.1f}" y1="11" x2="{tp[-1][0]:.1f}" y2="11" stroke="#999" stroke-width="0.8"/>')
    if not tp and len(bp) >= 2:
        out.append(f'<line x1="{bp[0][0]:.1f}" y1="33" x2="{bp[-1][0]:.1f}" y2="33" stroke="#999" stroke-width="0.8" stroke-dasharray="2 2"/>')
    for r, (x, y) in list(zip(top, tp)) + list(zip(bot, bp)):
        out.append(f'<circle cx="{x:.1f}" cy="{y}" r="7" fill="{ROLE_COLOR.get(r, "#8C8F91")}"><title>{r}</title></circle><text x="{x:.1f}" y="{y + 3}" font-size="8" text-anchor="middle" fill="#fff">{ROLE_ABBR.get(r, "?")}</text>')
    out.append('</svg>')
    return ''.join(out)


# ---- R2026.25 relationship networks: every family, shared setting and explicit pairwise relation drawn as a small graph
REL_EDGE = {'donor_of': ('donor', 'recipient', True, '#A88B4A', ''), 'mother_of': ('mother', 'child', True, '#999999', ''), 'father_of': ('father', 'child', True, '#999999', ''),
            'parent_of': ('parent', 'child', True, '#999999', ''), 'twin_of': ('twin', 'twin', False, '#AA3377', ''), 'twin_mz_of': ('twin', 'twin', False, '#AA3377', 'MZ'),
            'twin_dz_of': ('twin', 'twin', False, '#AA3377', 'DZ'), 'sibling_of': ('sibling', 'sibling', False, '#66CCEE', ''), 'partner_of': ('partner', 'partner', False, '#88AA55', ''),
            'roommate_of': ('household_member', 'household_member', False, '#BBBBBB', ''), 'household_member_of': ('household_member', 'household_member', False, '#BBBBBB', '')}
NET_ROLE_COLOR = dict(ROLE_COLOR, donor='#A88B4A', recipient='#6C8EBF', member='#B9B2A0')
NET_ROLE_ABBR = dict(ROLE_ABBR, donor='D', recipient='R', member='')
_PARENT_ROLES = ('mother', 'father', 'parent', 'grandparent', 'donor')


def relnet_svg(cl, uid):
    """One cluster as an SVG graph. cl = dict(nodes=[{id, label, role, n}], edges=[(a, b, kind)], hub=None | {label, more}).
    Layout: directed edges (donor -> recipient, parent -> child) run top -> bottom; undirected links (twins, partners,
    siblings, roommates) join nodes in a row; a hub (shared setting, or a family without known roles) sits on top with spokes."""
    nodes = {n['id']: n for n in cl['nodes']}
    edges = [e for e in cl['edges'] if e[0] in nodes and e[1] in nodes]
    lvl = {k: 0 for k in nodes}
    directed = [(a, b) for a, b, k in edges if REL_EDGE.get(k, ('', '', False))[2]]
    for _ in range(3):
        for a, b in directed:
            lvl[b] = max(lvl[b], min(2, lvl[a] + 1))
    if not directed and not cl.get('hub'):
        for k, n in nodes.items():
            lvl[k] = 0 if n['role'] in _PARENT_ROLES or n['role'] == 'partner' and any(m['role'] == 'child' for m in nodes.values()) else 1
        if len(set(lvl.values())) == 1:
            lvl = {k: 0 for k in nodes}
    if cl.get('hub'):
        lvl = {k: 1 + i // 10 for i, k in enumerate(nodes)}
    rows0 = {}
    for k in nodes:
        rows0.setdefault(lvl[k], []).append(k)
    rows = {}
    for l_ in sorted(rows0):   # wrap long rows (a donor with 40 recipients) at 12 nodes
        r_ = sorted(rows0[l_], key=lambda k: (nodes[k]['role'] not in _PARENT_ROLES, str(nodes[k]['label'])))
        for j in range(0, len(r_), 12):
            rows[l_ * 100 + j // 12] = r_[j:j + 12]
    ncol = max(len(v) for v in rows.values()) + (1 if cl.get('hub', {}) and cl['hub'].get('more') else 0)
    dx, dy = 32, 36
    W = max(64, ncol * dx + 16)
    levels = sorted(rows)
    top = 14 if cl.get('hub') else (10 if directed else 0)
    H = top + len(levels) * dy + 8
    pos = {}
    for li, l_ in enumerate(levels):
        r_ = rows[l_]
        for i, k in enumerate(r_):
            pos[k] = (W / 2 + (i - (len(r_) - 1) / 2) * dx, top + 12 + li * dy)
    out = [f'<svg class="relnet-g" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" role="img" aria-label="{html_escape(cl.get("title", ""))}">',
           f'<defs><marker id="ra{uid}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L8,4L0,8z" fill="#A88B4A"/></marker>'
           f'<marker id="rg{uid}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0L8,4L0,8z" fill="#999"/></marker></defs>']
    if cl.get('title'):
        out.append(f'<title>{html_escape(cl["title"])}</title>')
    if cl.get('hub'):
        hx, hy = W / 2, 9
        for k in nodes:
            x, y = pos[k]
            out.append(f'<line x1="{hx:.1f}" y1="{hy + 4}" x2="{x:.1f}" y2="{y - 7}" stroke="#cfc8b6" stroke-width="0.8"/>')
        out.append(f'<rect x="{hx - 5:.1f}" y="{hy - 5}" width="10" height="10" rx="2" fill="#8C8F91"><title>{html_escape(cl["hub"]["label"])}</title></rect>')
    fam_lines = cl.get('family_lines')
    if fam_lines:
        par = [k for k in nodes if lvl[k] == 0 and nodes[k]['role'] in _PARENT_ROLES + ('partner',)]
        kid = [k for k in nodes if lvl[k] == 1]
        for a in par:
            for b in kid:
                (x1, y1), (x2, y2) = pos[a], pos[b]
                out.append(f'<line x1="{x1:.1f}" y1="{y1 + 7}" x2="{x2:.1f}" y2="{y2 - 7}" stroke="#aaa" stroke-width="0.8"/>')
        if len(par) >= 2:
            out.append(f'<line x1="{pos[par[0]][0]:.1f}" y1="{pos[par[0]][1]}" x2="{pos[par[-1]][0]:.1f}" y2="{pos[par[-1]][1]}" stroke="#aaa" stroke-width="0.8"/>')
        if not par and len(kid) >= 2:
            out.append(f'<line x1="{pos[kid[0]][0]:.1f}" y1="{pos[kid[0]][1]}" x2="{pos[kid[-1]][0]:.1f}" y2="{pos[kid[-1]][1]}" stroke="#bbb" stroke-width="0.8" stroke-dasharray="2 2"/>')
        if not par and not kid or (len(set(lvl.values())) == 1 and len(nodes) >= 2 and not par):
            pass
    for a, b, k in edges:
        ra, rb, dr, col, lab = REL_EDGE.get(k, ('', '', False, '#999', ''))
        (x1, y1), (x2, y2) = pos[a], pos[b]
        if dr:
            import math as _m
            _dense = len(directed) > 16
            L = _m.hypot(x2 - x1, y2 - y1) or 1
            ux, uy = (x2 - x1) / L, (y2 - y1) / L
            out.append(f'<line x1="{x1 + ux * 8:.1f}" y1="{y1 + uy * 8:.1f}" x2="{x2 - ux * 8:.1f}" y2="{y2 - uy * 8:.1f}" stroke="{col}" stroke-width="{0.7 if _dense else 1.2}"{' stroke-opacity="0.45"' if _dense else ''} marker-end="url(#{"ra" if k == "donor_of" else "rg"}{uid})"><title>{html_escape(nodes[a]["label"])} {k.replace("_of", "").replace("_", " ")} of {html_escape(nodes[b]["label"])}</title></line>')
        else:
            dash = ' stroke-dasharray="3 2"' if k in ('roommate_of', 'household_member_of') else ''
            if abs(y1 - y2) < 1 and abs(x1 - x2) > dx * 1.5:
                mx, my = (x1 + x2) / 2, y1 - 14
                out.append(f'<path d="M{x1:.1f},{y1 - 7} Q{mx:.1f},{my} {x2:.1f},{y2 - 7}" fill="none" stroke="{col}" stroke-width="1.2"{dash}/>')
            else:
                out.append(f'<line x1="{x1:.1f}" y1="{y1}" x2="{x2:.1f}" y2="{y2}" stroke="{col}" stroke-width="1.2"{dash}/>')
            if lab:
                out.append(f'<text x="{(x1 + x2) / 2:.1f}" y="{min(y1, y2) - 9:.1f}" font-size="7" text-anchor="middle" fill="#AA3377">{lab}</text>')
    import os as _os
    _labs = [str(n['label']) for n in nodes.values()]
    _cp = _os.path.commonprefix(_labs) if len(_labs) >= 2 else ''
    _cut = len(_cp) if len(_cp) >= 3 and all(len(l_) > len(_cp) for l_ in _labs) else 0
    _hide = sum(1 for l_ in _labs if len(l_) - _cut > 9) > len(_labs) / 2   # mostly long accessions: tooltips only
    for k, n in nodes.items():
        x, y = pos[k]
        r_ = n['role'] or 'unknown'
        lbl = str(n['label']); short = lbl[_cut:].lstrip('-_. ') or lbl
        lbl_s = '' if _hide else (short if len(short) <= 6 else short[:5] + '…')
        ly = y - 11 if (directed and lvl[k] == 0) else y + 16
        out.append(f'<g><title>{html_escape(lbl)} — {r_.replace("_", " ")}{(" · " + str(n["n"]) + " sample" + ("s" if n["n"] != 1 else "")) if n.get("n") else " · not sequenced"}</title>'
                   f'<circle cx="{x:.1f}" cy="{y}" r="7" fill="{NET_ROLE_COLOR.get(r_, "#8C8F91")}"{"" if n.get("n") else " fill-opacity=\"0.35\""}/>'
                   f'<text x="{x:.1f}" y="{y + 3}" font-size="8" text-anchor="middle" fill="#fff">{NET_ROLE_ABBR.get(r_, "")}</text>'
                   + (f'<text x="{x:.1f}" y="{ly}" font-size="7.5" text-anchor="middle" fill="#666">{html_escape(lbl_s)}</text>' if lbl_s else '') + '</g>')
    if _cut and not _hide:
        out.append(f'<text x="3" y="{H + 7:.0f}" font-size="7" fill="#aaa">{html_escape(_cp.rstrip("-_. ")[:24])}…</text>')
        out[0] = out[0].replace(f'viewBox="0 0 {W:.0f} {H:.0f}"', f'viewBox="0 0 {W:.0f} {H + 10:.0f}"').replace(f'height="{H:.0f}"', f'height="{H + 10:.0f}"')
    if cl.get('hub') and cl['hub'].get('more'):
        r_ = rows[levels[-1]]
        x = W / 2 + (len(r_) - (len(r_) - 1) / 2) * dx
        out.append(f'<text x="{x:.1f}" y="{pos[r_[-1]][1] + 3}" font-size="8" text-anchor="middle" fill="#888">+{cl["hub"]["more"]}</text>')
    out.append('</svg>')
    return ''.join(out)


def relnet_clusters(acc, rel_rows, fam_rows, set_groups, n_by_subject, n_by_person, label_by_person, cap=24):
    """Builds the clusters shown on a project page. Explicit relations (gut_relations) first, as connected components;
    then families (one example per family structure, round-robin, so a 900-family cohort still shows its variety);
    then the largest shared settings. Returns (clusters, totals)."""
    clusters, seen = [], set()
    if rel_rows is not None and len(rel_rows):
        par = {}
        def f(x):
            while par.setdefault(x, x) != x:
                par[x] = par[par[x]]; x = par[x]
            return x
        for r in rel_rows.itertuples(index=False):
            a, b = str(r.subject_a), str(r.subject_b)
            par[f(a)] = f(b)
        comp = {}
        for x in list(par):
            comp.setdefault(f(x), set()).add(x)
        roles = {}
        for r in rel_rows.itertuples(index=False):
            ra, rb = REL_EDGE.get(str(r.relation), ('member', 'member', False, '', ''))[:2]
            for who, rr_ in ((str(r.subject_a), ra), (str(r.subject_b), rb)):
                if roles.get(who) in (None, 'member', 'household_member') or rr_ in _PARENT_ROLES:
                    roles[who] = rr_
        comps = sorted(comp.values(), key=lambda m: (-len(m), sorted(m)[0]))
        n_rel_all = len(comps)
        for members in comps[:cap]:
            if len(members) > 48:
                hubs = sorted(members, key=lambda m: -sum(1 for r in rel_rows.itertuples(index=False) if str(r.subject_a) == m or str(r.subject_b) == m))
                members = set(hubs[:48])
            e = [(str(r.subject_a), str(r.subject_b), str(r.relation)) for r in rel_rows.itertuples(index=False) if str(r.subject_a) in members and str(r.subject_b) in members]
            kinds = sorted({k.replace('_of', '').replace('_', ' ') for _, _, k in e})
            nodes_ = [dict(id=m, label=m, role=roles.get(m, 'member'), n=n_by_subject.get(m, 0)) for m in sorted(members)]
            undirected = not any(REL_EDGE.get(k, ('', '', False))[2] for _, _, k in e)
            if undirected and len(members) > 4:   # e.g. a dormitory of roommates: one hub, not a hairball
                show = nodes_[:20]
                clusters.append(dict(kind='relation', title=f"{' / '.join(kinds)} group · {len(members)} people", nodes=show, edges=[],
                                     hub=dict(label=f"{' / '.join(kinds)} group", more=max(0, len(nodes_) - len(show)))))
            else:
                clusters.append(dict(kind='relation', title=f"{' / '.join(kinds)}: {', '.join(sorted(members))}"[:300], nodes=nodes_, edges=e))
            seen |= members
        for members in comps[cap:]:
            seen |= members
    n_rel_comp = len(clusters)
    n_rel_total = n_rel_all if rel_rows is not None and len(rel_rows) else 0
    n_fam = 0
    if fam_rows is not None and len(fam_rows):
        fams = []
        for fid, x in fam_rows.groupby('family_id', sort=True):
            p = x.drop_duplicates('person_key')
            if len(p) < 2:
                continue
            labels = [label_by_person.get(k, k) for k in p.person_key]
            if set(map(str, labels)) <= seen:
                continue
            n_fam += 1
            roles = [str(r) if isinstance(r, str) and r else 'unknown' for r in p.family_role]
            fams.append((fid, list(p.person_key), labels, roles))
        by_motif = {}
        for fm in fams:
            by_motif.setdefault(' + '.join(sorted(fm[3])), []).append(fm)
        order = sorted(by_motif, key=lambda m: -len(by_motif[m]))
        picked, i = [], 0
        while len(picked) < max(0, cap - len(clusters)) and any(i < len(by_motif[m]) for m in order):
            for m in order:
                if i < len(by_motif[m]) and len(picked) < cap - len(clusters):
                    picked.append(by_motif[m][i])
            i += 1
        for fid, pks, labels, roles in picked:
            known = any(r not in ('unknown', 'household_member') for r in roles)
            nodes = [dict(id=pk, label=lb, role=r, n=n_by_person.get(pk, 0)) for pk, lb, r in zip(pks, labels, roles)]
            if known:
                clusters.append(dict(kind='family', title=f'family {fid}', nodes=nodes, edges=[], family_lines=True))
            else:
                clusters.append(dict(kind='family', title=f'family {fid}', nodes=[dict(n_, role=('household_member' if n_['role'] == 'household_member' else 'member')) for n_ in nodes], edges=[], hub=dict(label=f'family {fid}')))
    n_set = 0
    if set_groups is not None and len(set_groups):
        sg = set_groups.sort_values('n_people', ascending=False)
        n_set = len(sg)
        for x in sg.head(max(0, min(8, cap + 8 - len(clusters)))).itertuples(index=False):
            pks = [k for k in str(x.people).split(';') if k]
            show = pks[:20]
            clusters.append(dict(kind='setting', title=f'{str(x.group_type).replace("_", " ")} {x.group_id} · {int(x.n_people)} people',
                                 nodes=[dict(id=k, label=label_by_person.get(k, k), role='member', n=n_by_person.get(k, 0)) for k in show], edges=[],
                                 hub=dict(label=f'{str(x.group_type).replace("_", " ")} {x.group_id}', more=max(0, int(x.n_people) - len(show)))))
    return clusters, dict(n_rel_comp=n_rel_comp, n_rel_total=n_rel_total, n_fam=n_fam, n_set=n_set)


# ---- R2026.25 intervention arms: "I" marker (resolved = per-sample arms known) + a design-specific diagram on the project page
IV_CONTROL = {'placebo', 'no_intervention', 'standard_of_care', 'control', 'sham'}
IV_SHORT = {'no_intervention': 'no intervention', 'standard_of_care': 'standard care', 'dietary_supplement': 'supplement', 'diet_intervention': 'diet', 'prebiotic_or_fibre': 'prebiotic / fibre',
            'drug_therapy': 'drug', 'other_intervention': 'other', 'fmt': 'FMT'}
IV_DESIGNS = ('randomized_controlled_trial', 'non_randomized_controlled', 'crossover', 'single_arm_before_after', 'observational_with_procedure')


def iv_arms(x, design, labels):
    """x: catalog samples of one project (columns intervention, subject_id, days_since_first_sample?). Returns dict or None."""
    n = len(x)
    if not n:
        return None
    a = x.intervention.astype('string')
    cov = float(a.notna().mean())
    arms = []
    for v, g in x[a.notna()].groupby(a[a.notna()]):
        ppl = g.subject_id.dropna().astype(str).nunique() if 'subject_id' in g else 0
        codes = [c for c in str(v).split(';') if c]
        arms.append(dict(code=str(v), label=' + '.join(IV_SHORT.get(c, c.replace('_', ' ')) for c in codes), long=' + '.join(labels.get(c, c) for c in codes), control=all(c in IV_CONTROL for c in codes),
                         n_samples=int(len(g)), n_people=int(ppl) if ppl else None))
    arms.sort(key=lambda r: (r['control'], -r['n_samples']))
    multi = 0
    seq = {}
    if 'subject_id' in x.columns:
        xs = x[a.notna() & x.subject_id.notna()]
        k = xs.groupby('subject_id').intervention.nunique()
        multi = int((k >= 2).sum())
        if multi and 'days_since_first_sample' in xs.columns:
            for s_, g in xs[xs.subject_id.isin(k[k >= 2].index)].groupby('subject_id'):
                g = g.dropna(subset=['days_since_first_sample']).sort_values('days_since_first_sample')
                if len(g):
                    order = tuple(dict.fromkeys(g.intervention.astype(str)))
                    seq[order] = seq.get(order, 0) + 1
    need = 1 if design == 'single_arm_before_after' else 2
    resolved = cov >= 0.8 and len(arms) >= need
    return dict(cov=round(cov, 3), arms=arms[:8], n_arms=len(arms), resolved=resolved, multi=multi,
                seqs=[dict(order=[' + '.join(IV_SHORT.get(k, k.replace('_', ' ')) for k in c.split(';')) for c in o], n=v) for o, v in sorted(seq.items(), key=lambda kv: -kv[1])[:4]])


def iv_svg(iv, design, n_people_total=None):
    """Design-specific arm diagram (static SVG). Parallel designs fork from the cohort into arms (width ~ people);
    crossover shows the observed arm sequences; single-arm shows pre -> on intervention; observational shows exposed vs not."""
    arms = iv['arms']
    if not arms:
        return ''
    W = 460
    gold, grey, ink = '#CFB87C', '#B9B9B9', '#555'
    sz = lambda r: (r['n_people'] or r['n_samples'])
    mx = max(sz(r) for r in arms) or 1
    out = []
    def bar(x, y, w, r, h=16):
        col = grey if r['control'] else gold
        unit = f"{r['n_people']:,} people · {r['n_samples']:,} samples" if r['n_people'] else f"{r['n_samples']:,} samples"
        return (f'<rect x="{x:.1f}" y="{y}" width="{max(4, w):.1f}" height="{h}" rx="4" fill="{col}"><title>{html_escape(r.get("long") or r["label"])}: {unit}</title></rect>'
                f'<text x="{x + max(4, w) + 6:.1f}" y="{y + 12}" font-size="10.5" fill="{ink}">{html_escape(r["label"][:24])} <tspan fill="#999">{unit}</tspan></text>')
    if design in ('randomized_controlled_trial', 'non_randomized_controlled', 'observational_with_procedure') or (design == 'crossover' and not iv['seqs']):
        H = 18 + 24 * len(arms)
        cy = H / 2
        tot = n_people_total or sum(sz(r) for r in arms)
        out.append(f'<circle cx="16" cy="{cy:.1f}" r="11" fill="#fff" stroke="{ink}" stroke-width="1.2"/><text x="16" y="{cy + 3.5:.1f}" font-size="9" text-anchor="middle" fill="{ink}">'
                   f'{"R" if design == "randomized_controlled_trial" else ("E" if design == "observational_with_procedure" else "N")}</text>')
        for i, r in enumerate(arms):
            y = 10 + 24 * i
            out.append(f'<path d="M27,{cy:.1f} C60,{cy:.1f} 60,{y + 8} 92,{y + 8}" fill="none" stroke="#ccc" stroke-width="1.2"/>')
            out.append(bar(94, y, 120 * sz(r) / mx, r))
        cap = {'randomized_controlled_trial': 'randomised to parallel arms', 'non_randomized_controlled': 'non-randomised arms', 'observational_with_procedure': 'exposed vs. not exposed (observational)', 'crossover': 'crossover (arm order not resolved)'}[design]
    elif design == 'crossover':
        seqs = iv['seqs']
        H = 14 + 30 * len(seqs)
        for i, s_ in enumerate(seqs):
            y = 10 + 30 * i
            x = 8
            for j, lab in enumerate(s_['order'][:3]):
                c = grey if any(k in lab.lower() for k in ('placebo', 'no intervention', 'control', 'standard care')) else gold
                out.append(f'<rect x="{x}" y="{y}" width="92" height="18" rx="4" fill="{c}"/><text x="{x + 46}" y="{y + 12.5}" font-size="9.5" text-anchor="middle" fill="#333">{html_escape(lab[:16])}</text>')
                x += 92
                if j < len(s_['order'][:3]) - 1:
                    out.append(f'<path d="M{x + 3},{y + 9} l14,0" stroke="{ink}" stroke-width="1" marker-end="url(#ivarr)"/><text x="{x + 10}" y="{y - 1}" font-size="7.5" text-anchor="middle" fill="#999">washout</text>')
                    x += 22
            out.append(f'<text x="{x + 8}" y="{y + 12.5}" font-size="10.5" fill="{ink}">{s_["n"]:,} people</text>')
        cap = 'crossover: arm sequences observed per person'
    else:   # single_arm_before_after
        pre = [r for r in arms if r['control']]
        act = [r for r in arms if not r['control']]
        H = 46
        x = 8
        segs = [dict(r, label='before · ' + r['label']) for r in pre] + act
        for j, r in enumerate(segs[:4]):
            c = grey if r['control'] else gold
            lab = r['label'][:22]
            out.append(f'<rect x="{x}" y="10" width="120" height="20" rx="5" fill="{c}"><title>{html_escape(r["label"])}</title></rect><text x="{x + 60}" y="23.5" font-size="9.5" text-anchor="middle" fill="#333">{html_escape(lab)}</text>'
                       f'<text x="{x + 60}" y="42" font-size="9" text-anchor="middle" fill="#888">{r["n_samples"]:,} samples</text>')
            x += 120
            if j < len(segs[:4]) - 1:
                out.append(f'<path d="M{x + 3},20 l16,0" stroke="{ink}" stroke-width="1.2" marker-end="url(#ivarr)"/>')
                x += 24
        cap = 'single arm: samples before and on the intervention' if pre else 'single arm: samples on the intervention (no pre-intervention samples labelled)'
    svg = (f'<svg class="iv-svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="{html_escape(cap)}">'
           '<defs><marker id="ivarr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L8,4L0,8z" fill="#555"/></marker></defs>'
           + ''.join(out) + '</svg>')
    return svg + f'<div class="small muted">{html_escape(cap)}</div>'


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

AU_COLS = ['study_accession', 'pmid', 'position', 'author_display', 'author_key', 'is_first', 'is_last']
# R2026.25 author fix (owner, 2026-10-16, after an author's report): authors are credited only through papers that GENERATED the
# project's data. Re-analysis / citing papers (reused_public_data, cited_only) never credit authors; 'unsure' links are listed
# separately as "possibly associated". Relation-less infant rows count as generating only when the BioProject record itself
# cites the paper (bioproject_publication_epmc) or the paper was curated for the study (catalog_linked_pmids).
AU_GENERATED = {'own_data', 'bioproject_registered', 'ena_registered'}
AU_GENERATED_METHODS = {'bioproject_publication_epmc', 'catalog_linked_pmids'}


def _au_initials_compatible(a, b):
    a, b = a.replace(' ', ''), b.replace(' ', '')
    return a.startswith(b) or b.startswith(a)


def orcid_key_map(rows):
    """(orcid, author_key) -> canonical key for name variants carrying the same Europe PMC ORCID with the same surname and
    compatible initials ('morgan x' -> 'morgan xc'). Applied ROW-wise (only rows that carry that ORCID): a bare key such as
    'zhou j' is shared by many people. ORCIDs on different surnames are Europe PMC assignment errors and are ignored."""
    if 'orcid' not in rows.columns:
        return {}
    r = rows.dropna(subset=['orcid', 'author_key'])
    r = r[r.orcid.astype(str).str.match(r'^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$')]
    m = {}
    for oid, g in r.groupby('orcid'):
        keys = sorted(set(g.author_key), key=lambda k: (-len(k), k))
        if len(keys) < 2:
            continue
        canon = keys[0]
        sur = canon.rsplit(' ', 1)[0]
        for k in keys[1:]:
            ks = k.rsplit(' ', 1)
            if len(ks) == 2 and ks[0] == sur and ' ' in canon and _au_initials_compatible(ks[1], canon.rsplit(' ', 1)[1]):
                m[(oid, k)] = canon
    return m


def load_site_authors(pkg, inc_set, overrides_path, RR=None):
    parts, possible, rows_orcid = [], [], []
    if (pkg / 'registry_authors.parquet').exists():
        ra = pd.read_parquet(pkg / 'registry_authors.parquet')
        ra = ra[ra[RR].isna()] if RR and RR in ra.columns else ra
        rows_orcid.append(ra)
        ra = ra.assign(orcid=ra.orcid if 'orcid' in ra.columns else None)
        parts.append(ra[ra.paper_relation.isin(AU_GENERATED)][AU_COLS + ['orcid']])
        possible.append(ra[ra.paper_relation.eq('unsure')][AU_COLS + ['orcid']])
    if (pkg / 'authors.parquet').exists():
        ia = pd.read_parquet(pkg / 'authors.parquet')
        gen = ia.paper_relation.isin(AU_GENERATED) | (ia.paper_relation.isna() & ia.link_method.isin(AU_GENERATED_METHODS))
        parts.append(ia[gen][AU_COLS])
        possible.append(ia[ia.paper_relation.eq('unsure')][AU_COLS])
    empty = pd.DataFrame(columns=AU_COLS)
    authors = pd.concat(parts, ignore_index=True) if parts else empty.copy()
    poss = pd.concat(possible, ignore_index=True) if possible else empty.copy()
    ov = pd.read_csv(overrides_path, comment='#', dtype=str).fillna('') if Path(overrides_path).exists() else pd.DataFrame(columns=['action'])
    if len(ov):
        assert set(ov.action) <= {'remove_author', 'add_author', 'remove_link'}, f'author_overrides.csv: unknown action {set(ov.action)}'
        add = ov[ov.action == 'add_author']
        if len(add):
            add = add.assign(position=add.groupby(['study_accession', 'pmid']).cumcount() + 1, is_first=add.is_first.eq('1'), is_last=add.is_last.eq('1'))
            authors = pd.concat([add[AU_COLS], authors], ignore_index=True)
    km = orcid_key_map(pd.concat(rows_orcid, ignore_index=True)) if rows_orcid else {}
    disp = {}
    for d in (authors, poss):
        d['pmid'] = d.pmid.astype(str).str.replace(r'\.0$', '', regex=True)
        oc = d['orcid'] if 'orcid' in d.columns else pd.Series([None] * len(d), index=d.index)
        d['author_key'] = [km.get((o, k), k) for o, k in zip(oc, d.author_key)]
    for r in authors.dropna(subset=['author_key']).itertuples(index=False):
        if len(str(r.author_display)) > len(disp.get(r.author_key, '')):
            disp[r.author_key] = str(r.author_display)
    for d in (authors, poss):
        d['author_display'] = [disp.get(k, v) for k, v in zip(d.author_key, d.author_display)]
    if len(ov):
        for r in ov[ov.action == 'remove_author'].itertuples(index=False):
            for d in (authors, poss):
                keys = {r.author_key} | {c for (o, k), c in km.items() if k == r.author_key}
                d.drop(d.index[(d.study_accession == r.study_accession) & d.author_key.isin(keys)], inplace=True)
        for r in ov[ov.action == 'remove_link'].itertuples(index=False):
            for d in (authors, poss):
                d.drop(d.index[(d.study_accession == r.study_accession) & (d.pmid == str(r.pmid))], inplace=True)
    authors = authors[authors.study_accession.isin(inc_set)].dropna(subset=['author_key']).drop_duplicates(['study_accession', 'author_key'])[AU_COLS].reset_index(drop=True)
    poss = poss[poss.study_accession.isin(inc_set)].dropna(subset=['author_key']).drop_duplicates(['study_accession', 'author_key'])
    have = set(zip(authors.study_accession, authors.author_key))
    poss = poss[pd.Series([(s, k) not in have for s, k in zip(poss.study_accession, poss.author_key)], index=poss.index, dtype=bool)][AU_COLS].reset_index(drop=True)
    return authors, poss


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
    """Item 11: per-study sequencing summary from catalog_runs.parquet (base_count / 1e9 per run)."""
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
    ap.add_argument('--human-dir', default=None, help='ignored since R2026.31 (one catalog; kept so older Makefiles still run)')
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
    cs = pd.read_parquet(pkg / 'catalog_studies.parquet')
    cs_all = cs.copy()
    cw = pd.read_parquet(pkg / 'catalog_sample_metadata_wide.parquet')
    cd = pd.read_parquet(pkg / 'catalog_sample_determinations.parquet')
    cs = cs[cs[RR].isna()].reset_index(drop=True) if RR in cs.columns else cs
    if 'longitudinal_resolved' not in cs.columns and 'longitudinal_status' in cs.columns:   # packages built before R2026.25
        _rl = pd.read_parquet(pkg / 'catalog_relations.parquet') if (pkg / 'catalog_relations.parquet').exists() else pd.DataFrame(columns=['study_accession'])
        cs['n_relations'] = cs.study_accession.map(_rl.groupby('study_accession').size()).fillna(0).astype(int)
        _lg = cs.longitudinal_status.isin(['longitudinal', 'repeated_unresolved'])
        cs['longitudinal_resolved'] = (_lg & pd.to_numeric(cs.get('followup_days_median'), errors='coerce').notna()).where(_lg, None)
        _rr = (pd.to_numeric(cs.get('n_families'), errors='coerce').fillna(0) > 0) | (pd.to_numeric(cs.get('n_shared_settings'), errors='coerce').fillna(0) > 0) | (cs.n_relations > 0)
        _hr = cs.relatedness_designs.notna() & ~cs.relatedness_designs.astype(str).isin(['', 'nan', 'None']) if 'relatedness_designs' in cs.columns else _rr
        cs['relatedness_resolved'] = _rr.where(_hr | _rr, None)
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
    has_col['has_rna'] = 'has_rna' in cw.columns and 'has_dna' in cw.columns
    n_by_study = cw.groupby('study_accession').size().to_dict()
    # item 11: run-level table (optional) → per-study sequencing block + Gbp/run on the studies index
    runs_path = pkg / 'catalog_runs.parquet'
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
    # R2026.31: projects found by the large update's own enumeration (RNA slice, newer deposits) are catalog projects without a
    # registry row; F13 now only requires that the registry and the catalog do not disagree on accessions they share
    n_outside_registry = len(inc_set - set(rg.study_accession))
    print(f'catalog projects without a registry row: {n_outside_registry}', file=sys.stderr)
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
    if (pkg / 'catalog_study_papers.parquet').exists():   # papers judged by the link audit to have generated / reused a project's data
        cpl = pd.read_parquet(pkg / 'catalog_study_papers.parquet')
        cpl = cpl.reindex(columns=['study_accession', 'paper_id', 'pmid', 'pmcid', 'doi', 'title', 'relation', 'year', 'journal'])
        papers = pd.concat([papers, cpl], ignore_index=True)
    papers['paper_id'] = papers.paper_id.astype(str)
    papers = papers[papers.study_accession.isin(inc_set)].drop_duplicates(['study_accession', 'paper_id']).reset_index(drop=True)
    papers['title'] = papers.title.map(strip_frame_tokens).map(lambda t: None if isnull(t) else re.sub(r'<[^>]+>', '', str(t)))
    authors, authors_possible = load_site_authors(pkg, inc_set, Path(a.config).parent / 'author_overrides.csv', RR)
    authors['pmid'] = authors.pmid.astype(str)
    authors['author_display'] = authors.author_display.astype(str)
    leaks = [v for v in authors.author_display if ORG_LEAK_RE.search(v)]
    assert not leaks, f'F1: author strings leak submission ids or e-mail addresses: {leaks[:3]}'

    # curated infant cohorts (names + membership) — used to name the all-age cohorts
    coh = pd.read_csv(pkg / 'cohorts.csv', dtype=str, keep_default_na=False) if (pkg / 'cohorts.csv').exists() else pd.DataFrame(columns=['cohort_id', 'cohort_name', 'study_accessions'])

    # Sandpiper panels where they exist (infant studies) — optional
    # R2026.20: one genus panel for EVERY profiled catalog project (gut_sandpiper_study_panels); the infant-only panels are the fallback
    panels = pd.read_parquet(pkg / 'catalog_sandpiper_study_panels.parquet') if (pkg / 'catalog_sandpiper_study_panels.parquet').exists() else (
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
                description=f"{names['title']}: a curated, evidence-linked catalog of human-associated shotgun metagenomes and metatranscriptomes — every body site, all ages — with per-sample metadata, and a registry of every screened public study.",
                citation=f'{names["title"]}, release {release_id} (data package {version}), OlmLab, {cur_rel["release_date"]}.' + (f' doi:{doi}' if doi else ''),
                sri=json.loads((HERE / 'static' / 'vendor' / 'SRI.json').read_text()), has_contribute=True, contribute_page='contribute/index.html',
                has_registry=True, registry_page='registry/index.html', data_repo=read_github_repo(a.config, 'data'))

    env = Environment(loader=FileSystemLoader(HERE / 'templates'), autoescape=select_autoescape(['html']))
    def f_gbp(v):   # R2026.25: shallow runs are shown in Mbp instead of rounding to 0.00 Gbp
        try:
            x = float(v)
        except (TypeError, ValueError):
            return '—'
        if x != x:
            return '—'
        return f'{x:,.2f} Gbp' if x >= 0.1 else (f'{x * 1000:,.1f} Mbp' if x > 0 else '0')
    env.filters.update(fmt=f_fmt, pct=f_pct, pct1=f_pct1, num2=f_num2, numint=f_numint, gbp=f_gbp)
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
    IN_DATA = ['catalog_sample_metadata_wide.parquet', 'catalog_studies.parquet', 'catalog_sample_determinations.parquet', sspec['files']['studies']]
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
                              desc=FILE_DESC.get(f.name, ''), group=('catalog' if f.name.startswith('catalog_') or f.name == 'CATALOG_SANDPIPER_REPORT.md' else 'registry' if f.name.startswith('registry_') else
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
    def md_to_html(text, idp=''):
        html = markdown.markdown(text, extensions=['tables', 'fenced_code', 'toc'])
        if idp:  # several documents on one page (release notes on Downloads): keep heading ids unique
            html = re.sub(r'<(h[1-6]) id="', lambda m: f'<{m.group(1)} id="{idp}-', html)
        html = re.sub(r'<(/?)h([1-5])\b', lambda m: f'<{m.group(1)}h{int(m.group(2)) + 1}', html)  # accessibility: one h1 per page
        return re.sub(r'<a href="([^"]+)">', lambda m: m.group(0) if m.group(1).startswith(('http', '#')) else '<a>', html)
    dictionary = (pkg / 'DATA_DICTIONARY.md').read_text(encoding='utf-8')
    docs = [('README.md', readme, 'README'), ('DATA_DICTIONARY.md', dictionary, 'Data dictionary'), ('CHANGELOG.md', (pkg / 'CHANGELOG.md').read_text(encoding='utf-8'), 'Changelog')]
    for name, title in [('REGISTRY_REPORT.md', 'Registry report'), ('SANDPIPER_REPORT.md', 'Sandpiper report'), ('AUTHORS_REPORT.md', 'Authors report (infant extension)'), ('DATA_MODEL_FIX_REPORT.md', 'Data-model fix report (infant extension)')]:
        if (pkg / name).exists():
            docs.append((name, (pkg / name).read_text(encoding='utf-8'), title))
    for name, title in PUBLIC_REPORT_DOCS:
        if rep and (rep / name).exists() and name not in [d[0] for d in docs]:
            docs.append((name, (rep / name).read_text(encoding='utf-8'), title))
    for name, title in [('COLLECTIONS.md', 'Collections: how membership rules work and how to propose one'), ('CATALOG_SANDPIPER_REPORT.md', 'Sandpiper profiles for the whole catalog (report)')]:
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
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    from pages import project_facts as PF
    exposure_by_study = {}   # paper-derived exposure items (catalog_exposure_items.parquet), shown under Living conditions
    if (pkg / 'catalog_exposure_items.parquet').exists():
        _ex = pd.read_parquet(pkg / 'catalog_exposure_items.parquet')
        for acc_, e_ in _ex.groupby('study_accession'):
            exposure_by_study[acc_] = [dict(concept=str(x.get('concept') or ''), scale=str(x.get('scale') or ''), summary=str(x.get('summary') or '')[:200],
                                            quote=str(x.get('quote') or '')[:300]) for x in e_.head(12).to_dict('records')]
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
    # R2026.23 sampling design + relatedness per project (gut_studies longitudinal / relatedness columns, gut_relatedness table,
    # per-sample days_since_first_sample / family columns of the wide table)
    relg = pd.read_parquet(pkg / 'catalog_relatedness.parquet') if (pkg / 'catalog_relatedness.parquet').exists() else pd.DataFrame(columns=['study_accession'])
    design_by_study = {}
    _cxp = pkg / 'catalog_metadata_contradictions.parquet'
    _cx_all = pd.read_parquet(_cxp) if _cxp.exists() else pd.DataFrame(columns=['study_accession'])
    contra_by_study = {}
    # R2026.26 control samples named on the project page
    controls_by_study = {}
    if 'sample_type' in cw.columns:
        _CTL = {'negative_control': 'negative control (blank)', 'mock_community': 'mock community', 'positive_control': 'positive control', 'synthetic_community': 'defined community'}
        for r in cw[cw.sample_type.fillna('specimen') != 'specimen'][['study_accession', 'sample_key', 'sample_type']].sort_values(['study_accession', 'sample_key']).itertuples(index=False):
            controls_by_study.setdefault(r.study_accession, []).append(dict(key=r.sample_key, label=_CTL.get(r.sample_type, r.sample_type)))
    iv_by_study = {}
    _ivl = {c: (v or {}).get('label', c) for c, v in iv_voc.items()}
    if 'intervention' in cw.columns and 'intervention_design' in cs.columns:
        _ivs = cs[cs.intervention_design.isin(IV_DESIGNS)].set_index('study_accession')
        _ivcols = [c_ for c_ in ['study_accession', 'intervention', 'subject_id', 'days_since_first_sample'] if c_ in cw.columns]
        for acc_, x_ in cw[cw.study_accession.isin(set(_ivs.index))][_ivcols].groupby('study_accession'):
            dz_ = _ivs.loc[acc_, 'intervention_design']
            r_ = iv_arms(x_, dz_, _ivl)
            if r_ is None:
                continue
            ns_ = _ivs.loc[acc_, 'n_subjects'] if 'n_subjects' in _ivs.columns else None
            r_['svg'] = iv_svg(r_, dz_, None if isnull(ns_) else int(ns_)) if r_['resolved'] else ''
            r_['design'] = dz_
            iv_by_study[acc_] = r_
    if len(_cx_all):
        from catalog.scopes.contradictions import source_label as _slab, study_summary as _csum
        _csm = _csum(_cx_all).set_index('study_accession').contradiction_summary.to_dict()
        for acc_, g_ in _cx_all.groupby('study_accession'):
            g_ = g_.sort_values(['field_name', 'kind', 'sample_key'])
            contra_by_study[acc_] = dict(n=int(g_.sample_key.nunique()), n_rows=int(len(g_)), summary=_csm.get(acc_, ''), rows=[dict(
                sample=r_.sample_key, field=str(r_.field_name).replace('_at_collection_days', '').replace('_', ' '),
                a=None if isnull(r_.value_a) else str(r_.value_a), sa=_slab(r_.source_a) if not isnull(r_.source_a) else '',
                b=None if isnull(r_.value_b) else str(r_.value_b), sb=_slab(r_.source_b) if not isnull(r_.source_b) else '',
                handling=r_.handling, note=None if isnull(r_.note) else str(r_.note)) for r_ in g_.head(40).itertuples()])
    _tl_cols = [c_ for c_ in ['study_accession', 'sample_key', 'subject_id', 'person_key', 'days_since_first_sample', 'family_id', 'family_role', 'subject_n_samples'] if c_ in cw.columns]
    _tl = cw[_tl_cols] if 'days_since_first_sample' in cw.columns else pd.DataFrame(columns=['study_accession'])
    _tl_by = {k_: v_ for k_, v_ in _tl[pd.to_numeric(_tl.get('subject_n_samples'), errors='coerce').fillna(0) >= 2].groupby('study_accession')} if len(_tl) else {}
    _fam_by = {k_: v_ for k_, v_ in cw[cw.family_id.notna()][['study_accession', 'sample_key', 'person_key', 'family_id', 'family_role', 'subject_id']].groupby('study_accession')} if 'family_id' in cw.columns and 'person_key' in cw.columns else {}
    _relg_by = {k_: v_ for k_, v_ in relg.groupby('study_accession')} if len(relg) else {}
    _rlx = pd.read_parquet(pkg / 'catalog_relations.parquet') if (pkg / 'catalog_relations.parquet').exists() else pd.DataFrame(columns=['study_accession', 'subject_a', 'subject_b', 'relation'])
    _rlx_by = {k_: v_ for k_, v_ in _rlx.groupby('study_accession')} if len(_rlx) else {}
    _pc = cw[[c_ for c_ in ['study_accession', 'person_key', 'subject_id', 'sample_key', 'biosample_accession'] if c_ in cw.columns]]
    _net_studies = set(_rlx_by) | set(_fam_by) | set(_relg_by)
    _pc_by = {k_: v_ for k_, v_ in _pc[_pc.study_accession.isin(_net_studies)].groupby('study_accession')} if 'person_key' in _pc.columns else {}
    for r_ in cs.itertuples(index=False):
        acc_ = r_.study_accession
        ls_ = getattr(r_, 'longitudinal_status', None)
        rd_ = getattr(r_, 'relatedness_designs', None)
        if (ls_ not in ('longitudinal', 'repeated_unresolved')) and (isnull(rd_) or str(rd_) in ('', 'nan', 'None')) and not (int(getattr(r_, 'n_families', 0) or 0) or int(getattr(r_, 'n_shared_settings', 0) or 0) or int(getattr(r_, 'n_relations', 0) or 0)):
            continue
        d_ = dict(status=ls_, rel=([] if isnull(rd_) else [x for x in str(rd_).split(';') if x and x not in ('nan', 'None', 'none')]),
                  lg_res=(None if isnull(getattr(r_, 'longitudinal_resolved', None)) else bool(getattr(r_, 'longitudinal_resolved'))),
                  rel_res=(None if isnull(getattr(r_, 'relatedness_resolved', None)) else bool(getattr(r_, 'relatedness_resolved'))), n_rel=int(getattr(r_, 'n_relations', 0) or 0), n_sub=getattr(r_, 'n_subjects', None), n_rep=getattr(r_, 'n_subjects_repeated', None),
                  n_srep=getattr(r_, 'n_samples_repeated', None), spsm=getattr(r_, 'samples_per_subject_median', None), spsx=getattr(r_, 'samples_per_subject_max', None),
                  fum=getattr(r_, 'followup_days_median', None), fux=getattr(r_, 'followup_days_max', None), ivm=getattr(r_, 'interval_days_median', None), axis=getattr(r_, 'time_axis', None),
                  schedule=getattr(r_, 'sampling_schedule', None), origin=getattr(r_, 'time_origin', None), fu_stated=getattr(r_, 'followup_days_stated', None),
                  n_fam=int(getattr(r_, 'n_families', 0) or 0), n_relp=int(getattr(r_, 'n_related_people', 0) or 0), n_mc=int(getattr(r_, 'n_mother_child_families', 0) or 0),
                  n_tw=int(getattr(r_, 'n_twin_sets', 0) or 0), n_set=int(getattr(r_, 'n_shared_settings', 0) or 0), summary=getattr(r_, 'relatedness_summary', None))
        for k_ in list(d_):
            if isinstance(d_[k_], float) and math.isnan(d_[k_]):
                d_[k_] = None
        d_ = {k_: (None if (not isinstance(v_, (list, dict, str)) and v_ is not None and pd.isna(v_)) else v_) for k_, v_ in d_.items()}   # pd.NA / NaN -> None
        d_['fmt'] = {k_: (_days_label(d_[k_]) if d_[k_] is not None else None) for k_ in ('fum', 'fux', 'ivm', 'fu_stated')}
        mot = json.loads(getattr(r_, 'family_motifs', None)) if isinstance(getattr(r_, 'family_motifs', None), str) else {}
        d_['motifs'] = [dict(motif=m_, n=int(n_), svg=motif_svg(m_)) for m_, n_ in sorted(mot.items(), key=lambda kv: -kv[1])[:8]]
        g_ = _relg_by.get(acc_)
        d_['settings'] = [] if g_ is None else [dict(type=x.group_type, id=x.group_id, n=int(x.n_people)) for x in g_[g_.group_kind == 'shared_setting'].sort_values('n_people', ascending=False).head(20).itertuples()]
        d_['families'] = [] if g_ is None else [dict(id=x.group_id, motif=x.motif, n=int(x.n_people), samples=str(x.sample_keys).split(';')[:12]) for x in g_[g_.group_kind == 'family'].sort_values('n_samples', ascending=False).head(25).itertuples()]
        _pcs = _pc_by.get(acc_)
        if _pcs is not None and (acc_ in _rlx_by or acc_ in _fam_by or (g_ is not None and len(g_))):
            _nbs = _pcs.subject_id.dropna().astype(str).value_counts().to_dict() if 'subject_id' in _pcs.columns else {}
            _nbp = _pcs.person_key.dropna().astype(str).value_counts().to_dict()
            for c_ in ('sample_key', 'biosample_accession'):
                if c_ in _pcs.columns:
                    for k_ in _pcs[c_].dropna().astype(str):
                        _nbs.setdefault(k_, 1)
            _lbp = dict(zip(_pcs.person_key.astype(str), _pcs.subject_id.astype(str))) if 'subject_id' in _pcs.columns else {}
            _lbp = {k_: (v_ if v_ not in ('<NA>', 'nan', 'None') else k_) for k_, v_ in _lbp.items()}
            _cl, _tot = relnet_clusters(acc_, _rlx_by.get(acc_), _fam_by.get(acc_), None if g_ is None else g_[g_.group_kind == 'shared_setting'], _nbs, _nbp, _lbp)
            d_['net'] = [dict(kind=c_['kind'], title=c_['title'], svg=relnet_svg(c_, f'{i_}')) for i_, c_ in enumerate(_cl)]
            d_['net_tot'] = _tot
            d_['net_roles'] = sorted({n_['role'] for c_ in _cl for n_ in c_['nodes']} - {'unknown'}, key=lambda r_: list(NET_ROLE_COLOR).index(r_) if r_ in NET_ROLE_COLOR else 99)
            d_['net_roles'] = [dict(role=r_, color=NET_ROLE_COLOR.get(r_, '#8C8F91'), abbr=NET_ROLE_ABBR.get(r_, '')) for r_ in d_['net_roles']]
        else:
            d_['net'], d_['net_tot'], d_['net_roles'] = [], {}, []
        tl_ = _tl_by.get(acc_)
        people = []
        if tl_ is not None:
            fam_of = {}
            if acc_ in _fam_by:
                fb = _fam_by[acc_]
                fam_of = dict(zip(fb.person_key, zip(fb.family_id, fb.family_role)))
            for pk_, y_ in tl_.groupby('person_key'):
                ds_ = pd.to_numeric(y_.days_since_first_sample, errors='coerce').dropna().tolist()
                if len(ds_) >= 2:
                    fam_, role_ = fam_of.get(pk_, (None, None))
                    fam_ = fam_ if isinstance(fam_, str) and fam_ else None
                    role_ = role_ if isinstance(role_, str) and role_ else None
                    people.append(dict(label=(y_.subject_id.dropna().astype(str).iloc[0] if y_.subject_id.notna().any() else pk_), days=ds_, family=fam_, role=role_, n=len(ds_)))
            people.sort(key=lambda p_: (str(p_['family'] or 'zzzz' + p_['label']), -p_['n']))
            if len(people) > 36:
                fams_ = [p_ for p_ in people if p_['family']]
                people = (fams_[:36] if len(fams_) >= 12 else sorted(people, key=lambda p_: -p_['n'])[:36])
                people.sort(key=lambda p_: (str(p_['family'] or 'zzzz' + p_['label']), -p_['n']))
        d_['timeline'] = timeline_svg(people) if people else ''
        d_['n_timeline'] = len(people)
        design_by_study[acc_] = d_
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
            _pbs = r0.get('panel_body_site') if hasattr(r0, 'get') else None
            d['site'] = (vocabs['body_site'].get(_pbs) or str(_pbs).replace('_', ' ')).lower() if isinstance(_pbs, str) and _pbs else ''
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
                                            for x in papers_by_study.get(r.study_accession, []) if x.get('doi') or x.get('pmid')), ''),
                            paper_label=next(((f"PMID {x['pmid']}" if x.get('pmid') else 'DOI') for x in papers_by_study.get(r.study_accession, []) if x.get('doi') or x.get('pmid')), ''),
                            n_papers=len(papers_by_study.get(r.study_accession, []))))
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
    def _sites_of(s_):   # body-site codes of a project's samples, most samples first (catalog_studies.sample_body_sites)
        d_ = jl(s_.get('sample_body_sites'))
        return [k for k, _ in sorted(d_.items(), key=lambda kv: (-kv[1], kv[0]))] if d_ else [c for c in split_list(s_.get('body_sites')) if c != 'unknown_site']
    sidx_rows = [dict(**study_filters(s['study_accession']), ctry=';'.join(sorted(_cc.get(s['study_accession'], []))[:12]), pc=(s.get('population_condition') or '') if not isnull(s.get('population_condition')) else '',
                      dz=(s.get('intervention_design') or '') if not isnull(s.get('intervention_design')) else '', ags=sorted(k for k in s['age_categories_d'] if k != 'unknown'),
                      bs=_sites_of(s), na=(s.get('nucleic_acid') or '') if not isnull(s.get('nucleic_acid')) else '',
                      a=s['study_accession'], t=((s.get('short_title') if not isnull(s.get('short_title')) else None) or s['study_title'] or '')[:160], tf=(s['study_title'] or '')[:300], n=int(s['n_samples_curated'] or 0), ag=s['ages_short'], hc=s['top_condition'],
                      iv=(s.get('interventions') or '') if not isnull(s.get('interventions')) else '', co=_top_key(s.get('top_country')), ls=s.get('life_stage_primary') or '',
                      src=s['curated_source'], sf=''.join({'used': 'u', 'checked': 'c'}.get(x['status'], 'n') for x in s['sources']), fa=s['first_author'], p=s['n_papers'], y=(s.get('first_public_min') or '')[:4],
                      gs=(round(seq_by_study[s['study_accession']]['gbp_per_sample'], 2) if seq_by_study.get(s['study_accession'], {}).get('gbp_per_sample') is not None else None),
                      **({'grp': s['study_group_id'], 'grpn': len(str(s['study_group_members']).split(';'))} if not isnull(s.get('study_group_id')) else {}),
                      **({'lg': (2 if (not isnull(s.get('longitudinal_resolved')) and bool(s.get('longitudinal_resolved'))) else 1)} if s.get('longitudinal_status') in ('longitudinal', 'repeated_unresolved') else {}),
                      **({'rd': str(s['relatedness_designs'])} if not isnull(s.get('relatedness_designs')) and str(s.get('relatedness_designs')) not in ('', 'nan', 'None') else ({'rd': 'families'} if (not isnull(s.get('relatedness_resolved')) and bool(s.get('relatedness_resolved'))) else {})),
                      **({'rr': 1} if (not isnull(s.get('relatedness_resolved')) and bool(s.get('relatedness_resolved'))) else {}),
                      **({'ivr': (2 if iv_by_study[s['study_accession']]['resolved'] else 1)} if s['study_accession'] in iv_by_study else {}),
                      **({'ma': str(s.get('metadata_access'))} if str(s.get('metadata_access') or 'open') in ('controlled', 'partly_controlled') else {}),
                      **({'nc': int(s.get('n_control_samples'))} if (not isnull(s.get('n_control_samples')) and int(s.get('n_control_samples') or 0) > 0) else {}),
                      **({'cx': int(s.get('n_contradicted_samples')), 'cxs': str(s.get('contradiction_summary') or '')[:400]} if (not isnull(s.get('n_contradicted_samples')) and int(s.get('n_contradicted_samples') or 0) > 0) else {}))
                 for s in studies]
    (out / 'data' / 'studies_index.json').write_text(dumps(sidx_rows), encoding='utf-8')
    # home-page globe (R2026.25): projects per country (ISO-2), click -> Project sheet filtered to that country
    _hg = {}
    for r_ in sidx_rows:
        for c_ in (r_.get('ctry') or '').split(';'):
            if c_:
                _hg[c_] = _hg.get(c_, 0) + 1
    (out / 'data' / 'home_globe.json').write_text(json.dumps(_hg, sort_keys=True), encoding='utf-8')
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
    def _site_label(c):
        v_ = vocabs['body_site'].get(c)
        return v_ if isinstance(v_, str) else (v_.get('label') if isinstance(v_, dict) and v_.get('label') else c.replace('_', ' '))
    render('studies_index.html', 'studies/index.html', '../', nav='studies', use_datatables=True, stats=stats, n_rows=len(studies), has_seq=bool(seq_by_study),
           hc_opts=[(c, hc_labels.get(c, '')) for c in _hcs if c not in ('unknown',)], iv_opts=[(c, (iv_voc.get(c) or {}).get('label', c), _ivc[c]) for c in iv_voc if c in _ivc],
           design_opts=[d for d in ('randomized_controlled_trial', 'non_randomized_controlled', 'crossover', 'single_arm_before_after', 'observational_with_procedure') if any(r_['dz'] == d for r_ in sidx_rows)],
           age_opts=[c for c in age_cats if c != 'unknown'],
           site_opts=[(c, _site_label(c), n) for c, n in sorted(Counter(c for r_ in sidx_rows for c in r_['bs']).items(), key=lambda kv: -kv[1])],
           na_opts=[(k, PF.NA_LABEL[k]) for k in ('dna', 'rna', 'both') if any(r_['na'] == k for r_ in sidx_rows)],
           site_label_map={c: _site_label(c) for r_ in sidx_rows for c in r_['bs']},
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
        sites = counts_sorted(g.body_site_code.where(g.body_site_code.notna() & (g.body_site_code != 'unknown_site')).dropna().map(lambda c: (vocabs['body_site'].get(c) or c.replace('_', ' ')) if isinstance(vocabs['body_site'].get(c), str) else c.replace('_', ' '))) if 'body_site_code' in g.columns else []
        site_counts = {k: int(v) for k, v in g.body_site_code.dropna().value_counts().items()} if 'body_site_code' in g.columns else {}
        n_subjects = int(g.subject_id.nunique()) if 'subject_id' in g.columns and g.subject_id.notna().any() else 0
        n_timepoints = int(g.timepoint_label.nunique()) if 'timepoint_label' in g.columns and g.timepoint_label.notna().any() else 0
        has_dna = int(g.has_dna.fillna(False).astype(bool).sum()) if 'has_dna' in g.columns else 0
        has_rna = int(g.has_rna.fillna(False).astype(bool).sum()) if 'has_rna' in g.columns else 0
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
               group_stmts=group_stmts, iv_labels={c: (v or {}).get('label', c) for c, v in iv_voc.items()}, ev_rows=ev_rows_study, n_det=int(len(dg)), panel=panel_by_study.get(acc), design=design_by_study.get(acc), contra=contra_by_study.get(acc), ivarms=iv_by_study.get(acc), help=help_by_study.get(acc), flag_url=flag, n_core=len(CORE_FIELDS),
               controls=controls_by_study.get(acc, []), samples=srows, sample_cols=SAMPLE_COLS, tcols=tcols, glance=study_glance(g), verify_url=verify_issue_url(issue_repo, acc, release_id), cov_missing=[LABELS.get(f, f) for f in cov_missing], thr_pct=int(round(100 * thr)), n_total=n_total, n_shown=len(shown), dl=study_dl[acc], hc_labels=hc_labels,
               site_labels=vocabs['body_site'], stage_labels=vocabs['life_stage'],
               na_label=PF.NA_LABEL, methods=PF.methods_strip(PF.facts_of(s)), body=PF.body_figure(site_counts, n_total),
               living=PF.living_table(PF.living_of(s)), exposure=exposure_by_study.get(acc, []), n_subjects=n_subjects,
               n_timepoints=n_timepoints, has_dna=has_dna, has_rna=has_rna,
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
    # R2026.23 single-sample page: one static page + accession -> project lookup shards (keyed by the accession's last two
    # digits, ~100 small JSON files); the page reads the project's existing slice and evidence files in the browser
    lk_studies = cs[['study_accession', 'study_title']].copy()
    if 'short_title' in cs.columns:
        lk_studies['study_title'] = cs.short_title.where(cs.short_title.notna() & (cs.short_title.astype(str) != ''), cs.study_title)
    s_ix = {a_: i_ for i_, a_ in enumerate(lk_studies.study_accession)}
    def _shard(x):
        m = re.search(r'(\d)(\d)\D*$', x) or re.search(r'(\d)\D*$', x)
        return 'xx' if not m else (m.group(1) + m.group(2) if m.lastindex == 2 else '0' + m.group(1))
    lk = {}
    for col in ('sample_key', 'biosample_accession', 'secondary_sample'):
        if col in cw.columns:
            for k_, st_ in zip(cw[col].astype('string'), cw.study_accession):
                if k_ is not pd.NA and k_ and str(k_) != 'nan':
                    lk.setdefault(_shard(str(k_)), {}).setdefault(str(k_).upper(), str(s_ix[st_]))
    if runs_path.exists():
        gr_ = pd.read_parquet(runs_path, columns=['run_accession', 'sample_key', 'study_accession'])
        gr_ = gr_[gr_.sample_key.isin(set(cw.sample_key)) & gr_.study_accession.isin(s_ix)]
        for ra_, sk_, st_ in zip(gr_.run_accession, gr_.sample_key, gr_.study_accession):
            lk.setdefault(_shard(str(ra_)), {}).setdefault(str(ra_).upper(), f'{s_ix[st_]}|{sk_}')
    (out / 'data' / 'lookup').mkdir(parents=True, exist_ok=True)
    for sh_, d_ in lk.items():
        (out / 'data' / 'lookup' / f'{sh_}.json').write_text(json.dumps(d_, separators=(',', ':')), encoding='utf-8')
    (out / 'data' / 'lookup' / 'studies.json').write_text(json.dumps([[a_, str(t_ or '')[:140]] for a_, t_ in zip(lk_studies.study_accession, lk_studies.study_title)], separators=(',', ':')), encoding='utf-8')
    render('sample.html', 'samples/sample.html', '../', nav='samples', example=str(cw.sample_key.iloc[0]) if len(cw) else '', labels_json=json.dumps({k_: v_ for k_, v_ in LABELS.items()}),
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Samples', href='index.html'), dict(label='Sample')])
    _sc = cw.body_site_code.where(cw.body_site_code != 'unknown_site').dropna().value_counts() if 'body_site_code' in cw.columns else pd.Series(dtype=int)
    render('explorer.html', 'samples/index.html', '../', nav='samples', stats=stats, site_codes=[(c, vocabs['body_site'].get(c) or c.replace('_', ' '), int(n)) for c, n in _sc.items()], age_cats=[c for c in age_cats if (cw.age_category == c).any()], iv_codes=iv_codes, iv_sample_codes=iv_sample_codes,
           hc_codes=list(hc_rows.index), countries=sorted(cw.country.dropna().unique().tolist()), n_cols=int(cw.shape[1]), has_col=has_col, ls_codes=ls_codes,
           year_min=(years[0] if years else None), year_max=(years[-1] if years else None), n_fields_max=len(CORE_FIELDS) + len(KEY_FIELDS),
           parquet_size=human((pkg / 'catalog_sample_metadata_wide.parquet').stat().st_size), det_size=human((pkg / 'catalog_sample_determinations.parquet').stat().st_size),
           crumbs=[dict(label='Home', href='../index.html'), dict(label='Samples')])

    # ---------- collections (config/collections.yaml) and Atlas › PCA (Sandpiper scores in the package) ----------
    sys.path.insert(0, str(HERE))
    from pages import collections as _collections, pca as _pca
    coll_stats = _collections.build(a.collections_config, render, cw, cs, out, hc_labels=hc_labels, ls_labels=ls_labels)
    print(f'[{time.time()-t0:.0f}s] collections: {coll_stats}', file=sys.stderr)
    from pages import atlas as _atlas
    atlas_dir = Path(a.atlas_data) if a.atlas_data else None
    if atlas_dir and (atlas_dir / 'taxa.json').exists():   # precomputed taxon-map payload + observation cards (data/inputs/gut/atlas; make atlas-precompute)
        _atlas.build(env, render, dict(atlas_data_dir=atlas_dir, stats=stats, gut_studies=cs, figures_dir=str(Path(__file__).resolve().parents[2] / 'data' / 'inputs' / 'site' / 'figures')), out)
        print(f'[{time.time()-t0:.0f}s] atlas/index + observations from {atlas_dir}', file=sys.stderr)
    if (pkg / 'catalog_sandpiper_pca_scores.parquet').exists():   # after the atlas copy (atlas.build replaces data/atlas/)
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
    hum = rg[rg.host_human.isin(['yes', 'mixed'])]
    funnel = [('registry studies (every ENA study reached by the enumeration)', len(rg)), ('host human = yes or mixed', len(hum)),
              ('… of them in the catalog', int(hum.study_accession.isin(inc_set).sum())),
              ('catalog projects (including projects found outside the registry slices)', len(cs))]
    not_in = hum[~hum.study_accession.isin(inc_set)]
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
    for r in authors_possible.sort_values(['author_key', 'study_accession'], kind='mergesort').itertuples(index=False):
        d = aidx.setdefault(r.author_key, dict(n=r.author_display, s=[], f=0))
        d.setdefault('p', [])
        if r.study_accession not in d['p']:
            d['p'].append(r.study_accession)
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
        d['notes_html'] = md_to_html(notes_by_release[d['release_id']], idp='n' + re.sub(r'[^0-9A-Za-z]+', '-', str(d['release_id']))) if d['release_id'] in notes_by_release else None
        d['changes_href'] = rspec['site_pages']['changes_release'].format(release_id=d['release_id']).split('/')[-1]
        d['current'] = d['release_id'] == release_id
        releases.append(d)
    # public history (config/releases.yaml public_history_from): the site lists releases from that release on; the Changes
    # pages of earlier releases redirect to the Changes index (those packages stay archived as GitHub / Zenodo releases)
    _pub_from = rspec.get('public_history_from')
    _ids = [d['release_id'] for d in releases]
    if _pub_from in _ids:
        _older = releases[:_ids.index(_pub_from)]
        releases = releases[_ids.index(_pub_from):]
        for d in _older:
            REDIRECTS[rspec['site_pages']['changes_release'].format(release_id=d['release_id'])] = 'index.html'
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
        dict(path='data/catalog_sample_metadata_wide.parquet', desc='Sample table loaded by the explorer (one row per catalog sample)', **dirstat('data', 'catalog_sample_metadata_wide.parquet')),
        dict(path='data/catalog_studies.parquet', desc='Study table loaded by the explorer', **dirstat('data', 'catalog_studies.parquet')),
        dict(path='data/catalog_sample_determinations.parquet', desc='Evidence rows (sample × field with verbatim quote)', **dirstat('data', 'catalog_sample_determinations.parquet')),
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
    groups = [('catalog', 'Catalog tables (every body site, DNA and RNA, all ages)'), ('registry', 'Registry tables (every screened study)'), ('release', 'Release documents'), ('infant', 'Infant extension tables (deep infant-field curation of the infant studies; also present in the catalog tables)')]
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
    # R2026.22 (owner request): how many catalog values each evidence source contributes — computed from the shipped current
    # determinations (one winning row per sample x field; precedence archive R1 > supplement R2 > full text R3 > abstract R4)
    sys.path.insert(0, str(repo_root / 'src')) if str(repo_root / 'src') not in sys.path else None
    from catalog.scopes.study_sources import classify_rows as _classify, LABELS as _SRC_LABELS
    _v = cd[cd.value_normalized.notna() & (cd.value_normalized.astype(str) != 'unknown')][['sample_key', 'study_accession', 'field_name', 'value_normalized', 'route', 'scope', 'determined_by', 'evidence_source', 'src_track']].copy()
    _v['source'] = _classify(_v).values
    _v = _v[_v.source != '']
    VS_ORDER = ['archive', 'supplement', 'fulltext', 'abstract', 'external', 'contribution']
    VS_COLOR = {'archive': '#565A5C', 'supplement': '#CFB87C', 'fulltext': '#4477AA', 'abstract': '#88CCEE', 'external': '#AA3377', 'contribution': '#228833'}
    VS_DESC = {'archive': 'BioSample attributes, sample names / titles and run fields in ENA / SRA / DDBJ (route R1)',
               'supplement': "supplementary tables and data files of the project's papers, and of other papers that tabulate its samples (route R2)",
               'fulltext': 'methods prose and tables in the body of open-access papers (route R3)',
               'abstract': "paper abstracts and the archive's project description (route R4)",
               'external': 'curated external resources such as curatedMetagenomicData and GMrepo (route R2, evidence_source external.*)',
               'contribution': 'metadata contributed by users through the Contribute form'}
    # Count FACTS, not columns (owner review 2026-10-10): related columns form one concept (country + region + locality + site +
    # latitude + longitude = one location; a code + its detail text = one value), identifiers are not metadata (subject_id is
    # reported apart), and a concept counts once per sample and source (a sample whose country comes from the archive and whose
    # recruitment site comes from the paper counts one location fact for each source).
    VS_CONCEPTS = [('Age', ['age_at_collection_days'], 'host'), ('Sex', ['sex'], 'host'), ('BMI', ['bmi'], 'host'),
                   ('Health condition', ['health_condition', 'health_condition_detail'], 'host'), ('Antibiotics', ['antibiotic_exposure'], 'host'),
                   ('Medication', ['medication', 'medication_detail'], 'host'), ('Intervention / arm', ['intervention', 'intervention_detail'], 'host'),
                   ('Diet', ['diet', 'diet_detail'], 'host'), ('Lifestyle', ['lifestyle', 'lifestyle_detail'], 'host'), ('Smoking', ['smoking_status'], 'host'),
                   ('Stool consistency', ['stool_consistency_bristol'], 'host'), ('Timepoint', ['timepoint_label'], 'design'),
                   ('Family membership', ['family_role', 'family_id'], 'design'), ('Shared setting', ['shared_setting_type', 'shared_setting_id'], 'design'),
                   ('Prematurity', ['preterm_status', 'gestational_age_weeks'], 'infant'), ('Birth weight', ['birth_weight_grams'], 'infant'),
                   ('Delivery mode', ['delivery_mode'], 'infant'), ('Feeding', ['feeding_mode', 'hmo_supplementation'], 'infant'),
                   ('Probiotics', ['probiotic_exposure'], 'infant'), ('Maternal antibiotics', ['maternal_antibiotics'], 'infant'), ('NEC', ['nec_status'], 'infant'),
                   ('Location', ['country', 'location_region', 'location_locality', 'location_site', 'latitude', 'longitude'], 'submission'),
                   ('Collection date', ['collection_date'], 'submission')]
    VS_IDENT = ['subject_id']
    f2c = {f: c for c, fl, _ in VS_CONCEPTS for f in fl}
    c_kind = {c: k for c, _, k in VS_CONCEPTS}
    _v['concept'] = _v.field_name.map(f2c)
    n_ident = _v[_v.field_name.isin(VS_IDENT)].groupby('source').size().to_dict()
    unmapped = sorted(set(_v.field_name) - set(f2c) - set(VS_IDENT))
    assert not unmapped, f'value_sources: fields without a concept {unmapped}'
    _f = _v[_v.concept.notna()]
    # one fact per (sample, concept, source); 'varies by sample' uses the concept's first column present for that fact
    _f = _f.assign(_ord=_f.field_name.map({f: k for _, fl, _ in VS_CONCEPTS for k, f in enumerate(fl)})).sort_values('_ord', kind='mergesort')
    facts = _f.drop_duplicates(['sample_key', 'concept', 'source'])[['sample_key', 'study_accession', 'concept', 'source', 'value_normalized', 'field_name']]
    facts['kind'] = facts.concept.map(c_kind)
    n_vals = len(facts)
    _nv = facts.groupby(['study_accession', 'concept', 'source']).value_normalized.transform('nunique')
    cohort = _nv <= 1
    sub = facts.kind == 'submission'
    n_vals_ns = int((~sub).sum())
    vs_rows = []
    for src_ in VS_ORDER:
        m = facts.source == src_
        mns = m & ~sub
        vs_rows.append(dict(source=src_, label=_SRC_LABELS.get(src_, src_), desc=VS_DESC[src_], color=VS_COLOR[src_], n=int(m.sum()), share=(m.sum() / n_vals) if n_vals else 0,
                            n_ns=int(mns.sum()), share_ns=(mns.sum() / n_vals_ns) if n_vals_ns else 0,
                            samples=int(facts.loc[m, 'sample_key'].nunique()), projects=int(facts.loc[m, 'study_accession'].nunique()),
                            cohort_share=float(cohort[m].mean()) if m.any() else 0.0, n_fields=int(facts.loc[m, 'concept'].nunique()), n_ident=int(n_ident.get(src_, 0))))
    nonarch = facts[(facts.source != 'archive') & ~sub]
    vs_extra = dict(n_values=n_vals, n_values_ns=n_vals_ns, n_rows=len(_v), n_samples=int(facts.sample_key.nunique()), n_samples_nonarchive=int(nonarch.sample_key.nunique()),
                    n_samples_total=len(cw), n_projects_nonarchive=int(nonarch.study_accession.nunique()), n_projects=len(cs), n_ident=int(sum(n_ident.values())),
                    n_concepts=len(VS_CONCEPTS))
    ftab = facts.groupby(['concept', 'source']).size().unstack(fill_value=0)
    nsamp = facts.groupby('concept').sample_key.nunique()
    vs_fields = []
    for tier in ('host', 'design', 'infant', 'submission'):
        for c, fl, k in VS_CONCEPTS:
            if k != tier or c not in ftab.index:
                continue
            row = ftab.loc[c]; tot = int(row.sum())
            segs = [dict(source=s_, label=_SRC_LABELS.get(s_, s_), color=VS_COLOR[s_], n=int(row.get(s_, 0)), pct=round(100 * row.get(s_, 0) / tot, 1)) for s_ in VS_ORDER if row.get(s_, 0) > 0]
            vs_fields.append(dict(field=c, cols=fl, tier=tier, total=tot, n_samples=int(nsamp.get(c, 0)), cov=int(nsamp.get(c, 0)) / len(cw) if len(cw) else 0, segs=segs))
    _col = lambda c_: cs[c_] if c_ in cs.columns else pd.Series([None] * len(cs), index=cs.index)
    _gen = int(((cd.field_name == 'subject_id') & cd.determined_by.astype(str).isin(['gut_subject_unique_v1', 'gut_subject_single_v1', 'gut_subject_single_case_v1'])).sum())
    dz_stats = dict(n_long=int(_col('longitudinal_status').eq('longitudinal').sum()), n_rep_unres=int(_col('longitudinal_status').eq('repeated_unresolved').sum()),
                    n_cross=int(_col('longitudinal_status').eq('cross_sectional').sum()), n_people_rep=int(pd.to_numeric(_col('n_subjects_repeated'), errors='coerce').fillna(0).sum()),
                    n_samples_rep=int(pd.to_numeric(_col('n_samples_repeated'), errors='coerce').fillna(0).sum()),
                    n_rel=int(_col('relatedness_designs').notna().sum()), n_fam=int(pd.to_numeric(_col('n_families'), errors='coerce').fillna(0).sum()),
                    n_mc=int(pd.to_numeric(_col('n_mother_child_families'), errors='coerce').fillna(0).sum()), n_set=int(pd.to_numeric(_col('n_shared_settings'), errors='coerce').fillna(0).sum()),
                    n_subj_gen=_gen, n_subj_real=int(cw.subject_id.notna().sum()) - _gen if 'subject_id' in cw.columns else 0,
                    axes=_col('time_axis').value_counts().to_dict(), rel_kinds=pd.Series([k_ for v_ in _col('relatedness_designs').dropna() for k_ in str(v_).split(';') if k_]).value_counts().to_dict())
    render('value_sources.html', 'about/value_sources.html', '../', nav='about', rows=vs_rows, extra=vs_extra, fields=vs_fields, dz=dz_stats)
    render('external.html', 'about/external.html', '../', nav='about', ext=ext_list, others=[r for r in src['related_efforts'] if r.get('ingestion') != 'used'],
           crumbs=[dict(label='Home', href='../index.html'), dict(label='About', href='index.html'), dict(label='External resources')])
    render('sources.html', 'about/sources.html', '../', nav='about', src=src, crumbs=[dict(label='Home', href='../index.html'), dict(label='About', href='index.html'), dict(label='Sources & acknowledgements')])
    render('accessibility.html', 'about/accessibility.html', '../', nav='about', crumbs=[dict(label='Home', href='../index.html'), dict(label='About', href='index.html'), dict(label='Accessibility, privacy & security')])
    import datetime as _dt
    (out / '.well-known').mkdir(exist_ok=True)
    _exp = (_dt.date.fromisoformat(str(build_date)[:10]) + _dt.timedelta(days=365)).isoformat()  # deterministic: build date + 1 year
    (out / '.well-known' / 'security.txt').write_text(f"Contact: mailto:{about['contact_email']}\nExpires: {_exp}T00:00:00Z\nPreferred-Languages: en\nCanonical: {base_url}.well-known/security.txt\nPolicy: {base_url}about/accessibility.html#security\n", encoding='utf-8')
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
        if r.assay not in ('shotgun_dna', 'mixed', 'other'):
            return f'not shotgun sequencing of a community ({r.assay})'
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
    # R2026.22: project pages of projects that left the catalog (retired gut_studies rows) become stubs pointing to the registry
    # record, so bookmarks and citations of e.g. studies/PRJEB39960.html keep resolving
    retired_ = sorted(set(cs_all.loc[cs_all[RR].notna(), 'study_accession']) - set(cs.study_accession)) if RR in cs_all.columns else []
    for acc_ in retired_:
        if (out / 'studies' / f'{acc_}.html').exists():
            continue
        tgt = f'../registry/index.html?study={acc_}'
        (out / 'studies' / f'{acc_}.html').write_text(f'<!DOCTYPE html><meta charset="utf-8"><meta name="robots" content="noindex"><meta http-equiv="refresh" content="3; url={tgt}"><title>{acc_} — not in the catalog</title><p>{acc_} is no longer a catalog project (it has no catalog samples: its samples are catalogued under another BioProject, were excluded, or could not be harvested). It remains in the <a href="{tgt}">registry</a>.</p>', encoding='utf-8')
    # R2026.31: project pages of the former all-body-sites section point to the one project page
    for acc_ in sorted(inc_set):
        (out / 'human' / 'projects').mkdir(parents=True, exist_ok=True)
        (out / 'human' / 'projects' / f'{acc_}.html').write_text(f'<!DOCTYPE html><meta charset="utf-8"><meta name="robots" content="noindex"><meta http-equiv="refresh" content="0; url=../../studies/{acc_}.html"><link rel="canonical" href="{base_url}studies/{acc_}.html"><title>moved</title><p><a href="../../studies/{acc_}.html">{acc_}</a></p>', encoding='utf-8')
    # items 5/6: old URLs keep working as redirects to their new homes
    for old_, new_ in REDIRECTS.items():
        (out / old_).parent.mkdir(parents=True, exist_ok=True)
        (out / old_).write_text(f'<!DOCTYPE html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={new_}"><link rel="canonical" href="{base_url}{posixpath.normpath(posixpath.join(posixpath.dirname(old_), new_))}"><title>moved</title><p>This page moved to <a href="{new_}">{new_}</a>.</p>', encoding='utf-8')
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
