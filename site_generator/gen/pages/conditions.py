"""Insights > Condition explorer: ONE searchable page over every health condition the catalog holds cases for,
built from the three CSV tables of the conditions payload (``data/inputs/site/conditions/``).

``build(render, ctx, out_dir)`` copies the three tables to ``<out_dir>/data/conditions/`` and renders
``atlas/conditions.html``. With no payload it renders the placeholder, so the site build never breaks on it.

What this module writes into the built site
-------------------------------------------
    atlas/conditions.html                          the explorer (it contains NO list of conditions)
    data/conditions/condition_explorer.csv          one row per condition id — drives the main table
    data/conditions/condition_explorer_projects.csv one row per condition and project — the project list
    data/conditions/condition_explorer_associations.csv the replicated published associations
and needs these static files, copied by the site generator's static step:
    static/sp_core.js  static/side_pages.css  static/sp_conditions.js  static/sp_conditions.css
The page reads nothing else: the table, the search, the sort and every condition's section are built in the
browser from those three files (static/sp_conditions.js). No chart image is shipped and no script is fetched
from another host; the ontology links are plain links to OLS (www.ebi.ac.uk, a link-only host in
config/site.yaml hosting.js_link_only_hosts).

Every number the page prints about the whole table is computed here from the tables, not typed: the number of
conditions, case samples, projects, countries, tested conditions, testable and replicated claims, and the
counts of the two detail tables.

ctx: conditions_dir = the payload directory; package, release_id = the package the site is built from.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

CRUMBS = [dict(label="Home", href="../index.html"), dict(label="Insights", href="taxa/index.html"),
          dict(label="Condition explorer")]
DATA = "../data/conditions/"      # the payload, as seen from atlas/conditions.html
FILES = ("condition_explorer.csv", "condition_explorer_projects.csv", "condition_explorer_associations.csv")
# Figures the three tables cannot carry, because they count LABEL STRINGS rather than conditions, and the two
# ontology versions the ids were read against. scripts/condition_manifest.py counts the two label-string figures on
# the released package and writes conditions_manifest.json beside the tables (`make conditions-page`); the values
# below are only the fallback for a payload without that file, and are the counts reported for package 1.34.0.
DEFAULTS = dict(efo_version="3.96.0", mondo_release="2026-11-03", run_date="2026-11-15",
                labels_without_term=154, labels_unsettled=51)


def _rows(path: Path) -> list:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _int(v) -> int:
    try:
        return int(str(v or "0").strip() or 0)
    except ValueError:
        return 0


def build(render, ctx, out_dir):
    src = Path(ctx["conditions_dir"])
    out_dir = Path(out_dir)
    if not all((src / n).exists() for n in FILES):
        render("conditions_missing.html", "atlas/conditions.html", "../", nav="atlas", sub="conditions", crumbs=CRUMBS)
        return dict(page="atlas/conditions.html", placeholder=True)

    dst = out_dir / "data" / "conditions"
    dst.mkdir(parents=True, exist_ok=True)     # files are overwritten in place; nothing is deleted here
    for name in FILES:
        shutil.copyfile(src / name, dst / name)
    cond, proj, assoc = (_rows(src / n) for n in FILES)

    M = dict(DEFAULTS)
    man = src / "conditions_manifest.json"
    if man.exists():
        M.update({k: v for k, v in json.loads(man.read_text(encoding="utf-8")).items() if k in DEFAULTS})
    tested = [r for r in cond if (r.get("replication_scope") or "") == "this condition"]
    M.update(
        n_conditions=len(cond),
        n_cases=sum(_int(r.get("cases")) for r in cond),
        n_projects=len({r["study_accession"] for r in proj if r.get("study_accession")}),
        n_project_rows=len(proj),
        n_countries=len({c.split(":")[0] for r in cond for c in (r.get("countries") or "").split(";") if c}),
        n_tested=len(tested),
        claims_testable=sum(_int(r.get("replication_claims_testable")) for r in tested),
        claims_replicated=sum(_int(r.get("replication_claims_replicated")) for r in tested),
        n_broader=sum(1 for r in cond if (r.get("replication_scope") or "") == "broader condition"),
        # F05 / F06: rows that carry a replication result but no cases of their own, and rows whose term is a
        # phenotype or a finding rather than a disease — both are stated on the page, both counted here.
        n_no_cases=sum(1 for r in cond if _int(r.get("cases")) == 0),
        n_phenotype=sum(1 for r in cond if (r.get("term_branch") or "") not in ("disease", "")),
        has_manifest=man.exists(),
        n_associations=len(assoc),
        n_assoc_conditions=len({r["condition_id"] for r in assoc if r.get("condition_id")}),
        package=ctx.get("package"), release_id=ctx.get("release_id"),
        files=list(FILES),
    )
    render("conditions_explorer.html", "atlas/conditions.html", "../", nav="atlas", sub="conditions",
           M=M, data=DATA, crumbs=CRUMBS)
    return dict(page="atlas/conditions.html", n_conditions=M["n_conditions"], n_cases=M["n_cases"],
                n_projects=M["n_projects"], n_tested=M["n_tested"], n_associations=M["n_associations"],
                bytes=sum((dst / n).stat().st_size for n in FILES))
