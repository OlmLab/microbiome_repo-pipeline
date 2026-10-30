"""write_short_descriptions.py — the machine-written short title and one-sentence description shown on every project
page (R2026.18 format, R2026.31 wording for the full scope). One request per project at the `rubric` tier.

Context per project: the archive record (title, description), the abstract of the paper that generated the data (link
audit relation `generated`; abstract from ~/catalog/cache/abstracts.json, else Europe PMC), and the curated sample facts
(specimen count, body sites, DNA/RNA, countries, life stages, years, study design). Prints counts only.

usage (Claude Science kernel, `host` defined):
    runpy.run_path("scripts/write_short_descriptions.py", init_globals={"host": host,
        "ARGS": [IDS.txt, DOSSIER_DIRS(comma), LINKS.parquet, FACTS_DIR, OUT.parquet, BUDGET]}, run_name="__main__")
FACTS_DIR holds human_samples.parquet + human_studies.parquet (or catalog_samples / catalog_studies).
"""
import json, os, sys, time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from catalog.llm_backend import LLM, BudgetExceeded
from catalog import models as M

CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))
TOOL = {"name": "describe", "description": "short title and one-sentence description of the project",
        "input_schema": {"type": "object", "properties": {
            "short_title": {"type": "string"}, "short_description": {"type": "string"}},
            "required": ["short_title", "short_description"]}}
SYSTEM = (
    "Write a short title and a one-sentence description for a catalog of public human microbiome sequencing projects "
    "(shotgun metagenomes and metatranscriptomes from any body site). short_title <= 70 characters: topic + population + "
    "place (e.g. 'Oral and gut microbiome of twins across childhood, Finland'). "
    "short_description: ONE sentence <= 280 characters stating design, specimen count, body site(s), population, place "
    "and assay (shotgun metagenomics, metatranscriptomics or RNA sequencing, as the facts say). Use only facts in the "
    "material provided; leave out anything not given rather than guess. Use the specimen count from the curated facts. "
    "Do not mention the catalog, the archive or the paper itself.")
SOURCES = {True: "ENA study title/description; linked paper abstract; curated sample facts",
           False: "ENA study title/description; curated sample facts"}


def abstract(pmid, cache, local):
    if not pmid or pmid in ("None", "nan"):
        return None
    pmid = str(pmid).split(".")[0]
    if pmid in cache:
        return cache[pmid]
    if pmid in local:
        return local[pmid]
    try:
        r = requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                         params={"query": f"EXT_ID:{pmid} AND SRC:MED", "resultType": "core", "format": "json"},
                         timeout=60)
        hits = (r.json().get("resultList") or {}).get("result") or []
        local[pmid] = (hits[0].get("abstractText") if hits else None) or None
    except Exception:
        local[pmid] = None
    time.sleep(0.1)
    return local[pmid]


def facts_text(acc, smp, std):
    g = smp[smp.study_accession == acc]
    s = std[std.study_accession == acc]
    s = s.iloc[0] if len(s) else pd.Series(dtype=object)
    vc = lambda col, k=4: "; ".join(f"{a} {b}" for a, b in g[col].dropna().astype(str).value_counts().head(k).items()) \
        if col in g.columns else ""
    yrs = pd.to_numeric(g.get("collection_year"), errors="coerce").dropna()
    lines = [f"specimens: {len(g)}", f"body sites: {vc('body_site_code')}",
             f"nucleic acid of the data sets: {s.get('nucleic_acid')}", f"countries: {vc('country', 3)}",
             f"life stages: {vc('life_stage') or vc('age_category')}",
             f"collection years: {int(yrs.min())}-{int(yrs.max())}" if len(yrs) else "collection years: not given",
             f"study design (from the paper): {s.get('study_shape') or 'not given'}",
             f"participants stated by the paper: {s.get('n_participants') if pd.notna(s.get('n_participants')) else 'not given'}"]
    return "\n".join(lines)


