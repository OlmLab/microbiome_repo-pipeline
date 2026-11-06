"""Atlas › Profile measures (R2026.36): the four live measures on the Sandpiper join, precomputed by
scripts/sandpiper_measures.py into data/inputs/site/measures (measures.json, <name>.png / <name>_web.png, CSVs).

``build(render, ctx, out_dir)`` copies the payload to ``<out_dir>/data/measures/`` and renders ``atlas/measures.html``.
Returns None (page not built) when the payload is missing, so the site build never breaks on it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

SITE_LABEL = {"gut_stool": "Gut (stool)", "oral": "Oral", "skin": "Skin", "vaginal_urogenital": "Vaginal / urogenital",
              "nasal_nasopharyngeal": "Nasal / nasopharyngeal", "respiratory_lower": "Lower respiratory", "blood_tissue": "Blood / tissue",
              "eye_ear": "Eye / ear", "milk": "Milk"}


def _pct(x, d=1):
    return f"{100 * float(x):.{d}f} %"


def build(render, ctx, out_dir):
    src = Path(ctx["measures_dir"])
    out_dir = Path(out_dir)
    if not (src / "measures.json").exists():   # build without the payload: keep the Atlas nav link resolvable
        render("atlas_measures_missing.html", "atlas/measures.html", "../", nav="atlas", sub="measures",
               crumbs=[dict(label="Home", href="../index.html"), dict(label="Atlas", href="index.html"), dict(label="Profile measures")])
        return dict(page="atlas/measures.html", placeholder=True)
    study_pages = {q.stem for q in (out_dir / "studies").glob("*.html")}   # link only projects with a page in this build
    dst = out_dir / "data" / "measures"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    M = json.loads((src / "measures.json").read_text())
    cov = M["coverage"]
    n_cur, n_prof = cov["n_current"], cov["n_profiled"]
    reasons = [dict(code=k, label=cov["reason_labels"][k], n=v, share=_pct(v / n_cur)) for k, v in cov["reasons"].items()]
    ag = M["agreement"]
    per_site = [dict(site=SITE_LABEL.get(k, k), share=_pct(v, 0)) for k, v in ag["per_site"].items()]
    kn = M["known"]
    by_site = [dict(site=SITE_LABEL.get(r["body_site_code"], r["body_site_code"]), n=r["n"], median=r["median"]) for r in kn["by_site"]]
    by_site.sort(key=lambda r: -r["median"])
    big = sorted(kn.get("largest", []), key=lambda r: -r["n"])[:10]
    kn_big = dict(lo=min(r["median_known"] for r in big), hi=max(r["median_known"] for r in big), n=len(big),
                  names=", ".join(r["name"] for r in big[:3])) if big else None
    dp = M["depth"]
    spf = [dict(site=SITE_LABEL.get(r["body_site_code"], r["body_site_code"]), n=r["n"], median=r["median"]) for r in dp["spf_by_site"]]
    spf.sort(key=lambda r: -r["median"])
    render("atlas_measures.html", "atlas/measures.html", "../", nav="atlas", sub="measures", use_datatables=True, M=M,
           head=dict(n_current=n_cur, n_profiled=n_prof, share=_pct(n_prof / n_cur)), reasons=reasons, per_site=per_site,
           by_site=by_site, spf=spf, kn_big=kn_big, stats=ctx.get("stats", {}), study_pages=study_pages,
           crumbs=[dict(label="Home", href="../index.html"), dict(label="Atlas", href="index.html"), dict(label="Profile measures")])
    return dict(page="atlas/measures.html", n_current=n_cur, n_profiled=n_prof)
