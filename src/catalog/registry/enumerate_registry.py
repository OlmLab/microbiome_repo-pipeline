#!/usr/bin/env python
"""
enumerate_registry.py -- registry UNIVERSE enumeration (scale-up track S1a).

Builds the complete set of ENA studies that could hold human shotgun metagenomes, with
deterministic human-signal flags and study records aggregated for classification.

Slices (NO taxon frame, NO date filter unless --since):
  S1 shotgun_frame_free   library_source="METAGENOMIC" AND library_strategy in (WGS, WXS)
                          -- partitioned by first_public year (quarters from 2023; ENA offset paging is
                          unreliable and whole-year streams >~90 MB were truncated server-side). A
                          run_accession-only index of the whole slice is pulled first and any run
                          missing from the union of partitions (blank first_public) is fetched by
                          accession, so the slice is count-complete by construction.
  S2 misfiled_genomic     library_source="GENOMIC" AND strategy in (WGS, WXS) AND tax_eq(<human-
                          metagenome taxa>) -- community sequencing filed as GENOMIC.
  S3 adjudication         library_strategy in (OTHER, Targeted-Capture, WGA) AND
                          (library_source=METAGENOMIC [all taxa -- the infant universe holds 339
                          METAGENOMIC/OTHER studies under generic 'metagenome'/virome taxa, 3 of them
                          included cohorts] OR (library_source=OTHER AND (host_tax_id=9606 OR
                          tax_eq(9606) OR human-metagenome taxa)) OR (library_source=GENOMIC AND
                          human-metagenome taxa)).  GENOMIC x tax_eq(9606) (1.19M human-genome
                          runs) and GENOMIC x host_tax_id=9606 (70k isolate genomes) are excluded
                          by design -- see ENUMERATION_REGISTRY_REPORT.md.
  S4 rna_human           (R2026.30 scope expansion; not in the default slice set) RNA libraries of human-associated
                          communities: library_source=METATRANSCRIPTOMIC AND (host_tax_id=9606 OR tax_eq(9606) OR
                          human-metagenome taxa), OR library_source=VIRAL RNA AND human-metagenome taxa (VIRAL RNA x host
                          9606 alone is millions of virus genome-surveillance runs), OR library_source=TRANSCRIPTOMIC AND human-metagenome
                          taxa, OR library_strategy=RNA-Seq AND library_source=METAGENOMIC AND (human-metagenome taxa OR
                          host_tax_id=9606). Human tissue RNA-seq (TRANSCRIPTOMIC x Homo sapiens) is excluded by design.

Every human-metagenome taxid is verified against ENA's scientific_name before use
(enumerate_universe_v3.verify_taxa convention). Every slice's ENA count is compared with the
rows pulled (registry_universe_audit.csv); a short slice is retried once.

Outputs (--out DIR):
  registry_runs.parquet              every run (45 PULL_FIELDS + found_by), deduplicated
  registry_study_meta.parquet        ENA study result rows (title, description, center)
  registry_universe_studies.parquet  one row per study: frozen registry_studies universe columns,
                                     sample-field summaries, human-signal rule, infant join,
                                     candidate_class
  registry_biosample_index.parquet   distinct (sample, secondary sample, study, n_runs) of the
                                     human-candidate studies
  registry_universe_audit.csv        per-slice / per-partition ENA count vs rows pulled
  ENUMERATION_REGISTRY_REPORT.md     counts, classes, misses, runtime

No LLM calls. All HTTP through harvest_lib (cache-through).

Usage:
  python enumerate_registry.py --out build/registry [--slices S1 S2 S3] [--since YYYY-MM-DD]
                               [--infant-universe universe_studies_all.parquet] [--dry-run]
                               [--stage pull|aggregate|all]
"""
# --- repo layout shim -------------------------------------------------------------------------
import os as _os, sys as _sys
_here = _os.path.dirname(_os.path.abspath(__file__)) if "__file__" in globals() else _os.getcwd()
for _p in (_here, _os.path.join(_here, "..", "..")):
    _p = _os.path.abspath(_p)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import catalog  # noqa: F401,E402  registers src/catalog/<stage>/ on sys.path
# ---------------------------------------------------------------------------------------------
import argparse
import csv
import io
import json
import re
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import harvest_lib as HL                 # noqa: E402
import scope_constants_v3 as SC3         # noqa: E402
import resweep_universe as RU            # noqa: E402  (PULL_FIELDS, human_signal, regexes)

warnings.filterwarnings("ignore", message="This pattern is interpreted as a regular expression")

CHUNK = 100_000
STUDY_BATCH = 20
ACC_BATCH = 50
PULL_FIELDS = RU.PULL_FIELDS                      # 45 read_run fields (43 lean + host, environment_material)
RUN_SCHEMA = pa.schema([(c, pa.string()) for c in PULL_FIELDS] + [("slice_tag", pa.string())])
RUNS_OUT_COLS = PULL_FIELDS + ["found_by"]
FIRST_YEAR = 2010                                 # ENA: 0 METAGENOMIC WGS/WXS runs public before 2010 (probed 2026-09-27)
QUARTERLY_FROM = 2023                             # years with >150k runs are pulled per quarter (2025/2026 whole-year streams were truncated by ENA at ~93 MB)

