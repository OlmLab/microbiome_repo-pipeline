"""Static API for LLMs and scripts + the "For LLMs & API" page + /llms.txt (R2026.16, owner review 2026-10-08).

GitHub Pages serves static files only, so the API is a set of small, predictable JSON / CSV files written at build time:

  api/index.json                        what exists, field definitions, how to search
  api/projects.json                     one record per catalog project (≈ 2 MB — scripts; LLMs should start from a slice)
  api/by-intervention/<code>.json       projects that administered <code> (e.g. probiotic, fmt)
  api/by-condition/<code>.json          projects with samples (or a trial population) of health condition <code>
  api/by-age/<category>.json            projects with samples of an age category
  api/vocabularies.json                 every code with its label (conditions, interventions, age categories, designs …)
  api/samples/<PRJ>.csv                 plain-text per-sample sheet of one project (key columns only; full columns in data/studies/)

Every project record carries the same keys, so an agent can filter a slice without reading documentation twice. llms.txt (root)
states the procedure in plain words with worked examples; llms/index.html is the human page with the copy-paste prompt.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

SAMPLE_COLS = ['sample_key', 'study_accession', 'subject_id', 'timepoint_label', 'age_category', 'age_at_collection_days', 'sex', 'country',
               'health_condition', 'health_condition_detail', 'intervention', 'intervention_detail', 'antibiotic_exposure', 'collection_date',
               'body_site_class', 'n_runs', 'seq_gbp']
DESIGNS = ['randomized_controlled_trial', 'non_randomized_controlled', 'crossover', 'single_arm_before_after', 'observational_with_procedure', 'none', 'unknown']


def _isnull(v):
    try:
        return v is None or pd.isna(v)
    except (TypeError, ValueError):
        return False


def _clean(v):
    if _isnull(v):
        return None
    if hasattr(v, 'item'):
        return v.item()
    return v


def _dump(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')), encoding='utf-8')


def project_records(studies, sidx_rows, cw, papers_by_study, base_url):
    """One dict per project with stable keys (documented in api/index.json)."""
    by_acc = {r['a']: r for r in sidx_rows}
    cnt = {}
    for col in ('health_condition', 'age_category', 'country', 'sex'):
        if col in cw.columns:
            cnt[col] = cw.dropna(subset=[col]).groupby(['study_accession', col]).size()
    arms = (cw.dropna(subset=['intervention']).assign(_c=lambda d: d.intervention.str.split(';')).explode('_c').groupby(['study_accession', '_c']).size()
            if 'intervention' in cw.columns else pd.Series(dtype=int))

    def counts(series, acc, drop=()):
        try:
            s = series.loc[acc]
        except KeyError:
            return {}
        return {str(k): int(v) for k, v in sorted(s.items(), key=lambda kv: (-kv[1], str(kv[0]))) if str(k) not in drop}

    out = []
    for s in studies:
        acc = s['study_accession']
        r = by_acc.get(acc, {})
        papers = [dict(pmid=p.get('pmid'), doi=p.get('doi'), pmcid=p.get('pmcid'), title=(p.get('title') or '')[:200]) for p in papers_by_study.get(acc, [])[:5]]
        out.append(dict(
            accession=acc, title=s.get('study_title') or '', n_samples=int(s.get('n_samples_curated') or 0),
            first_public_year=(str(s.get('first_public_min') or '')[:4] or None),
            gbp_per_sample_median=r.get('gs'),
            health_conditions=counts(cnt.get('health_condition', pd.Series(dtype=int)), acc),
            age_categories=counts(cnt.get('age_category', pd.Series(dtype=int)), acc),
            countries=counts(cnt.get('country', pd.Series(dtype=int)), acc),
            sex=counts(cnt.get('sex', pd.Series(dtype=int)), acc),
            interventions=[c for c in str(_clean(s.get('interventions')) or '').split(';') if c],
            intervention_design=_clean(s.get('intervention_design')) or 'unknown',
            intervention_detail=_clean(s.get('intervention_detail')),
            trial_population_condition=_clean(s.get('population_condition')),
            sample_intervention_arms=counts(arms, acc),
            has_cases_and_healthy_controls=bool(r.get('ccc')),
            has_treated_and_control_arms=bool(r.get('cca')),
            papers=papers,
            urls=dict(page=f'{base_url}studies/{acc}.html', samples_csv=f'{base_url}api/samples/{acc}.csv',
                      samples_full_csv_gz=f'{base_url}data/studies/{acc}.csv.gz', evidence_csv_gz=f'{base_url}data/studies/{acc}_determinations.csv.gz',
                      ena=f'https://www.ebi.ac.uk/ena/browser/view/{acc}'),
        ))
    return out


def build(render, out: Path, ctx: dict):
    """ctx: studies, sidx_rows, cw, papers_by_study, base_url, site_title, version, release_id, hc_labels, iv_voc, age_cats, stats, vocab_more"""
    base = ctx['base_url']
    recs = project_records(ctx['studies'], ctx['sidx_rows'], ctx['cw'], ctx['papers_by_study'], base)
    api = out / 'api'
    _dump(dict(release=ctx['release_id'], package_version=ctx['version'], n_projects=len(recs), projects=recs), api / 'projects.json')
    # slices
    iv_codes = [c for c in ctx['iv_voc'] if not (ctx['iv_voc'][c] or {}).get('sample_level_only')]
    slices = dict(intervention={}, condition={}, age={})
    for c in iv_codes:
        sel = [r for r in recs if c in r['interventions'] or c in r['sample_intervention_arms']]
        if sel:
            _dump(dict(release=ctx['release_id'], filter=dict(intervention=c), n_projects=len(sel), projects=sel), api / 'by-intervention' / f'{c}.json')
            slices['intervention'][c] = len(sel)
    hcs = sorted({k for r in recs for k in r['health_conditions']} | {r['trial_population_condition'] for r in recs if r['trial_population_condition']})
    for c in hcs:
        if c in ('unknown',):
            continue
        sel = [r for r in recs if c in r['health_conditions'] or r['trial_population_condition'] == c]
        _dump(dict(release=ctx['release_id'], filter=dict(health_condition=c), n_projects=len(sel), projects=sel), api / 'by-condition' / f'{c}.json')
        slices['condition'][c] = len(sel)
    for c in ctx['age_cats']:
        if c == 'unknown':
            continue
        sel = [r for r in recs if c in r['age_categories']]
        if sel:
            _dump(dict(release=ctx['release_id'], filter=dict(age_category=c), n_projects=len(sel), projects=sel), api / 'by-age' / f'{c}.json')
            slices['age'][c] = len(sel)
    # per-project plain CSV sample sheets
    cw = ctx['cw']
    cols = [c for c in SAMPLE_COLS if c in cw.columns]
    (api / 'samples').mkdir(parents=True, exist_ok=True)
    sub = cw[cols].copy()
    if 'age_category' in sub.columns:
        sub['age_category'] = sub['age_category'].where(sub['age_category'] != 'unknown')
    if 'seq_gbp' in sub.columns:
        sub['seq_gbp'] = pd.to_numeric(sub['seq_gbp'], errors='coerce').round(3)
    for acc, g in sub.sort_values(['study_accession', 'subject_id', 'timepoint_label', 'sample_key'], na_position='last', kind='mergesort').groupby('study_accession', sort=True):
        g.to_csv(api / 'samples' / f'{acc}.csv', index=False, lineterminator='\n')
    for r in recs:   # projects without samples still get a header-only sheet so no URL breaks
        p = api / 'samples' / f"{r['accession']}.csv"
        if not p.exists():
            sub.iloc[0:0].to_csv(p, index=False, lineterminator='\n')
    vocab = dict(health_condition={k: v for k, v in ctx['hc_labels'].items()},
                 intervention={c: (ctx['iv_voc'][c] or {}).get('label', c) for c in ctx['iv_voc']},
                 intervention_design=DESIGNS, age_category=[c for c in ctx['age_cats']], **(ctx.get('vocab_more') or {}))
    _dump(vocab, api / 'vocabularies.json')
    index = dict(
        name=ctx['site_title'], release=ctx['release_id'], package_version=ctx['version'], base_url=base,
        description='Evidence-linked metadata for public human gut shotgun metagenomes (all ages). A project = an INSDC BioProject; a sample = a BioSample (rarely a run).',
        how_to_search=[
            'Pick the most specific slice: api/by-intervention/<code>.json, api/by-condition/<code>.json or api/by-age/<category>.json (codes in api/vocabularies.json).',
            'Filter its projects on the other keys (health_conditions counts, interventions, intervention_design, has_cases_and_healthy_controls, has_treated_and_control_arms, n_samples, countries).',
            'For each candidate open urls.samples_csv to see the samples, their arms (intervention column), conditions and sequencing depth (seq_gbp).',
            'Cite the project page (urls.page) and the ENA accession; values carry evidence on the project page.'],
        endpoints=dict(projects=f'{base}api/projects.json', vocabularies=f'{base}api/vocabularies.json',
                       by_intervention={c: f'{base}api/by-intervention/{c}.json' for c in slices['intervention']},
                       by_condition={c: f'{base}api/by-condition/{c}.json' for c in slices['condition']},
                       by_age={c: f'{base}api/by-age/{c}.json' for c in slices['age']},
                       samples_csv=f'{base}api/samples/<ACCESSION>.csv', wide_parquet=f'{base}data/gut_sample_metadata_wide.parquet'),
        project_keys=dict(accession='BioProject accession', title='project title', n_samples='curated catalog samples',
                          gbp_per_sample_median='median sequencing depth per sample (Gbp)', health_conditions='code -> number of samples',
                          age_categories='life stage -> number of samples', countries='country -> number of samples', sex='sex -> number of samples',
                          interventions='interventions the study ADMINISTERED (study level, from abstract / description; [] = none stated)',
                          intervention_design='randomized_controlled_trial | non_randomized_controlled | crossover | single_arm_before_after | observational_with_procedure | none | unknown',
                          intervention_detail='short free text naming agent / regimen', trial_population_condition="underlying condition of the trial population",
                          sample_intervention_arms='code -> number of samples whose own archive record states the arm (placebo / no_intervention = controls / baselines)',
                          has_cases_and_healthy_controls='>= 3 healthy_control samples and >= 3 samples with a disease code',
                          has_treated_and_control_arms='>= 3 samples in an active arm and >= 3 placebo / no_intervention samples (archive attributes)',
                          papers='up to 5 linked papers', urls='page, samples_csv (plain CSV), samples_full_csv_gz, evidence_csv_gz, ena'),
        sample_columns={c: '' for c in cols},
        counts=dict(projects=len(recs), interventions=slices['intervention'], conditions=slices['condition'], age=slices['age']))
    _dump(index, api / 'index.json')
    txt = llms_txt(index, ctx)
    (out / 'llms.txt').write_text(txt, encoding='utf-8')
    prompt = (f"Help me find human gut metagenome datasets using Microbiome Repo. First read {base}llms.txt and follow it exactly: "
              f"fetch the JSON/CSV files it points to (do not guess or rely on memory), filter them for my question, and answer with a table of "
              f"BioProject accessions, sample counts, the relevant groups/arms and a link to each project page. My question: ")
    render('llms.html', 'llms/index.html', '../', nav='llms', prompt=prompt, index=index, llms_txt=txt,
           crumbs=[dict(label='Home', href='../index.html'), dict(label='For LLMs & API')])
    return dict(n_projects=len(recs), slices={k: len(v) for k, v in slices.items()})


def llms_txt(index, ctx):
    b = index['base_url']
    ivs = ', '.join(f'{c}' for c in index['endpoints']['by_intervention'])
    return f"""# {index['name']} — instructions for LLMs and agents
