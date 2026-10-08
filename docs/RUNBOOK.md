# RUNBOOK — the monthly catalog cycle (Reviewer A findings A4, A14; publishing per A1–A3, A9)

Audience: a **fresh Claude Science session** (no memory of earlier runs) or the owner at a shell. Every stage names
its inputs, outputs, expected volume, token budget, wall time, whether it needs LLM delegation, and its stop rule.
Deterministic stages (0, 1, 5, 6, 7) can run anywhere with the conda env; LLM stages (2, 3) run only in a Claude
Science **root** session with delegation switched on (sub-agents cannot delegate).

Log the actuals of every cycle in `docs/CYCLE_LOG.md` (date, SINCE, candidates, includes, tokens, wall time, tag).

## 0. Preconditions (every cycle, 10 min, no tokens)

| check | command / action | expected |
|---|---|---|
| repo + env | `git -C ~/catalog/catalog-pipeline pull` · `pip install -e .` (once per env) · shell: `python bootstrap.py --no-env --only-required`; Claude kernel (artifact store reachable): `__file__='<repo>/bootstrap.py'; exec(open(__file__).read()); main(['--no-env','--only-required'])` | `missing_required: []`, `hash_mismatch: []`, exit 0 and tests green (hash, validator sync, model resolution, determinism). **Exit 4 = required inputs missing — tests were not run; do not proceed.** |
| inputs present | `ls data/inputs/` | `data_package_v*.zip`, `catalog_studies.parquet`, `study_triage_v2.parquet` (from `~/catalog/data/` or the artifact store — ids below) |
| harvest cache (stages 1–3 only) | `ls ~/catalog/cache/harvest_cache/http_cache.sqlite` — else untar `harvest_cache.tar.gz` (artifact, 9.7 GB → ≈ 25 GB) and `sha256sum -c harvest_cache.sha256` | present; ≥ 30 GB free disk |
| skills (Claude session) | `skill("infant-curation-rules")`, `skill("infant-catalog-harvest")`; then `make sync-skills` recipe → `python -m pytest tests/test_validators_sync.py -q` | repo validators == skill kernel |
| models | in a Claude kernel cell: `from catalog.models import set_host, main; set_host(host); main()` — or from a shell with `CATALOG_MODEL_<ROLE>` exported: `python -m catalog.models` | prints role → live id for all four roles and returns/exits 0; **exit 1 = a role is UNRESOLVED** (R1-01). No literal ids anywhere (`grep -rn "claude-" src` finds only comments) |
| network grants | ENA, Europe PMC, NCBI, Crossref (default); `github.com`/`api.github.com` (publish); `olmlab.github.io` (post-deploy check); `zenodo.org`, `sandpiper.qut.edu.au` (Sandpiper module) | granted (Settings → Domain Allowlist) |
| credentials (stage 7) | Customize → Credentials holds `GitHub` (fine-grained PAT, contents:write on the two repos; docs/SECURITY.md) | `git ls-remote` succeeds with the credential helper |
| delegation (stages 2–3) | the session toggle "allow delegation" is ON; host.llm cap 2.0 M/frame | leaf workers can be dispatched |
| version | `cat config/version.txt` → bump **before** stage 5 (`1.2.0` → `1.3.0` for a monthly data update; patch for fixes only) | semver; CHANGELOG entry started |

Key artifact ids (latest versions at 2026-09-26; the authoritative list with sha256 is `config/inputs.json`):

| input | artifact_id | version_id (latest) | size | sha256 (first 12) |
|---|---|---|---:|---|
| `data_package_v1.zip` | `3c54a664-0afe-46f3-9dcd-14575cbe9911` | `b3689a6a-8b35-4093-8a4a-a5a307c705f7` | 26.0 MB | `acf018317868` |
| `site_generator.zip` | `fed8f96a-3664-4e51-8de9-e2d1290f5975` | `48832759-2daf-461f-b96c-8c329bf881de` | 0.0 MB | `b20781381a36` |
| `release_bundle.tar.gz` | `e26d789c-599d-4908-b5bf-050252504dc2` | `1ed50ad8-6476-456f-a5c8-7d6c6dc31e3e` | 69.3 MB | `5464b037f9f7` |
| `catalog_studies.parquet` | `4e770407-a41a-4c38-86b5-977a05a5ec50` | `caf0360d-398e-4c5e-b7c2-d21781f5a7b1` | 1.1 MB | `25fd7f26a8a6` |
| `study_triage_v2.parquet` | `843218d6-2dc5-43ee-a706-cb5af88e13f6` | `6a2af383-b9ef-4c31-b2df-935ba5eb8401` | 1.0 MB | `108232fe4a42` |
| `harvest_cache.tar.gz` | `75b88127-b830-47c2-bdd1-19b2a09eeb96` | `1651f99b-eae3-40de-8674-585632ed39fb` | 9,751.4 MB | `n/a` |
| `infant_catalog.sqlite` | `9eb19bd1-f41c-4cdf-9d65-5eab9618df7e` | `76e57aaa-2c7f-487c-bed2-8fa57d7979ef` | 202.8 MB | `675404e79a75` |
| `auditor_findings.csv` | `3d100e7c-2e4f-4247-b574-1d696e948ced` | `fbdf361f-5cb4-4ee4-b69c-2cc5426460cc` | 0.0 MB | `5d0a4a55935a` |
| `site.zip` | `c99f6f6e-5fbc-4cba-9d8f-0ed37abe4ba3` | `95ff2ef5-fdb4-4777-b47e-7873d0e578d7` | 72.9 MB | `fd4f6c151682` |
| `harvest_lib.py` (code) | `23624e52-e5d3-46ba-8e4e-718f29ede141` | `b5eefacd-1a3b-4f48-a931-3b732fbad09e` | 18 kB | `94bec7791f3b` |
| `resweep_universe.py` (code) | `8f67afb0-0521-4201-8b3e-fc49606e4068` | `fdc86797-5a3a-4a32-bb74-a164d9de5a88` | 30 kB | `2a904a16c9a6` |
| `llm_batch_common.py` (code) | `bfe20c88-0384-44f5-ba8f-2f1e41163ae8` | `083873f8-557b-4f65-bc80-651c9f7ce527` | 4 kB | `815a37e19e94` |
| `run_sonnet_confirm.py` (code) | `aa54d394-b5e6-4462-868f-723e8e17ba15` | `f838c86e-a5d9-40a3-bcc0-c9851bccc390` | 11 kB | `7066bdb61bd3` |
| `curation_kernel_ext.py` (code) | `0bacc0dd-e051-45e0-b99c-373436e6f7d0` | `558bee32-ccbc-4cc3-a31a-ab2011fbc92b` | 8 kB | `7bd412cb0248` |
| `build_wide.py` (code) | `cc60f35d-7d16-482e-aed8-395cebe013f8` | `7c23cf00-5cde-4aa0-9366-8a063cf3018d` | 2 kB | `5b2f8704a3f5` |

