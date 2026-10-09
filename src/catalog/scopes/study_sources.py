"""study_sources.py — per-project source flags for the gut scope (R2026.18).

For every catalog project, one status per evidence source, shown as checkmarks on the Project sheet and the project page:

  archive      sequence-archive records (BioSample attributes, sample names / titles, run fields)      route R1
  abstract     paper abstract and the archive's project description                                   route R4
  fulltext     open full text of the linked paper (methods prose, tables in the article body)         route R3
  supplement   supplementary tables / files of the linked paper                                       route R2 (own)
  external     external curated resources (curatedMetagenomicData, GMrepo, ...)                       route R2 (external_ingest)
  contribution metadata uploaded by a user through the Contribute form                                 any route, determined_by *contribution*
  verified     a person checked the project against the web (papers, supplements, archive) and confirmed the metadata is
               complete — recorded in config/human_verified.csv from the 'Verify metadata' issue form

status: 'used' (the source gave >= 1 value now in the determinations), 'checked' (the source exists / was read but gave no usable
value), 'none' (not available: no linked paper, not open access, no supplement ...). Counts are distinct sample x field values that
are not 'unknown'. Deterministic; reads the leaf logs under data/inputs/gut (R3 / R3b / R4 study summaries, R2 gate tables,
deep-annotation source logs) when present.
"""
from __future__ import annotations

import glob
import json
import os

import numpy as np
import pandas as pd

SOURCES = ("archive", "abstract", "fulltext", "supplement", "external", "contribution", "verified")
LABELS = {"archive": "Sequence archive", "abstract": "Abstract / description", "fulltext": "Full text",
          "supplement": "Supplementary tables", "external": "External resources", "contribution": "User contribution",
          "verified": "Human verified"}


def classify_rows(det: pd.DataFrame) -> pd.Series:
    es = det.evidence_source.fillna("").astype(str)
    db = det.determined_by.fillna("").astype(str)
    route = det.route.fillna("").astype(str)
    # r2_ext rows are supplementary tables of OTHER papers that tabulate this project's samples -> supplement, not an external resource
    ext = es.str.startswith("external") | db.str.startswith("external_ingest")
    contrib = db.str.contains("contribution", case=False) | es.str.startswith("contribution")
    out = np.select([contrib, ext, route.eq("R1"), route.eq("R2"), route.eq("R3"), route.eq("R4")],
                    ["contribution", "external", "archive", "supplement", "fulltext", "abstract"], default="")
    return pd.Series(out, index=det.index)


VERIFIED_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "human_verified.csv")


def load_verified(path: str | None = None) -> dict:
    """study_accession -> list of {by, date, issue} from config/human_verified.csv (one row per person-verification)."""
    path = path or VERIFIED_PATH
    if not os.path.exists(path):
        return {}
    v = pd.read_csv(path, dtype=str, keep_default_na=False)
    out = {}
    for r in v.itertuples():
        out.setdefault(r.study_accession, []).append({"by": r.verified_by, "date": r.date, "issue": r.issue})
    return out


def _read_all(pattern: str) -> pd.DataFrame:
    fs = sorted(glob.glob(pattern))
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()


def _truthy(s: pd.Series) -> pd.Series:
    return s.astype(str).str.lower().isin(["1", "true", "1.0", "yes", "ok"])


