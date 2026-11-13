# Country screen, release R2026.38 — four flagged projects

Everything below was opened in this session: ENA browser XML (study and sample records), the ENA portal
read_run report for each project, NCBI BioSample XML, Europe PMC search and `fullTextXML`.
Current catalog rows were read from `build/package/catalog_sample_determinations.parquet`
(`field_name = country`, `release_retired` null) and `catalog_studies.parquet`.

---

## PRJEB6456 — Backhed et al. 2015 — country settled (SE); short title and description rewritten

Opened: ENA study XML, ENA read_run report for all 400 runs, ENA sample XML for SAMEA2579907,
Europe PMC record for PMID 25974306 (`resultType=core`; no PMC full text — the record has no PMCID).

Deciding sentence (Europe PMC abstract, PMID 25974306, verified substring of the fetched `abstractText`):

> "Applying metagenomic analysis on fecal samples from a large cohort of Swedish infants and their mothers, we characterized the gut microbiome during the first year of life and assessed the impact of mode of delivery and feeding on its establishment."

Archive cross-check: the 400 ENA sample aliases fall into exactly four groups of 100 — `*_B`, `*_4M`,
`*_12M` and `*_M`. The `_M` group is the mothers' samples named in the abstract; the old short description
("healthy infants and neonates") covered only the infant timepoints.

Decision: no country row (SE already live on all 400 samples, routes R2 paper.supp.table n=392 and
R2 external.cmd n=8). Short title and short description rewritten to say Sweden and to include the mothers.

Could not verify: the paper is not open access, so the per-timepoint reading of the aliases
(`_B` birth, `_4M` 4 months, `_12M` 12 months) rests on the aliases alone — I therefore kept the timepoints
out of the new short description and wrote only what the abstract states.

Noted for the registry, not corrected here: every one of the 400 ENA sample records still carries
`geographic location (country and/or sea,region) = Denmark` with Copenhagen coordinates
(55.676097 N 12.568337 E), and the ENA sample titles are "Stool sample from spanish" (291) and
"Stool sample from danish" (109). The archive metadata for this Swedish cohort is wrong at source
(MetaHIT template carry-over); the catalog already overrides it from the paper.

---

## PRJNA1165510 — probiotic strain genomes, not Kenyan infant samples — country RETIRED

Opened: ENA study XML, ENA read_run report for all 17 runs, BioSample XML for SAMN46435782
(Bifidobacterium animalis PB-B4) and SAMN46435774 (Lactobacillus acidophilus PB-A1),
Europe PMC full text PMC13293936 (Cell Reports Medicine 2026, PMID 42208544).

What the 17 records actually are: all 17 runs are `library_source = GENOMIC` with descriptions of the form
"NextSeq 500 sequencing: Bifidobacterium animalis PB-B4" — one per probiotic strain (PB-A1 … PB-C6).
Both BioSamples I opened use the Microbe 1.0 package with `sample_type = Pure culture`,
`host = not applicable`, `isolation_source = not applicable`, `collection_date = 2023`,
`geo_loc_name = United Kingdom: Norwich`, biomaterial provider "Prof Lindsay J Hall".

Deciding sentences (PMC13293936, both verified substrings of the fetched full text):

> methods, "DNA extraction and Whole Genome Sequencing of probiotic organisms":
> "The powder from the Labinic synbiotic and Lab4b probiotic capsules was serially diluted and spread plated on MRS agars to obtain pure colonies, followed by culturing and genomic DNA extraction using FastDNA Spin Kit for Soil (MP Biomedicals) according to manufacturer’s instructions."

> data availability: "For the probiotic organisms, raw sequence reads (FASTQ) are available on the NCBI SRA as BioProject: PRJNA1165510"

For completeness, where the trial's people are (same paper, summary):

> "In an open-label, randomized, four-arm, phase II clinical trial, infants with birthweight ≥2000 g in Homa Bay County, western Kenya receive live, multi-strain Bifidobacterium spp. and Lactobacillaceae pro/synbiotics from 0 to 6 months."

