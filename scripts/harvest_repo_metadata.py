#!/usr/bin/env python3
"""harvest_repo_metadata.py — metadata tables in the repositories a paper's data-availability statement names (R2026.41).

Input: a CSV of (study_accession, url) links found in data-availability statements (scripts scan them with a URL pattern).
Recognised: GitHub repositories, Zenodo records (DOI 10.5281/zenodo.N or a zenodo.org record URL), figshare items
(DOI 10.6084/m9.figshare.N). For each, the file listing is read through the host's public API and files that look like
per-sample tables are downloaded: extension csv / tsv / txt / xlsx / xls, at most 15 MB, and a name that suggests metadata
(metadata, sample, mapping, phenotype, clinical, subject, participant, demographic, cohort, manifest, design, covariate,
characteristic, patient, supp, table); at most 15 files per repository, shallowest paths first.
The files of one repository are zipped to ~/catalog/cache/supp/REPO_<key>.zip, which scripts/extract_supp_rows.tables_of
reads exactly like a paper's supplementary package, so the join ladder and the rest of stage 13 run unchanged.

Output: --out links.csv (study_accession, repo_key, kind, ref, n_files_listed, n_files_taken, status) and
--pmcids pmcids.csv (study_accession, pmcids = ';'-joined repo keys) for join_ladder_audit.py.
No model calls. GitHub requests use GITHUB_TOKEN when it is set (rate limit).
"""
import argparse, hashlib, io, json, os, re, sys, time, zipfile
import pandas as pd, requests

CACHE = os.path.expanduser("~/catalog/cache")
NAME = re.compile(r"(?i)meta|sample|mapping|pheno|clinic|subject|participant|demograph|cohort|manifest|design|covariat|characterist|patient|supp|table")
EXT = (".csv", ".tsv", ".txt", ".xlsx", ".xls")
SKIP = re.compile(r"(?i)(^|/)(node_modules|\.git|envs?|site-packages|vendor|test|tests|docs?/_build)/|readme|license|requirements|changelog|otu|taxonom|abundance|counts?[_.]|profile|kraken|metaphlan|humann|pathway|gene|blast|\.fa|\.fastq|log\.txt")
MAXB, MAXF = 15_000_000, 15
S = requests.Session()


