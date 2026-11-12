# lifestyle_decisions_part2 — notes

Input: lifestyle_candidates_part2.jsonl (88 projects, 14,596 samples with a `lifestyle` value).
Decisions: 11 keep (1,485 samples), 77 retire (13,111 samples), 0 recode, 0 undecided.

## Method
For every project I re-scanned the cached full text myself rather than relying only on the supplied
candidate sentences: sentences were re-extracted from the cached `.txt`, the reference list was cut off,
and the code's vocabulary match terms were searched with word boundaries. Every project with no
match-term hit was additionally scanned for equivalent residence wording
(resid*/living/dwell*/inhabitant*/city/cities/town/metropolitan/suburb/slum/peri-urban/lifestyle) in
sentences that also mention participants; those sentences were read before deciding. All 11 kept quotes
were verified programmatically as contiguous verbatim substrings of the named cached text.

## Source coverage
* 55 projects had the cached text path in the input.
* 33 projects had no paper in the input (`papers: []`). Their `pass_output.paper_id` (a PMID) was resolved
  to a PMCID with one Europe PMC `search` call; all 33 PMCIDs turned out to be present in the local
  pmc_oa cache, so the text was read from cache, not re-fetched. These projects are:
  PRJEB112518, PRJEB11685, PRJEB22348, PRJEB31117, PRJEB32184, PRJEB38148, PRJEB40590, PRJEB44020, PRJEB46230, PRJEB48068, PRJEB51894, PRJEB74775, PRJNA1049329, PRJNA1059748, PRJNA1108908, PRJNA1157857, PRJNA1196639, PRJNA1470033, PRJNA202303, PRJNA271229, PRJNA277905, PRJNA541574, PRJNA635133, PRJNA671163, PRJNA749564, PRJNA752146, PRJNA832581, PRJNA862314, PRJNA904925, PRJNA910314, PRJNA936638, PRJNA956591, PRJNA982297.
  `source_checked` is recorded as `cached_text` for them because the quote/absence evidence comes from the
  cached file; only the paper link came from Europe PMC.
* No project had to be decided as 'no accessible source'.

## Why the original pass produced so many unsupported codes
The candidate sentences in the input were cut with substring matching, so "urban" matched inside
"disturbance" and "residual" (12 projects had only such pseudo-hits: PRJEB54673, PRJEB102458, PRJNA1274175, PRJNA910314, PRJNA671163, PRJEB11685, PRJNA202303, PRJEB6358, PRJEB61661, PRJNA1189147, PRJNA490628, PRJEB31117).
Of the remaining hits, most were reference titles, background statements about industrialized countries,
or the *study site* (an urban hospital/medical centre), none of which state where participants live.

## Borderline calls worth the owner's eye
* **PRJNA1083578 (Yanomami skin, 152 samples) — retired, but partly recoverable.** The paper does describe
  Yanomami participants, but the same BioProject also holds the longitudinally sampled "westernized
  expeditioner" and a Yanomami traveler sampled in the USA (catalog countries: VE 129, US 18, DO 7), so
  `hunter_gatherer` is not project-wide. A sample-level rule (country VE -> hunter_gatherer) would be
  supportable from this paper; a project-wide code is not.
* **PRJNA217052 (FijiCOMP, 484) — kept as rural_non_industrialized.** The quote says "agrarian Fiji
  islanders"; `traditional_agriculturalist` would arguably be the closer code. Village residence is also
  stated ("differences ... between the mobile gene pools of proximal Fijian villages").
* **PRJNA1049329 (rural Netherlands COPD, 72) — kept as rural_non_industrialized.** "Rural residents" is
  stated project-wide, but the "non-industrialized" half of the code label is not stated and is unlikely
  for the Netherlands. If the vocabulary ever gains a plain `rural` code, this project belongs there.
* **PRJEB32184 (BEED, 58) — kept as urban_industrialized.** Participants are "slum-dwelling" children in
  "an urban slum (Mirpur) in Dhaka"; urban is stated, industrialized is not.
* **PRJEB6358 (cholera, 55) — kept as urban_industrialized.** "Bangladeshi adults living in Dhaka
  Municipal Corporation area" is a residence statement, not a recruitment site.
* **PRJNA1129090 (257) — kept.** The quote comes from the nasal-microbiome paper (PMC13426232); the project
  also holds air/TSP-related samples (74 of 323 rows have unknown_site), so the code is project-wide only
  for the human participants described there.
* **PRJEB51408 (GRACE, 441) — kept as institutionalized.** The quote states the whole GRACE study was
  conducted in long-term aged care; the individual papers describe their participants as aged-care residents.
* **PRJNA271229 (178) — retired.** The participants are described as "living in and around Irrua, a
  modest-sized peri-urban village in southeastern Nigeria", which contradicts urban_industrialized;
  `transitional_or_migrant` was considered and rejected as too interpretive.
* **PRJNA1196639 (73) — retired.** Residence is stated per subgroup (Guangzhou-city volunteers who moved to
  Nyingchi, and local Nyingchi residents). The cohort is a moderate-altitude relocation study, so
  `extreme_environment` may be worth a separate look; it was not assessed project-wide here.
* **PRJNA862314 (62) — retired.** The study samples both Cameroonian forest villagers and European
  zookeepers; the catalog rows carry no country, so the rural statement could not be shown to cover all 62.
* **PRJNA886972 (24) — retired.** Participants are described as "living in Singapore"; that is a country
  (city-state) name, which the rule forbids as the basis for an urban code.

## Not checked
* Whether any retired project states a *different* lifestyle elsewhere in a non-cached supplementary file
  (only the main text was read).
* Sample-level heterogeneity inside the 11 kept projects beyond the checks noted above.
