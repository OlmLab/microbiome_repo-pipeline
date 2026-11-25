# Analysis-ready exports — for the Editor

**Status (2026-11-24): ready.** All 44 checks pass on data package 1.34.0. Build the files on the release
package you publish and attach them with `DATASET_CARD.md` and `export_manifest.csv`.

These are **release downloads, not site files**. Nothing here touches the website or its size limit.

## Build (24 s with 3 threads and a 4 GB DuckDB memory limit; peak memory not measured)

```bash
python build_exports.py \
  --package <release package dir> \
  --profiles-dir <dir with *sandpiper_sample_genus*.parquet and *sandpiper_sample_species*.parquet> \
  --replication-claims ~/catalog/projects/replication/for_editor/replication_page/payload/replication_claims.csv \
  --bugsigdb-claims ~/catalog/projects/replication/tables/claims.parquet \
  --replication-label "the pre-registered replication run of 2026-11-15 on data package 1.34.0 (release R2026.41)" \
  --out <exports dir>

python check_exports.py --out <exports dir> --package <release package dir> --profiles-dir <same dir> \
  --replication-claims <same replication_claims.csv>
```

Needs only `duckdb` (Python). The profile tables may be one file per level or the `_part*` files of a release;
every file matching the pattern is read. The script reads the release tables and writes only into `--out`.

## What it writes (package 1.34.0: 7 data files, 99.9 MB)

| File | Rows | Bytes | Content |
|---|---|---|---|
| `ml_samples_v<v>.parquet` | 379,776 | 7,817,341 | row axis: sample, project, project group, person, 12 label columns, depth, `fold_5`, `fold_10` |
| `ml_genera_v<v>.parquet` | 7,083 | 168,087 | column axis: genus, lineage, number of samples |
| `ml_genus_abundance_long_v<v>.parquet` | 23,682,153 | 82,544,497 | the samples × genus table as non-zero cells |
| `taxon_prevalence_body_site_condition_v<v>.parquet` | 482,574 | 5,349,585 | genus and species prevalence per body site and condition |
| `prevalence_denominators_v<v>.csv` | 207 | 7,784 | samples and projects per body site and condition |
| `replication_signature_taxa_v<v>.csv` | 6,933 | 3,590,194 | one row per registered claim with result and BugSigDB source |
| `replication_signatures_v<v>.csv` | 1,074 | 397,273 | one row per BugSigDB signature with a count per result |
| `DATASET_CARD.md` | | | the dataset card, written with the counts of the build |
| `export_manifest.csv` | | | file, rows, bytes, sha256, release |

## Design choices to know

- **Sample set:** current release, specimen, profiled, none of the seven quality flags — the set the taxon explorer uses (379,776 samples, 2,905 projects on 1.34.0).
- **Sparse layout:** 379,776 × 7,083 cells are 0.9 % non-zero, so the table is three files (rows, columns, cells). The card shows the four lines that turn them into a matrix.
- **Splits:** a project group's fold is the first 8 hex digits of `md5(project_group)` modulo 5 (or 10). It never changes between releases. Folds are unequal in size (62,283 to 92,368 samples).
- **Conditions:** the release's `health_condition` field as it is. When the Conditions work changes that field, rebuild; nothing else changes.
- **Replication rows:** copied from the frozen replication run, not recomputed; the run is named in the card.

## What `check_exports.py` checks

Manifest sizes and sha256; the sample set against a separately written rule; labels against the release table; the fold rule for every project group; no duplicate cell; every non-zero cell count against the source (23,682,153); 300 sampled cells against the source value; prevalence arithmetic; condition rows adding up to the body-site row; 30 sampled prevalence cells recomputed from the source tables; replication rows and result counts against the run's claim table.

## BugSigDB links

`bugsigdb_url` is `https://bugsigdb.org/<study>/Experiment_<e>/Signature_<s>`, where `<study>` is the PubMed id of the
paper (BugSigDB names a study page by its PubMed id) or `Study_<n>` for a study without one. All 6,933 rows of the
replication run are of the PubMed-id form. The check rebuilds each link from `bsdb_id` alone and compares.
An earlier version of the check expected `Study_<n>` everywhere and failed on every row; the links were right and the
check was wrong. The form was confirmed against BugSigDB pages found by a web search; no link was fetched from here.

## Not done

- The exports were built on `build/package` 1.34.0, not on the published 1.34.1.
- No BugSigDB link was opened in a browser or fetched.