# Human-metagenome taxa for slices S2/S3. Names are ENA scientific_name values; ids were resolved
# 2026-09-27 with a read_run probe scientific_name="<name>" -> tax_id and are re-verified at run time.
HUMAN_METAGENOME_TAXA = {
    **SC3.TAXA_PRIMARY,                           # human gut / gut / feces / human / human feces metagenome
    447426: "human oral metagenome",
    539655: "human skin metagenome",
    1131769: "human nasopharyngeal metagenome",
    433733: "human lung metagenome",
    1504969: "human blood metagenome",
    1633571: "human milk metagenome",
    1632839: "human vaginal metagenome",
    1679718: "human saliva metagenome",
    2516875: "human urinary tract metagenome",
    1774142: "human eye metagenome",
    2517771: "human sputum metagenome",
    1712573: "human tracheal metagenome",
    1630596: "human bile metagenome",
    1837932: "human semen metagenome",
    1842734: "human reproductive system metagenome",
    2489051: "human viral metagenome",
}

INFANT_NONHUMAN_CODES = {"host_nonhuman", "host_environmental", "host_synthetic"}
SUMMARY_COLS = ["sample_title", "host_scientific_name", "host_body_site", "isolation_source",
                "environmental_medium", "dev_stage", "age", "host_sex", "tissue", "host", "environment_material",
                "country", "checklist"]
TOPK = 10


# --------------------------------------------------------------------------- queries
def taxon_clause(taxa):
    return "(" + " OR ".join(f"tax_eq({t})" for t in sorted(taxa)) + ")"


def build_slices(since=None):
    """[(slice_id, tag, query, partitions)] -- partitions = [(part_tag, extra_clause)] or None."""
    d = f" AND first_public>={since}" if since else ""
    taxc = taxon_clause(HUMAN_METAGENOME_TAXA)
    s1 = SC3.ena_shotgun_clause() + d
    s2 = SC3.ena_misfiled_genomic_clause() + f" AND {taxc}" + d
    strat3 = '(library_strategy="OTHER" OR library_strategy="Targeted-Capture" OR library_strategy="WGA")'
    s3 = (f'{strat3} AND (library_source="METAGENOMIC" OR '
          f'(library_source="OTHER" AND (host_tax_id=9606 OR tax_eq(9606) OR {taxc})) OR '
          f'(library_source="GENOMIC" AND {taxc}))' + d)
    this_year = int(time.strftime("%Y"))
    parts = []
    for y in range(FIRST_YEAR, this_year + 1):
        if y < QUARTERLY_FROM:
            parts.append((f"y{y}", f"first_public>={y}-01-01 AND first_public<={y}-12-31"))
        else:                                     # >150k runs/year: ENA truncates ~100 MB streams -> quarters
            for qi, (m0, m1, d1) in enumerate([("01", "03", "31"), ("04", "06", "30"), ("07", "09", "30"), ("10", "12", "31")], 1):
                parts.append((f"y{y}q{qi}", f"first_public>={y}-{m0}-01 AND first_public<={y}-{m1}-{d1}"))
    human = f"(host_tax_id=9606 OR tax_eq(9606) OR {taxc})"
    # VIRAL RNA x host 9606 alone is millions of SARS-CoV-2 / influenza genome-surveillance runs: only with community taxa
    s4 = (f'(library_source="METATRANSCRIPTOMIC" AND {human}) OR (library_source="VIRAL RNA" AND {taxc}) OR '
          f'(library_source="TRANSCRIPTOMIC" AND {taxc}) OR '
          f'(library_strategy="RNA-Seq" AND library_source="METAGENOMIC" AND (host_tax_id=9606 OR {taxc}))')
    s4 = f"({s4})" + d
    return [("S1", "shotgun_frame_free", s1, parts),
            ("S2", "misfiled_genomic_human_taxa", s2, None),
            ("S3", "adjudication_other_tc_wga", s3, None),
            ("S4", "rna_human", s4, None)]


def verify_taxa(taxa, log):
    """One read_run probe per taxid; ENA scientific_name must equal our label (enumerate_universe_v3 rule)."""
    bad = []
    for t, label in sorted(taxa.items()):
        tsv = HL.ena_search("read_run", f"tax_eq({t})", ["run_accession", "scientific_name"], limit=1,
                            purpose="registry_verify_taxa")
        df = _read_tsv(tsv)
        name = df["scientific_name"].iloc[0] if len(df) else None
        if (name or "").lower() != label.lower():
            bad.append((t, label, name))
    if bad:
        raise SystemExit(f"taxid/label mismatch in HUMAN_METAGENOME_TAXA: {bad}")
    log(f"verify_taxa: {len(taxa)} human-metagenome taxids match ENA scientific_name")


# --------------------------------------------------------------------------- pull
def _read_tsv(tsv, fields=None):
    if not tsv or not tsv.strip():
        return pd.DataFrame(columns=fields or [])
    df = pd.read_csv(io.StringIO(tsv), sep="\t", dtype=str, keep_default_na=False, quoting=csv.QUOTE_NONE)
    if fields:
        for c in fields:
            if c not in df:
                df[c] = ""
        df = df[fields]
    return df


