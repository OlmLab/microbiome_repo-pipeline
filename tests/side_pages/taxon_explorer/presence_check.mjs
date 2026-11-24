/* presence_check.mjs — checks data/taxa/presence/ (which sample carries which taxon) and static/sp_presence.js.
 *
 *   node presence_check.mjs <payload dir> <expected_cells.json> [path to static/]
 *
 * <expected_cells.json> is written by tests/data_check.py, which recomputes, straight from the profile tables
 * with DuckDB, for 300-odd sampled taxa: the number of carriers, the md5 of their sorted sample accessions, and
 * the number of carriers in every body site, age group, country and lifestyle. No package is needed here.
 *
 *   A  every block of every taxon decodes with the page's own DEFLATE decoder to exactly what zlib gives
 *   B  every taxon: its carriers are strictly increasing sample numbers, and as many as the tree file says
 *   C  sampled taxa: the carriers are exactly the samples DuckDB found (md5 of the sorted accessions)
 *   D  sampled taxa: the carriers of every group are as many as DuckDB counted for that group
 *   E  the index is consistent with the files on disk
 */
import { createRequire } from 'node:module';
import { readFileSync, existsSync, statSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inflateRawSync } from 'node:zlib';
import { createHash } from 'node:crypto';

const here = dirname(fileURLToPath(import.meta.url));
const payload = resolve(process.argv[2] || join(here, '..', '..', '..', 'track_b', 'explorer_out'));
const expectedPath = resolve(process.argv[3] || join(payload, 'expected_cells.json'));
const staticDir = resolve(process.argv[4] || join(here, '..', 'static'));
const require = createRequire(import.meta.url);
const SPX = require(join(staticDir, 'sp_taxdata.js'));
require(join(staticDir, 'sp_presence.js'));
const SPP = globalThis.SPP;

