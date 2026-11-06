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
| 18c | Community profiles of new samples | `python scripts/extend_sandpiper.py --bulk <sandpiper bulk> --old-dir data/inputs/gut/sandpiper --catalog-dir <catalog build> --work <dir> --out data/inputs/catalog/sandpiper` | none |
| 18d | Package, site, verify | `make release` → `make site` → `make verify` (fails when the site passes 950 MB — GitHub Pages refuses sites above 1 GB) | none |
| 18e | Visual and wording review | an Opus 5 sub-agent renders the system pages and a spread of project pages (one per body site, RNA, DNA + RNA) and reports by page and element only | ≈ $5 |
| 19 | Publish | `make check-credential`, `make publish-branch`, then push the two release branches, the two tags and the `release-assets` branch | none |

There is one catalog build (`gut-build`, called by `release`): it reads the original study list, the projects admitted by
the scope classification and every sample / determination / run input from `data/inputs/catalog/`, applies the run-level
scope (amplicon and single-cell runs out; refetched runs of earlier projects only when they are RNA libraries of a catalogued
sample or the only data set of a catalogued sample), and writes the `catalog_*` tables with their bitemporal release columns. `scripts/public_history.py` (also called
by `release`) keeps the package's release notes and changelog to the releases from `public_history_from` in
`config/releases.yaml`.

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
* **Do not run two heavy jobs at once on the local machine.** Rendering very large pages (the project list, the scope
  page) while the catalog build runs slowed the build from about 40 minutes to almost 4 hours.

## Next run: carried over from R2026.34

* First: the `catalog_runs` gap (38,158 samples without run rows), the Sandpiper 2.0.1 refresh and the Sandpiper table re-derivation,
  then the analyses and the benchmark changes in `docs/SANDPIPER_ANALYSES_PLAN.md` (sections 1–3).
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
