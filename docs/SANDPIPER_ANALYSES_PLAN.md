# Sandpiper join: sync status, analyses to display, lessons from Metappuccino (2026-11-06)

## 1. Sync status of the Sandpiper join (package 1.29.1)

| check | result |
|---|---|
| Sandpiper version used | 2.0.0 (Zenodo 20419175, 2026-05-28). Newer: 2.0.1 GTDB profiles and 2.1.0 GlobDB profiles (record 21287531, 2026-07-10) |
| catalog samples with a profiled run (2.0.0) that are missing from the Sandpiper tables | 2 of 414,970 |
| gain from 2.0.1 | +11,233 profiled runs, +10,488 samples (414,970 → 425,458 = 52.5 % of 810,820); no run lost |
| Sandpiper rows whose runs are not current profiled catalog runs | 8,202 samples: 6,235 have no rows in `catalog_runs`, 1,967 have runs none of which is flagged profiled |
| `catalog_runs` gap (found during this check) | 38,158 catalog samples in 343 projects have no run rows although their runs (WGS / METAGENOMIC, same project and BioSample) are in `runs_source.parquet`; rebuilding the run table from the final tables keys 42,101 runs to 38,093 of them. Supplement for the frozen copy: `~/catalog/frozen_releases/R2026.35_package_1.29.1_supplement/` |
| shipped `CATALOG_SANDPIPER_REPORT.md` | text describes the catalog of package 1.11.0 (2,837 projects); the tables are newer |

Next run: (1) find where `build_gut_scope` loses those runs (the function keys them correctly on the final tables, so the loss is in
the inputs it is called with) and add a release check `every sample with n_runs > 0 has a catalog_runs row`; (2) `make
sandpiper-refresh SANDPIPER_VERSION=2.0.1 ZENODO_RECORD=21287531` (GTDB profiles; GlobDB profiles as a second taxonomy later);
(3) regenerate the Sandpiper report and tables against the current catalog (`make sandpiper-delta`), which also drops the 8,202
mismatched rows; (4) add a release check that every Sandpiper row's runs are current catalog runs.

## 2. Analyses to compute and display (live-updating with each release)

Data available now (QC-clean profiled samples: 374,142 in 2,825 projects; genus level; counts below are QC-clean profiled samples
that also carry the field):

| field | samples / projects |
|---|---|
| body site labelled | 348,164 / 2,631 |
| country | 355,875 / 2,558 · latitude/longitude 142,718 / 995 |
| exact age | 91,316 / 674 · infant gut (< 3 y) 30,404 / 175 |
| health condition (not healthy) | 108,203 / 1,230 · healthy controls 65,949 / 823 |
| sex | 102,512 / 773 · BMI 25,613 / 211 · lifestyle 30,437 / 222 · diet 8,926 / 62 |
| antibiotic exposure (any value) | 67,983 / 527 (yes: 10,137 / 150) |
| intervention arm | 28,440 / 278 |
| delivery mode / feeding / preterm (infants) | 12,795 / 68 · 7,976 / 56 · 15,425 / 85 |
| people with ≥ 2 profiled samples | 33,189 people (146,277 samples, 1,093 projects); with a time axis 16,254 people (74,260 samples) |
| families with a mother and a child profiled | 2,462 |
| sequencing depth (Gbp) | 381,796 / 2,820 · collection year 309,760 / 2,308 |

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
   coverage gain for sex (29 %), helped by the 7,109 new subject ids in 1.29.0.
5. **Identifier columns**: UBERON for body site, MONDO / Disease Ontology for health condition, ISO 3166 for country, beside the
   current values.
6. **Published validators and a rejection log** shipped with each release; **confidence calibration** per field from the gold set.
7. **Library selection / rRNA depletion** field for metatranscriptomes (the paper's keyword dictionary, Table 2, is reusable).
8. **Bulk contribution template** (TSV keyed on run or sample accession) beside the per-project issue form.
9. Framing number for the manuscript: the paper finds 71.6 % of NCBI API run fields empty; compute the same for human metagenomes.
