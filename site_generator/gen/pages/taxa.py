"""Atlas > Taxon explorer: ONE interactive page for every genus and species in the community profiles,
from the payload written by scripts/taxon_explorer.py (tree.bin, stats.bin, projects.bin, lit/, manifest.json).

``build(render, ctx, out_dir)`` copies the payload to ``<out_dir>/data/taxa/`` and renders a single page,
``atlas/taxa/index.html``. With no payload it renders the placeholder, so the site build never breaks on it.

What this module writes into the built site
-------------------------------------------
    atlas/taxa/index.html          the explorer (about 8 KB; it contains NO list of taxa)
    data/taxa/tree.bin             2.41 MB   the pruned GTDB tree, its names and the taxon table
    data/taxa/stats.bin            2.99 MB   every group of every taxon
    data/taxa/projects.bin         0.79 MB   optional; fetched only when that section is opened
    data/taxa/manifest.json        the counts of the build
    data/taxa/lit/<slug>.json      387 files, 0.94 MB, fetched one at a time on demand
and needs these static files, copied by the site generator's static step:
    static/sp_core.js  static/sp_taxdata.js  static/sp_explorer.js  static/side_pages.css
    static/vendor/d3-7.9.0.min.js  static/vendor/topojson-client-3.1.0.min.js
    static/vendor/countries-110m.json

What round 2 produced and this module does NOT
----------------------------------------------
    atlas/taxa/<slug>.html         the 721 per-taxon pages            — replaced by the panel on this page
    data/taxa/<slug>.json          their per-taxon payload (6.5 MB)   — replaced by stats.bin
    data/taxa/index.json, countries.json, csv/*                       — replaced by tree.bin + stats.bin
    static/sp_taxa.js                                                 — replaced by static/sp_explorer.js
    templates/taxon.html, templates/taxa_index.html                   — replaced by taxa_explorer.html
The 387 literature files are the same bytes as round 2's; if both page sets were ever built, they would be
the same files in a different folder.

ctx: taxa_dir = the payload directory.
"""
from __future__ import annotations

import json
import shutil
import struct
from pathlib import Path

CRUMBS = [dict(label="Home", href="../../index.html"), dict(label="Insights")]
DATA = "../../data/taxa/"     # the payload, as seen from atlas/taxa/index.html


def _bin_header(path: Path) -> dict:
    """The JSON header of one of the payload's .bin files (magic, version, header length, header)."""
    with open(path, "rb") as fh:
        head = fh.read(10)
        if len(head) < 10:
            return {}
        hlen = struct.unpack("<I", head[6:10])[0]
        return json.loads(fh.read(hlen).decode("utf-8"))


def build(render, ctx, out_dir):
    src = Path(ctx["taxa_dir"])
    out_dir = Path(out_dir)
    crumbs = CRUMBS + [dict(label="Taxon explorer")]
    if not (src / "tree.bin").exists() or not (src / "manifest.json").exists():
        render("taxa_missing.html", "atlas/taxa/index.html", "../../", nav="atlas", sub="taxa", crumbs=crumbs)
        return dict(page="atlas/taxa/index.html", placeholder=True)

    dst = out_dir / "data" / "taxa"
    dst.mkdir(parents=True, exist_ok=True)     # files are overwritten in place; nothing is deleted here
    copied = []
    for name in ("tree.bin", "stats.bin", "projects.bin", "manifest.json"):
        if (src / name).exists():
            shutil.copyfile(src / name, dst / name)
            copied.append(name)
    if (src / "lit").is_dir():
        shutil.copytree(src / "lit", dst / "lit", dirs_exist_ok=True)
    # round 4: which sample carries which taxon (about 24 MB). Only index.bin and the shards it names are
    # copied, so a shard left over from an earlier, larger build never reaches the site.
    pres = src / "presence"
    has_presence = (pres / "index.bin").exists()
    if has_presence:
        (dst / "presence").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pres / "index.bin", dst / "presence" / "index.bin")
        for k in range(int(_bin_header(pres / "index.bin").get("n_shards", 0))):
            shutil.copyfile(pres / ("p%03d.bin" % k), dst / "presence" / ("p%03d.bin" % k))
        copied.append("presence/")

    man = json.loads((src / "manifest.json").read_text())
    stats_header = _bin_header(src / "stats.bin") if (src / "stats.bin").exists() else {}
    M = dict(man)
    M["gtdb"] = man.get("gtdb", "R232")
    M["min_cell_samples"] = stats_header.get("min_cell_samples", 30)
    M["min_cell_projects"] = stats_header.get("min_cell_projects", 2)
    M["has_projects"] = (dst / "projects.bin").exists()
    M["has_presence"] = has_presence
    render("taxa_explorer.html", "atlas/taxa/index.html", "../../", nav="atlas", sub="taxa",
           M=M, data=DATA, crumbs=crumbs)
    return dict(page="atlas/taxa/index.html", n_taxa=man["n_taxa"], n_genera=man["n_genera"],
                n_species=man["n_species"], n_literature=man["n_literature"],
                files=copied + (["lit/"] if (dst / "lit").is_dir() else []),
                package_version=man.get("package_version"))
