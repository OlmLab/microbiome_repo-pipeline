#!/usr/bin/env python
"""check_vanish_urls.py - resolve every URL the VANISH/BloSSUM page needs against the document root.

This sandbox refuses to bind a listening socket, so each URL is resolved the way `python3 -m http.server`
would (document root + path) and checked on disk, with JSON parsed. It proves the paths, not the server.

usage: python tests/check_vanish_urls.py [--root ../../previews/site]
"""
import argparse, json, re, sys
from pathlib import Path
from urllib.parse import urljoin

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=str(Path(__file__).resolve().parents[3] / "previews" / "site_r3"))
ap.add_argument("--page", default="atlas/vanish.html")
a = ap.parse_args()
root, page = Path(a.root), a.page
html = (root / page).read_text()
urls = set()
for m in re.finditer(r'(?:href|src)="([^"]+)"', html):
    u = m.group(1)
    if u.startswith(("http://", "https://", "#", "mailto:", "data:")):
        continue
    urls.add(urljoin("/" + page, u).split("#")[0])
meta_url = urljoin("/" + page, re.search(r"dataDir:\'([^\']+)\'", html).group(1) + "meta.json")
# round 3: a click goes to the taxon explorer, taxaHref + 'index.html#taxon=<name>'
taxa_href = urljoin("/" + page, re.search(r"taxaHref:\'([^\']+)\'", html).group(1) + "index.html")
urls |= {meta_url, taxa_href}
meta = json.loads((root / meta_url.lstrip("/")).read_text())
base = meta_url.rsplit("/", 1)[0] + "/"
for f in meta["files"] + meta["downloads"]:
    urls.add(base + f)
bad, n_json = [], 0
for u in sorted(urls):
    p = root / u.lstrip("/")
    if not p.exists():
        bad.append((u, "missing"))
        continue
    if p.suffix == ".json":
        try:
            json.loads(p.read_text())
            n_json += 1
        except Exception as e:
            bad.append((u, "bad JSON: %s" % e))
print("resolved mode (no socket): %d URLs for %s, %d JSON files parsed" % (len(urls), page, n_json))
for u, why in bad:
    print("  NOT OK", u, why)
print("OK" if not bad else "FAILED")
sys.exit(1 if bad else 0)
