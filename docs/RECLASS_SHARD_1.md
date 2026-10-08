# Registry re-classification — shard 1 of 3

Studies: 220 in / 220 classified. Classifier: Sonnet-class ×2 replicates → Opus-class adjudication (catalog.registry.classify_llm.run_llm_stage; 204 of 220 studies had no deterministic hint and received an empty hint).
LLM tokens: 678,075 (cost log in registry_reclass_shard_1.json).

## Stage counts

| stage | n |
|---|---|
| sonnet_x2 | 131 |
| opus_adjudicated | 81 |
| pending | 8 |

The 8 `pending` rows are conservative replicate merges (outcome `replicates_unadjudicated`): adjudication returned no usable output after two singleton retries. Accessions: PRJEB15303, PRJEB33950, PRJNA1242290, PRJNA645054, PRJNA658828, PRJNA926136, PRJNA966239, PRJNA996997.

## Re-classification reason (input) × catalog eligibility

| reason | not eligible | eligible |
|---|---|---|
| assay_unknown_gut | 4 | 2 |
| gut_wording_not_gut_site | 2 | 0 |
| pending | 195 | 17 |

## host_human

| value | n |
|---|---|
| yes | 178 |
| no | 28 |
| unknown | 9 |
| mixed | 5 |

## assay

| value | n |
|---|---|
| isolate_genome | 100 |
| other | 56 |
| shotgun_dna | 44 |
| unknown | 7 |
| amplicon_misfiled | 4 |
| mixed | 4 |
| amplicon | 3 |
| rna | 1 |
| assembly_only | 1 |

## body sites (list membership; a study can carry several)

| site | n |
|---|---|
| unknown_site | 69 |
| blood_tissue | 44 |
| gut_stool | 41 |
| oral | 12 |
| other_site | 12 |
| respiratory_lower | 11 |
| vaginal_urogenital | 8 |
| skin | 4 |
| nasal_nasopharyngeal | 4 |
| multi_site | 2 |
| milk | 2 |

## body_site_primary

| site | n |
|---|---|
| unknown_site | 71 |
| blood_tissue | 39 |
| gut_stool | 37 |
| other_site | 9 |
| respiratory_lower | 9 |
| oral | 8 |
| multi_site | 8 |
| vaginal_urogenital | 5 |
| skin | 3 |
| nasal_nasopharyngeal | 2 |
| milk | 1 |

## ENA assay hints (`assay_ena_hint`)

Studies left at assay unknown/other with library_source METAGENOMIC (n=4); up to 5 runs each from the ENA portal read_run search. The classifier's assay was not changed.

| study | classifier assay | host | body_sites | ENA hint |
|---|---|---|---|---|
| PRJNA1460434 | other | yes | unknown_site | likely amplicon: OTHER/PCR selection, METAGENOMIC, 142 bp PE, Illumina MiSeq, median 0.01 Gbp/run (n=5 runs) |
| PRJNA1501268 | other | yes | gut_stool | OTHER/other selection, METAGENOMIC, 151 bp PE, Illumina NovaSeq X, median 5.52 Gbp/run (n=5 runs) |
| PRJNA645054 | unknown | unknown | unknown_site | Targeted-Capture/other selection, METAGENOMIC, 51 bp PE, Illumina HiSeq 4000, median 0.21 Gbp/run (n=5 runs) |
| PRJNA996997 | unknown | yes | respiratory_lower;unknown_site | likely shotgun: WGS/RANDOM selection, METAGENOMIC, 114 bp PE, NextSeq 2000, median 0.23 Gbp/run (n=4 runs) |

PRJNA1501268 (human, gut_stool, assay=other) has 151 bp PE NovaSeq X runs of ~5.5 Gbp with selection 'other'. This depth is consistent with shotgun sequencing, and the study would become catalog-eligible if the root re-codes its assay to shotgun_dna.

## Catalog-eligible under the rule (host_human yes|mixed AND assay shotgun_dna|mixed AND gut_stool ∈ body_sites): 19

| study | stage | host | assay | title |
|---|---|---|---|---|
| PRJDB14272 | opus_adjudicated | yes | shotgun_dna | Metagenomic analysis of fecal samples obtained from ulcerative colitis patien... |
| PRJDB43252 | sonnet_x2 | yes | shotgun_dna | Integrated 16S rRNA sequencing and metagenomics insights into microbial dysbi... |
| PRJEB120969 | sonnet_x2 | yes | shotgun_dna | Gut microbiome profiles associated with simple liver steatosis in MASLD: Aust... |
| PRJNA1036657 | sonnet_x2 | yes | shotgun_dna | vaginal, rectal, and endometrial microbiome sequences of women diagnosed with... |
| PRJNA1106227 | sonnet_x2 | yes | shotgun_dna | Phosphatidylcholine inactivates cytotoxic CD8+ T cells through UFMylation via... |
| PRJNA1144402 | opus_adjudicated | yes | shotgun_dna | Patients Infected with the Hepatitis D Virus have a Unique Gut Microbiome wit... |
| PRJNA1165510 | sonnet_x2 | yes | shotgun_dna | Probiotics and synbiotics reduce systemic inflammation and improve gut health... |
| PRJNA1219549 | sonnet_x2 | yes | shotgun_dna | INDICATE-FH |
| PRJNA1251023 | sonnet_x2 | yes | shotgun_dna | DFI C. diff Metagenomics Profiling |
| PRJNA1295840 | sonnet_x2 | yes | shotgun_dna | Gut Microbiome and Virome Dysbiosis in Pediatric Idiopathic Nephrotic Syndrom... |
| PRJNA1394613 | opus_adjudicated | yes | shotgun_dna | Intestinal flora structure mediated regulatory T cells in the effect and mech... |
| PRJNA1441102 | sonnet_x2 | yes | shotgun_dna | Effect of AG1 supplementation on nutritional adequacy and gut microbial compo... |
| PRJNA293986 | opus_adjudicated | yes | shotgun_dna | Metagenomic approach for identification of the pathogens associated with diar... |
| PRJNA609594 | sonnet_x2 | yes | shotgun_dna | human gut metagenome Genome sequencing and assembly |
| PRJNA788147 | sonnet_x2 | yes | shotgun_dna | Adaptive nanopore sequencing on miniature flow cell detects extensive antimic... |
| PRJNA804967 | sonnet_x2 | yes | shotgun_dna | Alterations in Gut Microbiota Mediate Breast Cancer Development and Bone Meta... |
| PRJNA982264 | sonnet_x2 | yes | shotgun_dna | Investigating the Bacterial Gut Microbiome of Diverse Egyptian Populations |
| SRP006081 | sonnet_x2 | mixed | mixed | Extensive personal human gut microbiota culture collections characterized and... |
| SRP057027 | sonnet_x2 | yes | shotgun_dna | Inflammation, Antibiotics, and Diet as Concurrent Environmental Stressors of ... |
