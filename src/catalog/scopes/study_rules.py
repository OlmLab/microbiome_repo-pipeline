"""study_rules.py — deterministic project-level rules added in R2026.39 (catalog deep pass, stage A).

  apply_kit_processing   `processing` from the kit text: a library or extraction kit that names a sequence-independent
                         amplification chemistry (WTA / WTA2, SISPA, random-primed, TransPlex, GenomePlex) sets
                         `sequence_independent_amplification`; `wga_mda` is kept only when the same text also names a
                         phi29 / MDA chemistry (REPLI-g, GenomiPhi, MDA). Before R2026.39 the classification prompt told the
                         reader to file SISPA and WTA under wga_mda for want of another code.
  fill_linked_papers     `linked_pmids` / `n_linked_papers` of every project from the paper tables. Before R2026.39 the
                         column was filled only on rows first published in R2026.7, and a project that was retired and
                         added again (115 projects at R2026.30 / R2026.31) lost it.
  rollup_subtype_from_detail  `health_condition` on an unspecified parent code while the same sample's
                         `health_condition_detail` names exactly one subtype (config/health_subtype_rollup.csv).
"""
from __future__ import annotations

import re

import pandas as pd

NEW_CODE = "sequence_independent_amplification"
SIA_KIT = re.compile(r"(?i)\bWTA[\s\-]?2?\b|\bSISPA\b|random[\s\-]?prim(?:er|ed|ing)|\bTransplex\b|\bGenomePlex\b|"
                     r"sequence[\s\-]independent[,\s]+single[\s\-]primer")
MDA_KIT = re.compile(r"(?i)\bMDA\b|phi\s?29|repli-?g|genomiphi|multiple[\s\-]displacement")


def apply_kit_processing(gs: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Returns (gs, number of projects whose `processing` changed)."""
    if "processing" not in gs.columns or not {"library_kit", "extraction_kit"} & set(gs.columns):
        return gs, 0
    kit = pd.Series("", index=gs.index)
    for c in ("library_kit", "extraction_kit"):
        if c in gs.columns:
            kit = kit + " | " + gs[c].fillna("").astype(str)
    hit = kit[kit.str.contains(SIA_KIT)]
    gs = gs.copy()
    n = 0
    for ix, text in hit.items():
        old = gs.at[ix, "processing"]
        codes = [c for c in str(old if isinstance(old, str) else "").split(";") if c and c != "none"]
        if "wga_mda" in codes and not MDA_KIT.search(text):
            codes.remove("wga_mda")
        new = ";".join(sorted(set(codes) | {NEW_CODE}))
        if new != old:
            gs.at[ix, "processing"] = new
            n += 1
    return gs, n


def _pmid(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    m = re.fullmatch(r"(?:MED:)?(\d{5,9})(?:\.0)?", str(v).strip())
    return m.group(1) if m else None


def fill_linked_papers(gs: pd.DataFrame, papers: pd.DataFrame | None, previous: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    """`gs` is indexed by study_accession. `papers` = study_papers (study_accession, pmid, paper_id, relation own_data / reuse);
    `previous` = the previous package's catalog_studies rows (current AND retired) with study_accession, linked_pmids.
    Order inside linked_pmids: own-data papers, then ids the row or an earlier row of the project already carried, then
    reuse papers. The relation of each paper stays in catalog_study_papers; own-data ids alone are in paper_ids_generated."""
    own, reuse, carried = {}, {}, {}
    if papers is not None and len(papers):
        for acc, pm, pid, rel in zip(papers.study_accession, papers.pmid, papers.paper_id, papers.relation):
            p = _pmid(pm) or _pmid(pid)
            if p:
                (own if rel == "own_data" else reuse).setdefault(acc, []).append(p)
    if previous is not None and len(previous) and "linked_pmids" in previous.columns:
        for acc, v in zip(previous.study_accession, previous.linked_pmids):
            if isinstance(v, str) and v:
                carried.setdefault(acc, []).extend(re.findall(r"\d{5,9}", v))
    gs = gs.copy()
    cur = gs["linked_pmids"] if "linked_pmids" in gs.columns else pd.Series(None, index=gs.index, dtype=object)
    old_n = pd.to_numeric(gs["n_linked_papers"], errors="coerce") if "n_linked_papers" in gs.columns else pd.Series(float("nan"), index=gs.index)
    out, n_out, stats = [], [], {"filled": 0, "carried_from_earlier_row": 0, "unchanged": 0}
    for acc in gs.index:
        had = re.findall(r"\d{5,9}", cur.get(acc)) if isinstance(cur.get(acc), str) else []
        ids = list(dict.fromkeys(sorted(set(own.get(acc, []))) + had + carried.get(acc, []) + sorted(set(reuse.get(acc, [])))))
        if ids and not had:
            stats["filled"] += 1
            if set(carried.get(acc, [])) - set(own.get(acc, [])) - set(reuse.get(acc, [])):
                stats["carried_from_earlier_row"] += 1
        elif ids == had:
            stats["unchanged"] += 1
        out.append(";".join(ids) if ids else None)
        on = old_n.get(acc)
        n_out.append(max(len(ids), int(on) if pd.notna(on) else 0) if (ids or pd.notna(on)) else None)
    gs["linked_pmids"] = out
    gs["n_linked_papers"] = pd.array(n_out, dtype="Int64")
    return gs, stats


def rollup_subtype_from_detail(det: pd.DataFrame, table: pd.DataFrame | None, release_id: str = "") -> tuple[pd.DataFrame, int]:
    """`det` holds one row per sample and field (after resolve). A health_condition row on a parent code listed in `table`
    takes the subtype when the sample's health_condition_detail value equals a listed detail string. Rows set by a checker
    (dq_adjudication_v2) and curated infant rows are left alone."""
    if table is None or not len(table):
        return det, 0
    m = {(p, str(d).strip()): c for p, d, c in zip(table.parent_code, table.detail_value, table.new_code)}
    detail = det.loc[det.field_name.eq("health_condition_detail"), ["sample_key", "value_normalized"]].drop_duplicates("sample_key").set_index("sample_key").value_normalized
    hc = det.field_name.eq("health_condition") & det.value_normalized.isin({p for p, _ in m}) \
        & ~det.determined_by.astype(str).str.startswith("dq_adjudication_v2") & det.src_track.astype(str).ne("infant_catalog")
    idx, new, notes = [], [], []
    for ix, sk, v in zip(det.index[hc], det.loc[hc, "sample_key"], det.loc[hc, "value_normalized"]):
        d = detail.get(sk)
        c = m.get((v, str(d).strip())) if isinstance(d, str) else None
        if c:
            idx.append(ix); new.append(c)
            notes.append(f"subtype taken from the sample's health_condition_detail value '{d}' ({release_id}; was {v})")
    if not idx:
        return det, 0
    det = det.copy()
    det.loc[idx, "value_normalized"] = new
    old = det.loc[idx, "parse_note"].fillna("").astype(str)
    det.loc[idx, "parse_note"] = [(o + "; " + n_).lstrip("; ") for o, n_ in zip(old, notes)]
    return det, len(idx)
