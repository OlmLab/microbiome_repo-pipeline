# INFANT_R1_REPORT — BioSample-attribute R1 for the infant-catalog tier (gut_r1_infant_v1)

Generated 2026-10-08. Input: infant_tier_samples.parquet (154,356 sample keys; 153,851 distinct BioSample references in 389 studies). Mirrors the gut_all R1: key maps `config/packs/gut_attribute_field_map.csv` + infant `config/attribute_field_map.csv`, parsers `src/catalog/extraction/r1_parsers.py`, value maps `registry_pairs_normalised.parquet`, `gut_health_condition_map.parquet`, `gut_antibiotic_map.parquet`. Nothing was written to the pipeline repo.

## Harvest

- SAMEA/SAMD: ENA browser XML, 609 batches of 100 (3 workers): 60,845 / 60,845 resolved.
- SAMN: NCBI efetch db=biosample (POST, 466 batches of 200, ≤ 2.8 req/s): 93,000 / 93,000 single-accession SAMN resolved. Only records whose `accession` equals a requested accession were kept; batches also returned unrelated records resolved from numeric ids, which were discarded.
- 6 sample keys (PRJNA63661) carry `;`-joined multi-BioSample references (247 components; 246 also harvested as standalone samples, SAMN00009845 fetched separately and has only a title).
- **153,845 / 153,845 single-accession BioSamples harvested (100 %); 153,846 BioSamples with ≥ 1 row.** 4,729,396 raw attribute rows (ENA 3,531,468; NCBI harmonised 1,140,747; NCBI submitter-name duplicates 57,181); 2,257 distinct normalised keys. Wall time ≈ 12 min. Raw responses cached as gzip under `harvest_cache_r1/`.

## Key map

Union of the gut pack map and the infant map, restricted to the 8 target fields (for age keys a SKIP row wins; otherwise a mapped parser wins, gut pack first), plus registry country keys `host_country_of_residence` and `geo_location`, plus 14 keys added after manual triage of 368 unmapped candidate keys (regex screen over the harvested keys, reviewed with example values): country_residence, geographic_location_country_region_area, antibiotics_exposure, abxprior_specimen_24, bmi_corrected, ibd_diagnosis_refined, prenec, conditions, eczema, weightstatus, host_allergy, is_healthy, connatal_infection, visit_day. Saved as `gut_r1_infant_key_map.csv` (626 rows, 370 active). Rejected candidates include maternal BMI/age keys, lifetime or 0/1-coded antibiotic keys without legend, future outcomes (eczema_by_2_years …), questionnaire allergy/mental-illness flags and sample-code keys.

## Rules applied (differences from gut_all R1 in bold)

