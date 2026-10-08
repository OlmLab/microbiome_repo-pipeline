# INFANT-TIER R4 — cohort-wide core-field statements for the 389 infant-catalog studies

Route R4 (linked-paper abstracts + ENA/BioProject title and description), scope `study_all`, two replicate requests per study with `claude-sonnet-5` (`host.reasoning_model()`, no `temperature`), `determined_by = gut_r4_infant_v1:claude-sonnet-5x2`, `src_track = gut_all_v1`. Fields: health_condition (non-legacy codes of `config/vocab/health_conditions.yaml`), country (ISO-2, recruitment site), antibiotic_exposure (yes/no with a stated window), sex (single-sex cohorts only).

## Inputs

* `infant_tier_studies.parquet`: 389 studies, 154,356 samples; 387 with a non-empty ENA description; 261 with ≥ 1 PMID.
* First ≤ 3 PMIDs per study → 302 unique PMIDs; Europe PMC REST (`EXT_ID:<pmid> AND SRC:MED`, resultType=core), 3 concurrent, cached to `epmc_cache/`: 302/302 HTTP 200, 301 with an abstract (1 record without abstract text).
* Studies with ≥ 1 abstract in the prompt: **261** / 389; the other 128 were judged on ENA title + description only.

## Model pass

* 778 production requests (389 × 2; replicate 1 of each 48-study chunk finished before replicate 2 so the study text was read from prompt cache), plus a 6-study pilot under an earlier prompt layout (12 requests, discarded) and a 2×2 caching test.
* 0 errors, 0 unparsed responses, 0 refusals (`note='refused'` would have been recorded).
* Tokens (all calls incl. pilot): input 61,306, output 219,243, cache creation 456,432, cache reads 6,778,422. **Fresh tokens (input + output + cache creation) = 736,981** (pilot+test 93,300); hard stop 1.5 M not reached.

## Commit rule and guards

A (study, field) is committed only when both replicates return exactly one valid `applies_to=all` value, the values match, and neither replicate returns a contradicting subgroup value for that field. Quote = exact-case span (≤ 12 words) that is a verbatim substring of the named source text. Confidence = min(model, 0.5 abstract / 0.45 description / 0.4 title); `evidence_limited_to_abstract = 1`; `evidence_locator` = PMID (abstract) or the study accession (ENA text).

Deterministic guards (per replicate, before agreement): eligibility/exclusion wording; summary words (most, predominantly, median, …); pooled / meta-analysis wording; case-control design → no study-level condition; `healthy_control` needs healthy / no-disease wording and is dropped for 'born to healthy mothers'; non-`other_disease` codes need a vocabulary match term in the quote (`other_disease` kept with `flag_term_unmatched`); country needs the country name/alias or the model's place word in the quote, and a lab/affiliation quote without recruitment wording is dropped; antibiotic needs an antibiotic term, a non-empty verbatim `antibiotic_window`, and no policy / maternal / antiretroviral wording; sex needs a sex term. After agreement, health_condition rows on deposits that both replicates flag as also sampling mothers were demoted when the quote names infants only (2 rows), and three rows were removed on root review (listed below).

## Results

* Replicate agreement on validated cohort-wide values: **164/186 = 0.882** (study × field pairs where ≥ 1 replicate had a valid value); on raw model `all` statements before validation: 187/211 = 0.886.
* **Committed determination rows: 161** over 144 studies — per field: {"country": 114, "health_condition": 45, "sex": 1, "antibiotic_exposure": 1}. Evidence sources: {"study.description": 86, "paper.abstract": 44, "study.title": 31}.
* Samples reached by expansion (sum of n_samples of studies with a row): country 49,224; health_condition 9,401; antibiotic_exposure 96; sex 1. These overlap existing R1/R2/R3 values; precedence is applied downstream.
* health_condition values: {"healthy_control": 27, "gi_infection_or_diarrhoea": 5, "malnutrition": 5, "other_disease": 5, "other_cancer": 3}.
* country values: {"US": 11, "CN": 9, "AU": 8, "BD": 8, "IN": 5, "KE": 4, "ZA": 4, "MX": 4, "SE": 3, "GB": 3, "TZ": 3, "MW": 3, "NO": 3, "FI": 3, "PE": 3, "GM": 2, "CA": 2, "KR": 2, "DK": 2, "EC": 2, "NL": 2, "NE": 2, "ET": 2, "SG": 2, "VN": 1, "ZW": 1, "ES": 1, "DE": 1, "GA": 1, "TW": 1, "HT": 1, "BR": 1, "ML": 1, "BF": 1, "NG": 1, "IE": 1, "MY": 1, "UG": 1, "MG": 1, "GH": 1, "NZ": 1, "CI": 1, "IT": 1, "TN": 1, "NI": 1, "PR": 1}.
* antibiotic_exposure: PRJNA978345 = yes (infants with cystic fibrosis on ongoing prophylactic amoxicillin/clavulanate). sex: PRJNA215102 = male (single premature male infant).
* Study summary: design (agreed in 94.6%) {"cohort_longitudinal": 190, "randomized_trial": 39, "case_control": 35, "cross_sectional": 34, "unknown": 25, "case_series": 20, "other": 14, "nonrandomized_intervention": 11}; `n_subjects_stated` verified (both replicates equal + verbatim quote containing the number) for 142 studies.

