#!/usr/bin/env python
"""ingest_verifications.py — GitHub Issues titled '[verified] …' or labelled `metadata-verified` (form issue_templates/verify-metadata.yml) →
config/human_verified.csv, which sets the per-project "Human verified" source flag (catalog.scopes.study_sources).

A verification counts when the issue body names a project accession, both checkboxes of the form are ticked, and the
issue is not labelled `invalid` / `wontfix` (maintainers reject a check by adding one of those labels). One row per
(project, GitHub user); re-running is idempotent.

    GITHUB_TOKEN=... python scripts/ingest_verifications.py --repo OlmLab/microbiome_repo [--out config/human_verified.csv]
"""
import argparse
import csv
import os
import re
import sys

import requests

ACC = re.compile(r"\bPRJ(?:NA|EB|DB)\d+\b")
TICK = re.compile(r"- \[[xX]\]")


def fetch(repo, token):
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    out, page = [], 1
    while True:
        r = requests.get(f"https://api.github.com/repos/{repo}/issues", headers=h, timeout=60,
                         params={"state": "all", "per_page": 100, "page": page})
        r.raise_for_status()
        batch = [i for i in r.json() if "pull_request" not in i and ((i.get("title") or "").startswith("[verified]")
                 or "metadata-verified" in {l["name"] for l in i.get("labels", [])})]
        out += batch
        if len(r.json()) < 100:
            return out
        page += 1


def rows_from_issues(issues):
    rows = {}
    for i in issues:
        labels = {l["name"] for l in i.get("labels", [])}
        if labels & {"invalid", "wontfix"}:
            continue
        body = i.get("body") or ""
        m = ACC.search(body) or ACC.search(i.get("title") or "")
        if not m or len(TICK.findall(body)) < 2:
            continue
        user = (i.get("user") or {}).get("login", "")
        rows[(m.group(0), user)] = dict(study_accession=m.group(0), verified_by=user, date=(i.get("created_at") or "")[:10],
                                        issue=str(i["number"]), note="")
    return sorted(rows.values(), key=lambda r: (r["study_accession"], r["date"]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="OlmLab/microbiome_repo")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "config", "human_verified.csv"))
    a = ap.parse_args(argv)
    rows = rows_from_issues(fetch(a.repo, os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")))
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["study_accession", "verified_by", "date", "issue", "note"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} verifications → {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
