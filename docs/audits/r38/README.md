# R2026.38 check (November 2026)

Decision tables and notes of the Opus 5 readers who checked catalog values against primary sources for release R2026.38
(package 1.31.0). `scripts/build_dq_corrections_r38.py` turns them into `data/inputs/gut/dq_corrections_r38.parquet`
(8,064 rows, `determined_by = dq_adjudication_v2:r38`), which `build_gut_scope.apply_corrections` applies.

| file | content |
|---|---|
| `antibiotic_statements_part{1,2}.csv` | the 484 cohort-wide antibiotic statements of R2026.37 that were audited |
| `antibiotic_decisions_part{1,2}.csv` + notes | keep / retire per statement, category, the sentence checked, whether the paper was opened |
| `lifestyle_decisions_part{1,2,3}.csv` + notes | keep / recode / retire for the 263 project codes whose stored quote was the code itself |
| `corrections_classes.csv` + notes | four classes of suspect health_condition values (prematurity without wording, bare cancer strings, yes/no flags, ME/CFS) |
| `corrections_projects.csv` + notes | eleven single-project items |
| `corrections_countries.csv`, `short_descriptions_fix.csv` + notes | four projects whose machine-written title named another country than their samples: the per-sample countries were right in three (titles rewritten: `config/short_description_corrections.csv`); PRJNA1165510 is 17 probiotic isolate genomes and leaves the catalog (`config/sample_exclusions.csv`), so its country row is not applied |

Order to rebuild the inputs: `python scripts/build_dq_corrections_r38.py` (needs the unpacked R2026.37 package), then
`python scripts/build_ext_delta_r38.py` (needs the re-match outputs of the nine prior resources; its gate reads the corrections).

Rows added by the main session on the readers' evidence: PRJEB64515 controls labelled COVID-19 by a prior resource (289) and
patients coded by a named comorbidity in PRJEB64515 / PRJEB91718 (169); PRJNA474024 (72); PRJEB6456 country (the archive value
Denmark replaced by the checked statement; the withheld per-sample values from the paper's table then publish); PRJNA310878 age
withdrawn on every route; for a withdrawn antibiotic statement, every cohort-wide antibiotic statement of the project; for a
withdrawn lifestyle code, the same code from the abstract.

## Left for the next run

- Per-sample lifestyle where one arm has it: PRJEB63347, PRJNA1083578, PRJNA885289, PRJEB53437, PRJEB106785, PRJNA698366,
  PRJNA1115111, PRJNA1005267, PRJNA1521571, PRJNA971196, PRJNA982563. PRJNA846182 and PRJNA145383 need the closed-access papers.
- Per-time-point antibiotic exposure in the 39 intervention designs (the notes list those whose sample names carry the day), and
  per-sample data named in the notes (PRJNA506988, PRJEB28671, PRJDB5180, PRJNA1210004, PRJNA613054).
- PRJEB39610 (12 `nec: N` samples `preterm_nicu` vs 437 `healthy_control`); PRJNA987980 (1,029 simulated samples carry human
  metadata); PRJEB98012 (67 blanks / positive controls); PRJNA1001944 (`ibs` / `crohns_disease` in a breast cancer cohort);
  PRJNA1167328 (464 SARS-CoV-2-negative samples without a condition); the 25 class-3 projects under 30 samples
  (PRJEB68381 and PRJNA895487 show the same pattern).
- PRJNA932796: 26 samples with `sample_type = cell culture` have not been read.
- PRJNA544527: the linked paper PMC10039858 only cites the project; PRJNA770647: antibiotic statement kept, paper not open access;
  PRJEB91658: aliases ending .1 / .2; PRJNA310878: age unit; PRJEB45443: table-to-sample join not verified.
- Paper links of relation `unsure` that are unrelated to the project (PMC11986028 on PRJEB42612, PMC12749156 on PRJNA595703,
  PMC9600381 on PRJNA266117) still feed full-text passes.
- 2,269 re-matched prior-resource values that differ from a current per-sample value
  (`data/inputs/gut/ext_delta_r38_not_ingested.parquet`) have not been adjudicated.
