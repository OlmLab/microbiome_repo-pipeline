# antibiotic_statements_part1 - audit notes (R2026.38 fix pass)

Input: cache/fix_r38/antibiotic_statements_part1.csv (242 rows, 242/242 decided).
Output: antibiotic_decisions_part1.csv (same directory, out/).
Decisions: 210 keep, 32 retire. Retired statements cover 5,819 of the 31,365 sample-values in this file.

## Conventions used
* Every `paper_quote` is a contiguous span of a source opened in this session, verified
  programmatically (whitespace-normalised containment) against the cached/fetched text, the ENA
  project XML, the catalog study description, or the Europe PMC abstract record.
* `checked = paper` means a paper (or a Europe PMC abstract record) was opened. For R4 rows whose
  evidence *is* the archive description (rows 15, 17, 31, 45, 73, 129, 161, 215, 415, 449, 459)
  there is no paper to open; the full ENA/BioProject record was read instead and `checked` is
  `quote_only`, with the longer verbatim span in `paper_quote`.
* Row->sample mapping was verified: for all 242 rows, the number of determination rows carrying that
  exact (study, value, quote) equals `n_samples`, so decisions apply to exactly the intended samples.

## The failure mode this pass was looking for - confirmed cases
* **PRJNA943687 (row 3, 2,089 samples, the project flagged by the earlier check).** The sentence
  ("exposure to antibiotics within one month before the study were excluded") is a genuine
  recruitment criterion, but it belongs to the own_data paper PMC11540074, whose cohort is
  **598 children** (discovery 273 + validation 325). The deposit holds 2,089 samples, its ENA
  description says "This dataset will continue to be updated as more metagenomic data are included",
  and registry_study_papers lists four linked papers (one own_data, two unsure, one cites_accession).
  The criterion cannot be extended to all deposited samples -> retire.
* **PRJEB28671 (row 189).** "...16 samples from 15 children who received antibiotics were also
  removed leaving 1,389 samples from 633 children for detailed analysis" - removal at analysis; the
  1,616-sample deposit still contains those samples. The paper even localises them: "Antibiotic
  treatment affected 15 of the day 85 samples and one day 15 sample."
* **PRJEB58222 (row 319).** 7 of 30 recruited patients "were excluded from the analyses (n = 7)",
  partly for antibiotics; the deposit has 37 samples.
* **PRJEB120968 (row 215).** Archive description: individuals with recent antibiotic use "were not
  included in the analysis" - says nothing about what is deposited.
