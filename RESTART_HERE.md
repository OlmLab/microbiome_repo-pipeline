# RESTART_HERE — Microbiome Repo (v16, 2026-11-24, R2026.43 built)

**State**: R2026.43 / 1.34.2 is a site release that applies an external website review (one word per thing, shorter pages, stale
statements and dead links removed, the Taxon map recomputed on the current profiles); the data are those of 1.34.0 (813,356
samples, 5,086 projects, 4,814,518 values). Whether it is published and its DOI: `config/releases.yaml`, entry R2026.43 (a `doi`
line is added after publication). R2026.42 / 1.34.1 (DOI 10.5281/zenodo.24508002, published 24 November 2026) precedes it.
**`~/catalog` is read-only to chat code since 23 November 2026**: R2026.42 and R2026.43 were built in a chat workspace and pushed
from there, so the local clones and `build/` in `~/catalog` are behind GitHub. Read `docs/MONTHLY_RUN.md`, sections "Next run:
carried over from R2026.43" and "…R2026.42" (what is newer than `~/catalog`, where it is, and what the review left for the next
data release) and "When `~/catalog` is read-only to code" (how to build and publish), before anything else. The Editor's report
is `~/catalog/projects/editor/REPORT.md`, its decisions `~/catalog/projects/editor/DECISIONS.md`. The DuckDB self-hosting switch
stays off until the owner confirms that the CU server serves `.wasm` files. Publishing needs the owner at the machine to approve
the cards.

**Where things are**: pipeline `~/catalog/catalog-pipeline` (GitHub OlmLab/microbiome_repo-pipeline), site clone
`~/catalog/infant-gut-catalog` (OlmLab/microbiome_repo), data clone `~/catalog/infant-gut-catalog-data` (OlmLab/microbiome_repo-data);
caches `~/catalog/cache` (`large_update/` = intake stages, `deep_pass/` = this pass); shared notes `~/catalog/projects/`
(`STATUS.md`, `handoffs/COMMON_RULES.md`, `editor/DECISIONS.md`, `editor/REPORT.md`). Frozen copy of 1.31.0 for the other chats:
`~/catalog/frozen_releases/R2026.38_package_1.31.0/` (never change).

**How a release is made**: `docs/MONTHLY_RUN.md` (stages 1–19 and the passes the deep pass added). In short: intake and passes →
`scripts/prepare_catalog_inputs.py --extra-facts-dirs $L/stage13_merged/ $L/stage14_merged/ $L/stage17_deep_pass/` →
`make release BUILD_DATE=<d> PKG_ZIP=build/data_package_v<previous>.zip` → `make taxon-map` → `make measures` → `make taxa` →
`make site BUILD_DATE=<d>` →
`make verify` → compare the live Content-Security-Policy with `config/site.yaml` → `make check-credential` →
`make publish-branch BUILD_DATE=<d>` → copy `verify.yml`, `check-cu-deploy.yml`, `deploy-pages.yml` into the site clone and
`release.yml` into the data clone, amend, re-tag → push pipeline main first, then the site branch and tag, `release-assets`
(force, `GIT_DIR=/tmp/release_assets_<v>.gitdir`), the data branch and tag → check Actions, `version.json` on the CU server and the
Zenodo DOI → record the DOI in `config/releases.yaml`.

**Rules that bind every session**: paper and project text is read only by Opus 5 sub-agents, the main session handles counts,
code and tables of identifiers; memory rules of COMMON_RULES §8 (never load a package table whole; main kernel under 6 GB,
sub-agents under 3 GB; `memory_pressure` at least 35 % free before a build or a wave); every new kind of statement gets an
independent audit sample before it is committed; nothing is committed on a single reading; no `.wasm` file in a release until the
owner confirms the CU server serves them; no pull-request requirement on main.

**Open**: the held steps and the owner items in `~/catalog/projects/editor/REPORT.md`; the lists under "Next run" in
`docs/MONTHLY_RUN.md`.

---

<details><summary>v13 (after R2026.13) — kept for history</summary>

# RESTART_HERE — Microbiome Repo (v13, 2026-09-30, after R2026.13 / package 1.13.0)

