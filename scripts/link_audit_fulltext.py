#!/usr/bin/env python
"""link_audit_fulltext.py — one paper, one project: did the paper generate the project's data? (R2026.39, catalog deep pass A3)

The first link audit (pilot_passes.py --passes link_audit) reads titles and abstracts, eight candidates per request, on the
utility tier. It leaves a project without an own-data paper when the abstract does not say enough. This audit is for those
projects: ONE request per (project, paper) on the reasoning tier, with the archive record of the project and, where the
paper's full text is in the cache (~/catalog/cache/pmc_oa), the sentences that cite one of the project's accessions and the
data-availability statement. A verdict `generated` is committed only when
  * the model's supporting quote is verbatim in the text it was given, and
  * a second, independent reading on the audit tier (Opus 5, config/models.yaml) also returns `generated`.
Anything else stays out of the catalog's own-data links (the row is kept with its verdicts for the record).

Outputs in --out: pairs_<shard>.parquet (study_accession, paper_id, pmid, pmcid, doi, title, relation, relation_first,
relation_check, quote, quote_verbatim, quote_source, text_mode, accession_in_text, basis, status, model_first, model_check),
progress_<shard>.json (counts only) and ledger.csv. Paper text and model answers stay in files; the script prints counts.

usage (inside a Claude Science python kernel, so `host` exists):
    import runpy; runpy.run_path("scripts/link_audit_fulltext.py", init_globals={"host": host, "ARGS": [
        "--pairs", PAIRS.parquet, "--meta", META.parquet, "--accessions", ACC.csv, "--out", OUT, "--shard", "0", "--nshards", "12",
        "--budget", "25"]}, run_name="__main__")
"""
import argparse, json, os, re, sys, time, xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd  # noqa: E402
from catalog import models as M  # noqa: E402
from catalog.llm_backend import LLM, BudgetExceeded  # noqa: E402

M.set_host(globals().get("host"))
CACHE = os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache"))
NOTHINK = {"type": "disabled"}
RELS = ["generated", "reused", "unrelated", "unclear"]

SYSTEM = """You decide how ONE paper relates to ONE sequencing project in a public archive (ENA / SRA BioProject).

generated  The paper reports the study that PRODUCED the project's sequencing data: its authors collected or sequenced these
           samples, and the paper describes that work. Evidence that counts: the paper cites the project's accession (or one of
           its run / sample accessions) as the place where the data OF THIS STUDY were deposited; or, without an accession,
           the specific design matches the archive record unmistakably (same cohort or trial name, same place, same sample
           type and numbers, same submitting group and period). A general topical match is not enough.
reused     The paper used the project's data but did not produce it: re-analysis, meta-analysis, a method or database paper
           run on public data, a comparison set. A paper that lists the accession among data sets 'downloaded from' or
           'obtained from' the archive is reused, also when it produced other data itself.
unrelated  The paper does not use the project's data (same topic, another cohort; an accession string that means something else).
unclear    The text given does not let you decide between the options above.

Rules: judge only from the text given. A later paper from the same cohort that analyses the same deposited samples again is
`generated` only if it describes the sampling and sequencing as its own work; a paper that deposited NEW data under a
different accession and cites this project as earlier data is `reused`. `quote` must be copied character for character
from the paper text given (one sentence or part of one, at most 60 words) and must be the passage that decides the verdict;
leave it empty for `unclear`. `basis` is one of: accession_deposit_statement, accession_listed_as_public_data,
design_match, topic_only, no_link, other."""

TOOL = {"name": "link_verdict", "description": "Return the verdict for this paper and project.",
        "input_schema": {"type": "object", "properties": {
            "relation": {"type": "string", "enum": RELS},
            "basis": {"type": "string", "enum": ["accession_deposit_statement", "accession_listed_as_public_data", "design_match",
                                                 "topic_only", "no_link", "other"]},
            "quote": {"type": "string"}, "quote_from": {"type": "string", "enum": ["abstract", "data_availability", "accession_sentence", "title", "none"]},
            "confidence": {"type": "number"}}, "required": ["relation", "basis", "quote", "quote_from"]}}

DAV = re.compile(r"(?i)data availab|availability of data|accession|deposit|data access|data and (code|materials)|data sharing")


def norm(t):
    return re.sub(r"\s+", " ", str(t or "")).strip()


def squash(t):
    return re.sub(r"[^a-z0-9]", "", str(t or "").lower())


def jats_file(pmcid):
    import glob
    if not pmcid or not isinstance(pmcid, str):
        return None
    c = sorted(glob.glob(f"{CACHE}/pmc_oa/{pmcid}.*/{pmcid}.*.xml")) + sorted(glob.glob(f"{CACHE}/pmc_oa/{pmcid}.epmc/{pmcid}.xml"))
    return c[-1] if c else None


