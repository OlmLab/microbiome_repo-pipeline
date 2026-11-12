# corrections_projects — notes
Release R2026.37 package read-only; decisions for R2026.38. 11 items, 17 correction rows in `corrections_projects.csv`.
Every quote below was copied from a source opened in this session (ENA browser API / NCBI E-utilities / Europe PMC / cached PMC OA text or supplementary workbook); raw bodies are under `out/_fetch/`.
Row counts after each row are the number of current determination rows (release_retired null) the row matches in `catalog_sample_determinations.parquet`.

## 1. PRJEB45799 health_condition — retire (2 rows)
**Found.** ENA sample SAMEA10739526 (fetched this session) carries exactly three informative attributes: `phenotype = male_subject_of_0.328767123_years`, `subject_id = ARG_B10`, `gender = unknown`; title `CM_argentina`, description `stool_metagenome_from_ARG`. Nothing about prematurity or neonatal care. The ENA study is "The person-to-person transmission landscape of the gut and oral microbiomes". So `preterm_nicu` on the 47 phenotype-derived samples comes from the infant age in the string alone.
**Rows written.** (a) retire `health_condition` = `preterm_nicu`, route R1 — matches 47 rows, which is every health_condition row of the project not sourced from external.cmd (the other 424 are `healthy_control` from curatedMetagenomicData and are untouched).
(b) **Other fields derived from the same string.** Three fields use `biosample.attribute:phenotype`: `age_at_collection_days` (2,155 rows), `health_condition` (47) and `health_condition_detail` (2,394). The age reading is correct (0.328767123 years -> 120 days) and is left alone. `sex` (2,381 rows) is derived from the same string under `evidence_source = biosample_attr` and is also correct. `health_condition_detail` stores the phenotype string itself for all 2,394 rows (e.g. `male_subject_of_2.0_years`, and `NA_subject_of_NA_years` 13 times); the data dictionary defines the field as "Raw health / disease text" and the string contains none, so a second retire row was written — **judgment call, confidence 0.7**. If the build intends that field as raw-source provenance rather than a user-facing value, drop row (b) and keep row (a).
**Open.** Two different year lengths are used by the project's own age converters (365 vs 365.25 d/y); immaterial at infant ages and not corrected here. `gender = unknown` on SAMEA10739526 contradicts the sex derived from the phenotype string — the string is the more specific source, so sex was not touched, but the contradiction is not recorded anywhere in the catalog.

