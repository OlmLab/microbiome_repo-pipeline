#!/usr/bin/env python
"""find_papers.py — candidate paper links for a list of projects (free; no model calls). R2026.30 large update + monthly runs.

For every project accession (and its secondary SRP/ERP/DRP accession) it collects candidate papers from four sources:
  epmc_accession   Europe PMC search  ACCESSION_ID:"<acc>" OR "<acc>"  (text-mined accessions in abstracts + OA full text)
  bioproject_xml   NCBI BioProject <Publication> records (PRJNA / PRJDB / PRJEB all resolve through esearch)
  ena_xref         ENA study XML  XREF_LINK DB=PUBMED
  title_exact      Europe PMC TITLE:"<project title>" with the normalised titles equal (only for specific titles, >= 6 words)
Paper metadata (PMID, PMCID, DOI, title, journal, year, type, open access) comes from Europe PMC. The output is a set of
CANDIDATE links; whether a paper generated the data or only re-used it is decided later (link audit), never here.

usage: python scripts/find_papers.py --projects ids.txt --meta registry_studies.parquet [--meta s4_universe.parquet ...]
                                     --out links.parquet [--workers 6]
Responses are cached as JSON under $CATALOG_CACHE_DIR/find_papers/ so a re-run (monthly) only asks for what is new.
"""
import argparse, hashlib, json, os, re, sys, time, xml.etree.ElementTree as ET
import concurrent.futures as cf
import pandas as pd
import requests

