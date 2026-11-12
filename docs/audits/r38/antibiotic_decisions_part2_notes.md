# antibiotic_statements_part2 - audit notes (R2026.38 fix pass)

Input: `cache/fix_r38/antibiotic_statements_part2.csv` (242 rows, 241 projects, R2026.37 package).
Output: `antibiotic_decisions_part2.csv` (one row per input row, input order).
Sources opened this session: cached PMC OA texts under `cache/pmc_oa/`, Europe PMC
`fullTextXML` / `search` for papers with no cache, and ENA `browser/api/xml/<PRJ>` project records.
Everything fetched is under `out/fetched/` so the quotes can be re-checked.
Every `paper_quote` in the output was verified programmatically to be a contiguous verbatim span of one of those files.

## 1. The three projects a previous check had flagged
* **PRJEB110772 (row 2, 2,398 samples) - confirmed wrong.** "Study participants with any prescription for oral
  antibiotics in the past 90 days (n = 276) ... were excluded from analysis, yielding a complete dataset of 2,081
  study subjects." The exclusion is at analysis; the deposit still holds all 2,398 samples. **retire**.
* **PRJEB38742 (row 6, 1,018 samples) - doubt not confirmed.** In the Methods "Study population" section the sentence
  is a recruitment-stage non-inclusion for the MetaCardis cohort ("... were not included"), listed between the
  recruitment sites and the consent statement, and "a detailed list of prescribed medications, anthropometric data,
  clinical history, fecal sample, and a fasting blood sample was obtained at enrollment". **keep**.
* **PRJNA943687 is not present in this input file** (it is not among the 241 projects of part 2), so it was not
  checked here.

## 2. Catalog errors found that are not antibiotic_exposure errors
* **PRJNA544527 (row 16, 563 samples) - wrong paper-study link.** Evidence locator PMC10039858 is "The genomic
  landscape of reference genomes of cultivated human gut bacteria"; it mentions PRJNA544527 only as a downloaded
  reference genome collection ("3423 isolated genomes of Broad Institute-OpenBiome Microbiome Library (BIO-ML ...)").
  The quoted donor sentence describes that paper's own 299 donors. Any other value this project inherited from
  PMC10039858 should be reviewed, not just antibiotic_exposure.
* **PRJEB76849 (row 101 samples, row 146)** carries the quote "Written informed consent was obtained from all
  participants" for antibiotic_exposure=no, and the cached full text contains no antibiotic statement at all.
* **n_samples > n_samples_project** in the input table for rows 102 (149/89), 202 (78/1), 276 (47/1), 386 (22/1),
  450 (6/1). The per-row sample counts and the project totals disagree; worth checking in the build that produced
  the input, independently of these decisions.
* **PRJNA542578 (row 98)**: the paper also describes mice gavaged with an antibiotic cocktail before human-faecal
  transplantation. If the deposit's 151 samples include the mouse recipients, the human cohort statement does not
  apply to them (scope/host check, not an antibiotic-statement error).

## 3. Per-sample antibiotic data that the catalog could use instead of a cohort statement
* **PRJDB5180 (rows 466, 484)**: the paper tabulates preoperative antimicrobials per patient -
  "Antimicrobial agents CTRX SBT/CPZ SBT/CPZ NA SBT/CPZ CTRX" for P1-P6. The catalog currently carries only P6 and
  P3; P1, P2 and P5 can be filled the same way, and **P4 is NA** (i.e. no antimicrobial - a 'no' value, not 'yes').
* **PRJNA1079705 (row 22, PLAY-ON)**: participants "began the antibiotic and yogurt interventions on Day 1" with a
  Day 0-180 timeline and a per-participant antibiotic (74% amoxicillin). A per-timepoint value ('no' at Day 0,
  'yes' afterwards) is derivable if sample names carry the study day.
* **Before/during/after designs where a per-timepoint value would be correct and a single project-wide value is
  not**: PRJNA604820 (row 38, before/during/up to 1 year after), PRJEB1389 (row 384), PRJNA894204 (row 184,
  baseline / last day of treatment / 9 months after), PRJNA530252 (row 96, baseline / weeks 1-4 / follow-up),
  PRJNA851177 (row 304, baseline / end of 4-week treatment), PRJEB6358 (row 252, daily sampling after an
  azithromycin dose at admission), PRJEB22175 (row 158, days 0/7/14/28/90 post challenge).

## 4. Judgment boundaries used (so they can be tightened or loosened centrally)
* **Look-back windows are kept** ("no antibiotics in the previous 3 months"), per the task rule, even when short.
  Flagged case: **PRJNA1148144 (row 128)** uses a 48 h window in a neonatal cohort ("Infants were excluded if they
  appeared critically ill, had received antibiotics in the preceding 48 h"); kept, but a 48 h window does not rule
  out the neonatal antibiotic courses that the field (any antibiotics before sampling) is meant to capture.
* **Longitudinal deposits**: retired when sampling continues far past the look-back window with no re-screening and
  antibiotics are plausible - PRJEB101630 (row 18, 3-year follow-up in children, antibiotic intake collected by
  questionnaire during follow-up), PRJNA1226748 (row 142, half the samples from a follow-up one year later),
  PRJEB56830 (row 26, "Most common reasons for dropout were antibiotic usage during the trial (N = 3)"),
  PRJNA688274 (row 422, criterion covers only the 3 months before travel while samples are post-travel).
  Kept where sampling is at or near enrolment or the criterion explicitly covers the study period (e.g. row 8:
  "... within 1 month before baseline or during the study"; row 82: "within 6 months prior to and during the study").
* **Analysis exclusion but the deposit holds only the retained subjects**: PRJNA1263627 (row 346) - nine antibiotic
  users were dropped before analysis and the project holds exactly the 30 analysed participants, so the value still
  covers every deposited sample. Kept, category recorded as analysis_exclusion.
* **not_supported calls that are wording judgments** (owner may want to revisit): PRJEB108118 (row 40) - the
  criterion names "any treatment ... that could affect the gut function", never an antibiotic; PRJNA768298
  (row 258) - "a medicine history of oral microbial agents", which does not unambiguously mean antibiotics;
  PRJNA1169096 (row 116) - "discontinue antibiotics 48 hours prior to the screening colonoscopy" implies possible
  recent use rather than absence.

## 5. What was checked how
* 72 rows were decided with a paper (or, for row 208, the Europe PMC abstract record) open: all 23 rows with
  n_samples >= 300, every row whose quote paired an exclusion with a count or the word analysis, every row whose
  quote was a fragment that did not name an antibiotic, and the antibiotic-intervention deposits.
* 10 rows (112, 158, 162, 172, 256, 272, 424, 450, 452, 458) are R4 values whose evidence is the ENA study
  description. The full description was fetched and read; `paper_quote` holds the verbatim description span and
  `checked` stays `quote_only` because no paper was opened.
* The remaining 160 rows are plain eligibility/exclusion criteria or direct cohort statements with a look-back
  window, decided from the quote.

## 6. Could not check / residual uncertainty
* PMC12335371 (row 46) returned HTTP 500 from Europe PMC `fullTextXML`; the text was obtained from NCBI efetch
  (`out/fetched/PMC12335371_nc.xml`) and the row was decided from it.
* The intervention/design retires (rows 112, 158, 162, 172, 256, 272, 424, 450, 452, 458, 470, and 4, 22, 28, 38,
  384, 432, 438) rest on the design described in the paper or project record, not on a per-sample timepoint list.
  Where sample names carry time points, a per-sample value is recoverable; this audit did not enumerate samples.
* PRJNA943687 (named in the task) is not in this input file.