## 0a. Owner bootstrap (once, ~45 min, no tokens) — R1-05

**Status 2026-09-26 (evening): DONE.** All three repos exist and are public with an active `protect-main` ruleset;
Pages source on `microbiome_repo` = GitHub Actions; the PAT is stored as credential `GitHub` (env `GITHUB_TOKEN`);
`~/catalog/` is granted rw and holds the three clones. Steps 2–4 and 8 below were executed by the agent (the owner
authorised the first pushes to `main` of the two new repos and the `.github/` install on the site repo). Kept for reference:

State probed 2026-09-26 morning (Reviewer 1): `OlmLab/microbiome_repo` exists (main only, no `.github/`, pre-1.2.0 site);
`OlmLab/microbiome_repo-pipeline` and `OlmLab/microbiome_repo-data` do **not** exist. Nothing in stage 7 works until:

1. **Create the two missing repositories** (GitHub UI, org OlmLab): `microbiome_repo-pipeline` (private or public, empty, no
   README) and `microbiome_repo-data` (public, empty). Enable Issues on `microbiome_repo` (the Issues repo,
   `config/site.yaml` → `github.issues.repo`).
2. **First push of the pipeline repo** (owner shell):
   ```
   cd ~/catalog && unzip -q pipeline_repo_v3.zip && cd microbiome_repo-pipeline      # or the artifact of the current cycle
   git init -b main && git add -A && git commit -m "microbiome_repo-pipeline v1.2.1 (repo fix wave)"
   git remote add origin https://github.com/OlmLab/microbiome_repo-pipeline.git && git push -u origin main
   ```
   `verify.yml` runs on that push (unit tests only — no site in this repo).
3. **Clone the site and data repos** to the paths in `config/site.yaml` (`~/catalog/infant-gut-catalog`,
   `~/catalog/infant-gut-catalog-data`; the data repo may be empty — `git clone` still works).
4. **Install the workflows and the Issue form where GitHub needs them** (they are authored here but must live in the
   repo they act on): `make install-workflows` copies `verify.yml` + `deploy-pages.yml` + `ISSUE_TEMPLATE/catalog-finding.yml`
   into the site clone and `release.yml` into the data clone. Commit and push `.github/` in both clones **to main**
   (owner — the agent never pushes main). Without the template in the Issues repo every "Flag an issue" link opens a
   blank issue and the prefilled fields are lost.
5. **Pages source = GitHub Actions** on `microbiome_repo` (Settings → Pages → Build and deployment → Source:
   GitHub Actions). Until this is switched, `deploy-pages.yml` cannot deploy and generated HTML would have to be
   committed to `main` (the legacy path via `~/Downloads/site`, RUNBOOK §7 last line).
6. **Rulesets** on `main` of all three repos (docs/SECURITY.md §2 item 2) and **Zenodo ↔ GitHub** on the data repo.
7. **PAT**: fine-grained, resource owner OlmLab, repositories `microbiome_repo` + `microbiome_repo-data`
   (+ `microbiome_repo-pipeline` if the agent is to push `cycle/*` branches), permissions Contents: read/write, Issues:
   read/write (for `make ingest-issues`), Metadata: read. Store it under Customize → Credentials as `github`
   (SECURITY §2 item 5, §3). Record the injected env-var name in SECURITY §3 after the first successful
   `git ls-remote` (R1-15).
