#!/usr/bin/env python
"""ingest_contributions.py — GitHub Issues labelled `contribution` (form catalog-contribution.yml) → audit/contributions/<issue>/
(MATURITY_PLAN §3.4 step 1, zero-backend variant §3.3). Deterministic; no LLM; nothing uploaded is ever executed.

    python -m catalog.contribute.ingest_contributions --repo OlmLab/microbiome_repo --package data/inputs/data_package \\
        --out audit/contributions [--since 2026-10-01T00:00:00Z] [--comment] [--from-json issues.json]

Per issue: parse the form body (`### <field id>` blocks), find attachment URLs (github.com/user-attachments/files/…,
github.com/user-attachments/assets/…, user-images.githubusercontent.com), download each into <out>/<issue>/ and write
manifest.json (issue number, url, sha256, size, uploader login, created_at, declared study_accession, contribution_type,
source, licence_ok, note; NEVER e-mail addresses). Gates (config/contribute.yaml ingest): size <= 50 MB, extension in the
allow-list (.csv .tsv .txt .xlsx .xls); executables/archives are rejected without being downloaded.

Joinability check (deterministic): every column whose values look like identifiers is matched EXACTLY against the declared
study's own keys from the package — run accessions (runs.parquet), sample_key / secondary_sample / sample_title /
biosample_accession (sample_metadata_wide) and library names (runs.library_name). Verdict per table:
  accepted_for_review    >= max(min_matches, match_fraction * rows) values of ONE column match ONE key type
  duplicate_of_existing  the file sha256 already exists under <out>/ (another issue)
  unjoinable             no column reaches the threshold (top-3 candidate columns + observed ID form are reported)
  rejected               wrong extension / too large / not readable as a table / no attachment
Outputs per issue: manifest.json + report.json + REPORT.md; with --comment the verdict is posted as an Issue comment.

Auth: GITHUB_TOKEN (or GH_TOKEN) in the environment; used only as the Authorization header to api.github.com, never printed.
Without a token the anonymous API is used (60 req/h; --comment then fails).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
CFG_PATH = Path(os.environ.get('CATALOG_CONFIG_DIR', REPO_ROOT / 'config')) / 'contribute.yaml'
HEADING = re.compile(r'^###\s+(.+?)\s*$', re.M)
ATTACH_RE = re.compile(r'https://(?:github\.com/user-attachments/(?:files|assets)/[^\s)"\'>\]]+|user-images\.githubusercontent\.com/[^\s)"\'>\]]+)')
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
ID_FORMS = [  # (name, regex) — the form with the most matches names the observed ID form
    ('run_accession', re.compile(r'^[SED]RR\d{5,}$')), ('biosample', re.compile(r'^SAM(?:N|EA|D)\d{5,}$')),
    ('secondary_sample', re.compile(r'^[SED]RS\d{5,}$')), ('experiment', re.compile(r'^[SED]RX\d{5,}$')),
    ('bioproject', re.compile(r'^PRJ(?:NA|EB|DB)\d+$')), ('subject_timepoint', re.compile(r'^[A-Za-z]{1,4}\d{1,4}[_\-.]?[A-Za-z]{0,3}\d{0,4}$')),
    ('numeric', re.compile(r'^\d+$')), ('alnum_code', re.compile(r'^[A-Za-z0-9][A-Za-z0-9_\-.]{1,40}$')),
]
VERDICTS = ('accepted_for_review', 'duplicate_of_existing', 'unjoinable', 'rejected')
KEY_TYPES = ('run_accession', 'sample_key', 'secondary_sample', 'sample_title', 'library_name', 'biosample_accession')


def load_cfg(path=CFG_PATH):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8'))


def parse_form_body(body: str, field_ids, aliases=None) -> dict:
    """`### label` blocks → {field_id: value}; `_No response_` → ''. `aliases` maps a human label prefix (share-metadata.yml) to a field id."""
    out = {}
    if not body:
        return out
    parts = HEADING.split(body)
    for i in range(1, len(parts) - 1, 2):
        label, value = parts[i].strip(), parts[i + 1].strip()
        fid = next((f for f in field_ids if label == f or label.startswith(f + ' ') or label.startswith(f + '(')), None)
        if fid is None and aliases:
            fid = next((v for k, v in aliases.items() if label.startswith(k)), None)
        if fid is None:
            continue
        out[fid] = '' if value in ('_No response_', 'None', 'n/a') else value
    return out


def find_attachments(body: str):
    return sorted(dict.fromkeys(ATTACH_RE.findall(body or '')))


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def gh_headers(token=None):
    h = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    return h


def fetch_issues(repo, label, since=None, token=None, state='open'):
    import requests
    out, page = [], 1
    while True:
        params = {'labels': label, 'state': state, 'per_page': 100, 'page': page}
        if since:
            params['since'] = since
        r = requests.get(f'https://api.github.com/repos/{repo}/issues', headers=gh_headers(token), params=params, timeout=60)
        r.raise_for_status()
        batch = r.json()
        out.extend(i for i in batch if 'pull_request' not in i)
        if len(batch) < 100:
            break
        page += 1
    return out


def default_downloader(url, token=None, max_bytes=None):
    """→ bytes. github.com/user-attachments redirects to a signed objects URL; the token is never sent to attachments."""
    import requests
    with requests.get(url, timeout=300, stream=True, allow_redirects=True) as r:
        r.raise_for_status()
        buf, n = io.BytesIO(), 0
        for chunk in r.iter_content(1 << 20):
            n += len(chunk)
            if max_bytes and n > max_bytes:
                raise ValueError(f'attachment exceeds {max_bytes} bytes')
            buf.write(chunk)
    return buf.getvalue()


def ext_of(url_or_name: str) -> str:
    name = url_or_name.split('?')[0].rstrip('/').rsplit('/', 1)[-1]
    return ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''


def read_tables(data: bytes, ext: str) -> dict:
    """→ {sheet_name: DataFrame} (all sheets for xlsx/xls; one for delimited text). Everything read as str."""
    if ext in ('.xlsx', '.xls'):
        return {str(k): v.astype(str).replace('nan', '') for k, v in pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=str).items()}
    text = data.decode('utf-8-sig', errors='replace')
    sep = '\t' if ext == '.tsv' or (text.count('\t') > text.count(',')) else ','
    return {'table': pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)}


def study_keys(pkg: Path, acc: str) -> dict:
    """Key type → set of exact strings for the declared study (empty sets when the study is not in the package)."""
    keys = {k: set() for k in KEY_TYPES}
    runs = pd.read_parquet(pkg / 'runs.parquet', columns=['study_accession', 'run_accession', 'library_name', 'sample_accession', 'secondary_sample_accession'])
    runs = runs[runs.study_accession == acc]
    keys['run_accession'] = set(runs.run_accession.dropna().astype(str))
    keys['library_name'] = {v for v in runs.library_name.dropna().astype(str) if v}
    keys['secondary_sample'] |= set(runs.secondary_sample_accession.dropna().astype(str))
    keys['biosample_accession'] |= set(runs.sample_accession.dropna().astype(str))
    sw = pd.read_parquet(pkg / 'sample_metadata_wide.parquet', columns=['study_accession', 'sample_key', 'secondary_sample', 'sample_title', 'biosample_accession'])
    sw = sw[sw.study_accession == acc]
    for c in ('sample_key', 'secondary_sample', 'sample_title', 'biosample_accession'):
        keys[c] |= {v for v in sw[c].dropna().astype(str) if v}
    return keys


def looks_like_ids(vals) -> bool:
    """Identifier candidate: >= 2 non-empty values, >= 50 % unique, >= 80 % short space-free tokens."""
    v = [x for x in vals if x]
    if len(v) < 2:
        return False
    uniq = len(set(v)) / len(v)
    tok = sum(1 for x in v if len(x) <= 60 and ' ' not in x.strip()) / len(v)
    return uniq >= 0.5 and tok >= 0.8


def id_form(vals) -> str:
    v = [x for x in vals if x][:200]
    if not v:
        return 'empty'
    best = max(ID_FORMS, key=lambda nf: sum(1 for x in v if nf[1].match(x)))
    n = sum(1 for x in v if best[1].match(x))
    return best[0] if n / len(v) >= 0.6 else 'free_text'


def joinability(df: pd.DataFrame, keys: dict, min_matches: int, match_fraction: float) -> dict:
    n_rows = int(len(df))
    threshold = max(int(min_matches), -(-int(round(match_fraction * n_rows * 1000)) // 1000))  # ceil(match_fraction * rows)
    cands = []
    for col in df.columns:
        vals = [str(x).strip() for x in df[col].tolist()]
        if not looks_like_ids(vals):
            continue
        hit = False
        for kt in KEY_TYPES:
            ks = keys.get(kt) or set()
            m = sum(1 for x in vals if x in ks) if ks else 0
            if m:
                cands.append(dict(column=str(col), key_type=kt, matches=m, form=id_form(vals)))
                hit = True
        if not hit:
            cands.append(dict(column=str(col), key_type=None, matches=0, form=id_form(vals)))
    cands.sort(key=lambda c: (-c['matches'], c['form'] in ('numeric', 'free_text', 'empty'), c['column'], c['key_type'] or ''))  # ties: named-id forms before numeric/free text
    best = cands[0] if cands else None
    ok = bool(best and best['key_type'] and best['matches'] >= threshold)
    return dict(n_rows=n_rows, n_cols=int(df.shape[1]), threshold=threshold, joinable=ok, best=best, top3=cands[:3], columns=[str(c) for c in df.columns][:60])


def check_file(name: str, data, cfg_ing: dict, seen_sha: dict, keys: dict, err: str | None = None) -> dict:
    """Verdict for one attachment (pure function; the tests call it directly)."""
    ext = ext_of(name)
    rec = dict(name=name.split('?')[0].rsplit('/', 1)[-1], ext=ext, size=None, sha256=None, verdict=None, reason=None, tables={})
    if ext in cfg_ing['rejected_extensions'] or ext not in cfg_ing['allowed_extensions']:
        rec.update(verdict='rejected', reason=f'extension {ext or "(none)"} not in the allow-list {cfg_ing["allowed_extensions"]}')
        return rec
    if err or data is None:
        rec.update(verdict='rejected', reason=f'download failed: {err or "no data"}')
        return rec
    rec['size'] = len(data)
    if len(data) > int(cfg_ing['max_bytes']):
        rec.update(verdict='rejected', reason=f'{len(data)} bytes > {cfg_ing["max_bytes"]} (50 MB limit)')
        return rec
    rec['sha256'] = sha256_bytes(data)
    if rec['sha256'] in seen_sha:
        rec.update(verdict='duplicate_of_existing', reason=f'same sha256 already ingested from issue #{seen_sha[rec["sha256"]]}')
        return rec
    try:
        tables = read_tables(data, ext)
    except Exception as e:  # noqa: BLE001 — any parse failure is a rejection, never a crash
        rec.update(verdict='rejected', reason=f'not readable as a table: {type(e).__name__}: {str(e)[:120]}')
        return rec
    if not tables or all(len(t) == 0 for t in tables.values()):
        rec.update(verdict='rejected', reason='no table rows')
        return rec
    joinable = False
    for sheet, df in tables.items():
        j = joinability(df, keys, cfg_ing['min_matches'], cfg_ing['match_fraction'])
        rec['tables'][sheet] = j
        joinable = joinable or j['joinable']
    if joinable:
        rec.update(verdict='accepted_for_review', reason="an identifier column matches the declared study's own keys")
    else:
        rec.update(verdict='unjoinable', reason="no column reaches the match threshold against the declared study's run accessions, sample keys, "
                                                 "secondary sample accessions, sample titles, BioSamples or library names")
    return rec


def scan_seen(out_dir: Path) -> dict:
    seen = {}
    for m in sorted(out_dir.glob('*/manifest.json')):
        try:
            man = json.loads(m.read_text())
        except ValueError:
            continue
        for f in man.get('files', []):
            if f.get('sha256'):
                seen.setdefault(f['sha256'], man.get('issue_number'))
    return seen


def render_report(rep: dict) -> str:
    L = [f"# Contribution issue #{rep['issue_number']} — {rep['verdict']}", '',
         f"study `{rep['study_accession'] or '?'}` · type `{rep['contribution_type'] or '?'}` · source `{rep['source'] or '?'}` · "
         f"licence {'confirmed' if rep['licence_ok'] else 'NOT confirmed'} · opened {rep['created_at']} by `{rep['uploader']}`", '']
    if rep['study_in_package'] is False:
        L.append('> The declared study has no runs or samples in the package, so the joinability check had no keys to match against.')
    if not rep['files']:
        L.append('No attachment found in the issue body (drag-and-drop the CSV/TSV/XLSX into the note field).')
    for f in rep['files']:
        L.append(f"## `{f['name']}` — **{f['verdict']}**")
        L.append(f"{f['reason']}" + (f" · {f['size']} bytes · sha256 `{f['sha256'][:16]}…`" if f.get('sha256') else ''))
        for sheet, j in (f.get('tables') or {}).items():
            L.append(f"* sheet `{sheet}`: {j['n_rows']} rows × {j['n_cols']} columns; threshold {j['threshold']} matches")
            for c in j['top3']:
                L.append(f"  * column `{c['column']}`: {c['matches']} match(es) against {c['key_type'] or 'no key type'}; observed ID form `{c['form']}`")
            if not j['joinable'] and j['top3']:
                L.append(f"  * ids look like `{j['top3'][0]['form']}` — a key file mapping them to run accessions (SRR/ERR/DRR) or BioSamples (SAMN/SAMEA) is needed")
    L += ['', f"_Verdict rules: {', '.join(VERDICTS)} — src/catalog/contribute/ingest_contributions.py; nothing is published without Curator review._"]
    return '\n'.join(L) + '\n'


def process_issue(issue: dict, pkg: Path, out_dir: Path, cfg: dict, downloader=default_downloader, token=None, seen_sha=None) -> dict:
    ing, form_ids = cfg['ingest'], cfg['issue_form']['field_ids']
    n = int(issue['number'])
    body = issue.get('body') or ''
    form = parse_form_body(body, form_ids, cfg['issue_form'].get('label_aliases'))
    acc = (form.get('study_accession') or '').strip().upper()
    if not acc:
        m = re.search(r'\[(?:contribution|metadata)\]\s*([^:\s]+)', issue.get('title') or '')
        acc = m.group(1).upper() if m else ''
    licence_ok = bool(re.search(r'\[x\]', form.get('licence', ''), re.I))
    note = EMAIL_RE.sub('[e-mail removed]', form.get('note', ''))[:2000]
    idir = out_dir / str(n)
    idir.mkdir(parents=True, exist_ok=True)
    keys = study_keys(pkg, acc) if acc else {k: set() for k in KEY_TYPES}
    in_pkg = any(keys.values())
    seen_sha = dict(seen_sha or {})
    files = []
    for url in find_attachments(body):
        data, err = None, None
        if ext_of(url) in ing['allowed_extensions']:
            try:
                data = downloader(url, token=token, max_bytes=int(ing['max_bytes']) + 1)
            except Exception as e:  # noqa: BLE001
                err = f'{type(e).__name__}: {str(e)[:120]}'
        rec = check_file(url, data, ing, seen_sha, keys, err=err)
        rec['url'] = url
        if data is not None and rec['verdict'] != 'rejected':
            (idir / rec['name']).write_bytes(data)
            rec['path'] = str((idir / rec['name']).relative_to(out_dir))
            seen_sha.setdefault(rec['sha256'], n)
        files.append(rec)
    if files:
        order = {v: i for i, v in enumerate(VERDICTS)}
        verdict = sorted((f['verdict'] for f in files), key=lambda v: order[v])[0]
    else:
        verdict = 'rejected'
    login = (issue.get('user') or {}).get('login') or ''
    rep = dict(issue_number=n, issue_url=issue.get('html_url'), created_at=issue.get('created_at'), uploader=login, uploader_hash=sha256_bytes(login.encode())[:16],
               study_accession=acc, contribution_type=form.get('contribution_type', ''), source=form.get('source', ''),
               source_url=EMAIL_RE.sub('', form.get('source_url', ''))[:500], licence_ok=licence_ok, note=note, release_tag=form.get('release_tag', ''),
               study_in_package=in_pkg if acc else None, verdict=verdict, files=files, no_attachment=not files)
    assert not EMAIL_RE.search(json.dumps(rep)), 'manifest must not carry an e-mail address'
    (idir / 'manifest.json').write_text(json.dumps(dict(rep, files=[{k: v for k, v in f.items() if k != 'tables'} for f in files]), indent=1, sort_keys=True), encoding='utf-8')
    (idir / 'report.json').write_text(json.dumps(rep, indent=1, sort_keys=True), encoding='utf-8')
    (idir / 'REPORT.md').write_text(render_report(rep), encoding='utf-8')
    return rep


def post_comment(repo, number, body, token):
    import requests
    if not token:
        raise SystemExit('--comment needs GITHUB_TOKEN (Issues write)')
    r = requests.post(f'https://api.github.com/repos/{repo}/issues/{number}/comments', json={'body': body}, headers=gh_headers(token), timeout=60)
    r.raise_for_status()
    return r.json().get('html_url')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--repo', default=None, help='owner/name of the Issues repo (default config/contribute.yaml issue_form.repo)')
    ap.add_argument('--package', required=True, help='unpacked package dir (runs.parquet, sample_metadata_wide.parquet)')
    ap.add_argument('--out', default='audit/contributions')
    ap.add_argument('--since', default=None, help='ISO timestamp; only issues updated at or after it')
    ap.add_argument('--state', default='open', choices=['open', 'closed', 'all'])
    ap.add_argument('--comment', action='store_true', help='post the verdict as an Issue comment (Issues write)')
    ap.add_argument('--from-json', default=None, help='offline: JSON list of issues as returned by the API')
    ap.add_argument('--config', default=str(CFG_PATH))
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    repo = a.repo or cfg['issue_form']['repo']
    issues = json.load(open(a.from_json, encoding='utf-8')) if a.from_json else fetch_issues(repo, cfg['issue_form']['label'], a.since, token, a.state)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen = scan_seen(out_dir)
    summary = []
    for issue in sorted(issues, key=lambda i: int(i['number'])):
        rep = process_issue(issue, Path(a.package), out_dir, cfg, token=token, seen_sha=seen)
        for f in rep['files']:
            if f.get('sha256'):
                seen.setdefault(f['sha256'], rep['issue_number'])
        row = dict(issue=rep['issue_number'], study=rep['study_accession'], type=rep['contribution_type'], verdict=rep['verdict'], n_files=len(rep['files']))
        if a.comment:
            row['comment_url'] = post_comment(repo, rep['issue_number'], render_report(rep), token)
        summary.append(row)
    (out_dir / 'INGEST_SUMMARY.json').write_text(json.dumps(dict(repo=repo, n_issues=len(summary), issues=summary), indent=1, sort_keys=True), encoding='utf-8')
    print(json.dumps(dict(n_issues=len(summary), verdicts={v: sum(1 for s in summary if s['verdict'] == v) for v in VERDICTS}, out=str(out_dir)), sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
