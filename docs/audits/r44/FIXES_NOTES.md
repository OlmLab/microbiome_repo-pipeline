# FIXES_NOTES — R2026.44 / package 1.35.0
`confirmed_fixes_r44.csv` 5,192 rows = 5,145 Metalog (36 of the 43 ok+uniform patterns, 5,640 samples) + 47 audit rows; `not_applied_r44.csv` 432 rows. Every value re-derived from the cited source; no Metalog value and no reader proposal copied.

## Per pattern — confirmed (samples changed / samples in pattern) and the source re-read
| pattern | study | field | confirmed | source |
|---|---|---|---|---|
| MP0317 | PRJEB45799 | body_site_code | 1929/1929 | ena DESCRIPTION saliva_metagenome_from_USA |
| MP0749 | PRJEB32631 | subject_id | 1415/1415 | supp S1 BBS metadata table!Individual |
| MP0388 | PRJEB42363 | country | 177/177 | PMC8740015 methods (MORDOR Malawi) |
| MP0318 | PRJNA1085219 | body_site_code | 149/149 | BioProject PRJNA1085219 description |
| MP0005 | PRJNA1112983 | age_at_collection_days | 148/148 | supp Table_1.docx!Age(year) |
| MP0756 | PRJNA63661 | subject_id | 88/133 | biosample sample_name |
| MP0757 | PRJNA510445 | subject_id | 122/122 | ena sample TITLE 4-14-<subj>-<visit> |
| MP0321 | PRJNA475246 | body_site_code | 116/116 | biosample tissue=Stool |
| MP0758 | PRJNA947377 | subject_id | 54/108 | supp mmc2.xlsx!sample_ID |
| MP0759 | PRJNA384716 | subject_id | 96/96 | supp MOESM5!PAIR+TYPE |
| MP0561 | PRJNA385949 | health_condition | 88/89 | supp MOESM1!Diagnosis |
| MP0708 | PRJNA266117 | sample_type | 69/69 | biosample Site_Categories=Control |
| MP0573 | PRJNA1112983 | health_condition | 62/62 | supp Table_1.docx!Category |
| MP0324 | PRJNA352475 | body_site_code | 60/60 | biosample isolation_source |
| MP0709 | PRJEB53403 | sample_type | 58/58 | ena TITLE *BLANK* |
| MP0729 | PRJNA1112983 | sex | 50/50 | supp Table_1.docx!Sex |
| MP0730 | PRJNA1112983 | sex | 47/47 | supp Table_1.docx!Sex |
| MP0022 | PRJNA1112983 | age_at_collection_days | 45/45 | supp Table_1.docx!Age(year) |
| MP0391 | PRJEB21933 | country | 40/40 | PMC5769311 methods (BISCUIT, Scotland) |
| MP0328 | PRJNA949528 | body_site_code | 40/40 | biosample host_body_product=stool |
| MP0583 | PRJNA277905 | health_condition | 38/38 | supp Table S2!Health state |
| MP0332 | PRJNA384716 | body_site_code | 32/32 | biosample isolation_source=breastmilk |
| MP0029 | PRJEB74322 | age_at_collection_days | 32/32 | supp mmc2.xlsx Tab1!maternal_age |
| MP0731 | PRJEB25422 | sex | 28/29 | supp MOESM5!Gender |
| MP0773 | PRJNA289586 | subject_id | 13/28 | biosample host_subject_id |
| MP0734 | PRJEB25422 | sex | 25/26 | supp MOESM5!Gender |
| MP0736 | PRJEB74322 | sex | 25/25 | supp mmc2.xlsx Tab1!mother_infant |
| MP0601 | PRJEB25422 | health_condition | 17/19 | supp MOESM5!Diagnosis |
| MP0339 | PRJNA352475 | body_site_code | 19/19 | biosample isolation_source |
| MP0338 | PRJEB28097 | body_site_code | 19/19 | ena tissue_type |
| MP0341 | PRJNA352475 | body_site_code | 15/15 | biosample isolation_source |
| MP0538 | PRJEB32631 | delivery_mode | 7/7 | supp S1 BBS!Delivery_mode |
| MP0706 | PRJEB74322 | preterm_status | 7/7 | supp mmc2.xlsx Tab1!gestational_age |
| MP0539 | PRJEB32631 | delivery_mode | 5/5 | supp S1 BBS!Delivery_mode |
| MP0398 | PRJNA63661 | country | 5/5 | supp Table S1!geo_loc_name_country |
| MP0619 | PRJEB44237 | health_condition | 5/5 | PMC8222733 methods (lean FMT donors) |
Unchanged, source agrees with the catalog: MP0756 45, MP0758 54, MP0773 15. No source value: MP0561 1, MP0731 1, MP0734 1, MP0601 2 (sample id absent from the clinical supplement).