def _write_chunks(tsv, tag, out_f):
    """Parse a limit=0 TSV body in CHUNK-row pieces straight into a zstd parquet file. Returns rows written."""
    got, writer = 0, None
    try:
        for chunk in pd.read_csv(io.StringIO(tsv), sep="\t", dtype=str, keep_default_na=False,
                                 quoting=csv.QUOTE_NONE, chunksize=CHUNK, low_memory=False):
            for c in PULL_FIELDS:
                if c not in chunk:
                    chunk[c] = ""
            chunk = chunk[PULL_FIELDS].copy()
            chunk["slice_tag"] = tag
            tbl = pa.Table.from_pandas(chunk, schema=RUN_SCHEMA, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(out_f, RUN_SCHEMA, compression="zstd")
            writer.write_table(tbl)
            got += len(chunk)
    finally:
        if writer:
            writer.close()
    return got


def stream_part(query, tag, out_f, log, force=False):
    """One limit=0 stream -> parquet. Returns (ena_count, rows, seconds)."""
    expected = HL.ena_count("read_run", query, purpose=f"registry_count:{tag}") or 0
    t0 = time.time()
    if expected == 0:
        log(f"  [{tag}] expected 0 -- skipped")
        return 0, 0, 0.0
    tsv = HL.fetch_text(_ena_url("read_run", query, PULL_FIELDS), purpose=f"registry_pull:{tag}", force=force)
    got = _write_chunks(tsv, tag, out_f) if tsv else 0
    del tsv
    status = "OK" if got == expected else f"MISMATCH (expected {expected:,})"
    log(f"  [{tag}] rows={got:,} {status} {time.time()-t0:.0f}s")
    return expected, got, time.time() - t0


def _ena_url(result, query, fields, limit=0):
    import urllib.parse
    return (f"{HL.ENA}/search?result={result}&query={urllib.parse.quote(query)}"
            f"&fields={','.join(fields)}&limit={limit}&format=tsv")


def pull_index(query, tag, log):
    """run_accession-only index of a whole slice (the completeness reference)."""
    t0 = time.time()
    tsv = HL.ena_search("read_run", query, ["run_accession"], limit=0, purpose=f"registry_index:{tag}")
    accs = set(_read_tsv(tsv, ["run_accession"]).run_accession) if tsv else set()
    accs.discard("")
    log(f"  [{tag}] index: {len(accs):,} run accessions {time.time()-t0:.0f}s")
    return accs


def fetch_by_accession(accs, tag, out_f, log):
    """Runs missing from every partition (blank first_public etc.) fetched ACC_BATCH per call."""
    accs = sorted(accs)
    rows = []
    for i in range(0, len(accs), ACC_BATCH):
        b = accs[i:i + ACC_BATCH]
        q = "(" + " OR ".join(f'run_accession="{a}"' for a in b) + ")"
        rows.append(_read_tsv(HL.ena_search("read_run", q, PULL_FIELDS, limit=0,
                                            purpose=f"registry_fill:{tag}"), PULL_FIELDS))
    df = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=PULL_FIELDS)
    df = df[df.run_accession.isin(set(accs))].drop_duplicates("run_accession")
    if len(df):
        df = df.copy()
        df["slice_tag"] = tag
        pq.write_table(pa.Table.from_pandas(df, schema=RUN_SCHEMA, preserve_index=False), out_f, compression="zstd")
    log(f"  [{tag}] accession fill: {len(df):,}/{len(accs):,} runs")
    return len(df)


def pull_slice(slice_id, tag, query, parts, out_dir, log):
    """Pull one slice (partitioned or whole), verify completeness against the index, return audit rows + files."""
    audit, files = [], []
    total_expected = HL.ena_count("read_run", query, purpose=f"registry_count:{slice_id}") or 0
    log(f"[{slice_id} {tag}] ENA count {total_expected:,}")
    index = pull_index(query, slice_id, log)
    seen = set()
    for ptag, clause in (parts or [(None, None)]):
        full_tag = f"{slice_id}:{ptag}" if ptag else slice_id
        q = f"{query} AND {clause}" if clause else query
        f = out_dir / f"runs_{slice_id}_{ptag or 'all'}.parquet"
        exp, got, secs = stream_part(q, full_tag, f, log)
        if exp and got != exp:                    # retry once with force (bypass a truncated cached body)
            log(f"  [{full_tag}] retrying (force)")
            exp, got, secs2 = stream_part(q, full_tag, f, log, force=True)
            secs += secs2
        if got:
            files.append(f)
            seen |= set(pq.read_table(f, columns=["run_accession"]).column(0).to_pylist())
        audit.append({"slice": slice_id, "partition": ptag or "all", "tag": tag, "query": q, "ena_count": exp,
                      "rows_pulled": got, "complete": exp == got, "seconds": round(secs, 1)})
    missing = index - seen
    if missing:
        f = out_dir / f"runs_{slice_id}_fill.parquet"
        got = fetch_by_accession(missing, f"{slice_id}:fill", f, log)
        if got:
            files.append(f)
        audit.append({"slice": slice_id, "partition": "fill_missing_from_index", "tag": tag,
                      "query": f"run_accession IN (<{len(missing)} accessions missing from partitions>)",
                      "ena_count": len(missing), "rows_pulled": got, "complete": got == len(missing), "seconds": 0})
        seen |= set(missing) if got == len(missing) else set()
    extra = seen - index
    audit.append({"slice": slice_id, "partition": "TOTAL", "tag": tag, "query": query, "ena_count": total_expected,
                  "rows_pulled": len(seen), "complete": len(seen) == total_expected == len(index),
                  "seconds": round(sum(a["seconds"] for a in audit), 1)})
    log(f"[{slice_id}] union {len(seen):,} runs vs ENA count {total_expected:,} / index {len(index):,}"
        f" (missing after fill {len(index - seen)}, extra {len(extra)})")
    return audit, files


