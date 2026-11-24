/* reader_check.mjs — checks that static/sp_taxdata.js returns, from the payload, the numbers that
 * tests/data_check.py recomputed straight from the profile tables with DuckDB.
 *
 *   node reader_check.mjs <payload dir> <expected_cells.json> [path to sp_taxdata.js]
 *
 * Counts (detected samples, projects with a detection) must be exact; mean relative abundance must agree within
 * the quantisation of the file (half a step of 10 ** (1 / ra_scale)). No package is needed.
 */
import { createRequire } from 'node:module';
import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const payload = resolve(process.argv[2] || join(here, '..', '..', '..', 'track_b', 'explorer_out'));
const expectedPath = resolve(process.argv[3] || join(payload, 'expected_cells.json'));
const readerPath = resolve(process.argv[4] || join(here, '..', 'static', 'sp_taxdata.js'));

const require = createRequire(import.meta.url);
const SPX = require(readerPath);

const checks = new Map();
const failures = [];
function ok(name, cond, detail) {
  const c = checks.get(name) || [0, 0];
  c[cond ? 0 : 1] += 1;
  checks.set(name, c);
  if (!cond && failures.length < 20) { failures.push(name + ': ' + (detail || '')); }
}
function buf(f) { const b = readFileSync(join(payload, f)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); }

const treeMeta = SPX.loadTree(buf('tree.bin')).tree;
const statsMeta = SPX.loadStats(buf('stats.bin')).stats;
let projMeta = null;
if (existsSync(join(payload, 'projects.bin'))) { projMeta = SPX.loadProjects(buf('projects.bin')).projects; }

const exp = JSON.parse(readFileSync(expectedPath, 'utf8'));
const tol = Math.pow(10, 0.5 / statsMeta.ra_scale) - 1 + 1e-9;   // half a quantisation step, relative

/* ---- (i) the reader against DuckDB ------------------------------------------------------------------ */
const gkey = {};
for (const dim of statsMeta.dims) { gkey[dim] = statsMeta.groups[dim].map((g) => g.key); }
let nCellsChecked = 0;
for (const idStr of Object.keys(exp.taxa)) {
  const id = Number(idStr);
  const t = SPX.taxon(id);
  ok('taxon name round-trips', t && t.name === exp.taxa[idStr], idStr + ': ' + (t && t.name));
  ok('slug round-trips', t && t.slug === exp.slugs[idStr], idStr + ': ' + (t && t.slug));
  const st = SPX.stats(id);
  const want = exp.cells[idStr] || {};
  const leadKey = exp.lead[idStr];                               // the leading body site, from the tree file
  ok('scope is the leading body site', leadKey === null ? st.scope === null : st.scope.site === leadKey,
    `${exp.taxa[idStr]}: ${st.scope && st.scope.site} vs ${leadKey}`);
  ok('noScope flag agrees with the scope', st.noScope === (leadKey === null) && t.noScope === (leadKey === null),
    exp.taxa[idStr]);
  for (const dim of statsMeta.dims) {
    const rows = st[dim];
    const wantDim = want[dim] || {};
    const byKey = new Map(rows.map((r) => [r.key, r]));
    const scoped = dim !== 'site';
    ok('every group returned', rows.length === (scoped && leadKey === null ? 0 : gkey[dim].length), dim);
    if (scoped && leadKey !== null) {
      const dn = statsMeta.denoms[dim][leadKey];
      ok('denominators come from the leading site',
        rows.every((r, k) => r.n === dn[k][0] && r.proj === dn[k][1] &&
          r.shown === (dn[k][0] >= statsMeta.min_cell_samples && dn[k][1] >= statsMeta.min_cell_projects)),
        `${exp.taxa[idStr]} ${dim}`);
    }
    if (scoped && leadKey === null) {
      ok('a taxon without a leading site has no rows there', rows.length === 0 &&
        Object.keys(wantDim).length === 0, `${exp.taxa[idStr]} ${dim}`);
    }
    for (const [key, v] of Object.entries(wantDim)) {
      const r = byKey.get(key);
      nCellsChecked += 1;
      if (!r) { ok('cell present', false, dim + '/' + key); continue; }
      ok('detected count', r.detected === v[0], `${exp.taxa[idStr]} ${dim}/${key}: ${r.detected} vs ${v[0]}`);
      ok('projects with a detection', r.detectedProjects === v[2],
        `${exp.taxa[idStr]} ${dim}/${key}: ${r.detectedProjects} vs ${v[2]}`);
      ok('mean relative abundance', Math.abs(r.mean - v[1]) <= tol * v[1],
        `${exp.taxa[idStr]} ${dim}/${key}: ${r.mean} vs ${v[1]}`);
      ok('prevalence = detected / n', Math.abs(r.prevalence - v[0] / r.n) < 1e-12, dim + '/' + key);
    }
    for (const r of rows) {
      if (!(r.key in wantDim)) { ok('absent cell reads as zero', r.detected === 0 && r.mean === 0, dim + '/' + r.key); }
    }
  }
}

