// Shared DuckDB-WASM start-up for the explorers (R2026.40).
// Two modes, chosen by the page:
//   * default — the pinned build is loaded from cdn.jsdelivr.net and the parquet extension from extensions.duckdb.org
//     (both hosts are in the CU server's Content-Security-Policy);
//   * self-hosted — window.SITE_DUCKDB = {base: '<relative URL of static/vendor/duckdb/>', builds: ['eh'] or ['eh','mvp']}: the module, the workers, the .wasm
//     files and the parquet extension all come from this site (files made by scripts/vendor_duckdb_wasm.py; switched on with
//     hosting.duckdb_self_hosted in config/site.yaml, which stays false until the CU server is known to serve .wasm files).
export const DUCKDB_VERSION = '1.29.0';
const SELF = (typeof window !== 'undefined' && window.SITE_DUCKDB && window.SITE_DUCKDB.base) ? window.SITE_DUCKDB : null;
export const DUCKDB_SOURCE = SELF ? 'this site' : 'cdn.jsdelivr.net';
const abs = p => new URL(p, document.baseURI).href;

export async function bootDuckDB() {
  let duckdb, bundle, worker, workerUrl = null;
  if (SELF) {
    const base = abs(SELF.base.replace(/\/?$/, '/'));
    duckdb = await import(base + 'duckdb-browser.bundle.js');
    // builds the site ships: 'eh' (WebAssembly exception handling; every current browser) and optionally 'mvp' (older browsers)
    const have = SELF.builds || ['eh'];
    const one = k => have.includes(k) ? { mainModule: base + `duckdb-${k}.wasm`, mainWorker: base + `duckdb-browser-${k}.worker.js` } : { mainModule: null, mainWorker: null };
    bundle = await duckdb.selectBundle({ mvp: one('mvp'), eh: have.includes('eh') ? one('eh') : undefined });
    if (!bundle.mainModule) throw new Error('this browser lacks WebAssembly exception handling, which the DuckDB-WASM build on this site needs');
    worker = new Worker(bundle.mainWorker);   // same origin: no blob wrapper needed
  } else {
    duckdb = await import(`https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@${DUCKDB_VERSION}/+esm`);
    bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
    workerUrl = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], {type: 'text/javascript'}));
    worker = new Worker(workerUrl);
  }
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  if (workerUrl) URL.revokeObjectURL(workerUrl);
  if (SELF) {
    // extensions (parquet) are fetched as <repository>/<duckdb version>/<platform>/<name>.duckdb_extension.wasm
    const c = await db.connect();
    try { await c.query(`SET custom_extension_repository = '${abs(SELF.base.replace(/\/?$/, '/')) + 'extensions'}'`); } finally { await c.close(); }
  }
  return { duckdb, db };
}