def checked_sets(gut_dir: str) -> dict:
    """Studies for which each source was available / read, from the leaf logs."""
    chk = {k: set() for k in SOURCES}
    pm = {}
    r4 = _read_all(os.path.join(gut_dir, "r4", "gut_r4_study_summary_shard_*.parquet"))
    if len(r4):
        m = pd.Series(False, index=r4.index)
        for c in ("abstract_available", "has_description"):
            if c in r4:
                m |= _truthy(r4[c])
        chk["abstract"] |= set(r4.loc[m, "study_accession"])
    for p in ("r3/gut_r3_study_summary_all.parquet", "r3b/gut_r3b_study_summary_all.parquet"):
        f = os.path.join(gut_dir, p)
        if os.path.exists(f):
            d = pd.read_parquet(f)
            if "fulltext_available" in d:
                ok = d[_truthy(d.fulltext_available)]
                chk["fulltext"] |= set(ok.study_accession)
                pc = "pmcid_used" if "pmcid_used" in ok else ("pmcid" if "pmcid" in ok else None)
                if pc:
                    for s, v in zip(ok.study_accession, ok[pc]):
                        if isinstance(v, str) and v.startswith("PMC"):
                            pm.setdefault(s, set()).add(v)
    g = _read_all(os.path.join(gut_dir, "r2", "gut_r2_gate_shard_*.parquet"))
    if len(g):
        chk["supplement"] |= set(g.study_accession)
    ds = _read_all(os.path.join(gut_dir, "deep", "deep_sources_shard_*.parquet"))
    if len(ds):
        def num(c):
            return pd.to_numeric(ds[c], errors="coerce").fillna(0) if c in ds else pd.Series(0, index=ds.index)
        if "abstract_available" in ds:
            chk["abstract"] |= set(ds.loc[_truthy(ds.abstract_available), "study_accession"])
        if "fulltext_available" in ds:
            chk["fulltext"] |= set(ds.loc[_truthy(ds.fulltext_available), "study_accession"])
        chk["supplement"] |= set(ds.loc[num("supp_files_found") > 0, "study_accession"])
        if "pmcids_tried" in ds:
            for s, v in zip(ds.study_accession, ds.pmcids_tried.fillna("").astype(str)):
                for x in v.replace(",", ";").split(";"):
                    if x.strip().startswith("PMC"):
                        pm.setdefault(s, set()).add(x.strip())
    chk["_pmcids"] = pm
    return chk


def study_source_flags(det: pd.DataFrame, studies: list, gut_dir: str | None, infant_acc: set, verified_path: str | None = None) -> pd.DataFrame:
    d = det[["study_accession", "sample_key", "field_name", "value_normalized", "evidence_source", "determined_by", "route", "src_track"]]
    d = d[d.value_normalized.notna() & ~d.value_normalized.astype(str).isin(["unknown", "", "nan", "None"])]
    cls = classify_rows(d)
    d = d.assign(src=cls)
    cnt = (d[d.src != ""].drop_duplicates(["study_accession", "sample_key", "field_name", "src"])
           .groupby(["study_accession", "src"]).size().unstack(fill_value=0))
    fields = (d[d.src != ""].groupby(["study_accession", "src"]).field_name.apply(lambda s: sorted(set(s))).unstack())
    verified = load_verified(verified_path)
    chk = checked_sets(gut_dir) if gut_dir and os.path.isdir(gut_dir) else {k: set() for k in SOURCES}
    ext_names = (d[d.src == "external"].evidence_source.str.replace(r"^external\.", "", regex=True).str.split(r"[.:\[]").str[0]
                 .groupby(d.loc[d.src == "external", "study_accession"]).apply(lambda s: sorted(set(s))))
    rows = []
    for s in studies:
        r = {"study_accession": s}
        counts = {}
        for k in SOURCES:
            n = (1 if s in verified else 0) if k == "verified" else int(cnt.at[s, k]) if (s in cnt.index and k in cnt.columns) else 0
            counts[k] = n
            if k == "verified":
                st = "used" if n > 0 else "none"
            elif n > 0:
                st = "used"
            elif k == "archive" or s in chk.get(k, set()):
                st = "checked"
            else:
                st = "none"
            r[f"src_{k}"] = st
        det_fields = {k: fields.at[s, k] for k in SOURCES if s in fields.index and k in fields.columns and isinstance(fields.at[s, k], list)}
        detail = {k: {"n_values": counts[k], "fields": det_fields.get(k, [])} for k in SOURCES}
        if s in chk.get("_pmcids", {}):
            detail["fulltext"]["pmcids"] = sorted(chk["_pmcids"][s])
        if s in verified:
            detail["verified"] = {"n_values": 0, "fields": [], "by": verified[s]}
        if s in ext_names.index:
            detail["external"]["resources"] = ext_names.at[s]
        r["src_detail"] = json.dumps(detail, sort_keys=True)
        rows.append(r)
    return pd.DataFrame(rows).set_index("study_accession")
