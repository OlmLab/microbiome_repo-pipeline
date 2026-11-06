"""Convert Sandpiper 'unfilled SingleM condensed' coverage (2.0.1+: coverage assigned exactly at a node; nodes with nothing
assigned, Root included, are omitted) to the filled form the table builder expects (2.0.0 shipped it): filled(node) = sum of
unfilled coverage over the node's subtree, for every prefix of every lineage, Root included.
usage (cwd = work dir): python unfilled_to_filled.py   — reads work/sp/raw_parts_unfilled/*.parquet, writes work/sp/raw_parts/."""
import duckdb, json, os, time
t0 = time.time(); src = "work/sp/raw_parts_unfilled"; dst = "work/sp/raw_parts"; os.makedirs(dst, exist_ok=True)
con = duckdb.connect(); con.execute("PRAGMA threads=8; PRAGMA memory_limit='28GB'; SET preserve_insertion_order=false;")
con.execute(f"CREATE TABLE u AS SELECT run, coverage_filled AS cov, string_split(taxonomy, '; ') AS parts FROM read_parquet('{src}/*.parquet')")
n_u = con.execute("SELECT count(*), count(DISTINCT run), sum(cov) FROM u").fetchone()
con.execute(f"""COPY (SELECT run, round(sum(cov), 4) AS coverage_filled, array_to_string(list_slice(parts, 1, k), '; ') AS taxonomy
  FROM u, range(1, 10) t(k) WHERE k <= len(parts) GROUP BY run, taxonomy)
  TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, PER_THREAD_OUTPUT TRUE)""")
n_f = con.execute(f"SELECT count(*), count(DISTINCT run), sum(coverage_filled) FILTER (WHERE taxonomy = 'Root') FROM read_parquet('{dst}/*.parquet')").fetchone()
log = {"unfilled_rows_runs_cov": n_u, "filled_rows_runs_rootcov": n_f, "seconds": round(time.time() - t0, 1)}
json.dump(log, open("work/sp/unfilled_to_filled_log.json", "w"), indent=1, default=str); print(json.dumps(log, default=str), flush=True)