**State**: R2026.13 / 1.13.0 published: data release data-v1.13.0 (all assets incl. SQLite 1.26 GB, Sandpiper parts, registry_runs zstd 75 MB) on OlmLab/microbiome_repo-data; site tag site-v1.13.0 deploying to https://olmlab.github.io/microbiome_repo/ (new base_url after the rename). Pipeline main b4e2e0d + later doc commits (push them if `git status` shows them unpushed). Full account: docs/R2026_13_CLEANUP_REPORT.md, docs/OWNER_QUESTIONS_R2026.13.md, docs/CYCLE_LOG.md.

**Repos renamed 2026-09-30**: microbiome_repo (site, Pages), microbiome_repo-data, microbiome_repo-pipeline; old names redirect for git/web. Local clones keep old dir names (~/catalog/{catalog-pipeline, infant-gut-catalog-data, infant-gut-catalog}); remotes still point at old URLs (.git/config read-only) — pushes follow the redirect. Push with `git -c credential.helper= -c "credential.helper=!f() { echo username=x-access-token; echo password=$GITHUB_TOKEN; }; f"` and credential "GitHub".

**What 1.13.0 added**: registry 64,369 studies (+9,959 gap-fill enumeration; slices in data/inputs/registry/gapfill/*.py), catalog 2,887 studies / 582,841 samples (+50 gap-fill studies at registry stage pending, shard 999); new R1 fields diet / smoking_status / medication / stool_consistency_bristol (src/catalog/scopes/newfields_r1_v2.py; vocab config/vocab/{diet,smoking,medication}.yaml; `make gut-newfields-r1-v2`); `--corrections data/inputs/gut/dq_corrections.parquet` hook in build_gut_scope.py (1,932 rows: retire/recode; 35,592 dropped / 457 recoded); single-life-stage rule for study-level age fallback; R3 shard 09 + R3b/R4b completion merged; atlas on official HDI (data/inputs/gut/atlas/country_hdi_official.csv; scripts/atlas_observations.py; 18 cards); Sources acknowledgement.

**Release mechanics (changed)**: `make release BUILD_DATE=<d> REGISTRY_LLM=build/registry/registry_llm_classification.parquet` → `make site` → `make verify` → `make publish-branch BUILD_DATE=<d>` → push release-assets FIRST (`GIT_DIR=/tmp/release_assets_<v>.gitdir GIT_WORK_TREE=/tmp/release_assets_<v> git push --force <data repo> HEAD:refs/heads/release-assets`; the sandbox cannot create .git dirs), then `push origin release/<v> site-v<v>` (site clone) and `release/<v> data-v<v>` (data clone). deploy-pages runs on site-v* tags only; never push to the site repo main except .github/README. release.yml drops SQLite > 1.9 GB and skips gut_sample_determinations/gut_runs in it. Zenodo badge: https://zenodo.org/badge/latestdoi/1389758854 (404 right after 1.13.0 publish — recheck).

**Open**: 5 owner questions (ileal-pouch body site, Hutterite accession, LifeLines-DEEP listing, same-country institute coordinates ~300 studies, pre-1990 date floor); R3b/R4b residue (Europe PMC 500s ~70 studies); 224 gap-fill studies unjudged; reviewer-model audit of R3/R4; lifestyle/diet/medication via R2; oral scope deferred by owner; Zenodo DOI display on site.

**Make pitfalls**: no trailing comments on `VAR ?= value` lines; atlas.build runs before pca.build; contribute worklist columns may be NaN for brand-new studies (handled with pd.isna).

</details>

---

<details><summary>v12 (after R2026.12) — kept for history</summary>

**State**: R2026.12 / 1.12.0 is LIVE (https://olmlab.github.io/infant-gut-catalog/, VERSION.json release_id R2026.12; Actions verify/deploy green; data release data-v1.12.0 published by hand without the SQLite asset). Pipeline main c4e49e1. Full account: docs/R2026_12_SITE_REVIEW_REPORT.md (artifact) and docs/CYCLE_LOG.md.

**What 1.12.0 added** (owner review of 2026-09-29): new fields collection_date / location_region / location_locality / location_site (→ detailed_location) / latitude / longitude / lifestyle (+detail) — pack config/packs/gut.yaml (core_fields = age, sex, country, health_condition, subject_id; key_fields), vocab config/vocab/lifestyle.yaml; routes R1 (src/catalog/scopes/newfields_r1.py; inputs data/inputs/gut/gut_biosample_attributes.parquet + maps), R3b (data/inputs/gut/r3b/{shards,merged}; merge_r3_shards --prefix gut_r3b), R4b (data/inputs/gut/r4b/*.parquet — shards must be directly in r4b/). gut_runs.parquet table + study sequencing summaries. Sandpiper whole-catalog tables data/inputs/gut/sandpiper (filter_gut.py + build_gut_tables.py regenerate from the bulk artifact a970aed8…); PCA page pages/pca.py; Atlas payload data/inputs/gut/atlas (pages/atlas.py precompute; observations.json cards) passed via --atlas-data; collections config/collections.yaml → pages/collections.py + explorer ?collection=. Site restructure: About, merged Downloads, simple issue form (site_generator/gen/issue_templates/simple-finding.yml), registry human-first, sample archive links, ?sample= filter.

**Owner actions pending**: install simple-finding.yml in the Issues repo; install the fixed release.yml (SQLite skip) into infant-gut-catalog-data (PAT lacks Workflows); replace data/inputs/gut/atlas/country_hdi.csv with the official UNDP CSV and rebuild; rename repos; PAT Workflows/Administration; licence contacts (GMrepo, HumanMetagenomeDB, MicrobeAtlas, mBodyMap, AHMP).

**Make pitfalls**: no trailing comments on `VAR ?= value` lines (blanks are kept → globs break); atlas.build replaces data/atlas/ so it runs before pca.build; package zip > 90 MiB is served from the GitHub Release.

</details>

---


<details><summary>v11 (after R2026.11) — kept for history</summary>

# RESTART_HERE — how to start a fresh session on this project with full context
*Use this whenever a session ends, a model becomes unavailable and you switch, or you just want a clean context. Everything a new session needs is in the artifact store and project memory; nothing lives in a session's head.*

## 1. Before you type anything
1. **Project**: make sure the new session is in the same project (“organizing human microbiome metadata”) — project memory (~150 facts) and every artifact are attached to it, and that is 90 % of the context transfer.
2. **Delegation toggle**: switch delegation ON (the toggles icon next to the message box). It is per-session and OFF by default; without it I cannot spawn sub-agents and large jobs will run single-threaded or refuse.
3. **Profile**: leave it on the default (Curator) profile for anything that changes data. Pick *Catalog Auditor* only for read-only “why is this study like this?” questions, *Release Engineer* only for a rebuild-and-publish run.
4. **Model**: any model works; the pipeline pins its own models per role (`config/models.yaml` resolves at runtime, no hard-coded ids), so a session-model change never changes the catalog's judging tier. Heavier session models mainly make the orchestration turns better, not the curation.

## 2. Paste this as the first message (edit the last line)

```
Resume the human-microbiome metadata catalog project. Do not re-harvest and do not recompute anything that already exists.

Setup: delegation is ON. Use environment="infantcat" for analysis cells.

First, load context in this order:
1. skill("infant-curation-rules") and skill("infant-catalog-harvest") — these govern every value you commit.
2. read_memory("project") — the durable decisions and conventions.
3. host.artifacts(filename="RESTART_HERE.md", exact=True) and read it (this file).
4. Read these artifacts by exact filename (take the newest version): NEXT_STAGE.md (organisation, credentials, runbook), SCALE_UP_PLAN.md (all-human-metagenome expansion), MATURITY_PLAN.md (versioned releases + community uploads), REVIEW_FINDINGS_APPLIED.md (open items), CATALOG_REPORT.md and EXTRACTION_REPORT.md (what the catalog is), DATA_DICTIONARY.md.
5. The current release is **R2026.11** = data_package_v1.11.0.zip (GitHub Release data-v1.11.0 on OlmLab/infant-gut-catalog-data, plus the asset registry_runs_v1.11.0.parquet; releases.csv is the registry, RELEASE_NOTES_R2026.11.md the diff report); the site — titled **Microbiome Repo** (names in config/site.yaml; repositories/URL not yet renamed) — is live at https://olmlab.github.io/infant-gut-catalog/ from tag site-v1.11.0. **Since 1.10.0 the site is all-age**: every section (Studies, Cohorts, Samples, Fields, Authors, Scope, Methods, Downloads, Releases, Contribute) is generated from the catalog tables gut_studies / gut_sample_metadata_wide / gut_sample_determinations and the registry_* tables (site_generator/gen/build_site.py v3); there is no infant-gut section any more — the infant-only tables are the 'infant extension' (fields + Downloads group). Tiers: registry (all human shotgun metagenomes) → the catalog = curated scope gut_all (all human gut, all ages; config/packs/gut.yaml; tables gut_*); the former infant catalog = gut_all filtered to infant_scope (its deep tables remain in the package as the infant extension). Read GUT_SCOPE_REPORT.md (R1/R2/R4 build) and GUT_R3_REPORT.md (full-text route R3, R2026.8) for depth and caveats. The project now has TWO tiers (docs/EXPANSION.md, SCALE_UP_PLAN.md): the **registry** of ALL human shotgun metagenomes (registry_studies.parquet, 54,410 ENA studies / 1.98 M runs, archive-only classification; site registry/) and the **curated** infant gut scope (the original catalog, unchanged). Read REGISTRY_LLM_REPORT.md, REGISTRY_S2_REPORT.md and ENUMERATION_REGISTRY_REPORT.md for the registry's deviations. The registry also has a SAMPLE tier (registry_biosamples.parquet, 611,601 BioSamples of the human_all studies with normalised site/age/sex/country) and papers/authors/BioProject side tables. Release model: docs/RELEASES.md + config/releases.yaml in catalog-pipeline (bitemporal columns release_added/release_retired/package_added on every fact table; sample_determinations_all.parquet holds retired values). Cutting the next release = bump config/version.txt, add a row to config/releases.yaml, `make release site verify publish-branch`, push release/<v> + tags. Code of record: https://github.com/OlmLab/catalog-pipeline (main). Local clones under the granted folder ~/catalog/: catalog-pipeline, infant-gut-catalog-data, infant-gut-catalog. Publishing is unattended: credential 'GitHub' (env GITHUB_TOKEN; declare credentials=["GitHub"]), push release/<semver> + site-v*/data-v* tags, Actions verifies and deploys (docs/SECURITY.md §3, §5).

Then confirm in one short message: package version, sample/study/determination counts read from the tables, what the open owner-actions are, and what you understand the next task to be. Do not start work until I reply.

My task for this session: <SAY WHAT YOU WANT>
```

## 3. What a fresh session should NOT do
* Re-enumerate ENA or re-run triage “to be safe” — the infant universe is 9,581 screened studies and the registry universe 54,361 (+49 carried) studies; re-sweeps are monthly jobs with their own targets (`make resweep`, `make registry-enumerate REGISTRY_SINCE=…`). Never re-run the 6,406-study LLM classification (19.5 M tokens) or the gut R1/normalisation/R4/R2 leaves (≈ 7 M tokens) or the gut R3 full-text leaves (≈ 36 M tokens): registry_llm_classification.parquet and data/inputs/gut/* (artifacts gut_r1_determinations, gut_health_condition_map, gut_antibiotic_map, gut_condition_abx_determinations, gut_r4_determinations_shard_0..4, gut_r2_determinations_shard_0..7, gut_r3_determinations_shard_00..23 + completion_shard_0..2, merged gut_r3_determinations_merged.parquet, gut_r3_subgroups_all, gut_r3_unreplicated_all, gut_r3_study_summary_all) are the records; data/inputs is git-ignored, so a fresh clone must restore data/inputs/gut/ from these artifacts before `make release`; curate only NEW studies.
* Re-derive numbers from memory or from an old report: every count must be read from the current package tables.
* Rebuild the site from an older package — always the newest `data_package_v*.zip`.
* Push to GitHub without the credential in place (see NEXT_STAGE §4 / RUNBOOK §0a).

## 4. Current state (2026-09-28, update this line when it changes)
Eleven numbered releases, all published unattended: R2026.1–R2026.3 (infant catalog machinery), **R2026.4** (1.6.0 registry tier), **R2026.5** (1.7.0 registry sample tier), **R2026.6** (1.7.1 rebrand), **R2026.7** (1.8.0 curated scope gut_all + rename Microbiome Repo), **R2026.8** (1.9.0 route R3 full text), **R2026.11** (1.11.0: ten external curated resources — cMD curation tables, GMrepo v3, Meta2DB, HumanMetagenomeDB, MicrobiomeDB, MicrobeAtlas, HMP/iHMP via IBDMDB, mBodyMap, AHMP, BugSigDB — ingested as R2 rows with evidence_source external.<id> and a source-record URL as locator: 428,902 rows → 1,861,883 determinations; health condition 31.0→37.5 %, antibiotics 12.5→16.5 %, age 23.6→26.0 %, sex 28.6→31.0 %; shards in data/inputs/gut/r2/gut_r2_determinations_shard_ext_<id>.parquet, reports/maps/links in data/inputs/gut/external/; docs/GUT_EXTERNAL_INGEST_REPORT.md; Sources page lists incorporated resources), **R2026.10** (1.10.1: Sources & acknowledgements page from config/sources.yaml + docs/RELATED_RESOURCES.md — 15 related metadata efforts assessed for ingestion, cMD 3 and GMrepo v3 rated candidate_high; no data change), **R2026.9** (1.10.0 **all-age site**: generator v3, every section from the gut_*/registry_* tables; duplicate R3/R4 cohort rows removed from gut_sample_determinations, 1,787,854 → 1,729,195 current rows; README intro generated from the tables via docs/package_readme_intro.md; release notes gained Catalog + Registry sections). Catalog: 2,837 studies / 579,252 samples / 1,729,195 values; coverage age 23.6 %, sex 28.6 %, BMI 5.3 %, country 90.1 %, health condition 31.0 %, antibiotics 12.5 %, subject 34.4 %, timepoint 13.6 %; 69 cohorts (172 studies; rule: curated record · shared own-data paper ≤ 4 studies · shared paper with same submitting centre), 17,661 author names over 1,899 studies, contribute worklist 1,266 studies (≥ 50 samples, < 50 % on ≥ 3 of 5 core fields). Registry: 54,410 studies / 1.98 M runs; registry_biosamples 611,601. Site: 2,954 pages, 541 MB (per-study csv.gz/parquet/determinations slices for all 2,837 studies). Infant tables unchanged since 1.5.0.
Machinery (catalog-pipeline main ≥ ceb95b1): site_generator/gen/build_site.py v3 + templates (index, studies_index (DataTables over data/studies_index.json), study, cohorts_index, cohort, explorer (static/explorer.js = DuckDB-WASM over gut_sample_metadata_wide; `?study=`/`?q=`/`?sample=` params; the verify smoke test needs the literal text "N samples match"), fields, scope, authors (authors/idx/<letter>.json = {author_key: {n, s:[studies], f}}), methods, downloads (groups catalog/registry/release/infant), contribute, changes_*); gut/index.html and universe.html are redirects. package_docs.rewrite_readme_intro (Makefile --readme-intro docs/package_readme_intro.md); release_notes has Catalog/Registry sections first; build_gut_scope.expand_study_all keeps one cohort statement per study × field; build_registry.carry_release_columns dedupes previous keys. RUNBOOK §6 describes generator v3. Tests 232 passed.
Open owner actions: **rename the repositories** (GitHub → Settings → rename infant-gut-catalog → e.g. microbiome-repo, infant-gut-catalog-data → microbiome-repo-data, catalog-pipeline → microbiome-repo-pipeline; then edit config/site.yaml base_url + github.repos + contribute.yaml issue_form.repo + ISSUE_REPO in build_site.py, the three clone remotes redirect automatically) or grant the PAT *Administration* so the agent can; optional Zenodo toggle; PAT Workflows permission; decide whether to review the 55 content-filter-blocked R3 studies by hand.
Known limits: cohorts for non-infant studies rest on registry paper links (relation own_data is loose — re-analysis papers appear as own_data on large studies); Sandpiper panels exist only for infant studies (registry n_runs_sandpiper shown for the rest); no Opus group audit on R3/R4 statements; R3 read one paper per study (362 truncated chunks; shard 09 = 55 studies not read); case-control cohorts unknown at sample level (2,754 subgroup statements in gut_r3_subgroups_all); diet/smoking/medication not curated; registry limits as before (43 pending, 1,264 unresolvable BioSamples).
Next candidates: (0) external-resource follow-ups: refresh cadence (GMrepo live API, BugSigDB dump), review the 102 PRJNA50637 pouchitis-vs-cancer disagreements, MicrobeAtlas flagged-study list, licence contacts (GMrepo, HumanMetagenomeDB, MicrobeAtlas, mBodyMap, AHMP publish no licence); (a) Opus group audit of R3/R4 statements + R3 re-run for truncated-chunk studies; (b) per-sample Sandpiper profiles for all catalog studies (Sandpiper snapshot join, currently infant only); (c) subgroup statements → samples via naming patterns; R2 for docx/pdf supplements; (d) monthly cycle (October) for registry + catalog (new studies only); (e) diet / smoking / medication vocabularies; (f) next scope: oral (565 registry studies); (g) rename repos when the owner decides; (h) MATURITY_PLAN §3.3 upload backend.

</details>
