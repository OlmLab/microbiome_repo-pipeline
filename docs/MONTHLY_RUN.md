# The monthly run

> **Frozen benchmark version — do not alter.** The owner benchmarks Microbiome Repo against hand-curated metadata at
> release **R2026.35 / package 1.29.1** (DOI 10.5281/zenodo.23938350; site build `0bf5ec2`, package generator `6d77bb5`,
> pipeline tag `benchmark-R2026.35`). Never re-tag or force-push `data-v1.29.1` or `site-v1.29.1`, and never edit or delete
> `~/catalog/frozen_releases/R2026.35_package_1.29.1/` (complete copy: published release files with checksums, the deployed
> site, the data repository, the pipeline source and history; see its `MANIFEST.md`). Later releases get new versions.

One command per stage, in order. Every stage is resumable: it skips what is already in the cache, so a stage that
stops halfway can simply be run again. Model calls are capped per stage and recorded in that stage's `ledger.csv`;
refusals are stored and never re-sent.

Set `L=~/catalog/cache/large_update` and run from the pipeline root.

| # | What | Command | Model cost |
|---|---|---|---|
| 1 | New studies since the last run | `python3 src/catalog/registry/enumerate_registry.py --out build/registry_delta_<date>/dna --slices S1 S2 S3 --since <last run>` and `--out …/rna --slices S4`; then `python scripts/monthly_intake.py select --delta build/registry_delta_<date> --tag <tag>` (new = not in the catalog, not an earlier candidate, not classified; kept = human signal + ambiguous) and `monthly_intake.py fold` (adds them to the `s4_enum` caches) | none |
| 2–4 | Read check, candidate papers, dossiers, classification batches | `python scripts/monthly_intake.py prep --tag <tag> --date <date>` (detached: `nohup … &`) | none |
| 5 | Classify (two readings) | sub-agents running `scripts/pilot_passes.py --passes classify` | ≈ $0.03 / project |
| 6 | Merge the readings | `python scripts/merge_classify.py $L/stage1/delta_out $L/stage1/delta_merged.parquet $L/stage1/delta_adjudicate_ids.txt` | none |
| 7 | Tie-break the disagreements | `scripts/adjudicate_classify.py` (role `adjudicate`, Opus 5) | ≈ $0.07 / project |
| 8 | Final classification table | `python scripts/finalise_classify.py $L/stage1/delta_merged.parquet $L/stage1/delta_adj $L/stage1/delta_final.parquet` | none |
| 9 | Paper link audit and full-text reading | sub-agents running `scripts/pilot_passes.py --passes link_audit,fulltext` | ≈ $0.05 / project |
| 10 | Harvest BioSample attributes (R2026.34: `monthly_intake.py samples --tag <tag>` after classification: sample stage `stage16_delta_samples_<tag>`, harvest with an EBI BioSamples fallback for samples released in the last day, fielding, new pairs) | `python scripts/harvest_biosamples.py --samples <new samples>.csv --out $L/stageN_attrs` | none |
| 11 | Map any attribute values not already in the map | `scripts/map_attribute_pairs.py` | ≈ $0.0004 / value |
| 12 | Subject and timepoint | `python scripts/derive_subject_timepoint.py $L/new_biosample_attributes.parquet $L/stage5/subject_timepoint.parquet` | none |
| 13 | Supplementary tables of the new papers | `join_ladder_audit.py` → `extract_supp_rows.py` → `classify_supp_columns.py` → `map_attribute_pairs.py` → `assemble_supp_values.py` | ≈ $0.01 / project |
| 14 | Per-sample values | `python scripts/assemble_samples_stage5.py $L` (new projects); `python scripts/assemble_rna_specimens.py $L <stage dir>` for samples harvested outside stage 5 (RNA-only samples of earlier projects, projects admitted without samples) | none |
| 15 | Summarise the paper passes | `python scripts/summarise_paper_passes.py --out-dirs <pass out dirs> --out-prefix <dir>/ --merge-into <previous three parquets>` | none |
| 16 | Release check: recall by exclusion reason | `scripts/blind_audit_scope.py` on a 40-project sample of EACH `out_reason`, then a full recheck only of the reasons whose error rate is material | ≈ $0.03 / project |
| 17 | Apply recoveries | flip the recovered projects in `classify_all.parquet`, then repeat steps 10–15 for them | none |
| 17b | Study-level passes for the newly admitted projects (R3 / R4 cohort statements, interventions, study design, generated subjects) | sub-agents running `scripts/catchup_passes.py --shard K --nshards N --budget 4 --projects <new projects>` (two readings per project of the generating paper's full text, else its abstracts, plus the archive record), then `python scripts/catchup_passes.py --merge` | ≈ $0.05 / project |
| 17b+ | Verify and filter the catch-up outputs before the build | an Opus 5 audit of a random sample; then `scripts/verify_catchup_rows.py --shard K --nshards 8` (sub-agents; a third reading, role `adjudicate`, of every committed statement and kinship call against the passage around its quote) → `--apply`; then `python scripts/apply_catchup_filters.py` (generated subjects only where stated participants ≥ specimens; study-level intervention codes only in trial designs) | ≈ $0.02 / statement |
| 17c | Credit the per-sample passes the large-update stages ran | `python scripts/write_large_update_pass_log.py --projects <new projects>` → `data/inputs/gut/large_update_pass_log.csv` (read by `check_pass_coverage.py`) | none |
| 17d | Study-design reading (design type, one-sentence design statement, compared groups, per-sample comparison_group) for projects without a design statement, projects whose paper compares groups the samples do not separate, and trials whose arms are not per sample | targets from the built site's design checks → `data/inputs/gut/design/design_targets_<release>.csv`; sub-agents running `scripts/design_passes.py --projects <targets> --shard K --nshards 48 --budget 5 --verify-budget 4` (two Sonnet-class readings + an Opus 5 check of every proposed value → group map), then `scripts/design_passes.py --merge`, `scripts/design_statement_check.py --shard K --nshards 12` (Opus 5 check of every design statement) and `--apply` → `design/design_reading_all.parquet` (read by build_gut_scope) and `deep/deep_determinations_shard_GRPD*.parquet` (per-sample rows) | pilot with an Opus 5 audit of every assigned project before the full run (R2026.33 pilot 2: 1,082 of 1,086 assigned specimens correct) |
| 17d+ | Re-run of the design reading on the projects whose readings disagreed (R2026.34: with an Opus 5 tie-break of the design type) | sub-agents running `scripts/design_passes.py --tag V<n>_ …`, then `python scripts/merge_design_rerun.py --targets <csv> --tag V<n>_` and `scripts/design_statement_check.py --projects <csv> --tag V<n>_` (sub-agents) → `--apply` | ≈ $0.05 / project |
| 17e | Who and when: person and time-point variables of repeated-sampling projects whose samples show no repeated person, or whose repeated persons have no time axis | `python scripts/persontime_passes.py --make-targets build/persontime_targets.csv`; sub-agents running `--targets … --shard K --nshards N` (≤ 60 projects per sub-agent: the per-session model token ceiling); write to a staging `--out` (e.g. `~/catalog/cache/persontime_prod`); an Opus 5 audit of ~30 written projects; then `python scripts/apply_persontime_filters.py --stage <staging>` (F1 age axis without repeats, F2 signed codes merged with unsigned twins, F3 the audit's withhold list `design/persontime_withhold.csv`) copies the outputs into `deep/deep_determinations_shard_PTD*` (subject_id, timepoint_label) and `timing/timing_samples_pt_shard_*` (day offsets, read by the build beside `timing_samples.parquet`) | ≈ $0.05 / project |
| 18 | Inputs of the catalog build | `PYTHONPATH=src python scripts/prepare_catalog_inputs.py --extra-facts-dirs <summarised pass prefixes>` → `data/inputs/catalog/` (study rows, samples, determinations, paper facts, exposure items, papers, short descriptions, `runs_source.parquet` with `run_origin`) | none |
| 18b | Short descriptions of projects that have none | sub-agents running `scripts/write_short_descriptions.py` (rubric tier; refused projects once more with `SHORT_DESC_ROLE=adjudicate`) | ≈ $0.01 / project |
| 18c | Community profiles (R2026.36: rebuilt every run for the current samples, not extended) | `PYTHONPATH=src python scripts/refresh_sandpiper.py --bulk <sandpiperX.gtdb.csv.gz> --per-acc <sandpiperX.per_acc_summary.csv.gz> --version <X> --record <Zenodo record> --catalog-dir <catalog build> --work <work dir> --out data/inputs/catalog/sandpiper --previous data/inputs/catalog/sandpiper --registry-runs data/inputs/registry/registry_runs_sandpiper.parquet` (≈ 5 min after a ≈ 1 min bulk pass; converts the unfilled coverage of Sandpiper ≥ 2.0.1; DNA runs only; QC flags from the per-run summary for every sample; then re-run the gut build so catalog_runs.sandpiper_profiled follows the new flags) | none |
| 18d | Package, site, verify | `make release` → `make measures` (profile measures) → the Insights payloads, see "Insights pages (R2026.42)" below → `make site` → `make verify` → `make side-page-tests` (now also runs the release integrity checks, `catalog.release.integrity_checks`, on current rows only, and `check_file_types.py`, which rejects anything the CU server would refuse) (fails when the site passes 950 MB — GitHub Pages refuses sites above 1 GB) | none |
| 18e | Visual and wording review | an Opus 5 sub-agent renders the system pages and a spread of project pages (one per body site, RNA, DNA + RNA) and reports by page and element only | ≈ $5 |
| 19 | Publish | `make check-credential`, `make publish-branch`, copy the pipeline's `.github/workflows/release.yml` into the data clone and `check-cu-deploy.yml` (+ `deploy-pages.yml`, `verify.yml`) into the site clone, amend, re-tag locally, then push the pipeline `main` FIRST (the site repo's verify fetches `check_links.py` from it; pushed after the site tag, R2026.37's verify ran the old checker and failed), then the two release branches, the two tags and the `release-assets` branch. Within ≈ 20 min https://microbiome-repo.colorado.edu/version.json must show the new `site-v` tag (the site repo's check-cu-deploy workflow fails otherwise); record the Zenodo DOI in `config/releases.yaml` | none |

There is one catalog build (`gut-build`, called by `release`): it reads the original study list, the projects admitted by
the scope classification and every sample / determination / run input from `data/inputs/catalog/`, applies the run-level
scope (amplicon and single-cell runs out; refetched runs of earlier projects only when they are RNA libraries of a catalogued
sample or the only data set of a catalogued sample), and writes the `catalog_*` tables with their bitemporal release columns. `scripts/public_history.py` (also called
by `release`) keeps the package's release notes and changelog to the releases from `public_history_from` in
`config/releases.yaml`.

## Passes added by the deep pass (R2026.40)

Run these after step 17e and before step 18 for the projects a month touches; each is resumable and keeps its own ledger.

| What | Command | Rule |
|---|---|---|
| Paper search for projects without an own-data paper | `scripts/find_papers.py` (accession search in Europe PMC full text, then title / author search) → candidates | a candidate is not a link |
| Full-text link audit and scope check | sub-agents running `scripts/link_audit_fulltext.py`, then the scope check, then `scripts/merge_link_audit_fulltext.py --audit-out <dir> --pairs <pairs.parquet>` | only pairs read as *generated the data* with scope *whole deposit* are linked; *part* and *reuse* are listed, not linked |
| Duplicate papers | done inside `scripts/prepare_catalog_inputs.py` (paper rows are de-duplicated by project and paper id) | one row per (project, paper) |
| Methods re-read (extraction kit, library kit, instrument, sequencing facility, sampling period, lifestyle, exposure items) | sub-agents running `scripts/reread_passes.py`; audit sample with `scripts/reread_audit_context.py`; `scripts/build_reread_methods.py` builds the controlled names (`config/methods_names/*_map.csv`) | lifestyle statements are committed only for audit-confirmed kinds; a re-read kit wording never changes a kit-rule outcome that earlier wording set |
| Exposure items check (**mandatory**) | sub-agents running `scripts/exposure_item_check.py` | every exposure item is checked by Opus 5 against the passage before it is committed; nothing is committed on one reading |
| Study design for newly linked papers | `scripts/design_passes.py --run-fields`, then `scripts/merge_design_rerun.py` (targets only) | a re-run that loses per-sample groups keeps the earlier result |
| Verification of catch-up statements (**mandatory before the build**) | sub-agents running `scripts/verify_catchup_rows.py --tag <tag>` on every newly written cohort statement (R3, R4, healthy-control, relatedness) | unverified statements are not built; medication cohort statements are not written (1 of 3 right in the R2026.40 audit) |
| Condition labels that do not say what the samples are | `scripts/condition_string_context.py` (context for two independent Opus 5 readings per (project, label)) → checked corrections (`dq_adjudication_v2:<release>`) | written only when both readings agree on role and code, each with a verbatim quote |
| Case / control labels from archive fields (label hunt) | sub-agents with `scripts/label_hunt_context.py` propose a rule per project; second readers verify; decisions in `docs/audits/<release>/label_hunt_decisions.csv`; `scripts/build_dq_corrections_r41.py` writes the checked corrections | the meaning of a value or name pattern must be stated by the project's own text, or fixed exactly and uniquely by stated group sizes; every proposal is re-derived by a second reader |
| Metadata tables in repositories named in data-availability statements | `scripts/harvest_repo_metadata.py --links <da_links.csv> --out links.csv --pmcids pmcids.csv`, then the supplementary-table chain of step 13 on the `REPO_*` packages, a whole-column check of health, age and sex columns, a spot check | values enter as route R2 through a deep-input shard; the archive cross-check and the column check are mandatory |
| Unusual library strategies | `enumerate_registry.py --slices S5` with the monthly intake | admit only after the read check; without it, only projects whose median run has at least 0.5 Gbp |
| Sandpiper gap list | `scripts/sandpiper_gap.py` | report only |
| Profile measures | `make measures` (`scripts/sandpiper_measures.py` builds the genus matrix out of core) | about 35 minutes under load |
| Release notices | `docs/release_notices/<release>.md` is printed in the release notes under "Notices" | used for the replication plan hash in R2026.40 |

The explorers can load DuckDB-WASM from the site itself (`docs/DUCKDB_SELF_HOSTING.md`); the switch `hosting.duckdb_self_hosted`
in `config/site.yaml` stays off until the CU server is confirmed to serve `.wasm` files.

## Insights pages (R2026.42)

Three pages are built from payloads under `data/inputs/site/`; without a payload a page renders a placeholder, so a build never
breaks on them. Their non-release inputs (GTDB R232 trees, 428 literature summaries, method text, published lists, the
replication run) stay in `~/catalog/projects/side_pages` and `~/catalog/projects/replication` (`SIDE_PAGES`, `REPLICATION` in
the Makefile).

| Page | Command | Cost |
|---|---|---|
| VANISH / BloSSUM (`atlas/vanish.html`) | `make vanish` on the built package (about 11 h at 6 jobs, 3 GB, resumable, no model cost), then `make vanish-page VANISH_CALIBRATION="<which threshold relaxation was adopted, or that none was>"`, which also computes the numbers of the six "how to read these lists" sentences and the three exploratory checks of the Robustness section (`scripts/vanish_robustness.py`, `docs/VANISH_ROBUSTNESS.md`; answer to Ibata et al., bioRxiv 2026.11.17.767315; no class or threshold depends on them) | none |
| Taxon explorer (`atlas/taxa/index.html`) | `make taxa` after `make vanish` (it reads the classes from `results.csv`; about 70 s, 3.6 GB) | none |
| Taxon map (`atlas/index.html`) | `make taxon-map` after `make release` and before `make measures`, `make taxa` and `make site` (about 40 s, 3 GB; `scripts/build_taxon_map.py`). **Every release** (R2026.43): until then the map kept showing the page data of September 2026 | none |
| Profile coverage and quality (`atlas/measures.html`) | `make measures` after `make taxon-map` (it reads `countries.json`) | none |
| Replication of published findings (`atlas/replication.html`) | `make replication-page` (3 s; checks the payload against the checksums of the Replication chat). The test is **not** repeated: its results belong to package 1.34.0 | none |

- Rerun `make vanish` and `make taxa` when the samples, the profiles or `country_hdi.csv` change; a release whose data tables
  are unchanged can reuse the payloads (the pages print the package they were computed on).
- Size: the explorer's per-bar sample lists (`data/taxa/presence/`) are about 24 MB and grow with the number of profiled
  samples. When `make verify` reports more than 950 MB, rebuild with `make taxa TAXA_FLAGS=--skip-presence` (the bars then link
  to the sample table by group) and say so in the changelog. R2026.42 fits with the lists.
- `make side-page-tests` runs the three page tests on the built site (jsdom from `$(SIDE_PAGES)/work/node`).
- Menu and naming (owner, 2026-11-23): the section is "Insights"; the word "Atlas" is not shown to visitors; addresses stay
  under `atlas/`. Since R2026.43 (owner, 2026-11-24) `atlas/measures.html` is in the menu as "Profile coverage and quality";
  `atlas/figures.html` and `atlas/observations.html` are gone and redirect to the Taxon explorer.
- The Taxon explorer, the Taxon map and Profile coverage and quality print the release and package their page data were
  computed from: rerun `make taxon-map`, `make measures` and `make taxa` on the built package of every release, also when
  the data did not change, so that the stamp is the release being published.

## Rules that hold every month

* **One request per paper**; a worker reads at most 25 papers, or 10 when the projects concern pathogens or infection.
* **Agents never see paper text** in their own transcripts — they run scripts and report counts.
* **Tie-breaks and audits run on Opus 5**, pinned in `config/models.yaml`; the newer Opus refuses pathogen projects.
* **Refusals are recorded, never re-sent.** A project where both readings refused keeps archive metadata only and is
  listed in the release notes.
* **A stage stops at its own budget.** If a stage runs more than 30 % over its estimate, halt and re-estimate.
* **The frame token ceiling** (2 M per session) is the practical limit on running model passes from the main session:
  anything larger is dispatched to sub-agents, one shard each.
* **Amplicon and single-cell data sets are dropped at RUN level**, not project level: a project may deposit a 16S set
  beside a shotgun set. The read check overrides the archive label. A specimen left with no in-scope data set leaves
  the catalog, and a project left with no specimen is marked `no_data_set_in_scope` rather than published empty.
* **Projects on the original study list that fail the old gut-DNA rule are classified like new projects** (stage 14 in
  R2026.31: 124 classified, 107 admitted); check the study-rule drop list each month for new ones.
* **Pass coverage is a release gate.** A project that entered a scope after that scope's annotation passes ran must be
  recorded in `config/pass_coverage_exceptions.csv` with a reason, and run first next month.
  Stage 17b runs those passes for them; after it, only genuinely unrunnable project-passes (both readings refused, no
  text of any kind) stay in the exceptions file.

* **The project badge describes the catalogued data sets.** DNA and RNA runs in the archive make a project "DNA and RNA";
  a paper describing both while only one kind was deposited gives the deposited kind.
* **Memory.** Never load a package table whole; filter by project or read columns. A main session stays under 6 GB, a
  sub-agent under 3 GB, and `memory_pressure` must show at least 35 % free before a build, a heavy cell or a wave of sub-agents
  (the machine was force-quit on 12 November 2026 when several chats ran builds at once).
* **Do not run two heavy jobs at once on the local machine.** Rendering very large pages (the project list, the scope
  page) while the catalog build runs slowed the build from about 40 minutes to almost 4 hours.

## Next run: carried over from R2026.43

R2026.43 / 1.34.2 is a site release (website review applied; data tables unchanged). It was built in a chat workspace like
R2026.42; the section "Next run: carried over from R2026.42" below still describes what `~/catalog` lacks, with these changes:

- **Previous package for the next `make release`** is `data_package_v1.34.2.zip` (GitHub release `data-v1.34.2`, Zenodo, session
  file of the Editor chat).
- **Order of the page-data steps** after `make release`: `make taxon-map` → `make measures` → `make vanish-page` (only when the
  VANISH run or its page texts changed) → `make taxa` → `make site` → `make verify` → `make side-page-tests`. The Taxon map
  page data are now rebuilt in every release (they had not been since September 2026).
- **Issue forms.** The site repository's `main` branch holds the forms GitHub shows. `make install-workflows` no longer installs
  `catalog-finding.yml` and `catalog-contribution.yml` (no page links to them); after a change to
  `site_generator/gen/issue_templates/*.yml`, run `make install-workflows`, commit and push `main` of the site repository
  before `make publish-branch`.

- **Browser test in Actions.** `verify.yml` runs a Playwright test that looks for visible text on the built pages. A change of
  wording on the sample explorer or the registry page can fail it after the tag is pushed (R2026.43: the first text matching
  "evidence" had moved into a collapsed box). The fix then goes to `main` of the pipeline and of the site repository, and the
  deployment is started by hand: Actions → deploy-pages → Run workflow on `main` with `ref` = the site tag (no tag is moved).
  The CU server takes the tag by itself and does not wait for Actions.
- **Form parsers.** `scripts/ingest_verifications.py` and `src/catalog/ingest_issues.py` match the labels and options of the
  issue forms; `tests/test_ingest_verifications.py` and `tests/test_simple_form_labels.py` fail when a form is reworded
  without them.

Left for the next **data** release by the website review (each is a value or a rule in the data, not wording):

| Item | What |
|---|---|
| Owner's decision 8 | map free-text health conditions to vocabulary codes (3,603 samples at the time of the review); `api/by-condition/` then gains the files that were removed as free text |
| F405 | the summary of PRJEB55648 (`short_description`) promises four body sites; the catalog holds stool only |
| F413 | `reason_label` values in `catalog_sandpiper_unprofiled.parquet` are worded for the pipeline, not for a reader |
| F069 | the Amazonian Amerindian collection's lifestyle filter leaves out the 226 Venezuelan samples of PRJEB55648 (changes membership counts: owner) |
| F170 | `release_notes.py`: a release with an empty diff should say so in one sentence instead of tables of `+0`; its "Sandpiper snapshot" section still describes the retired infant-phase join (2.0.0, 79,473 samples) and is cut at render time on Downloads (`NOTE_SECTIONS_DROPPED` in `build_site.py`) — check that heading match after the next build |
| F608 | 4,814,518 current values in `catalog_sample_determinations` against 4,814,208 filled cells counted from the wide table: explain or remove the 310 |
| `scope == 'study_all'` | `build_site.py` tests this value in three places (project-page group rows, Fields, External resources); the column holds `sample`, `group`, `subject`, `biosample`, so the tests never match. Decide whether `group` is meant and what the project page should then show |
| F496 | FMT and other intervention counts: the Samples page counts projects whose `interventions` carries the code (151 for FMT), the Projects sheet also counts projects with the code on a sample arm (161); the page says so, one definition would be better |
| QC rule | the Taxon map keeps low-complexity profiles (six flags, 367,485 samples), the Taxon explorer and Profile coverage and quality drop them (seven flags, 379,851; the explorer also drops non-specimens, 379,776). Each page states its rule; decide whether to use one |
| F056, F406, F087 | cohort page link into the sample explorer (needs a list parameter); relationship diagrams label unknown roles `unknown`; half of `config/contribute.yaml` duplicates what `build_worklist.py` reads |
| `audit/schema.json` | the description of the withdrawn finding form still names the old product |

## Next run: carried over from R2026.42

**Start state.** R2026.42 was built in a chat workspace because `~/catalog` was read-only to code (see "When `~/catalog` is
read-only to code" below). GitHub holds everything that is tracked. What is not tracked and is newer than `~/catalog`:

| What | Where the current copy is | In `~/catalog` |
|---|---|---|
| Previous package for the next `make release` (`PKG_ZIP`) | `data_package_v1.34.1.zip`: GitHub release `data-v1.34.1`, Zenodo 10.5281/zenodo.24508002, session file of the Editor chat (sha256 `af9c3a64853290c49ff7a4fb3207f84b841516691a9518082c80e2ecfa23c51d`) | `build/` ends at `data_package_v1.34.0.zip` |
| Page data of the Insights pages and the profile measures (`data/inputs/site/{taxa,vanish,replication,measures}`), `data/inputs/gut/atlas/observations.json` with the renamed map reference | session file `site_inputs_R2026.42.tar.gz` (untar inside `data/inputs/`) | `site/taxa`, `site/vanish`, `site/replication` absent; `site/measures` and `observations.json` are those of R2026.41 |
| VANISH/BloSSUM run on 1.34.0 with its permutation cache (`VANISH_OUT`, default `data/inputs/site/vanish_work`) | session file `vanish_run_1.34.0_full.tar.gz` (its `vanish_out/` is the run directory; `country_hdi_used.csv` is the HDI table of the run) | absent |
| `data/inputs/catalog/sandpiper/` | rebuilt by every `make release`; the R2026.42 tables differ from those in `~/catalog` only in the last decimal places | R2026.41 tables (usable as `--previous`) |
| `build/package`, `build/site` | not kept (rebuilt by `make release`, `make site`) | R2026.41 |

All other input tables under `data/inputs/` are unchanged since R2026.41: R2026.42 added no data.

* **Not checked in a browser.** The three new pages passed their scripted page tests only. After the owner has looked at them,
  apply what he reports.
* **VANISH / BloSSUM, long checks not done** (listed on the page): a detection-threshold sweep, a hunter-gatherer-only
  contrast, a rerun without samples from subjects with a recorded condition. The quick checks on the page (`docs/VANISH_ROBUSTNESS.md`)
  show that many classes do not hold in every lifestyle, that the simple rule compares studies with each other (7 of 655 adult
  cohorts hold both HDI bands) and that it moves with sequencing depth. Whether the lists stay as they are is the owner's call.
* **Second implementation** of the VANISH/BloSSUM method was compared on package 1.31.0 only; the page says so.
* **Build defects found by comparing 1.34.1 with 1.34.0 table by table** (no data value is wrong; fix before the next data release):
  1. `catalog_rejected_values` and `catalog_sample_determinations_conflicts`: most rows get the current release in
     `release_added` / `package_added` at every build (2,680 of 3,081 and 6,072 of 184,012 rows in 1.34.1).
  2. 320 health-condition rows whose IBD code was refined carry the current release id in `parse_note` ("R2026.42; was
     ibd_unspecified"): the note should name the release in which the refinement was first made.
  3. One project note in `study_metadata_wide` grows by a repeated "auditor_review … excluded→excluded" line at every build.
  4. The profile tables are not bit-reproducible between builds (differences up to 4e-8).
* `scripts/vanish_blossum.py` in the pipeline differs from the Side pages chat's copy in the HDI note of the page stage only
  (a full run reads the release's own HDI table); the analysis stages are identical to the file that was run.
* The explorer's `data_check.py` compares against the round-2 per-taxon pages of package 1.31.0 (`--out721`); on a newer
  package those three comparisons fail by design (8,752 of them on 1.34.0) while every structural check passes. Use
  `reader_check.mjs` (91,647 pass) and `presence_check.mjs` (75,523 pass) as the data tests.
* GitHub Pages: `deploy-pages` can fail with "No artifacts named github-pages" right after the upload (it did for R2026.42). A
  re-run of the failed job then finds two artifacts and fails again: delete the run's `github-pages` artifacts, then re-run.
* Everything in "Next run: carried over from R2026.41" below still stands.

### When `~/catalog` is read-only to code

Since 23 November 2026 the chat sandbox makes `~/catalog` read-only to cells (also `.git`), and git works only in
`<workspace>/repos/<name>`. R2026.42 was made like this, and it works:

1. `git clone` the three repositories from GitHub into `<workspace>/repos/` (site and data clones with `--depth 1`; then
   `git remote set-branches origin main` and `git fetch --depth 1 origin`, because `make publish-branch` starts from `origin/main`).
2. Copy the untracked inputs into the pipeline clone: `cp -Rc ~/catalog/catalog-pipeline/data ~/catalog/catalog-pipeline/build`
   (copy-on-write, seconds), then restore the session files of the table above over `data/inputs/` and put the previous package
   zip into `build/`.
3. Build as usual. Publish with `make publish-branch SITE_CLONE=<ws>/repos/infant-gut-catalog DATA_CLONE=<ws>/repos/infant-gut-catalog-data
   ASSETS_WT=<ws>/repos/release_assets_<version>` (set `git config user.name/user.email` in the two clones first), then the
   workflow copy, amend, re-tag and the pushes as in step 19.
4. The publish step raises an approval request and waits for the owner; it expires after six hours. Publish when he is at the
   machine. Keep the session active until then (a long idle session loses its workspace): save the package zip, the page
   data and a `git bundle` of the pipeline commit as session files as soon as the build is verified.
5. Afterwards the owner brings his local clones up to date in his own terminal (`git fetch` and `git reset --hard origin/main`
   in the pipeline; the site and data clones are only publishing areas). Files can be put into `~/catalog` one at a time with
   `host.save_to_folder`; it refuses JSON, YAML, `Makefile`, dot-files, archives and `.bin`.

## Next run: carried over from R2026.41

* Read check: `scripts/read_check16s.py` needs `minimap2` in the environment. It was missing from 1 November to 14 November 2026
  and every run was recorded as `error`; the intake now warns. After an intake, confirm that most runs have a verdict.
* Slice S5 (`enumerate_registry.py --slices S5`): two projects wait for a decision (`large_update/r41b_pending_read_check.txt`:
  PRJNA1079578, PRJNA950479). PRJEB128068 (admitted in R2026.34) has one amplicon run among 20 checked.
* Label hunt (`scripts/label_hunt_context.py`, decisions in `docs/audits/r41/`): six projects whose labels were accepted but whose
  detail wording was not (PRJNA297252, PRJNA1368374, PRJNA728888, PRJEB73590, PRJNA401385, PRJNA637785); 133 projects without a
  basis in archive fields need a supplementary table or the authors.
* Repository metadata (`scripts/harvest_repo_metadata.py`, stage 19): integrate the checked values; 15 figshare items could not
  be downloaded (storage hosts outside the network allowlist).
* Person and time pass for the 36 projects admitted in R2026.41.
* A catch-up merge rebuilds the merged statement tables from the shards: anything removed by hand must be removed in the shards
  (as done for the medication statements of R2026.40), or it returns with the next merge.

## Next run: carried over from R2026.34

* First: the Sandpiper 2.0.1 refresh and the Sandpiper table re-derivation against current samples (6,235 rows of retired samples
  to drop), then the analyses and the benchmark changes in `docs/SANDPIPER_ANALYSES_PLAN.md` (sections 1–3). Every integrity
  count must use current rows only (`release_retired` null): the wide table also holds 54,709 retired rows.
* Probiotics: filter I2 should keep administered products in single-arm designs (22 missed tags in 1.29.0; see 1.29.1 changelog).

* The R2026.31 catch-up list (`config/pass_coverage_exceptions.csv`): 18 project-passes in 6 projects remain (both
  readings refused). 4 projects have no short description and 4 have unresolved DNA / RNA labels.
* Person and time (after R2026.34): 402 projects still "repeated sampling, not resolved" and 554 longitudinal projects whose
  repeatedly sampled people have no time axis. Run `scripts/persontime_passes.py` on newly admitted projects and on the
  439 targets with no person / time variable only if their dossiers have gained sample variables. Recall items the
  production audit found and that were not built yet: (1) numeric twin of a coded time variable (offsets from a numeric
  column when the label column is coded; PRJNA675301); (2) cleaned numeric split parts (`D07` → 7; PRJNA1185841);
  (3) label normalisation across spellings of the same visit (PRJNA1078731); (4) constant tokens inside person ids
  (`S01_stool` vs `S01_saliva`); (5) archive attribute names as a hint for the time variable (PRJNA385009); (6) a gate
  for collection dates used as time axes when the source states study days; (7) aligning a stated visit schedule
  (weeks 0, 4, 12) with ordinal codes 1, 2, 3 — Opus check required for every alignment.
* Follow-up over 20 years (found while adding the project sheet's sort buttons): 21 projects, which the sheet does not rank
  ("?") — PRJEB55648, PRJEB38909, PRJNA1169096, PRJEB50408, PRJEB27661, PRJEB74322, PRJEB83236, PRJNA419524, PRJEB42155,
  PRJEB101630, PRJEB59450, PRJEB1220, PRJEB29373, PRJEB22863, PRJNA1300398, PRJEB39813 (age axis) and PRJEB91718,
  PRJNA928758, PRJNA785545, PRJEB32762, PRJNA1031616 (date axis). Likely causes: subject ids that group different people,
  ages misread (PRJEB38909: `age_of_first_use_tos`), placeholder dates. Give them to an Opus check of subject ids and
  time values, then add a plausibility gate (follow-up ≤ 20 years unless the source states it) in the timing build.
* Study design: 12 projects still carry "two readings differed"; ~1,015 projects whose source compares groups that no
  sample variable separates and ~200 trials whose arms are not per sample remain (R2026.33 page checks).
* Machine pauses: the final build in R2026.34 stopped for hours while the machine slept; the build is restartable
  (`SKIP_UNPACK=1`). Keep heavy passes and builds sequential.

## What to watch

* `classify_all.parquet` status counts — a jump in `refused` means a model-tier change.
* The blind audit's in-scope agreement — it was 97.7 % for inclusions and 91.4 % for exclusions in R2026.30; a drop in
  either is a reason to look at the scope rules rather than at the projects.
* Supplementary joins: the archive cross-check agreement per field. Below ~90 % for sex or body site means the join
  ladder is matching the wrong column.
* The recall table by exclusion reason. In R2026.30 `virus_genome_surveillance` was leaking 18 % — a clinical mNGS
  study that reports viruses looks much like a surveillance deposit in an archive record. Watch that boundary.
