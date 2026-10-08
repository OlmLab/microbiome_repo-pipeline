#!/usr/bin/env python
"""package_docs.py — regenerate build_counts.json from the tables and bring the package docs up to the release.

    python -m catalog.release.package_docs --package build/package --package-version 1.3.0 --release-id R2026.1 \
        --build-date 2026-09-26 [--changelog-entry docs/package_changelog/1.3.0.md]

* build_counts.json: recomputed from the tables (n_samples, n_studies, n_catalog_scope, n_biosample_units, n_run_units,
  n_runs, n_profiled_samples, n_author_rows, n_determinations_current, package_version, release_id) — the 1.2.2
  package shipped a stale copy (package_version 1.2.1, n_catalog_scope 71,795).
* README.md: heading → "data package v<semver> (<build_date>)" (make_version --check requires it), Files-table rows for
  the new files, a "Release model" section (once).
* DATA_DICTIONARY.md: the three release columns appended to every fact table's section / complete column reference,
  a section for sample_determinations_all.parquet and releases.csv, with the reconstruction caveat (once).
* CHANGELOG.md: the entry file is prepended when its first heading is not yet present.
* Contribute worklist (R2026.2, config/contribute.yaml): README Files-table rows + a DATA_DICTIONARY section 'Contribute worklist'
  listing every column of contribute_worklist.csv / contribute_worklist_fields.csv — only when the files are in the package.
Deterministic and idempotent (re-running on an updated package changes nothing).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import pandas as pd
import re as _re

FRAME_TOKEN_RE = re.compile(r'\s*\((?:frame|session)\s+[0-9a-f]{6,}[^)]*\)|\b(?:frame|session)\s+[0-9a-f]{8,}\b')


def sanitise_session_tokens(package: str) -> dict:
    """F7: strip session/frame identifiers from free-text columns of published tables (note, evidence, validator_msg)."""
    out = {}
    for fn, cols in (("human_review_queue.csv", ["note", "validator_msg"]), ("universe_studies_all.parquet", ["note", "validator_msg"])):
        path = os.path.join(package, fn)
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, low_memory=False) if fn.endswith(".csv") else pd.read_parquet(path)
        n = 0
        for c in cols:
            if c in df.columns and (df[c].dtype == object or str(df[c].dtype).startswith(('str', 'string'))):
                m = df[c].astype(str).str.contains(FRAME_TOKEN_RE.pattern, regex=True, na=False)
                n += int(m.sum())
                df.loc[m, c] = df.loc[m, c].astype(str).map(lambda t: _re.sub(r'\s{2,}', ' ', FRAME_TOKEN_RE.sub('', t)).strip())
        if n:
            (df.to_csv(path, index=False) if fn.endswith(".csv") else df.to_parquet(path, index=False))
        out[fn] = n
    return out

import pyarrow.parquet as pq
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
CONFIG_DIR = os.environ.get("CATALOG_CONFIG_DIR", os.path.join(REPO, "config"))
COLS = ("release_added", "release_retired", "package_added")
COL_DOC = {
    "release_added": "release in which this row first became visible (`R<YYYY>.<n>` or a pre-numbered package semver `1.0.0`–`1.2.2`); pre-1.3.0 values are reconstructed — see DATA_DICTIONARY 'Release columns'",
    "release_retired": "release that replaced/removed the row; null (empty in CSV) = current row",
    "package_added": "semver of the data package in which the row first appeared",
}


def _rows(path: str) -> int:
    if path.endswith(".parquet"):
        return pq.ParquetFile(path).metadata.num_rows
    with open(path, "rb") as f:
        return max(sum(1 for _ in f) - 1, 0)


def build_counts(pkg: str, package_version: str, release_id: str) -> dict:
    smw = pd.read_parquet(os.path.join(pkg, "sample_metadata_wide.parquet"), columns=["sample_key", "catalog_scope", "sample_unit", "age_scope", "body_site_class"])
    c = dict(package_version=package_version, release_id=release_id,
             n_samples=int(len(smw)), n_catalog_scope=int(smw["catalog_scope"].fillna(False).astype(bool).sum()),
             n_biosample_units=int((smw["sample_unit"] == "biosample").sum()), n_run_units=int((smw["sample_unit"] == "run").sum()),
             n_age_scope_infant=int(smw["age_scope"].isin(["infant_evidenced", "study_all_infant"]).sum()),
             n_body_site_excluded=int((smw["body_site_class"] == "excluded").sum()),
             n_studies=_rows(os.path.join(pkg, "study_metadata_wide.parquet")), n_runs=_rows(os.path.join(pkg, "runs.parquet")),
             n_determinations_current=_rows(os.path.join(pkg, "sample_determinations.parquet")))
    for key, fn in (("n_profiled_samples", "sandpiper_sample_summary.parquet"), ("n_author_rows", "authors.parquet"),
                    ("n_parent_biosamples", "parent_biosamples.parquet"), ("n_universe_studies", "universe_studies_all.parquet"),
                    ("n_determinations_all", "sample_determinations_all.parquet"), ("n_value_history", "value_history.parquet")):
        p = os.path.join(pkg, fn)
        if os.path.exists(p):
            c[key] = _rows(p)
    all_p = os.path.join(pkg, "sample_determinations_all.parquet")
    if os.path.exists(all_p):
        rr = pd.read_parquet(all_p, columns=["release_retired"])["release_retired"]
        c["n_determinations_retired"] = int(rr.notna().sum())
    c["n_wide_cols"] = len(pq.ParquetFile(os.path.join(pkg, "sample_metadata_wide.parquet")).schema_arrow.names)
    c["n_study_cols"] = len(pq.ParquetFile(os.path.join(pkg, "study_metadata_wide.parquet")).schema_arrow.names)
    return c


def update_readme(pkg: str, package_version: str, release_id: str, build_date: str, counts: dict) -> None:
    p = os.path.join(pkg, "README.md")
    s = open(p, encoding="utf-8").read()
    s, n = re.subn(r"data package v\d+(?:\.\d+)* \(\d{4}-\d{2}-\d{2}\)", f"data package v{package_version} ({build_date})", s, count=1)
    assert n == 1, "README heading 'data package vX.Y.Z (date)' not found"
    # Rebrand (R2026.6): the package carries both tiers; the heading names the whole catalog, the infant catalog is its first curated scope.
    # The title comes from config/site.yaml (site.title / site.curated_scope_title) so a later rename is a one-line config change.
    try:
        import yaml as _y
        _site = _y.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "site.yaml"), encoding="utf-8"))["site"]
        _title, _scope = _site.get("title"), _site.get("curated_scope_title")
    except Exception:
        _title, _scope = None, None
    if _title and s.startswith("# ") and " — data package" in s.split("\n", 1)[0]:
        # the H1 always carries the current site title (config/site.yaml) — a rename is a one-line config change
        s = re.sub(r"^# .*? — data package", f"# {_title} — data package", s, count=1)
        intro = ("\n\n**Two tiers in one package.** The `registry_*` tables cover every public human shotgun-metagenome study in ENA/SRA/DDBJ "
                 "(all body sites, all ages; archive-only classification plus the harvested BioSample tier). All other tables belong to the first "
                 f"**curated scope**, the {_scope} — the description below is that scope's.\n")
        if "**Two tiers in one package.**" not in s:
            first_blank = s.index("\n\n") if "\n\n" in s else len(s)
            s = s[:first_blank] + intro + s[first_blank:]
    marker = "## Release model"
    if marker not in s:
        rows = [f"| `sample_determinations_all.parquet` | {counts.get('n_determinations_all', ''):,} | Current **and retired** determinations: `sample_determinations` columns + `release_added`, `release_retired` (null = current), `package_added`, `retired_reason`, `retired_change_stage` — the per-field value timeline — {release_id} |",
                f"| `releases.csv` | {counts.get('n_releases', ''):,} | Release registry: one row per release id (package version, date, tags, DOI, headline counts, notes file) — {release_id} |",
                f"| `RELEASE_NOTES_{release_id}.md` | | Generated diff report vs the previous package (studies/samples, coverage, verdict flips, findings, Sandpiper, gold, schema) — {release_id} |"]
        anchor = "| `DATA_DICTIONARY.md` | | Every column, every vocabulary |"
        assert anchor in s, "README Files table anchor row not found"
        s = s.replace(anchor, "\n".join(rows) + "\n" + anchor, 1)
        section = f"""