def fulltext_context(pmcid, accessions, max_da=2500, max_sent=6):
    """(data-availability text, sentences citing one of the accessions, whole-text accession hit) or (None, [], False)."""
    f = jats_file(pmcid)
    if not f:
        return None, [], False
    try:
        root = ET.parse(f).getroot()
    except ET.ParseError:
        return None, [], False
    txt = lambda e: norm("".join(e.itertext()))
    da = []
    for sec in list(root.iter("sec")) + list(root.iter("notes")):
        t = (sec.findtext("title") or sec.get("sec-type") or sec.get("notes-type") or "")
        if DAV.search(t):
            s = txt(sec)
            if s and not any(s in k for k in da):
                da.append(s)
    whole = txt(root)
    sents, hit = [], False
    for acc in accessions:
        for m in re.finditer(re.escape(acc), whole):
            hit = True
            a_, b_ = max(0, whole.rfind(". ", 0, m.start()) + 2), whole.find(". ", m.end())
            s = whole[a_: (b_ + 1 if b_ > 0 else m.end() + 200)][:600]
            if s not in sents:
                sents.append(s)
            if len(sents) >= max_sent:
                break
    return " ".join(da)[:max_da], sents, hit


def abstract_for(pmid, doi):
    import find_papers as FP
    q = f"EXT_ID:{pmid} AND SRC:MED" if pmid else (f'DOI:"{doi}"' if doi else None)
    if not q:
        return ""
    r = FP.epmc(q, ttl_days=365)
    return (r[0].get("abstractText") or "")[:3500] if r else ""


def build_request(proj, paper, accs):
    ab = norm(re.sub(r"<[^>]+>", " ", abstract_for(paper.get("pmid"), paper.get("doi"))))
    da, sents, hit = fulltext_context(paper.get("pmcid"), accs)
    rec = "\n".join(f"{k}: {norm(v)[:600]}" for k, v in proj.items() if v is not None and str(v) not in ("", "nan", "None"))
    body = (f"PROJECT (archive record)\n{rec}\naccessions of the project: {', '.join(accs[:8])}\n\n"
            f"PAPER\npaper_id: {paper['paper_id']}\nTITLE: {norm(paper.get('title'))}\njournal / year: {paper.get('journal')} {paper.get('year')}\n"
            f"ABSTRACT: {ab or '(not available)'}\n")
    if da is not None:
        body += f"DATA AVAILABILITY (from the full text): {da or '(no such section found)'}\n"
        body += "SENTENCES OF THE FULL TEXT THAT CITE AN ACCESSION OF THE PROJECT:\n" + ("\n".join(f"- {s}" for s in sents) if sents else "(none: no accession of the project occurs in the full text)") + "\n"
    else:
        body += "(full text not available: abstract only)\n"
    given = " ".join([norm(paper.get("title")), ab, da or "", " ".join(sents)])
    return {"system": SYSTEM, "prompt": body, "tools": [TOOL], "tool_choice": {"type": "tool", "name": "link_verdict"},
            "max_tokens": 700, "thinking": NOTHINK}, given, ("fulltext" if da is not None else ("abstract" if ab else "title")), hit


