#!/usr/bin/env python
"""exposure_item_check.py — an Opus 5 check of every exposure item (R2026.40).

An audit of 40 exposure items from the paper re-read found 21 right (9 wrong scale, 7 not stated in the quote, 2 wrong concept,
1 about animals), so no exposure item is committed on a single reading any more. Every item — those already in
catalog_exposure_items and those the re-read proposed — is checked against the passage its quote comes from and the sample
table of the deposit by the `audit` role. One request per project.

Merge (deterministic, --merge): an item is kept when the check finds the quote in the text, says the passage reports the
concept for people whose samples are in the deposit, and names a concept from the list; the concept and the scale are the
checker's; the summary is the checker's revision when it found unsupported parts (dropped when no revision of 15+ characters
is given). One item per project and concept (items already in the catalog first).

usage (kernel with `host`, via runpy with init_globals={"host": host}):
  python scripts/exposure_item_check.py --items <items.parquet> --shard K --nshards N --budget USD --out <dir>
  python scripts/exposure_item_check.py --merge --out <dir>          -> <dir>/exposure_items_checked.parquet
Prints counts only.
"""
import argparse, glob, json, os, sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)
from catalog import models as M  # noqa: E402
from catalog.llm_backend import LLM, BudgetExceeded  # noqa: E402
import catchup_passes as CP  # noqa: E402
import reread_passes as RP  # noqa: E402

PKG = os.path.expanduser(os.environ.get("CATALOG_PKG", "~/catalog/cache/deep_pass/pkg_r39"))
NOTHINK = {"type": "disabled"}
CONCEPTS = ["sanitation", "drinking_water", "handwashing", "floor_or_housing", "household_size_or_crowding", "siblings", "daycare",
            "livestock_or_animals", "pets", "farm_living", "socioeconomic", "breastfeeding", "diarrhoea", "parasite_or_pathogen_testing",
            "deworming", "travel", "wash_intervention"]
SYSTEM = """You check "exposure items" of a catalog of human microbiome sequencing projects. An item says that the project's paper
records one kind of exposure information about the people whose samples are in the deposit. For each item you get the concept, the
scale, a one-line summary and the passage that holds the item's quote; you also get the project's sample table summarised by group.

Concepts: sanitation = toilet, latrine or sewage facilities; drinking_water = water source or treatment; handwashing = handwashing
or hygiene practice as an exposure (not instructions before sampling); floor_or_housing = floor, wall or roof material, housing
type; household_size_or_crowding = people or children per household or room; siblings = siblings or birth order; daycare = daycare
or nursery attendance; livestock_or_animals = contact with livestock or other animals; pets = pet ownership or contact;
farm_living = living or working on a farm; socioeconomic = education, income, occupation, wealth or asset index, low-income area;
breastfeeding = breastfeeding or infant feeding mode; diarrhoea = diarrhoea episodes, symptoms or an exclusion based on diarrhoea;
parasite_or_pathogen_testing = testing participants for enteric parasites or enteric pathogens; deworming = anthelmintic drugs;
travel = travel; wash_intervention = a water, sanitation or hygiene intervention given to participants.
Scale: per_person = a value recorded for each participant (a questionnaire item, a column of a table); per_group = reported
separately for study groups (e.g. Table 1 percentages by arm); whole_cohort = one statement or figure for everyone in the deposit
(an inclusion or exclusion criterion, one cohort-wide proportion). A statement that covers only one of several groups of the
deposit is per_group, not whole_cohort.

For each item return:
  supported: true only if the passage itself reports this kind of information (not a laboratory use of the word, not another study,
    not a neighbouring topic such as diet, antibiotics, inflammation markers or study logistics);
  about_deposit_people: true only if the information is about the human participants whose samples are deposited (not animals,
    not a parent cohort described in general, not people the deposit does not hold);
  concept: the concept from the list that the passage supports (it may differ from the item's), or "none";
  scale: the right scale (it may differ from the item's);
  summary_ok: true if every fact in the summary is supported by the passage;
  summary_revised: when summary_ok is false, the summary rewritten to keep only supported facts (at most 200 characters), else "";
  note: at most 120 characters, only when something is wrong.
Judge from the passage and the sample table only."""
TOOL = {"name": "check", "description": "Return a verdict per item.", "input_schema": {"type": "object", "properties": {
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "integer"}, "supported": {"type": "boolean"}, "about_deposit_people": {"type": "boolean"},
        "concept": {"type": "string", "enum": CONCEPTS + ["none"]}, "scale": {"type": "string", "enum": ["per_person", "per_group", "whole_cohort"]},
        "summary_ok": {"type": "boolean"}, "summary_revised": {"type": "string"}, "note": {"type": "string"}},
        "required": ["id", "supported", "about_deposit_people", "concept", "scale", "summary_ok"]}}}, "required": ["items"]}}
