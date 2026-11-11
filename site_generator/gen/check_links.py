#!/usr/bin/env python
"""Crawl site/: every internal href/src (and fetch() target) must resolve to a file; fragments must exist; no root-absolute paths."""
import re, sys, json
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote

site = Path(sys.argv[1] if len(sys.argv) > 1 else 'site')


class P(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.ids = set()
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for k in ('href', 'src'):
            if d.get(k): self.links.append(d[k])
        if d.get('id'): self.ids.add(d['id'])


site = site.resolve()
pages = sorted(site.rglob('*.html'))
ids = {}
parsed = {}
for pg in pages:
    p = P(); p.feed(pg.read_text(encoding='utf-8')); parsed[pg] = p; ids[pg] = p.ids
broken, absolute, external, internal, frag_bad = [], [], 0, 0, []
for pg, p in parsed.items():
    links = list(p.links) + re.findall(r"fetch\('([^']+)'\)", pg.read_text(encoding='utf-8'))
    for href in links:
        u = urlsplit(href)
        if u.scheme in ('http', 'https', 'mailto'):
            external += 1; continue
        root_page = pg == site / '404.html'   # served for missing addresses at any depth, so its links are root-relative by design
        if href.startswith('/') and not root_page:
            absolute.append((str(pg.relative_to(site)), href)); continue
        internal += 1
        path = unquote(u.path)
        target = pg if not path else ((site / path.lstrip('/')) if (root_page and path.startswith('/')) else (pg.parent / path)).resolve()
        if not target.exists() or not target.is_file():
            broken.append((str(pg.relative_to(site)), href)); continue
        if u.fragment and target.suffix == '.html' and u.fragment not in ids.get(target, set()):
            frag_bad.append((str(pg.relative_to(site)), href))
# explorer.js referenced data paths
js = (site / 'static' / 'explorer.js').read_text()
for rel in re.findall(r"'(\.\./data/[^']+)'", js) + re.findall(r'"(\.\./data/[^"]+)"', js):
    if not (site / 'samples' / rel).resolve().exists():
        broken.append(('static/explorer.js (from samples/)', rel))
cfg = re.findall(r"(?:parquet|det|vh|sdall|topgen|csvgz):'(\.\./data/[^']+)'", (site / 'samples' / 'index.html').read_text())
for rel in cfg:
    if not (site / 'samples' / rel).resolve().exists():
        broken.append(('samples/index.html EXPLORER_CFG', rel))
report = dict(pages=len(pages), internal_links=internal, external_links=external, broken=len(broken), root_absolute=len(absolute),
              bad_fragments=len(frag_bad), nojekyll=(site / '.nojekyll').exists(), index=(site / 'index.html').exists(),
              examples_broken=broken[:20], examples_absolute=absolute[:10], examples_bad_fragments=frag_bad[:10])
print(json.dumps(report, indent=1))
sys.exit(1 if (broken or absolute or frag_bad) else 0)