## Release model ({release_id}, package {package_version})
This is the first **numbered catalog release** (`R<YYYY>.<n>`; `VERSION.json.release_id`). Every fact table
(`sample_determinations`, `universe_studies_all`, `study_metadata_wide`, `cohorts`, `study_paper_links`, `sandpiper_*`)
carries three trailing columns `release_added`, `release_retired` (null = current) and `package_added`; `sample_metadata_wide`
is derived and has none. `sample_determinations_all.parquet` adds the retired determinations ({counts.get('n_determinations_retired', 0):,} rows,
reconstructed from `value_history`) so a per-field value timeline is one `ORDER BY release_added` query; `releases.csv` is the
registry of every release. **Caveat:** packages before 1.3.0 had no release columns, so `release_added` for pre-existing rows is
reconstructed from `value_history.change_stage` / `src_track` and is exact only for rows those stages touched (adult-age
recommits, run-level rows, retired values); every other pre-1.3.0 row is labelled `1.0.0` by assumption. Full rules:
`DATA_DICTIONARY.md` 'Release columns' and the pipeline's `docs/RELEASES.md`.
"""
        s = s.replace("\n## How to read a value", section + "\n## How to read a value", 1)
    open(p, "w", encoding="utf-8").write(s)


def update_dictionary(pkg: str, package_version: str, release_id: str, cfg: dict, counts: dict) -> None:
    p = os.path.join(pkg, "DATA_DICTIONARY.md")
    s = open(p, encoding="utf-8").read()
    if "## Release columns" in s:
        return
    fact_files = [t["file"] for t in cfg["fact_tables"]] + [t.get("csv_twin") for t in cfg["fact_tables"] if t.get("csv_twin")]
    # 1. sample_determinations section (two-column table) — append rows before "## Routes"
    det_rows = "\n".join(f"| `{c}` | {COL_DOC[c]} |" for c in COLS)
    s = s.replace("| `src_track` | pipeline pass that produced the row |\n", f"| `src_track` | pipeline pass that produced the row |\n{det_rows}\n", 1)
    # 2. complete column reference for study_metadata_wide (3-column table; the build fails on an undocumented column)
    m = re.search(r"### study_metadata_wide\.parquet \((\d+) columns\)", s)
    if m:
        n = int(m.group(1))
        s = s.replace(m.group(0), f"### study_metadata_wide.parquet ({n + 3} columns)", 1)
        tail = "| `shared_biosample_note` | object | human-readable note for the five studies sharing BioSamples (F6) |"
        assert tail in s
        s = s.replace(tail, tail + "\n" + "\n".join(f"| `{c}` | string | {COL_DOC[c]} |" for c in COLS), 1)
    section = f"""
