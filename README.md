# microbiome_repo-pipeline — Infant Gut Shotgun-Metagenome Catalog

Code, configuration, tests and documentation that build and maintain the catalog
(https://microbiomerepo.com/). Data tables live in `microbiome_repo-data`, the generated site in
`microbiome_repo` (docs/DATA_LAYOUT.md). Assembled 2026-09-26 from the release-v11 artifacts (config/inputs.json
records every source artifact id + sha256).

* **docs/RUNBOOK.md** — the monthly cycle: re-sweep → triage → extraction → findings → package → site → publish,
  with commands, budgets (≈ 0.8 M tokens, ≈ 5 h), stop rules and the fresh-session checklist.
* **docs/ARCHITECTURE.md** — data flow (Mermaid), tables/keys, `sample_unit` semantics.
* **docs/SECURITY.md** — PAT scope, rulesets, what the agent may never do.  **docs/DATA_LAYOUT.md** — `~/catalog/`.
* **docs/CHANGELOG.md** — releases v1–v11 reconstructed; **docs/CYCLE_LOG.md** — per-cycle actuals.

```
python bootstrap.py            # env + inputs (~/catalog/data; the artifact store only in the kernel form) + hash check + tests
                               # exit 4 = a required input is missing (tests are NOT run); see RUNBOOK §0
make help                      # resweep · triage · extract · findings · package · site · verify · publish-branch
pip install -e .               # pyproject.toml (package-dir src) — makes `python -m catalog.*` work without PYTHONPATH
python -m pytest tests -q      # hash, validator-sync, model-resolution, determinism (≈ 70 s on a full package) all pass
```

Layout: `src/catalog/{enumeration,harvest,triage,extraction,prompts,legacy}` + `apply_findings.py`, `make_version.py`,
`models.py` (roles → live model ids, never literals) · `config/` (models, inputs, site, budgets, version) ·
`site_generator/gen/` · `skills/` (vendored SKILL.md + kernel.py; `tests/test_validators_sync.py`) · `.github/workflows/`
(verify, deploy-pages, release) · `.github/ISSUE_TEMPLATE/catalog-finding.yml` · `audit/findings/`.

Scripts were written to run flat from one working directory; a header in each file registers the stage
directories on `sys.path`, so both `python -m catalog.extraction.r1_prime` and the leaf-worker convention
(copy the stage files into the cwd and `exec`) work. Exec-style worker scripts (`run_sonnet_confirm.py`,
`run_paper_screen.py`, `r2_full_driver.py`, `mp2_*.py`, `tier_assign.py`, `run_rescue_gate.py`) expect globals
(`SLICE`, `OUT_PREFIX`, …) and are not importable — by design (see REPO_REPORT.md).