- **Age: explicit units only.** Unit sources in order: the attribute's own UNITS element, a key-specific sibling (`<key>_units`, `host_age_units_infant`, `host_age_units_mother`), the unit in the key name, then a generic sibling (`host_age_units`, `age_unit` …) only for keys without a unit in their name. An attribute unit and a key-name unit that disagree give no value. **No bare-number = years default**: 11,801 bare unitless rows gave no value. Ranges, lists, thresholds (`Less than 1 month`, 141 rows) and life-stage words (`BF infant`, `Toddler`, `near birth`; 57 rows) are not values; `infant birth` / `newborn` → 0 d. Week-unit `host_age` values were checked for the gestational trap (ranges span 1–104 weeks; all compatible with postnatal age).
- Sex: infant `parse_sex` plus Spanish/German tokens; coded `0`/`3` and `other` give no value. **Samples whose keys disagree on sex are committed for neither** (145 samples, PRJEB14941 `sex_infant_1` vs other keys).
- Country: registry pair-map value when the (key, value) pair is in it (195 of 196 matching pairs agree with the deterministic parse; the exception, `China: Hong Kong` → HK, takes the map value at 0.8); otherwise `parse_country` after stripping `GAZ:`; fuzzy pycountry matches rejected.
- health_condition_detail: raw text ≤ 120 chars (`health_text`) or `<condition from key>: yes|no` (`health_bool_key`; AG answers `I do not have this condition` → no, `Diagnosed by a medical professional` → yes, self-diagnosed / alternative practitioner → yes at 0.6). The health_condition code comes from `gut_health_condition_map` on (key, value) (code ≠ unknown, confidence ≥ 0.5), else from the new-pair map.
- antibiotic_exposure: infant `parse_yesno` + phrases + drug names (`antibiotic_history = Year` is a 12-month window and gives no value, as in gut_r1_v1 — 919 rows; `gut_antibiotic_map` rows with rule A7_old_window, which code an older course as `no`, were not reused because they contradict the any-exposure definition); otherwise `gut_antibiotic_map`, then new pairs. `yes` from any key dominates `no` (confidence 0.7). **`antibiotics_admin` day-of-life schedules were forced to unknown** (timing relative to sampling is not in the value).
- bmi: numeric 10–80. subject_id: pass-through minus sex-like / group words / accession-equal / (study, key)-constant values (2 groups, 49 rows dropped). timepoint_label: minus clock times and calendar dates.
- One row per (sample, field): highest confidence, then the key used by fewest studies, then key name. Age conflicts (> max(10 %, 1 d)) are committed for neither.
- Confidence: 0.9 for deterministic parses and rule-method map rows; 0.8 when a utility model interpreted the value (new pairs, LLM-method map rows, registry-map overrides); capped by the key-map confidence (subject keys 0.5–0.85, some antibiotic keys 0.6–0.8).
- Run-unit sample keys (6 studies): only subject-scope fields (sex, country, subject_id) inherited from the parent BioSample, and only country for the multi-subject parents PRJNA1055141, PRJNA1258733, PRJNA869587. Pooled multi-BioSample keys: emitted only when every component agrees (0 rows).
- Evidence: `evidence_source = biosample.attribute:<attr_key_norm>`, `evidence_locator` = BioSample accession, quote `<key>: <value>` trimmed to ≤ 12 words; route R1, scope sample, determined_by gut_r1_infant_v1, src_track gut_all_v1, evidence_limited_to_abstract 0.

## Coverage per field

| field | rows | samples | studies | mean conf | rows via utility model | top keys |
|---|---|---|---|---|---|---|
| age_at_collection_days | 37,690 | 37,690 | 70 | 0.891 | 0 | host_age (16,653), age_years (3,924), age (3,448), phenotype (2,373), dol (1,797), host_age_infant (1,768) |
| antibiotic_exposure | 11,054 | 11,054 | 16 | 0.801 | 258 | antibiotic_history (5,896), antibioticuse (706), antibiotics_summary (644), antibiotics_at_birth (637), exposed_abx (542), host_antimicrobial_in_last_7days (369) |
| bmi | 8,674 | 8,674 | 9 | 0.900 | 0 | bmi (5,220), host_body_mass_index (1,720), bmi_corrected (1,138), body_mass_index (596) |
| country | 120,220 | 120,220 | 343 | 0.899 | 61 | geo_loc_name (77,606), geographic_location_country_and_or_sea (31,301), country (9,526), country_residence (1,156), geographic_location_country_and_or_sea_region (597), geographic_location (22) |
| health_condition | 7,828 | 7,828 | 29 | 0.789 | 5,095 | host_disease (1,772), host_disease_status (705), subject_is_affected (688), ibs (556), autoimmune (427), skin_condition (380) |
| health_condition_detail | 16,523 | 16,523 | 38 | 0.890 | 0 | clinical_condition (3,927), phenotype (2,394), diabetes (1,891), host_disease (1,831), host_disease_status (1,441), subject_is_affected (688) |
| sex | 52,811 | 52,811 | 88 | 0.900 | 0 | sex (27,384), host_sex (20,412), phenotype (2,381), baby_gender_cat (809), gender (467), babygender_m_f (414) |
| subject_id | 74,540 | 74,540 | 143 | 0.833 | 0 | host_subject_id (40,593), gap_subject_id (13,093), subject_id (11,051), isolate (3,669), participant_id (1,013), patient (655) |
| timepoint_label | 16,919 | 16,919 | 52 | 0.844 | 0 | timepoint (6,295), day (2,930), visit (2,833), time_point (1,054), planned_sampling_day_0_1_3_7_14_30_60_90_365 (1,016), study_day (563) |

