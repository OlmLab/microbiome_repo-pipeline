# Sandpiper join: sync status, analyses to display, lessons from Metappuccino (2026-11-06)

## 1. Sync status of the Sandpiper join (package 1.29.1)

| check | result |
|---|---|
| Sandpiper version used | 2.0.0 (Zenodo 20419175, 2026-05-28). Newer: 2.0.1 GTDB profiles and 2.1.0 GlobDB profiles (record 21287531, 2026-07-10) |
| catalog samples with a profiled run (2.0.0) that are missing from the Sandpiper tables | 2 of 414,969 |
| gain from 2.0.1 | +4,347 profiled runs, +4,257 samples (414,969 → 419,226 = 51.7 % of the 810,820 current samples); no run lost |
| Sandpiper rows of samples no longer in the catalog | 6,235 (samples retired in earlier releases; their rows were never dropped) |
| current samples in the Sandpiper tables none of whose runs is flagged profiled | 1,967 |
| `catalog_runs` | complete for current samples: 35 current samples (2 projects) have no run, and the archive records none. (An earlier count of 38,158 "samples without runs" wrongly included the wide table's 54,709 retired rows.) |
| shipped `CATALOG_SANDPIPER_REPORT.md` | text describes the catalog of package 1.11.0 (2,837 projects); the tables are newer |

Next run: (1) `make sandpiper-refresh SANDPIPER_VERSION=2.0.1 ZENODO_RECORD=21287531` (GTDB profiles; GlobDB profiles as a second
taxonomy later); (2) re-derive the Sandpiper tables and report against the current samples only (drops the 6,235 retired rows and
re-checks the 1,967); (3) release checks: every Sandpiper row is a current sample whose runs are current profiled catalog runs, and
every current sample with archive runs has `catalog_runs` rows. Count only current rows (`release_retired` null) in every check.

## 2. Analyses to compute and display (live-updating with each release)

Data available now (QC-clean profiled current samples: 369,036 in 2,814 projects; genus level; counts below are QC-clean profiled
current samples that also carry the field):

| field | samples / projects |
|---|---|
| body site labelled | 341,499 / 2,624 |
| country | 344,129 / 2,547 · latitude/longitude 135,154 / 987 |
| exact age | 90,226 / 672 · infant gut (< 3 y) 30,394 / 172 |
| health condition (not healthy) | 102,601 / 1,230 · healthy controls 64,447 / 822 |
| sex | 99,391 / 771 · BMI 25,340 / 211 · lifestyle 30,376 / 222 · diet 8,863 / 61 |
| antibiotic exposure (any value) | 66,660 / 526 (yes: 10,022 / 149) |
| intervention arm | 27,951 / 278 |
| delivery mode / feeding / preterm (infants) | 12,795 / 68 · 7,976 / 56 · 15,425 / 85 |
| people with ≥ 2 profiled samples | 35,115 people (124,113 samples, 909 projects); with a time axis 20,096 people (72,240 samples). People are (study_accession, person_key) pairs — person_key is unique only within a project |
| families with a mother and a child profiled | 2,578 ((study_accession, family_id) pairs) |
| sequencing depth (Gbp) | 368,675 / 2,809 · collection year 299,052 / 2,298 |

Rules for every analysis: one value per person per stratum (or mixed models with person and project random effects); contrasts
inside projects pooled by random-effects meta-analysis, never raw pooled samples across projects; QC-flagged samples excluded;
n shown on every display; genus level only (no strain claims); recomputed each release with "changed since last release".

**Live measures (dashboards)**

1. **Profiling coverage tracker** — share of catalog samples with a Sandpiper profile by body site, country, year deposited and
   release; the largest unprofiled projects; the Sandpiper version gap. Display: stacked bars + table.
2. **Label versus composition agreement** — a classifier trained on genus profiles of confidently labelled samples predicts body
   site and life stage; per-release agreement rate and a list of discordant samples (stool with an oral profile, "adult" with an
   infant profile). Doubles as a metadata QC queue for Opus checks. Display: confusion matrix + flagged-project list.
3. **Known versus unknown microbiome** — `known_species_fraction` by country, body site and lifestyle: where reference genomes
   still miss most of the community; moves as GTDB / GlobDB grow. Display: choropleth + per-site distributions.
4. **Depth and quality panel** — prokaryotic coverage against Gbp per sample; share of samples deep enough for species-level work;
   QC-flag rates (low depth, low complexity, non-metagenome) per project. Display: scatter (density) + project table.

**Statistical analyses**

5. **Infant gut maturation** — genus abundance and Shannon diversity against exact age (birth to 3 years), by delivery mode,
   feeding and preterm status, project as a random effect. Display: smoothed curves with intervals (log age axis).
6. **Lifespan diversity curve** — Shannon against exact age, birth to 100 years, per body site. Display: curves per site.
7. **Geography and lifestyle gradient** — Prevotella / Bacteroides balance and diversity against country HDI, latitude and
   lifestyle, as a meta-regression over projects. Display: map + forest-style plot.
8. **Disease signatures across studies** — per condition, case versus healthy-control differences inside each project (Shannon,
   genus log-ratios) pooled by random-effects meta-analysis with heterogeneity (I²). Display: forest plots, condition × genus heatmap
   of pooled effects with the number of projects per cell.
9. **Within-person stability over time** — Bray–Curtis distance between a person's samples against days apart, with the
   between-person distance as reference; per body site and life stage. Display: distance-decay curves.
10. **Family resemblance** — profile similarity of mother–infant, twin and sibling pairs against unrelated pairs matched on project,
    age and site. Display: paired distributions.
11. **Intervention and antibiotic checks** — probiotic arms: change of the administered genus (Bifidobacterium, Lactobacillus …)
    against control arms; antibiotic exposure: diversity drop and recovery. Also validates the curated arm labels. Display: per-trial
    effect plots.
12. **How much is technical** — variance in community composition explained by project, instrument and depth against body site,
    age and country (PERMANOVA on a balanced subsample). Tells users how much harmonisation cross-study work needs. Display: bars.
13. **Sex and BMI associations** — adjusted within-project contrasts, pooled. Display: forest plots.
14. **Body-site signature taxa** — prevalence and specificity of each genus across body sites. Display: searchable heatmap.

Build order (most value per effort): 1, 2, 3, 4 (no modelling), then 5, 8, 9, 11.

## 3. Lessons from Metappuccino (Hak et al., Bioinformatics 2026, btag166) — read by Opus 5

The paper reconstructs SRA RNA-seq metadata for cancer research (rule-based extraction, validators, one fine-tuned adapter per
field, ontology normalisation). It is evaluated mostly on synthetic text plus 500 hand-annotated real records. Microbiome Repo
already goes further on reading papers and supplements, per-value evidence, and benchmarking against curated resources. Worth
adopting (the reader's 22 lessons, ranked):

1. **Benchmark design for the owner's hand comparison**: report coverage and accuracy-given-a-value separately with n per field;
   add majority-class baselines ("stool", "adult", "healthy" score high); classify errors as abstention (missing) or substitution
   (wrong value) and list the commonest wrong values per field.
2. **An out-of-reference gold set**: randomly drawn projects and samples outside curatedMetagenomicData / GMrepo / Meta2DB, which
   favour well-annotated stool cohorts.
3. **Cross-route agreement**: flag samples where two routes give different values for one field and send conflicts to the Opus
   check; set route precedence per field from the benchmark, not one global order (cf. the person / time precedence fix in 1.29.1).
4. **Subject invariants**: once sex, country or date of birth is known for a person, apply it to all that person's samples — direct
   coverage gain, grouping by (study_accession, person_key): sex +2,125 samples (46 projects; no person has conflicting sex),
   country +585 samples; 263 people in 9 projects whose samples disagree on country go to a check instead. (An earlier estimate
   of +18,341 grouped by person_key alone and mixed people from different projects.)
5. **Identifier columns**: UBERON for body site, MONDO / Disease Ontology for health condition, ISO 3166 for country, beside the
   current values.
6. **Published validators and a rejection log** shipped with each release; **confidence calibration** per field from the gold set.
7. **Library selection / rRNA depletion** field for metatranscriptomes (the paper's keyword dictionary, Table 2, is reusable).
8. **Bulk contribution template** (TSV keyed on run or sample accession) beside the per-project issue form.
9. Framing number for the manuscript: the paper finds 71.6 % of NCBI API run fields empty; compute the same for human metagenomes.