## 2. PRJNA310878 age_at_collection_days — retire (1 row), unit not establishable
**Found.** BioSamples SAMN04456664 / SAMN04456666 / SAMN04456704 fetched from NCBI: the `age` attribute is a bare number (`17`, `146`, …) with no unit attribute and no other attribute that implies one (the 16 attributes are isolate, age, biomaterial_provider, sex, tissue = "middle ear" plus submission metadata). The BioProject record is titled "Secretions from patients infected with bacteria and viruses raw sequence reads" (Children's National Health System) and states no unit and no publication. Europe PMC returns **0 hits** for both `"PRJNA310878"` and `"SRP069302"`.
**Rows written.** retire every `age_at_collection_days` value of the project (21 rows). Months is the only unit consistent with the 6-146 range seen across the 44 BioSamples, but no source states it, so it is not committed.
**Open.** The unit. Only a submitter enquiry or an unindexed publication would settle it.

## 3. PRJEB14383 country — recode PH -> US (7 per-sample rows)
**Found.** The earlier check reported five re-deposited HMP samples; there are **seven**. Every sample of this deposit whose country came from the study title (route R4, confidence 0.4, quote "Oral microbiome samples from the Philippines") is an HMP sample: SAMN00035160, SAMN00035686, SAMN00035910, SAMN00036273, SAMN00040338, SAMN00099900, SAMN00146343. ENA portal returns each one's title as `Human metagenome sample from G_DNA_Saliva of a <male|female> participant in the dbGaP study "HMP Core Microbiome Sampling Protocol A (HMP-A)"`. The remaining 24 samples carry the BioSample attribute `geographic location (country and/or sea) = Philippines` and are left alone.
**Rows written.** 7 recode rows, each with its own sample title as the verbatim quote and its own BioSample accession as locator.
**Open.** The sample title names the dbGaP HMP-A study rather than the word "United States"; US rests on HMP-A being the US NIH protocol. I did not fetch the dbGaP/HMP project record for a sentence that states the country explicitly.

## 4. PRJEB6456 country — set SE project-wide (1 row)
**Found.** ENA study PRJEB6456 (fetched) has NAME `InfantGut` and TITLE "Dynamics and Stabilization of the Human Gut Microbiome during the First Year of Life" — verbatim the title of Backhed et al. 2015 (Cell Host & Microbe, PMID 25974306). That paper's abstract (Europe PMC core record, fetched) reads "Applying metagenomic analysis on fecal samples from a large cohort of Swedish infants and their mothers, we characterized the gut microbiome during the first year of life…". The cohort statement covers infants and mothers, i.e. all deposited samples.
**Rows written.** 1 recode row with empty sample_key and empty old_value = set `country = SE` for the project (no sample currently has a country, so this is an insert; 0 existing rows matched, as intended).
**Open.** The paper is not open access, so the locator is `PMID:25974306` rather than a PMCID. The ENA sample attributes remain self-contradictory ("spanish" in titles, country attribute "Denmark", Madrid coordinates); this row does not fix the archive record, and a reader comparing the two will still see the conflict.

## 5. PRJNA929328 sex — retire (1 row)
**Found.** Legend searched once more in three places and not found anywhere. (i) Article full text PMC10876974: the words sex, gender, male, female do not occur. (ii) All 30 supplementary files cached with the paper were scanned (xlsx cell by cell, docx body XML, pdf text): the only occurrence of 'sex' or 'gender' in any of them is the bare header cell `Sex` in `41598_2024_53506_MOESM23_ESM.xlsx`, sheet 'Tabelle1 (2)'. Every other numeric column of that sheet has its legend spelled out. (iii) BioSample SAMN32957175 (NCBI, fetched) carries PatID, time and read statistics but no sex attribute.
**Rows written.** retire `sex` for the project — 101 rows (65 female, 36 male). The patient-level join (BioSample PatID -> sheet 'Patient ID') is sound; only the 0/1 semantics are undefined, and an inverted legend would flip all 101 values.

## 6. PRJEB45443 sex — NO CHANGE (0 rows)
**Found.** Source opened: supplementary PDF `41522_2025_714_MOESM1_ESM.pdf` of PMC12122682, downloaded from Europe PMC this session. Page 4 is "Supplementary Table 2. Sample characteristics" and its column header reads verbatim **`Sex (1=F, 2=M)`**. So in this project 1 = female and 2 = male, and the catalog's decoding (17 samples female from `1`, 30 male from `2`) follows the source. The decoding is **not** inverted; this project is a genuine exception to the 1=male/2=female convention of the six other 1/2 projects, and its evidence_quote already carries the legend (`Sex (1=F, 2=M)=1`).
**Open.** I did not verify the row-to-sample join (the PDF's SampleID column, e.g. `Y13_032_C`, `52370678`, was not matched against ENA aliases). The article full text contains no sex words, so the header is the only legend and there is no count cross-check.

## 7. PRJNA1237910 / PRJNA1184021 sex=female — recode, value kept, evidence replaced (2 rows)
**PRJNA1237910** (62 samples; title letters C 22 / G 20 / M 20, all -> female). NCBI BioProject record fetched: title "Integrated metagenomic and metabolomic analysis reveals the association between the maternal gut microbiota and macrosomia in gestational diabetes mellitus", description ending "Notably, maternal gut microbiota samples were collected from pregnant women undergoing cesarean section before delivery." That sentence is the new evidence (source `ena.study_description`, locator PRJNA1237910) and covers all 62 samples.
**PRJNA1184021** (36 samples; alias letters A 18 / B 18, both -> female). Own-data paper PMC12913639 (cached) abstract: "Adult female participants diagnosed with AN were recruited." The 36 samples carrying sex are the 18 patients P03-P21 at timepoints A and B; the 13 samples with no subject_id (donor/standard material) carry no sex and are untouched.
**Rows written.** 2 recode rows, old_value = new_value = `female`, route R1.
**Open.** Nothing. Both values were already correct; only the locator/quote changes.

## 8. PRJNA746290 health_condition — recode, values kept, verbatim cell text as evidence (2 rows)
**Found.** `41467_2022_28678_MOESM4_ESM.xlsx` (cached with the earlier check), sheet 'Supplementary Data 1', columns `subject ID` / `normal/disease` / `number of human + microbial`. The column takes exactly two values: `disease case` (1,870 rows) and `normal` (12 rows).
**Verification of the join.** All 1,879 catalog samples of the project were matched to the sheet by ENA `sample_alias` (MLL_*) -> `subject ID`: 1,879 of 1,879 join, 1,868 to `disease case` (all `other_disease` in the catalog) and 11 to `normal` (all `healthy_control`), with no crossovers. Three sheet subjects (MLL_00207, MLL_11986, MLL_55650) have no current catalog sample — the count drift the earlier check reported.
**Rows written.** 2 recode rows keyed on old_value, carrying the verbatim cell text and `paper.supp.41467_2022_28678_MOESM4_ESM.xlsx[Supplementary Data 1!normal/disease]` / PMC8873459.
**Open.** The code itself is unchanged per the item, but `other_disease` is weak for a deposit the paper describes as myeloid malignancy patients; a project-level code would be more specific than the generic cell text supports.

## 9. PRJEB6070 health_condition — recode other_cancer -> colorectal_cancer (1 row)
**Found.** ENA study PRJEB6070 (fetched) TITLE "Potential of fecal microbiota for early stage detection of colorectal cancer", DESCRIPTION the Zeller et al. 2014 abstract ("…taxonomic markers that distinguished CRC patients from tumor-free controls in a study population of 156 participants…"). The 91 samples whose BioSample `diagnosis` reads `Cancer` are therefore colorectal carcinomas.
**Other diagnosis values checked at the same time.** `Small adenoma` (27) and `Large adenoma` (15) are already `colorectal_adenoma`; `Normal` (66) is `healthy_control`, matching the paper's tumour-free controls. No change to either.
**Rows written.** 1 recode row keyed on old_value `other_cancer` (91 rows).
**Open.** The deposit holds 199 samples while the paper's French population F has 156 participants, so the sample-to-participant mapping was not reconstructed; the recode rests on the deposit-level diagnosis vocabulary, not on a per-sample statement.

## 10. PRJNA398089 — nothing to do
No rows written, as instructed; the Crohn's / ulcerative colitis coding is handled by a build rule.

## 11. PRJEB91658 subject_id — report only, no rows
**Found.** The suffix is **not** carried by most aliases: of the 1,970 sample aliases, only 90 have a `.n` suffix — 45 ending `.1` and 45 ending `.2`, forming 45 exact pairs on the same `CCA…` base. The other 1,880 aliases have no suffix at all. That is exactly the catalog's 1,925 distinct subjects over 1,970 samples (1,880 + 45).
Both members of a pair were fetched from ENA (SAMEA118657230 `CCA1010334.1` and SAMEA118657231 `CCA1010334.2`): their attribute sets are identical field for field — same title/description "Colorectal cancer microbiome", same `project name`, same coordinates (54.979608, -1.621687), `collection date` = "not provided" in both. Nothing distinguishes them in the archive. The own-data paper PMC13629894 (COLO-COHORT baseline characteristics, cached) describes the microbiome sub-study and FIT-derived stool samples but never mentions a second or repeat stool sample, and contains no occurrence of 1970 / 1925 / "two stool" / "second stool".
**Conclusion.** What `.1` / `.2` encodes (repeat visit, aliquot, re-sequencing) is **not stated in the project record or the paper**. The stripped ids are not clearly wrong — the pattern (only 45 ids suffixed, each exactly twice) is consistent with two samples from one participant, which is what stripping produces — so no correction rows were written. If the suffix were part of the participant identifier, the catalog would be merging 45 pairs of distinct people; nothing I could open supports or refutes that, and the only way to settle it is to ask the submitter.

---
## Row inventory
| # | study | field | action | old_value | rows matched |
|---|---|---|---|---|---|
| 0 | PRJEB45799 | health_condition | retire | preterm_nicu | 47 |
| 1 | PRJEB45799 | health_condition_detail | retire | (any) | 2394 |
| 2 | PRJNA310878 | age_at_collection_days | retire | (any) | 21 |
| 3 | PRJEB14383 | country | recode | PH | 1 |
| 4 | PRJEB14383 | country | recode | PH | 1 |
| 5 | PRJEB14383 | country | recode | PH | 1 |
| 6 | PRJEB14383 | country | recode | PH | 1 |
| 7 | PRJEB14383 | country | recode | PH | 1 |
| 8 | PRJEB14383 | country | recode | PH | 1 |
| 9 | PRJEB14383 | country | recode | PH | 1 |
| 10 | PRJEB6456 | country | recode | (any) | 0 |
| 11 | PRJNA929328 | sex | retire | (any) | 101 |
| 12 | PRJNA1237910 | sex | recode | female | 62 |
| 13 | PRJNA1184021 | sex | recode | female | 36 |
| 14 | PRJNA746290 | health_condition | recode | other_disease | 1868 |
| 15 | PRJNA746290 | health_condition | recode | healthy_control | 11 |
| 16 | PRJEB6070 | health_condition | recode | other_cancer | 91 |

## What I could not check
- PRJNA310878: the age unit (no publication exists for the accession in Europe PMC; the archive states no unit).
- PRJEB14383: no source opened states "United States" in words; the US recode rests on the dbGaP HMP-A study named in each sample title.
- PRJEB45443: the supplementary table's row-to-sample join (SampleID column vs ENA aliases).
- PRJEB6456: whether any deposited sample falls outside the Backhed 2015 Swedish cohort; the ENA sample attributes contradict the paper and were not reconciled.
- PRJEB6070: the per-sample mapping between the 199 deposited samples and the paper's 156 French participants.
- PRJEB91658: the meaning of the alias suffix.
- PRJEB45799: whether `health_condition_detail` is meant as a user-facing value or as raw-source provenance (decides whether the second retire row should be applied).
