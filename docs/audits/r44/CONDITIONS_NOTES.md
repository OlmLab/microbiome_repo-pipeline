# CONDITIONS — notes for the Editor (R2026.44, package 1.35.0)
Counts computed on package 1.34.2, current rows (813,356 samples; 11,277 (project, code, detail) triples) and read back from the delivered files. No
network. Ontology ids are copied from the hand-over's own `condition_strings.csv` (EFO 3.96.0 / MONDO 2026-11-03), never written from memory. Details
are cut at 120 characters, the length `scripts/build_dq_corrections_r41.py` writes.

## 1. `condition_pair_changes_r44.csv` — 701 rows, 15,753 current samples, 199 projects
- By origin: `unclear_strings_settled.csv` 408 rows / 6,212 samples (388 as_handed_over, 20 verified_by_me); `unsettled_strings_settled.csv` 276
  / 5,938 (253 as_handed_over, 23 verified_by_me); `condition_strings.csv` (the 17 free-text strings) 17 / 3,603, all verified_by_me. Totals: 641
  as_handed_over, 60 verified_by_me.
- Every row changes the detail; 163 also change the code. One row is `retire` — PRJEB63360, 230 samples: the only evidence behind code and detail
  alike is a study description of an occupational exposure cohort that names no infection. No other row met both conditions for `retire`.
- The 17 free-text rows keep the free text as the detail (`<free text>; <old detail>`) and take a code read off the project record; all 17
  matched a specific code, so the catch-all was not needed. For 7 the reading's role differs from step 1 (healthy → not a condition: trial-arm
  labels in formula and vaginal-seeding trials). Those projects hold no cases, so the explorer's healthy-control counts do not move.
- **Decision for you:** `unsettled_strings_settled.csv` row `CSc4ae628c9b|PRJNA480674` proposes `other_control`, which is a role, not a code in
  `config/vocab/health_conditions.yaml`. No code fits a non-IBD symptomatic control, so the row is in `left_out_r44.csv`. Moving those 6 samples
  needs a new code in the vocabulary.

## 2. `condition_sample_changes_r44.csv` — 318 rows, 160 samples, 4 projects
158 `health_condition` rows and 160 `health_condition_detail` rows, from the 10 MMC patterns that carry a fix (CC008, CC013–CC018, CC021–CC023);
every sample is still current in 1.34.2 and still carries the old value. I re-checked each reader quote against the cached paper: verbatim for
CC008 (PMC4769288), CC018 (PMC12374449) and CC021–CC023 (PMC10730187, reader 2's figure caption; reader 1's supplementary-table quotes are not in
the cached text). All ten are `verified_by_me`.
- **Deviation from the hand-over.** For PRJEB59450 the paper (PMID 37260059) is not open and not in the cache, so the codes rest on the archive
  record alone. CC013 (`diag_pda`, 32 samples) → `other_cancer` stands: the title expands PDA as pancreatic ductal adenocarcinoma. CC016
  (`diag_papca`, 3) I give `other_disease`, not the proposed `other_cancer`: the record never expands the abbreviation, and the only quote I
  could locate ("In total 105 patients were recruited prospectively before elective pancreas surgery") supports no more than the catch-all.
  CC014/CC015/CC017 take the catch-all for the same reason, at confidence 0.7.

## 3. Flagged rows — verified or left out
89 rows carried a flag (`settled_quote_to_check` 22, `settled_role_readers_differ_on_code` 18, `not_settled` 49; 1,828 samples). I read the
catalog's own record for all 44 projects (title, archive description, the `population_condition` and `design_statement` fields, every label with
its sample count, and the evidence quotes behind the labels under question), one Opus 5 reading per project, then required: the reading settles
the label, its quote is verbatim in the record I supplied (checked mechanically), the code is in the vocabulary, and — where the hand-over had
already settled a role — the role agrees. 38 rows passed, 1,001 samples (including `stressed adults`, PRJNA634666, which is delivered as its
free-text row).