> Evidence-linked metadata for every public human gut shotgun metagenome (all ages): {index['counts']['projects']:,} projects (INSDC BioProjects), {ctx['stats'].get('n_samples', 0):,} samples. Release {index['release']} (package {index['package_version']}).

Use ONLY the files below; fetch them, do not guess accessions or counts. All files are plain JSON / CSV at stable URLs.

## How to search (do this in order)
1. Choose the most specific slice and fetch it:
   - by intervention the study administered: {b}api/by-intervention/<code>.json   (codes: {ivs})
   - by health condition: {b}api/by-condition/<code>.json   (codes in {b}api/vocabularies.json, e.g. ulcerative_colitis, crohns_disease, ibd_unspecified, colorectal_cancer, type2_diabetes, obesity, healthy_control)
   - by age category: {b}api/by-age/<neonate|infant|child|adolescent|adult|elderly>.json
   - everything (large, ~2 MB): {b}api/projects.json
2. Filter the slice's `projects` on the other keys: `health_conditions` (code -> samples), `interventions`, `intervention_design`, `trial_population_condition`,
   `sample_intervention_arms`, `has_cases_and_healthy_controls`, `has_treated_and_control_arms`, `n_samples`, `countries`, `age_categories`, `gbp_per_sample_median`.
3. For each candidate fetch `urls.samples_csv` ({b}api/samples/<ACCESSION>.csv): one row per sample with subject_id, timepoint_label, age_category,
   age_at_collection_days, sex, country, health_condition, intervention (this sample's arm), antibiotic_exposure, collection_date, n_runs, seq_gbp.
4. Answer with accessions, sample counts per group/arm, and links (`urls.page`, `urls.ena`). Say when a value is missing rather than inferring it.

## Meaning of the key fields
- `interventions` = what the investigators ADMINISTERED (FMT, probiotic, diet, drug …), study level, read from the abstract / BioProject description. [] = none stated.
- `sample_intervention_arms` / sample column `intervention` = the arm recorded in the archive for that sample; `placebo` and `no_intervention` are the controls / baselines.
- `health_conditions` counts each sample's own condition; `trial_population_condition` is the condition of a trial's participants (e.g. ulcerative_colitis for a UC FMT trial).
- An "IBD + FMT study" = by-intervention/fmt.json filtered to projects whose health_conditions or trial_population_condition include ulcerative_colitis, crohns_disease or ibd_unspecified.

## Worked examples
- "Probiotic trials where both treated and control samples have metagenomes": fetch {b}api/by-intervention/probiotic.json; keep projects with
  intervention_design in (randomized_controlled_trial, crossover, non_randomized_controlled) OR has_treated_and_control_arms = true. has_treated_and_control_arms = true
  means the archive labels each sample's arm (best case); for the others read the samples_csv (intervention, timepoint_label, subject_id) and the paper to split arms.
- "Ulcerative colitis FMT studies": fetch {b}api/by-intervention/fmt.json; keep trial_population_condition = ulcerative_colitis or health_conditions containing ulcerative_colitis.
- "Adult colorectal-cancer case-control cohorts with healthy controls": fetch {b}api/by-condition/colorectal_cancer.json; keep has_cases_and_healthy_controls = true and age_categories containing adult.
- "Infant gut samples from Finland": fetch {b}api/by-age/infant.json; keep countries containing Finland; then read samples_csv.

## For code-running agents
DuckDB can query the full per-sample table over HTTP:
  INSTALL httpfs; LOAD httpfs;
  SELECT study_accession, intervention, count(*) FROM read_parquet('{b}data/gut_sample_metadata_wide.parquet') WHERE health_condition IN ('ulcerative_colitis','crohns_disease') GROUP BY ALL;
Study-level columns (interventions, intervention_design, population_condition …): {b}data/gut_studies.parquet. Field definitions: {b}fields/index.html.

## Caveats
Values are evidence-linked claims (route R1 archive attribute > R2 supplementary table > R3 paper text > R4 abstract), not ground truth; study-level
interventions come from abstracts and may miss arms; every value's evidence is on the project page. Cite: {ctx.get('citation', '')}
"""
