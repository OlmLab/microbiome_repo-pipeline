# DuckDB-WASM served by the site itself

Status (2026-11-13): prepared and tested, **switched off**. No `.wasm` file is in any release. Switch it on only after the
owner confirms that the CU server serves `.wasm` files (owner's instruction of 2026-11-13).

## Why

The two explorers (Samples, Registry) run DuckDB-WASM 1.29.0 in the browser. Today the module, the workers and the
`.wasm` build come from `cdn.jsdelivr.net`, and DuckDB then fetches its parquet extension from `extensions.duckdb.org`
(DuckDB core v1.1.1). Both hosts are named in the CU server's Content-Security-Policy. Self-hosting removes both
dependencies: the explorers then work with `connect-src 'self'` and keep working if either host is unreachable.

## Parts

| Part | Where |
|---|---|
| Pinned inputs (`npm ci`) | `site_generator/vendor_src/duckdb/package.json`, `package-lock.json` |
| Vendoring script | `scripts/vendor_duckdb_wasm.py` → `build/vendor_duckdb/` (outside the site tree; not in git) |
| Start-up code shared by both explorers | `site_generator/gen/static/duckdb_boot.js` (CDN mode unless the page sets `window.SITE_DUCKDB`) |
| Switch | `config/site.yaml` → `hosting.duckdb_self_hosted` (false) and `hosting.duckdb_self_hosted_builds` (`[eh]`) |
| Build | `site_generator/gen/build_site.py` copies the vendor files into `static/vendor/duckdb/` and sets `window.SITE_DUCKDB` on the two explorer pages only when the switch is on; an incomplete vendor directory stops the build |
| Guard | `site_generator/gen/check_file_types.py` (run by `make verify`) rejects `.wasm`; `tests/test_duckdb_self_hosting.py` fails if the switch is on by default or a `.wasm` file sits in `static/` |
| Browser test | `.github/workflows/duckdb-selfhost-test.yml` in the pipeline repository: Chromium, Firefox and WebKit; self-hosted mode under a policy naming no other host (and asserting no request leaves the origin), and CDN mode under the CU policy |

## Files made by the vendoring script

| File | Bytes | sha256 |
|---|---:|---|
| `duckdb-browser-eh.worker.js` | 760,530 | `260b4773d2d78e5ab1106bc163ffd5b0b6eaeddedacf23190e9cafc9cc010d3d` |
| `duckdb-browser-mvp.worker.js` | 820,866 | `f798d151c3db03d3f277f884acb882c7a324d5249ef540bdebb82d3cdafbbc37` |
| `duckdb-browser.bundle.js` | 225,344 | `b6828cdd294043a20d4d390515a418f3fe427a4d0e6aa1763ae10bb421e02645` |
| `duckdb-eh.wasm` | 35,659,694 | `00d45e5e074b7f7e17e45daabacb0fb67248eac8bd638a72cd1b42dad23788e4` |
| `duckdb-mvp.wasm` | 40,621,595 | `79c530032e746177e0406f3f1c4fa9f69fd7437e17a18587b1f8ab38dc5132ea` |
| `extensions/v1.1.1/wasm_eh/parquet.duckdb_extension.wasm` | 2,803,379 | `e24fb2953c6a80e32d17fa3921f90f1f05ccbfa40f382e77702c1e6af9b79479` |
| `extensions/v1.1.1/wasm_mvp/parquet.duckdb_extension.wasm` | 2,713,313 | `e73e280539207f51ca56f3d302ca6051182ef6213999fae9f809bfe2113b9311` |

Shipping the `eh` build only (default) adds 39.4 MB to the site; both builds add 83.6 MB. R2026.39's site is
897 MB against the 950 MB limit in `make verify`, so both builds do not fit without raising that limit. The `eh` build needs
WebAssembly exception handling (Chrome 95, Firefox 100, Safari 15.2 and later); an older browser gets a message that says so.
The bundle is named `.js`, not `.mjs`, because servers map `.js` to a JavaScript MIME type by default and module scripts
require one.

## Switching it on (after the owner's go-ahead)

1. Ask BIT to confirm that the CU server serves `*.wasm` with `Content-Type: application/wasm` (a streaming compile needs it)
   and has no upload filter on the extension.
2. `python scripts/vendor_duckdb_wasm.py` (needs node/npm, registry.npmjs.org, extensions.duckdb.org) and compare
   `build/vendor_duckdb/MANIFEST.json` with the table above.
3. `config/site.yaml`: `duckdb_self_hosted: true`. `site_generator/gen/check_file_types.py`: add `'wasm'` to `ALLOWED`.
   Update `tests/test_duckdb_self_hosting.py::test_switch_is_off_and_wasm_is_not_an_allowed_site_file_type` accordingly.
4. `make site && make verify` — the site-repository smoke test then exercises the explorers in self-hosted mode under the CU
   policy. After the release `https://extensions.duckdb.org` can be dropped from `connect-src`;
   `cdn.jsdelivr.net` stays in the policy for as long as other pages load libraries from it (the Atlas map loads D3 and
   topojson-client from there; `grep -l jsdelivr site_generator/gen/templates/*.html` lists the pages).
5. When DuckDB-WASM is upgraded: change the version in `package.json`, run `npm install` there to refresh the lock file, set
   `DUCKDB_VERSION` in `duckdb_boot.js`, re-run the vendoring script (it reads the DuckDB core version from the build and
   fetches the matching extension) and the browser test.