SEL = ["comparison_group", "country", "health_condition", "age_category", "lifestyle"]
SYSTEM2 = SYSTEM + """

For these items no passage could be found for the stored quote, so you get the text of the project's paper as the readings saw it
(archive record, then the paper). Decide each item from that text. When supported is true you MUST also return `quote`: one
sentence copied character for character from the text that reports the information (no ellipsis, no paraphrase); an item
whose quote is not found verbatim in the text is dropped. When the text does not report it, supported = false and quote = ""."""
TOOL2 = json.loads(json.dumps(TOOL))
TOOL2["input_schema"]["properties"]["items"]["items"]["properties"]["quote"] = {"type": "string"}


def locate_any(q, segs, papers_of, cache):
    """the quote in the texts the readings saw, else anywhere in the title / abstract / data availability / methods of any
    own-data paper with open full text (the readings saw the first 30,000 to 40,000 characters of one paper)."""
    name, passage = RP.locate(q, segs)
    if name:
        return name, passage
    qn = CP.norm(q).strip(" .,;:\"'")
    if len(qn.split()) < 3:
        return None, None
    for pmcid in papers_of:
        if pmcid not in cache:
            cache[pmcid] = CP.norm(CP.PP.paper_sections(pmcid, max_chars=600000) or "")
        t = cache[pmcid]
        i = t.find(qn)
        if i >= 0:
            return f"paper_{pmcid}", t[max(0, i - 700): i + len(qn) + 700]
    for pmcid in papers_of:   # results, tables and legends: the whole article
        k = "whole:" + pmcid
        if k not in cache:
            try:
                import xml.etree.ElementTree as ET
                cache[k] = CP.norm(" ".join(ET.parse(CP.PP.jats_file(pmcid)).getroot().itertext()))
            except Exception:  # noqa: BLE001 — unreadable file counts as no text
                cache[k] = ""
        t = cache[k]
        i = t.find(qn)
        if i >= 0:
            return f"article_{pmcid}", t[max(0, i - 700): i + len(qn) + 700]
    return None, None


