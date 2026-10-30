# Stage 7 — supplementary tables joined to samples (R2026.30)

Most archive records do not state age, sex or health condition. The paper often does, in a supplementary table, but a
table is only useful if its rows can be matched to the deposited samples. This stage does that match deterministically,
and only then asks a model what the matched columns mean.

## How it works

1. **Which tables can be joined** (`scripts/join_ladder_audit.py`, no model calls). For every in-scope project with a
   paper in the open-access cache, every spreadsheet in that paper's supplementary files is read and each column is
   tested against the project's archive keys — run, sample and secondary sample accession, sample alias, sample title,
   library name, experiment alias — at four rungs: exact, case-folded, stem (lane and read suffixes removed), and
   trailing number. A column counts as a join when it matches at least half the project's samples.
2. **Extraction** (`scripts/extract_supp_rows.py`, no model calls). The joining table is re-read, the header row is
   found from where the matching values start, every row is joined to one sample accession, and the remaining columns
   become long rows shaped exactly like harvested BioSample attributes. Two guards reject false joins: a per-sample
   metadata table must have **one row per sample** (a matrix whose numbers coincide with sample numbers does not), and
   its columns must be **named**, not numbered.
3. **What the columns mean** (`scripts/classify_supp_columns.py`, utility tier). Each distinct column name, with a few
   example values, is classified into a pack field or `none`. Sequencing statistics, assay readouts and identifiers are
   `none`; an ambiguous name with uninformative examples is `none` as well, because a wrong field is worse than none.
4. **Values** (`scripts/map_attribute_pairs.py`, utility tier — the same prompts and the same cache as stage 5, so the
   two sources are normalised identically) and **assembly** (`scripts/assemble_supp_values.py`, no model calls).

## What it found

| Step | Projects |
|---|---:|
| In-scope projects with supplementary files on disk | 826 |
| Audited for a join | 784 |
| No column joins to the samples | 557 |
| At least one column joins (≥ 50 % of samples) | 196 |
| Survived the one-row-per-sample and named-column guards | 126 |
| Contributed at least one committed value | 60 |

1,059,653 long rows were extracted over 9,599 distinct column names. 137 of those column
names carry a catalog field; the other 9,462 are sequencing statistics, assay readouts,
identifiers or free text.

**17,201 values were committed for 6,878 specimens in 60 projects**
(route R2, which ranks below the archive — a supplementary value is used only where the archive is silent):

| Field | Values |
|---|---:|
| sex | 3,570 |
| subject id | 2,163 |
| condition code | 2,052 |
| age days | 1,303 |
| body site code | 1,282 |
| timepoint label | 1,102 |
| life stage | 1,005 |
| smoking status detail | 896 |
| ethnicity or population | 828 |
| location locality | 571 |
| diet detail | 488 |
| antibiotic exposure detail | 472 |
| country iso2 | 393 |
| study arm | 375 |
| bmi | 320 |
| collection year | 279 |
| intervention detail | 102 |

## The cross-check that decides whether to trust a join

Where a sample has **both** an archive value and a supplementary value for the same field, the two must agree. That is a
free test of the join, and it is applied per project and field:

| Field | Values checkable | Agreement |
|---|---:|---:|
| sex | 1,248 | 93.1 % |
| body site | 1,033 | 96.3 % |
| country | 393 | 99.2 % |
| health condition | 131 | 100 % |
| age | 34 | 100 % |

Two projects failed and were dropped entirely: **PRJNA819439** (42 % of its checkable sex values disagreed) and
**PRJNA281366** (body site). A join that is wrong where it can be checked is not trusted where it cannot.

## What this does not cover

* Supplementary files that are **not spreadsheets** — Word documents and PDFs hold many per-sample tables and are not
  read here. That is the largest remaining gain and the obvious next pass.
* The 557 projects whose tables carry no sample identifier at all. Nothing deterministic can be
  done for them; their per-sample values can only come from the authors.
* 1,246 (sample, field) pairs where two columns of the same project disagreed. Neither value was kept.

Model cost of this stage: **$1.09** (column classification $0.76, value mapping $0.33).
