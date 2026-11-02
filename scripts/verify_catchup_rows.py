#!/usr/bin/env python
"""verify_catchup_rows.py — a third, independent check of every statement the R2026.32 catch-up pass committed
(scripts/catchup_passes.py), and of the kinship relatedness calls, before they are applied (R2026.32 audit).

The full-run audit found the two-reading agreement rule too permissive for some fields (R3 health_condition and
collection_date from the methods; relatedness from a setting the paper mentions). Each committed row is shown to the
`adjudicate` role (Opus 5) with the passage around its quote — not the whole paper — and the question it must answer:
does this passage state this value for EVERY participant whose samples this project sequenced? Only confirmed rows are
kept; rejected rows go to a side table with the verifier's reason category.

usage (kernel with `host`, via runpy):
  python scripts/verify_catchup_rows.py --shard K --nshards N --budget USD     -> data/inputs/gut/verify_r2026_32/verdicts_shard_K.parquet
  python scripts/verify_catchup_rows.py --apply                                  -> filters r3c / r4c / design in place (backups kept)
Prints counts only.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)
import catchup_passes as CP  # noqa: E402  (norm, vocab dicts, project_texts helpers, PP)
from catalog import models as M  # noqa: E402
from catalog.llm_backend import LLM, BudgetExceeded  # noqa: E402

M.set_host(globals().get("host"))
GUT = os.path.join(ROOT, "data", "inputs", "gut")
OUT = os.path.join(GUT, "verify_r2026_32")
KIN = ["mother_infant", "twins", "families", "siblings", "households"]
NOTHINK = {"type": "disabled"}
REASONS = ["confirmed", "not_stated", "subgroup_only", "other_cohort", "wrong_value_or_code", "not_collection_date",
           "inclusion_threshold_not_value", "mentioned_setting_not_sampled_relation", "other"]
TOOL = CP.PP.tool("verify", {"items": {"type": "array", "items": {"type": "object", "properties": {
    "item": {"type": "integer"}, "verdict": {"type": "string", "enum": ["confirm", "reject"]},
    "reason": {"type": "string", "enum": REASONS}}, "required": ["item", "verdict", "reason"]}}}, ["items"])

FIELD_RULES = {
    "health_condition": "the code must name the condition shared by every sequenced participant (codes: " + CP._voc(CP.HC) + "); 'healthy_control' only if every participant is described as healthy. A disease studied in only cases, a comorbidity of some, or the condition of a different cohort is a reject.",
    "health_condition_detail": "the wording must describe the condition of every sequenced participant.",
    "collection_date": "the value must be the period in which THESE samples were collected (not recruitment of another cohort, publication, ethics approval, sequencing or follow-up dates).",
    "antibiotic_exposure": "'no' needs an explicit statement that participants had no (recent) antibiotics; 'yes' that every participant received them.",
    "life_stage": "every participant must fall in the stated life stage (neonate <28 d, infant 29 d–~3 y, child 3–12, adolescent 12–18, adult 18–65, elderly >65).",
    "country": "the participants were recruited / sampled in this single country.",
    "sex": "every participant has this sex.",
}
SYSTEM = ("You verify facts that an automatic reader extracted from a paper or an archive record about the participants of ONE human "
          "microbiome sequencing project. For each item you see the field, the extracted value, the quote it was based on and the "
          "passage around that quote. Confirm ONLY when the passage itself states the value and it holds for EVERY participant whose "
          "samples this project sequenced. Reject when the passage does not state it, when it holds for a subgroup only, when it "
          "describes another cohort or a re-used data set, when the value or code is wrong, when an inclusion threshold is taken as a "
          "value, or (relatedness) when a setting is merely mentioned (a ward, a community, a donor) rather than people being sampled "
          "as related pairs or groups. When unsure, reject. Field rules: " + " ".join(f"[{k}] {v}" for k, v in FIELD_RULES.items()))


HC_TOOL = CP.PP.tool("choose", {"items": {"type": "array", "items": {"type": "object", "properties": {
    "item": {"type": "integer"},
    "code": {"type": "string", "enum": list(CP.HC) + ["mixed_or_cases_and_controls", "not_stated"]}},
    "required": ["item", "code"]}}}, ["items"])
HC_SYSTEM = ("You read a passage about the participants of ONE human microbiome sequencing project and choose the health-condition code "
             "that describes EVERY participant whose samples this project sequenced. Codes: " + CP._voc(CP.HC) + ". Choose "
             "'mixed_or_cases_and_controls' when the sequenced participants include people with and without the condition, or with different "
             "conditions; 'not_stated' when the passage does not say. 'healthy_control' only when every participant is described as healthy. "
             "Use 'other_disease' / 'other_infection' only when no specific code fits. Decide from the passage alone.")


def window(text, quote, width=1400):
    q = CP.norm(quote).strip(" .,;:\"'")
    i = text.find(q) if q else -1
    if i < 0:
        return text[:2 * width]
    a, b = max(0, i - width), min(len(text), i + len(q) + width)
    return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")


def load_rows():
    r3 = pd.read_parquet(os.path.join(GUT, "r3c", "gut_r3c_determinations_merged.parquet")).assign(kind="r3")
    r4 = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(GUT, "r4c", "gut_r4c_determinations_shard_*.parquet")))], ignore_index=True).assign(kind="r4")
    dz = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(GUT, "design", "study_design_shard_C*.parquet")))], ignore_index=True)
    dz = dz[dz.relatedness_design.isin(KIN)]
    dz = dz.assign(kind="design", field_name="relatedness_design", value_normalized=dz.relatedness_design,
                   evidence_quote=dz.evidence_quote.fillna(""), evidence_source=dz.evidence_source.fillna(""))
    rows = pd.concat([r3, r4, dz[["study_accession", "kind", "field_name", "value_normalized", "evidence_quote", "evidence_source"]]], ignore_index=True)
    rows["row_id"] = rows.kind + ":" + rows.study_accession + ":" + rows.field_name + ":" + rows.value_normalized.astype(str)
    return rows


def run(shard, nshards, budget, host=None, hc=False, projects=None, tag=""):
    if host is not None:
        M.set_host(host)
    os.makedirs(OUT, exist_ok=True)
    rows = load_rows()
    if hc:   # code-choice check: one more independent reading of every health_condition row (the confirm check missed wrong codes)
        rows = rows[rows.field_name == "health_condition"].copy()
        rows["row_id"] = "hc:" + rows.row_id
    sel = sorted(set(rows.study_accession) & set(projects)) if projects else sorted(set(rows.study_accession))   # R2026.34: --projects
    mine = [p for i, p in enumerate(sel) if i % nshards == shard]
    shard = f"{tag}{shard}"
    st = pd.read_parquet(os.path.join(CP.PKG, "catalog_studies.parquet"))
    st = st[st.release_retired.isna()].set_index("study_accession") if "release_retired" in st.columns else st.set_index("study_accession")
    papers = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "catalog", "study_papers.parquet"))
    papers = papers[papers.relation == "own_data"]
    llm = LLM("adjudicate", cache_dir=f"{CP.CACHE}/llm/verify_r2026_32", ledger=os.path.join(OUT, f"{'hc_' if hc else ''}ledger_{shard}.csv"),
              budget_usd=budget, pass_name="verify_r2026_32", max_concurrency=4)
    reqs, keys = [], []
    for acc in mine:
        if acc not in st.index:
            continue
        tx = CP.project_texts(acc, CP.load_dossier(acc), st.loc[acc].to_dict(), papers)
        segs = tx["segs"]
        sub = rows[rows.study_accession == acc].reset_index(drop=True)
        items = []
        for i, r in sub.iterrows():
            src = {"paper.fulltext.methods": "paper_methods", "paper.fulltext.abstract": "paper_abstract", "paper.abstract": "paper_abstract",
                   "paper.fulltext.data_availability": "paper_data_availability", "study.description": "archive_description",
                   "study.title": "archive_title"}.get(str(r.evidence_source), None)
            seg = CP.find_quote(r.evidence_quote, segs, src) if r.evidence_quote else None
            ctx = window(segs.get(seg or src or "", "") or " ".join(segs.values()), r.evidence_quote)
            head = f"ITEM {i}: field={r.field_name}" + ("" if hc else f" value={r.value_normalized}")
            items.append(f"{head}\nQUOTE: {r.evidence_quote}\nPASSAGE: {ctx}")
        prompt = (f"PROJECT {acc}; archive title: {st.loc[acc].get('study_title')}; catalog samples: {CP.n_catalog_samples(st.loc[acc])}\n\n" + "\n\n".join(items))
        if hc:
            reqs.append({"system": HC_SYSTEM, "prompt": prompt, "tools": [HC_TOOL],
                         "tool_choice": {"type": "tool", "name": "choose"}, "max_tokens": 800, "thinking": NOTHINK})
        else:
            reqs.append({"system": SYSTEM, "prompt": prompt, "tools": [TOOL], "tool_choice": {"type": "tool", "name": "verify"},
                         "max_tokens": 1500, "thinking": NOTHINK})
        keys.append((acc, sub))
    status = {}
    try:
        res = llm(reqs)
    except BudgetExceeded as e:
        res, status["budget"] = [], str(e)[:200]
    out = []
    for (acc, sub), r in zip(keys, res):
        status[r.get("status")] = status.get(r.get("status"), 0) + 1
        got = {}
        if r.get("status") in ("ok", "cached") and isinstance(r.get("tool_use"), dict):
            for it in r["tool_use"].get("items") or []:
                if isinstance(it, dict) and isinstance(it.get("item"), int):
                    got[it["item"]] = it
        for i, row in sub.iterrows():
            it = got.get(i)
            if hc:
                code = (it or {}).get("code")
                verdict = ("confirm" if code == row.value_normalized else "reject") if code else ("no_answer" if r.get("status") != "refused" else "refused")
                out.append(dict(row_id=row.row_id, study_accession=acc, kind=row.kind, field_name=row.field_name, verdict=verdict,
                                reason="confirmed" if verdict == "confirm" else f"code_choice:{code}"))
                continue
            out.append(dict(row_id=row.row_id, study_accession=acc, kind=row.kind, field_name=row.field_name,
                            verdict=(it or {}).get("verdict") or ("no_answer" if r.get("status") != "refused" else "refused"),
                            reason=(it or {}).get("reason") or ""))
    df = pd.DataFrame(out, columns=["row_id", "study_accession", "kind", "field_name", "verdict", "reason"])
    df.to_parquet(os.path.join(OUT, f"{'hc_' if hc else ''}verdicts_shard_{shard}.parquet"), index=False)
    print(json.dumps({"shard": shard, "projects": len(keys), "rows": len(df), "llm_status": status,
                      "verdicts": df.verdict.value_counts().to_dict(), "cost": llm.summary()}, default=str)[:1200])


def apply():
    """Keep only confirmed rows; rows without an answer (refusal / error) are kept out too. Backups under verify_r2026_32/backup."""
    v = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(OUT, "verdicts_shard_*.parquet")))], ignore_index=True)
    ok = set(v.row_id[v.verdict == "confirm"])
    hcf = sorted(glob.glob(os.path.join(OUT, "hc_verdicts_shard_*.parquet")))
    if hcf:   # health_condition rows must ALSO match the independent code choice
        h = pd.concat([pd.read_parquet(f) for f in hcf], ignore_index=True)
        hc_ok = set(h.row_id[h.verdict == "confirm"].str.replace("hc:", "", n=1, regex=False))
        hc_all = set(h.row_id.str.replace("hc:", "", n=1, regex=False))
        ok = {r for r in ok if r not in hc_all or r in hc_ok}
        v = pd.concat([v, h], ignore_index=True)
    bk = os.path.join(OUT, "backup")
    os.makedirs(bk, exist_ok=True)
    rid = lambda d, kind: kind + ":" + d.study_accession + ":" + d.field_name + ":" + d.value_normalized.astype(str)
    stats = {}
    f3 = os.path.join(GUT, "r3c", "gut_r3c_determinations_merged.parquet")
    for f, kind in [(f3, "r3")] + [(f, "r4") for f in sorted(glob.glob(os.path.join(GUT, "r4c", "gut_r4c_determinations_shard_*.parquet")))]:
        d = pd.read_parquet(f)
        if not os.path.exists(os.path.join(bk, os.path.basename(f))):
            shutil.copy(f, bk)
        keep = rid(d, kind).isin(ok) if len(d) else pd.Series(dtype=bool)
        # a detail row stands or falls with its code row
        if len(d):
            base = d.field_name.str.replace("_detail", "", regex=False)
            code_ok = set(zip(d.study_accession[keep & ~d.field_name.str.endswith("_detail")], base[keep & ~d.field_name.str.endswith("_detail")]))
            det = d.field_name.str.endswith("_detail")
            keep = keep & (~det | pd.Series([(a, b) in code_ok for a, b in zip(d.study_accession, base)], index=d.index))
        stats[kind] = stats.get(kind, 0) + int(len(d) - keep.sum())
        d[keep].to_parquet(f, index=False)
    for f in sorted(glob.glob(os.path.join(GUT, "design", "study_design_shard_C*.parquet"))):
        d = pd.read_parquet(f)
        if not os.path.exists(os.path.join(bk, os.path.basename(f))):
            shutil.copy(f, bk)
        kin = d.relatedness_design.isin(KIN)
        conf = ("design:" + d.study_accession + ":relatedness_design:" + d.relatedness_design.astype(str)).isin(ok)
        drop = kin & ~conf
        stats["design_relatedness_cleared"] = stats.get("design_relatedness_cleared", 0) + int(drop.sum())
        d.loc[drop, "relatedness_design"] = "none"
        d.loc[drop, "relatedness_summary"] = ""
        d.loc[drop, "n_families"] = None
        d.loc[drop, "notes"] = d.loc[drop, "notes"].fillna("") + "; relatedness not confirmed by the verification reading"
        d["screen"] = ["both" if l == "yes" and r != "none" else ("longitudinal" if l == "yes" else ("related" if r != "none" else "none"))
                       for l, r in zip(d.longitudinal_design, d.relatedness_design)]
        d.to_parquet(f, index=False)
    v.to_parquet(os.path.join(OUT, "verdicts_all.parquet"), index=False)
    print(json.dumps({"verdicts": v.verdict.value_counts().to_dict(), "rows_removed": stats,
                      "reject_reasons": v[v.verdict == "reject"].reason.value_counts().to_dict()}))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--budget", type=float, default=10)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--hc", action="store_true", help="health_condition code-choice check")
    ap.add_argument("--projects", help="file of accessions: verify only these projects' rows (R2026.34 monthly runs)")
    ap.add_argument("--tag", default="", help="verdict shard name prefix, e.g. R34_")
    a = ap.parse_args(argv)
    if a.apply:
        return apply()
    pr = sorted({l.strip().split(",")[0] for l in open(a.projects) if l.strip() and not l.startswith("study_accession")}) if a.projects else None
    return run(a.shard, a.nshards, a.budget, host=globals().get("host"), hc=a.hc, projects=pr, tag=a.tag)


if __name__ == "__main__":
    main()