## Totals by field (all 5,192 rows)
body_site_code 2,379 · subject_id 1,789 · age_at_collection_days 225 · country 222 · health_condition 220 · sex 176 · sample_type 127 · delivery_mode 12 · collection_date 11 · intervention_detail 8 · preterm_status 7 · health_condition_detail 5 · nec_status 2 · antibiotic_exposure, feeding_mode, lifestyle_detail, location_locality, location_region, medication, medication_detail, smoking_status, timepoint_label 1 each.

## Before applying
**subject_id (1,789 rows, 7 projects)** — new identifiers are the project's own throughout. PRJEB32631: taken from the supplement's `Individual` column, **not** the title prefix the readers proposed, because the prefix merges twins (`A00021_T2`); 1,415 per-sample Sanger ids become **507 people** (the prefix rule would give 494). PRJNA510445 5 → **25** (catalog had grouped by visit month), PRJNA63661 86 → **43**, PRJNA289586 10 → **5** (zero-padding normalised), PRJNA384716 96 → **32** (`<PAIR>_mother`/`<PAIR>_infant`: the pair code is the project's, the role suffix is added because the deposit carries no separate mother/infant id), PRJNA947377 54 → **108** (the study's Table S1 lists `P001` and `P001L1` as separate participants with different sexual orientation, ethnicity and HIV status — one person per sample in this project).

**body_site_code (2,379 rows)** — codes used, all existing vocabulary, none new: `oral` 1,989 (PRJEB45799 saliva 1,929; PRJNA352475 tongue dorsum 60), `gut_stool` 324 (PRJNA475246 116, PRJNA1085219 149, PRJNA949528 40, PRJEB28097 19 duodenal mucosa), `milk` 32, `vaginal_urogenital` 19, `skin` 15.

**Other fields** — ages are integer days (`years × 365.25`, rounded): PRJNA1112983 193 child ages, PRJEB74322 32 maternal ages. Countries are ISO-2: MW 177, GB 40, US 5. All 127 `sample_type` rows are `negative_control`.

## Not applied — your decision
Five age patterns (MP0007/12/14/33/50; 322 samples, PRJNA688274 + PRJNA491335): the archive attribute equals the catalog's own age determination, so there is no per-sample value error; only the years→days normalisation differs, and it is inconsistent (362.9–365.8 d/y across samples) — a normaliser bug rather than 322 fixes.
MP0041 (16 maternal samples, PRJNA352475): the supplement gives maternal age only in 5-year bands, so no day value is derivable — but the catalog's `0` comes from the infant `Sampling, day` column and should be removed in a later release.
MP0769 (38): the `Patient` column exists only in the study's github repository, unreachable here. A0059: cited source is a Zenodo CSV and the DDBJ record carries no age attribute. A0085: the source says "3 months"; 90 d vs 91.3 d is a month-length convention, not a source conflict.
107 ok audit rows of class "cited source does not support a value" are listed as `audit_not_supported_listed_for_later`; no value was removed this release. 316 check-flag rows (16 Metalog patterns, 300 audit rows) are listed with their check notes.
