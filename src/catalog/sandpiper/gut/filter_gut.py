"""One-pass streaming filter of the Sandpiper bulk file to the catalog's sandpiper_profiled gut runs.
Mirrors src/catalog/sandpiper/filter_bulk.py (same semantics: keep rows whose `sample` is a matched run;
rename sample->run, filled_coverage->coverage_filled) but reads with pyarrow.csv for throughput and writes
chunked zstd parquet parts to work/sp/raw_parts/. Writes work/sp/filter_log.json."""
import sys, gzip, json, time, os
import pandas as pd, pyarrow as pa, pyarrow.csv as pcsv, pyarrow.parquet as pq, pyarrow.compute as pc
SRC = sys.argv[1]; RUNS = sys.argv[2]; OUTDIR = "work/sp/raw_parts"; os.makedirs(OUTDIR, exist_ok=True)
runs = pd.read_parquet(RUNS); matched = runs.loc[runs.sandpiper_profiled, "run_accession"].unique()
matched_set = set(matched); matched_arr = pa.array(sorted(matched_set), pa.string())
t0 = time.time(); n_rows = 0; n_kept = 0; runs_kept = set(); n_blocks = 0; part = 0
schema = pa.schema([("run", pa.string()), ("coverage_filled", pa.float64()), ("taxonomy", pa.string())])
ro = pcsv.ReadOptions(block_size=64 << 20); po = pcsv.ParseOptions(delimiter="\t", quote_char=False)
co = pcsv.ConvertOptions(column_types={"sample": pa.string(), "filled_coverage": pa.float64(), "taxonomy": pa.string()})
buf = []; buf_rows = 0
def flush():
    global buf, buf_rows, part
    if not buf: return
    tbl = pa.concat_tables(buf); pq.write_table(tbl, f"{OUTDIR}/part-{part:04d}.parquet", compression="zstd"); part += 1
    buf = []; buf_rows = 0
with gzip.open(SRC, "rb") as fh:
    reader = pcsv.open_csv(fh, read_options=ro, parse_options=po, convert_options=co)
    for batch in reader:
        n_blocks += 1; n_rows += batch.num_rows
        mask = pc.is_in(batch.column("sample"), value_set=matched_arr)
        keep = batch.filter(mask)
        if keep.num_rows:
            n_kept += keep.num_rows
            runs_kept.update(pc.unique(keep.column("sample")).to_pylist())
            tbl = pa.Table.from_batches([keep]).rename_columns(["run", "coverage_filled", "taxonomy"]).cast(schema)
            buf.append(tbl); buf_rows += tbl.num_rows
            if buf_rows >= 5_000_000: flush()
        if n_blocks % 40 == 0:
            print(f"block {n_blocks}: rows {n_rows:,} kept {n_kept:,} runs_kept {len(runs_kept):,} {time.time()-t0:.0f}s", flush=True)
flush()
missing = sorted(matched_set - runs_kept)
log = {"source": SRC, "rows_scanned": n_rows, "rows_kept": n_kept, "runs_kept": len(runs_kept), "matched_runs_expected": len(matched_set),
       "n_missing": len(missing), "matched_runs_missing_from_bulk_sample": missing[:50], "parts": part, "seconds": round(time.time()-t0, 1)}
json.dump(log, open("work/sp/filter_log.json", "w"), indent=1)
pd.Series(missing, name="run_accession").to_frame().to_parquet("work/sp/missing_runs.parquet", index=False)
print(json.dumps({k: v for k, v in log.items() if k != "matched_runs_missing_from_bulk_sample"}), flush=True)
