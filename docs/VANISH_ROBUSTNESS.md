# VANISH / BloSSUM — the response to Ibata et al. 2026 on the page (round 5, 2026-11-23)

What this adds to the VANISH/BloSSUM page: six plain-language sentences answering a published critique of how
VANISH and BloSSUM lists are made and read (Ibata et al., bioRxiv 2026.11.17.767315, doi
10.64898/2026.11.17.767315, not peer reviewed), and a collapsed **Robustness** section with three exploratory
checks and a list of three checks that are not done.

**Nothing in the analysis changed.** No class, threshold, permutation result or stored number was touched.
The stages `obs`, `perm`, `classes` and `simple` of `vanish_blossum.py` were not rerun; `results.csv`,
`simple_results.csv`, `analyses.csv`, `groups.csv`, `cohorts.csv`, `calibration.csv`,
`permutation_classes.csv`, `carter_comparison.csv`, `published_comparison.csv`, `simple_analyses.csv` and
`simple_countries.csv` of the stored 1.31.0 run are byte-identical after this work (checked by `diff` and
md5; `results.csv` d52639e22ee8b1d02fcff61fb10b5704, `simple_results.csv`
2b67f012bbf42a2f9d42c847a6a6fc53).

## Files

| File | Goes to | Changed |
|---|---|---|
| `vanish_robustness.py` | next to `vanish_blossum.py` (it imports it) | new |
| `templates/atlas_vanish.html` | `site_generator/gen/templates/` | the six sentences, the Robustness section, `_insights_nav.html` |
| `pages/vanish.py` | `site_generator/gen/pages/` | copies and renders the two new payload files; breadcrumb "Insights" |
| `static/sp_vanish.js` | `site_generator/gen/static/` | **unchanged** (byte-identical to the round-4 file: the new block and section are server-rendered markup, nothing is fetched in the browser) |
| `static/side_pages.css` (taxon_explorer) | `site_generator/gen/static/` | **unchanged** — no new style was needed; the block and the section use the page's own `small`, `num`, `sp-note` and `details.sp-more` |
| `tests/dom_vanish.mjs` | tests | 28 new checks (42 → 70) |
| `tests/verify_robustness.py` | tests | new: the independent DuckDB check of the numbers |
| `templates/_insights_nav.html` | preview only | one-line stand-in, so `preview_vanish.py` can render the page alone; the site generator ships the real one |

## The two command lines

Both stages read a finished run's output directory and write into `<vanish-out>/page/`. They are independent
of each other and of `--stage page`; run `--stage page` first or afterwards, it does not matter (the page
stage writes into the same folder and deletes nothing).

```
python scripts/vanish_robustness.py --stage sentences \
  --vanish-out  <run out dir> \
  --package     <release>/package \
  --sandpiper-dir <release>/sandpiper_profiles \
  --hdi         data/inputs/gut/atlas/country_hdi.csv \
  --external-labels <this folder>/external_labels_carter_s4.csv \
  --threads 10

python scripts/vanish_robustness.py --stage checks \
  --vanish-out  <run out dir> \
  --package     <release>/package \
  --sandpiper-dir <release>/sandpiper_profiles \
  --hdi         data/inputs/gut/atlas/country_hdi.csv \
  --external-labels <this folder>/external_labels_carter_s4.csv \
  --threads 10 --memory-limit 2GB
```

`--external-labels` defaults to `external_labels_carter_s4.csv` next to the script, so it can be left out when
the script sits in this folder. `--hdi` **must be the HDI table the run itself used**: both stages rebuild the
run's groups and the simple rule's subjects and stop with an explicit error if what they rebuild does not
match `groups.csv` / `simple_analyses.csv`. For the stored 1.31.0 run that file is
`track_a/v2/hdi_as_of_round2.csv` (the current table with the three values filled in on 2026-11-14 left
blank); for the Editor's own rerun it is the current `country_hdi.csv`, and everything matches by
construction.

As run here, on the stored 1.31.0 run (`track_a/v2/out`, package 1.31.0 / R2026.38, profiles from
`frozen_releases/R2026.38_package_1.31.0/sandpiper_profiles`, 10 threads):