- For PRJEB11419 the reading alone would have retired seven labels whose detail is a negative survey answer ("I do not have this condition",
  "autoimmune: no"). The package shows why that is wrong: the code comes from a *different* BioSample attribute that does state a diagnosis
  (`diabetes`, `ibd`, `cancer`, `mental_illness`, `autoimmune`) and only the detail was mis-carried from `clinical_condition`; all seven became
  detail-only recodes with the attribute quote as evidence. The same check reversed one hand-over row: `CSa93bade28c|PRJEB11419` (other_cancer,
  107 samples) was proposed for retirement, but `cancer` reads "Diagnosed by a medical professional" for 98 of the 107.
- 52 flagged rows (833 samples) are left out, reason per row in `left_out_r44.csv`: 32 the record did not settle (largest: PRJEB94043 255
  samples, PRJNA1045701 212, PRJNA788972 77), 16 of PRJNA1004560 where the code rests on an endoscopic finding ("small aphthous", "Inflamed") and
  the detail on a numeric field, 4 where my role differs from the hand-over's. The file also lists the 27 MMC patterns that need no change (1,374
  samples).
- 9 settled rows (409 samples) were applied as handed over although their quote cites an archive record the hand-over's check could not read
  (`quote_check = source_not_a_cached_paper`); these are not quote failures. Verification spend: about US$4 of the US$10 allowed (58 reader
  calls, all claude-opus-5).

## 4. `condition_terms_r44.csv` — 6,450 rows, 784 condition ids
525 per-project rows (the changed pairs under their new code and detail; a per-project row wins) and 5,925 global rows. Coverage tested against
the package with the changes applied: all 11,090 (project, code, detail) triples resolve, including the 2,856 that carry a detail but no code
(one global row, `health_condition` empty, `health_condition_detail = '*'`, role `unclear`, no term). Nine codes got a `*` row because the code
by itself names exactly one condition: `crohns_disease`, `ulcerative_colitis`, `ibs`, `ibd_unspecified`, `colorectal_cancer`,
`colorectal_adenoma`, `type1_diabetes`, `nec`, `malnutrition`. `obesity` ("Obesity / overweight") and `type2_diabetes` ("Type 2 diabetes /
prediabetes") are not on that list: their labels name two conditions.

## 5. `build_condition_explorer.py` — reproduction, then the changes
`--package DIR --terms FILE --static <hand-over folder> --out DIR [--check]`; DuckDB at a 2 GB limit, no network, every count from the package.
On 1.34.2 with the labels as they are (terms = `condition_strings.csv` plus the 21 per-project rows for the 7 strings the hand-over settles per
project) it returns the same 788 conditions, 2,992 condition-project rows and 224 associations, the same `condition_id` and project keys, and the
spec's checks hold (unique id and anchor; 24 "this condition" rows summing to 1,918 testable and 515 replicated; cases 190,896). 41 of the 45
main columns and 13 of the 15 project columns reproduce exactly. The rest:

- `mondo_id`, 25 of 788 rows: the hand-over blanks it where the MONDO term is only a broader match. That flag (`mondo_match`) is not a column of
  the terms table, so the script cannot see it; it writes the MONDO id all the condition's label strings agree on. Add `mondo_match` to the terms
  table to make this column identical.
- `n_label_strings`, 12 rows (+1 or +2): the script counts label strings in the terms table, where a per-project row adds a string under its own
  condition id while the global row still maps the pair to the old one.
- `countries` 28 rows and `body_sites` 19 in the main table, `age_bands` 35 and `body_sites` 40 in the project table: same entries, same counts,
  ordered differently where counts tie (the hand-over's within-tie order came from pandas insertion order and is not recoverable). Row order
  within equal `cases` differs likewise; the script sorts by cases descending, then condition id.

With my changes simulated in DuckDB (15,910 samples change their pair, 4,708 of them their code): **785 conditions (−3), 2,990 condition-project
rows (−2), cases 190,896 → 190,940**; the replication checks still hold. Three rows disappear because no case keeps their term — Invasive fungal
infection (HP:0020101, 46), pancreatic intraductal papillary-mucinous neoplasm (MONDO:0004286, 2), immunodeficiency-related disorder
(MONDO:0024572, 1). Ten rows move: obesity disorder +92, pancreatic ductal adenocarcinoma +31, escherichia coli infection +8, irritable bowel
syndrome +4, Crohn disease −25, patent ductus arteriosus −9, sickle cell disease −3, chronic pancreatitis −2, mucinous neoplasm −2, cerebral
palsy −1. Rebuild the three tables on the real 1.35.0 package before the site build.
