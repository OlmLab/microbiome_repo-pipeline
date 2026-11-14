#!/usr/bin/env python3
"""Build and serve a small test page that starts DuckDB-WASM with site_generator/gen/static/duckdb_boot.js — the same start-up
code the two explorers use — and reads a parquet file with it. Used by .github/workflows/duckdb-selfhost-test.yml.

  python scripts/duckdb_boot_harness.py build --out /tmp/h_self --mode self [--vendor build/vendor_duckdb] [--builds eh,mvp]
  python scripts/duckdb_boot_harness.py build --out /tmp/h_cdn  --mode cdn
  python scripts/duckdb_boot_harness.py serve --dir /tmp/h_self --port 8131 --csp "default-src 'self'; ..."

`serve` sends the given Content-Security-Policy on every response (enforced) and application/wasm for .wasm files, and appends every
request path to <dir>/requests.log. Mode `self` must work under a policy that names no other host.
"""
import argparse, http.server, json, shutil, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>DuckDB boot test</title></head><body>
<div id="out">starting</div>
%(cfg)s
<script type="module">
import { bootDuckDB, DUCKDB_SOURCE } from './static/duckdb_boot.js';
const out = document.getElementById('out');
try {
  const { duckdb, db } = await bootDuckDB();
  const resp = await fetch('data/t.parquet');
  await db.registerFileBuffer('t.parquet', new Uint8Array(await resp.arrayBuffer()));
  const c = await db.connect();
  const r = (await c.query("SELECT count(*)::INT AS n, sum(x)::INT AS s FROM read_parquet('t.parquet')")).toArray()[0];
  const v = (await c.query("SELECT version() AS v")).toArray()[0].v;
  out.textContent = `ok n=${r.n} s=${r.s} source=${DUCKDB_SOURCE} core=${v}`;
} catch (e) { out.textContent = 'FAIL ' + (e && e.message || e); }
</script></body></html>
"""


def build(a):
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / 'static').mkdir(parents=True)
    (out / 'data').mkdir()
    shutil.copyfile(REPO / 'site_generator' / 'gen' / 'static' / 'duckdb_boot.js', out / 'static' / 'duckdb_boot.js')
    import pyarrow as pa, pyarrow.parquet as pq
    pq.write_table(pa.table({'x': list(range(1, 1001)), 'label': [f'row{i}' for i in range(1000)]}), out / 'data' / 't.parquet', compression='zstd')
    cfg = ''
    if a.mode == 'self':
        builds = [b for b in a.builds.split(',') if b]
        vend = Path(a.vendor)
        for f in sorted(vend.rglob('*')):
            if f.is_file():
                rel = f.relative_to(vend)
                if any(b not in builds and (rel.name in (f'duckdb-{b}.wasm', f'duckdb-browser-{b}.worker.js') or f'wasm_{b}' in rel.parts) for b in ('eh', 'mvp')):
                    continue
                (out / 'static' / 'vendor' / 'duckdb' / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, out / 'static' / 'vendor' / 'duckdb' / rel)
        cfg = "<script>window.SITE_DUCKDB={base:'static/vendor/duckdb/',builds:%s};</script>" % json.dumps(builds)
    (out / 'index.html').write_text(PAGE % dict(cfg=cfg))
    print(json.dumps(dict(out=str(out), mode=a.mode, files=sum(1 for p in out.rglob('*') if p.is_file()))))


def serve(a):
    root = Path(a.dir).resolve()
    log = root / 'requests.log'

    class H(http.server.SimpleHTTPRequestHandler):
        extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map, **{'.wasm': 'application/wasm', '.js': 'text/javascript', '.parquet': 'application/octet-stream'})

        def __init__(self, *args, **kw):
            super().__init__(*args, directory=str(root), **kw)

        def end_headers(self):
            if a.csp:
                self.send_header('Content-Security-Policy', a.csp)
            super().end_headers()

        def log_message(self, fmt, *args):
            with open(log, 'a') as f:
                f.write(fmt % args + '\n')

    http.server.ThreadingHTTPServer(('127.0.0.1', a.port), H).serve_forever()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build'); b.add_argument('--out', required=True); b.add_argument('--mode', choices=['self', 'cdn'], required=True)
    b.add_argument('--vendor', default=str(REPO / 'build' / 'vendor_duckdb')); b.add_argument('--builds', default='eh')
    s = sub.add_parser('serve'); s.add_argument('--dir', required=True); s.add_argument('--port', type=int, default=8131); s.add_argument('--csp', default='')
    a = ap.parse_args()
    (build if a.cmd == 'build' else serve)(a)


if __name__ == '__main__':
    main()
