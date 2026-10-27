#!/usr/bin/env python
"""fetch_pmc_oa.py — download full text (JATS XML) and every supplementary file of open-access papers (free).

Figures and videos are skipped (only text, tables and documents are read). Source order per PMCID: (1) the PubMed Central open-access bucket on AWS (https://pmc-oa-opendata.s3.amazonaws.com,
fast and complete: JATS + all supplements), (2) Europe PMC fullTextXML (text only) when the bucket has no folder.
Files go to $CATALOG_CACHE_DIR/pmc_oa/<PMCID>.<version>/<file>, the layout the deep / supplementary passes already read.
Existing files are skipped, so the script is safe to re-run (monthly). One CSV row per PMCID in --log records what was
fetched or why not; failures are retried on the next run, per paper (lesson of R2026.28: one failed download must not
hide a paper's supplements forever).

usage: python scripts/fetch_pmc_oa.py --pmcids pmcids.txt --log fetch_log.csv [--workers 6] [--max-file-mb 2000]
"""
import argparse, csv, os, re, time, xml.etree.ElementTree as ET
import concurrent.futures as cf
import requests

ROOT = os.path.join(os.path.expanduser(os.environ.get("CATALOG_CACHE_DIR", "~/catalog/cache")), "pmc_oa")
BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
SKIP_EXT = (".jpg", ".jpeg", ".gif", ".png", ".tif", ".tiff", ".bmp", ".svg", ".mp4", ".avi", ".mov")  # figures and videos are not read


def list_bucket(pmcid):
    r = requests.get(BUCKET, params={"list-type": "2", "prefix": f"{pmcid}."}, timeout=60)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for c in root.findall("s3:Contents", NS):
        key = c.findtext("s3:Key", namespaces=NS)
        size = int(c.findtext("s3:Size", namespaces=NS) or 0)
        out.append((key, size))
    return out


def download(url, path, max_bytes):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return "exists"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with requests.get(url, stream=True, timeout=(15, 60)) as r:
        if r.status_code != 200:
            return f"http_{r.status_code}"
        n = 0
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                n += len(chunk)
                if n > max_bytes:
                    fh.close(); os.remove(tmp)
                    return "too_large"
                fh.write(chunk)
    os.replace(tmp, path)
    return "ok"


def fetch_one(pmcid, max_bytes):
    pmcid = pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"
    t0 = time.time()
    try:
        keys = list_bucket(pmcid)
    except Exception as e:  # noqa: BLE001
        keys, err = [], repr(e)[:120]
    else:
        err = ""
    if keys:
        vers = sorted({k.split("/")[0] for k, _ in keys}, key=lambda v: int(re.sub(r"\D", "", v.split(".")[-1]) or 0))
        latest = vers[-1]
        files = [(k, s) for k, s in keys if k.startswith(latest + "/") and not k.lower().endswith(SKIP_EXT)]
        stats = {"ok": 0, "exists": 0, "too_large": 0, "fail": 0}
        for k, s in files:
            if s > max_bytes:
                stats["too_large"] += 1
                continue
            try:
                st = download(f"{BUCKET}/{k}", os.path.join(ROOT, k), max_bytes)
            except Exception:  # noqa: BLE001
                st = "fail"
            stats[st if st in stats else "fail"] += 1
        has_xml = any(k.endswith(".xml") for k, _ in files)
        status = "bucket_ok" if stats["fail"] == 0 else "bucket_partial"
        return dict(pmcid=pmcid, status=status, folder=latest, n_files=len(files), has_xml=has_xml,
                    mb=round(sum(s for _, s in files) / 1e6, 2), **stats, seconds=round(time.time() - t0, 1), error="")
    # fallback: Europe PMC JATS text only
    path = os.path.join(ROOT, f"{pmcid}.epmc", f"{pmcid}.xml")
    try:
        st = download(f"{EPMC}/{pmcid}/fullTextXML", path, max_bytes)
    except Exception as e:  # noqa: BLE001
        st, err = "fail", repr(e)[:120]
    return dict(pmcid=pmcid, status="epmc_text_only" if st in ("ok", "exists") else "not_available", folder=f"{pmcid}.epmc",
                n_files=1 if st in ("ok", "exists") else 0, has_xml=st in ("ok", "exists"), mb=0, ok=int(st == "ok"),
                exists=int(st == "exists"), too_large=0, fail=int(st not in ("ok", "exists")),
                seconds=round(time.time() - t0, 1), error=err or ("" if st in ("ok", "exists") else st))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pmcids", required=True); ap.add_argument("--log", required=True)
    ap.add_argument("--workers", type=int, default=6); ap.add_argument("--max-file-mb", type=float, default=2000)
    a = ap.parse_args(argv)
    ids = list(dict.fromkeys(l.strip() for l in open(a.pmcids) if l.strip()))
    done = set()
    if os.path.exists(a.log):
        done = {r["pmcid"] for r in csv.DictReader(open(a.log)) if r["status"] in ("bucket_ok", "epmc_text_only")}
    todo = [i for i in ids if (i if i.startswith("PMC") else f"PMC{i}") not in done]
    print(f"{len(ids)} PMCIDs, {len(todo)} to fetch -> {ROOT}", flush=True)
    F = ["pmcid", "status", "folder", "n_files", "has_xml", "mb", "ok", "exists", "too_large", "fail", "seconds", "error"]
    new = not os.path.exists(a.log)
    fh = open(a.log, "a", newline=""); w = csv.DictWriter(fh, fieldnames=F)
    if new:
        w.writeheader()
    n = 0
    with cf.ThreadPoolExecutor(a.workers) as ex:
        for res in ex.map(lambda p: fetch_one(p, int(a.max_file_mb * 1e6)), todo):
            w.writerow(res); n += 1
            if n % 200 == 0:
                fh.flush(); print(n, flush=True)
    fh.close(); print("done", n, flush=True)


if __name__ == "__main__":
    main()