### Drops before agreement (per replicate statement)

| reason | n |
|---|---|
| dropped_quote:quote_not_verbatim | 31 |
| dropped_quote:empty_quote | 24 |
| dropped_rule:place_not_in_quote | 12 |
| dropped_value:hc_not_core_code | 12 |
| dropped_rule:hc_term_not_in_quote | 10 |
| dropped_rule:healthy_wording_missing | 7 |
| dropped_value:not_iso2 | 6 |
| dropped_quote:quote_gt_12_words | 5 |
| dropped_value:unknown_field | 4 |
| dropped_value:abx_not_yes_no | 3 |
| dropped_rule:eligibility_criterion | 3 |
| dropped_rule:pooled_dataset | 2 |
| dropped_root_review:phenotypes_related_to_not_cohort_wide | 2 |
| dropped_rule:abx_term_not_in_quote | 2 |
| dropped_rule:abx_policy_or_nonantibiotic | 2 |
| dropped_root_review:quote_is_disease_definition_not_cohort_statement | 2 |
| dropped_rule:healthy_refers_to_mothers | 2 |
| dropped_rule:abx_window_not_verbatim | 1 |

Non-committed (study, field) outcomes: {"no_valid_all_statement": 25, "replicate_disagreement": 20, "multi_value_conflict": 2, "demoted_infant_only_quote_on_mixed_deposit": 2, "dropped_root_review:trial_registry_not_recruitment_site": 1}; details in `gut_r4_infant_dropped_and_disagreements.csv`; every replicate statement with its status in `gut_r4_infant_statement_log.csv`.

### Root-review removals / demotions

* PRJNA629392 health_condition other_disease — quote is a disease definition ('… is a lethal disease'), not a cohort statement.
* PRJNA1139951 health_condition malnutrition — 'phenotypes related to undernutrition in toddlers' does not say every subject was undernourished.
* PRJEB37883 country DE — quote names a trial registry, not the recruitment site.
* PRJEB46943, PRJEB52774 healthy_control — quote says 'healthy … infants' while both replicates report that mothers were also sampled → demoted (not committed).

## Points for the root audit

* Borderline commits worth a look: PRJNA1355224 malnutrition (cohort of children discharged after malnutrition treatment, quote 'risk of relapse to acute malnutrition'); PRJNA1425751 other_disease (neonatal jaundice, term unmatched); PRJEB15257 healthy_control ('preterm but healthy infants'); PRJNA1160256 healthy_control (ex-vivo incubation of 24 healthy donors across age groups); PRJNA739008 malnutrition taken from the study title.
* No Opus group-statement audit and no consistency check against per-sample R1/R2 values were run here (`group_audit` is null); both belong to the root assembly step. `release_added` / `package_added` are left null for the root to stamp.
* `health_condition_detail` rows were not emitted (the task named the four core fields only); the raw wording is in `field_value`.
* Re-analysis papers: only statements describing the deposit's own cohort were requested; 2 replicate statements were dropped by the pooled-dataset guard.

## Files
`gut_r4_determinations_shard_infant.parquet` (determination schema, 19 columns as the gut_all R4 shards), `gut_r4_infant_study_summary.parquet`, `gut_r4_infant_dropped_and_disagreements.csv`, `gut_r4_infant_statement_log.csv`, `gut_r4_infant_cost.json`, code `r4i_driver.py`, `r4i_post.py`, prompt `r4i_system.txt`, raw replicate outputs `r4i_raw.json`.