def combine_runs(files, out_f, log):
    """Union of slice parquet files -> registry_runs.parquet (deduplicated, found_by = ';'-joined slice ids)."""
    tbls = [pq.read_table(f) for f in files]
    runs = pa.concat_tables(tbls).to_pandas()
    del tbls
    n_raw = len(runs)
    n_blank_study = int((runs.study_accession == "").sum())
    runs = runs[runs.study_accession != ""]
    runs["slice_id"] = runs.slice_tag.str.split(":").str[0]
    found_by = runs.groupby("run_accession").slice_id.agg(lambda s: ";".join(sorted(set(s))))
    runs = runs.drop_duplicates("run_accession").copy()
    runs["found_by"] = runs.run_accession.map(found_by)
    runs = runs[RUNS_OUT_COLS]
    runs.to_parquet(out_f, index=False, compression="zstd")
    log(f"registry_runs: {n_raw:,} rows -> {len(runs):,} unique runs / {runs.study_accession.nunique():,} studies "
        f"({n_blank_study} rows with empty study_accession dropped)")
    return runs


# --------------------------------------------------------------------------- study metadata
STUDY_FIELDS = ["study_accession", "secondary_study_accession", "study_title", "description", "center_name",
                "first_public"]


def fetch_study_meta(accs, log):
    """ENA result=study rows in batches of STUDY_BATCH accessions (fetch_many keeps the per-host rate limit)."""
    accs = sorted(set(a for a in accs if a))
    urls = []
    for i in range(0, len(accs), STUDY_BATCH):
        q = " OR ".join(f'study_accession="{a}"' for a in accs[i:i + STUDY_BATCH])
        urls.append(_ena_url("study", q, STUDY_FIELDS))
    t0 = time.time()
    res = HL.fetch_many(urls, purpose="registry_study_meta", workers=4, expect="bytes")
    rows = []
    for u in urls:
        r = res.get(u) or {}
        body = r.get("body")
        if r.get("ok") and body:
            rows.append(_read_tsv(body.decode("utf-8", "replace"), STUDY_FIELDS))
    meta = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=STUDY_FIELDS)
    meta = meta.drop_duplicates("study_accession")
    n_hit = int(meta.study_accession.isin(set(accs)).sum())   # ENA also returns rows for accessions not asked for
    log(f"study metadata: {n_hit:,}/{len(accs):,} requested studies have an ENA study record "
        f"({len(accs) - n_hit} without -> title from read_run; {len(meta) - n_hit} unrequested rows returned) "
        f"[{len(urls)} calls, {time.time()-t0:.0f}s]")
    return meta


# --------------------------------------------------------------------------- aggregation
def _topk_json(runs, col, k=TOPK):
    """study_accession -> json {value: count} of the top-k non-empty values of `col`."""
    s = runs[["study_accession", col]]
    s = s[s[col] != ""]
    if s.empty:
        return pd.Series(dtype=object)
    vc = s.groupby(["study_accession", col], sort=False).size().reset_index(name="n")
    vc = vc.sort_values(["study_accession", "n", col], ascending=[True, False, True])
    vc = vc.groupby("study_accession", sort=False).head(k)
    return vc.groupby("study_accession").apply(lambda g: json.dumps(dict(zip(g[col].str.slice(0, 120), g.n.astype(int)))),
                                                include_groups=False)


def _joined_counts(runs, col, k=5, sep=";"):
    """'value(count)' top-k joined string (scientific_names_top convention)."""
    s = runs[["study_accession", col]]
    s = s[s[col] != ""]
    if s.empty:
        return pd.Series(dtype=object)
    vc = s.groupby(["study_accession", col], sort=False).size().reset_index(name="n")
    vc = vc.sort_values(["study_accession", "n", col], ascending=[True, False, True]).groupby("study_accession").head(k)
    vc["lab"] = vc[col].str.slice(0, 80) + "(" + vc.n.astype(str) + ")"
    return vc.groupby("study_accession").lab.agg(sep.join)


def _distinct_joined(runs, col, sep=";", maxv=20):
    s = runs[["study_accession", col]]
    s = s[s[col] != ""].drop_duplicates()
    return s.groupby("study_accession")[col].agg(lambda v: sep.join(sorted(v)[:maxv]))


def _stem(name):
    """library_name pattern stem: digits -> '#', keep the first 12 chars."""
    return re.sub(r"\d+", "#", name or "")[:12]


def _num(s):
    return pd.to_numeric(s.replace("", np.nan), errors="coerce")


