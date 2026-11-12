# lifestyle_decisions_part1 — notes

Scope: all 88 projects in `lifestyle_candidates_part1.jsonl` (15,617 samples). One row per project, input order.

## Method
1. Read the input candidates and `pass_output` for every project.
2. Built a sentence corpus from every readable paper linked to each project: the cached
   `pmc_oa` plain texts listed in the input, plus any further PMCIDs in
   `registry_study_papers.parquet` that were already cached.
3. Only 17 of 88 projects had any candidate sentence from the literal vocabulary match terms,
   so each project's full text was re-scanned with a widened per-code pattern that also catches
   the "equivalent wording" the vocabulary allows (e.g. *city / metropolitan / residing in /
   living in / inhabitants* for `urban_industrialized`, *care home / residential care / inmate*
   for `institutionalized`). This widened pass is what the decisions are based on; candidate
   lists alone would have under-detected.
4. 30 projects had no cached text. Their `pass_output.paper_id` was resolved through the
   Europe PMC search endpoint and the full text fetched from `/<PMCID>/fullTextXML`.
   28 of 30 fetched successfully; the XML is kept in `out/fetched/`.
5. Every quote in the output CSV was re-checked against the file it came from after the
   decisions were made: all 6 are contiguous whitespace-normalised spans of their source
   and all are under 60 words.

## Result
retire 82 (15,293 samples) · keep 5 (285 samples) · recode 1 (39 samples).

The high retire rate is the expected consequence of the vocabulary rule. In almost every
`urban_industrialized` project the only geographic fact in the paper is the recruitment site —
a named hospital, clinic, university or city where sampling happened — which the vocabulary
explicitly excludes as a basis for the code.

## Could not check
- **PRJNA846182** (163 samples) and **PRJNA145383** (23 samples): the linked papers
  (PMC9877180, PMC3303609) are indexed in Europe PMC but are not open access, so no full text
  could be retrieved and no other source was available. Both retired as "no accessible source".
  If the owner has subscription access, these two are the only rows in part 1 decided without
  reading any source.

## Cases a curator may want to revisit (all retired here)
These are projects where a lifestyle *is* stated, but only for part of the cohort. The catalog
stores lifestyle per sample, so several of them could carry correct per-sample values if the
sample-to-arm mapping can be established; a project-wide code cannot be supported.

- **PRJNA698366** (379 samples, Botswana): dyads were recruited at both urban and rural clinics
  and "location of residence (urban vs. rural)" is an explicit model covariate. Per-sample
  urban/rural is in principle recoverable from the study, not from the paper text alone.
- **PRJEB52962** (20 samples, Cordoba): "the majority (26/44) lived in large urban centres,
  while the remaining patients lived in villages".
- **PRJNA1521571** (41 samples, scabies): "a majority of institutionalized individuals",
  the rest walk-in outpatients.
- **PRJNA1005267** (10 samples, TBI): five residents of the CNS facility (Bakersfield, CA) plus
  five healthy controls recruited by advertisement from the Galveston, TX area — exactly half
  the samples are institutionalized.
- **PRJNA885289** (169 samples): three arms — 109 esports players, 36 physical education
  students, 25 professional endurance athletes. Only the third arm is `athlete`.
- **PRJNA971196** (16 samples): dairy workers vs community controls. Neither an occupational
  code nor the stored `rural_non_industrialized` holds project-wide; "rural" appears once, in a
  general statement about dairy workers used to argue about an unrelated comparison cohort.
- **PRJEB53437** (52 samples): the stored `traditional_agriculturalist` is contradicted by the
  paper — the eight southern African populations are cattle and small-stock herders
  (Kuvale, Himba, Kwepe), foragers (!Xun, Tshwa) and peripatetic groups (Twa, Tjimba, Kwisi),
  and the paper states "the desert soil is not suitable for agriculture, pastoralism is the sole
  food production strategy available in the region". Per-population codes (`pastoralist`,
  `hunter_gatherer`) are well supported if samples can be mapped to population.
- **PRJEB106785** (88 samples): three archaeological communities that the paper says differed in
  their lifestyles (Isola Sacra, the rural centre Lucus Feroniae, Selvicciola). Per-cemetery
  codes are supportable; a single project-wide code is not.

## Other judgement calls
- **PRJNA419274 — recode to `extreme_environment`.** Participants are members of the 34th Indian
  Scientific Expedition to Antarctica (voyage plus a stay at Maitri station). `extreme_environment`
  lists "Antarctic, expedition, polar" and is the right code; `spaceflight_or_analog` also lists
  "Antarctic station" but its label scopes it to space-analog isolation studies, which this is not.
- **PRJNA987158 — keep `other_lifestyle`.** The cohort is ten workers at one commercial
  farrow-to-wean swine operation, stated project-wide. The vocabulary has no occupational code,
  so `lifestyle_detail` should carry "commercial swine farm workers" for this to be interpretable.
- **PRJNA478488 — keep `urban_industrialized`.** The kept quote states that the subjects live in
  the cities of Baoding and Dalian. For completeness, the methods describe Baoding as "the
  relatively rural and industrial city" and Dalian as "an urbanized and modern northern Chinese
  city"; both arms are city residents, so the project-wide code holds, but a curator who wants a
  finer value should read that methods sentence.
- **PRJNA730407** (Bronze Age Quanergou cemetery) and **PRJEB49091** (18th-century Kazakh
  individuals) were retired on hedged evidence: "the cemetery ... likely used by mobile
  pastoralists" (a citation-backed guess) and nomadic wording that describes the Kazakh Khanate's
  tribal origins rather than the five analysed individuals.
- **PRJNA720904** was retired on a narrow reading: the paper says the 22 nasopharyngeal samples
  were "collected from Dhaka city of Bangladesh", which locates the sampling, not the
  participants' residence.

## Other errors noticed (outside this task's scope)
- Several projects carry linked papers with `relation` = `unsure` whose subject matter is
  unrelated to the project, and their sentences enter any full-text evidence pass for that
  project. Clear examples: PMC11986028 ("phyllosphere virome of fresh vegetables") on
  PRJEB42612, an at-risk rheumatoid arthritis virome study; PMC12749156 ("toxic genomic R-loops
  ... biofilm") on PRJNA595703, an airway microbiome study; PMC9600381 ("cobamide sharing") on
  PRJNA266117. The westernization/industrialized background sentences that triggered part of this
  candidate list for PRJEB42612 come entirely from that unrelated paper.
- `pass_output.quotes.lifestyle` is frequently not a lifestyle statement at all. Examples seen
  here: PRJNA841786 stored a diet/ethnicity sentence ("All subjects are on a stable traditional
  Chinese style diet and are of Han Chinese ethnicity"), PRJEB49124 stored "community-living
  participants", PRJNA1136496 stored an ICU length-of-stay criterion, PRJNA478488 stored the
  literal string "urban_industrialized". This is consistent with the reported defect that the
  pass returned a code without a supporting quote.
- **PRJNA1209946** ("The human face microbiome is unique compared to other hominids") samples zoo
  care staff alongside zoo-housed chimpanzees and gorillas; worth confirming that none of its 19
  catalog samples are non-human. Similarly **PRJNA145383** pairs human twins with gnotobiotic
  mice. Not verified here.

## Files in out/
- `lifestyle_decisions_part1.csv` — the decision table (88 rows, input order).
- `lifestyle_decisions_part1_notes.md` — this file.
- `fetched/*.xml` — 28 Europe PMC full texts retrieved for projects with no cached text.
- `_scan_bundle.json` — the ranked candidate sentences per project that the decisions were read from.
