#!/usr/bin/env python
"""R2026.44: conditions_manifest.json beside the Condition explorer tables — the two counts of label strings that the three
tables cannot carry, counted on the built package.

A label string is a distinct (health_condition, health_condition_detail) pair on current samples. Each string is looked up in
the condition terms table the way the scope build does it (the project's own row, else the row for the pair in every project,
else the code's `*` row); a string that projects read differently is counted once per class it takes.
  labels_without_term  strings that name a condition (condition_name filled) for which no ontology term was found (no EFO and no MONDO id)
  labels_unsettled     strings whose reading is unclear or unsettled (string_class unclear / unsettled)
The ontology versions and the date of the replication run are those of the hand-over the terms come from.
"""
import argparse
import json
import os

import duckdb


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--terms", required=True)
    ap.add_argument("--out", required=True, help="folder of the condition explorer tables")
    ap.add_argument("--efo-version", default="3.96.0")
    ap.add_argument("--mondo-release", default="2026-11-03")
    ap.add_argument("--run-date", default="2026-11-15")
    a = ap.parse_args(argv)
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    w = os.path.join(a.package, "catalog_sample_metadata_wide.parquet")
    con.execute(f"""CREATE TABLE t AS SELECT COALESCE(study_accession,'') study_accession, COALESCE(health_condition,'') hc,
        COALESCE(health_condition_detail,'') hcd,
        CASE WHEN COALESCE(condition_name,'') <> '' AND COALESCE(efo_id,'') = '' AND COALESCE(mondo_id,'') = '' THEN 'named_without_term' ELSE COALESCE(string_class,'') END cls
        FROM read_csv_auto('{a.terms}', header=true, all_varchar=true)""")
    rows = con.execute(f"""
        WITH w AS (SELECT study_accession, COALESCE(health_condition,'') hc, COALESCE(health_condition_detail,'') hcd, count(*) n
                   FROM '{w}' WHERE release_retired IS NULL AND (health_condition IS NOT NULL OR health_condition_detail IS NOT NULL)
                   GROUP BY ALL),
             r AS (SELECT w.hc, w.hcd, w.n, COALESCE(pp.cls, gg.cls, st.cls, 'not_in_table') cls
                   FROM w LEFT JOIN t pp ON pp.study_accession = w.study_accession AND pp.hc = w.hc AND pp.hcd = w.hcd
                          LEFT JOIN t gg ON gg.study_accession = '' AND gg.hc = w.hc AND gg.hcd = w.hcd
                          LEFT JOIN t st ON st.study_accession = '' AND st.hc = w.hc AND st.hcd = '*')
        SELECT cls, count(DISTINCT hc || chr(31) || hcd) strings, sum(n) samples FROM r GROUP BY cls ORDER BY cls""").fetchall()
    by = {c: (int(s), int(n)) for c, s, n in rows}
    man = dict(efo_version=a.efo_version, mondo_release=a.mondo_release, run_date=a.run_date,
               labels_without_term=by.get("named_without_term", (0, 0))[0],
               labels_unsettled=by.get("unclear", (0, 0))[0] + by.get("unsettled", (0, 0))[0],
               label_strings_by_class={c: dict(strings=s, samples=n) for c, (s, n) in by.items()})
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "conditions_manifest.json"), "w") as fh:
        json.dump(man, fh, indent=1, sort_keys=True)
    print(json.dumps(man, sort_keys=True))


if __name__ == "__main__":
    main()