Any field: 144,940 sample keys (93.9 % of 154,356) in 365 of 389 studies. health_condition on the infant tier: 21,361 samples in the catalog → 25,375 with this R1 added (13.8 % → 16.4 %).

## New pairs (utility model)

Distinct non-placeholder health pairs: 1,288, of which 26 were already in `gut_health_condition_map`; of the 1,262 new pairs, 8 were coded by the map's K2 rule (`negative for that condition only` → unknown) and 1,254 went to the model. Antibiotic pairs not resolved deterministically: 117 (0 in `gut_antibiotic_map`), all to the model. Sex had 6 unparsed pairs (coded/other; no model call); every country pair resolved by the registry map or deterministic parsing; age never goes to the model. Model `claude-haiku-4-5-20251001`, 35 requests of ≤ 40 pairs; every id returned and every code was valid. Post-model guards: 2 `healthy_control` answers for negatives under a named condition → unknown; 48 `antibiotics_admin` pairs → unknown. Result: health_condition unknown 1137, preterm_nicu 21, healthy_control 15, gi_infection_or_diarrhoea 13, allergy_or_atopy 10, other_disease 8, ibd_unspecified 7, crohns_disease 7; antibiotic unknown 90, yes 26, no 1. Saved as `gut_r1_infant_new_pairs.parquet` (1,379 rows).

**LLM tokens: 261,086** (input 208,195, output 52,891, cache creation 0); budget 1.5 M.

## Within-sample key conflicts (`gut_r1_infant_conflicts.parquet`)

| field | resolution | samples |
|---|---|---|
| age_at_collection_days | committed_neither | 1,278 |
| antibiotic_exposure | yes_dominates_no | 1,162 |
| country | kept_top_ranked | 26 |
| health_condition | kept_top_ranked | 1,500 |
| health_condition_detail | kept_top_ranked (multi-key text field) | 7,375 |
| sex | committed_neither | 145 |
| subject_id | kept_top_ranked (multi-key text field) | 21,368 |
| timepoint_label | kept_top_ranked (multi-key text field) | 1,027 |