| Stage | Wall clock | Peak RSS | Output |
|---|---|---|---|
| `sentences` | 0.6 s (1.4 s including interpreter start) | 0.45 GB | `page/robustness_sentences.json`, 2,827 bytes |
| `checks` | 9.6 s (10.4 s) | 1.83 GB | `page/robustness.json`, 44,124 bytes; `robustness_lifestyle.csv` 317,165 B (2,016 rows); `robustness_cohort.csv` 73,390 B (358 rows); `robustness_depth.csv` 245,334 B (1,790 rows) |

No CSV reached the 500 kB mark, so none is gzipped. The target was under 15 minutes for the checks stage and
under 3 GB for the whole script; the measured figures are 9.6 s and 1.83 GB. The DuckDB connection is opened
with `SET memory_limit='2GB'`, `preserve_insertion_order=false` and a temp directory inside `_work/`; the
per-sample profile tables (205 MB and 224 MB) are never read whole — they are streamed with a join on the
subjects' samples and a filter on the classed taxon names, and every cross-tabulation is done in SQL. Both
stages are deterministic: there is no sampling anywhere, the depth strata are cut by rank on an explicit sort
key, and every output is sorted before it is written.

## The six sentences, with the 1.31.0 numbers filled in

Taken verbatim from the rendered preview page. Where the assessment gave a number, the template takes it from
`robustness_sentences.json`, so the Editor's rerun will print its own.

**S1 — with the class definition, in "Both rules in plain words", after the model rule's thresholds.**

> VANISH and BloSSUM here are names for a **difference in how often a taxon is detected**, not for absence.
> Every one of the 71 VANISH species on this page is detected in the industrialized reference group — in a
> median of 536 of the 27,406 reference samples, and in a median of 201 of the 471 reference cohorts; the
> rarest is detected in 1. The 37 BloSSUM species are likewise all detected in the non-industrialized group,
> in a median of 47 of its 675 samples.

**S2 — beside the simple rule's description, in the same section.**

> The simple rule compares subjects by the development index of their country with Fisher's exact test. It
> makes **no adjustment for sequencing depth** and **does not account for which study a subject came from**;
> the model rule does both (a depth covariate, a cohort random effect, cohort-label permutations and a
> leave-one-cohort-out check). Where the two rules disagree, the model rule is the one to trust.

**S3 — in "The groups, and where their labels come from", under the description of the group.**

> The non-industrialized group **pools several ways of living**. Its 675 adult samples divide by the lifestyle
> value they carry in the catalog into 289 indigenous community, 165 rural non-industrialized, 117 no value
> recorded (external label only), 64 traditional agriculturalist, 26 hunter-gatherer and 14 pastoralist, and
> they come from 15 cohorts in 12 countries. These ways of living differ from one another as well as from
> industrialized populations, so part of a taxon's score may reflect the shift to agriculture rather than
> industrialization. Contrast C3, which takes stated catalog labels only (393 samples, 7 cohorts, in the
> sensitivity table below), is the narrower comparison.

**S4 — in the headed block "How to read these lists", under the plot's note lines.**

> A taxon counts as **detected** in a sample when the profile assigns it any relative abundance above zero.
> The smallest non-zero value these profiles record is 0.1 % of a sample's species profile and 0.0009 % of its
> genus profile. Nothing on this page has been recomputed at a stricter detection threshold.

**S5 — in the same block.**

> The reference group is every quality-checked gut sample from a high-development-index country whose
> lifestyle is unrecorded or urban-industrialized — 27,406 samples from 471 cohorts. Public archives are rich
> in case-control disease studies, so this group holds both healthy people and patients: 12,175 of those
> samples (44 %) carry a recorded health condition other than healthy control, 7,841 are recorded as healthy
> controls and 7,390 carry no value at all. They are not separated here.

**S6 — in "Limits", after the existing paragraph.**

> All results are statements about taxa that GTDB R232 contains and that SingleM's marker genes can see:
> 24,322 species appear in these gut profiles at all, and 469 of them met the testing rule of the model-based
> analysis for adults against high-HDI countries. They are not statements about strains, about functions, or
> about lineages that no reference catalogue holds.

The block under the plot closes with one line of signposting ("What the two names do and do not mean, what the
non-industrialized group pools, and what these lists cannot cover are in the sections below."), because four
of the six sentences sit in collapsed sections.

### Every number: what it is and where it comes from

All of the following are for the page's headline analysis — species level, contrast C1 (the broad
non-industrialized group against high-HDI countries), adults — and all are written to
`page/robustness_sentences.json`.