const checks = new Map();
const failures = [];
function ok(name, cond, detail) {
  const c = checks.get(name) || [0, 0];
  c[cond ? 0 : 1] += 1;
  checks.set(name, c);
  if (!cond && failures.length < 20) { failures.push(name + ': ' + (detail || '')); }
}
const ab = (f) => { const b = readFileSync(join(payload, f)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };

SPX.loadTree(ab('tree.bin'));
SPX.loadStats(ab('stats.bin'));
const H = SPP.loadIndex(ab('presence/index.bin'));
const N = H.n_samples, nT = SPX.nTaxa();
const shards = [];
for (let k = 0; k < H.n_shards; k++) { shards.push(new Uint8Array(ab('presence/' + SPP.shardName(k)))); }

// E. the index and the files
ok('E index: as many taxa as the tree file', H.n_taxa === nT && N > 0, `${H.n_taxa} taxa, ${N} samples`);
ok('E index: shard sizes equal the files', H.shard_bytes.length === shards.length &&
   H.shard_bytes.every((v, k) => v === shards[k].length), 'a shard differs in size');
const keys = SPP.keys();
ok('E index: one accession per sample, all different', keys.length === N && new Set(keys).size === N,
   `${keys.length} accessions, ${new Set(keys).size} different`);
let orderOk = true;
for (let i = 1; i < N && orderOk; i++) {
  const a = SPP.sample(i - 1), b = SPP.sample(i);
  const sa = H.site_keys.indexOf(a.site), sb = H.site_keys.indexOf(b.site);
  if (sa === sb && a.project === b.project && !(a.key < b.key)) orderOk = false;
}
ok('E index: accessions ascend within a project and body site', orderOk);

// A and B. every taxon
let pairs = 0, tA = 0;
const all = new Array(nT);
const t0 = Date.now();
for (let id = 0; id < nT; id++) {
  const k = SPP.shardOf(id), n = SPP.count(id), t = SPX.taxon(id);
  ok('B count: carriers equal the tree file', n === t.nSamples, `${t.name}: ${n} vs ${t.nSamples}`);
  if (k < 0) { ok('B empty: a taxon without a block has no carrier', n === 0, t.name); continue; }
  let c;
  try { c = SPP.carriers(id, shards[k]); } catch (e) { ok('A decode: no error', false, t.name + ' ' + e.message); continue; }
  let inc = c.length === n && (n === 0 || c[n - 1] < N);
  for (let i = 1; i < c.length && inc; i++) { if (c[i] <= c[i - 1]) inc = false; }
  ok('B order: strictly increasing sample numbers below the total', inc, t.name);
  pairs += c.length;
}
ok('B total: the carriers add up to the number of pairs in the header', pairs === H.n_pairs, `${pairs} vs ${H.n_pairs}`);
// A. the decoder against zlib, byte for byte, on every block (read through the same index)
{
  const b = new Uint8Array(ab('presence/index.bin'));
  const dv = new DataView(b.buffer);
  const hlen = dv.getUint32(6, true), start = 10 + hlen, S = H.sections;
  const u16 = (name, i) => dv.getUint16(start + S[name][0] + 2 * i, true);
  const u32 = (name, i) => dv.getUint32(start + S[name][0] + 4 * i, true);
  let bad = 0, blocks = 0;
  for (let id = 0; id < nT; id++) {
    const enc = b[start + S.taxEnc[0] + id];
    if (!enc) continue;
    const blk = shards[u16('taxShard', id)].subarray(u32('taxOff', id), u32('taxOff', id) + u32('taxLen', id));
    const want = inflateRawSync(blk);
    const got = SPP.inflateRaw(blk, want.length);
    blocks++;
    if (got.length !== want.length || Buffer.compare(Buffer.from(got.buffer, got.byteOffset, got.length), want) !== 0) bad++;
  }
  ok('A decoder: every block equals zlib, byte for byte', bad === 0, `${bad} of ${blocks} blocks differ`);
  for (const name of Object.keys(H.raw)) {
    const z = b.subarray(start + S[name][0], start + S[name][0] + S[name][1]);
    const want = inflateRawSync(z), got = SPP.inflateRaw(z, H.raw[name]);
    ok('A decoder: every compressed section equals zlib', got.length === want.length &&
       Buffer.compare(Buffer.from(got.buffer, got.byteOffset, got.length), want) === 0, name);
  }
  console.log(`blocks decoded: ${blocks} in ${H.n_shards} shards, ${pairs} sample-taxon pairs, ${Date.now() - t0} ms`);
}

// C and D. the sampled taxa against DuckDB
if (!existsSync(expectedPath)) {
  console.log('no expected file: checks C and D skipped (run tests/data_check.py first)');
} else {
  const E = JSON.parse(readFileSync(expectedPath, 'utf8'));
  const P = E.presence || {};
  let nC = 0;
  for (const sid of Object.keys(P)) {
    const id = +sid, [n, md5] = P[sid], k = SPP.shardOf(id);
    const c = k < 0 ? new Uint32Array(0) : SPP.carriers(id, shards[k]);
    const ks = Array.from(c, (i) => keys[i]).sort();
    ok('C sampled taxa: as many carriers as DuckDB found', c.length === n, `${E.taxa[sid]}: ${c.length} vs ${n}`);
    ok('C sampled taxa: exactly the same samples (md5 of the sorted accessions)',
       createHash('md5').update(ks.join('\n')).digest('hex') === md5, E.taxa[sid]);
    nC++;
    const cells = (E.cells || {})[sid] || {}, lead = (E.lead || {})[sid];
    for (const dim of Object.keys(cells)) {
      for (const g of Object.keys(cells[dim])) {
        const crit = dim === 'site' ? { site: g } : { site: lead, [dim]: g };
        const got = SPP.filter(c, crit).length;
        ok(`D group counts: ${dim}`, got === cells[dim][g][0], `${E.taxa[sid]} ${dim}=${g}: ${got} vs ${cells[dim][g][0]}`);
      }
    }
  }
  ok('C sampled taxa: the expected file lists some', nC > 0, 'presence missing from the expected file');
  console.log(`taxa compared against DuckDB: ${nC}`);
}

let npass = 0, nfail = 0;
for (const [k, v] of [...checks.entries()].sort()) {
  console.log('  ' + k.padEnd(72) + ' pass ' + String(v[0]).padStart(7) + '  fail ' + v[1]);
  npass += v[0]; nfail += v[1];
}
for (const f of failures) console.log('  !! ' + f);
console.log(`presence_check: ${npass} pass, ${nfail} fail`);
process.exit(nfail ? 1 : 0);
