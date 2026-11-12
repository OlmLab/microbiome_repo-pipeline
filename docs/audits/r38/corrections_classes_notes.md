# Correction notes — four classes of suspect `health_condition` values (release R2026.38)

Input: `cache/fix_r38/class_*.csv` against package R2026.37
(`catalog_sample_determinations.parquet`, rows with `release_retired` null).
Output: `corrections_classes.csv` (285 rows: 34 project-level, 251 per-sample).

**How to read the rows.** `action=recode` with `new_value == old_value` is a re-anchor: the code is
right but its current evidence (a bare sample title, a `feces [ENVO:00002003]` string, a comorbidity
flag) does not state it, so the row replaces the evidence with a project-level statement that does.
`old_route` is set on every row so the match cannot reach values that arrived by another route.
Every quote in the CSV was checked programmatically to be a contiguous substring of the source it
names (ENA study/BioSample XML fetched this session, or the cached Europe PMC full text).

---

## 1. `preterm_nicu` without prematurity wording (7 projects, all reviewed)

| project | decision |
|---|---|
| PRJEB37883 | **Keep, re-anchored.** 20 samples got the code from sample titles (`G11-7`). The project description states the cohort is very low birth weight infants in three NICUs, so the code holds for every sample. |
| PRJEB39610 | **Keep, re-anchored.** 12 samples got the code from `nec: N` (no NEC). Both arms of the study (NEC cases and matched controls) are preterm infants, so `preterm_nicu` is supported; the title is the anchor. *Alternative considered:* recode to `healthy_control` to match the 437 `noNEC` siblings that the supplementary table put in that bucket. Rejected — `nec: N` states only the absence of NEC, and these infants are NICU patients, not healthy volunteers. Flagged below as an internal inconsistency for the owner. |
| PRJEB45799 | **Retire (47 samples).** Person-to-person transmission cohort; the `phenotype` attribute encodes sex and age only (`male_subject_of_0.328767123_years`). Confirmed directly on BioSample SAMEA10739526 (ENA XML): attributes are phenotype, subject_id, gender, organism — nothing about prematurity, NICU care or health. Nothing in the project supports a health_condition for these samples, and the cohort is not a preterm cohort, so retire rather than recode. |
| PRJEB79896 | **Keep, re-anchored.** 945 samples got the code from `environmental_medium: feces [ENVO:00002003]`. PRIMAL trial description: preterm infants born at 28–32 weeks gestation. |
| PRJNA1280936 | **Keep, re-anchored.** Code came from the BioSample title "Child stool metagenomic sample"; project title states the cohort is NICU infants. |
| PRJNA884103 | **Keep, re-anchored.** Code came from `exp_type: control` (239 samples). The title places the whole cohort in the NICU; "control" is the case-control arm of a bloodstream-infection study, not a statement of health, so it is not evidence for `healthy_control`. |
| PRJNA889771 | **Keep, re-anchored.** Code came from sample titles (`Infant Mouth #5 DOL 59`); the title states the infants are premature. Applies to gut, mouth and skin samples alike. |

## 2. Bare cancer strings (16 projects, all reviewed)

**Recoded to a more specific code**

* **PRJEB6070** — `Diagnosis` takes Cancer / Normal / Small adenoma / Large adenoma in a colorectal screening cohort → `colorectal_cancer` (91).
* **PRJNA389927** — `DiseaseClass` Healthy / Adenoma / Cancer; description: subjects with "healthy, adenomatous (precancerous), or cancerous colons" → `colorectal_cancer` (56).
* **PRJNA987980** — 261 simulated (CAMISIM) samples; the description names PRJEB6070 and PRJEB7774 as the real source cohorts and the `diagnosis` vocabulary is identical to PRJEB6070's. PRJEB7774 is "Gut microbiome development along the colorectal adenoma-carcinoma sequence" in this catalog → `colorectal_cancer`, confidence 0.65 (simulated samples; PRJEB7774 identified from the catalog study row, not from its own record).
* **PRJNA314481** — `disease` takes Normal / Adenoma / Tumor on colonic biopsies → `colorectal_cancer` (10), confidence 0.7 (no linked paper; one-sentence description).
* **PRJNA943491** — `host_phenotype` cancer / healthy on colorectal mucosa → `colorectal_cancer` (10).
* **PRJEB110936** (reached via class 3) — `host_health_state: diseased`; all specimens from bladder cancer patients → `other_cancer` (174).
* **PRJEB68381** — the single `other_cancer` sample came from `gastrointestinal_cancer_in_past: yes`, a comorbidity flag in an acute-pancreatitis cohort → `other_disease`.
* **PRJEB64515 / PRJEB91718** — `tumour: yes` on COVID-19 inpatients; see class 3.