def run(items, out, budget, shard, host=None):
    if host is not None:
        M.set_host(host)
    os.makedirs(os.path.join(out, "logs"), exist_ok=True)
    projects = list(dict.fromkeys(items.study_accession))
    st = pd.read_parquet(os.path.join(PKG, "catalog_studies.parquet"), filters=[("study_accession", "in", projects)])
    st = (st[st.release_retired.isna()] if "release_retired" in st.columns else st).set_index("study_accession")
    papers = pd.read_parquet(os.path.join(ROOT, "data", "inputs", "catalog", "study_papers.parquet"))
    papers = papers[(papers.relation == "own_data") & papers.study_accession.isin(projects)]
    have = set(__import__("pyarrow.parquet", fromlist=["x"]).ParquetFile(os.path.join(PKG, "catalog_sample_metadata_wide.parquet")).schema_arrow.names)
    w = pd.read_parquet(os.path.join(PKG, "catalog_sample_metadata_wide.parquet"), columns=[c for c in ["study_accession", "sample_key", "sample_type", "release_retired"] + SEL if c in have],
                        filters=[("study_accession", "in", projects)])
    w = w[w.release_retired.isna()]
    w_by = {k: g for k, g in w.groupby("study_accession")}
    chk = LLM("audit", cache_dir=os.path.expanduser("~/catalog/cache/llm/exposure_check_r2026_40"), ledger=os.path.join(out, "logs", f"ledger_{shard}.csv"),
              budget_usd=budget, pass_name="exposure_check_r2026_40", max_concurrency=6)
    rows, reqs, keys, reqs2, keys2 = [], [], [], [], []
    for acc, g in items.groupby("study_accession", sort=False):
        base = [dict(r._asdict(), located=None, check_status=None) for r in g.itertuples(index=False)]
        if acc not in st.index:
            for b in base:
                b["check_status"] = "not_in_catalog"
            rows += base
            continue
        tx = CP.project_texts(acc, CP.load_dossier(acc), st.loc[acc].to_dict(), papers)
        segs = tx.get("segs") or {}
        ws = w_by.get(acc)
        summ = f"{0 if ws is None else len(ws)} samples"
        if ws is not None:
            for c in ["sample_type"] + SEL:
                if c in ws.columns:
                    vc = ws[c].fillna("(blank)").value_counts()
                    summ += f"\n  {c}: " + "; ".join(f"{k} x{v}" for k, v in vc.head(8).items()) + (f" … (+{len(vc) - 8} more)" if len(vc) > 8 else "")
        body = f"PROJECT {acc}: {st.loc[acc].get('study_title')}\nSAMPLE TABLE: {summ}\n\n"
        n = 0
        own = [r.pmcid for r in papers[papers.study_accession == acc].itertuples() if isinstance(r.pmcid, str) and CP.PP.jats_file(r.pmcid)]
        cache = {}
        for j, b in enumerate(base):
            name, passage = locate_any(b["quote"], segs, own, cache)
            b["located"] = name
            if not name:
                b["check_status"] = "quote_not_found"
                if tx.get("mode") in ("fulltext", "abstract") and tx.get("prompt"):
                    b["check_status"] = None
                    b["_requote"] = True
                continue
            n += 1
            body += f"ITEM {j} — concept = {b['concept']}; scale = {b['scale']}\n  SUMMARY: {b['summary']}\n  PASSAGE ({name}): …{passage}…\n\n"
        rq = [(j, b) for j, b in enumerate(base) if b.get("_requote")]
        if rq:
            body2 = tx["prompt"] + f"\n\nSAMPLE TABLE: {summ}\n\nITEMS TO CHECK AGAINST THE TEXT ABOVE:\n" + "".join(
                f"ITEM {j} — concept = {b['concept']}; scale = {b['scale']}\n  SUMMARY: {b['summary']}\n" for j, b in rq)
            reqs2.append({"system": SYSTEM2, "prompt": body2, "tools": [TOOL2], "tool_choice": {"type": "tool", "name": "check"}, "max_tokens": 300 + 300 * len(rq), "thinking": NOTHINK})
            keys2.append((base, segs, own, cache))
        if n:
            reqs.append({"system": SYSTEM, "prompt": body, "tools": [TOOL], "tool_choice": {"type": "tool", "name": "check"}, "max_tokens": 300 + 220 * n, "thinking": NOTHINK})
            keys.append(base)
        elif not rq:
            rows += base
    try:
        res = chk(reqs)
        res2 = chk(reqs2) if reqs2 else []
    except BudgetExceeded as e:
        res = locals().get("res") or []
        res2 = []
        print("budget:", e)
    done = set()
    for i, (base, segs, own, cache) in enumerate(keys2):
        r = res2[i] if i < len(res2) else {"status": "not_run"}
        ok = r.get("status") in ("ok", "cached") and isinstance(r.get("tool_use"), dict)
        verd = {int(x["id"]): x for x in (r["tool_use"].get("items") or []) if isinstance(x, dict) and str(x.get("id", "")).lstrip("-").isdigit()} if ok else {}
        for j, b in enumerate(base):
            if not b.get("_requote"):
                continue
            v = verd.get(j)
            b["check_status"] = "ok" if v else ("no_verdict" if ok else str(r.get("status_original") or r.get("status")))
            for k in ("supported", "about_deposit_people", "concept", "scale", "summary_ok", "summary_revised", "note"):
                b[f"chk_{k}"] = (v or {}).get(k)
            b["quote_original"] = b["quote"]
            if v and v.get("supported") is True:
                name, _ = locate_any(str(v.get("quote") or ""), segs, own, cache)
                if name:
                    b["quote"], b["located"] = str(v.get("quote")), name
                else:
                    b["check_status"] = "new_quote_not_verbatim"
        if id(base) not in {id(k) for k in keys}:
            rows += base
    for i, base in enumerate(keys):
        r = res[i] if i < len(res) else {"status": "not_run"}
        ok = r.get("status") in ("ok", "cached") and isinstance(r.get("tool_use"), dict)
        verd = {int(x["id"]): x for x in (r["tool_use"].get("items") or []) if isinstance(x, dict) and str(x.get("id", "")).lstrip("-").isdigit()} if ok else {}
        for j, b in enumerate(base):
            if b["check_status"] or b.get("_requote"):
                continue
            v = verd.get(j)
            b["check_status"] = "ok" if v else ("no_verdict" if ok else str(r.get("status_original") or r.get("status")))
            for k in ("supported", "about_deposit_people", "concept", "scale", "summary_ok", "summary_revised", "note"):
                b[f"chk_{k}"] = (v or {}).get(k)
        rows += base
    df = pd.DataFrame(rows)
    df.loc[df.check_status.isna(), "check_status"] = "not_run"
    df["requoted"] = df["_requote"].map(lambda v: v is True) if "_requote" in df.columns else False
    df = df.drop(columns=["_requote"], errors="ignore")
    if "quote_original" not in df.columns:
        df["quote_original"] = None
    for c in ("chk_supported", "chk_about_deposit_people", "chk_concept", "chk_scale", "chk_summary_ok", "chk_summary_revised", "chk_note"):
        if c not in df.columns:
            df[c] = None
    df.to_parquet(os.path.join(out, f"exposure_check_shard_{shard}.parquet"), index=False)
    print(json.dumps({"shard": shard, "items": len(df), "projects": len(projects), "check_status": df.check_status.value_counts().to_dict(), "cost": chk.summary()}, default=str))


