# R2026.36 (package 1.30.0) — evidence for the integrity release

- `readings/` — structured outputs of the Opus 5 readings: I2_audit_1-4.json (+ i2_audit_r36_all.json, i2_audit_input.json),
  Plausibility_1-2.json, Country_conflicts.json, Scope_check_1-3.json. Each verdict names its source and quotes it.
- `plausibility_corrections.csv` — the corrections applied by catalog.scopes.plausibility (built by
  scripts/build_plausibility_corrections_r36.py; confidence >= 0.7 only); `plausibility_corrections_not_applied.csv` — verdicts held back.
- `gut_intervention_studies_shard_zzi2audit_r36.parquet` + `_log.csv` — interventions restored by scripts/apply_i2_audit.py.
- `person_invariant_exclusions.csv` — projects excluded from per-person sex / country propagation (none this release).
- `integrity_checks.json` — the release integrity checks (catalog.release.integrity_checks) on the built package.
Removed values themselves are in the package table `catalog_rejected_values.parquet`; excluded samples are listed in
config/sample_exclusions.csv (date 2026-11-06).
