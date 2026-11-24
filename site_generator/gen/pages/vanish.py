"""Insights > VANISH and BloSSUM taxa: one interactive plot of every tested taxon, from the payload written
by ``scripts/vanish_blossum.py --stage page`` into data/inputs/site/vanish (meta.json,
points_<level>_<stratum>_<rule>.json, the CSV downloads, METHOD_v2.md).

``build(render, ctx, out_dir)`` copies the payload to ``<out_dir>/data/vanish/`` and renders
``atlas/vanish.html``. With no payload it renders the placeholder, so the site build never breaks on it.

The plot is drawn in the browser (static/sp_core.js + static/sp_vanish.js, d3 from the vendored, SRI-pinned
file). No chart image is shipped. The HTML carries the groups, the method in words, the checks and the
download links, so a reader without JavaScript still gets those.

The page also carries the response to a published critique, in two optional payload files written by
``scripts/vanish_robustness.py``. The page renders in all three states:
  neither file            exactly as before;
  robustness_sentences.json only   the six plain-language sentences are shown and the Robustness section
                          says the three quick checks are not computed, still listing the three long ones;
  both files              the sentences, and the three checks with their tables and CSV downloads.
The CSVs of the checks are named in robustness.json (``csv_files``), so this module copies exactly the
files the page uses and never guesses a name; as before it deletes nothing.

ctx: vanish_dir = the payload directory.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

CRUMBS = [dict(label="Home", href="../index.html"), dict(label="Insights", href="taxa/index.html"),
          dict(label="VANISH / BloSSUM")]
DATA = "../data/vanish/"      # payload, as seen from the page
SENTENCES = "robustness_sentences.json"
ROBUSTNESS = "robustness.json"


def build(render, ctx, out_dir):
    src = Path(ctx["vanish_dir"])
    out_dir = Path(out_dir)
    if not (src / "meta.json").exists():      # build without the payload: keep the Atlas nav link resolvable
        render("atlas_vanish_missing.html", "atlas/vanish.html", "../", nav="atlas", sub="vanish",
               crumbs=CRUMBS)
        return dict(page="atlas/vanish.html", placeholder=True)
    dst = out_dir / "data" / "vanish"
    dst.mkdir(parents=True, exist_ok=True)
    M = json.loads((src / "meta.json").read_text())
    # the critique response, when the robustness stages have been run: the sentences carry the numbers the
    # page prints, robustness.json the summaries of the three quick checks and the names of their CSVs
    S = json.loads((src / SENTENCES).read_text()) if (src / SENTENCES).exists() else None
    R = json.loads((src / ROBUSTNESS).read_text()) if (src / ROBUSTNESS).exists() else None
    extra = ([SENTENCES] if S else []) + ([ROBUSTNESS] if R else [])
    if R:
        extra += [f for f in R.get("csv_files", []) if (src / f).exists()]
    # copy exactly the files the page uses (meta.json, the point files, the downloads): nothing else from the
    # payload folder reaches the site, and nothing is deleted here (the site build starts from an empty tree)
    for name in ["meta.json"] + list(M["files"]) + list(M["downloads"]) + extra:
        shutil.copyfile(src / name, dst / name)
    render("atlas_vanish.html", "atlas/vanish.html", "../", nav="atlas", sub="vanish",
           M=M, S=S, R=R, data=DATA, crumbs=CRUMBS)
    n_v = sum(b.get("n_vanish", 0) for f in [src / x for x in M["files"] if x.endswith("_model.json")]
              for b in json.loads(f.read_text())["blocks"].values() if b.get("contrast") == "C1")
    # the simple rule also carries the StIL candidate class; the model rule's is pending
    n_s = sum(b.get("n_stil", 0) for f in [src / x for x in M["files"] if x.endswith("_simple.json")]
              for b in json.loads(f.read_text())["blocks"].values())
    return dict(page="atlas/vanish.html", n_point_files=len(M["files"]), n_vanish_c1=n_v,
                n_stil_simple=n_s,
                n_stil_model_pending=(M.get("stil") or {}).get("pending_model_rule_total", 0),
                package_version=M.get("package_version"),
                sentences=bool(S), robustness_checks=bool(R), robustness_files=extra)