| Number (1.31.0) | JSON key | Definition | Computed from |
|---|---|---|---|
| 71 VANISH species | `s1.n_vanish` | rows with `class == "VANISH"` | `results.csv` (level=species, contrast=C1, stratum=adult) |
| none absent (71 of 71 detected) | `s1.n_vanish_absent_from_ref` = 0 | rows with `n_pos_ref <= 0`; when this is not 0 the template switches to a second wording that states how many are absent | `results.csv`, `n_pos_ref` |
| median 536 reference samples | `s1.med_samples_ref` | median over the VANISH rows of `n_pos_ref`, the number of reference samples in which the taxon is detected | `results.csv` |
| of 27,406 | `s1.n_ref` | the contrast's reference samples | `analyses.csv`, `n_ref` |
| median 201 reference cohorts | `s1.med_cohorts_ref` | median of `cohorts_ref_detected` | `results.csv` |
| of 471 | `s1.cohorts_ref` | `cohorts_ref` | `analyses.csv` |
| the rarest, 1 sample | `s1.min_samples_ref` | minimum `n_pos_ref` | `results.csv` |
| 37 BloSSUM species, median 47 of 675 | `s1.n_blossum`, `s1.med_samples_ni`, `s1.n_ni` | the mirror quantities: `n_pos_ni` over the BloSSUM rows, and `n_ni` | `results.csv`, `analyses.csv` |
| 675 non-industrialized samples; 289 / 165 / 117 / 64 / 26 / 14 by lifestyle | `s3.n_ni`, `s3.lifestyles` | the run's own selection for C1 adults, reproduced by calling `select()` of `vanish_blossum.py` on `_work/samples_clean.parquet`, then grouped by the sample's `lifestyle` value, or "no value recorded (external label only)" where it has none | `_work/samples_clean.parquet`, `--external-labels`, `--hdi`; verified against `groups.csv` |
| 15 cohorts, 12 countries | `s3.cohorts_ni`, `s3.countries_ni` | distinct `cohort` and `country` of those samples | same |
| 393 samples, 7 cohorts (C3) | `s3.c3_n_ni`, `s3.c3_cohorts_ni` | contrast C3's non-industrialized group | `groups.csv` |
| 0.1 % species, 0.0009 % genus | `s4.floors[].min_relabund_pct` | 100 × the smallest `relabund > 0` in the profile rows of the samples this run selected | `_work/species_long.parquet`, `_work/genus_long.parquet` |
| 27,406 samples, 471 cohorts (reference) | `s5.n_ref`, `s5.cohorts_ref` | as above | `analyses.csv` |
| 12,175 with a recorded condition (44 %), 7,841 healthy controls, 7,390 with no value | `s5.n_condition`, `s5.n_healthy`, `s5.n_no_value` | `health_condition` of the reference samples of C1 adults: "healthy_control", null, or one of 24 other values; joined on `sample_key` with the same `release_retired IS NULL AND sample_type = 'specimen'` filter the run's stage 1 uses, so the matched total is exactly 27,406 | `--package` `catalog_sample_metadata_wide.parquet` |
| GTDB R232 | `s6.taxonomy_version` | the distinct `taxonomy_db` and `taxonomy_version` of the profile table (one value each) | `--sandpiper-dir` `gut_sandpiper_sample_species.parquet` |
| 24,322 species in the profiles | `s6.n_species_profiled` | distinct species names in the whole gut profile table (the run stored them in `_work/taxa_species_full.parquet`; recounted from the profile table when it did not) | `_work/taxa_species_full.parquet` |
| 469 tested | `s6.n_species_tested_model` | `n_tested` of the headline analysis | `analyses.csv` |

## The three quick checks

All three are labelled exploratory on the page: they were run after the classes were fixed, they were not
pre-specified, and no class depends on them. Per-taxon rows are in the CSV beside each check; the page shows
summaries only.

### 1. Each classed taxon's prevalence, split by lifestyle (`robustness_lifestyle.csv`)

* **Samples.** The 675 non-industrialized samples of contrast C1, adults — the run's own selection, rebuilt by
  calling `group_label()` and `select()` of `vanish_blossum.py` on `_work/samples_clean.parquet` and verified
  against `groups.csv` (675 samples / 15 cohorts, 27,406 / 471).
* **Strata.** The sample's catalog `lifestyle` value; samples that are in the group only through Carter's
  external label and carry no catalog value form a sixth stratum. A stratum with fewer than `MIN_CELL` = 10
  samples would not be judged; all six clear it.
