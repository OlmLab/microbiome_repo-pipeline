#!/usr/bin/env python
"""check_urls.py — resolve every URL the replication page needs, without a server.

Each relative href/src of atlas/replication.html, the data file, every download and every project page the
page can link to is resolved the way a static server would (document root + path) and looked up on disk:
first in the preview root, then in the roots given with --also (the built site, and the preview that holds
the taxon explorer and the VANISH page). JSON is parsed. It also lists every absolute host the page's HTML
links to and every absolute host inside the page's own script.

    python tests/check_urls.py --root <preview root> [--also <root> ...]
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit, unquote

ap = argparse.ArgumentParser()
ap.add_argument("--root", required=True)
ap.add_argument("--also", action="append", default=[])
ap.add_argument("--page", default="atlas/replication.html")
a = ap.parse_args()
root, page = Path(a.root), a.page
roots = [root] + [Path(x) for x in a.also]
html = (root / page).read_text(encoding="utf-8")
urls, hosts = set(), set()
for m in re.finditer(r'(?:href|src)="([^"]+)"', html):
    u = m.group(1)
    if u.startswith(("http://", "https://")):
        hosts.add(urlsplit(u).netloc)
    elif not u.startswith(("#", "mailto:", "data:")):
        urls.add(urljoin("/" + page, u).split("#")[0])
data_dir = re.search(r"dataDir:'([^']+)'", html).group(1)
taxa = re.search(r"taxaHref:'([^']+)'", html).group(1)
studies = re.search(r"studiesHref:'([^']+)'", html).group(1)
base = urljoin("/" + page, data_dir)
meta = json.loads((root / (base + "replication.json").lstrip("/")).read_text(encoding="utf-8"))
urls |= {base + "replication.json", urljoin("/" + page, taxa + "index.html")}
urls |= {base + f for f in meta["meta"]["downloads"]}
proj = meta["projects"]["acc"]
urls |= {urljoin("/" + page, studies + p + ".html") for p in proj}
where, bad, n_json = {}, [], 0
for u in sorted(urls):
    rel = unquote(u).lstrip("/")
    hit = next((r for r in roots if (r / rel).is_file()), None)
    if hit is None:
        bad.append(u)
        continue
    where[str(hit)] = where.get(str(hit), 0) + 1
    if u.endswith(".json"):
        json.loads((hit / rel).read_text(encoding="utf-8"))
        n_json += 1
js = (root / "static" / "sp_replication.js").read_text(encoding="utf-8")
js_hosts = sorted({urlsplit(x).netloc for x in re.findall(r"https?://[A-Za-z0-9.\-]+", js)})
print("resolved mode (no socket): %d URLs for %s (%d project pages), %d JSON parsed" % (len(urls), page, len(proj), n_json))
for r, k in where.items():
    print("  %5d found in %s" % (k, r))
print("absolute hosts linked from the HTML (navigation only):", sorted(hosts))
print("absolute hosts inside sp_replication.js:", js_hosts)
for u in bad[:15]:
    print("  NOT FOUND", u)
if len(bad) > 15:
    print("  ... and %d more" % (len(bad) - 15))
print("OK" if not bad else "FAILED: %d not found" % len(bad))
sys.exit(1 if bad else 0)