8. Re-run `make install-workflows` whenever a workflow or the Issue form changes in this repo (the site repo's copies
   are excluded from `publish-branch`'s rsync so they are never overwritten by a site build).

## 1. Re-sweep — find new candidate studies (deterministic, network; 20–40 min; 0 tokens)

```
make resweep SINCE=$(date -v-35d +%F)        # or: python src/catalog/enumeration/resweep_universe.py --since YYYY-MM-DD \
                                             #        --catalog data/inputs/catalog_studies.parquet --out build/resweep_<cycle>
```
Inputs: `catalog_studies.parquet` (current verdicts), harvest cache. Slices: S1 frame-free METAGENOMIC WGS/WXS,
S2 misfiled GENOMIC on primary taxa, S3/S3b OTHER/Targeted-Capture with host or tax 9606 — all `first_public >= SINCE`
(overlap the previous cycle by ≥ 1 week; ENA `offset` paging is unreliable, so each slice is one `limit=0` stream
checked against the ENA count endpoint). Output: `build/resweep_<cycle>/candidates.parquet` (+ slice parquet, counts,
`resweep_report.md`). Expected: **≈ 146 new human-signal studies/month (111–180)**, of which ≈ 38 auto-excluded
deterministically (host taxon / isolate / amplicon) → **≈ 108 to judge**; ≈ 6 will end up included (2–11).
Every count mismatch vs the ENA count endpoint is printed — re-run the slice, do not proceed with a partial slice.

**Stop rule:** > 300 candidates after deterministic exclusion (or any slice count off by > 5 %) → stop and ask the
owner (an ENA schema change or a broken cache is more likely than a real surge).

Superset check (quarterly): `python src/catalog/enumeration/enumerate_universe_v3.py --audit-only` — taxon frames
must be a subset of the frame-free universe; `verify_taxa()` refuses to run on a taxid/label mismatch.

### 1b. Sandpiper delta — Curator (deterministic; ≈ 40 min; 0 tokens; runs after 1 when new runs entered the catalog) — R1-14

```
make sandpiper-delta                     # ZENODO_RECORD=20419175 SANDPIPER_VERSION=2.0.0 (current snapshot); needs the bulk file
```
Reads the snapshot bulk file from `~/catalog/external/sandpiper/<version>/` (or restore it from the snapshot artifact listed
in `config/inputs.json` → `sandpiper2.0.0.gtdb.csv.gz`, 3.7 GB, sha256 `4732c4e1…`), `data/inputs/sandpiper/
catalog_runs_sandpiper_match.parquet` (re-derive it first when runs were added: membership = run accession present in
`per_acc_summary`) and the unpacked package. Steps: `prepare_inputs.py` (reconstruction — see `src/catalog/sandpiper/README.md`)
→ `filter_bulk.py` (one streaming pass, ≈ 25 min) → `build_sandpiper_tables.py` (DuckDB, ≈ 10 min). Output
`build/sandpiper_<record>/sp/sandpiper_*` → `make package-merge` copies them into the package. Owner: **Curator** (not the
Release Engineer — it is a data-derivation step). Stop rule: `filter_log.json` runs_kept < 95 % of matched runs → the
bulk file or the match table is stale.

### 1c. Sandpiper snapshot refresh — when a NEW Zenodo version appears (deterministic, network; ≈ 70 min; 0 tokens) — R3-4

```
make sandpiper-refresh ZENODO_RECORD=<new record id> SANDPIPER_VERSION=<x.y.z>
```
Monthly check of the concept DOI 10.5281/zenodo.10547493. `download_bulk.py` streams the new bulk file (resumable, md5
checked against Zenodo), then 1b runs against it. Afterwards: store the bulk file under `~/catalog/external/sandpiper/<version>/`
**and** save it once as a snapshot artifact (`docs/DATA_LAYOUT.md`: one snapshot artifact per Zenodo version + the host copy);
add its artifact id + sha256 to `config/inputs.json` (group `sandpiper`); bump `taxonomy_version` if GTDB changed
(`SANDPIPER_TAXVER`). Requests: 1 record lookup + 1 streamed download (+ Range resumes).

### 1d. Author index rebuild (deterministic, network; ≈ 25 min; 0 tokens; quarterly or when > 50 new studies) — R3-4

```
make authors                             # src/catalog/authors/harvest_bioproject_authors.py <catalog_studies.parquet>
```
NCBI eutils esearch (100 accessions/request) + efetch BioProject XML → `bioproject_records.parquet` (checkpoint every
≈ 2,000 studies) via `harvest_lib` cache-through; ≈ 60 + 60 requests per 6 k studies (≈ 3 requests/s with an NCBI key).
The downstream tables (`authors.parquet`, `study_authors_summary.csv`, `authors_index.json`, `organisations.parquet`) are
rebuilt by the Data-fix track's builder in the package step (`make package-merge --authors`); inputs are listed in
`config/inputs.json` group `authors`.

## 2. Triage — LLM verdicts for new candidates (Claude root session; ≈ 1.5 h; ≈ 0.4–0.6 M tokens)

Load `skill("infant-curation-rules")` (rules, traps, validators). Cascade, all batched through
`src/catalog/triage/llm_batch_common.py` (JSON-schema tool output, `validate_row` on every row, checkpoint every
200, missing ids → sentinel + re-run at batch 10 with 2× max_tokens):

1. Haiku screen — 40 studies/request, role `screen` (only if > 150 candidates; below that go straight to 2).
2. Sonnet rubric ×2 — 4 studies/request, role `rubric`, `run_sonnet_confirm.py` (set `SLICE`, `OUT_PREFIX`,
   `REPLICATE` globals, then `exec`); ≈ 1.15 k tokens/study + 4.9 k/request.
3. Opus adjudication — non-unanimous or `unsure` studies, role `adjudicate`, `adjudicate.py`; typically 15–25 %.
4. Literature check for includes/unsure: `run_paper_screen.py` (25 papers/request, ≈ 775 tok/paper) on Europe PMC
   hits for the accession; deterministic + Sonnet linking (`link_papers.py`); supplementary-table rescue rule 15.

Dispatch: from the root session, one leaf worker per ≤ 0.3 M projected tokens (`platform.leaf_worker_soft_cap_tokens`
in `config/budgets.yaml`; the platform hard cap is 2.0 M/frame — never plan a leaf above the soft cap); the worker
copies `src/catalog/triage/*.py` + `curation_kernel*.py` flat into its cwd (or `pip install -e .` the repo), sets the
globals and execs. Never impute a verdict for a missing id; `uncertain` after Opus is the human queue.

Outputs: `build/triage_<cycle>/verdicts.parquet` → merge into `catalog_studies.parquet` / `study_triage_v2.parquet`
with `decision_stage = 'cycle_<YYYY-MM>'`, append the new rows to `universe_studies_all`. Expected tokens:
108 studies × (2 × 1.15 k) + 27 requests × 4.9 k ≈ 0.38 M; + Opus (≈ 25 × 3 k) ≈ 0.08 M; + literature ≈ 0.1 M.

**Stop rules:** > 30 includes in one month (5× the historical max) → ask the owner before extraction; any batch
with > 10 % validator-rejected rows → fix the prompt (quote length) before continuing; a worker error rate > 5 % →
stop, the model id or schema changed.

## 3. Extraction — per-sample metadata for NEW included studies (Claude root session; ≈ 3 h; ≈ 50 k tokens/study)

Load `skill("infant-curation-rules")` (per-sample extraction rules §"Per-sample extraction rules"). Per new study:
* R1 archive attributes + sample-name conventions: `r1_prime.py`, `r1_title_parser.py`, `r1_ext.py`
  (Haiku normalisation role `screen`) — deterministic first, cheapest, precedence 1.
* R2 supplementary tables: `r2_supp_extract_v2.py` (column classification, role `screen`; prompt
  `prompts/r2_column_classify_system.txt`), `supp_parse.py`, `col_reader.py`, `re_gate.py` (ID gate ≥ 50 % / ≥ 20 rows
  pooled), `r2_rescue_map.py` for non-archive IDs.
* R3 paper prose (group scope): `r3_prose_extract.py` (Sonnet ×2, role `rubric`; prompt `prompts/r3_prose_system.txt`).
* R4 abstract / ENA description: `r4_abstract_extract.py` (role `screen`; conf ≤ 0.5, `evidence_limited_to_abstract=1`).
* Merge: `merge_routes.py` (precedence R1 > R2 > R3 > R4; conflicts adjudicated per pattern by role `adjudicate`;
  every group statement Opus-audited) → `subject_resolution.py` → `build_wide.py` (+ `build_fix.py` sample-unit
  criteria; `re_gate.py`).
Inputs: the new studies' ENA sample attributes (harvest cache), linked papers' full text + supplements
(`fetch_papers.py`, Europe PMC; ≈ 15 % of fullTextXML calls return HTTP 500 — record, do not loop).
Outputs: `build/extraction_<cycle>/sample_determinations_new.parquet` (+ rejected, candidates), merged into the
package tables. Expected: 6 studies × ≈ 50 k = **0.3 M tokens**; 2–3 h wall time dominated by supplement fetches.

**Stop rule:** a study with > 5,000 samples or > 3 own-data papers → run it as its own leaf worker and check the
mixed-age banner logic (B1) before merging.

### 3b. New samples in an included study (deterministic; ≈ 15 min + network; 0 tokens) — added R2026.2

When stage 1 reports new runs for a study that is ALREADY included (`resweep` → `<study>_new_runs.parquet`), do not re-triage:
run `make gapfill GAPFILL_STUDY=<acc> GAPFILL_NEW_RUNS=<parquet>` (module `src/catalog/extraction/gapfill_samples.py`). It
harvests the new BioSamples cache-through (ENA XML, NCBI efetch fallback, study + experiment XML), writes
`attribute_comparison.json` (attribute keys / value shifts vs the study's existing samples — read it: the R2026.2 case,
PRJNA1140720, added 150 *saliva* samples of mothers/fathers/siblings to a stool study), then runs the same deterministic R1
stack as stage 3 (`config/attribute_field_map.csv` parsers, `r1_title_parser`, U1/U2 unit rules) plus two gapfill-specific
rules: **U3** (a linked paper's per-individual supplementary table whose age column header names the unit and agrees with the
bare attribute per sample; disagreeing samples stay sentinels) and **composite subject ids** (when the study's existing
`subject_id` convention is `<family>_<subject>`). R2 is deterministic only (exact-ID gate ≥ 50 % / ≥ 20 rows; header-named
columns); R3/R4 rows are never minted — existing `cohort_default` statements of the study are extended verbatim. Every
committed row passes `validate_row`; ages > 1,100 d are committed under the v1.2 `out_of_scope_adult` convention. With
`GAPFILL_SANDPIPER=1` the per-run Sandpiper API is checked (≤ 0.5 req/s; runs newer than the snapshot horizon get
`published_after_snapshot_horizon`). Outputs (`build/gapfill_<release>/`): `runs_new`, `samples_new_wide` (148 package
columns), `sample_determinations_new` (+ `_superseded`, `_rejected`, `_sentinels`), `sample_subjects_new`,
`sandpiper_run_qc_new`, `sandpiper_sample_summary_new`, `study_metadata_wide_delta.json`, `GAPFILL_<study>_REPORT.md`. Read
the report, then `make apply-gapfill` appends the rows to the package tables, updates `study_metadata_wide` (counts, cov_*,
sp_*), `universe_studies_all` and `build_counts.json`, and asserts that no existing row changed
(`APPLY_GAPFILL_<study>.json` holds the before/after counts and row hashes). Then continue with stage 4/5.
Known limits: `t_index`/`n_timepoints_subject` of the new rows are computed within the new series (existing rows are never
rewritten); a study with > 2,000 new runs exceeds the Sandpiper delta budget — split across cycles.

## 4. Findings — apply audit results (deterministic; 5 min; 0 tokens)

```
make findings        # python -m catalog.apply_findings --package data/inputs/data_package --findings audit/findings --out build/applied
```
Inputs: `audit/findings/YYYY-MM-DD_<source>.csv` — Auditor session output, or GitHub Issues labelled `finding` pulled by
`make ingest-issues` (`src/catalog/ingest_issues.py`: parses the issue-form body; `date` = created_at, `source` = `issue#N`).
**`audit/schema.json` is the single source** of columns, finding types, actions and their allowed pairs (R1-09/F2): the
Issue form is generated from it (`python -m catalog.findings_schema --write-template`, checked by
`tests/test_apply_findings.py::test_template_matches_schema`), `apply_findings` validates against it, and the Auditor
profile's finding_type list is `python -m catalog.findings_schema --auditor-vocab`.
Each row is re-validated with the kernel validators; rejected rows stay in `APPLY_FINDINGS_DIFF.md` with the message.
Applied rows carry `decision_stage='auditor_review'`, `src_track='auditor_review:<source>'`, route `H` for human evidence;
superseded rows move to `sample_determinations_superseded.parquet`. **The wide tables are rewritten in step** (R1-06):
`sample_metadata_wide` cells (+ `__confidence`, `__route`), `universe_studies_all.catalog_status`, and the samples of a
study turned `excluded` leave the wide table (kept in `sample_metadata_wide_excluded_by_review.parquet`).
`action = confirm` (the site's "Confirm correct" button, finding_type `confirmed_correct`, no quote needed) appends to
`confirmations.parquet` and sets `<field>__verified = true` on the sample row — the human truth set. `action = add_study`
(`universe_miss`) writes `candidates_<date>.csv` for the next triage cycle and never includes directly.
Tables are emitted only when ≥ 1 row applied (`build/applied_<version>/APPLIED`); `make package` re-runs `findings` and
copies them only then. Review the diff before stage 5.

## 4b. Contribute worklist + contribution ingest (deterministic; 1 min; 0 tokens) — R2026.2
The community-contribution loop (MATURITY_PLAN §3.2–3.4; `config/contribute.yaml` frozen schema; `docs/CONTRIBUTE.md` narrative).
* **Worklist** — `make worklist` (runs inside `make release` after `bitemporal`, before `package-docs`): `python -m catalog.contribute.build_worklist
  --package build/package --inputs data/inputs/contribute --out build/package --release-id $(RELEASE_ID) --package-version $(VERSION)`
  writes `contribute_worklist.csv` (one row per OPEN included/uncertain study, ranked) and `contribute_worklist_fields.csv` (study × 6 fields)
  plus `build/WORKLIST_REPORT.md` (blocker distribution, top-25, deviations). Inputs = `config/inputs.json` group `contribute`
  (bootstrap.py materialises them under `data/inputs/contribute/`). No LLM, no network; `tests/test_contribute_worklist.py` recomputes
  priority_score from every row and checks vocabularies, coverages ∈ [0,1], six field rows per study, issue_url round-trip.
* **Intake** — contributors open the prefilled GitHub Issue (`issue_url`; form `.github/ISSUE_TEMPLATE/catalog-contribution.yml` in the site
  repo, label `contribution`, table dragged into the note). `python -m catalog.contribute.ingest_contributions` (site/ingest track) lists the
  Issues, downloads attachments, applies the size/type gate and the deterministic R2 joinability check (does an ID column map to the study's
  own BioSamples/runs/library names, directly or via an uploaded key?) and posts the verdict `accepted_for_review | unjoinable |
  duplicate_of_existing | rejected` back on the Issue. Accepted tables go to the Curator session (stage 3 extraction contract, route R2,
  `evidence_source = contributor_table:<issue>`); applied values enter the next release with `release_added` and the study leaves the
  worklist (`release_retired` on its row). Stop rule: never apply a contributed value without the Curator step; never copy an e-mail
  address into any table.
## 4b. Contributions — ingest GitHub-Issue uploads (deterministic; 5 min; 0 tokens) — R2026.2

`make ingest-contributions` (optionally `SINCE_ISO=2026-10-01T00:00:00Z`, `COMMENT=1` to post verdicts; reads work without a token,
comments need the GitHub credential declared on the cell). Output `audit/contributions/<issue>/{manifest.json,report.json,REPORT.md,<file>}`
and `INGEST_SUMMARY.json`. Then, in the Curator session, run the normal R2 extraction contract on every `accepted_for_review` table
(`evidence_source = contributor_table:<issue>`; conflicts to the conflict queue), reply on the Issue, and let the Data track's
`build_worklist` drop the study from `contribute_worklist.csv` in the next release. `unjoinable` issues get the ID form seen and the key
that is missing in the comment; no further action until the contributor answers. Never open attachments outside pandas; never copy an
e-mail address anywhere (docs/SECURITY.md §7).

## 5. Package / release (deterministic; 5 min; 0 tokens)

```
vi config/version.txt                        # bump semver
vi config/releases.yaml                      # append the release under releases: (release_id R<YYYY>.<n>, package_version, previous_*)
vi docs/package_changelog/<semver>.md        # the package CHANGELOG entry (prepended by package-docs); docs/CHANGELOG.md by hand
make release BUILD_DATE=YYYY-MM-DD PKG_ZIP=data/inputs/data_package_v<prev>.zip
#   = unpack → findings → package-assemble (rsync + applied tables) → bitemporal (release columns on every fact table,
#     sample_determinations_all.parquet, releases.csv; asserts the previous tables are reproduced when the columns are dropped)
#     → package-docs (build_counts.json from the tables; README heading + Files rows; DATA_DICTIONARY 'Release columns'; CHANGELOG)
#     → release-notes (RELEASE_NOTES_<release_id>.md, prev vs new) → make_version (VERSION.json with release_id/previous_release_id) → zip → --check
make package BUILD_DATE=YYYY-MM-DD           # the pre-R2026.1 flow (no release columns) still works for hot fixes of the package format
```
Release model: `docs/RELEASES.md` (ids, immutability, bitemporal columns, registry, reconstruction caveat); every id/column/table name
lives in `config/releases.yaml`. Rows new in the release get `release_added = <release_id>`; rows of the previous package that vanished
become retired rows in `sample_determinations_all` (`release_retired = <release_id>`, `retired_change_stage = apply_findings`).
Record the cycle's tokens in `docs/CYCLE_LOG.md` BEFORE `make release` so the notes' "Token cost" line finds the row (else "not recorded").
`make_version --check` fails if README.md's "data package vX.Y.Z" ≠ `config/version.txt` (the package README is regenerated by
the package builder with the new version — never relabel old content) and if any table file is not listed in VERSION.json.
Outputs: `build/package/` (+ `VERSION.json` with per-table sha256 and row counts, written last), the **deterministic**
`build/data_package_v<semver>.zip` (fixed timestamps; byte-identical across rebuilds), `…zip.sha256` and a sidecar
`build/VERSION.json` carrying `package_zip.sha256` (R1-11). `make unpack PKG_ZIP=…` takes an explicit zip and flattens a
nested top-level directory.

## 6. Site + verify (deterministic; 1 min build + Actions; 0 tokens)

```
make site        # check-reports (fails when a report the site links to is missing, R1-08) → build_site.py …
make verify      # check_links.py (0 broken required) + DuckDB reads every parquet + make_version --check
```
The Playwright explorer smoke (loads `samples/index.html?study=PRJEB32631`, waits for "samples match", clicks a row,
expects the Evidence table) runs in GitHub Actions (`verify.yml`) — the sandbox has no browser. `REPORTS_DIR` needs
`CATALOG_REPORT.md`, `EXTRACTION_REPORT.md`, `NEXT_STAGE.md`, `SCALE_UP_PLAN.md`, `field_coverage.png` — all provisioned by bootstrap
from `config/inputs.json` group `reports` into `data/inputs/reports/`; the PNG is regenerated by `make plot-coverage`
(`scripts/plot_field_coverage.py` from `field_coverage_summary.csv`), not by the notebook.

**Site generator v3 (1.10.0, all ages).** `site_generator/gen/build_site.py` builds every section from the catalog tables (`gut_studies`, `gut_sample_metadata_wide`, `gut_sample_determinations`) and the `registry_*` tables: Studies (one page per catalog study + `data/studies/<PRJ>.csv.gz|.parquet|_determinations.csv.gz`), Cohorts (recomputed: curated records + registry papers describing 2–4 studies or citing 2–4 same-centre studies), Samples (DuckDB-WASM explorer over `gut_sample_metadata_wide`), Fields (from `config/packs/gut.yaml`), Authors (`registry_authors` ∪ infant `authors`), Scope (registry → catalog funnel), Contribute (studies ≥ 50 samples below 50 % on ≥ 3 core fields), Changes (`release_added` on catalog values / studies / registry studies). The infant-only tables surface only as the infant fields and in Downloads › Infant extension. `gut/index.html` and `universe.html` redirect. The generator asserts one current row per sample × field in `gut_sample_determinations` (retired rows carry `release_retired`).

**Site generator v3 — R2026.12 restructure (package 1.12.0; owner's site review 2026-09-29).** Top nav is `Home · Studies · Samples · Cohorts · Collections · Atlas · Authors · Registry · Downloads · Contribute · About` (`build_site.NAV`); Collections and Atlas pages come from other tracks — when a build has none, `build_site.py` writes a placeholder page so the nav entries are never dead links. Scope, Methods and Sources & acknowledgements live under `about/` (`about/index.html` names the lab and the funder from `config/site.yaml about:`); Downloads and Releases are one page `downloads/index.html` (release list + notes, anchors `#releases`, `#rel-<id>`); the changes pages stay under `changes/`. Old URLs (`scope.html`, `methods.html`, `sources.html`, `downloads.html`, `releases/index.html`, `gut/index.html`, `universe.html`) are written as redirect stubs (`build_site.REDIRECTS`). Field tiers are read from `config/packs/gut.yaml` (`core_fields`, `key_fields`, `derived_fields`; `read_field_tiers`) — the fields page, the study coverage table, the contribute worklist (open when ≥ 3 core fields are below threshold), the explorer filters / detail panel and the home tiles all follow the pack; columns the wide table does not yet carry are omitted (`field_series` / `has_col`), never rendered as 0 %. The home page shows five tiles (catalog studies, catalog samples, samples with ≥ 1 curated value, registry human studies = `host_human in {yes, mixed}`, countries), the search box (studies, cohorts, collections from `config/collections.yaml` when present, authors via the `authors/idx/<letter>.json` shards, accessions → explorer) and two top-8 bar lists (health condition, country) linking into the explorer. The registry page leads with the explorer (default filter host human = yes + mixed; checkboxes add unknown / no; `?study=PRJ…` filters to the study and opens its record) and states the host breakdown. Study pages carry "How this study entered the registry" (summary sentence + evidence list), a Sequencing block from `gut_runs.parquet` (copy it into the package dir before `make site`; absent → block omitted) and sample rows linking to `samples/index.html?sample=<key>` (filtered + panel open) and to the archive record (SAMN → NCBI, SAME/SAMEA → ENA, SAMD → DDBJ, runs → ENA). "Flag an issue" opens the two-question form `site_generator/gen/issue_templates/simple-finding.yml` (`config/site.yaml github.issues.template`); install it into the Issues repo next to `catalog-finding.yml` (copy to `<site clone>/.github/ISSUE_TEMPLATE/`, or add it to the `install-workflows` copy list). The "Confirm correct" button is gone. `tests/test_site_generator.py` builds the site from a synthetic package with and without the new columns and asserts all of this.

## 7. Publish (Release Engineer profile; 10 min; 0 tokens) — the agent pushes branches + tags, Actions deploys

Sandbox notes (2026-09-26): `git checkout -B release/<v> origin/main` inside the clones prints `could not write config file .git/config: Operation not permitted` — harmless (branch tracking only); rsync into the clones exits 23 (utimensat on the clone root) — `publish-branch` tolerates it and proves completeness with a checksum dry-run. Actions job logs cannot be downloaded from the sandbox (Azure blob redirect); read the jobs/steps API instead. Re-running a failed deploy: `POST /repos/OlmLab/microbiome_repo/actions/runs/<id>/rerun-failed-jobs` (PAT has Actions: write).

```
make publish-branch                          # release/<semver> in both clones; tags site-v<semver>, data-v<semver>
git -C ~/catalog/infant-gut-catalog      -c credential.helper='!f(){ echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f' push origin release/<semver> site-v<semver>
git -C ~/catalog/infant-gut-catalog-data -c credential.helper='!f(){ echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f' push origin release/<semver> data-v<semver>
```
(declare `credentials=["GitHub"]` on the cell; `GITHUB_TOKEN` is read from the environment, never printed.)

**Large release assets (R2026.13, owner 2026-09-30: no Sandpiper history in the data repo).** `publish-branch` no longer commits
`assets/` on the release branch. It stages the Sandpiper long tables (`scripts/split_parquet.py`, byte-adaptive parts < 90 MB) and
`registry_runs_v<v>.parquet` (re-written with zstd — the snappy copy passed GitHub's 100 MB hard limit at R2026.13) in
`/tmp/release_assets_<v>/assets/` and commits them in a throw-away repo whose git dir is `/tmp/release_assets_<v>.gitdir`
(the sandbox refuses to create any `.git` directory, so `GIT_DIR`/`GIT_WORK_TREE` are set explicitly). Push it **before** the data tag,
because `release.yml` fetches the branch at depth 1 and checks `assets/ASSETS_VERSION.txt` against `VERSION.json`:
```
GIT_DIR=/tmp/release_assets_<v>.gitdir GIT_WORK_TREE=/tmp/release_assets_<v> git -c credential.helper='!f(){ echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f' \
  push --force https://github.com/OlmLab/microbiome_repo-data.git HEAD:refs/heads/release-assets
```
The branch is overwritten every release (single commit, no history). `release.yml` skips `gut_sample_determinations` / `gut_runs`
in the SQLite and drops the SQLite when it exceeds 1.9 GB (GitHub asset limit 2 GiB; 1.12.0's Release stayed a draft for that reason).

**Registry owner overrides (R2026.14).** Study-level owner decisions that must beat every classification stage go into
`config/registry_overrides.yaml` (study_accession + any of host_human / assay / body_sites / body_site_primary / life_stages /
life_stage_primary, a ≤ 12-word evidence quote, a free-text note). `build_registry.apply_owner_overrides` applies them last and
sets `classification_stage = owner_decision`; `build_gut_scope` then re-checks the leaf-built study list against the pack
`study_rule` on the current registry, so an override that removes `gut_stool` retires the study's catalog rows in the same
release (first use: PRJNA50637 ileal-pouch cohort → `other_site`). Append entries, never delete them.

**Sequencing depth backfill (R2026.16).** Before `make release`, run `python scripts/fill_run_bases.py --runs build/package/gut_runs.parquet --out data/inputs/registry/run_bases_ncbi.parquet`
(NCBI E-utilities runinfo, ≈ 10 min for 74 k runs the first time, cached afterwards) so runs with ENA base_count 0 get NCBI bases; re-run it after the release
build when new runs were added (cross-study / infant fallbacks) and rebuild. Target: every catalog sample has `seq_gbp`.

**Analytics (R2026.16).** `config/site.yaml analytics: {provider: goatcounter|ga4|plausible, id: ...}`; the base template injects the provider's script only
when `id` is set. GoatCounter: the owner creates a site at goatcounter.com (code = subdomain) and the dashboard is at https://<code>.goatcounter.com.

**Repository names (2026-09-30).** `infant-gut-catalog` → `microbiome_repo` (site, Pages), `infant-gut-catalog-data` → `microbiome_repo-data`,
`catalog-pipeline` → `microbiome_repo-pipeline`. GitHub redirects the old names for git and web (not for Pages: the site moved to
https://olmlab.github.io/microbiome_repo/ and `config/site.yaml` `base_url` changed with it). The local clones keep their old directory
names (`~/catalog/{catalog-pipeline,infant-gut-catalog-data,infant-gut-catalog}`) and their remotes still point at the old URLs because
`.git/config` is read-only under the host grant — pushes follow the redirect. `deploy-pages.yml` runs on `site-v*` tags only
(a push to `main` on 2026-09-30 deployed a stale 1.2.1 build; `main` in the site repo now holds only `.github/` + README).
Then: `verify.yml` runs on the branch/tag → `deploy-pages.yml` deploys the `site-v*` tag to
https://olmlab.github.io/microbiome_repo/ → `release.yml` builds `data_package_v<semver>.zip` +
`infant_catalog_v<semver>.sqlite` and attaches them to the `data-v<semver>` Release (Zenodo mints the DOI).
Post-check (needs the `olmlab.github.io` grant): fetch `/data/VERSION.json` and compare `release_tag`.
The owner merges `release/<semver>` → `main` at leisure (or `main` is left as the last-known-good pointer).
**Rollback:** Actions → deploy-pages → Run workflow with `ref = site-v<previous>`; data: the previous Release stays.
**Never:** push `main`, force-push, delete tags, embed the token in a URL (docs/SECURITY.md §4).
Until a credential exists: `rsync -a --delete --exclude .git build/site/ ~/Downloads/site/` and the owner pushes.

## 8. Per-cycle budget summary (A14)

Same numbers as NEXT_STAGE.md §7 and `config/budgets.yaml` (`python scripts/budget_calc.py` recomputes both; R1-12). Triage per judged
study is 4.0–7.2 k tokens (measured aggregate vs component build-up), i.e. 0.43–1.03 M for 108–143 studies — the 0.4–0.6 M below is the
measured-aggregate end of that range.

| stage | LLM tokens | wall time | delegation | stop rule |
|---|---:|---|---|---|
| 0 preconditions | 0 | 10 min | no | any hash mismatch / red test |
| 1 re-sweep | 0 | 20–40 min | no | > 300 candidates; slice count off > 5 % |
| 2 triage (≈ 108 studies) | 0.4–0.6 M | 1.5 h | **yes** (leaf workers) | > 30 includes; > 10 % validator-rejected |
| 3 extraction (≈ 6 studies) | ≈ 0.3 M (50 k/study) | 2–3 h | **yes** | study > 5 k samples or > 3 papers → own worker |
| 4 findings | 0 | 5 min | no | any `rejected` row needs a curator look |
| 5 package | 0 | 5 min | no | README/VERSION mismatch |
| 6 site + verify | 0 | 5 min + Actions ≈ 10 min | no | broken links > 0; Playwright fail |
| 7 publish | 0 | 10 min | no | verify-failed Issue opened |
| **cycle** | **≈ 0.8 M (≤ 1.5 M high)** | **≈ 5 h agent time** | | |

Contrast: building the catalog cost ≈ 47.3 M tokens (CHANGELOG); a monthly cycle is ≈ 2 % of that.

## 9. Running a stage in a fresh Claude session — checklist

1. Start from the profile (Curator for 1–4 incl. 1b–1d Sandpiper/authors, Release Engineer for 5–7, Auditor for read-only review).
2. `skill("infant-curation-rules")`; for 1–3 also `skill("infant-catalog-harvest")`.
3. Environment `infantcat` (or `python bootstrap.py` to create it from `environment.yml`). `cd ~/catalog/catalog-pipeline`.
4. In a python cell (env `infantcat`): `__file__ = '<repo>/bootstrap.py'; exec(open(__file__).read()); main(['--no-env', '--only-required'])`
   — the kernel form reaches the artifact store through the kernel `host` global (`make bootstrap-kernel` prints it); the shell
   form `python bootstrap.py --no-env --only-required` only sees `~/catalog/data/`. Exit 4 (required input missing) or 3 (hash
   mismatch) = stop; tests are not run then.
5. Run the stage's `make` target; LLM stages: dispatch leaf workers from the root with `SLICE`/`OUT_PREFIX` and the
   budget caps in `config/budgets.yaml`; save every output as an artifact as soon as it exists (workspaces are wiped).
6. Append the actuals to `docs/CYCLE_LOG.md`; commit code/doc changes to `microbiome_repo-pipeline` on a `cycle/<YYYY-MM>` branch.

## Stage 7 — registry tier (scale-up S1, from R2026.4 / package 1.6.0)

The registry of ALL human shotgun metagenomes (docs/EXPANSION.md; spec `config/scope.yaml` + `config/vocab/*.yaml`, frozen columns
`audit/registry_schema.json`) is rebuilt on every release when `data/inputs/registry/registry_universe_studies.parquet` exists
(`make release` calls `registry-build` into the package dir; without the universe the registry tables are skipped and the site
builds without `registry/`).

1. `make registry-enumerate` — ENA slices S1/S2/S3 without a taxon frame → `data/inputs/registry/registry_universe_studies.parquet`,
   `registry_runs.parquet` (≈ 78 MB, working_data artifact + Release asset), `registry_biosample_index.parquet`, `registry_universe_audit.csv`.
   Add `REGISTRY_SINCE=<date>` for an incremental pull.
2. `make registry-classify-det` — deterministic priors/rules for every study → `build/registry/registry_classification_det.parquet`
   (`needs_llm` flags the studies the LLM stage must see).
3. LLM stage (leaf workers, `src/catalog/registry/run_llm_shard.py`): shard the LLM-eligible studies (`candidate_class ∈ {prior_human,
   signal_human_new, ambiguous_new}` and `needs_llm`) into ≈ 290-study parquet files; each leaf runs
   `run_shard(host, shard, det, out_prefix)` (Sonnet ×2 → Opus adjudication with singleton retries → conservative replicate merge;
   ≈ 2.8 k tokens/study, ≈ 20 min per shard). Concatenate the shard parquets → `REGISTRY_LLM=<path>`. Re-run only the
   `classification_stage == pending` accessions as one extra shard when adjudication failures remain (their rows replace the sentinels).
4. `make release … REGISTRY_LLM=build/registry/registry_llm_classification.parquet` — `registry_studies.parquet`, `registry_universe_audit.csv`
   and `REGISTRY_REPORT.md` land in the package; `package-docs` documents them; `publish-branch` stages `registry_runs_v<version>.parquet`
   under the data clone's `assets/` (attached to the GitHub Release by `release.yml`, never committed under `package/`).
5. Site: `registry/index.html` (facets + DuckDB-WASM explorer over `data/registry_studies.parquet`), `registry/scopes/<id>.html`; the Playwright
   smoke in `verify.yml` opens `registry/index.html` and `contribute/index.html`.
6. Sample tier (S2, from R2026.5 / 1.7.0): BioSample attribute harvest in leaves (pattern: artifact `harvest_pilot.py` + `fields.py`; ENA browser
   XML batches of 100 with 2 workers per leaf, NCBI BioSample `esearch [accn] → efetch` for misses, an identifier guard against same-digit
   substitutions) → `registry_biosample_attributes.parquet` (working_data). Distinct (field, key, value) pairs of age / body_site / sex / country →
   utility-model normalisation in leaves (`normalise_pairs.py`, batches of 40, ≈ 213 tokens per pair, ≤ 1.5 M tokens per leaf — the root frame's
   host.llm ceiling is 2.0 M) → `registry_pairs_normalised.parquet`. `make release` then runs `registry-biosamples` (registry_biosamples.parquet +
   study roll-up / refinement) and `add_registry_tables` (papers / authors / BioProject side tables) when the inputs are in data/inputs/registry/.
   Measured budgets: config/budgets.yaml `registry_s2`; reports docs/REGISTRY_S2_PILOT.md, docs/REGISTRY_S2_REPORT.md, docs/REGISTRY_S2_PAPERS.md.

## Stage 8 — curated scope gut_all (from R2026.7 / package 1.8.0; config/packs/gut.yaml)

The first curated scope beyond the infant catalog: every human gut shotgun-metagenome study in the registry, all ages. Tables
`gut_studies`, `gut_sample_metadata_wide`, `gut_sample_determinations` (same determination schema as the infant tables).
`infant_scope` reproduces the infant catalog's catalog_scope exactly; infant rows are copied verbatim and win.
1. Study list: `data/inputs/gut/gut_studies.parquet` = registry_studies rows matching the pack `study_rule` (+ included infant studies)
   with linked PMIDs from registry_study_papers.
2. Leaves (one wave, in parallel; every leaf reads config/packs/gut.yaml and infant-curation-rules):
   * **R1** — gut attribute field map (config/packs/gut_attribute_field_map.csv: 287 keys → 7 fields, deterministic parsers of
     src/catalog/extraction/r1_parsers.py; utility model for KEY triage only) over the harvested attributes → `gut_r1_determinations.parquet`.
   * **Normalisation** — distinct (key, value) pairs of disease / health / antibiotic keys → `gut_health_condition_map.parquet`,
     `gut_antibiotic_map.parquet` (rules + utility model; config/vocab/health_conditions.yaml) + their R1 expansion.
   * **R4** — title + ENA description + Europe PMC abstracts (≤ 3 PMIDs) → cohort-wide `study_all` statements, utility model, verbatim-quote
     check, confidence ≤ 0.5 (`r4/gut_r4_determinations_shard_*.parquet`, study summaries with design / n_subjects).
   * **R2** — supplementary zips of open-access papers (Europe PMC), exact-accession gate, header-named columns, no model
     (`r2/gut_r2_determinations_shard_*.parquet`, gate tables).
3. `make release` runs `gut-build` (catalog.scopes.build_gut_scope) inside the package: precedence infant catalog > R1 > R2 > R3 > R4;
   `unknown` codes are never rows; pack routes per field enforced; age_category from age → sample life stage → R4 life stage → study
   life stage (basis recorded); body_site_class from the sample attribute or the study's single registry site.
4. Not yet run for non-infant studies: R3 (full-text prose), infant-only fields, Opus group audit of R4 statements (group_audit pending),
   Sandpiper per-sample join. Future fields inventory: data/inputs/gut/gut_future_fields_inventory.csv.
5. **New fields (R2026.12 / 1.12.0; owner site review 2026-09-29)** — `collection_date`, `location_region` / `location_locality` /
   `location_site` (+ derived `detailed_location`), `latitude` / `longitude`, `lifestyle` / `lifestyle_detail` (+ derived
   `collection_year`). The scope builder takes every field list from `config/packs/gut.yaml` (`fields`, `core_fields`, `key_fields`,
   `derived_fields`) — adding a field = one line in the pack + a leaf that emits determination rows for it; the wide table, the
   `cov_<field>` study columns and `n_fields_with_value` (= core + key fields with a value) follow. Vocabulary-typed fields
   (`health_condition`, `lifestyle`) are validated against their `config/vocab/*.yaml` codes (drops counted in the summary).
   * Route R1 leaf: `make gut-newfields-r1` (`catalog.scopes.newfields_r1`; input `GUT_ATTRIBUTES` = the harvested BioSample attribute
     rows of the catalog studies, ≈ 1.8 M rows). Deterministic parsers for dates (YYYY / YYYY-MM / YYYY-MM-DD / DD-Mon-YYYY / Mon-YYYY /
     M/D/YYYY when unambiguous or settled by the study's other values / ISO datetimes / intervals; placeholders, years < 1990 or after the
     run's `first_public` year, impossible dates rejected) and coordinates (`lat_lon`, `latitude_and_longitude`, latitude+longitude key
     pairs; DMS; (0,0), out-of-range and swapped pairs rejected — the country check uses the offline GeoNames cities1000 table of
     `reverse_geocoder`, not Natural Earth). Place strings are normalised on DISTINCT values only by the utility model in batches of 40 and
     every output token is validated against the raw string (substring / documented US-state, Chinese-province, Canadian/Australian
     abbreviation / declared exonym) — the reviewable map is `data/inputs/gut/gut_location_map.parquet` (+ `gut_location_exonym_map.json`);
     lifestyle codes come from lifestyle-stating keys only (`urban`, `rural_urban_status`, `community_type`, exact vegan/vegetarian diet
     values, `tribe` / `population` / `community` with utility-model confirmation; never ethnicity/race) → `gut_lifestyle_map.parquet`.
     Optional `REVERSE_GEOCODE=1` fills `location_region` (never a locality) from ≥ 2-decimal coordinates at confidence 0.6 with
     `parse_note` "reverse-geocoded from lat_lon (GeoNames cities1000)"; centroid-like points (shared by ≥ 3 studies, > 3 km from any
     GeoNames place) and points > 10 km from any place are skipped. Outputs: `gut_r1_newfields_determinations.parquet`,
     `gut_r1_newfields_rejects.parquet`, `gut_r1_newfields_conflicts.parquet`, `gut_r1_newfields_summary.json`. Re-runs reuse the maps
     (`GUT_LOCATION_MAP=… GUT_LIFESTYLE_MAP=…`, 0 tokens). Measured 2026-09-29: 34 utility calls, ≈ 0.26 M tokens.
   * `gut-build` now also writes **`gut_runs.parquet`** (one row per run of a catalog study from `registry_runs` + `registry_runs_sandpiper`,
     with the catalog `sample_key`; run-unit samples keyed by run accession) and the study sequencing summary columns on `gut_studies`
     (`n_runs_total`, `gbp_per_run_mean` / `_median`, `instrument_models_top`, `library_layouts`, `sandpiper_profiled_share`).
     Inputs: `REGISTRY_RUNS`, `REGISTRY_SANDPIPER`, `GUT_R1_NEWFIELDS`. The table is documented from audit/registry_schema.json
     (package-docs) and counted in the release notes.
   * Tests: tests/test_newfields_r1.py (parsers ≥ 40 cases each, place validator, lifestyle rules, gut_runs, wide derivations).