## Release columns ({release_id}, package {package_version})
Fact tables — {', '.join(f'`{f}`' for f in fact_files)} — end with three columns:

| column | dtype | meaning |
|---|---|---|
| `release_added` | string | {COL_DOC['release_added']} |
| `release_retired` | string \\| null | {COL_DOC['release_retired']} |
| `package_added` | string | {COL_DOC['package_added']} |

Release ids are `R<YYYY>.<n>` (first: `{release_id}` = package {package_version}) or, for states published before numbering, the package
semver `1.0.0` (first public package, 2026-09-25), `1.1.0` (release v11), `1.2.0`, `1.2.1`, `1.2.2`. "Current" = `release_retired IS NULL`;
in the shipped fact tables every row is current. `sample_metadata_wide` is derived from `sample_determinations` and carries no release columns.

**Reconstruction caveat.** Packages before 1.3.0 carried no release columns, so `release_added` of pre-existing rows was reconstructed:
newest `value_history.change_stage` touching the (sample_key, field_name) or `sample_determinations.src_track`, mapped to the package the
CHANGELOG records for that stage (`sample_unit_fix` → 1.1.0, `auditor_review:B2`/`B9` → 1.2.0, `auditor_review:R3-3` → 1.2.1,
`owner_decision` → 1.2.2). Exact for rows those stages touched ({counts.get('n_release_added_reconstructed', '')} current determinations:
`src_track = sample_unit_fix` → 1.1.0, `src_track = adult_scope_fix` → 1.2.0); every other pre-1.3.0 row is labelled `1.0.0` by assumption
(the first package is not archived as a table, so this cannot be verified row by row). `package_added` equals `release_added` for all
pre-1.3.0 rows. Study verdict rows are all `1.0.0` (no verdict change is dated after the first package); Sandpiper rows are `1.2.0`
(when Sandpiper landed; the v1.2.1 recomputation of existing rows is recorded in the CHANGELOG, not as retire+add).

## sample_determinations_all.parquet ({release_id})
`sample_determinations` columns + the three release columns + `retired_reason` (string|null; `value_history.reason`) +
`retired_change_stage` (string|null; `value_history.change_stage`). Rows: every current determination ({counts.get('n_determinations_current', 0):,},
`release_retired` null) ∪ every **retired published value** ({counts.get('n_determinations_retired', 0):,}) reconstructed from `value_history`
rows whose `status` ∈ {{`superseded`, `moved_to_parent_biosamples`, `recommitted_out_of_scope_host`, `recommitted_out_of_scope_isolate`}}
(`release_retired` = the package of their `change_stage`). value_history rows with status `rejected`, `dropped`, `not_committed_duplicate`,
`recommitted_out_of_scope_adult` (the pre-history of a row that IS current) or `auditor_finding_applied` were never published and are not
rows here. Exactly one current row per (`sample_key`, `field_name`); retired rows may repeat a key. `sample_determinations_superseded.parquet`
(223 rows) is the same set as the `superseded` rows of stages `sample_unit_fix` (75) and `auditor_review:B9` (148) and is kept for the v1.1 layout.