def merge(out):
    fs = sorted(glob.glob(os.path.join(out, "exposure_check_shard_*.parquet")))
    d = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    t = lambda s: s.map(lambda v: v is True)
    ok = (d.check_status == "ok") & t(d.chk_supported) & t(d.chk_about_deposit_people) & d.chk_concept.isin(CONCEPTS)
    rev = d.chk_summary_revised.fillna("").astype(str).str.strip()
    sum_ok = t(d.chk_summary_ok) | (rev.str.len() >= 15)
    d["outcome"] = "kept"
    d.loc[d.check_status != "ok", "outcome"] = "dropped: " + d.check_status.astype(str)
    d.loc[(d.check_status == "ok") & ~t(d.chk_supported), "outcome"] = "dropped: the passage does not report it"
    d.loc[(d.check_status == "ok") & t(d.chk_supported) & ~t(d.chk_about_deposit_people), "outcome"] = "dropped: not about the people in the deposit"
    d.loc[(d.check_status == "ok") & t(d.chk_supported) & t(d.chk_about_deposit_people) & ~d.chk_concept.isin(CONCEPTS), "outcome"] = "dropped: no listed concept fits"
    d.loc[ok & ~sum_ok, "outcome"] = "dropped: summary not supported and not revised"
    k = d[ok & sum_ok].copy()
    k["concept_changed"] = k.concept != k.chk_concept
    k["scale_changed"] = k.scale != k.chk_scale
    k["summary_changed"] = ~t(k.chk_summary_ok)
    k["summary"] = k.summary.where(t(k.chk_summary_ok), rev[k.index].str[:300])
    k["concept"], k["scale"] = k.chk_concept, k.chk_scale
    k["_o"] = (k.origin != "catalog").astype(int)
    k = k.sort_values(["study_accession", "_o"]).drop_duplicates(["study_accession", "concept"], keep="first")
    d.loc[ok & sum_ok & ~d.index.isin(k.index), "outcome"] = "dropped: second item of the same concept"
    k[["study_accession", "concept", "scale", "summary", "quote"]].to_parquet(os.path.join(out, "exposure_items_checked.parquet"), index=False)
    d.to_parquet(os.path.join(out, "exposure_check_all.parquet"), index=False)
    print(json.dumps({"shards": len(fs), "items": len(d), "by_origin": d.groupby("origin").size().to_dict(), "outcome": d.groupby(["origin", "outcome"]).size().rename(lambda x: x).reset_index().values.tolist(),
                      "kept": len(k), "kept_projects": int(k.study_accession.nunique()), "concept_changed": int(k.concept_changed.sum()), "scale_changed": int(k.scale_changed.sum()),
                      "summary_changed": int(k.summary_changed.sum())}, default=str))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--items"); ap.add_argument("--shard", type=int, default=0); ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--budget", type=float, default=6.0); ap.add_argument("--out", required=True); ap.add_argument("--merge", action="store_true")
    a = ap.parse_args(argv)
    if a.merge:
        return merge(a.out)
    it = pd.read_parquet(a.items)
    pr = sorted(it.study_accession.unique())
    mine = set(pr[a.shard::a.nshards])
    return run(it[it.study_accession.isin(mine)], a.out, a.budget, a.shard, host=globals().get("host"))


if __name__ == "__main__":
    main(globals().get("ARGS"))