Decision: `country` RETIRED for the project (one project-wide row, `old_value = GB`, `old_route = R1`).
The deposit holds pure cultures grown from the capsule powder and sequenced at Quadram Institute Bioscience,
Norwich; no person was sampled, so there is no country of collection to record. Recoding to KE would be wrong:
the Kenyan infants' stool metagenomes of this trial are not in this BioProject — the paper deposits only
"the probiotic organisms" under PRJNA1165510.

Beyond the country field: this study looks out of scope for a human shotgun-metagenome catalog
(17 bacterial isolate genomes, no human material). Flagged, not acted on here.

---

## PRJEB110312 — Slovakia is right, the short description was wrong — no country row

Opened: ENA study XML, ENA read_run report for all 300 runs, ENA sample XML for SAMEA122010954.
Europe PMC search for `PRJEB110312 OR ERP191000 OR PreveLYNCH` returned no paper for this deposit
(6 hits, all unrelated); the project was first public 2026-03-24.

Deciding record (ENA sample SAMEA122010954, sample attributes):
`geographic location (country and/or sea) = Slovakia`, `project name = PreveLYNCH`,
`environmental medium = blood plasma [UBERON:0001969]`, `collection date = 2021`.
All 300 runs carry country Slovakia, centre "Geneton Ltd;Geneton", collection years 2021 (59), 2022 (237),
2023 (4), and sample aliases of the single form `vpuk-ON_nn_nnnnn-...`.

Decision: no country correction. The live rows are `SK` (R1, `sample.attr.geographic_location_country_and_or_sea`,
quote "Slovakia") and SK is the catalog's code for Slovakia, not South Korea — across the whole package,
`SK` is only ever written from the value "Slovakia" (320 live rows) while Korean samples normalise to `KR`
(~8,500 live rows). The error is in the machine-written text, which turned the code into "South Korea";
no source I opened mentions Korea. Short title and description rewritten.

Could not verify: no publication and no per-sample clinical grouping is public, so the new short description
names only what the archive states (country, years, the healthy / colorectal cancer / IBD groups from the ENA
study description, and the source-tracking aim).

---

## PRJNA296435 — China is right, the short description was wrong — no country row

Opened: ENA study XML, ENA read_run report (1 run, SRR2467289 / SRX1267325), BioSample XML SAMN04099168,
Europe PMC full text PMC4754835 (Parasites & Vectors 2016, PMID 26879053).

BioSample SAMN04099168: `geo_loc_name = China:qingdao`, `lat_lon = 36.07 N 120.33 E`,
`collection_date = 2014-05-21`, `isolation_source = patient's eye discharge`, `tissue = eye`,
`collected_by = guangqiang wang`, owner "the institute of hydrobiology".

Deciding sentences (PMC4754835, all verified substrings of the fetched full text):

> "A 34-year-old female Chinese student who used to study in New York presented to our services in May 2014."

> "In our hospital, Qilu Hospital of Shandong University, China, an initial blood test showed leukopenia and eosinophilia"

> "Because the patient’s eyes were itching terribly and often secreted foreign matter, the first sample was from the patient’s eye discharge."

Decision: no country correction — `CN` stands. The woman had been living and studying in New York (and was
first treated there), but she travelled to China and the specimen was taken after she presented to Qilu Hospital
of Shandong University in May 2014, which matches the BioSample collection date 2014-05-21 and Qingdao
coordinates. The catalog's `country` is where the sample was collected from the person, so China is correct;
"New York, United States" in the archive study title is the patient's residence, not the collection site.
Short title and description rewritten to say Qingdao, China, and to keep the New York history.

Could not verify / noticed: the ENA and NCBI study title says "35-year Old Women" and the BioSample `age`
attribute says 35, while the paper says 34-year-old throughout — I left the age out of the new short
description. The paper reports two specimens (eye discharge and subcutaneous tissue) but only one run
(SRR2467289, experiment SRX1267325, the eye-discharge library) is deposited under PRJNA296435.
