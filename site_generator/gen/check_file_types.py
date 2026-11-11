#!/usr/bin/env python
"""Release gate for the CU Boulder server (R2026.37): it refuses a whole release if the site holds a symlink, a file with no
extension, or a file type outside its allow-list, and then keeps serving the previous release (visible only in its logs).
Fail here instead, before anything is tagged. `.github/` and `.git/` are repository plumbing, not served files."""
import json, sys
from pathlib import Path

ALLOWED = {'html', 'gz', 'parquet', 'csv', 'json', 'png', 'md', 'js', 'bin', 'txt', 'css', 'ico', 'xml', 'svg', 'ipynb', 'nojekyll'}
SKIP_DIRS = {'.git', '.github'}

site = Path(sys.argv[1] if len(sys.argv) > 1 else 'site')
bad_type, no_ext, links, n = [], [], [], 0
for p in sorted(site.rglob('*')):
    rel = p.relative_to(site)
    if rel.parts and rel.parts[0] in SKIP_DIRS:
        continue
    if p.is_symlink():
        links.append(str(rel)); continue
    if not p.is_file():
        continue
    n += 1
    name = p.name
    ext = name[1:].lower() if (name.startswith('.') and name.count('.') == 1) else (name.rsplit('.', 1)[1].lower() if '.' in name else '')
    if not ext:
        no_ext.append(str(rel))
    elif ext not in ALLOWED:
        bad_type.append(str(rel))
report = dict(files=n, symlinks=len(links), no_extension=len(no_ext), disallowed_type=len(bad_type),
              examples=(links + no_ext + bad_type)[:20], allowed=sorted(ALLOWED))
print(json.dumps(report, indent=1))
sys.exit(1 if (links or no_ext or bad_type) else 0)
