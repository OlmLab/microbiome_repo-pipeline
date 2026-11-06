"""Build gut-wide Sandpiper tables from work/sp/raw_parts/*.parquet (run, coverage_filled, taxonomy).
Follows src/catalog/sandpiper/build_sandpiper_tables.py: (1) unfilled = filled - sum(filled children);
(2) sample aggregation = SUM of filled coverage over the sample's profiled runs (== root-coverage-weighted mean of
per-run relative abundance); (3) normalise by sample root coverage with unassigned_at_<rank> remainder."""
import duckdb, pandas as pd, numpy as np, json, time, os, sys
t0 = time.time(); OUT = "work/out"; os.makedirs(OUT, exist_ok=True)
TAXDB, TAXVER = "GTDB", os.environ.get("SANDPIPER_TAXONOMY_VERSION", "R232")
SPVER, ZEN = os.environ.get("SANDPIPER_VERSION", "2.0.0"), os.environ.get("SANDPIPER_ZENODO_RECORD", "20419175")
con = duckdb.connect(); con.execute("PRAGMA threads=8; PRAGMA memory_limit='28GB'; SET preserve_insertion_order=false;")
con.execute("""CREATE TABLE raw AS SELECT run, coverage_filled, taxonomy,
    length(taxonomy) - length(replace(taxonomy, ';', '')) AS rank_i,
    CASE WHEN taxonomy = 'Root' THEN NULL ELSE regexp_replace(taxonomy, ';\\s*[^;]+$', '') END AS parent
  FROM read_parquet('work/sp/raw_parts/*.parquet')""")
n_raw = con.execute("SELECT count(*), count(DISTINCT run) FROM raw").fetchone(); print("raw rows/runs", n_raw, flush=True)
# (1) unfilled coverage per node
con.execute("""CREATE TABLE runlevel AS
  SELECT r.run, r.taxonomy, r.rank_i, r.coverage_filled,
         round(r.coverage_filled - coalesce(c.child_sum, 0), 4) AS coverage_unfilled
  FROM raw r LEFT JOIN (SELECT run, parent, sum(coverage_filled) AS child_sum FROM raw WHERE parent IS NOT NULL GROUP BY run, parent) c
    ON c.run = r.run AND c.parent = r.taxonomy""")
neg = con.execute("SELECT count(*) FROM runlevel WHERE coverage_unfilled < -0.02").fetchone()[0]
rank_dist = con.execute("SELECT rank_i, count(*) FROM raw GROUP BY 1 ORDER BY 1").fetchall()
print("negative unfilled rows:", neg, "rank dist", rank_dist, time.time()-t0, flush=True)
con.execute(f"""COPY (SELECT run, taxonomy, rank_i, coverage_filled, coverage_unfilled,
   '{TAXDB}' AS taxonomy_db, '{TAXVER}' AS taxonomy_version, '{SPVER}' AS sandpiper_version, '{ZEN}' AS zenodo_record
   FROM runlevel) TO '{OUT}/gut_sandpiper_profiles_runs.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)""")
print("runlevel written", time.time()-t0, flush=True)
# per-run genus relabund (for run-level checks and the per-run root table)
con.execute("CREATE TABLE runroot AS SELECT run, coverage_filled AS root_coverage FROM raw WHERE rank_i = 0")
# (2) map runs -> sample_key
con.execute("CREATE TABLE m AS SELECT run_accession AS run, sample_key, study_accession FROM read_parquet('work/sp/gut_runs_profiled.parquet')")
con.execute("""CREATE TABLE agg AS SELECT m.sample_key, r.taxonomy, r.rank_i, sum(r.coverage_filled) AS coverage_filled
  FROM raw r JOIN m ON m.run = r.run WHERE r.rank_i IN (0, 6, 7) GROUP BY m.sample_key, r.taxonomy, r.rank_i""")