def main(ids_path, dossier_dirs, links, facts_dir, out, budget):
    if globals().get("host") is not None:
        M.set_host(globals()["host"])
    ids = [x.strip() for x in open(ids_path) if x.strip()]
    dirs = [d.strip() for d in dossier_dirs.split(",") if d.strip()]
    la = pd.read_parquet(links)
    gen = la[la.relation == "generated"].groupby("study_accession").paper_id.apply(list).to_dict()
    def _first(*names):
        for n in names:
            p = os.path.join(facts_dir, n)
            if os.path.exists(p):
                return pd.read_parquet(p)
        raise FileNotFoundError(names)
    smp = _first("catalog_samples.parquet", "catalog_sample_metadata_wide.parquet", "human_samples.parquet")
    std = _first("catalog_studies.parquet", "human_studies.parquet")
    if "release_retired" in smp.columns:
        smp, std = smp[smp.release_retired.isna()], std[std.release_retired.isna()]
    ab = json.load(open(f"{CACHE}/abstracts.json")) if os.path.exists(f"{CACHE}/abstracts.json") else {}
    local = {}
    reqs, keys = [], []
    for acc in ids:
        d = None
        for dd in dirs:
            fp = os.path.join(dd, f"{acc}.json")
            if os.path.exists(fp):
                d = json.load(open(fp))
                break
        arch = (d or {}).get("archive") or {}
        papers = {str(p.get("paper_id")): p for p in (d or {}).get("papers", [])}
        abs_txt = None
        for pid in gen.get(acc, []):
            p = papers.get(str(pid), {})
            abs_txt = abstract(p.get("pmid") or pid, ab, local)
            if abs_txt:
                break
        ctx = (f"Archive title: {arch.get('study_title') or ''}\nArchive description: {(arch.get('description_short') or '')[:1500]}\n"
               + (f"Abstract of the paper that generated the data: {abs_txt[:2500]}\n" if abs_txt else "")
               + "Curated sample facts:\n" + facts_text(acc, smp, std))
        reqs.append({"system": SYSTEM, "prompt": ctx, "tools": [TOOL],
                     "tool_choice": {"type": "tool", "name": "describe"}, "max_tokens": 600})
        keys.append((acc, bool(abs_txt)))
    _role = os.environ.get("SHORT_DESC_ROLE", "rubric")   # "adjudicate" (Opus-class) for projects the rubric model refused
    llm = LLM(_role, cache_dir=f"{CACHE}/llm/short_descriptions" + ("" if _role == "rubric" else f"_{_role}"), ledger=os.path.splitext(out)[0] + "_ledger.csv",
              budget_usd=float(budget), pass_name="short_descriptions", max_concurrency=8)
    try:
        res = llm(reqs)
    except BudgetExceeded as e:
        print(json.dumps({"error": "budget", "detail": str(e)}))
        return
    rows, st = [], {"ok": 0, "refused_or_error": 0, "too_long": 0, "with_abstract": 0}
    for (acc, has_abs), r in zip(keys, res):
        tu = r.get("tool_use") if r.get("status") in ("ok", "cached") else None
        if isinstance(tu, str):
            try:
                tu = json.loads(tu)
            except ValueError:
                tu = None
        if not isinstance(tu, dict) or not tu.get("short_title"):
            st["refused_or_error"] += 1
            continue
        t, ds = str(tu["short_title"]).strip(), str(tu.get("short_description") or "").strip()
        if len(t) > 90 or len(ds) > 340:
            st["too_long"] += 1
            continue
        st["ok"] += 1
        st["with_abstract"] += int(has_abs)
        rows.append(dict(study_accession=acc, short_title=t, short_description=ds, short_description_model=r.get("model"),
                         short_description_sources=SOURCES[has_abs]))
    pd.DataFrame(rows, columns=["study_accession", "short_title", "short_description", "short_description_model",
                                "short_description_sources"]).to_parquet(out, index=False)
    if local:
        json.dump(local, open(os.path.splitext(out)[0] + "_abstracts.json", "w"))
    st["projects"] = len(ids)
    st["llm"] = llm.summary()
    print(json.dumps(st))


if __name__ == "__main__":
    main(*(globals().get("ARGS") or sys.argv[1:7]))
