# Closing the recall gap and reading Word / PDF supplements (R2026.30)

The first release check found that stage 1 almost never *included* a project wrongly (97.7 % of inclusions confirmed)
but sometimes *excluded* one that belonged (91.4 % of exclusions confirmed). This pass measured that error per
exclusion reason and then re-examined only where it was real, rather than re-reading 2,600 excluded projects.

## Where the recall error actually is

A 40-project blind sample of each exclusion reason, re-classified at the Opus-5 audit tier:

| Excluded for | Sampled | Wrongly excluded | Rate | Projects with that reason |
|---|---:|---:|---:|---:|
| virus genome surveillance | 40 | 7 | 17.5% | 154 |
| human tissue rna | 40 | 2 | 5.0% | 310 |
| culture enriched | 40 | 3 | 7.5% | 82 |
| animal host | 40 | 0 | 0.0% | 646 |
| human genome | 40 | 0 | 0.0% | 278 |
| single cell | 40 | 0 | 0.0% | 269 |

`animal_host`, `human_genome` and `single_cell` were clean at 0 of 40 each, so they were left alone — re-reading 1,193
projects to find nothing would have cost more than the rest of this release. The other three reasons were re-checked in
full (543 projects).

## What came back

**35 further projects were recovered** — 21 excluded as virus genome surveillance that are in fact clinical
metagenomic or metatranscriptomic sequencing of patient specimens, 7 called human tissue RNA that are microbial
community RNA, and 6 called culture-enriched that sequenced the specimen directly. Their runs, samples and attributes
were harvested and mapped, and their papers were linked and read like every other in-scope project.

Together with the 37 from the first recheck, **72 projects were recovered by the audits**. The virus-surveillance
boundary is the one worth remembering: 18.8 % of what stage 1 put there belonged in the catalog, because a clinical
mNGS study that reports viruses looks, in an archive record, much like a surveillance deposit.

## The 11 refused projects

Both stage-1 readings had refused to classify these. One reading at the Opus-5 tier resolved **9 of 11** (1 in scope,
2 partly, 6 out). **PRJNA1071083** and **PRJNA770495** refused again and stay unresolved with archive metadata only.

## Word and PDF supplementary tables

The extractor now reads tables from Word documents (python-docx) and PDFs (pdfplumber's ruling-line strategy; the
whitespace strategy was tried and dropped — it turns prose into character-wide fake columns that no downstream guard
can undo). The audit and the extraction share one table reader, so they always see the same tables.

Of 514 in-scope projects that had a Word or PDF supplement but no joinable spreadsheet, **77 had a joinable table** and
24 contributed values after the guards and the archive cross-check: 5,603 values for 2,519
specimens. Combined with the spreadsheet pass, supplementary tables now provide **22,804 values for
9,397 specimens across 84 projects**.

## Two data-quality fixes found on the way

* **Amplicon data sets were inside the scope.** 5,457 runs labelled AMPLICON sat in projects admitted for their shotgun
  runs. They are now dropped at run level — with the read check overriding the label, so the 10 runs the archive calls
  amplicon but whose reads are shotgun stay. 5,009 specimens left with no in-scope data set were removed, and 2
  projects that lost every specimen are marked `no_data_set_in_scope` rather than published as empty pages.
* **DNA/RNA conflicts: 1 remaining, from 31.** An archive label of TRANSCRIPTOMIC or RNA-Seq is a deliberate statement
  about that deposit and now outranks a paper summarised as DNA; where the archive says DNA and the paper says RNA, the
  measured rRNA read share decides. Only PRJNA1143542 is still unresolved — its runs cannot be read.

## Scope after this pass

5,050 projects · 780,892 specimens · 991,265 runs
(960,059 DNA, 31,206 RNA).
Coverage: body site 95.7%, country 88.2%,
collection year 77.1%, life stage 77.3%,
subject 60.0%, health condition 39.0%,
sex 28.7%, age 24.3%.