def aggregate_studies(runs, meta, log):
    """One row per study_accession: frozen universe columns + sample-field summaries + depth stats."""
    t0 = time.time()
    r = runs.copy()
    for c in PULL_FIELDS:
        r[c] = r[c].fillna("").astype(str)
    for c in ["host_sex", "tissue"]:            # not in PULL_FIELDS; summarised only when present
        if c not in r:
            r[c] = ""
    r["is_host_9606"] = r.host_tax_id == "9606"
    r["is_nonhuman_host"] = (r.host_tax_id != "") & (r.host_tax_id != "9606")
    r["base_count_n"] = _num(r.base_count)
    r["read_count_n"] = _num(r.read_count)
    r["nominal_length_n"] = _num(r.nominal_length)
    r["read_len_proxy"] = (r.base_count_n / r.read_count_n).where(r.read_count_n > 0)
    r["blank_tax"] = r.tax_id == ""
    g = r.groupby("study_accession", sort=True)
    st = g.agg(secondary_study_accession=("secondary_study_accession", "first"),
               study_title_run=("study_title", "first"),
               center_name_run=("center_name", "first"),
               first_public_min=("first_public", "min"),
               first_public_max=("first_public", "max"),
               n_runs=("run_accession", "size"),
               n_samples=("secondary_sample_accession", "nunique"),
               n_biosamples=("sample_accession", "nunique"),
               n_runs_host_9606=("is_host_9606", "sum"),
               n_runs_nonhuman_host=("is_nonhuman_host", "sum"),
               n_runs_blank_tax_id=("blank_tax", "sum"),
               median_base_count=("base_count_n", "median"),
               median_read_count=("read_count_n", "median"),
               median_nominal_length=("nominal_length_n", "median"),
               median_read_len_proxy=("read_len_proxy", "median"),
               n_target_gene=("target_gene", lambda s: int((s != "").sum())),
               found_by=("found_by", lambda s: ";".join(sorted(set(x for v in s for x in v.split(";")))))).reset_index()
    st["library_strategies"] = st.study_accession.map(_distinct_joined(r, "library_strategy"))
    st["library_sources"] = st.study_accession.map(_distinct_joined(r, "library_source"))
    st["library_selections"] = st.study_accession.map(_distinct_joined(r, "library_selection"))
    st["library_layouts"] = st.study_accession.map(_distinct_joined(r, "library_layout"))
    st["instrument_platforms"] = st.study_accession.map(_distinct_joined(r, "instrument_platform"))
    st["instrument_models_top"] = st.study_accession.map(_joined_counts(r, "instrument_model", 5))
    st["scientific_names_top"] = st.study_accession.map(_joined_counts(r, "scientific_name", 5))
    st["tax_ids"] = st.study_accession.map(_distinct_joined(r, "tax_id", maxv=50))
    st["host_tax_ids"] = st.study_accession.map(_distinct_joined(r, "host_tax_id", maxv=50))
    st["target_genes"] = st.study_accession.map(_distinct_joined(r, "target_gene", maxv=10))
    r["library_name_stem"] = r.library_name.map(_stem)
    st["library_name_stems_top"] = st.study_accession.map(_joined_counts(r, "library_name_stem", 5))
    for c in SUMMARY_COLS:
        if c in r:
            st[f"top_{c}"] = st.study_accession.map(_topk_json(r, c))
    for c in st.columns:
        if st[c].dtype == object or c.startswith("top_") or c.endswith("_top") or c in (
                "library_strategies", "library_sources", "library_selections", "library_layouts",
                "instrument_platforms", "tax_ids", "host_tax_ids", "target_genes"):
            st[c] = st[c].astype(object).fillna("")
    # study record (ENA result=study) preferred; read_run title/center as fallback
    m = meta.rename(columns={"study_title": "study_title_ena", "center_name": "center_name_ena",
                             "secondary_study_accession": "secondary_study_accession_ena"})
    st = st.merge(m[["study_accession", "study_title_ena", "description", "center_name_ena",
                     "secondary_study_accession_ena"]], how="left", on="study_accession")
    st["has_ena_study_record"] = st.study_title_ena.notna()
    st["study_title"] = st.study_title_ena.fillna("").where(st.study_title_ena.fillna("") != "", st.study_title_run)
    st["center_name"] = st.center_name_ena.fillna("").where(st.center_name_ena.fillna("") != "", st.center_name_run)
    st["secondary_study_accession"] = st.secondary_study_accession.where(
        st.secondary_study_accession != "", st.secondary_study_accession_ena.fillna(""))
    st["description"] = st.description.fillna("")
    st["description_short"] = st.description.str.replace(r"\s+", " ", regex=True).str.slice(0, 300)
    st = st.drop(columns=["study_title_ena", "center_name_ena", "secondary_study_accession_ena", "study_title_run",
                          "center_name_run"])
    log(f"aggregated {len(st):,} studies from {len(r):,} runs in {time.time()-t0:.0f}s")
    return st


# --------------------------------------------------------------------------- rules
def taxon_name_rule(runs):
    """Extra human-signal rule: scientific_name starts with 'human ' OR host_tax_id 9606 on >=1 run."""
    r = runs[["study_accession", "scientific_name", "host_tax_id"]].copy()
    r["hit"] = r.scientific_name.fillna("").str.lower().str.startswith("human ") | (r.host_tax_id == "9606")
    return r.groupby("study_accession").hit.any()