* **Taxa.** Every taxon classed VANISH, BloSSUM or StIL in C1/adults at species, genus and family level
  (336 taxa; 2,016 taxon × stratum rows).
* **Statistic.** The prevalence of the taxon in the stratum — detected (`relabund > 0`) in how many of its
  samples, read from `_work/<level>_long.parquet`, the profile rows of the samples the run selected.
* **"Holds".** For VANISH, the stratum prevalence is at least `D_min` = 0.10 above the reference group's mean
  per-cohort prevalence (`prev_ref_cohort_mean` in `results.csv`); for BloSSUM, at least 0.10 below it; for a
  StIL candidate, both sides at or above `stil_min_prev` = 0.20. `D_min` and `stil_min_prev` are read from
  `analyses.csv`, so the bar is the class rule's own.

**Headline on 1.31.0.** The classes do **not** hold uniformly across lifestyles. At species level 6 of the 71
VANISH species clear the margin in all six lifestyles and the median VANISH species clears it in three; none
fails in every lifestyle. Per stratum, VANISH species clearing the margin: 23 of 71 in indigenous community
(289 samples), 39 in rural non-industrialized (165), 71 in the external-label-only stratum (117), 60 in
traditional agriculturalist (64), 33 in hunter-gatherer (26), 24 in pastoralist (14). BloSSUM is steadier:
15 of 37 species in all six, and 29–37 in every stratum except pastoralist (20). At genus level 13 of 116
VANISH and 29 of 52 BloSSUM genera clear it everywhere; at family level 4 of 28 and 5 of 8. The two smallest
strata (14 and 26 samples) resolve prevalence only in steps of 7 and 4 points, so a failure there is weak
evidence — this is said on the page.

### 2. The simple rule, stratified by cohort (`robustness_cohort.csv`)

* **Subjects.** The simple rule's own subject table (METHOD v2 section B), rebuilt from
  `_work/samples_clean.parquet` and the HDI table with the same SQL shape the `simple` stage uses, and
  verified against `simple_analyses.csv`: adults 83,515 industrialized / 2,837 non-industrialized / 15,889
  transitional, infants 16,104 / 3,125 / 1,172. Transitional subjects are not tested, as in the rule.
* **Strata.** The subject's cohort — the cohort (study group) most of their samples in that age group come
  from, ties by cohort name. A cohort can contribute to the test only if it holds subjects of both bands.
* **Taxa.** Every taxon classed by the simple rule: 358 level × age group × taxon rows (adults 22 VANISH /
  6 BloSSUM / 43 StIL species, 33 / 15 / 96 genera, 6 / 2 / 41 families; infants 18 / 0 / 12 species,
  10 / 0 / 30 genera, 3 / 0 / 21 families).
* **Statistic.** Cochran–Mantel–Haenszel over the contributing cohorts
  (`statsmodels.stats.contingency_tables.StratifiedTable`: `oddsratio_pooled` and `test_null_odds` with the
  continuity correction), beside the pooled (Haldane-corrected) odds ratio of the whole 2×2 table. Carriage is
  the rule's own: a subject carries a taxon when it is detected in any of their samples.
* **"Holds".** Two ways, both reported. For the stratified test: the Mantel-Haenszel odds ratio keeps the
  direction of the pooled one and CMH p < 0.05. For the cohort-dependence figure beside it: the cohort that
  contributes the most carriers in a band is left out of that band and the rule's prevalence thresholds
  (≥ 20 % in one band, ≤ 5 % in the other) are applied again. The direction column is left blank for StIL
  candidates, whose rule asks for the two bands to be *alike*, so the sign of their odds ratio says nothing.
* **Internal check.** The carrier counts this stage recomputes from the profile tables match `pos_ind` and
  `pos_nonind` in `simple_results.csv` for all 358 classed taxa (`cohort.pooled_mismatches` = 0). A mismatch
  would mean the profile tables given are not the ones the run used; the script logs a warning naming the
  count.