## releases.csv ({release_id})
One row per release id, oldest first: `release_id`, `package_version`, `release_date`, `data_tag`, `site_tag`, `doi` (empty until Zenodo
mints one), `n_studies_included`, `n_samples`, `n_catalog_scope`, `n_determinations_current`, `sandpiper_version`, `notes_file`. Historical
rows carry only the counts the CHANGELOGs state (empty otherwise — never invented).
"""
    s = s.replace("\n## Sandpiper columns (added v1.2.0)", section + "\n## Sandpiper columns (added v1.2.0)", 1)
    open(p, "w", encoding="utf-8").write(s)


WORKLIST_COL_DOC = {
    "rank": ("int64", "1 = highest priority_score (ties: n_samples desc, accession)"),
    "study_accession": ("string", "BioProject accession (universe_studies_all key)"),
    "study_title": ("string", "universe_studies_all.study_title"),
    "cohort_id": ("string", "cohorts.csv id"),
    "cohort_name": ("string", "cohorts.csv name"),
    "triage_verdict": ("string", "`include` | `uncertain` (open studies only)"),
    "n_samples": ("int64", "sample units of the study (universe_studies_all.n_samples)"),
    "n_catalog_scope": ("int64", "sample_metadata_wide rows with catalog_scope = True"),
    "n_infant_samples_est": ("float64 | null", "triage estimate of infant samples (universe_studies_all)"),
    "missing_fields": ("string", "';'-joined short field names (age, delivery, feeding, preterm, antibiotics, probiotic) whose coverage < 0.5"),
    "n_missing_fields": ("int64", "count of missing_fields (≥ 1 for every listed study)"),
    "coverage_<field>": ("float64", "fraction of catalog_scope samples with a value for the field (body-site scope {primary, unknown} when n_catalog_scope = 0); six columns: age, delivery, feeding, preterm, antibiotics, probiotic"),
    "best_tier_<field>": ("string", "best recoverability tier for study × field (`R1` archive attribute · `R2` supplementary table · `R3` paper text · `R4` abstract · `R0` none); six columns"),
    "blocker_code": ("string", "dominant reason the study is open — vocabulary in config/contribute.yaml `blocker_codes` (controlled_access, no_linked_paper, paywalled_abstract_only, tables_unjoinable_need_key, pdf_only_supplement, no_supplement_found, archive_only_uncertain, unitless_age_needs_curator, partial_coverage); decision order `blocker_order`"),
    "blocker_detail": ("string ≤ 200", "the deciding evidence (counts, access tier, sample-ID forms from RESCUE_REPORT_v2, human-review note)"),
    "unlock_text": ("string ≤ 200", "imperative 'what would unlock this' sentence from the `unlock_templates` of the blocker (id form / missing fields substituted)"),
    "contribution_type": ("string", "primary ask: per_sample_table | id_key | paper_pointer | age_schedule | verdict_evidence"),
    "n_linked_papers": ("int64", "rows in study_paper_links for the study"),
    "own_data_pmids": ("string", "';'-joined PMIDs of the linked papers (empty when none)"),
    "n_supp_tables_inventoried": ("int64", "supp_inventory members with member_type = table across the linked papers"),
    "controlled_access": ("bool", "study or its cohort is in controlled_access_registry.csv (non-open tier) or flagged controlled in extraction_worklist / universe_studies_all"),
    "priority_score": ("float64", "Σ over missing fields of weight × (1 − coverage) × log10(n_catalog_scope + 1); weights age 3, delivery 2, feeding 2, preterm 1.5, antibiotics 1, probiotic 0.5"),
    "ena_url": ("string", "ENA browser URL of the study"),
    "ncbi_url": ("string", "NCBI BioProject URL"),
    "issue_url": ("string", "prefilled GitHub Issue (form catalog-contribution.yml, label contribution; query keys study_accession, contribution_type, release_tag, title)"),
}
FIELDS_COL_DOC = {
    "study_accession": ("string", "BioProject accession"),
    "field": ("string", "age | delivery | feeding | preterm | antibiotics | probiotic (short names of age_at_collection_days, delivery_mode, feeding_mode, preterm_status, antibiotic_exposure, probiotic_exposure)"),
    "coverage": ("float64", "fraction of in-scope samples with a value (same scope rule as the worklist)"),
    "n_with_value": ("int64", "in-scope samples with a value"),
    "n_catalog_scope": ("int64", "catalog_scope samples of the study"),
    "best_tier": ("string", "best recoverability tier R0–R4 for the study × field"),
    "blocker_code": ("string", "`complete` when coverage ≥ 0.5, else the study blocker (only the age field carries unitless_age_needs_curator; other fields then partial_coverage)"),
    "evidence": ("string ≤ 120", "tier + first recoverability evidence quote/source + note"),
}


def update_docs_worklist(pkg: str, package_version: str, release_id: str, counts: dict) -> bool:
    """README Files rows + DATA_DICTIONARY 'Contribute worklist' section; no-op when the tables are absent or already documented."""
    wl_p, fl_p = os.path.join(pkg, "contribute_worklist.csv"), os.path.join(pkg, "contribute_worklist_fields.csv")
    if not (os.path.exists(wl_p) and os.path.exists(fl_p)):
        return False
    n_wl, n_fl = _rows(wl_p), _rows(fl_p)
    counts["n_worklist_studies"], counts["n_worklist_fields_rows"] = n_wl, n_fl
    rp = os.path.join(pkg, "README.md")
    s = open(rp, encoding="utf-8").read()
    if "`contribute_worklist.csv`" not in s:
        anchor = "| `DATA_DICTIONARY.md` | | Every column, every vocabulary |"
        assert anchor in s, "README Files table anchor row not found"
        rows = [f"| `contribute_worklist.csv` | {n_wl:,} | Community-contribution worklist: one row per OPEN included/uncertain study (≥ 1 of the six fields age/delivery/feeding/preterm/antibiotics/probiotic below 0.5 coverage on catalog_scope), ranked by priority_score, with blocker_code, unlock_text and a prefilled contribution Issue URL — {release_id} |",
                f"| `contribute_worklist_fields.csv` | {n_fl:,} | Study × field detail of the worklist (coverage, best recoverability tier, field-level blocker, evidence) — {release_id} |"]
        s = s.replace(anchor, "\n".join(rows) + "\n" + anchor, 1)
        open(rp, "w", encoding="utf-8").write(s)
    dp = os.path.join(pkg, "DATA_DICTIONARY.md")
    d = open(dp, encoding="utf-8").read()
    if "## Contribute worklist" in d:
        return True
    L = [f"\n## Contribute worklist ({release_id}, package {package_version})",
         "`contribute_worklist.csv` — one row per **open** study (`universe_studies_all.triage_verdict ∈ {include, uncertain}` and at least one of the",
         "six worklist fields below 0.5 coverage on its catalog_scope samples; studies with 0 catalog_scope samples are judged on body-site scope",
         "`body_site_class ∈ {primary, unknown}`). Complete studies are not listed. Rules, templates and vocabularies: the pipeline's",
         "`config/contribute.yaml` and `docs/CONTRIBUTE.md`. Read-only: nothing here is a curated value — every column is derived from the tables",
         "of this package and from the recoverability / R2-rescue / supplement-inventory artifacts registered in `config/inputs.json` (group `contribute`).", "",
         f"### contribute_worklist.csv ({n_wl:,} rows)", "", "| column | dtype | meaning |", "|---|---|---|"]
    L += [f"| `{c}` | {t} | {m} |" for c, (t, m) in WORKLIST_COL_DOC.items()]
    L += [f"| `{c}` | string | {COL_DOC[c]} |" for c in COLS]
    L += ["", f"### contribute_worklist_fields.csv ({n_fl:,} rows = 6 per worklist study)", "", "| column | dtype | meaning |", "|---|---|---|"]
    L += [f"| `{c}` | {t} | {m} |" for c, (t, m) in FIELDS_COL_DOC.items()]
    L += [f"| `{c}` | string | {COL_DOC[c]} |" for c in COLS]
    L += ["", "**Blocker codes** (one per study, decision order = listing order for included studies; uncertain studies always `archive_only_uncertain`):",
          "`controlled_access` (study/cohort in the controlled-access registry) · `no_linked_paper` (0 rows in study_paper_links) · `paywalled_abstract_only`",
          "(extraction_worklist access_tier C/D/E) · `tables_unjoinable_need_key` (R2 rescue: supplementary tables keyed by paper-internal names) ·",
          "`pdf_only_supplement` (only PDF/DOCX supplements) · `no_supplement_found` (paper but no inventoried supplement) · `unitless_age_needs_curator`",
          "(age column without unit) · `partial_coverage` (paper + tables processed, fields still missing). Field-level code `complete` = field at/above threshold.",
          "**Contribution types**: `per_sample_table`, `id_key`, `paper_pointer`, `age_schedule`, `verdict_evidence` (dropdown of the GitHub Issue form",
          "`catalog-contribution.yml` that `issue_url` opens prefilled). Rows get `release_retired` when the study becomes complete in a later release.", ""]
    marker = "\n## Sandpiper columns (added v1.2.0)"
    if marker in d:
        d = d.replace(marker, "\n".join(L) + marker, 1)
    else:
        d = d.rstrip() + "\n" + "\n".join(L)
    open(dp, "w", encoding="utf-8").write(d)
    return True


def update_docs_registry(pkg: str, package_version: str, release_id: str, counts: dict, schema_path: str | None = None) -> bool:
    """Registry tier (R2026.4, scale-up S1): README Files rows + DATA_DICTIONARY 'Registry tier' section generated from the frozen
    audit/registry_schema.json (column descriptions) — no-op when registry_studies.parquet is absent or already documented."""
    st_p, au_p = os.path.join(pkg, "registry_studies.parquet"), os.path.join(pkg, "registry_universe_audit.csv")
    if not os.path.exists(st_p):
        return False
    schema_path = schema_path or os.path.join(os.path.dirname(__file__), "..", "..", "..", "audit", "registry_schema.json")
    schema = json.load(open(schema_path, encoding="utf-8"))["tables"]
    n_st = _rows(st_p)
    rg = pd.read_parquet(st_p, columns=["host_human", "n_runs", "classification_stage"])
    counts["n_registry_studies"] = n_st
    counts["n_registry_runs"] = int(pd.to_numeric(rg["n_runs"], errors="coerce").fillna(0).sum())
    counts["n_registry_human"] = int((rg["host_human"] == "yes").sum())
    counts["n_registry_llm"] = int(rg["classification_stage"].isin(["sonnet_x2", "opus_adjudicated"]).sum())
    rp = os.path.join(pkg, "README.md")
    s = open(rp, encoding="utf-8").read()
    if "`registry_studies.parquet`" not in s:
        anchor = "| `DATA_DICTIONARY.md` | | Every column, every vocabulary |"
        assert anchor in s, "README Files table anchor row not found"
        rows = [f"| `registry_studies.parquet` | {n_st:,} | **Registry tier**: one row per ENA study of the human shotgun-metagenome universe (all body sites, all ages) — host_human, assay, body_sites/life_stages (config/vocab codes with UBERON anchors), population flags, access, evidence rows, classification_stage, infant-catalog verdict, scope_memberships (config/scope.yaml) — {release_id} |"]
        if os.path.exists(au_p):
            rows.append(f"| `registry_universe_audit.csv` | {_rows(au_p):,} | Registry enumeration audit: per ENA slice the archive count, rows pulled and completeness — {release_id} |")
        rows.append(f"| `REGISTRY_REPORT.md` | | Registry build report (universe counts per slice, host / assay / site / stage facets, scope sizes, deviations) — {release_id} |")
        s = s.replace(anchor, "\n".join(rows) + "\n" + anchor, 1)
        open(rp, "w", encoding="utf-8").write(s)
    extra_desc = {"gut_sample_determinations.parquet": "**Curated scope gut_all (R2026.7)**: one row per sample × field for every human gut shotgun-metagenome sample, all ages — value, normalised value, route (R1 archive attribute / R2 supplementary table / R3 paper prose / R4 abstract), confidence, verbatim evidence quote and source; infant-catalog rows copied verbatim (src_track infant_catalog)",
                  "gut_sample_metadata_wide.parquet": "gut_all scope, one row per sample: age category, age, sex, BMI, country, health condition, antibiotic exposure, subject, timepoint, collection date, detailed location (region / locality / site), latitude / longitude, lifestyle (+ route / confidence per field; 1.12.0 fields from config/packs/gut.yaml), infant-only fields, body-site class, `infant_scope` (== the infant catalog's catalog_scope filter)",
                  "gut_studies.parquet": "gut_all scope studies: registry columns + per-field coverage (cov_<field> for every pack field), age-category / health-condition distributions, sequencing summary (n_runs_total, Gbp per run, instrument models, layouts, Sandpiper share; 1.12.0), curated depth and source",
                  "gut_runs.parquet": "**Catalog runs (R2026.12)**: one row per sequencing run of a gut_all study — accessions, library strategy / source / layout, instrument, read and base counts, first_public, sandpiper_profiled and the catalog sample_key (run-unit samples keyed by run accession)",
                  "gut_sandpiper_sample_summary.parquet": "**Sandpiper community profiles for the whole catalog (R2026.12)**: one row per catalog sample with a SingleM/Sandpiper 2.0.0 profile (GTDB R232) — profiled runs, depth proxy, genus richness, Shannon, top genus, QC flags; the genus- and species-level long tables (gut_sandpiper_sample_genus / _species, 185 + 199 MB) ship as the release asset gut_sandpiper_profiles_<version>.zip",
                  "gut_sandpiper_pca_scores.parquet": "Genus-level CLR-PCA scores (pc1-pc5) of every profiled sample with root coverage >= 2 — the data behind Atlas › PCA",
                  "gut_sandpiper_pca_loadings.parquet": "Genus loadings of the same PCA", "gut_sandpiper_pca_variance.csv": "Explained variance ratio per component", "gut_sandpiper_study_coverage.csv": "Sandpiper join coverage per catalog study",
                  "registry_biosamples.parquet": "**Registry sample tier (R2026.5)**: one row per harvested BioSample of the human_all registry studies outside the curated infant catalog — body site, life stage / age_days, sex, country, collection year normalised to the vocabularies (utility-model pass over distinct attribute pairs, docs/REGISTRY_S2_PILOT.md), raw key/value provenance, disease text unnormalised",
                  "registry_study_papers.parquet": "Registry study × paper links from Europe PMC accession mentions and NCBI BioProject declared publications (deterministic relation classes; docs/REGISTRY_S2_PAPERS.md)",
                  "registry_authors.parquet": "Authors (with ORCID / affiliation where present) of ≤ 5 linked papers per registry study, Europe PMC core records",
                  "registry_bioproject_records.parquet": "NCBI BioProject record per registry study: organisation, submitter, declared publications, dates, data types"}
    readme_rows = []
    for fn in [f for f in schema if f not in ("registry_studies.parquet", "registry_universe_audit.csv", "registry_runs.parquet") and os.path.exists(os.path.join(pkg, f))]:
        if f"`{fn}`" not in s:
            readme_rows.append(f"| `{fn}` | {_rows(os.path.join(pkg, fn)):,} | {extra_desc.get(fn, schema[fn].get('one_row_per', ''))} — {release_id} |")
    if readme_rows:
        anchor = "| `DATA_DICTIONARY.md` | | Every column, every vocabulary |"
        s = open(rp, encoding="utf-8").read()
        s = s.replace(anchor, "\n".join(readme_rows) + "\n" + anchor, 1)
        open(rp, "w", encoding="utf-8").write(s)
    dp = os.path.join(pkg, "DATA_DICTIONARY.md")
    d = open(dp, encoding="utf-8").read()
    if "## Registry tier" in d:  # section exists from an earlier release: append blocks for tables documented since (R2026.5 side tables)
        blocks = []
        for fn in [f for f in schema if f != "registry_runs.parquet" and os.path.exists(os.path.join(pkg, f)) and f"### {f}" not in d]:
            blocks += [f"### {fn} ({_rows(os.path.join(pkg, fn)):,} rows) — one row per {schema[fn].get('one_row_per', '')} — added {release_id}", "",
                       "| column | dtype | meaning |", "|---|---|---|"]
            blocks += [f"| `{c['name']}` | {c.get('type', '')} | {c.get('description', '')} |" for c in schema[fn]["columns"]]
            blocks += [""]
        if blocks:
            marker = "\n## Sandpiper columns (added v1.2.0)"
            d = d.replace(marker, "\n" + "\n".join(blocks) + marker, 1) if marker in d else d.rstrip() + "\n\n" + "\n".join(blocks)
            open(dp, "w", encoding="utf-8").write(d)
        return True
    L = [f"\n## Registry tier ({release_id}, package {package_version})",
         "The registry is the **outer tier** of the catalog (docs/EXPANSION.md): every ENA study with a human shotgun-metagenome signal, any body site,",
         "any age, classified at STUDY level from ENA study/sample/run metadata (no paper reading). Enumeration slices: S1 `library_source=METAGENOMIC` ×",
         "`WGS|WXS`; S2 misfiled `GENOMIC` on verified human-metagenome taxa; S3 `OTHER|Targeted-Capture|WGA` adjudication (per-slice completeness in",
         "`registry_universe_audit.csv`). Classification stages: `deterministic_prior` (infant-universe verdict carried over), `deterministic_rule`,",
         "`sonnet_x2` (two rubric replicates agree), `opus_adjudicated` (replicates disagreed → adjudication), `curator_audit` (study-level host/assay audit, config/registry_overrides.yaml), `owner_decision` (study-level owner override, config/registry_overrides.yaml), `pending`. Vocabularies: the pipeline's",
         "`config/vocab/{body_sites,life_stages,assay,population_flags}.yaml`; scope rules: `config/scope.yaml`. The curated infant catalog is the scope",
         "`infant_gut` inside this registry (`in_infant_catalog` mirrors `universe_studies_all.triage_verdict`). The run-level table `registry_runs.parquet`",
         "(all runs of the universe, 46 ENA fields + `found_by`) is attached to the GitHub Release of the data repository as `registry_runs_v<version>.parquet`",
         "(too large for this package). Bitemporal columns follow the package convention (`release_added`, `release_retired`, `package_added`).", ""]
    tables = [("registry_studies.parquet", f"registry_studies.parquet ({n_st:,} rows)"), ("registry_universe_audit.csv", "registry_universe_audit.csv")]
    tables += [(fn, f"{fn} ({_rows(os.path.join(pkg, fn)):,} rows) — one row per {schema[fn].get('one_row_per', '')}") for fn in schema
               if fn not in dict(tables) and fn != "registry_runs.parquet" and os.path.exists(os.path.join(pkg, fn))]
    for fn, title in tables:
        if fn not in schema or not os.path.exists(os.path.join(pkg, fn)):
            continue
        L += [f"### {title}", "", "| column | dtype | meaning |", "|---|---|---|"]
        L += [f"| `{c['name']}` | {c.get('type', '')} | {c.get('description', '')} |" for c in schema[fn]["columns"]]
        L += [""]
    marker = "\n## Sandpiper columns (added v1.2.0)"
    if marker in d:
        d = d.replace(marker, "\n".join(L) + marker, 1)
    else:
        d = d.rstrip() + "\n" + "\n".join(L)
    open(dp, "w", encoding="utf-8").write(d)
    return True


def prepend_changelog(pkg: str, entry_path: str | None) -> bool:
    if not entry_path or not os.path.exists(entry_path):
        return False
    p = os.path.join(pkg, "CHANGELOG.md")
    entry = open(entry_path, encoding="utf-8").read().rstrip() + "\n\n"
    s = open(p, encoding="utf-8").read()
    head = entry.splitlines()[0].strip()
    if head in s:
        return False
    open(p, "w", encoding="utf-8").write(entry + s)
    return True


def rewrite_readme_intro(pkg: str, package_version: str, intro_template: str | None) -> bool:
    """1.10.0 (all-age site): replace the README text between the H1 and '## Files' with docs/package_readme_intro.md filled from the
    gut_* / registry_* tables; the former infant description is kept under an 'Infant extension (historical description)' heading."""
    if not intro_template or not os.path.exists(intro_template):
        return False
    rp = os.path.join(pkg, "README.md")
    s = open(rp, encoding="utf-8").read()
    if "## Files" not in s or not s.startswith("# "):
        return False
    gs = pd.read_parquet(os.path.join(pkg, "gut_studies.parquet"))
    gw = pd.read_parquet(os.path.join(pkg, "gut_sample_metadata_wide.parquet"), columns=["sample_key", "age_at_collection_days", "sex", "country", "health_condition", "antibiotic_exposure", "infant_scope"])
    gd = pq.ParquetFile(os.path.join(pkg, "gut_sample_determinations.parquet")).metadata.num_rows
    rg = pd.read_parquet(os.path.join(pkg, "registry_studies.parquet"), columns=["study_accession", "n_biosamples_harvested", "release_retired"])
    rg = rg[rg.release_retired.isna()]
    vals = dict(n_registry=len(rg), n_registry_biosamples=int(pd.to_numeric(rg.n_biosamples_harvested, errors="coerce").fillna(0).sum()),
                n_gut_studies=len(gs), n_gut_samples=len(gw), n_gut_determinations=int(gd), package_version=package_version,
                cov_age=float(gw.age_at_collection_days.notna().mean()), cov_sex=float(gw.sex.notna().mean()), cov_country=float(gw.country.notna().mean()),
                cov_hc=float(gw.health_condition.notna().mean()), cov_abx=float(gw.antibiotic_exposure.notna().mean()),
                n_infant_studies=int((gs.curated_source == "infant_catalog").sum()), n_infant_scope=int(gw.infant_scope.fillna(False).astype(bool).sum()))
    intro = open(intro_template, encoding="utf-8").read().format(**vals).strip()
    h1_end = s.index("\n")
    files_at = s.index("## Files")
    old_body = s[h1_end:files_at].strip()
    hist_marker = "### Infant extension (historical description)"
    if hist_marker in old_body:  # already rewritten in an earlier release: keep the historical part as is
        old_body = old_body[old_body.index(hist_marker) + len(hist_marker):].strip()
    old_body = old_body.replace("**Two tiers in one package.**", "").strip()
    new_body = f"\n\n{intro}\n\n{hist_marker}\n\n{old_body}\n\n"
    open(rp, "w", encoding="utf-8").write(s[:h1_end] + new_body + s[files_at:])
    return True



INFANT_QUALIFIER = ("INFANT-CATALOG verdict (historical, 2026-09 infant screen) — NOT membership in the all-age catalog: a study can be "
                    "`excluded` here (e.g. age_adult_only) and still be in `gut_studies`. Catalog membership = presence in `gut_studies.parquet`.")
_INFANT_REWRITES = {
    "| `triage_verdict` | str | include/exclude/uncertain from the triage cascade |": "| `triage_verdict` | str | include/exclude/uncertain from the infant triage cascade. " + INFANT_QUALIFIER + " |",
    "| `catalog_status` | str | included / excluded / human_review |": "| `catalog_status` | str | included / excluded / human_review in the INFANT catalog. " + INFANT_QUALIFIER + " |",
    "| `reason_code` | str | controlled exclusion reason (null for included) |": "| `reason_code` | str | controlled exclusion reason of the INFANT screen (null for included); age_* codes are infant-age reasons, not catalog exclusions |",
    "| `catalog_status`, `triage_verdict`, `decision_stage`, `confidence`, `evidence` | inclusion verdict and its evidence |": "| `catalog_status`, `triage_verdict`, `decision_stage`, `confidence`, `evidence` | INFANT-catalog inclusion verdict and its evidence (historical; not all-age catalog membership — use `gut_studies.parquet`) |",
}


def qualify_infant_verdicts(pkg: str) -> int:
    """R2026.17 (reviewer finding): the infant-era tables universe_studies_all / study_metadata_wide carry `catalog_status`,
    `triage_verdict`, `reason_code` without saying they are INFANT verdicts; 2,357 of the catalog's studies read `excluded` there.
    The columns keep their names (renaming breaks consumers of the bitemporal tables); the dictionary and README now say what they mean."""
    n = 0
    p = os.path.join(pkg, "DATA_DICTIONARY.md")
    if os.path.exists(p):
        t = open(p, encoding="utf-8").read()
        for a, b in _INFANT_REWRITES.items():
            if a in t:
                n += t.count(a); t = t.replace(a, b)
        open(p, "w", encoding="utf-8").write(t)
    r = os.path.join(pkg, "README.md")
    if os.path.exists(r):
        t = open(r, encoding="utf-8").read()
        warn = ("> **Catalog membership:** a study is in the catalog when it is in `gut_studies.parquet` (samples: `gut_sample_metadata_wide.parquet`). "
                "The `catalog_status` / `triage_verdict` / `reason_code` columns of `universe_studies_all.parquet` and `study_metadata_wide.parquet` are the "
                "historical INFANT-catalog screen (389 included) — do not filter the all-age catalog on them.")
        if warn not in t:
            i = t.find("\n## ")
            t = (t[:i] + "\n\n" + warn + "\n" + t[i:]) if i > 0 else (t + "\n\n" + warn + "\n")
            open(r, "w", encoding="utf-8").write(t); n += 1
    return n

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--package", required=True)
    ap.add_argument("--package-version", required=True)
    ap.add_argument("--release-id", required=True)
    ap.add_argument("--build-date", required=True)
    ap.add_argument("--changelog-entry", default=None)
    ap.add_argument("--readme-intro", default=None, help="docs/package_readme_intro.md — rewrites the README intro from the gut_* / registry_* tables (1.10.0)")
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(open(a.config or os.path.join(CONFIG_DIR, "releases.yaml"), encoding="utf-8"))
    counts = build_counts(a.package, a.package_version, a.release_id)
    reg = os.path.join(a.package, cfg["files"]["registry"])
    counts["n_releases"] = _rows(reg) if os.path.exists(reg) else 0
    sd_p = os.path.join(a.package, "sample_determinations.parquet")
    ra = pd.read_parquet(sd_p, columns=["release_added"])["release_added"] if "release_added" in pq.ParquetFile(sd_p).schema_arrow.names else None
    counts["n_release_added_reconstructed"] = int((ra != cfg["fact_tables"][0]["default_release_added"]).sum()) if ra is not None else 0
    counts["release_added_counts"] = ra.value_counts().sort_index().to_dict() if ra is not None else {}
    json.dump(counts, open(os.path.join(a.package, "build_counts.json"), "w"), indent=1, sort_keys=True)
    update_readme(a.package, a.package_version, a.release_id, a.build_date, counts)
    intro_doc = rewrite_readme_intro(a.package, a.package_version, a.readme_intro)
    update_dictionary(a.package, a.package_version, a.release_id, cfg, counts)
    qualify_infant_verdicts(a.package)
    worklist_doc = update_docs_worklist(a.package, a.package_version, a.release_id, counts)
    registry_doc = update_docs_registry(a.package, a.package_version, a.release_id, counts)
    json.dump(counts, open(os.path.join(a.package, "build_counts.json"), "w"), indent=1, sort_keys=True)
    changed = prepend_changelog(a.package, a.changelog_entry)
    sanitised = sanitise_session_tokens(a.package)  # F7
    print(json.dumps({k: counts[k] for k in ("package_version", "release_id", "n_samples", "n_studies", "n_catalog_scope", "n_determinations_current")} | {"changelog_prepended": changed, "worklist_documented": worklist_doc, "registry_documented": registry_doc, "readme_intro_rewritten": intro_doc, "session_tokens_stripped": sanitised}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
