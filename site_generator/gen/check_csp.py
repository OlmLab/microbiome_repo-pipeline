#!/usr/bin/env python
"""Release gate (R2026.37): would any page load something the CU server's Content-Security-Policy forbids?

The CU server publishes a new site tag without waiting for GitHub's checks, so once BIT enforces the policy a page that loads an
outside resource would break live. This reads every built page and script and checks each resource it loads against
config/site.yaml `hosting.csp`:
  * <script src>, <link rel=stylesheet|preload|modulepreload|icon> href, <img>/<source>/<iframe> src, GoatCounter's endpoint;
  * absolute URLs inside inline scripts and static/*.js: each host must be allowed for scripts or connections, or be listed in
    `hosting.js_link_only_hosts` (hosts we only build <a href> links to);
  * url(...) in CSS files.
Links (<a href>) are navigation, not loads, and are not checked. Exit 1 on any violation.
"""
import json, re, sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import yaml

HERE = Path(__file__).resolve().parent
cfg = yaml.safe_load((HERE.parents[1] / 'config' / 'site.yaml').read_text())['hosting']
site = Path(sys.argv[1] if len(sys.argv) > 1 else 'site').resolve()

policy = {}
for part in cfg['csp'].split(';'):
    toks = part.split()
    if toks:
        policy[toks[0]] = toks[1:]
LINK_ONLY = set(cfg.get('js_link_only_hosts', []))


def sources(directive):
    return policy.get(directive, policy.get('default-src', []))


def allowed(url, directive):
    u = urlsplit(url)
    if not u.scheme and u.netloc:                          # protocol-relative //host/path loads over https
        u = urlsplit('https:' + url)
    if not u.scheme or u.scheme in ('http', 'https') and not u.netloc:
        return "'self'" in sources(directive)            # relative = same origin
    if u.scheme in ('data', 'blob'):
        return f'{u.scheme}:' in sources(directive)
    origin = f'{u.scheme}://{u.netloc}'
    return any(s == origin or s == f'{u.scheme}:' for s in sources(directive))


class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.loads = []; self.inline = []; self._in_script = False
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == 'script':
            if d.get('src'):
                self.loads.append(('script-src', d['src']))
            else:
                self._in_script = True
            if d.get('data-goatcounter'):
                self.loads.append(('connect-src', d['data-goatcounter']))
        elif tag == 'link' and d.get('href'):
            rel = set((d.get('rel') or '').lower().split())
            if 'stylesheet' in rel:
                self.loads.append(('style-src', d['href']))
            elif rel & {'preload', 'modulepreload'}:
                self.loads.append(({'style': 'style-src', 'font': 'font-src', 'image': 'img-src'}.get(d.get('as'), 'script-src'), d['href']))
            elif rel & {'icon', 'apple-touch-icon'}:
                self.loads.append(('img-src', d['href']))
        elif tag in ('img', 'source') and d.get('src'):
            self.loads.append(('img-src', d['src']))
        elif tag == 'iframe' and d.get('src'):
            self.loads.append(('frame-src', d['src']))
    def handle_endtag(self, tag):
        if tag == 'script':
            self._in_script = False
    def handle_data(self, data):
        if self._in_script:
            self.inline.append(data)


ABS = re.compile(r"https?://[A-Za-z0-9.-]+")
violations, n_pages, n_loads, js_hosts = [], 0, 0, {}


def check_script_text(text, where):
    for m in ABS.finditer(text):
        host = urlsplit(m.group(0)).netloc
        js_hosts[host] = js_hosts.get(host, 0) + 1
        if host in LINK_ONLY:
            continue
        if not (allowed(m.group(0), 'script-src') or allowed(m.group(0), 'connect-src')):
            violations.append(dict(where=where, kind='script URL', url=m.group(0)))


for pg in sorted(site.rglob('*.html')):
    rel = str(pg.relative_to(site))
    if rel.startswith(('.git/', '.github/')):
        continue
    p = P(); p.feed(pg.read_text(encoding='utf-8', errors='replace')); n_pages += 1
    for directive, url in p.loads:
        n_loads += 1
        if not allowed(url, directive):
            violations.append(dict(where=rel, kind=directive, url=url))
    check_script_text('\n'.join(p.inline), rel + ' (inline script)')
for js in sorted((site / 'static').rglob('*.js')):
    if '/vendor/' in str(js):
        continue   # pinned third-party libraries: they load nothing themselves
    check_script_text(js.read_text(encoding='utf-8', errors='replace'), str(js.relative_to(site)))
for css in sorted((site / 'static').rglob('*.css')):
    for m in re.finditer(r"url\((['\"]?)(https?://[^)'\"]+)\1\)", css.read_text(encoding='utf-8', errors='replace')):
        if not (allowed(m.group(2), 'img-src') or allowed(m.group(2), 'font-src')):
            violations.append(dict(where=str(css.relative_to(site)), kind='css url()', url=m.group(2)))

seen, uniq = set(), []
for v in violations:
    k = (v['kind'], v['url'])
    if k not in seen:
        seen.add(k); uniq.append(v)
report = dict(pages=n_pages, resource_loads=n_loads, violations=len(violations), distinct_violations=len(uniq),
              examples=uniq[:20], script_hosts=dict(sorted(js_hosts.items(), key=lambda kv: -kv[1])[:15]))
print(json.dumps(report, indent=1))
sys.exit(1 if violations else 0)