def apply_rules(st, runs, meta, log):
    """resweep_universe.human_signal (verbatim, rules A/B/C + ambiguous) + taxon-name rule -> columns on st."""
    t0 = time.time()
    sm = meta[["study_accession", "study_title", "description"]] if len(meta) else \
        pd.DataFrame(columns=["study_accession", "study_title", "description"])
    sig = RU.human_signal(runs, sm)
    keep = ["study_accession", "human_signal", "signal_rule", "ambiguous", "human_specific_score", "site_score",
            "animal_score", "site_terms", "hspec_terms", "animal_terms", "taxon_name_human", "taxon_nonhuman_species",
            "n_runs_human_host", "n_runs_hspec", "n_runs_site", "n_runs_animal", "human_signal_reason"]
    st = st.merge(sig[keep], how="left", on="study_accession")
    st["human_signal"] = st.human_signal.fillna(False).astype(bool)
    st["ambiguous"] = st.ambiguous.fillna(False).astype(bool)
    st["human_signal_rule"] = st.signal_rule.fillna("").map(
        {"A_host_human": "A", "B_no_animal_terms": "B", "C_human_specific_outweighs_animal": "C"}).fillna("none")
    st["taxon_name_rule"] = st.study_accession.map(taxon_name_rule(runs)).fillna(False).astype(bool)
    st["human_signal_any"] = st.human_signal | st.taxon_name_rule
    st = st.drop(columns=["signal_rule"])
    log(f"human-signal rule on {len(st):,} studies in {time.time()-t0:.0f}s: "
        f"signal {int(st.human_signal.sum()):,} (A {int((st.human_signal_rule=='A').sum()):,} "
        f"B {int((st.human_signal_rule=='B').sum()):,} C {int((st.human_signal_rule=='C').sum()):,}), "
        f"ambiguous {int(st.ambiguous.sum()):,}, taxon-name rule only {int((st.taxon_name_rule & ~st.human_signal).sum()):,}")
    return st


def join_infant_universe(st, infant):
    """universe_studies_all.parquet columns -> in_infant_catalog / infant_* ; not_screened when absent."""
    cols = ["study_accession", "triage_verdict", "reason_code", "body_site_call", "universe_slice"]
    inf = infant[cols].drop_duplicates("study_accession").rename(columns={
        "triage_verdict": "in_infant_catalog", "reason_code": "infant_reason_code",
        "body_site_call": "infant_body_site_call", "universe_slice": "infant_universe_slice"})
    st = st.merge(inf, how="left", on="study_accession")
    st["infant_screened"] = st.in_infant_catalog.notna()
    st["in_infant_catalog"] = st.in_infant_catalog.fillna("not_screened")
    for c in ["infant_reason_code", "infant_body_site_call", "infant_universe_slice"]:
        st[c] = st[c].fillna("")
    return st


def candidate_class(st):
    screened = st.infant_screened
    prior_nonhuman = screened & st.infant_reason_code.isin(INFANT_NONHUMAN_CODES)
    prior_human = screened & ~prior_nonhuman
    sig_new = ~screened & st.human_signal_any
    amb_new = ~screened & ~st.human_signal_any & st.ambiguous
    return pd.Series(np.select([prior_nonhuman, prior_human, sig_new, amb_new],
                               ["prior_nonhuman", "prior_human", "signal_human_new", "ambiguous_new"],
                               default="nosignal_new"), index=st.index)


HUMAN_CANDIDATE_CLASSES = {"prior_human", "signal_human_new", "ambiguous_new"}

UNIVERSE_COL_ORDER = [
    "study_accession", "secondary_study_accession", "study_title", "description_short", "center_name",
    "first_public_min", "first_public_max", "n_runs", "n_samples", "n_biosamples", "library_strategies",
    "library_sources", "library_selections", "library_layouts", "instrument_platforms", "instrument_models_top",
    "scientific_names_top", "tax_ids", "host_tax_ids", "n_runs_host_9606", "n_runs_nonhuman_host",
    "n_runs_blank_tax_id", "target_genes", "n_target_gene", "median_base_count", "median_read_count",
    "median_nominal_length", "median_read_len_proxy", "library_name_stems_top",
    "human_signal", "human_signal_rule", "ambiguous", "taxon_name_rule", "human_signal_any",
    "human_specific_score", "site_score", "animal_score", "site_terms", "hspec_terms", "animal_terms",
    "taxon_name_human", "taxon_nonhuman_species", "n_runs_human_host", "n_runs_hspec", "n_runs_site",
    "n_runs_animal", "human_signal_reason",
    "in_infant_catalog", "infant_reason_code", "infant_body_site_call", "infant_universe_slice", "infant_screened",
    "candidate_class", "universe_slice", "found_by", "has_ena_study_record", "description",
]


def build_universe(runs, meta, infant, log):
    st = aggregate_studies(runs, meta, log)
    st = apply_rules(st, runs, meta, log)
    st = join_infant_universe(st, infant)
    st["candidate_class"] = candidate_class(st)
    st["universe_slice"] = "registry_enumeration_" + time.strftime("%Y-%m") + ":" + st.found_by
    for c in ["n_runs", "n_samples", "n_biosamples", "n_runs_host_9606", "n_runs_nonhuman_host",
              "n_runs_blank_tax_id", "n_target_gene", "n_runs_human_host", "n_runs_hspec", "n_runs_site",
              "n_runs_animal", "human_specific_score", "site_score", "animal_score"]:
        st[c] = st[c].fillna(0).astype("int64")
    rest = [c for c in st.columns if c not in UNIVERSE_COL_ORDER and not c.startswith("top_")]
    tops = sorted(c for c in st.columns if c.startswith("top_"))
    return st[UNIVERSE_COL_ORDER + tops + rest]