CACHE = os.path.join(os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache")), "find_papers")
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
GENERIC = re.compile(r"(?i)^(human|homo sapiens)?\s*(gut|oral|skin|vaginal|lung|stool|fecal|faecal|feces|nasopharyngeal|saliva|blood)?"
                     r"\s*(metagenom\w*|microbiom\w*|metatranscriptom\w*|raw sequence reads|sequencing)\s*(raw sequence reads|project|data)?$")
_ncbi_lock_t = [0.0]


def _get(url, params=None, ttl_days=None, ncbi=False):
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    path = os.path.join(CACHE, key[:2], key)
    if os.path.exists(path) and (ttl_days is None or time.time() - os.path.getmtime(path) < ttl_days * 86400):
        return open(path, encoding="utf-8").read()
    for attempt in range(4):
        if ncbi:                                    # NCBI allows ~3 requests/s without a key
            wait = 0.34 - (time.time() - _ncbi_lock_t[0])
            if wait > 0:
                time.sleep(wait)
            _ncbi_lock_t[0] = time.time()
        try:
            r = requests.get(url, params=params, timeout=90)
            if r.status_code == 200:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w", encoding="utf-8").write(r.text)
                return r.text
            if r.status_code in (404, 400):
                return None
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    return None


def norm_title(t):
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", str(t or "").lower())).strip()


def epmc(query, ttl_days=30):
    txt = _get(EPMC, {"query": query, "format": "json", "pageSize": 100, "resultType": "core"}, ttl_days=ttl_days)
    if not txt:
        return []
    try:
        return json.loads(txt).get("resultList", {}).get("result", [])
    except ValueError:
        return []


def paper_row(r):
    return {"pmid": r.get("pmid"), "pmcid": r.get("pmcid"), "doi": r.get("doi"), "title": r.get("title"),
            "journal": (r.get("journalInfo") or {}).get("journal", {}).get("title") or r.get("bookOrReportDetails", {}).get("publisher"),
            "year": r.get("pubYear"), "pub_type": ";".join((r.get("pubTypeList") or {}).get("pubType", []))[:200],
            "is_open_access": r.get("isOpenAccess") == "Y", "in_epmc_fulltext": r.get("inEPMC") == "Y", "source_db": r.get("source")}


def by_accession(acc, sec):
    q = " OR ".join(f'ACCESSION_ID:"{a}" OR "{a}"' for a in [acc, sec] if a)
    return [dict(paper_row(r), method="epmc_accession", match_accession=acc) for r in epmc(f"({q})")]


def by_title(acc, title):
    t = str(title or "").strip()
    if len(t.split()) < 6 or GENERIC.match(t):
        return []
    hits = epmc(f'TITLE:"{t[:250]}"')
    nt = norm_title(t)
    return [dict(paper_row(r), method="title_exact", match_accession=acc) for r in hits if norm_title(r.get("title")) == nt]


STOP = set("a an the of in on and or for with from to by at as is are was were be this that these those study analysis analyses "
           "sequencing sequence sequences metagenome metagenomic metagenomics shotgun human patients patient samples sample raw reads "
           "data project microbiome microbiota microbial gut fecal faecal stool using based".split())


def _words(t):
    return [w for w in norm_title(t).split() if w not in STOP and len(w) > 2 and not w.startswith("prj")]


def by_title_similar(acc, title, desc=None, year=None, k=5):
    """Fuzzy candidates for projects with no direct link: Crossref bibliographic search on the project title and
    description (top k each, published from the year before the project). Only CANDIDATES (method title_similar):
    on 10 known hard cases the right paper was the top hit for 3; the link audit reads the abstracts and decides."""
    out = []
    for t in [title, desc]:
        t = str(t or "").strip()
        if len(t.split()) < 4 or GENERIC.match(t):
            continue
        prm = {"query.bibliographic": t[:300], "rows": k, "select": "DOI,title"}
        if year:
            prm["filter"] = f"from-pub-date:{int(year) - 1}"
        txt = _get("https://api.crossref.org/works", prm, ttl_days=60)
        try:
            items = json.loads(txt)["message"]["items"] if txt else []
        except (ValueError, KeyError):
            items = []
        for rank, h in enumerate(items):
            if h.get("DOI"):
                out.append({"doi": h["DOI"].lower(), "title": (h.get("title") or [None])[0], "method": "title_similar",
                            "match_accession": acc, "candidate_rank": rank})
    return out


def pmids_meta(ids):
    """PMIDs and DOIs -> Europe PMC metadata, keyed by the id given."""
    out = {}
    pmids = [p for p in ids if p and p.isdigit()]
    dois = [p for p in ids if p and not p.isdigit()]
    for i in range(0, len(pmids), 50):
        q = " OR ".join(f"EXT_ID:{p}" for p in pmids[i:i + 50])
        for r in epmc(f"({q}) AND SRC:MED"):
            out[r.get("pmid")] = paper_row(r)
    for i in range(0, len(dois), 25):
        q = " OR ".join(f'DOI:"{d}"' for d in dois[i:i + 25])
        for r in epmc(f"({q})"):
            if r.get("doi"):
                out[r["doi"].lower()] = paper_row(r)
    return {k: v for k, v in out.items()} | {d: out.get(d.lower()) for d in dois if out.get(d.lower())}


def bioproject_pubs(accs):
    """PRJ* -> PMIDs from NCBI BioProject XML (all three INSDC prefixes are indexed there)."""
    out = {}
    for i in range(0, len(accs), 100):
        chunk = accs[i:i + 100]
        term = " OR ".join(f"{a}[Project Accession]" for a in chunk)
        t = _get(f"{EUTILS}/esearch.fcgi", {"db": "bioproject", "term": term, "retmax": 500, "retmode": "json"}, ttl_days=30, ncbi=True)
        try:
            ids = json.loads(t)["esearchresult"]["idlist"] if t else []
        except (ValueError, KeyError):
            ids = []
        if not ids:
            continue
        x = _get(f"{EUTILS}/efetch.fcgi", {"db": "bioproject", "id": ",".join(ids), "retmode": "xml"}, ttl_days=30, ncbi=True)
        if not x:
            continue
        try:
            root = ET.fromstring(x)
        except ET.ParseError:
            continue
        for doc in root.iter("DocumentSummary"):
            a = doc.find(".//ArchiveID")
            acc = a.get("accession") if a is not None else None
            for pub in doc.iter("Publication"):
                pid = pub.get("id")
                db = (pub.findtext("DbType") or "").lower()
                if acc and pid and (pid.isdigit() or pid.startswith("10.")):    # PMID, or a DOI (DbType eDOI)
                    out.setdefault(acc, set()).add(pid.strip())
    return out


def ena_xrefs(accs):
    out = {}
    for i in range(0, len(accs), 50):
        chunk = [a for a in accs[i:i + 50] if a.startswith(("PRJEB", "PRJDB", "ERP", "DRP"))]
        if not chunk:
            continue
        x = _get("https://www.ebi.ac.uk/ena/browser/api/xml/" + ",".join(chunk), ttl_days=30)
        if not x:
            continue
        try:
            root = ET.fromstring(x)
        except ET.ParseError:
            continue
        for node in list(root):
            acc = node.get("accession")
            for xr in node.iter("XREF_LINK"):
                if (xr.findtext("DB") or "").upper() in ("PUBMED", "MEDLINE") and (xr.findtext("ID") or "").isdigit():
                    out.setdefault(acc, set()).add(xr.findtext("ID"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", required=True); ap.add_argument("--meta", action="append", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    proj = [l.strip() for l in open(a.projects) if l.strip()]
    meta = pd.concat([pd.read_parquet(m, columns=[c for c in ["study_accession", "secondary_study_accession", "study_title",
                                                                 "description_short", "first_public_min"]
                                                   if c in pd.read_parquet(m).columns]) for m in a.meta]).drop_duplicates("study_accession")
    meta = meta.set_index("study_accession").reindex(proj)
    sec = meta.get("secondary_study_accession", pd.Series(index=meta.index, dtype=object))
    title = meta.get("study_title", pd.Series(index=meta.index, dtype=object))
    desc = meta.get("description_short", pd.Series(index=meta.index, dtype=object))
    year = pd.to_datetime(meta.get("first_public_min", pd.Series(index=meta.index, dtype=object)), errors="coerce").dt.year

    def one(p):
        s_ = sec.get(p) if isinstance(sec.get(p), str) else None
        got = by_accession(p, s_) + by_title(p, title.get(p))
        if not got:                                    # fuzzy title search only when nothing more direct was found
            y = year.get(p)
            got = by_title_similar(p, title.get(p), desc.get(p), None if pd.isna(y) else y)
        return got
    rows = []
    t0 = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(one, p): p for p in proj}
        for n, f in enumerate(cf.as_completed(futs), 1):
            for r in f.result():
                rows.append(dict(r, study_accession=futs[f]))
            if n % 500 == 0:
                print(f"epmc {n}/{len(proj)} {time.time() - t0:.0f}s", flush=True)
    bp = bioproject_pubs(proj)
    ex_ = ena_xrefs(proj + [s for s in sec.dropna() if isinstance(s, str)])
    secmap = {s: p for p, s in sec.dropna().items()}
    xref = {}
    for d, m in ((bp, "bioproject_xml"), (ex_, "ena_xref")):
        for acc, ids in d.items():
            p = acc if acc in meta.index else secmap.get(acc)
            if p:
                for i in ids:
                    xref.setdefault((p, i), set()).add(m)
    md = pmids_meta(sorted({i for (_, i) in xref}))
    for (p, i), ms in xref.items():
        base = md.get(i) or ({"pmid": i} if i.isdigit() else {"doi": i})
        for m in ms:
            rows.append(dict(base, method=m, match_accession=p, study_accession=p))
    df = pd.DataFrame(rows)
    if len(df) and "candidate_rank" in df:
        dois = sorted(set(df.loc[df.method.eq("title_similar"), "doi"].dropna()))
        dm = pmids_meta(dois)
        for c in ["pmid", "pmcid", "journal", "year", "pub_type", "is_open_access", "in_epmc_fulltext"]:
            m = df.method.eq("title_similar")
            df.loc[m, c] = df.loc[m, "doi"].map(lambda d: (dm.get(d) or {}).get(c))
    if len(df):
        df["paper_id"] = df.pmid.fillna(df.pmcid).fillna(df.doi)
        df = df.dropna(subset=["paper_id"])
        g = df.groupby(["study_accession", "paper_id"])
        first = g.first().reset_index()
        first["methods"] = g.method.apply(lambda s: ";".join(sorted(set(s)))).values
        first["n_projects_for_paper"] = first.groupby("paper_id").study_accession.transform("nunique")
        df = first.drop(columns=["method"])
    df.to_parquet(a.out, index=False)
    print(f"done: {len(proj)} projects, {df.study_accession.nunique() if len(df) else 0} with >=1 candidate paper, "
          f"{len(df)} links, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