* **Antibiotic-intervention deposits carrying cohort-wide 'no'** (the mirror image of the problem):
  PRJEB28058 (164), PRJNA1364832 vancomycin pretreatment (129), PRJNA1138254 PSC-Vanc (60),
  PRJNA588313 (59), PRJEB56022 (312, infections requiring antibiotics during a 2-year trial),
  PRJNA506988 DFU (195, "a subset of patients were administered systemic antibiotics at some time
  during the study"). In each the enrolment criterion is real but only covers the pre-treatment state.
* **'yes' statements that are study designs spanning a baseline**: PRJNA749138 (121 samples =
  44 baseline + 44 six-week + 33 six-month), PRJEB52093, PRJNA993161, PRJNA1463324 (stool collected
  "during screening", before vancomycin), PRJNA545794, PRJDB14272, PRJEB48983, PRJEB21338, PRJEB18265,
  PRJEB5771, PRJEB58815, PRJNA703034, PRJEB31957.
* **Quote does not say it at all**: PRJNA565546 (row 243) - the quote is "none of the participants had
  a history of fractures" and the paper contains **zero** occurrences of "antibiot"/"antimicrob";
  PRJNA1457641 (row 31) - "without routine antibiotic use" describes the farming community's practice;
  PRJNA484151 (row 431) - the quote is a discussion claim about resistance genes;
  PRJNA748448 (row 427) and PRJEB22207 (row 443) - both quotes are figure-legend/timeline fragments.

## Statements that look like analysis exclusions but were kept
* **PRJDB41738 (row 5, 1,342 samples).** "Exclusion criteria ... (i) use of antibiotics within 3 months
  at sample collection ... Finally, a total of 1,342 participants were included in this analysis"
  (from 1,423 available). The deposit (Sun Yat-sen University) holds exactly 1,342 samples, i.e. the
  excluded 81 are not in it -> keep.
* **PRJNA785561 (row 133).** "Participants were excluded from this analysis if they ... reported
  antibiotic use in the past month" - but the deposit holds exactly 110 urethral samples and the paper
  analyses 110 men ("108 of 110"), so the 54 screened-out men are not deposited. This keep rests on a
  count match, not on a statement; it is listed as unverified below.

## Per-sample antibiotic data the catalog could use instead of a cohort statement
* **PRJNA506988** (Kalan et al., DFU): per-visit antibiotic annotation - "The letter 'A' indicates a
  visit where antibiotics were administered", and 30 patients received antibiotic therapy.
* **PRJEB28671**: the 16 antibiotic-exposed samples are identified by day (15 of day 85, one of day 15).
* **PRJDB5180**: Table 1 gives the agent per patient (P1 CTRX, P2/P3/P5 SBT/CPZ, P4 none, P6 CTRX);
  the catalog already uses it for P1/P2/P5 and correctly leaves P4 unset.
* **PRJEB10391**: per-sample timepoint labels already in the catalog ("baseline stool sample",
  "Day 1/3/6 of ciprofloxacin treatment", "Day 2/28 after ciprofloxacin treatment") - this is the
  model case of a per-sample split done right.
* **PRJNA1210004**: a questionnaire at *each* collection recorded "the consumption of probiotic
  products or antibiotics in the six months prior to the collection".
* **PRJNA613054**: the parent RCT randomised 147 infants 1:1:1 to three regimens with an untreated
  control group, across six BioProjects - arm-level assignment is possible from the trial tables.

## Other errors noticed (outside this field)
* Row 27 (PRJNA1128723): `text_paths` points at PMC11733223 while `evidence_locator` is PMC12345167.
  The quote is verbatim in the locator paper; the other paper carries a near-identical sentence
  ("the subject ... two weeks" vs "the participant ... 2 weeks"). Checked against the locator paper.
* `n_samples_project` in the input is unreliable for some rows: rows 121, 241, 279 give 1; rows 443
  (16 vs 22), 397 (20 vs 32), 475 (305 vs 305 values but 2 carrying this quote) disagree with the
  determinations table. `n_samples` itself matched exactly for all 242 rows.
* PRJNA841786 (row 1): the paper recruited 2,320 subjects but the deposit carries 2,470 samples;
  the enrolment criteria cover both patients and healthy controls, so the value was kept.
* PRJNA1077691 (row 19): the deposited samples are cultivation-derived communities (51 media
  modifications x 6 donors); the 'no' is a property of the six stool donors, not of 498 human subjects.
* PRJNA396794 (row 475): the two samples carrying 'yes' have BioSample attribute dol=0, i.e. day of
  birth, while the quote covers antibiotics given "during the first week" -> retired.
* Several PRJDB study titles in the catalog are the paper title with a PRJCA accession appended
  (PRJDB41738, PRJDB41048, PRJDB37151); cosmetic, but it makes the deposit look like a Chinese
  GSA submission.

## Not verified / residual risk
* Row 41 (PRJNA770647, 342 samples): full text not open access (Europe PMC fullTextXML returns HTTP
  500); decided from the quote plus the Europe PMC abstract. The abstract shows a 12-month
  longitudinal design, so antibiotic use during follow-up could not be checked.
* Rows 15, 17, 31, 45 (all >=300 samples): no linked paper in the input row, so the archive project
  record was read instead of a paper.
* Row 25 (PRJNA1258884): kept, but one participant withdrew because of antibiotic treatment during the
  crossover; whether that participant's samples are deposited was not established.
* Row 123 (PRJNA1210004): kept on the enrolment criterion; per-collection questionnaire data exist but
  were not consulted.
* Row 473 (PRJNA221723): the day of life of the two deposited samples is not recorded in the archive
  ("newborn (premature)"); the keep rests on Table 1 (ampicillin + gentamicin DOL 1-7) plus
  "Fresh fecal samples were collected every 3 days" over the first month.
* Row 161 (PRJNA978345) and row 321 (PRJEB66728): the cohort descriptors ("ongoing prophylactic
  amoxicillin/clavulanate"; "antibiotic free infants") were taken at face value; no per-infant data.
* PRJEB38742 and PRJEB110772, named in the brief, are **not** in part1 of the input.
