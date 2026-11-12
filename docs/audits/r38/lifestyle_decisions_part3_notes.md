# lifestyle_decisions_part3 — notes

## What was checked
87 projects (13,833 samples) from `lifestyle_candidates_part3.jsonl`. All 87 catalog rows carry
`evidence_source=paper.fulltext.methods`, `route=R3` and `evidence_quote == field_value` (the code
itself) — i.e. no real quote exists for any of them. Catalog row counts per project match the
`n_samples` in the input exactly (checked against `catalog_sample_determinations.parquet`,
`release_retired` null).

Procedure: for each project I scanned the *whole* cached full text (not only the supplied candidate
sentences) with a broad lifestyle regex (all vocabulary codes' terms plus residence wording:
resident/residing/living in/inhabitants/city of/town of/province of), ranked the hits, and read them
with the study title, cohort arms and `pass_output` places. Every quote written to the CSV was
extracted programmatically from the source file and re-verified as a whitespace-normalised
contiguous substring of that file (11/11 verified).

## Sources
* 52 projects: full text supplied in the input (`papers[].text_path`) — `source_checked=cached_text`.
* 35 projects: no paper in the input. Their `pass_output.paper_id` was resolved once through the
  Europe PMC REST search API; all 35 resolved to a PMCID that was already present in the local
  `cache/pmc_oa` store, so no full text had to be downloaded. These rows are labelled
  `source_checked=europepmc` (that is how the paper was *located*; the text read was the local cached
  copy). One of them (PRJNA1115111, PMC12335371) is flagged `isOpenAccess=N` in Europe PMC but a
  cached text was available.
* Every keep/recode paper was checked to contain the project accession in its text, so it reports the
  project's own data (11/11).
* No project ended with `source_checked=none`; nothing was decided on "no accessible source".

## Decisions: 76 retire, 11 keep/recode (10 keep, 1 recode) — 87 rows total

### Kept
| project | code | basis |
|---|---|---|
| PRJNA780005 | other_lifestyle | all sampled individuals called "pre-industrialized" |
| PRJNA1330355 | institutionalized | all 38 sampled people are nursing-home residents |
| PRJNA1066963 | rural_non_industrialized | children sampled in 150 Niger villages (communities of 200–2,000 inhabitants) |
| PRJNA608948 | rural_non_industrialized | children "in a rural area of eastern Ethiopia" |
| PRJEB55789 | hunter_gatherer | both individuals called Upper Palaeolithic hunter-gatherers |
| PRJEB84038 | other_lifestyle | "a royal lifestyle and a rich diet" attributed to the one sampled individual |
| PRJNA779415, PRJNA1024640, PRJNA1013332, PRJNA694925 | urban_industrialized | see "metropolitan" below |

### Recoded
* **PRJNA650011** other_lifestyle -> `rural_non_industrialized`: "two of the patients ... were from
  rural Shanxi and one was from rural Beijing" covers all three sampled patients. Caveat for the
  owner: the source says only *rural*; the code's label adds "non-industrialized", which the source
  does not state. It is the only rural code in the vocabulary.

## Judgment calls the owner may want to overrule

**"metropolitan area" as an urban statement (4 keeps).** PRJNA779415 ("were all living in the
Baltimore metropolitan area at the time of sampling"), PRJNA1024640 ("all from lower-income
households in the Baltimore metropolitan area"), PRJNA1013332 ("Study participants from the New York
metropolitan area"), PRJNA694925 ("recruited from the Washington, DC, metropolitan region"). I treated
these as kept because *metropolitan* is an explicit settlement-type word applied to where the
participants live/come from, and no facility is given as the residence. PRJNA694925 is the weakest of
the four (the wording is "recruited from"); if the owner wants recruitment wording excluded
altogether, that one becomes a retire.

**Borderline retires.**
* PRJEB42539 (422 samples): cohort "born in Nandipara, a peri-urban area near Dhaka" — peri-urban, and
  the paper explicitly contrasts its population with "studies mostly conducted in industrialized
  settings". Retired. `transitional_or_migrant` could be argued but is not stated.
* PRJNA905467 (142): "13 healthy men and women (UCC employees and students, residents of Cork,
  Ireland)". Residence is stated but the source never calls it urban; reading Cork as urban would be
  inferring from a city name, which the vocabulary forbids. Retired.
* PRJNA1366739 (635): "AARP members ... residing in six U.S. states ... or two metropolitan areas" —
  urban applies to part of the cohort only. Retired.
* PRJNA909599 (1): the patient is "a 66-year-old male farmer". Occupation, not a lifestyle code;
  `traditional_agriculturalist` needs subsistence/traditional farming. Retired.
* PRJNA785545 (107): the only cohort description is men who have sex with men living in the Amsterdam
  area — a residence plus a behavioural group, not a lifestyle in this vocabulary. Retired.
* PRJEB30280 (1): "hunter-gatherer" in that paper refers to the individual's genetic ancestry
  (western hunter-gatherers), not to a stated subsistence mode. Retired.
* PRJNA1037120 (15): the paper discusses the colonial enslavement context of the Anson Street
  Ancestors in general terms but makes no lifestyle statement about the sampled individuals. Retired.

**Subgroup retires (the lifestyle is real but belongs to one arm).** These are candidates for
per-sample coding in a later release rather than a project-wide code:
* PRJNA1115111 (315, indigenous_community): Orang Asli villagers **and** urban Kuala Lumpur controls —
  the paper even says "Skin swabs are collected from residents of OA villages with varied levels of
  urbanization, and from cities."
* PRJEB63347 (44, hunter_gatherer): 26 Baka (forest camp + settlement) **and** 18 Nzime villagers.
* PRJNA1155269 (112, urban_industrialized): "While Finnish Karelia is westernized, many households in
  Russian Karelia follow a traditional rural lifestyle" — two arms, two lifestyles.
* PRJNA1057503 (1,832, urban_industrialized): 36 competitive athletes and a vegetarian/vegan arm
  inside a 515-patient hospital cohort — `athlete` and `vegetarian_or_vegan` are supportable for those
  samples only.
* PRJNA812695 (83, urban_industrialized): cohort defined by altitude exposure in Nyingchi with a
  pre-exposure baseline; `extreme_environment` would fit the long-term resident arms only, and the
  paper calls Nyingchi moderate altitude.

## Other things noticed (not part of this decision table)
* PRJNA780005: catalog holds 213 samples; the paper reports 235 samples assessed / 183 individuals.
  PRJEB38352 (361 vs 382), PRJNA590026 (42 vs 78), PRJEB90572 (120 vs 2), PRJNA949023 (120 vs 1),
  PRJNA553326 (22 vs 1) also have `n_samples` != `n_samples_project` in the input; the last three look
  like a project-sample-count bug rather than a lifestyle issue.
* `registry_study_papers` has no `own_data` paper for most of the 35 projects whose paper was missing
  from the input (mostly `unsure`), although the resolved paper does contain the accession in 11/11 of
  the cases I needed to verify. The paper-linking for those projects may be worth a separate pass.
* The two Vanderbilt projects (PRJNA922078, PRJNA671738) and several others have no lifestyle term at
  all anywhere in their paper, which confirms the original pass was emitting `urban_industrialized`
  without any textual basis.
