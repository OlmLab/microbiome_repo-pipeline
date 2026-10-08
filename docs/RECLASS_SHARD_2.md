# Registry re-classification — shard 2 of 3

Studies in: 218 · rows out: 218 (all accessions returned). Hints: 17 studies had a deterministic row; 201 received an empty hint.
Reason mix (from reasons file, this shard): pending 209, gut_wording_not_gut_site 5, assay_unknown_gut 4
`shard` column set to 202 (= reclass shard 2) so it does not collide with original shard ids.

## Classification stages

| stage | n |
|---|---|
| sonnet_x2 | 125 |
| opus_adjudicated | 79 |
| pending | 14 |

Outcomes: predicted 204, replicates_unadjudicated 10, sentinel_no_evidence 4

Pending (not adjudicated; conservative replicate merge kept): PRJEB35140 (replicates_unadjudicated), PRJEB41353 (replicates_unadjudicated), PRJNA1042914 (sentinel_no_evidence), PRJNA1071862 (sentinel_no_evidence), PRJNA1080226 (sentinel_no_evidence), PRJNA1087294 (sentinel_no_evidence), PRJNA1276427 (replicates_unadjudicated), PRJNA1327070 (replicates_unadjudicated), PRJNA1404621 (replicates_unadjudicated), PRJNA1427578 (replicates_unadjudicated), PRJNA1429284 (replicates_unadjudicated), PRJNA1432941 (replicates_unadjudicated), PRJNA1478278 (replicates_unadjudicated), PRJNA1480961 (replicates_unadjudicated)

## Host human

| value | n |
|---|---|
| yes | 167 |
| no | 25 |
| unknown | 14 |
| mixed | 12 |

## Assay

| value | n |
|---|---|
| isolate_genome | 109 |
| other | 45 |
| shotgun_dna | 42 |
| unknown | 9 |
| amplicon_misfiled | 7 |
| amplicon | 4 |
| mixed | 2 |

## Primary body site

| value | n |
|---|---|
| unknown_site | 78 |
| gut_stool | 40 |
| blood_tissue | 31 |
| respiratory_lower | 14 |
| skin | 7 |
| oral | 6 |
| vaginal_urogenital | 6 |
| other_site | 5 |
| multi_site | 2 |
| nasal_nasopharyngeal | 2 |
| milk | 1 |
| eye_ear | 1 |

## Primary life stage

| value | n |
|---|---|
| unknown_age | 149 |
| adult | 22 |
| child | 9 |
| infant | 4 |
| mixed_ages | 4 |
| neonate | 2 |
| adolescent | 2 |
| elderly | 1 |

## Body sites (any listed)

| site | n studies |
|---|---|
| unknown_site | 76 |
| gut_stool | 41 |
| blood_tissue | 33 |
| respiratory_lower | 16 |
| skin | 7 |
| vaginal_urogenital | 7 |
| other_site | 6 |
| oral | 6 |
| nasal_nasopharyngeal | 3 |
| multi_site | 1 |
| milk | 1 |
| eye_ear | 1 |

## Catalog-eligible under the rule (host_human yes|mixed AND assay shotgun_dna|mixed AND gut_stool in body_sites): 18

| accession | stage | host | assay | title |
|---|---|---|---|---|
| PRJDB43571 | sonnet_x2 | yes | shotgun_dna | Changes in the gut microbiota of the drinking population after Fucha tea inte... |
| PRJEB120967 | sonnet_x2 | yes | shotgun_dna | Gut microbiome profiles associated with simple liver steatosis in MASLD: Ital... |
| PRJEB6358 | sonnet_x2 | mixed | mixed | Effects of cholera on the human gut microbiota, and interactions between huma... |
| PRJEB81819 | sonnet_x2 | yes | shotgun_dna | microbiota in cirrhosis |
| PRJEB91792 | sonnet_x2 | yes | shotgun_dna | MIAB |
| PRJNA1221567 | sonnet_x2 | yes | shotgun_dna | Viral Microbiome Dataset of Children Post-Allogeneic Hematopoietic Cell Trans... |
| PRJNA1297975 | sonnet_x2 | yes | shotgun_dna | Mediterranean diet intervention for intestinal microecology in metabolic-rela... |
| PRJNA1306109 | opus_adjudicated | yes | shotgun_dna | Home versus Hospital Transplant |
| PRJNA1368707 | opus_adjudicated | mixed | shotgun_dna | Meta Analysis of Paired Short-read 16S V4, Full-length 16S, and Shotgun Metag... |
| PRJNA1454700 | sonnet_x2 | yes | shotgun_dna | human fecal metagenome |
| PRJNA356544 | sonnet_x2 | yes | shotgun_dna | Hypervariable loci in the human gut virome |
| PRJNA566436 | opus_adjudicated | yes | shotgun_dna | human gut metagenome |
| PRJNA594824 | sonnet_x2 | yes | shotgun_dna | Bacteriophages regulate gut bacterial communities isolated from stunted child... |
| PRJNA715947 | opus_adjudicated | yes | shotgun_dna | Gut microbiota and tuberculosis |
| PRJNA783956 | sonnet_x2 | yes | shotgun_dna | gut metagenome |
| PRJNA971196 | sonnet_x2 | yes | shotgun_dna | Healthy Dairy Worker Metagenomics Study |
| SRP000287 | sonnet_x2 | yes | shotgun_dna | Direct metagenomic detection of viral pathogens in nasal and fecal specimens ... |
| SRP006764 | sonnet_x2 | yes | shotgun_dna | DIPP Diabetes Microbiome |

## ENA assay hints (7 studies at assay unknown/other with library_source METAGENOMIC)

Column `assay_ena_hint`; the classifier's `assay` is NOT overwritten. Up to 5 runs per study from the ENA portal read_run search.

| accession | assay | hint |
|---|---|---|
| PRJEB41353 | other | OTHER strategy, unspecified selection, ~76 bp SE, median 133.29 M reads, Illumina HiSeq 4000 (n=5 runs) => inconclusive |
| PRJEB7604 | other | OTHER strategy, unspecified selection, read length n/a SE, read counts n/a, unspecified (n=5 runs) => inconclusive |
| PRJNA1042914 | unknown | WGS strategy, PCR selection, read length n/a SE, median 0.00 M reads, BGISEQ-500 (n=1 runs) => suggests amplicon |
| PRJNA1080226 | unknown | WGS strategy, RANDOM selection, ~222 bp PE, median 0.87 M reads, Illumina MiSeq (n=5 runs) => consistent with shotgun |
| PRJNA1151239 | unknown | OTHER strategy, other selection, ~150 bp PE, median 34.48 M reads, Illumina NovaSeq 6000 (n=5 runs) => inconclusive |
| PRJNA1276427 | unknown | WGS strategy, RT-PCR/cDNA selection, ~125 bp PE, median 0.33 M reads, Illumina MiSeq (n=5 runs) => suggests RNA/cDNA library |
| PRJNA633241 | other | OTHER strategy, PCR selection, ~151 bp PE, median 0.51 M reads, Illumina iSeq 100 (n=1 runs) => suggests amplicon |

## LLM cost

| stage | requests | studies | tokens |
|---|---|---|---|
| sonnet_rep1 | 37 | 218 | 250311 |
| sonnet_rep2 | 37 | 218 | 250077 |
| opus_adjudicate | 16 | 93 | 87251 |
| opus_adjudicate_retry1 | 36 | 36 | 84585 |
| opus_adjudicate_retry2 | 15 | 15 | 3889 |

Total tokens (input+output as counted by the classifier): 676113 · wall 298 s. Under the 1.5 M self-stop.