con.execute("CREATE TABLE root AS SELECT sample_key, coverage_filled AS root_coverage FROM agg WHERE rank_i = 0")
n_root = con.execute("SELECT count(*), sum(CASE WHEN root_coverage>0 THEN 1 ELSE 0 END) FROM root").fetchone(); print("samples with root row / root>0:", n_root, flush=True)
# (3) genus and species long tables with unassigned remainder
def long_table(rank_i, rank_name, min_ra):
    con.execute(f"""CREATE OR REPLACE TABLE lt_{rank_name} AS
      SELECT a.sample_key, regexp_replace(a.taxonomy, '^.*;\\s*', '') AS {rank_name}, a.taxonomy AS lineage,
             a.coverage_filled AS coverage, a.coverage_filled / r.root_coverage AS relabund
      FROM agg a JOIN root r USING (sample_key) WHERE a.rank_i = {rank_i} AND r.root_coverage > 0
      UNION ALL
      SELECT r.sample_key, 'unassigned_at_{rank_name}', 'unassigned_at_{rank_name}', greatest(r.root_coverage - coalesce(s.s, 0), 0),
             greatest(r.root_coverage - coalesce(s.s, 0), 0) / r.root_coverage
      FROM root r LEFT JOIN (SELECT sample_key, sum(coverage_filled) s FROM agg WHERE rank_i = {rank_i} GROUP BY 1) s USING (sample_key)
      WHERE r.root_coverage > 0 AND greatest(r.root_coverage - coalesce(s.s, 0), 0) > 1e-9""")
    chk = con.execute(f"SELECT min(s), max(s), count(*) FROM (SELECT sample_key, sum(relabund) s FROM lt_{rank_name} GROUP BY 1)").fetchone()
    con.execute(f"""COPY (SELECT sample_key, {rank_name}, lineage, round(relabund, 6) AS relabund, round(coverage, 4) AS coverage,
        '{TAXDB}' AS taxonomy_db, '{TAXVER}' AS taxonomy_version, '{SPVER}' AS sandpiper_version, '{ZEN}' AS zenodo_record
        FROM lt_{rank_name} WHERE relabund >= {min_ra} OR {rank_name} LIKE 'unassigned_at_%' ORDER BY sample_key, relabund DESC)
        TO '{OUT}/gut_sandpiper_sample_{rank_name}.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    n = con.execute(f"SELECT count(*), count(DISTINCT sample_key), count(DISTINCT {rank_name}) FROM lt_{rank_name} WHERE relabund >= {min_ra} OR {rank_name} LIKE 'unassigned_at_%'").fetchone()
    print(rank_name, "sum-check (min,max,n)", chk, "written rows/samples/taxa", n, time.time()-t0, flush=True)
    return {"sum_check": chk, "written": n}
log = {"raw": n_raw, "negative_unfilled_rows": neg, "rank_dist": rank_dist, "root": n_root}
log["genus"] = long_table(6, "genus", 0.0)
log["species"] = long_table(7, "species", 0.001)
# summary per sample
con.execute("""CREATE TABLE summ AS
  WITH g AS (SELECT sample_key, genus, relabund FROM lt_genus WHERE genus NOT LIKE 'unassigned_at_%'),
  gs AS (SELECT sample_key, count(*) FILTER (WHERE relabund >= 0.001) AS richness_genus,
                -sum(relabund * ln(relabund)) FILTER (WHERE relabund > 0) AS shannon_genus_assigned FROM g GROUP BY 1),
  sh AS (SELECT sample_key, -sum(relabund * ln(relabund)) FILTER (WHERE relabund > 0) AS shannon_genus FROM lt_genus GROUP BY 1),
  tp AS (SELECT sample_key, genus AS top_genus, relabund AS top_genus_relabund FROM (SELECT *, row_number() OVER (PARTITION BY sample_key ORDER BY relabund DESC, genus) rk FROM g) WHERE rk = 1),
  ug AS (SELECT sample_key, relabund AS unassigned_genus_relabund FROM lt_genus WHERE genus = 'unassigned_at_genus'),
  us AS (SELECT sample_key, relabund AS unassigned_species_relabund FROM lt_species WHERE species = 'unassigned_at_species')
  SELECT r.sample_key, r.root_coverage AS root_coverage_sum, gs.richness_genus, sh.shannon_genus, gs.shannon_genus_assigned, tp.top_genus, tp.top_genus_relabund,
         coalesce(ug.unassigned_genus_relabund, 0) AS unassigned_genus_relabund, coalesce(us.unassigned_species_relabund, 0) AS unassigned_species_relabund
  FROM root r LEFT JOIN gs USING (sample_key) LEFT JOIN sh USING (sample_key) LEFT JOIN tp USING (sample_key) LEFT JOIN ug USING (sample_key) LEFT JOIN us USING (sample_key)""")
con.execute(f"COPY summ TO '{OUT}/_summ_core.parquet' (FORMAT PARQUET)")
con.execute(f"COPY runroot TO '{OUT}/_run_root.parquet' (FORMAT PARQUET)")
# genus matrix input: sample x genus relabund for root >= 2 (assigned genera only), long
con.execute(f"COPY (SELECT l.sample_key, l.genus, l.relabund FROM lt_genus l JOIN root r USING (sample_key) WHERE r.root_coverage >= 2 AND l.genus NOT LIKE 'unassigned_at_%') TO '{OUT}/_genus_long_ge2.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
log["summ_rows"] = con.execute("SELECT count(*) FROM summ").fetchone()[0]; log["seconds"] = round(time.time()-t0, 1)
json.dump(log, open("work/sp/build_log.json", "w"), indent=1, default=str); print(json.dumps(log, default=str), flush=True)
