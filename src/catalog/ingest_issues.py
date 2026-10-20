#!/usr/bin/env python
"""ingest_issues.py — GitHub Issues (label `finding`, issue-form body) → audit/findings/YYYY-MM-DD_issues.csv (F2 / R1-09).

Parses the issue-form body GitHub renders as `### <label>\n\n<value>` blocks; headings are matched to schema fields by
their `id` (the label text starts with the id — see findings_schema.render_template). `date` = issue.created_at (UTC
date), `source` = `issue#<number>`; both are never typed by hand. Issues already present in an earlier
audit/findings/*_issues.csv (same source) are skipped unless --all.

    python -m catalog.ingest_issues --repo OlmLab/microbiome_repo --out audit/findings [--state open] [--since 2026-09-01]
    python -m catalog.ingest_issues --from-json issues.json --out audit/findings     # offline: a saved API response

Auth: GITHUB_TOKEN or GH_TOKEN in the environment (fine-grained PAT, issues:read). In a Claude kernel declare the
credential on the cell; the token is used only as the Authorization header, never printed. No token → unauthenticated
API (60 requests/h) is tried.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from catalog import findings_schema as FS  # noqa: E402

SCHEMA = FS.load()
COLS = FS.finding_cols(SCHEMA) + FS.struct_cols(SCHEMA)
FORM_IDS = ["identifier", "finding_type", "action", "sample_key", "field_name", "current_state", "proposed_change", "new_value",
            "reason_code", "evidence_quote", "evidence_source", "confidence", "route", "release_tag"]
HEADING = re.compile(r"^###\s+(.+?)\s*$", re.M)


def parse_form_body(body: str) -> dict:
    """`### label` blocks → {field_id: value}. A dropdown left unselected renders `_No response_` → ''."""
    out = {}
    if not body:
        return out
    parts = HEADING.split(body)
    # parts = [preamble, label1, value1, label2, value2, ...]
    for i in range(1, len(parts) - 1, 2):
        label, value = parts[i].strip(), parts[i + 1].strip()
        fid = next((f for f in FORM_IDS if label == f or label.startswith(f + " ") or label.startswith(f + "(")), None)
        if fid is None:
            continue
        if value in ("_No response_", "None", "n/a"):
            value = ""
        out[fid] = value
    return out


# R2026.26 (intake test): the site's two-question form (site_generator/gen/issue_templates/simple-finding.yml) and the Authors
# page link use human labels, not schema ids; map them so a site report keeps its accession and text.
SIMPLE_LABELS = [("What is wrong or missing", "current_state"), ("Tell us more", "evidence_quote"), ("accession", "identifier"),
                 ("release id", "release_tag"), ("page URL", "evidence_source")]


def parse_simple_form(body: str) -> dict:
    out = {}
    parts = HEADING.split(body or "")
    for i in range(1, len(parts) - 1, 2):
        label, value = parts[i].strip(), parts[i + 1].strip()
        fid = next((f for k, f in SIMPLE_LABELS if label.startswith(k)), None)
        if fid and value not in ("_No response_", "None", "n/a"):
            out[fid] = value
    return out


def issue_to_row(issue: dict) -> dict:
    form = parse_form_body(issue.get("body") or "")
    if not form.get("finding_type"):
        simple = parse_simple_form(issue.get("body") or "")
        if simple:
            form = {**simple, **{k: v for k, v in form.items() if v}}
            names = {l.get("name") for l in issue.get("labels", []) if isinstance(l, dict)}
            form.setdefault("finding_type", "other")
            kind = "authorship report (Authors page)" if ("authorship" in names or "[authorship]" in (issue.get("title") or "")) else "site report (two-question form)"
            form["proposed_change"] = form.get("proposed_change") or f"curator to review — {kind}"
    row = {c: "" for c in COLS}
    row.update({k: v for k, v in form.items() if k in row})
    row["date"] = (issue.get("created_at") or "")[:10]
    row["source"] = f"issue#{issue.get('number')}"
    if not row["identifier"]:
        m = re.search(r"\[finding\]\s*([^:]+):", issue.get("title") or "")
        row["identifier"] = m.group(1).strip() if m else ""
    if row["finding_type"] in SCHEMA["issue_form"]["evidence_optional_for"] and not row["confidence"]:
        row["confidence"] = ""
    return row


def fetch_issues(repo: str, state: str, since: str | None, label: str) -> list[dict]:
    import requests

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out, page = [], 1
    while True:
        params = {"labels": label, "state": state, "per_page": 100, "page": page}
        if since:
            params["since"] = since + "T00:00:00Z" if len(since) == 10 else since
        r = requests.get(f"https://api.github.com/repos/{repo}/issues", headers=headers, params=params, timeout=60)
        r.raise_for_status()
        batch = [i for i in r.json() if "pull_request" not in i]
        out.extend(batch)
        if len(r.json()) < 100:
            break
        page += 1
    return out


def already_ingested(out_dir: str) -> set[str]:
    seen = set()
    for f in glob.glob(os.path.join(out_dir, "*_issues.csv")):
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r.get("source"):
                    seen.add(r["source"])
    return seen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=None, help="owner/name of the Issues repo (config/site.yaml github.issues.repo)")
    ap.add_argument("--from-json", default=None, help="offline: JSON list of issues as returned by the API")
    ap.add_argument("--out", default="audit/findings")
    ap.add_argument("--state", default="open", choices=["open", "closed", "all"])
    ap.add_argument("--since", default=None, help="YYYY-MM-DD")
    ap.add_argument("--label", default=SCHEMA["issue_form"]["labels"][0])
    ap.add_argument("--all", action="store_true", help="include issues already present in earlier *_issues.csv files")
    ap.add_argument("--date", default=None, help="file date (default today)")
    a = ap.parse_args(argv)
    if a.from_json:
        issues = json.load(open(a.from_json, encoding="utf-8"))
    elif a.repo:
        issues = fetch_issues(a.repo, a.state, a.since, a.label)
    else:
        ap.error("--repo or --from-json required")
    seen = set() if a.all else already_ingested(a.out)
    rows = [issue_to_row(i) for i in issues]
    rows = [r for r in rows if r["source"] not in seen]
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"{a.date or dt.date.today().isoformat()}_issues.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["source"]):
            w.writerow(r)
    n_struct = sum(1 for r in rows if r["action"])
    print(json.dumps({"issues": len(issues), "written": len(rows), "skipped_seen": len(issues) - len(rows), "with_action": n_struct, "file": path}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