The age conflicts are dyad records with both maternal and infant ages: PRJEB10914 (`age` = mother's years vs `age_baby_days`), PRJEB83236 / PRJEB83552 (`host_age` = maternal age vs `host_age_infant`), PRJNA473126 (two day-count keys). The infant catalog already holds an R1 age for these samples.

## Agreement with the infant catalog (`build/package/gut_sample_metadata_wide.parquet`, curated_source = infant_catalog)

| field | R1 samples | compared (catalog has value) | agree % | agree % (legacy-code aware) | vs catalog R1 only: n / agree % | new fills: samples / studies |
|---|---|---|---|---|---|---|
| age_at_collection_days | 37,690 | 35,260 | 99.2 | 99.2 | 34,941 / 99.2 | 2,430 / 4 |
| antibiotic_exposure | 11,054 | 11,050 | 98.2 | 98.2 | 11,047 / 98.3 | 4 / 1 |
| bmi | 8,674 | 679 | 100.0 | 100.0 | 13 / 100.0 | 7,995 / 8 |
| country | 120,220 | 119,092 | 99.6 | 99.6 | 118,700 / 99.9 | 1,128 / 1 |
| health_condition | 7,828 | 3,814 | 42.7 | 89.1 | 2,578 / 25.1 | 4,014 / 16 |
| health_condition_detail | 16,523 | 3,257 | 20.3 | 20.3 | 129 / 100.0 | 13,266 / 35 |
| sex | 52,811 | 52,749 | 100.0 | 100.0 | 52,749 / 100.0 | 62 / 4 |
| subject_id | 74,540 | 74,376 | 97.9 | 97.9 | 74,376 / 97.9 | 164 / 6 |
| timepoint_label | 16,919 | 16,877 | 97.4 | 97.4 | 16,733 / 98.2 | 42 / 4 |

Numeric agreement: age within max(10 %, 1 d); bmi within 10 %. Categorical/text: case-insensitive exact match. The legacy-aware health_condition column counts crohns / UC / IBD-unspecified / IBS / GI-infection → `ibd_or_gi_disease` and other_infection → `sepsis_or_infection` as agreeing (the catalog's legacy infant codes).

Main disagreement patterns (all rows in `gut_r1_infant_catalog_disagreements.parquet`):
- age_at_collection_days: PRJEB108678 R1 `1957.7` vs catalog `163.0` (4); PRJEB108678 R1 `2995.0` vs catalog `250.0` (4)
- antibiotic_exposure: PRJNA1086674 R1 `yes` vs catalog `no` (100); PRJNA473126 R1 `yes` vs catalog `no` (36)
- country: PRJEB6456 R1 `DK` vs catalog `SE` (392); PRJNA588513 R1 `HK` vs catalog `CN` (61)
- health_condition: PRJNA398089 R1 `crohns_disease` vs catalog `ibd_or_gi_disease` (930); PRJNA398089 R1 `ulcerative_colitis` vs catalog `ibd_or_gi_disease` (566)
- health_condition_detail: PRJNA398089 R1 `Crohn's disease` vs catalog `Crohn Disease` (598); PRJNA46337 R1 `subject is affected: no` vs catalog `Health` (458)
- subject_id: PRJNA1140720 R1 `5029` vs catalog `B9_BA5029` (34); PRJNA1140720 R1 `5018` vs catalog `B6_BA5018` (33)
- timepoint_label: PRJNA549787 R1 `A` vs catalog `pre-weaning` (46); PRJNA549787 R1 `B` vs catalog `pre-weaning` (42)

Notes: PRJEB108678: `host_age` with `host_age_units = years` (e.g. 2.83 → 1,034 d) vs catalog 86 d (value read as months); the explicit submitter unit is followed here; flag for review (273 samples). PRJEB6456: BioSample says Denmark, catalog R2 says SE (392). health_condition_detail disagreement is mostly R1 raw text vs R2 supplementary wording, not contradiction. Antibiotic `yes` vs catalog `no` comes from cumulative `days_on_abx` > 0 (PRJNA1086674) and `host_lifetime_antibiotics_exposure = 1` (PRJNA473126), i.e. any antibiotics before sampling, where the catalog used a current-use key. subject_id differences in PRJNA1140720 are the same subject in two formats (`5029` vs `B9_BA5029`).

## Deviations and caveats
- 6 pooled multi-BioSample keys (PRJNA63661) got no rows (components never all carry the field).
- Run-unit keys receive subject-scope fields only (by design; sample-scope BioSample attributes cannot be assigned to individual runs).
- Age is deterministic only; the registry pair map's age normalisation (bare numbers read as years) was deliberately not reused. The sex/country pair maps were reused; no model calls for sex, country or age.
- Key triage beyond the two existing maps was a manual review of 368 regex-screened candidate keys (14 added); keys outside the regex families were not reviewed, so coverage is a lower bound.
- health_condition keeps one code per sample (1,500 multi-key samples resolved by rank; alternates in the conflicts file).
- The evidence-source label follows this task's spec (`biosample.attribute:<key>`), not the `sample.attr.<key>` label used by gut_r1_v1; `apply_condition_maps._attr_key` expects the latter if these rows are fed back to it.
