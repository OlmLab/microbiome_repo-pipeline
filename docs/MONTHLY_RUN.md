# The monthly run

One command per stage, in order. Every stage is resumable: it skips what is already in the cache, so a stage that
stops halfway can simply be run again. Model calls are capped per stage and recorded in that stage's `ledger.csv`;
refusals are stored and never re-sent.

Set `L=~/catalog/cache/large_update` and run from the pipeline root.

| # | What | Command | Model cost |
|---|---|---|---|
| 1 | Refresh the registry: new ENA studies since the last run | `make resweep` then `make registry-enumerate` | none |
| 2 | Read check on the new studies' runs | `python scripts/read_check16s.py --runs $L/delta_runs.txt --out $L/delta_readcheck.csv` | none |
| 3 | Dossiers for the new studies | `python scripts/build_dossiers.py --projects $L/delta_new_projects.txt --out $L/dossiers_delta` | none |
| 4 | Find candidate papers | `python scripts/find_papers.py --projects $L/delta_new_projects.txt --out $L/delta_paper_candidates.parquet` | none |
| 5 | Classify (two readings) | sub-agents running `scripts/pilot_passes.py --passes classify` | ≈ $0.03 / project |
| 6 | Merge the readings | `python scripts/merge_classify.py $L/stage1/delta_out $L/stage1/delta_merged.parquet $L/stage1/delta_adjudicate_ids.txt` | none |
| 7 | Tie-break the disagreements | `scripts/adjudicate_classify.py` (role `adjudicate`, Opus 5) | ≈ $0.07 / project |
| 8 | Final classification table | `python scripts/finalise_classify.py $L/stage1/delta_merged.parquet $L/stage1/delta_adj $L/stage1/delta_final.parquet` | none |
| 9 | Paper link audit and full-text reading | sub-agents running `scripts/pilot_passes.py --passes link_audit,fulltext` | ≈ $0.05 / project |
| 10 | Harvest BioSample attributes | `python scripts/harvest_biosamples.py --samples <new samples>.csv --out $L/stageN_attrs` | none |
| 11 | Map any attribute values not already in the map | `scripts/map_attribute_pairs.py` | ≈ $0.0004 / value |
| 12 | Subject and timepoint | `python scripts/derive_subject_timepoint.py $L/new_biosample_attributes.parquet $L/stage5/subject_timepoint.parquet` | none |
| 13 | Supplementary tables of the new papers | `join_ladder_audit.py` → `extract_supp_rows.py` → `classify_supp_columns.py` → `map_attribute_pairs.py` → `assemble_supp_values.py` | ≈ $0.01 / project |
| 14 | Per-sample values | `python scripts/assemble_samples_stage5.py $L` (new projects); `python scripts/assemble_rna_specimens.py $L <stage dir>` for samples harvested outside stage 5 (RNA-only samples of earlier projects, projects admitted without samples) | none |
| 15 | Summarise the paper passes | `python scripts/summarise_paper_passes.py --out-dirs <pass out dirs> --out-prefix <dir>/ --merge-into <previous three parquets>` | none |
| 16 | Release check: recall by exclusion reason | `scripts/blind_audit_scope.py` on a 40-project sample of EACH `out_reason`, then a full recheck only of the reasons whose error rate is material | ≈ $0.03 / project |
| 17 | Apply recoveries | flip the recovered projects in `classify_all.parquet`, then repeat steps 10–15 for them | none |
| 18 | Inputs of the catalog build | `PYTHONPATH=src python scripts/prepare_catalog_inputs.py --extra-facts-dirs <summarised pass prefixes>` → `data/inputs/catalog/` (study rows, samples, determinations, paper facts, exposure items, papers, short descriptions, `runs_source.parquet` with `run_origin`) | none |
| 18b | Short descriptions of projects that have none | sub-agents running `scripts/write_short_descriptions.py` (rubric tier; refused projects once more with `SHORT_DESC_ROLE=adjudicate`) | ≈ $0.01 / project |
| 18c | Community profiles of new samples | `python scripts/extend_sandpiper.py --bulk <sandpiper bulk> --old-dir data/inputs/gut/sandpiper --catalog-dir <catalog build> --work <dir> --out data/inputs/catalog/sandpiper` | none |
| 18d | Package, site, verify | `make release` → `make site` → `make verify` | none |
| 19 | Publish | `make check-credential`, `make publish-branch`, then push the two release branches, the two tags and the `release-assets` branch | none |

There is one catalog build (`gut-build`, called by `release`): it reads the original study list, the projects admitted by
the scope classification and every sample / determination / run input from `data/inputs/catalog/`, applies the run-level
scope (amplicon and single-cell runs out; refetched runs of earlier projects only when they are RNA libraries of a catalogued
sample), and writes the `catalog_*` tables with their bitemporal release columns. `scripts/public_history.py` (also called
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

## What to watch

* `classify_all.parquet` status counts — a jump in `refused` means a model-tier change.
* The blind audit's in-scope agreement — it was 97.7 % for inclusions and 91.4 % for exclusions in R2026.30; a drop in
  either is a reason to look at the scope rules rather than at the projects.
* Supplementary joins: the archive cross-check agreement per field. Below ~90 % for sex or body site means the join
  ladder is matching the wrong column.
* The recall table by exclusion reason. In R2026.30 `virus_genome_surveillance` was leaking 18 % — a clinical mNGS
  study that reports viruses looks much like a surveillance deposit in an archive record. Watch that boundary.
