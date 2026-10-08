# Registry re-classification — shard 0 of 3

Input: 220 studies (universe shard 09c0a204; reasons in this shard: {'pending': 210, 'assay_unknown_gut': 5, 'gut_wording_not_gut_site': 5}). Deterministic hints present for 16 studies; the other 204 received an empty hint.
Classifier: `catalog.registry.classify_llm.run_llm_stage` (rubric role ×2 replicates → adjudicate role on disagreements, singleton retries ×2, conservative merge fallback), via a wrapper that only adds empty hints and a token self-stop guard (1.45 M; never triggered).

LLM tokens (uncached input + cache creation + output): **722,619** (main run 714,463; singleton re-run of PRJNA1235703 8,156). Cache-read tokens (not counted): 719,192.
Adjudication: 92 studies queued; 36 retried as singletons in round 1; 10 still empty after round 2 → conservative replicate merge (stage `pending`).
PRJNA1235703 failed in both replicate batches (unparsable output → sentinel); it was re-run once as a singleton and is now `sonnet_x2`.

## Stage counts
| classification_stage | n |
|---|---|
| sonnet_x2 | 129 |
| opus_adjudicated | 82 |
| pending | 9 |

Outcomes: {'predicted': 211, 'replicates_unadjudicated': 9}

## host_human
| host_human | n |
|---|---|
| yes | 167 |
| no | 31 |
| unknown | 14 |
| mixed | 8 |

## assay
| assay | n |
|---|---|
| isolate_genome | 94 |
| other | 51 |
| shotgun_dna | 51 |
| amplicon_misfiled | 8 |
| mixed | 6 |
| unknown | 4 |
| amplicon | 4 |
| rna | 2 |

## Body sites (list membership; 31 studies have an empty list — host not human)
| body_site | n |
|---|---|
| unknown_site | 76 |
| gut_stool | 39 |
| blood_tissue | 31 |
| respiratory_lower | 16 |
| oral | 10 |
| vaginal_urogenital | 8 |
| nasal_nasopharyngeal | 5 |
| skin | 5 |
| other_site | 4 |
| eye_ear | 2 |

Primary body site: {'unknown_site': 75, 'gut_stool': 38, 'blood_tissue': 28, 'respiratory_lower': 15, 'oral': 9, 'vaginal_urogenital': 8, 'skin': 5, 'nasal_nasopharyngeal': 4, 'other_site': 4, 'eye_ear': 2, 'multi_site': 1}

Primary life stage: {'unknown_age': 144, 'adult': 24, 'child': 11, 'infant': 3, 'neonate': 3, 'elderly': 2, 'mixed_ages': 1, 'adolescent': 1}

## assay_ena_hint (assay unknown/other AND library_source METAGENOMIC; ENA read_run, ≤5 runs)
| study | classifier assay | ENA hint |
|---|---|---|
| PRJDB33475 | unknown | OTHER/PCR selection, 431 bp SE, median 0.03 Gb, Illumina MiSeq (n=5 runs) → amplicon-like, not shotgun |
| PRJEB13619 | other | OTHER/PCR selection, 253 bp SE, median 0.01 Gb, Illumina MiSeq (n=5 runs) → amplicon-like, not shotgun |
| PRJNA1089551 | other | OTHER/cDNA selection, 129 bp PE, median 1.15 Gb, NextSeq 2000 (n=2 runs) → RNA-derived (metatranscriptome-like) |
| PRJNA867750 | unknown | WGS/RANDOM selection, 53 bp PE, median 0.06 Gb, Illumina MiSeq (n=4 runs) → shotgun (shallow) |

Only 17/220 studies in this shard carry library_source METAGENOMIC (180 are GENOMIC, mostly WGS/WXS human-genome or isolate projects). None of the 4 hinted studies looks like an unrecognised deep shotgun stool study: two are PCR/amplicon-like, one is cDNA, and PRJNA867750 is RANDOM selection but very shallow (~0.06 Gb).

## Catalog-eligible (host_human yes|mixed AND assay shotgun_dna|mixed AND gut_stool in body_sites): **17**
By reason: {'pending': 16, 'assay_unknown_gut': 1}

| study | stage | life_stage | title (≤80 chars) |
|---|---|---|---|
| PRJEB120968 | sonnet_x2 | adult | Gut microbiome profiles associated with simple liver steatosis in MASLD: Swedish |
| PRJEB19677 | sonnet_x2 | infant | This dataset contains the output from shotgun metagenomic and genomic sequencing |
| PRJEB87798 | sonnet_x2 | child | Short- and long-term development of gut microbiota in children after liver trans |
| PRJNA1045701 | sonnet_x2 | unknown_age | Sample size estimations based on human microbiome temporal stability over six mo |
| PRJNA1242044 | opus_adjudicated | unknown_age | Profound taxonomic and functional gut microbiota alterations associated with tri |
| PRJNA1255389 | sonnet_x2 | child | Carbapenemase producing Enterobacterales from Qatar and Gaza area in Sidra Medic |
| PRJNA1258574 | sonnet_x2 | unknown_age | IncL plasmid-mediated dissemination of OXA-48 beta-lactamase and blaCTX-M-15 gen |
| PRJNA1295133 | sonnet_x2 | child | Gut Microbiome and Virome Dysbiosis in Pediatric Idiopathic Nephrotic Syndrome:  |
| PRJNA1333319 | sonnet_x2 | unknown_age | Phage bacteria interaction networks differentiate ulcerative colitis and colorec |
| PRJNA1358001 | sonnet_x2 | unknown_age | human gut Metagenome |
| PRJNA1391873 | sonnet_x2 | adult | Correlation between gut microbiota and their metabolites and the efficacy of che |
| PRJNA1401443 | sonnet_x2 | unknown_age | Whole-genome sequencing and intestinal metagenome sequencing revealed the carria |
| PRJNA1457283 | sonnet_x2 | adult | Homo sapiens Genome sequencing and assembly |
| PRJNA631464 | sonnet_x2 | unknown_age | lactobacillus composition in human fece |
| PRJNA895415 | sonnet_x2 | adult | Association of gut microbial dysbiosis with disease severity, response to therap |
| SRP005975 | opus_adjudicated | unknown_age | Microbial culturomics: African Gut Paradigm |
| SRP012035 | opus_adjudicated | unknown_age | gut microbiota from an obese human |

Pending (unadjudicated, conservative merge) accessions: PRJNA1039243, PRJNA1417726, PRJNA1422863, PRJNA573682, PRJNA579348, PRJNA603842, PRJNA809398, PRJNA867750, PRJNA883512. None of these is catalog-eligible under the rule; PRJNA603842 (host yes, shotgun_dna, site unknown) is the nearest.