**Headline on 1.31.0.** The simple rule cannot really be stratified by study: only **5 of the 642 cohorts that
contribute adult subjects hold subjects of both development bands** (913 of 86,352 subjects, 1.1 %; infants
5 of 189 cohorts, 673 of 19,229). The industrialized and non-industrialized subjects come from different
studies, so the pooled comparison is a comparison *between* studies — which is the critique's point, and the
check's main finding. On the 913 subjects that do allow a within-study test, all 22 adult VANISH species keep
the direction of the pooled comparison and all 22 reach CMH p < 0.05. The cohort-dependence figure is more
informative: one cohort, PRJNA1157371, is the largest contributor of non-industrialized carriers for all 22 of
them, with a median 41 % of the carriers, and with it left out **12 of the 22 still meet the rule's prevalence
thresholds and 10 do not**. At genus level only 4 of 33 VANISH genera survive the same removal (median share
53 %); the 6 BloSSUM species lose 1 of 6, and the StIL candidates, which rest on common taxa, lose 7 of 43.

### 3. The simple rule, inside strata of sequencing depth (`robustness_depth.csv`)

* **Subjects.** The same subject table.
* **Strata.** Five equal-sized strata per age group, cut by rank on the subject's total `root_coverage_sum`
  over their samples in that age group (total, because carriage counts a taxon detected in any one sample);
  ties broken by subject key, so the cut is deterministic. Adults: 17,271 / 17,270 / 17,271 / 17,270 / 17,270
  subjects, of which 678 / 266 / 635 / 860 / 398 non-industrialized.
* **Taxa.** The same 358 classed rows.
* **Statistic.** Inside each stratum, the 2×2 table of carriage by band, Fisher's exact test (two-sided), and
  BH over the classed taxa of that level and age group within the stratum. That q is **not** the run's q,
  which was computed over every tested taxon; the page says so.
* **"Holds".** The rule's own prevalence thresholds met inside the stratum (≥ 20 % in the band where the taxon
  should be common, ≤ 5 % in the other; for StIL, ≥ 20 % in both and |lfc| < 1) **and**, for VANISH and
  BloSSUM, q < 0.05 within the stratum. Both are reported separately in the CSV
  (`thresholds_met`, `holds`).

**Headline on 1.31.0.** The two bands differ little in depth — the median non-industrialized adult subject has
1,506 of root coverage against 1,287 for the median industrialized one (infants 1,316 against 868) — but the
classes move with depth all the same. Averaged over the 22 adult VANISH species, carriage in the
non-industrialized band rises from 13 % in the shallowest fifth to 32 % in the middle fifth, and carriage in
the industrialized band from 1.4 % in the shallowest to 3.7 % in the deepest. VANISH species meeting the rule
per fifth: 2, 13, 21, 22, 14 of 22. In the shallowest fifth 20 of the 22 fall below the 20 % floor; in the
deepest, 4 cross the 5 % ceiling. **2 of the 22 meet the rule in all five strata** (median 3). At genus level
none of the 33 does (median 3); for infants the median VANISH species meets it in 1 of 5 and one meets it
nowhere. The test is never the binding constraint: the count of taxa meeting the thresholds and the count
also clearing q < 0.05 are identical in every row of the summary, so what fails is prevalence, not power.

### Not yet done (listed on the page, one line each)

* A detection-threshold sweep at 0.01 % and 0.1 % relative abundance instead of "above zero" — would show
  whether a class rests on marginal detections. (These profiles already record nothing below 0.1 % at species
  level, so it would bite hardest at genus and family level; the page says this.)
* A hunter-gatherer-only contrast (NI cut back to hunter-gatherer and indigenous community) — would show how
  much of a class is the shift to agriculture rather than to industrialization. Needs its own permutations and
  leaves a small group.
* A rerun with the reference group restricted to samples recorded as healthy or with no condition recorded —
  would show whether the disease composition of the public archives moves any class.

## Tests

```
python for_editor/vanish_blossum/preview_vanish.py --payload <out>/page --out <abs>/previews/site_r3
NODE_PATH=<side_pages>/work/node/node_modules node for_editor/vanish_blossum/tests/dom_vanish.mjs \
  <abs>/previews/site_r3 <abs>/previews/site_r3_sent <abs>/previews/site_r3_none
python for_editor/vanish_blossum/tests/check_vanish_urls.py --root <abs>/previews/site_r3
python for_editor/vanish_blossum/tests/verify_robustness.py --vanish-out <out> \
  --sandpiper-dir <release>/sandpiper_profiles --hdi <the run's HDI table> \
  --external-labels <this folder>/external_labels_carter_s4.csv
```