def gh(url, raw=False):
    h = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        h["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    return S.get(url, headers=h, timeout=90)


def pick(files):
    """files: [(path, size)] -> the ones to take."""
    c = [(p, s) for p, s in files if p.lower().endswith(EXT) and (s or 0) <= MAXB and (s or 1) > 200 and NAME.search(os.path.basename(p)) and not SKIP.search(p)]
    c.sort(key=lambda x: (x[0].count("/"), len(x[0])))
    return c[:MAXF]


def parse(url):
    u = url.strip().rstrip(".,;)")
    m = re.search(r"(?i)github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", u)
    if m and m.group(1).lower() not in ("orgs", "topics", "about", "features"):
        return "github", f"{m.group(1)}/{re.sub(r'(?i)\.git$', '', m.group(2))}"
    m = re.search(r"(?i)zenodo\.(\d+)", u) or re.search(r"(?i)zenodo\.org/records?/(\d+)", u)
    if m:
        return "zenodo", m.group(1)
    m = re.search(r"(?i)figshare\.(\d+)", u) or re.search(r"(?i)figshare\.com/.*?/(\d{6,})", u)
    if m:
        return "figshare", m.group(1)
    return None, None


def fetch(kind, ref):
    """-> (listed, [(name, bytes)], status)"""
    try:
        if kind == "github":
            r = gh(f"https://api.github.com/repos/{ref}")
            if r.status_code != 200:
                return 0, [], f"repo http {r.status_code}"
            br = r.json().get("default_branch", "main")
            t = gh(f"https://api.github.com/repos/{ref}/git/trees/{br}?recursive=1")
            if t.status_code != 200:
                return 0, [], f"tree http {t.status_code}"
            files = [(x["path"], x.get("size")) for x in t.json().get("tree", []) if x.get("type") == "blob"]
            got = []
            for p, s in pick(files):
                c = gh(f"https://api.github.com/repos/{ref}/contents/{requests.utils.quote(p)}?ref={br}", raw=True)
                if c.status_code == 200 and c.content:
                    got.append((p.replace("/", "__"), c.content))
                time.sleep(0.2)
            return len(files), got, "ok"
        if kind == "zenodo":
            r = S.get(f"https://zenodo.org/api/records/{ref}", timeout=90)
            if r.status_code != 200:
                return 0, [], f"record http {r.status_code}"
            fl = r.json().get("files") or []
            files = [(f.get("key") or f.get("filename"), f.get("size")) for f in fl]
            link = {(f.get("key") or f.get("filename")): (f.get("links") or {}).get("self") or (f.get("links") or {}).get("download") for f in fl}
            got = []
            for p, s in pick(files):
                if link.get(p):
                    c = S.get(link[p], timeout=180)
                    if c.status_code == 200 and c.content:
                        got.append((p.replace("/", "__"), c.content))
            return len(files), got, "ok"
        if kind == "figshare":
            r = S.get(f"https://api.figshare.com/v2/articles/{ref}", timeout=90)
            if r.status_code != 200:
                return 0, [], f"article http {r.status_code}"
            fl = r.json().get("files") or []
            files = [(f.get("name"), f.get("size")) for f in fl]
            link = {f.get("name"): f.get("download_url") for f in fl}
            got = []
            for p, s in pick(files):
                c = S.get(link[p], timeout=180)
                if c.status_code == 200 and c.content:
                    got.append((p.replace("/", "__"), c.content))
            return len(files), got, "ok"
    except Exception as e:  # noqa: BLE001 — a blocked or failing host is recorded, not retried here
        return 0, [], f"error {type(e).__name__}: {str(e)[:80]}"
    return 0, [], "unknown kind"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", required=True); ap.add_argument("--out", required=True); ap.add_argument("--pmcids", required=True)
    a = ap.parse_args()
    L = pd.read_csv(a.links, dtype=str).dropna(subset=["url"])
    L = L[L.url.str.len() > 0]
    L["kind"], L["ref"] = zip(*L.url.map(parse))
    L = L.dropna(subset=["kind"]).drop_duplicates(["study_accession", "kind", "ref"])
    done = pd.read_csv(a.out, dtype=str) if os.path.exists(a.out) else pd.DataFrame(columns=["study_accession", "repo_key", "kind", "ref", "n_files_listed", "n_files_taken", "status"])
    seen = {(r.kind, r.ref): r for r in done.itertuples()}
    rows = done.to_dict("records")
    have = set(zip(done.study_accession, done.kind, done.ref))
    for i, r in enumerate(L.itertuples()):
        if (r.study_accession, r.kind, r.ref) in have:
            continue
        key = "REPO_" + r.kind[:2] + "_" + hashlib.sha1(f"{r.kind}:{r.ref}".encode()).hexdigest()[:12]
        if (r.kind, r.ref) in seen:
            s = seen[(r.kind, r.ref)]
            rows.append(dict(study_accession=r.study_accession, repo_key=key, kind=r.kind, ref=r.ref, n_files_listed=s.n_files_listed, n_files_taken=s.n_files_taken, status=s.status))
            continue
        listed, got, status = fetch(r.kind, r.ref)
        if got:
            with zipfile.ZipFile(f"{CACHE}/supp/{key}.zip", "w", zipfile.ZIP_DEFLATED) as z:
                for n, b in got:
                    z.writestr(n, b)
        row = dict(study_accession=r.study_accession, repo_key=key, kind=r.kind, ref=r.ref, n_files_listed=listed, n_files_taken=len(got), status=status)
        rows.append(row)
        seen[(r.kind, r.ref)] = pd.Series(row)
        if i % 20 == 0:
            pd.DataFrame(rows).to_csv(a.out, index=False)
            print(i, len(L), flush=True)
    d = pd.DataFrame(rows)
    d.to_csv(a.out, index=False)
    t = d[d.n_files_taken.astype(int) > 0]
    t.groupby("study_accession").repo_key.agg(lambda s: ";".join(sorted(set(s)))).rename("pmcids").reset_index().to_csv(a.pmcids, index=False)
    print(json.dumps({"links": len(d), "projects": int(d.study_accession.nunique()), "by_kind_status": d.groupby(["kind", "status"]).size().rename(str).to_dict().__repr__()[:900],
                      "repos_with_files": int(t.repo_key.nunique()), "projects_with_files": int(t.study_accession.nunique()), "files": int(t.drop_duplicates("repo_key").n_files_taken.astype(int).sum())}))


if __name__ == "__main__":
    main()