def biosample_index(runs, st):
    cand = set(st.loc[st.candidate_class.isin(HUMAN_CANDIDATE_CLASSES), "study_accession"])
    r = runs[runs.study_accession.isin(cand)]
    bi = (r.groupby(["sample_accession", "secondary_sample_accession", "study_accession"], sort=True)
           .run_accession.size().reset_index(name="n_runs"))
    return bi


# --------------------------------------------------------------------------- report
def _md(df, cols, n=None):
    d = df[cols].head(n) if n else df[cols]
    d = d.copy()
    for c in d.columns:
        if d[c].dtype == object:
            d[c] = d[c].astype(str).str.replace("|", "/").str.slice(0, 90)
    return ("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
            + "\n".join("| " + " | ".join(str(v) for v in r) + " |" for r in d.itertuples(index=False)))


def write_report(out_dir, audit, st, runs, bi, infant, misses, t_start, lines, since=None):
    aud = pd.DataFrame(audit)
    cls = st.candidate_class.value_counts()
    by_slice = st.groupby("found_by").candidate_class.value_counts().unstack(fill_value=0)
    by_slice["studies"] = by_slice.sum(axis=1)
    by_slice = by_slice.reset_index()
    runs_per_class = st.groupby("candidate_class").n_runs.sum()
    infant_set = set(infant.study_accession)
    refound = infant_set & set(st.study_accession)
    rep = [f"# Registry universe enumeration (S1a) -- ENUMERATION_REGISTRY_REPORT",
           f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}; runtime {time.time()-t_start:.0f}s; "
           f"deterministic (no LLM calls); all HTTP cache-through via harvest_lib."
           + (f" Date window: first_public>={since}." if since else " No date window, no taxon frame."),
           "", "## Slices and completeness (registry_universe_audit.csv)",
           _md(aud, ["slice", "partition", "ena_count", "rows_pulled", "complete", "seconds"]),
           "", "S4 (library_source=METATRANSCRIPTOMIC) is out of scope and was not pulled. S3 pulls every "
           "METAGENOMIC x OTHER/Targeted-Capture/WGA run (no taxon restriction), OTHER-source runs only with a human host/"
           "taxon signal, and GENOMIC-source runs only on the 21 human-metagenome taxa; GENOMIC x tax_eq(9606) (human "
           "genome sequencing, 1.19M runs on 2026-09-27) and GENOMIC x host_tax_id=9606 (bacterial isolate genomes, 70k "
           "runs) are excluded by design.",
           "", "## Runs",
           f"* registry_runs.parquet: **{len(runs):,}** unique runs / **{runs.study_accession.nunique():,}** studies; "
           f"columns = 45 read_run PULL_FIELDS + found_by.",
           f"* runs found by slice: " + ", ".join(f"{k} {v:,}" for k, v in runs.found_by.value_counts().items()),
           f"* runs with blank tax_id: {int((runs.tax_id == '').sum()):,}; studies with blank tax_id on every run: "
           f"{int((st.n_runs_blank_tax_id == st.n_runs).sum()):,}; on >=1 run: {int((st.n_runs_blank_tax_id > 0).sum()):,}",
           f"* studies without an ENA study record (title from read_run): {int((~st.has_ena_study_record).sum()):,}",
           "", "## Human-signal rule (resweep_universe.human_signal verbatim + taxon-name rule)",
           f"* human_signal True: **{int(st.human_signal.sum()):,}** -- rule A {int((st.human_signal_rule=='A').sum()):,}, "
           f"B {int((st.human_signal_rule=='B').sum()):,}, C {int((st.human_signal_rule=='C').sum()):,}",
           f"* taxon-name rule (scientific_name 'human ...' or host_tax_id 9606 on >=1 run): {int(st.taxon_name_rule.sum()):,} "
           f"(adds {int((st.taxon_name_rule & ~st.human_signal).sum()):,} not caught by A/B/C)",
           f"* ambiguous (human terms, animal/env score >= human score): {int(st.ambiguous.sum()):,}",
           "", "## Candidate classes",
           _md(pd.DataFrame({"candidate_class": cls.index, "studies": cls.values,
                             "runs": [int(runs_per_class.get(k, 0)) for k in cls.index]}),
               ["candidate_class", "studies", "runs"]),
           "", "### by slice (found_by)", _md(by_slice, list(by_slice.columns)),
           "", "## Infant universe re-found",
           f"* infant universe (universe_studies_all.parquet): {len(infant_set):,} studies; re-found in the registry "
           f"universe: **{len(refound):,}**; missed: **{len(misses):,}**",
           (_md(misses, ["study_accession", "triage_verdict", "reason_code", "universe_slice", "study_title", "n_runs"], 100)
            + (f"\n\n({len(misses) - 100} more in registry_infant_universe_misses.csv)" if len(misses) > 100 else ""))
           if len(misses) else "(none)",
           "", "## BioSample index",
           f"* registry_biosample_index.parquet: {len(bi):,} (sample, secondary sample, study) rows across "
           f"{bi.study_accession.nunique():,} human-candidate studies (prior_human + signal_human_new + ambiguous_new)",
           "", "## Files",
           "* registry_runs.parquet -- every run of the registry universe (working_data artifact)",
           "* registry_universe_studies.parquet -- one row per study (frozen universe columns, summaries, rule, infant join, candidate_class)",
           "* registry_biosample_index.parquet -- distinct BioSamples of the human-candidate studies",
           "* registry_universe_audit.csv -- per-slice / per-partition ENA count vs rows pulled",
           "* registry_study_meta.parquet -- ENA study records (title, description, center_name)",
           "", "## Log", "```", *lines, "```"]
    (out_dir / "ENUMERATION_REGISTRY_REPORT.md").write_text("\n".join(rep) + "\n")


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--slices", nargs="*", default=["S1", "S2", "S3"], help="subset of S1 S2 S3 S4 (S4 = human RNA libraries, opt-in)")
    ap.add_argument("--since", default=None, help="optional YYYY-MM-DD; first_public>=SINCE on every slice")
    ap.add_argument("--infant-universe", default=None, help="universe_studies_all.parquet of the infant catalog")
    ap.add_argument("--stage", choices=["pull", "aggregate", "all"], default="all",
                    help="pull = slices + registry_runs only; aggregate = reuse OUT/registry_runs.parquet")
    ap.add_argument("--dry-run", action="store_true", help="print slice queries and ENA counts, pull nothing")
    a = ap.parse_args(argv)
    if a.since and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.since):
        ap.error("--since must be YYYY-MM-DD")
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = []

    def log(s):
        print(s, flush=True)
        lines.append(s)

    t_start = time.time()
    slices = [s for s in build_slices(a.since) if s[0] in set(a.slices)]
    log(f"# registry enumeration  ({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}) slices={a.slices}")
    if a.dry_run:
        for sid, tag, q, parts in slices:
            log(f"  [{sid} {tag}] count={HL.ena_count('read_run', q, purpose='registry_dryrun')}  query={q}")
        return 0

    runs_path = out_dir / "registry_runs.parquet"
    audit = []
    if a.stage in ("pull", "all"):
        verify_taxa(HUMAN_METAGENOME_TAXA, log)
        files = []
        for sid, tag, q, parts in slices:
            au, fs = pull_slice(sid, tag, q, parts, out_dir, log)
            audit += au
            files += fs
        pd.DataFrame(audit).to_csv(out_dir / "registry_universe_audit.csv", index=False)
        if not files:
            log("no runs pulled; nothing to do")
            return 0
        runs = combine_runs(files, runs_path, log)
        for f in files:
            f.unlink(missing_ok=True)
        if a.stage == "pull":
            (out_dir / "ENUMERATION_REGISTRY_REPORT.md").write_text("\n".join(lines) + "\n")
            return 0
    else:
        runs = pd.read_parquet(runs_path)
        for c in RUNS_OUT_COLS:
            if c in runs:
                runs[c] = runs[c].fillna("").astype(str)
        aud_f = out_dir / "registry_universe_audit.csv"
        audit = pd.read_csv(aud_f).to_dict("records") if aud_f.exists() else []
        log(f"loaded {len(runs):,} runs from {runs_path}")

    meta_path = out_dir / "registry_study_meta.parquet"
    need = set(runs.study_accession.unique())
    if meta_path.exists():
        meta = pd.read_parquet(meta_path)
        log(f"loaded {len(meta):,} study records from {meta_path}")
        todo = sorted(need - set(meta.study_accession))
        if todo:                                  # incremental: only studies not yet fetched
            log(f"fetching study records for {len(todo):,} new studies")
            meta = pd.concat([meta, fetch_study_meta(todo, log)], ignore_index=True).drop_duplicates("study_accession")
            meta.to_parquet(meta_path, index=False)
    else:
        meta = fetch_study_meta(need, log)
        meta.to_parquet(meta_path, index=False)

    infant = (pd.read_parquet(a.infant_universe) if a.infant_universe
              else pd.DataFrame(columns=["study_accession", "triage_verdict", "reason_code", "body_site_call",
                                         "universe_slice", "study_title", "n_runs"]))
    st = build_universe(runs, meta, infant, log)
    st.to_parquet(out_dir / "registry_universe_studies.parquet", index=False)
    bi = biosample_index(runs, st)
    bi.to_parquet(out_dir / "registry_biosample_index.parquet", index=False)
    misses = infant[~infant.study_accession.isin(set(st.study_accession))]
    for c in ["study_title", "n_runs", "reason_code", "universe_slice", "triage_verdict"]:
        if c not in misses:
            misses[c] = ""
    misses.to_csv(out_dir / "registry_infant_universe_misses.csv", index=False)
    log(f"candidate classes: {st.candidate_class.value_counts().to_dict()}")
    log(f"infant universe re-found {len(infant) - len(misses):,}/{len(infant):,}; biosample index {len(bi):,} rows")
    write_report(out_dir, audit, st, runs, bi, infant, misses, t_start, lines, a.since)
    log(f"done in {time.time()-t_start:.0f}s -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
