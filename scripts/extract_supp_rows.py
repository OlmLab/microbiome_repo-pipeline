#!/usr/bin/env python
"""extract_supp_rows.py — turn the joinable supplementary tables found by join_ladder_audit.py into per-sample
attribute rows (route R2). Deterministic; no model calls.

For each (project, table) the audit reports the column that joins to the project's archive keys and the rung it joins
at. This script re-reads that table, finds the header row, joins every data row to a sample_accession through the same
rung, and melts the remaining columns into long rows:

    sample_key | study_accession | attr_key_norm (column header) | attr_value | source (PMC file!sheet#column)

The output has the same shape as the harvested BioSample attributes, so the stage-5 pair mapper can map it with the
same prompt and the same cached answers.

Guards: a row must join to exactly one sample; the 'number' rung is accepted only when the project's numbers are
unique per sample (the audit already enforced this); a column is skipped when its header is empty, duplicated, or the
join column itself; values longer than 200 characters are dropped (free text, not a per-sample variable).

usage: python scripts/extract_supp_rows.py --ladder ladder.csv --pmcids pmcids.csv --out rows.parquet [--min-share 0.5]
"""
import argparse, glob, io, os, re, sys, warnings, zipfile

import pandas as pd
import requests

warnings.filterwarnings("ignore")
CACHE = os.path.expanduser("~/catalog/cache")
MAX_LEN = 200


def n1(s):
    return re.sub(r"[^0-9a-z]", "", str(s).lower())


def n2(s):
    s = str(s).strip()
    s = re.split(r"\.", s)[0]
    s = re.sub(r"(?i)([_-]S\d+)?([_-]L\d{3})?([_-]R[12])?([_-]00\d)?(\.f(ast)?q(\.gz)?)?$", "", s)
    return n1(s)


def n3(s):
    m = re.findall(r"\d+", str(s))
    return m[-1].lstrip("0") if m else ""


RUNGS = {"exact": lambda s: str(s).strip(), "casefold": n1, "stem": n2, "number": n3}


def _docx_tables(data):
    """Tables in a Word supplement. python-docx gives cell text directly; merged cells repeat their text, which is
    what we want for a header row that spans columns."""
    import docx  # python-docx

    out = []
    with io.BytesIO(data) as fh:
        d = docx.Document(fh)
    for i, t in enumerate(d.tables):
        rows = []
        for r in t.rows[:20000]:
            rows.append([c.text.strip() for c in r.cells])
        if len(rows) < 3:
            continue
        w = max(len(r) for r in rows)
        out.append((f"#docx_table_{i + 1}", pd.DataFrame([r + [""] * (w - len(r)) for r in rows], dtype=str)))
    return out


def _pdf_tables(data, max_pages=40):
    """Tables in a PDF supplement, via pdfplumber's ruling-line and whitespace strategies. A PDF table is the least
    reliable source here, so only tables with at least 3 rows and 2 columns are returned and the caller still applies
    the one-row-per-sample and named-column guards."""
    import pdfplumber

    out = []
    with io.BytesIO(data) as fh, pdfplumber.open(fh) as pdf:
        for pi, page in enumerate(pdf.pages[:max_pages]):
            try:
                tabs = page.extract_tables()   # ruling-line strategy only: the whitespace strategy turns prose into
            except Exception:                  # character-wide fake columns, which no downstream guard can undo
                continue
            for ti, t in enumerate(tabs or []):
                rows = [[("" if c is None else str(c).strip().replace("\n", " ")) for c in r] for r in t[:20000]]
                if len(rows) < 3 or max((len(r) for r in rows), default=0) < 2:
                    continue
                # a real table has a consistent width; a mis-detected block does not
                widths = [len(r) for r in rows]
                if max(set(widths), key=widths.count) != max(widths):
                    continue
                w = max(len(r) for r in rows)
                out.append((f"#pdf_p{pi + 1}_t{ti + 1}",
                            pd.DataFrame([r + [""] * (w - len(r)) for r in rows], dtype=str)))
    return out


def read_any(name, data):
    ext = name.lower().rsplit(".", 1)[-1]
    base = os.path.basename(name)
    try:
        if ext in ("xlsx", "xlsm", "xls"):
            sh = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=str, header=None, nrows=20000)
            return [(f"{base}!{k}", v) for k, v in sh.items()]
        if ext in ("csv", "tsv", "txt"):
            return [(base, pd.read_csv(io.BytesIO(data), sep=None, engine="python", dtype=str,
                                       header=None, nrows=20000))]
        if ext == "docx":
            return [(f"{base}{k}", v) for k, v in _docx_tables(data)]
        if ext == "pdf":
            return [(f"{base}{k}", v) for k, v in _pdf_tables(data)]
    except Exception:
        return []
    return []


def tables_of(pmc):
    """Every table in this paper's cached SUPPLEMENTARY files. The article's own files (PMCnnnn.1.pdf / .xml / .txt /
    .json) are skipped: they are the paper, not a per-sample table, and reading them is slow and yields nothing."""
    out = []
    for p in glob.glob(f"{CACHE}/pmc_oa/{pmc}.*/*") + glob.glob(f"{CACHE}/pmc_oa/{pmc}/*"):
        if os.path.basename(p).split(".")[0] == str(pmc):
            continue
        try:
            out += read_any(p, open(p, "rb").read())
        except Exception:
            pass
    z = f"{CACHE}/supp/{pmc}.zip"
    if os.path.exists(z):
        try:
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    out += read_any(n, zf.read(n))
        except Exception:
            pass
    return out