def verdict(r):
    if r.get("status") not in ("ok", "cached") or not isinstance(r.get("tool_use"), dict):
        return {"relation": None, "status": r.get("status_original") or r.get("status") or "error"}
    v = r["tool_use"]
    rel = v.get("relation") if v.get("relation") in RELS else "unclear"
    return {"relation": rel, "basis": v.get("basis"), "quote": norm(v.get("quote")), "quote_from": v.get("quote_from"),
            "confidence": v.get("confidence"), "status": "ok", "model": r.get("model")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True); ap.add_argument("--meta", required=True); ap.add_argument("--accessions", default=None)
    ap.add_argument("--out", required=True); ap.add_argument("--shard", type=int, default=0); ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--budget", type=float, default=25.0); ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    pairs = pd.read_parquet(a.pairs)
    pairs = pairs[pairs.shard.astype(int) == a.shard] if "shard" in pairs.columns else pairs.iloc[a.shard::a.nshards]
    if a.limit:
        pairs = pairs.head(a.limit)
    meta = pd.read_parquet(a.meta).drop_duplicates("study_accession").set_index("study_accession")
    extra = {}
    if a.accessions and os.path.exists(a.accessions):
        for p_, x_ in pd.read_csv(a.accessions, dtype=str).dropna().itertuples(index=False):
            extra.setdefault(p_, []).append(x_)
    led = os.path.join(a.out, "ledger.csv")
    first = LLM("rubric", cache_dir=f"{CACHE}/llm/link_ft_first", ledger=led, budget_usd=a.budget * 0.6, pass_name="link_ft_first", max_concurrency=8)
    check = LLM("audit", cache_dir=f"{CACHE}/llm/link_ft_check", ledger=led, budget_usd=a.budget * 0.4, pass_name="link_ft_check", max_concurrency=6)
    reqs, given, modes, hits, rows = [], [], [], [], []
    for r in pairs.itertuples(index=False):
        d = r._asdict()
        acc = d["study_accession"]
        m = meta.loc[acc].to_dict() if acc in meta.index else {}
        accs = [x for x in [acc, m.get("secondary_study_accession")] if isinstance(x, str) and x] + extra.get(acc, [])
        proj = {k: m.get(k) for k in ("study_title", "description_short", "center_name", "first_public_min", "n_samples", "sample_countries",
                                      "sample_body_sites", "instrument_models_top") if k in m}
        q, g, mode, hit = build_request(proj, d, accs)
        reqs.append(q); given.append(g); modes.append(mode); hits.append(hit); rows.append(d)
    prog = os.path.join(a.out, f"progress_{a.shard}.json")
    json.dump({"shard": a.shard, "pairs": len(rows), "stage": "first reading", "time": time.time()}, open(prog, "w"))
    try:
        out1 = first(reqs)
    except BudgetExceeded as e:
        out1 = []
        print("budget:", e)
    v1 = [verdict(x) for x in out1] + [{"relation": None, "status": "not_run"}] * (len(reqs) - len(out1))
    ok_quote = [bool(v.get("quote")) and len(squash(v["quote"])) >= 20 and squash(v["quote"]) in squash(g) for v, g in zip(v1, given)]
    todo = [i for i, v in enumerate(v1) if v.get("relation") == "generated" and ok_quote[i]]
    json.dump({"shard": a.shard, "pairs": len(rows), "stage": "check reading", "first_generated": len(todo), "time": time.time()}, open(prog, "w"))
    try:
        out2 = check([dict(reqs[i], system=SYSTEM + "\n(independent check reading)") for i in todo])
    except BudgetExceeded as e:
        out2 = []
        print("budget:", e)
    v2 = {i: verdict(x) for i, x in zip(todo, out2)}
    res = []
    for i, d in enumerate(rows):
        a1, a2 = v1[i], v2.get(i, {})
        if a1.get("relation") is None:
            rel = a1.get("status") or "error"                       # refused / error / not_run: recorded, never re-sent
        elif a1["relation"] == "generated":
            if not ok_quote[i]:
                rel = "unclear"                                     # no verbatim support: not committed
            elif a2.get("relation") == "generated":
                rel = "generated"
            elif a2.get("relation") is None:
                rel = "unclear"                                     # check refused / not run: not committed
            else:
                rel = "unclear"                                     # the two readings disagree: not committed
        else:
            rel = a1["relation"]
        res.append({"study_accession": d["study_accession"], "paper_id": str(d["paper_id"]), "pmid": d.get("pmid"), "pmcid": d.get("pmcid"),
                    "doi": d.get("doi"), "title": d.get("title"), "journal": d.get("journal"), "year": d.get("year"),
                    "relation": rel, "relation_first": a1.get("relation"), "relation_check": a2.get("relation"),
                    "basis": a1.get("basis"), "quote": a1.get("quote") if ok_quote[i] else None, "quote_verbatim": bool(ok_quote[i]),
                    "quote_source": f"{d['paper_id']} ({a1.get('quote_from')})" if ok_quote[i] else None,
                    "text_mode": modes[i], "accession_in_text": bool(hits[i]), "confidence": a1.get("confidence"),
                    "status": a1.get("status"), "status_check": a2.get("status"), "model_first": a1.get("model"), "model_check": a2.get("model"),
                    "methods": d.get("methods")})
    df = pd.DataFrame(res)
    df.to_parquet(os.path.join(a.out, f"pairs_{a.shard}.parquet"), index=False)
    summ = {"shard": a.shard, "pairs": len(df), "stage": "done", "relation": df.relation.value_counts().to_dict() if len(df) else {},
            "first": first.summary(), "check": check.summary(), "time": time.time()}
    json.dump(summ, open(prog, "w"), default=str)
    print(json.dumps({k: summ[k] for k in ("shard", "pairs", "relation")}, default=str))
    return summ


if __name__ == "__main__":
    main(globals().get("ARGS"))
