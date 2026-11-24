"""Atlas > Replication of published findings: one interactive page over the results of the pre-registered
replication test (package 1.34.0), from the payload written by ``replication_page.py`` (replication.json,
four CSV downloads, the frozen plan and registration, the run report).

``build(render, ctx, out_dir)`` copies the payload to ``<out_dir>/data/replication/`` and renders
``atlas/replication.html``. With no payload it renders the placeholder, so the site build never breaks on it.

The graphic is drawn in the browser (static/sp_core.js + static/sp_replication.js, d3 from the vendored,
SRI-pinned file) from ONE file, data/replication/replication.json. No chart image is shipped. The HTML carries
the headline, what it depends on, the method, the limits, the sensitivity table and the download links, so a
reader without JavaScript still gets those.

The payload is a RESULT, not something to rebuild per release: it belongs to the package that was under test
and stays as it is when the catalog moves on.

ctx: replication_dir = the payload directory.
Files this page needs in <site>/static/: side_pages.css and sp_core.js (shared, from the taxon explorer),
sp_replication.js and sp_replication.css (this folder's static/).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

CRUMBS = [dict(label="Home", href="../index.html"), dict(label="Insights"),
          dict(label="Replication of published findings")]
DATA = "../data/replication/"      # payload, as seen from atlas/replication.html


def build(render, ctx, out_dir):
    src = Path(ctx["replication_dir"])
    out_dir = Path(out_dir)
    if not (src / "replication.json").exists():
        render("atlas_replication_missing.html", "atlas/replication.html", "../", nav="atlas", sub="replication",
               crumbs=CRUMBS)
        return dict(page="atlas/replication.html", placeholder=True)
    M = json.loads((src / "replication.json").read_text(encoding="utf-8"))["meta"]
    dst = out_dir / "data" / "replication"
    dst.mkdir(parents=True, exist_ok=True)
    # copy exactly the files the page uses: the one data file and the downloads it links to
    names = ["replication.json"] + list(M["downloads"])
    for name in names:
        shutil.copyfile(src / name, dst / name)
    render("atlas_replication.html", "atlas/replication.html", "../", nav="atlas", sub="replication",
           M=M, data=DATA, crumbs=CRUMBS)
    return dict(page="atlas/replication.html", files=len(names), bytes=sum((dst / n).stat().st_size for n in names),
                package=M["package"], replicated=M["replicated"], testable=M["testable"])
