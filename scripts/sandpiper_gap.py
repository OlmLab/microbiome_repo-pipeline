#!/usr/bin/env python
"""sandpiper_gap.py — why a current catalog sample has no Sandpiper community profile (R2026.39).

The catalog does no profiling of its own: a sample has a profile when Sandpiper released one for at least one of its DNA
runs. This script compares every DNA and RNA run of the current samples with the two run lists of a Sandpiper release —
the profiled runs (`sandpiper<X>.per_acc_summary.csv.gz`) and the runs Sandpiper selected from the archive
(`sandpiper<X>.kingfisher_metadata.tsv.gz`) — and with Sandpiper's published selection rule (Woodcroft et al., methods,
"Generation of Sandpiper dataset": library source METAGENOMIC or organism under the metagenome taxonomy; platform ILLUMINA;
public; more than 1 Gbp, or more than 0.1 Gbp when the library selection is RANDOM). The rule's size part is reproduced
exactly by the 2.1.0 list (no listed run under 0.1 Gbp; every catalog run of 0.1-1 Gbp with another selection is absent).

Per run, the first reason that applies:
  profiled                 a profile exists
  listed_no_profile        on Sandpiper's run list, no profile released (failed or pending there)
  rna                      RNA library
  after_snapshot           public after the newest run on Sandpiper's list
  no_public_date           the catalog holds no public date for the run
  platform                 not Illumina
  no_base_count            the catalog holds no base count for the run
  size_rule                at most 0.1 Gbp, or at most 1 Gbp with a library selection other than RANDOM
  source_rule              library source not METAGENOMIC and organism not a metagenome
  unexplained              meets the published rule and predates the snapshot, yet is not on Sandpiper's list
Per sample, the reason of the run that came closest to a profile (order: listed_no_profile, unexplained, after_snapshot,
size_rule, platform, source_rule, no_base_count, no_public_date, rna); a sample without any run row is `no_runs`, and a
sample whose only profiled runs are ones the catalog does not use for community profiles is `profile_not_used`.

usage: python scripts/sandpiper_gap.py --package build/package --per-acc <per_acc_summary.csv.gz> --run-list <kingfisher_metadata.tsv.gz>
           --version 2.1.0 [--registry-runs data/inputs/registry/registry_runs.parquet] [--out-dir build/package]
Writes OUT/catalog_sandpiper_unprofiled.parquet (one row per current sample without a profile), OUT/catalog_sandpiper_gap_by_cause.csv
and OUT/sandpiper_runs_not_profiled.csv.gz (runs that are listed without a profile or unexplained: the list for the Sandpiper
maintainers). Out of core (DuckDB, 4 GB limit): no table is loaded whole.
"""
import argparse
import os
import sys

import duckdb

ORDER = ["profiled", "listed_no_profile", "unexplained", "after_snapshot", "size_rule", "platform", "source_rule", "no_base_count",
         "no_public_date", "rna"]
