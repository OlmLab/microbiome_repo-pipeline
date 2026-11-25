# Condition explorer — page specification for the Editor

**Purpose.** One searchable page that answers "which projects and how many samples does the Repo hold for condition X, and did published microbiome associations for it replicate?"

**Place.** Insights menu, next to the taxon explorer; same layout, type and table component as the home page and the taxon explorer.

**Data.** Three tables in this folder, shipped with the site and offered for download:
- `condition_explorer.csv` (788 rows, one per condition id) drives the main table.
- `condition_explorer_projects.csv` (2,992 rows) drives the per-condition project list.
- `condition_explorer_associations.csv` (224 rows) drives the per-condition list of replicated associations.

## Layout

1. Header line with totals read from the table at build time: number of conditions, case samples, projects, and the release id.
2. One search box. It filters rows on `condition_name`, `condition_id`, `efo_id`, `mondo_id`, `efo_legacy_ids` and `disease_areas`, case-insensitive, matching any word.
3. Main table, one row per condition, sorted by `cases` descending by default, every column sortable:

| Column shown | Source column | Note |
|---|---|---|
| Condition | `condition_name` | links to the condition's own section (see 5) |
| Ids | `efo_id`, `mondo_id` | each id links to its OLS page; show the legacy EFO id as a tooltip |
| Projects | `projects` | |
| Cases | `cases` | tooltip: current, treated or in remission, at risk, history |
| Healthy controls | `healthy_controls` | in the same projects |
| Countries | `n_countries` | tooltip or expandable list from `countries` (ISO code:count) |
| Age bands | `cases_age_*` | one small stacked bar per row on a linear axis, bands in the order neonate, infant, child, adolescent, adult, elderly, unknown |
| Profiled | `cases_profiled` | samples with a Sandpiper profile |
| Replicated associations | `replication_claims_replicated` / `replication_claims_testable` | blank when `replication_scope` is "not tested"; when it is "broader condition" show the numbers greyed with the text "tested as <replication_condition_name>" |

4. Download button above the table: `condition_explorer.csv`; a second link for the two detail tables.
5. A link per condition: each condition has a stable anchor, `#<anchor>` (the `anchor` column, for example `#MONDO_0005011`). Opening it shows a section, or a page of its own if the Editor prefers one page per condition, with:
   - the counts of the row, including `cases_incl_narrower` and links to the broader and narrower conditions (`broader_condition_ids`, `narrower_condition_ids`);
   - the project list from `condition_explorer_projects.csv` (project accession linked to the project page, title, cases, healthy controls, profiled cases, countries), sorted by cases;
   - the replicated associations from `condition_explorer_associations.csv` (taxon linked to the taxon explorer, rank, direction, published claims and papers, pooled AUC, catalog projects tested, q), with a link to the replication page.

## Wording that must be on the page

- Counts are of samples whose health-condition label names the condition as the donor's main condition. Labels come from archive records and papers and were mapped to EFO 3.96.0 and MONDO (2026-11-03) by two or three independent readings.
- Healthy controls are the samples labelled healthy in the projects that also hold cases of the condition; they are not matched controls.
- The replication numbers are the registered result of the test run on 2026-11-15 (strict mapping, primary analysis). A row marked "tested as <broader condition>" shows the result for that broader condition, not for the row's own condition.
- A condition with no row is not necessarily absent: 154 label strings name a condition for which no ontology term was found, and 51 are unclear or unsettled.

## Checks for the build

- Totals over the 24 rows with `replication_scope` = "this condition": 1,918 testable claims, 515 replicated.
- Sum of `cases` = 190,896; `condition_id` is unique; every `anchor` is unique.
- The tables are for package 1.34.0 / 1.34.1. If a later package changes health_condition values, the tables must be rebuilt first.
