# R2026.30 — every human body site, DNA and RNA

This release widens the catalog from human gut DNA shotgun metagenomes to **every human-associated shotgun metagenome
in the registry — any body site, DNA and RNA kept as separate data sets** — and adds the sequenced mock communities
used as benchmarks. The gut catalog is unchanged and is now one track inside the wider scope.

## What is in it

| | |
|---|---:|
| Projects | **4,950** (2,657 gut catalog · 2,293 added now) |
| Specimens | **765,294** |
| Sequencing runs | **967,139** (935,933 DNA · 31,206 RNA) |
| Projects by nucleic acid | 4,482 DNA · 292 RNA · 173 both · 1 unresolved |

Body sites: gut stool 465,970 · unknown site 57,499 · blood tissue 41,390 · vaginal urogenital 33,196 · respiratory lower 30,769 · oral 28,294 · nasal nasopharyngeal 25,646 · skin 23,289.

**Nucleic acid belongs to a data set, not to a specimen.** One sample can carry a DNA library and an RNA library, and
they are recorded separately. The value comes from the archive's library source; where the paper disagrees, an explicit
archive label of TRANSCRIPTOMIC or RNA-Seq wins (it is a deliberate statement about *that* deposit), and where the
archive says DNA and the paper says RNA the **measured rRNA read share** decides — an undepleted RNA library of a
microbial community keeps a large ribosomal fraction. That left **1** project unresolved, from 31.

Two kinds of data set are excluded at run level even when their project is in scope: **single-cell genomes** (480 runs)
and **amplicon libraries** (5,457 runs in 56 projects), the latter unless the read check shows the reads are in fact
shotgun. 5,009 specimens that were left with no in-scope data set were removed with them.

## Per-specimen coverage

| Field | Share of specimens |
|---|---:|
| body site | 93.6% |
| country | 88.2% |
| collection year | 77.4% |
| life stage | 76.8% |
| subject (person) | 59.8% |
| health condition | 38.5% |
| sex | 28.6% |
| age in days | 24.5% |

Body site carries a **basis**: whether it came from the sample's own record, from a project that sampled a single site,
or from a supplementary table. Age, sex and condition stay thin because most archive records do not state them.

## How the new projects were decided

1. **Registry refresh** — 538 new studies screened, 99 with a human signal added.
2. **Scope classification** — 4,932 candidate projects, two independent readings each, Opus-5 tie-break on every
   disagreement. **2,293 projects are in scope.** Two projects where both
   readings refused twice keep archive metadata only.
3. **Paper link audit** — which paper *generated* the data rather than merely citing it:
   1,274 projects have one.
4. **Full-text reading** — 1,114 projects read for platform, extraction and library kits, RNA methods, study
   design, metadata-access route, named places and living-conditions items, each with a quote.
5. **Archive harvest and normalisation** — 2.8 M attribute rows; distinct attribute values mapped to codes, with
   10,530 reused from the gut work at no cost.
6. **Supplementary tables** — spreadsheets, Word documents and PDFs joined deterministically to samples where a column
   matches the archive keys, then cross-checked against the archive: **22,804 values for
   9,397 specimens** in 84 projects.
7. **Release checks** — a blind re-classification at a higher model tier, a recall audit per exclusion reason, and a
   full recheck of the three reasons where the error was real. **72 projects were recovered** and given the same
   harvest, mapping and paper reading as every other project.

Model spend for the whole update: **≈ $271** against $1,200 budgeted.

## Still open

* **2 projects** where both classification readings refused twice (PRJNA1071083, PRJNA770495) — archive metadata only.
* **1 project** whose archive label and paper disagree on DNA vs RNA and whose runs cannot be read (PRJNA1143542).
* **149 projects** where both readings agreed the project is out of scope but differed on the reason; the verdict
  stands, the reason is the less certain of the two.
* **3 gut projects** (PRJEB96943, PRJNA1377467, PRJNA1457996 — 77 samples) entered the *gut* scope through this
  cycle's registry refresh after that scope's annotation passes had already run. They carry their archive values and
  are recorded in `config/pass_coverage_exceptions.csv`; their gut passes run first in the next monthly refresh.

## New on the site

* **All body sites** section: what the scope holds, a filterable project list (body site, DNA/RNA, track), and a project
  page for every newly added project.
* Project pages carry a **methods strip** (extraction · library preparation · sequencing, with everything else as
  chips), a small **body figure** showing which sites were sampled, a **study-design block** that names the per-sample
  variable behind each statement, and a **living-conditions panel** with the exposure items read from the paper. Where
  the paper's participant count cannot be reconciled with the deposited records, the page says so instead of printing a
  number.

## Known gaps

* Supplementary tables are read from spreadsheets, Word and PDF; a PDF table is only taken when it has ruling lines.
* Recall was audited for every exclusion reason and re-checked in full for the three where the error was material;
  `animal_host`, `human_genome` and `single_cell` were clean at 0 of 40 sampled each and were not re-read.
* Area-level water and sanitation joins are deferred pending licence checks.
