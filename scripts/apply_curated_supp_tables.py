#!/usr/bin/env python
"""apply_curated_supp_tables.py — config/curated_supp_tables.yaml -> data/inputs/gut/curated_supp_corrections.parquet (R2026.28).
Each listed field is retired for the whole project, then recoded per sample from the named supplementary table (route R2).
usage: python scripts/apply_curated_supp_tables.py [--config config/curated_supp_tables.yaml] [--out data/inputs/gut/curated_supp_corrections.parquet]
"""
import argparse, io, os, re
import pandas as pd, requests, yaml

CACHE = os.path.expanduser("~/catalog/cache/pmc_oa")
COLS = ["sample_key", "study_accession", "field_name", "action", "old_value", "new_value", "evidence_source", "evidence_locator", "evidence_quote", "confidence", "rationale", "determined_by"]


def source_path(src):
    pmc, fn = src.split("/", 1)
    p = os.path.join(CACHE, pmc + ".1", fn)
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        r = requests.get(f"https://pmc-oa-opendata.s3.amazonaws.com/{pmc}.1/{fn}", timeout=120); r.raise_for_status()
        open(p, "wb").write(r.content)
    return p


def ena_runs(acc):
    r = requests.get("https://www.ebi.ac.uk/ena/portal/api/filereport", params=dict(accession=acc, result="read_run", fields="run_accession,sample_accession,sample_alias", format="tsv", limit=0), timeout=120)
    return pd.read_csv(io.StringIO(r.text), sep="\t", dtype=str)


def stem(s):
    return str(s).split(".")[0].upper().replace("-", "").replace("_", "").strip()


def transform(v, t):
    v = "" if pd.isna(v) else str(v).strip()
    if t is None:
        return v or None
    if t == "number":
        try: return str(float(v)) if v else None
        except ValueError: return None
    if t == "day_label":
        m = re.match(r"(?i)day\s*(\d+)", v); return str(float(m.group(1))) if m else None
    if t == "preterm_lt37":
        try: return "preterm" if float(v) < 37 else "term"
        except ValueError: return None
    if isinstance(t, dict) and "preterm_or" in t:
        try: return "preterm_nicu" if float(v) < 37 else t["preterm_or"]
        except ValueError: return None
    if isinstance(t, dict) and "map" in t:
        return t["map"].get(v)
    if isinstance(t, dict) and "regex" in t:
        m = re.search(t["regex"], v); return m.group(1) if m else None
    raise ValueError(f"unknown transform {t}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--config", default="config/curated_supp_tables.yaml")
    ap.add_argument("--out", default="data/inputs/gut/curated_supp_corrections.parquet"); ap.add_argument("--wide", default="build/package/gut_sample_metadata_wide.parquet")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    w = pd.read_parquet(a.wide, columns=["sample_key", "study_accession", "biosample_accession", "release_retired"])
    w = w[w.release_retired.isna()]
    rows, report = [], []
    for t in cfg["tables"]:
        acc = t["study_accession"]
        tab = pd.read_excel(source_path(t["source"]), sheet_name=t["sheet"], dtype=str)
        for k, v in (t.get("filter") or {}).items():
            tab = tab[tab[k].astype(str) == str(v)]
        runs = ena_runs(acc)
        cat = w[w.study_accession == acc]
        by_bs = cat.groupby("biosample_accession").sample_key.apply(list).to_dict()
        keys_cat = set(cat.sample_key)
        kc, km = t["key"]["column"], t["key"]["match"]
        if km == "run":
            r2s = dict(zip(runs.run_accession, runs.sample_accession))
            look = lambda x: [x] if x in keys_cat else by_bs.get(r2s.get(x), [])
        elif km == "sample_alias":
            a2s = runs.drop_duplicates("sample_alias").set_index("sample_alias").sample_accession.to_dict()
            look = lambda x: by_bs.get(a2s.get(x), [])
        elif km == "sample_alias_stem":
            a2s = runs.assign(s=runs.sample_alias.map(stem)).drop_duplicates("s").set_index("s").sample_accession.to_dict()
            look = lambda x: by_bs.get(a2s.get(stem(x)), [])
        else:
            raise ValueError(km)
        for f in t["fields"]:
            rows.append(dict(sample_key="", study_accession=acc, field_name=f, action="retire", old_value="", new_value=None, evidence_source="curated_supp_table",
                             evidence_locator=t["source"], evidence_quote="", confidence=None, rationale=f"replaced by {t['source']} [{t['sheet']}] (config/curated_supp_tables.yaml)", determined_by="curated_supp_v1"))
        n_rows, joined, n_vals = len(tab), set(), 0
        for i, r in tab.iterrows():
            sks = look(str(r[kc]))
            if not sks:
                continue
            for f, spec in t["fields"].items():
                raw = r[spec["column"]]
                val = transform(raw, spec.get("transform"))
                if val is None:
                    continue
                for sk in sks:
                    joined.add(sk); n_vals += 1
                    rows.append(dict(sample_key=sk, study_accession=acc, field_name=f, action="recode", old_value="", new_value=val, evidence_source=f"paper.supp.{t['source'].split('/', 1)[1]}[{t['sheet']}!{spec['column']}]",
                                     evidence_locator=f"{t['source']}/{t['sheet']}!{spec['column']}:{i + 2}", evidence_quote=f"{kc}={r[kc]}; {spec['column']}={'' if pd.isna(raw) else raw}",
                                     confidence=0.9, rationale=spec.get("note", "") or t.get("reported", ""), determined_by="curated_supp_v1"))
        report.append(dict(study_accession=acc, table_rows=n_rows, samples_joined=len(joined), catalog_samples=len(cat), values=n_vals))
    out = pd.DataFrame(rows, columns=COLS)
    out["confidence"] = pd.to_numeric(out.confidence)
    out.to_parquet(a.out, index=False)
    print(pd.DataFrame(report).to_string(index=False))


if __name__ == "__main__":
    main()