def ena_keys(acc):
    r = requests.get("https://www.ebi.ac.uk/ena/portal/api/filereport",
                     params=dict(accession=acc, result="read_run", format="tsv", limit=0,
                                 fields="run_accession,sample_accession,secondary_sample_accession,sample_alias,"
                                        "sample_title,library_name,experiment_alias"), timeout=120)
    return pd.read_csv(io.StringIO(r.text), sep="\t", dtype=str)


def header_row(df, ci, fn, keys):
    """The data rows are the ones whose join-column value matches an archive key; the header is the row above them."""
    col = df.iloc[:, ci].astype(str)
    hit = [i for i, v in enumerate(col) if fn(v) in keys]
    if not hit:
        return None, []
    first = min(hit)
    return (first - 1 if first > 0 else None), hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder", required=True)
    ap.add_argument("--pmcids", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-share", type=float, default=0.5)
    ap.add_argument("--projects", default=None, help="optional subset file")
    a = ap.parse_args()

    lad = pd.read_csv(a.ladder, dtype=str)
    lad["share"] = pd.to_numeric(lad["share"], errors="coerce")
    lad = lad[(lad["rung"].isin(RUNGS)) & (lad["share"] >= a.min_share)]
    if a.projects:
        want = {l.strip() for l in open(a.projects) if l.strip()}
        lad = lad[lad["project"].isin(want)]
    rows, stats = [], {"projects": 0, "tables": 0, "rows": 0, "ena_error": 0}

    for proj, pg in lad.groupby("project"):
        try:
            e = ena_keys(proj)
        except Exception:
            stats["ena_error"] += 1
            continue
        tabs = {}
        for pmc in {t.split("/")[0] for t in pg["table"]}:
            for tname, df in tables_of(pmc):
                tabs[f"{pmc}/{tname}"] = df
        stats["projects"] += 1
        for tname, tg in pg.groupby("table"):
            df = tabs.get(tname)
            if df is None:
                continue
            best = tg.sort_values("share", ascending=False).iloc[0]
            kt, rung = best["key_type"], best["rung"]
            if kt not in e.columns:
                continue
            fn = RUNGS[rung]
            k = pd.DataFrame({"key": e[kt], "s": e["sample_accession"]}).dropna()
            k["n"] = k["key"].map(fn)
            k = k[k["n"] != ""].drop_duplicates(["n", "s"])
            dup = k.groupby("n")["s"].nunique()
            keymap = k[k["n"].isin(dup[dup == 1].index)].drop_duplicates("n").set_index("n")["s"].to_dict()
            # which column of this table carries the key
            ci = None
            for c in range(min(df.shape[1], 60)):
                vals = df.iloc[:, c].dropna().astype(str)
                if len(vals) >= 5 and sum(1 for v in vals if fn(v) in keymap) >= 5:
                    ci = c
                    break
            if ci is None:
                continue
            hrow, hit = header_row(df, ci, fn, set(keymap))
            head = df.iloc[hrow] if hrow is not None else pd.Series([f"col{j}" for j in range(df.shape[1])])
            body = df.iloc[min(hit):]
            names, seen = [], set()
            for j, h in enumerate(head):
                nm = re.sub(r"\s+", "_", str(h).strip().lower())[:60] if pd.notna(h) else ""
                nm = re.sub(r"[^0-9a-z_%()/+.-]", "_", nm).strip("_")
                if not nm or nm in seen or nm.startswith("unnamed") or nm.startswith("nan"):
                    nm = ""
                seen.add(nm)
                names.append(nm)
            key_col = body.iloc[:, ci].astype(str).map(fn).map(keymap)
            # a per-sample metadata table has ONE row per sample: a matrix joined by coincident numbers has many
            kc = key_col.dropna()
            if kc.empty or kc.duplicated().mean() > 0.2:
                stats["dropped_not_one_row_per_sample"] = stats.get("dropped_not_one_row_per_sample", 0) + 1
                continue
            # and its columns are named, not numbered
            if sum(1 for nm in names if nm and re.search(r"[a-z]{2}", nm)) < 3:
                stats["dropped_unnamed_columns"] = stats.get("dropped_unnamed_columns", 0) + 1
                continue
            stats["tables"] += 1
            for j, nm in enumerate(names):
                if not nm or j == ci:
                    continue
                v = body.iloc[:, j]
                part = pd.DataFrame({"sample_key": key_col.values, "attr_value": v.astype(str).str.strip().values})
                part = part.dropna(subset=["sample_key"])
                part = part[(part["attr_value"] != "") & (part["attr_value"].str.lower() != "nan") &
                            (part["attr_value"].str.len() <= MAX_LEN)]
                if part.empty:
                    continue
                part["study_accession"], part["attr_key_norm"] = proj, nm
                part["source"] = f"supp:{tname}#{nm}"
                rows.append(part)
        print(proj, stats["tables"], flush=True)

    out = pd.concat(rows, ignore_index=True).drop_duplicates(["sample_key", "attr_key_norm", "attr_value"]) if rows \
        else pd.DataFrame(columns=["sample_key", "study_accession", "attr_key_norm", "attr_value", "source"])
    out.to_parquet(a.out, index=False)
    stats["rows"] = int(len(out))
    stats["samples"] = int(out["sample_key"].nunique()) if len(out) else 0
    print(stats)


if __name__ == "__main__":
    main()