LABEL = {"listed_no_profile": "On Sandpiper's run list, no profile released",
         "unexplained": "Meets Sandpiper's selection rule, not on its run list",
         "after_snapshot": "Public after Sandpiper's snapshot",
         "size_rule": "Too small for Sandpiper's selection rule",
         "platform": "Not sequenced on Illumina",
         "source_rule": "Library source not METAGENOMIC and organism not a metagenome",
         "no_base_count": "No base count held for the run",
         "no_public_date": "No public date held for the run",
         "rna": "RNA only",
         "profile_not_used": "Sandpiper profiled a run the catalog does not count as a DNA metagenome run",
         "no_runs": "No run row held for the sample"}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True); ap.add_argument("--per-acc", required=True); ap.add_argument("--run-list", required=True)
    ap.add_argument("--version", required=True); ap.add_argument("--registry-runs", default=None); ap.add_argument("--out-dir", default=None)
    ap.add_argument("--memory-limit", default="4GB")
    a = ap.parse_args(argv)
    out = a.out_dir or a.package
    pk = a.package.rstrip("/") + "/"
    con = duckdb.connect(); con.execute(f"SET memory_limit='{a.memory_limit}'"); con.execute("SET threads=4")
    con.execute(f"""CREATE TABLE kf AS SELECT run, TRY_CAST(published AS TIMESTAMP) AS published
        FROM read_csv('{a.run_list}', delim='\t', header=true, all_varchar=true, quote='', escape='', ignore_errors=true,
                      null_padding=true, strict_mode=false)""")
    con.execute(f"CREATE TABLE pa AS SELECT sample AS run FROM read_csv('{a.per_acc}', header=true, all_varchar=true)")
    snap = con.execute("SELECT CAST(MAX(published) AS DATE) FROM kf WHERE published < now()").fetchone()[0]
    con.execute(f"CREATE TABLE cur AS SELECT sample_key, study_accession FROM read_parquet('{pk}catalog_sample_metadata_wide.parquet') WHERE release_retired IS NULL")
    con.execute(f"CREATE TABLE prof AS SELECT sample_key FROM read_parquet('{pk}catalog_sandpiper_sample_summary.parquet') WHERE n_runs_profiled > 0")
    tax = "CAST(NULL AS VARCHAR)"
    if a.registry_runs and os.path.exists(a.registry_runs):
        con.execute(f"CREATE TABLE rtax AS SELECT run_accession, ANY_VALUE(CAST(scientific_name AS VARCHAR)) AS taxon FROM read_parquet('{a.registry_runs}') GROUP BY 1")
        tax = "t.taxon"
    else:
        con.execute("CREATE TABLE rtax (run_accession VARCHAR, taxon VARCHAR)")
    con.execute(f"""CREATE TABLE rr AS
      WITH r AS (SELECT r.run_accession, r.sample_key, cur.study_accession, r.library_strategy, r.library_source, r.library_selection,
                        r.instrument_platform, TRY_CAST(r.base_count AS DOUBLE) / 1e9 AS gbp, TRY_CAST(r.first_public AS DATE) AS first_public,
                        r.nucleic_acid, {tax} AS taxon
                 FROM read_parquet('{pk}catalog_runs.parquet') r JOIN cur USING (sample_key) LEFT JOIN rtax t USING (run_accession))
      SELECT r.*, CASE
          WHEN run_accession IN (SELECT run FROM pa) THEN 'profiled'
          WHEN run_accession IN (SELECT run FROM kf) THEN 'listed_no_profile'
          WHEN nucleic_acid = 'rna' THEN 'rna'
          WHEN first_public > DATE '{snap}' THEN 'after_snapshot'
          WHEN first_public IS NULL THEN 'no_public_date'
          WHEN upper(instrument_platform) <> 'ILLUMINA' THEN 'platform'
          WHEN gbp IS NULL THEN 'no_base_count'
          WHEN gbp <= 0.1 OR (gbp <= 1 AND upper(coalesce(library_selection, '')) <> 'RANDOM') THEN 'size_rule'
          WHEN upper(coalesce(library_source, '')) <> 'METAGENOMIC' AND (taxon IS NULL OR taxon NOT ILIKE '%metagenome%') THEN 'source_rule'
          ELSE 'unexplained' END AS reason
      FROM r""")
    rank = "CASE reason " + " ".join(f"WHEN '{k}' THEN {i}" for i, k in enumerate(ORDER)) + " ELSE 99 END"
    con.execute(f"""CREATE TABLE unp AS
      WITH u AS (SELECT * FROM rr WHERE sample_key NOT IN (SELECT sample_key FROM prof)),
           best AS (SELECT sample_key, arg_min(reason, {rank}) AS reason, COUNT(*) AS n_runs, SUM(gbp) AS gbp_total,
                           SUM(CASE WHEN nucleic_acid = 'dna' THEN 1 ELSE 0 END) AS n_dna_runs FROM u GROUP BY 1)
      SELECT cur.sample_key, cur.study_accession, CASE WHEN best.reason = 'profiled' THEN 'profile_not_used' ELSE coalesce(best.reason, 'no_runs') END AS reason, coalesce(best.n_runs, 0) AS n_runs,
             coalesce(best.n_dna_runs, 0) AS n_dna_runs, best.gbp_total,
             '{a.version}' AS sandpiper_version, CAST(DATE '{snap}' AS VARCHAR) AS sandpiper_snapshot_date
      FROM cur LEFT JOIN best USING (sample_key) WHERE cur.sample_key NOT IN (SELECT sample_key FROM prof)
      ORDER BY cur.study_accession, cur.sample_key""")
    lab = "CASE reason " + " ".join(f"WHEN '{k}' THEN '{v.replace(chr(39), chr(39) * 2)}'" for k, v in LABEL.items()) + " END"
    con.execute(f"ALTER TABLE unp ADD COLUMN reason_label VARCHAR"); con.execute(f"UPDATE unp SET reason_label = {lab}")
    con.execute(f"COPY unp TO '{out}/catalog_sandpiper_unprofiled.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.execute(f"""COPY (SELECT reason, ANY_VALUE(reason_label) AS reason_label, COUNT(*) AS samples, COUNT(DISTINCT study_accession) AS projects,
                          ROUND(SUM(gbp_total) / 1000, 2) AS total_Tbp, ROUND(MEDIAN(gbp_total), 3) AS median_Gbp
                   FROM unp GROUP BY 1 ORDER BY samples DESC) TO '{out}/catalog_sandpiper_gap_by_cause.csv' (HEADER)""")
    con.execute(f"""COPY (SELECT run_accession, study_accession, sample_key, reason, ROUND(gbp, 3) AS gbp, first_public, library_strategy,
                          library_source, library_selection FROM rr WHERE reason IN ('listed_no_profile', 'unexplained')
                   ORDER BY study_accession, run_accession) TO '{out}/sandpiper_runs_not_profiled.csv.gz' (HEADER, COMPRESSION GZIP)""")
    n = con.execute("SELECT COUNT(*) FROM unp").fetchone()[0]
    print(f"sandpiper gap: {n} current samples without a profile; snapshot {snap}; reasons "
          + str(con.execute("SELECT reason, COUNT(*) FROM unp GROUP BY 1 ORDER BY 2 DESC").fetchall()), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
