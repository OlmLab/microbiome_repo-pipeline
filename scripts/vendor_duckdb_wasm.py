#!/usr/bin/env python3
"""Make the files that let the site serve DuckDB-WASM itself (R2026.40; owner's request of 2026-11-13).

The explorers load DuckDB-WASM 1.29.0 from cdn.jsdelivr.net and DuckDB then fetches its parquet extension from
extensions.duckdb.org. This script produces a directory with everything those two hosts supply:

  duckdb-browser.bundle.js              the browser module, bundled with esbuild into ONE ES module (apache-arrow inlined)
  duckdb-eh.wasm, duckdb-mvp.wasm       the two single-threaded builds (the threaded "coi" build needs cross-origin isolation
                                        headers the CU server does not send, so it is left out)
  duckdb-browser-{eh,mvp}.worker.js     their workers
  extensions/<duckdb version>/wasm_{eh,mvp}/parquet.duckdb_extension.wasm
  MANIFEST.json                         versions, sources, byte sizes and sha256 of every file

Inputs are pinned in site_generator/vendor_src/duckdb/ (package.json + package-lock.json; `npm ci`). The output directory is
build/vendor_duckdb by default — OUTSIDE the site tree; site_generator/gen/build_site.py copies it into static/vendor/duckdb/
only when hosting.duckdb_self_hosted is true in config/site.yaml. Needs node/npm and network access to registry.npmjs.org and
extensions.duckdb.org.

  python scripts/vendor_duckdb_wasm.py [--out build/vendor_duckdb]
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile
import requests
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / 'site_generator' / 'vendor_src' / 'duckdb'
EXT_HOST = 'https://extensions.duckdb.org'
PLATFORMS = ['wasm_eh', 'wasm_mvp']
DIST_FILES = ['duckdb-eh.wasm', 'duckdb-mvp.wasm', 'duckdb-browser-eh.worker.js', 'duckdb-browser-mvp.worker.js']


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def run(cmd, cwd, env=None):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f'{" ".join(map(str, cmd))} failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}')
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(REPO / 'build' / 'vendor_duckdb'))
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    env = dict(os.environ)
    env.setdefault('npm_config_cache', str(Path(tempfile.gettempdir()) / 'npmcache'))
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for n in ['package.json', 'package-lock.json', 'version.cjs']:
            shutil.copyfile(SRC / n, td / n)
        run(['npm', 'ci', '--no-audit', '--no-fund'], td, env)
        dist = td / 'node_modules' / '@duckdb' / 'duckdb-wasm' / 'dist'
        pkg_version = json.loads((dist.parent / 'package.json').read_text())['version']
        arrow_version = json.loads((td / 'node_modules' / 'apache-arrow' / 'package.json').read_text())['version']
        run([str(td / 'node_modules' / '.bin' / 'esbuild'), str(dist / 'duckdb-browser.mjs'), '--bundle', '--format=esm', '--minify', '--platform=browser',
             '--legal-comments=inline', f'--outfile={out / "duckdb-browser.bundle.js"}'], td, env)
        txt = (out / 'duckdb-browser.bundle.js').read_text(encoding='utf-8')
        left = re.findall(r'\bfrom\s*"([^"./][^"]*)"|\bimport\s*"([^"./][^"]*)"', txt)
        if left:
            sys.exit(f'the bundle still imports bare modules: {left[:5]}')
        for n in DIST_FILES:
            shutil.copyfile(dist / n, out / n)
        # the DuckDB core version inside the .wasm decides the extension path
        info = json.loads(run(['node', 'version.cjs'], td, env).strip().splitlines()[-1])[0]
        core = info['v']
    files = []
    for plat in PLATFORMS:
        rel = f'extensions/{core}/{plat}/parquet.duckdb_extension.wasm'
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        url = f'{EXT_HOST}/{core}/{plat}/parquet.duckdb_extension.wasm'
        resp = requests.get(url, timeout=120)   # (urllib's default client is refused by this host with HTTP 403; requests is not)
        resp.raise_for_status()
        (out / rel).write_bytes(resp.content)
        if (out / rel).stat().st_size < 1_000_000 or (out / rel).read_bytes()[:4] != b'\0asm':
            sys.exit(f'{url} did not return a WebAssembly file')
    for p in sorted(out.rglob('*')):
        if p.is_file():
            rel = p.relative_to(out).as_posix()
            src = (f'{EXT_HOST}/{rel[len("extensions/"):]}' if rel.startswith('extensions/') else
                   f'npm @duckdb/duckdb-wasm@{pkg_version} dist/duckdb-browser.mjs bundled with esbuild (apache-arrow {arrow_version} inlined)' if rel.endswith('.bundle.js') else
                   f'npm @duckdb/duckdb-wasm@{pkg_version} dist/{rel}')
            files.append(dict(file=rel, bytes=p.stat().st_size, sha256=sha256(p), source=src))
    manifest = dict(duckdb_wasm=pkg_version, duckdb_core=core, apache_arrow=arrow_version, esbuild=json.loads((SRC / 'package.json').read_text())['devDependencies']['esbuild'],
                    licenses={'@duckdb/duckdb-wasm': 'MIT', 'apache-arrow': 'Apache-2.0', 'DuckDB parquet extension': 'MIT'}, files=files)
    (out / 'MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(out=str(out), duckdb_wasm=pkg_version, duckdb_core=core, n_files=len(files), total_bytes=sum(f['bytes'] for f in files)), indent=1))


if __name__ == '__main__':
    main()