The second and third preview roots are the same run with one or both payload files removed
(`site_r3_sent` = `robustness_sentences.json` only, `site_r3_none` = neither), each rendered into a tree
without a `data/vanish` folder, so that what `pages/vanish.py` copies can be checked exactly.

Results on the stored 1.31.0 run, 2026-11-23:

* `dom_vanish.mjs`: **70 passed, 0 failed** (42 before; 28 new). The new checks cover the headed block and its
  position (inside the chart section, after the plot's note line, with the two-sentence house rule above the
  plot still satisfied), each of the six sentences in its place and carrying the numbers that are in
  `robustness_sentences.json`, the Robustness section (collapsed, its summary line saying exploratory and that
  no class depends on it, four headings, three tables, three CSV links that resolve to files that exist,
  three not-done items), the agreement of all three drawn tables with `robustness.json` cell by cell, the
  presence of each check's result in words, the absence of the word "Atlas" from anything a visitor reads, and
  states (ii) and (iii) — state (i) as well, where the page must be as it was.
* `check_vanish_urls.py`: OK, 48 URLs resolved, 13 JSON files parsed.
* `verify_robustness.py`: **9 checks, 0 failed.** It recomputes, with SQL written for that file alone and
  importing neither `vanish_robustness.py` nor `vanish_blossum.py`: the selection of the primary contrast
  (group label, one sample per person, the per-cohort cap, the minimum cell size), the lifestyle strata, the
  subject table with each subject's band, cohort and depth stratum, and the carrier counts. Twenty classed
  taxa per level are drawn with seed 20261123 (60 taxa) and compared row by row with the three CSVs: 360
  lifestyle rows, 65 pooled cohort rows and 325 depth-stratum rows agree exactly, as do the group counts, the
  lifestyle strata, the cohorts holding both bands and the size of every depth stratum.

## What I could not implement as the assessment wrote it

1. **S3 — the lifestyles in the group.** The assessment's wording ("pools hunter-gatherer,
   indigenous-community, pastoralist and traditional-agriculturalist samples from 15 cohorts in 12 countries")
   leaves out 282 of the 675 samples: 165 with the catalog value `rural_non_industrialized` and 117 with no
   lifestyle value at all, both in the group only through Carter's external label. The sentence now lists the
   composition as it is, from the run's own selection.
2. **S3 — "the 'stated labels only' reference setting (7 cohorts)".** That setting is not a reference group and
   is not on the page's reference-group switch: it is contrast C3, whose *non-industrialized* group is built
   from stated catalog labels only (393 samples, 7 cohorts), and it appears in the sensitivity table. The
   sentence names it as such.
3. **S4 — "This is a permissive rule".** A verdict the data do not support as written: the species profile
   rows of this run hold nothing below 0.1 % relative abundance (the genus rows go down to 0.0009 %). The
   sentence now gives the measured floor at both levels instead of the adjective, and keeps the assessment's
   point that nothing has been recomputed at a stricter threshold.
4. **S5 — "we have not separated them".** Kept as a statement about the page, with the counts added: the
   `health_condition` field of the package does let the composition be counted (12,175 of 27,406 with a
   recorded condition), and counting it is not the same as the rerun that is listed as not done.
5. **Where the sentences sit.** The assessment names a place for each. Four of those places exist on the page
   and the sentences are in them (S1 and S2 in "Both rules in plain words", S3 in "The groups, and where their
   labels come from", S6 in "Limits"). Two do not: detection is defined nowhere on the page, and neither is
   the reference group, so S4 and S5 are in the headed block under the plot, which is where the brief says to
   put a sentence the assessment gives no home for. The block is placed after the plot's legend and caption
   rather than between the note line and the legend, so that the colour key stays next to the plot.
6. **Check 2 as the assessment specifies it** (Cochran–Mantel–Haenszel over cohorts) is nearly empty on this
   data: 5 of 642 adult cohorts, 1.1 % of subjects. It is reported, and the largest-contributing-cohort
   removal is reported beside it so that every classed taxon has an answer to "is one study carrying this".
   That removal is an addition, not part of the assessment's check 5.
7. **Not attempted, by instruction:** the three long checks above, and anything that would rerun `obs`,
   `perm`, `classes` or `simple`.
8. **Left alone:** the page is still rendered to `atlas/vanish.html` and its payload to `data/vanish/`. The
   word "Atlas" appears nowhere a visitor reads (the rendered page contains zero occurrences), but renaming
   the output path is a site-wide routing change and is not part of this work.