**Left alone** (bare string is right because the cohort has no more specific code in the vocabulary):
PRJNA1235197 (mixed-cancer Mayo cohort, `group: Cancer`; specific ICD-coded siblings already carry
finer codes), PRJEB91542 (`primaryTumorType: Carcinoma` in the multi-tumour DRUP-MSI cohort),
PRJNA1472545 (biliary malignancy), PRJNA1001944 (breast cancer), PRJNA681889 and PRJNA707099
(cryptogenic malignancies in body fluids), PRJNA800058 (melanoma; the code came from
`hp_serology: Positive`, which is the wrong evidence, but the value is right and no finer code
exists — per the brief for this class, left as is), PRJNA1295573 (breast cancer; code came from
`tumor_er: Positive`, same reasoning).

## 3. Flag-like values as condition (28 projects ≥30 samples reviewed; see "not reviewed" below)

**Per-sample, split by `subject type` (the brief's worked example)**

* **PRJEB64515** — 251 per-sample rows. 199 `other_disease` + 45 `other_cancer` samples carry
  `subject type: patient (case)` → `other_infection` (hospitalised COVID-19, project description).
  7 `other_disease` samples carry `subject type: control` → `healthy_control`, anchored on the
  paper: "Additional 147 healthy subjects of medical staff ... were included as a control group."
  Only values that came from the bare `yes` flags (`other_medical_conditions`, `tumour`) are
  touched; values from named comorbidities (lymphoma, leukaemia, type II diabetes) are left alone
  and reported below.
* **PRJEB91718** — same cohort, deep-sequencing deposit; all 73 `other_disease` and all 24
  `other_cancer` samples are `patient (case)` and all came from `yes` flags, so two project-level
  rows → `other_infection`. The 141 controls here are already `healthy_control`.

**Recoded to a different code**

* **PRJEB98012** (Creeping Fat) — `status: disease` (270) → `crohns_disease`, confidence 0.65. The
  description is "A study of bacterial translocation into mesenteric fat in Crohn's disease." and 47
  specimens are labelled `creeping_fat`, a Crohn's-specific lesion. Least certain recode in the set:
  there is no per-sample diagnosis attribute and no own-data paper, so a non-Crohn's surgical case
  inside the disease arm cannot be excluded; `ibd_unspecified` is the fallback if the owner prefers.
* **PRJEB110936** — `host_health_state: diseased` (174) → `other_cancer` (bladder cancer patients).
* **PRJEB101120** — `host_disease_status: Case` (68) → `allergy_or_atopy` (atopic dermatitis; one
  sibling sample in the project already carries that code).
* **PRJEB101152** — `host_health_state: diseased` (41) → `ibd_unspecified` (biopsies from IBD
  patients, mixed CD / UC / IBD-U, so the parent code). Not in `class_ibd_parent_codes.csv`.
* **PRJEB48814** — `autoimmune_disease_any: yes` (31) → `type1_diabetes`. All 31 carry
  `analysis_disease_stage: T1D + normal weight` (verified for all 31); their `T1D + overweight`
  siblings are already `type1_diabetes`.
* **PRJNA1078999** — supplementary column `Anemia=1` (64) → `preterm_nicu`, confidence 0.7. The flag
  is a comorbidity the vocabulary cannot express; every sample of the deposit is a preterm infant in
  the first week of life. *Alternative:* retire, if the owner would rather not mint a legacy infant
  code from a project-level statement.

**Retired**

* **PRJNA1083321** — `isolate: case` (165). Nothing in the project ("Normal DNA-seq of human
  faeces"), in the attributes or in a paper names a condition; `case` alone supports no value.

**Kept, re-anchored** (code right, flag uninformative): PRJNA699976 (`disease: Disease` → pneumonia
/ ARI patients, `other_infection`), PRJNA1168594 (`subject is affected = Yes` → ichthyosis; no
specific code in the vocabulary, so `other_disease` stands), PRJNA1090271 (`Case` → melioidosis
patients), PRJEB35615 (`description: patient` → 57 AMD cases, matching the 57 samples),
PRJNA885137 (`description: Case` → SARS-CoV-2-positive outpatients, paper quote), PRJNA401385
(`Patient` → TB patients; the quote reproduces the misspelling "beween" in the ENA description).

**Left alone** (the attribute key itself names the condition, so the flag is informative):
PRJNA1167328 (`sars_cov2_status: Positive`), PRJNA797994 (`helminth: Yes`), PRJEB76817
(`HIV_Status=positive`), PRJNA307231 and PRJNA947377 and PRJNA836336 (`hiv_serostatus/hiv_status:
Positive`), PRJNA1304731 (`Pneumonia = Yes`), PRJNA1450325 (`any_diagnosed_disease_yes_no: YES` →
`other_disease` is the right broad code), PRJNA850529 (the `TRUE` flags sit in named supplementary
columns — `disease_hypertension`, `disease_heart`, `disease_autoimmunological`,
`disease_digestive_celiac` — and each maps correctly), PRJEB86785 (`host health state: diseased` in
a mixed inpatient/outpatient antimicrobial-exposure cohort with no specific condition),
PRJNA1314973 (`disease: diseased`; no disease named anywhere in the project).

## 4. ME/CFS (all projects in the table plus one found by title search)

**Decision: ME/CFS is coded `neurological_psychiatric`.** The vocabulary has no ME/CFS code. The
realistic candidates are `neurological_psychiatric` ("Neurological / psychiatric (PD, MS, AD, ASD,
depression, schizophrenia)") and `other_disease` ("Other disease / condition"). `neurological_psychiatric`
is what two prior curated resources use for ME/CFS, it is already what three of the catalog's own
ME/CFS projects carry (PRJNA1125469 319 samples, PRJNA954397 15, PRJEB57952 5), and keeping the
remaining projects on `other_disease` would split one condition across two codes. Applied
consistently; controls were moved/kept at `healthy_control` only where the source says healthy.

* **PRJNA751448** — `mecfs_status: Y` (106) `other_disease` → `neurological_psychiatric`; paper:
  "The study included stool samples from 106 ME/CFS cases". The 91 `mecfs_status: N` controls keep
  `healthy_control` but are re-anchored to the paper ("91 healthy control individuals recruited from
  5 sites in the United States") because `N` states only the absence of ME/CFS.
* **PRJNA878603** — `illness_duration: Short` / `Long` (146) → `neurological_psychiatric`; the
  description identifies both as ME/CFS patient cohorts. The 92 `illness_duration: Control` samples
  are already `healthy_control` and were left alone.
* **PRJNA379741** — found by searching study titles for fatigue / ME/CFS (not in the input table).
  50 samples carry `external.cmd` `disease: Chronic Fatigue Syndrome` coded `other_disease` →
  `neurological_psychiatric`. Its 50 controls are already `healthy_control` from
  `description: Control`.
* **PRJEB57953** — 3 samples with BioSample description "Severe ME/CFS" (`other_disease`) →
  `neurological_psychiatric`. 4 samples with "Diagnosed with IBS and severe ME/CFS" (`ibs`) →
  `neurological_psychiatric` at confidence 0.7: one code per sample, and the cohort condition of
  this ME/CFS virome study is ME/CFS; the IBS comorbidity belongs in `health_condition_detail`.
  This is the one decision in class 4 that discards a true, more specific statement — flag for the
  owner if the preference is to keep comorbidity codes where they are stated.
* **PRJNA1125469, PRJNA954397, PRJEB57952** — already `neurological_psychiatric`; left alone. The
  five `healthy_control` samples of PRJEB57952 and PRJEB57953 come from "same household healthy
  controls (SHHC)", which says healthy — left alone.
* Title search for `fatigue|ME/CFS|MECFS|myalgic|chronic fatigue` over all 5,052 catalog studies
  returned only these plus PRJEB49262 (Depression and Fatigue in IBD — already `ibd_unspecified`)
  and PRJNA1269031 (a Bif195 trial in IBS — already `ibs`); neither is an ME/CFS cohort.

---

## Not reviewed

Class-3 rows with fewer than 30 samples, per the brief. Projects marked * were nonetheless resolved
because another row of theirs is in scope.

PRJEB91718*(24), PRJNA1430386(22), PRJNA521878(20), PRJNA1057503(20), PRJEB55787(20), PRJEB38997(19),
PRJNA521455(18), PRJNA800058*(16), PRJEB59450(15+4), PRJNA1176463(15), PRJNA728680(14), PRJDB41210(14),
PRJEB13835(12), PRJEB50408(10), PRJNA850529*(10+1), PRJNA1306676(9+2), PRJEB68381*(9+1),
PRJNA283642(6), PRJNA221620(6), PRJNA688274(4), PRJNA895487(4), PRJNA834801(2), PRJNA1450325*(2),
PRJNA821920(1), PRJNA1295573*(1).

Two of these look like the same error pattern and are cheap for a later pass: PRJEB68381
`cardiometabolic_other` (9, `cardiovascular_disease_in_past: yes`) and `other_disease` (1) in an
acute-pancreatitis cohort — the same comorbidity-outranks-cohort-condition problem as its
`other_cancer` row, which is corrected here; and PRJNA895487 `other_disease` (4,
`pica_disease_biospecimen: True`) in an autism cohort.

## Other errors noticed (outside the four classes, not corrected here)

1. **PRJEB64515** — 289 samples whose BioSample says `subject type: control` carry `other_infection`
   from `external.gmrepo` (`phenotype: COVID-19`, route R2). The external source labels the healthy
   staff controls as COVID-19 cases. This is the largest remaining error in that project and is not
   reachable through the four classes.
2. **PRJEB64515 / PRJEB91718** — patient samples also carry `type2_diabetes` (95 / 47),
   `other_cancer` from `location_of_the_tumour` (lymphoma 10, leukaemia 6), `colorectal_cancer`
   (6 / 1) and `type1_diabetes` (1). These come from named comorbidity attributes rather than bare
   flags, so they fall outside the class definitions, but by the owner's rule for this cohort
   (comorbidity must not outrank the cohort condition) they should also become `other_infection`.
3. **PRJEB39610** — 12 `nec: N` samples kept `preterm_nicu` while 437 samples with the same meaning
   (`PreNEC=noNEC`, supplementary table) are `healthy_control`. One of the two should move.
4. **PRJNA987980** — 1,029 CAMISIM-simulated samples carry human `health_condition` values
   (and `country`, `age_category` in the wide table). They are not human specimens; the catalog may
   want a sample-type flag rather than clinical metadata on them.
5. **PRJEB98012** — 67 samples are blanks and positive controls (`katharoseq_*` attributes,
   `empo_1: Control`, `sample_type: control blank`) with `status: not applicable`; they sit in the
   human catalog without a health_condition.
6. **PRJNA1001944** (breast cancer cohort) carries `ibs` on 51 samples and `crohns_disease` on 3,
   alongside `other_cancer` 80 — worth checking the source of those codes.
7. **PRJNA1167328** — 464 `sars_cov2_status: Negative` samples have no `health_condition` while the
   480 positives do. Not an error, but the negatives are a defined comparison group and are
   currently invisible as such.