/* ---- the reader's own API ---------------------------------------------------------------------------- */
ok('tree header counts', treeMeta.n_taxa === SPX.nTaxa() && treeMeta.n_nodes === SPX.nNodes(), 'header vs reader');
ok('stats and tree agree on the number of taxa', statsMeta.n_taxa === treeMeta.n_taxa, '');
const rootKids = SPX.children(SPX.root());
ok('root has the two domains (and the unplaced group when there is one)',
  rootKids.length === (treeMeta.n_unplaced > 0 ? 3 : 2), 'children: ' + rootKids.length);
for (const k of rootKids.slice(0, 2)) {
  ok('domain branch length is zero', SPX.node(k).len === 0, SPX.node(k).name);
  ok('domain node is a domain', SPX.node(k).rank === 'domain', SPX.node(k).name);
}
let tips = 0, negative = 0, taxaSeen = 0;
for (let i = 0; i < SPX.nNodes(); i++) {
  const nd = SPX.node(i);
  if (nd.isTip) { tips += 1; }
  if (nd.len < 0) { negative += 1; }
  taxaSeen += nd.taxa.length;
}
ok('tip count matches the header', tips === treeMeta.n_tips, tips + ' vs ' + treeMeta.n_tips);
ok('no negative branch length', negative === 0, String(negative));
ok('every taxon sits on a node', taxaSeen === treeMeta.n_taxa, taxaSeen + ' vs ' + treeMeta.n_taxa);

const probe = ['g__Bacteroides', 's__Phocaeicola vulgatus', 'g__Bifidobacterium', 's__Escherichia coli',
  'g__Methanobrevibacter_A'];
for (const name of probe) {
  const id = SPX.byName(name);
  if (id < 0) { ok('probe taxon found', false, name); continue; }
  const t = SPX.taxon(id);
  ok('probe taxon found', true, name);
  ok('lineage starts at a domain', t.lineage.indexOf('d__') === 0, name + ': ' + t.lineage);
  ok('lineage ends with the taxon itself', t.lineage.split(';').pop() === name, name + ': ' + t.lineage);
  const p = SPX.path(t.node);
  ok('path starts at the root and ends at the node', p[0] === 0 && p[p.length - 1] === t.node, name);
  ok('path is a chain of parents', p.every((v, k) => k === 0 || SPX.node(v).parent === p[k - 1]), name);
  ok('search finds the taxon', SPX.search(SPX.bare(name), 20).some((h) => h.id === id), name);
  ok('taxaAt contains the taxon', SPX.taxaAt(t.node).indexOf(id) >= 0, name);
  if (t.lit) {
    ok('literature file exists for a taxon flagged as having one', existsSync(join(payload, 'lit', t.slug + '.json')),
      t.slug);
  }
}
let litFlagged = 0, litMissing = 0;
for (let i = 0; i < SPX.nTaxa(); i++) {
  const t = SPX.taxon(i);
  if (t.lit) { litFlagged += 1; if (!existsSync(join(payload, 'lit', t.slug + '.json'))) { litMissing += 1; } }
}
ok('literature flag matches the files', litFlagged === treeMeta.n_literature && litMissing === 0,
  litFlagged + ' flagged, ' + litMissing + ' without a file');
const t0 = Date.now();
const hits = SPX.search('bacteroid', 20);
ok('search returns something and is fast', hits.length > 0 && Date.now() - t0 < 1000,
  hits.length + ' hits in ' + (Date.now() - t0) + ' ms');
ok('search respects the limit', SPX.search('a', 5).length === 5, '');

if (projMeta) {
  let withProjects = 0;
  for (const idStr of Object.keys(exp.taxa).slice(0, 50)) {
    const pr = SPX.projects(Number(idStr));
    if (pr === null) { ok('projects readable', false, idStr); continue; }
    ok('at most five projects', pr.length <= projMeta.n_top, idStr + ': ' + pr.length);
    ok('projects are sorted by mean, descending', pr.every((v, k) => k === 0 || pr[k - 1].mean >= v.mean - 1e-12),
      idStr);
    ok('project sample count passes the threshold',
      pr.every((v) => v.n >= projMeta.min_project_samples), idStr);
    if (pr.length) { withProjects += 1; }
  }
  ok('some taxa have a most-abundant project list', withProjects > 0, String(withProjects));
}

let pass = 0, fail = 0;
for (const [name, c] of [...checks.entries()].sort()) {
  pass += c[0]; fail += c[1];
  console.log('  ' + name.padEnd(55) + ' pass ' + String(c[0]).padStart(7) + '  fail ' + c[1]);
}
for (const f of failures) { console.log('  !! ' + f); }
console.log('cells compared against DuckDB: ' + nCellsChecked + ' for ' + Object.keys(exp.taxa).length + ' taxa');
console.log('reader_check: ' + pass + ' pass, ' + fail + ' fail');
process.exit(fail ? 1 : 0);
