# Catalog host / assay audit (37 studies)

Studies audited: 37 — keep 7, keep_partial 26, exclude_study 4.
Samples flagged as not human gut metagenomes: 2742 (host_nonhuman 2503, other 142, site_not_gut 96, assay_isolate_genome 1), across 30 studies.

Evidence: ENA read_run fields (scientific_name, host_tax_id/host_scientific_name, sample_title, isolation_source), NCBI/ENA BioSample attributes for 16 ambiguous studies, ENA study descriptions, and one Europe PMC abstract (PMID 40130436). No LLM calls were used; all decisions are rule-based on archive fields.

Reason codes: host_nonhuman (animal host), assay_isolate_genome (cultured isolate), site_not_gut (human urine/skin), other (environmental samples, in vitro cultures/fermentations, HEK cell-line spike-ins, reagent blanks, declared negative controls).

## Decisions

| study | verdict | flagged / audited | reason | decision |
|---|---|---|---|---|
| PRJDB36905 | keep_partial | 30 / 42 | host_nonhuman,other | 12 HF human faecal samples kept; pig gut, pork, dust, soil, wastewater samples flagged. |
| PRJEB39960 | keep_partial | 84 / 87 | host_nonhuman | 3 D0 human fecal transplant preparations (MD/NaCl/TR) kept as human gut; 84 germ-free mouse faeces flagged. |
| PRJEB73511 | keep_partial | 27 / 47 | host_nonhuman | 20 child gut samples kept; 27 horse skin/oral/fecal samples flagged. |
| PRJEB81804 | keep_partial | 244 / 382 | host_nonhuman,other | 138 human colon samples kept (incl. 1 mislabelled pig gut metagenome with host 9606); 140 pig and 104 HEK/reagent control samples flagged. |
| PRJNA1008138 | keep_partial | 11 / 68 | host_nonhuman | 11 LMR-prefixed samples are mouse lemurs mis-annotated host Homo sapiens (inferred from prefix + title); 57 HUM samples kept. |
| PRJNA1032744 | keep_partial | 100 / 120 | host_nonhuman | 20 pediatric KD patient samples kept; 100 mouse samples flagged. |
| PRJNA1067813 | keep_partial | 351 / 437 | host_nonhuman | 86 adult human stool samples kept; 351 gnotobiotic mouse stool/cecal samples flagged (incl. 39 with blank scientific_name). |
| PRJNA1082665 | exclude_study | 767 / 767 | host_nonhuman | All 767 samples canine (560) or feline (207); human comparators not deposited here. |
| PRJNA1101587 | keep_partial | 37 / 76 | host_nonhuman | 39 human samples kept; 37 cat samples flagged. |
| PRJNA1105425 | keep_partial | 100 / 104 | host_nonhuman | 4 human IBD fecal slurries (FMT inocula, host Homo sapiens) kept; 100 mouse pellets flagged. |
| PRJNA1207520 | keep_partial | 2 / 37 | other | Human stool, metagenomic WGS targeting rotavirus A genomes (viral genotyping); 35 kept, 2 declared negative controls flagged. Owner may prefer to treat whole study as virus-targeted. |
| PRJNA1230553 | keep_partial | 85 / 86 | host_nonhuman | 1 human stool sample (TL1) kept; 85 mouse luminal samples flagged. |
| PRJNA1240831 | exclude_study | 2 / 2 | other | Both samples are in vitro anaerobic cultures (library GENOMIC/OTHER), not direct gut metagenomes. |
| PRJNA1314234 | keep | 0 / 194 | - | All 194 samples human stool; mouse datasets mentioned in title are not in this deposit. |
| PRJNA1337640 | exclude_study | 16 / 16 | host_nonhuman | All 16 samples are mouse cecum (isolation_source Cecum-N). |
| PRJNA1457283 | keep | 0 / 51 | - | All 51 BioSamples human gut metagenome, feces; title is generic NCBI template. library_source GENOMIC (assay label unaffected here). |
| PRJNA320015 | keep_partial | 1 / 2 | host_nonhuman | Infant stool sample kept; 1 canine stool sample (17 runs) flagged. |
| PRJNA400628 | keep_partial | 93 / 515 | site_not_gut | Title is NCBI template; 422 stool/rectal human samples kept; 93 urine samples flagged. |
| PRJNA506496 | keep_partial | 38 / 39 | host_nonhuman,other | Only the human fecal inoculum (SAMN10461857, 0h) kept; 6 human in vitro fermentation and 32 porcine samples flagged. |
| PRJNA513350 | keep_partial | 3 / 6 | site_not_gut | 3 human gut samples kept; 3 skin samples flagged; no isolate genomes in this deposit. |
| PRJNA528511 | keep | 0 / 888 | - | All 888 samples human traveller stool; title is NCBI template. |
| PRJNA609594 | exclude_study | 1 / 1 | other | Single sample is a Bacteroidaceae culture from feces (library_source OTHER), not a direct metagenome. |
| PRJNA635116 | keep_partial | 23 / 51 | host_nonhuman | 28 human (BaAka/Bantu) samples kept; 23 gorilla samples flagged. |
| PRJNA639909 | keep_partial | 8 / 9 | host_nonhuman | Human FMT donor sample kept; 8 recipient mouse samples flagged. |
| PRJNA678365 | keep_partial | 16 / 24 | host_nonhuman | 8 human samples kept; 8 goat and 8 chicken samples flagged. |
| PRJNA684904 | keep | 0 / 2 | - | Both samples human child diarrhoeal faeces; title is NCBI template. |
| PRJNA685581 | keep_partial | 20 / 60 | host_nonhuman | 40 human samples kept; 20 mouse samples flagged. |
| PRJNA705695 | keep_partial | 180 / 207 | host_nonhuman | 27 human samples kept; 180 mouse samples flagged (host fields blank; decided on scientific_name). |
| PRJNA721002 | keep_partial | 35 / 51 | host_nonhuman | 16 owner samples kept; 35 dog (owned + kennel) samples flagged. |
| PRJNA802048 | keep | 0 / 187 | - | All 187 samples infant gut; animal word is bovine lactoferrin. |
| PRJNA832701 | keep_partial | 18 / 37 | host_nonhuman | 19 human stool samples kept; 18 recipient mouse samples flagged. |
| PRJNA852373 | keep_partial | 3 / 5 | host_nonhuman | 2 human (farmer/non-farmer) samples kept; 3 chicken samples flagged. |
| PRJNA858101 | keep_partial | 392 / 587 | assay_isolate_genome,host_nonhuman | 195 human samples kept; 391 mouse samples and 1 A. muciniphila isolate flagged. |
| PRJNA885137 | keep | 0 / 53 | - | All 53 samples human stool (host Homo sapiens); no mouse samples deposited. |
| PRJNA902368 | keep_partial | 44 / 52 | host_nonhuman | 8 human IBD donor samples kept; 44 humanized mouse samples flagged. |
| PRJNA970820 | keep | 0 / 21 | - | All 21 samples human stool; title is NCBI template. |
| SRP006081 | keep_partial | 11 / 13 | host_nonhuman,other | Only WGS run SRR254173 pools 13 BioSamples; 2 uncultured human fecal samples kept, 8 mouse + 2 cultured-plate + 1 default sample flagged. Human reads are not separable at run level; 9 other runs are AMPLICON. |

## Points for the owner

- SRP006081: the only WGS run (SRR254173) pools 13 BioSamples (human, mouse, cultured), so the 2 human samples are not separable by run. Excluding the whole study is a reasonable alternative.
- PRJNA1207520: shotgun sequencing of human stool aimed at rotavirus A genotyping. Kept (minus 2 negative controls), but it may be better treated as a virus-targeted study.
- PRJNA1008138: the 11 lemur samples (LMR prefix) are annotated host Homo sapiens in the archive. The call is inferred from the prefix and the study title.
- PRJEB39960, PRJNA1105425, PRJNA506496, PRJNA639909: only the human fecal inocula/donor samples are kept (3, 4, 1 and 1 samples).
- PRJEB81804: the human samples are colon samples (UBERON:colon/rectum), not stool. Check them against the gut_stool body-site rule.
- Title-template studies (PRJNA400628, PRJNA528511, PRJNA609594, PRJNA684904, PRJNA970820, PRJNA1457283, PRJNA513350) were decided on sample fields. Only PRJNA609594 (a culture) is excluded.
