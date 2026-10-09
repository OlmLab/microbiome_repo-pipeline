  const td = (val, f, cls) => { const s = val === null || val === undefined ? '' : String(val); const rt = f ? row_[f + '__route'] : null, cf = f ? row_[f + '__confidence'] : null;
    return `<td class="${cls || ''}" title="${h((f || '') + (s ? ': ' + s : '') + (rt ? ' · ' + rt : '') + (cf !== null && cf !== undefined ? ' · confidence ' + fmtV(cf) : ''))}">${h(s)}</td>`; };
  let row_ = null;
  tbody.innerHTML = rows.map(row => { row_ = row; return `<tr data-key="${h(row.sample_key)}" tabindex="0" role="button" aria-label="open details for ${h(row.sample_key)}">` +
    `<td class="mono">${h(row.sample_key)}</td><td>${archiveLink(row.biosample_accession || row.sample_key, (row.biosample_accession || row.sample_key))}</td><td>${studyLink(row)}</td>` +
    td(row.age_category, 'age_category') + td(row.age_at_collection_days === null || row.age_at_collection_days === undefined ? '' : Math.round(Number(row.age_at_collection_days)), 'age_at_collection_days', 'num') +
    td(row.sex, 'sex') + td(row.country, 'country') + td(row.health_condition, 'health_condition', 'clip') + td(row.intervention, 'intervention', 'clip') + td(row.antibiotic_exposure, 'antibiotic_exposure') +
    td(row.subject_id, 'subject_id', 'clip mono') + `<td class="num" title="${h(row.seq_depth_source || '')}">${row.seq_gbp === null || row.seq_gbp === undefined ? '' : Number(row.seq_gbp).toFixed(1)}</td></tr>`; }).join('');
// Sample explorer — the catalog (human gut, all ages): DuckDB-WASM over data/gut_sample_metadata_wide.parquet (+ gut_studies.parquet
// for titles), fully client-side, one row per sample. Same boot / failure pattern as registry_explorer.js. Values shown with their
// route (R1 archive attribute, R2 supplementary table, R3 paper full text, R4 abstract) and confidence; the verbatim evidence quotes
// live in gut_sample_determinations.parquet (Downloads) and per study in data/studies/<PRJ>_determinations.csv.gz.
const DUCKDB_URL = 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm';
const CFG = window.GUT_CFG;
const PAGE = 50;
// core + key fields come from config/packs/gut.yaml through GUT_CFG (item 7); detailed_location is derived from location_site/locality/region
const FIELDS = [...(CFG.coreFields || ['age_at_collection_days', 'sex', 'country', 'health_condition', 'subject_id']), ...(CFG.keyFields || ['detailed_location', 'lifestyle', 'collection_date', 'antibiotic_exposure', 'bmi', 'timepoint_label'])];
// R2026.16: depth (seq_gbp, bases summed over the sample's runs) and the sample's intervention arm are shown; BMI / body-site class stay in the detail panel
const SHOW = ['sample_key', 'archive', 'study_accession', 'age_category', 'age_at_collection_days', 'sex', 'country', 'health_condition', 'intervention', 'antibiotic_exposure', 'subject_id', 'seq_gbp'];
const LOC_PARTS = ['location_site', 'location_locality', 'location_region'];
const HDR = {seq_gbp: 'Gbp', archive: 'archive', age_category: 'age cat.', age_at_collection_days: 'age (d)', health_condition: 'condition', antibiotic_exposure: 'abx', subject_id: 'subject', intervention: 'interv.', study_accession: 'project', sample_key: 'sample'};
let COLS = new Set();   // columns present in the wide table (filled at boot); absent columns are omitted, never rendered as empty
const archiveUrl = acc => { if (!acc) return null; const a = String(acc); if (/^[SED]RR\d+$/.test(a)) return CFG.archive.RUN + a; return (CFG.archive[a.slice(0, 4)] || CFG.archive.SAME) + a; };
const archiveLink = (acc, label) => { const u = archiveUrl(acc); return u ? `<a class="small" href="${u}">${h(label || 'archive')}</a>` : ''; };
const detailedLocation = s => LOC_PARTS.filter(p => COLS.has(p) && s[p] !== null && s[p] !== undefined && s[p] !== '').map(p => s[p]).join(', ');
const SORTABLE = new Set(['sample_key', 'study_accession', 'age_category', 'age_at_collection_days', 'sex', 'bmi', 'country', 'health_condition', 'n_fields_with_value', 'seq_gbp']);
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/'/g, "''");
const h = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const fmtV = v => { if (v === null || v === undefined) return ''; if (typeof v === 'bigint') return v.toString(); if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(1); return String(v); };
let duckdb, db, conn, page = 0, total = 0, sortCol = 'study_accession', sortDir = 'ASC', lastFocus = null;

function setBoot(msg) { $('boot-msg').textContent = msg; }
function bootFail(e) { $('boot').innerHTML = '<b>The explorer could not start.</b> ' + h(e && e.message || e) + '<br>DuckDB-WASM (pinned 1.29.0) is loaded from cdn.jsdelivr.net; the tables are plain parquet files under data/ and can be opened with any parquet reader.'; console.error(e); }

async function init() {
  try {
    setBoot('loading DuckDB-WASM module…');
    duckdb = await import(DUCKDB_URL);
    const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
    const workerUrl = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], {type: 'text/javascript'}));
    const worker = new Worker(workerUrl);
    db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
    await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
    URL.revokeObjectURL(workerUrl);
    setBoot('fetching the sample table…');
    for (const [name, url] of [['samples.parquet', CFG.samples], ['studies.parquet', CFG.studies]]) {
      const resp = await fetch(url); if (!resp.ok) throw new Error('fetch of ' + url + ' failed: HTTP ' + resp.status);
      await db.registerFileBuffer(name, new Uint8Array(await resp.arrayBuffer()));
    }
    conn = await db.connect();
    // current samples only: rows carried with release_retired set (bitemporal history, e.g. PRJNA50637 at R2026.14) are not catalog members
    const sCols = new Set((await conn.query(`DESCRIBE SELECT * FROM read_parquet('samples.parquet')`)).toArray().map(r => r.toJSON().column_name));
    await conn.query(`CREATE VIEW s AS SELECT * FROM read_parquet('samples.parquet')${sCols.has('release_retired') ? ' WHERE release_retired IS NULL' : ''}`);
    // R2026.15: study-level interventions (gut_studies.interventions, ';'-joined codes) join the explorer through `st`
    const stCols = new Set((await conn.query(`DESCRIBE SELECT * FROM read_parquet('studies.parquet')`)).toArray().map(r => r.toJSON().column_name));
    await conn.query(`CREATE VIEW st AS SELECT study_accession, study_title${stCols.has('interventions') ? ', interventions AS study_interventions' : ", NULL::VARCHAR AS study_interventions"} FROM read_parquet('studies.parquet')`);
    COLS = new Set((await conn.query(`DESCRIBE s`)).toArray().map(r => r.toJSON().column_name));
    for (const el of document.querySelectorAll('[data-field], [data-text], [data-year]')) {   // filters for columns this package does not carry are hidden
      const col = el.dataset.field || el.dataset.text || (el.dataset.year ? 'collection_date' : null);
      const present = col === 'detailed_location' ? LOC_PARTS.some(p => COLS.has(p)) : COLS.has(col);
      if (!present) { el.disabled = true; const lab = el.closest('.row2') || el; lab.style.display = 'none'; const l = document.querySelector(`label[for="${el.id}"]`); if (l) l.style.display = 'none'; }
    }
    window.__gutReady = true;
    $('boot').style.display = 'none'; $('exp').style.display = '';
    await loadCollection(new URLSearchParams(location.search).get('collection'));   // ?collection=<id>: pre-entered filters from data/collections.json
    const k = new URLSearchParams(location.search).get('sample');   // read BEFORE run(): writeUrl() would drop the param while the panel is still closed
    readUrl();
    if (k && !$('f-q').value.trim()) $('f-q').value = k;   // item 10: ?sample=<key> filters the table to that sample …
    await run();
    if (k) { await showDetail(k); $('detail').scrollIntoView({block: 'start'}); }   // … and opens its detail panel
  } catch (e) { bootFail(e); }
}

function whereClause() {
  const w = [];
  const q = $('f-q').value.trim();
  if (q) {
    if (/^PRJ[A-Z]*\d+$/i.test(q)) w.push(`s.study_accession = '${esc(q.toUpperCase())}'`);
    else if (/^SAM[NED]A?\d+$/i.test(q) || /^[SED]RR\d+$/i.test(q)) w.push(`(s.sample_key = '${esc(q.toUpperCase())}' OR s.biosample_accession = '${esc(q.toUpperCase())}')`);
    else w.push(`(st.study_title ILIKE '%${esc(q)}%' OR s.study_accession ILIKE '%${esc(q)}%')`);
  }
  for (const sel of document.querySelectorAll('select[data-field]')) { const v = sel.value; if (!v || sel.disabled) continue; w.push(v === '__null__' ? `s."${sel.dataset.field}" IS NULL` : `s."${sel.dataset.field}" = '${esc(v)}'`); }
  for (const inp of document.querySelectorAll('input[data-text]')) {
    const v = inp.value.trim(); if (!v || inp.disabled) continue;
    const cols = inp.dataset.text === 'detailed_location' ? LOC_PARTS.filter(p => COLS.has(p)) : [inp.dataset.text];
    if (cols.length) w.push('(' + cols.map(c => `s."${c}" ILIKE '%${esc(v)}%'`).join(' OR ') + ')');
  }
  if (COLS.has('collection_date')) {
    const y0 = $('f-year_min') && $('f-year_min').value, y1 = $('f-year_max') && $('f-year_max').value;
    if (y0) w.push(`TRY_CAST(substr(s.collection_date, 1, 4) AS INTEGER) >= ${parseInt(y0)}`);
    if (y1) w.push(`TRY_CAST(substr(s.collection_date, 1, 4) AS INTEGER) <= ${parseInt(y1)}`);
  }
  const iv = $('f-study_intervention') ? $('f-study_intervention').value : '';
  if (iv === '__none__') w.push(`coalesce(st.study_interventions, '') = ''`);
  else if (iv === '__any__') w.push(`coalesce(st.study_interventions, '') <> ''`);
  else if (iv) w.push(`list_contains(string_split(coalesce(st.study_interventions, ''), ';'), '${esc(iv)}')`);
  const sv = $('f-sample_intervention') ? $('f-sample_intervention').value : '';
  if (sv && COLS.has('intervention')) w.push(sv === '__null__' ? `s.intervention IS NULL` : `list_contains(string_split(coalesce(s.intervention, ''), ';'), '${esc(sv)}')`);
  if ($('f-infant').checked) w.push(`s.infant_scope`);
  if ($('f-has_age').checked) w.push(`s.age_at_collection_days IS NOT NULL`);
  if ($('f-has_subject').checked) w.push(`s.subject_id IS NOT NULL`);
  const mn = $('f-min_fields').value; if (mn !== '') w.push(`s.n_fields_with_value >= ${parseInt(mn)}`);
  for (const c of collectionClauses()) w.push(c);
  return w.length ? 'WHERE ' + w.join(' AND ') : '';
}
// ---- collections (config/collections.yaml → data/collections.json): studies = any-of accessions; filters: list = any-of, {min,max} = range,
// min_samples_per_subject = subjects with >= n samples in the same study. Same semantics as pages/collections.py, so counts match the collection page.
let COLLECTION = null;
async function loadCollection(id) {
  COLLECTION = null; const bar = $('collection-bar'); if (bar) bar.style.display = 'none';
  if (!id) return;
  try {
    const r = await fetch(CFG.collections); if (!r.ok) return;
    const all = await r.json(); if (!all[id]) return;
    COLLECTION = Object.assign({id}, all[id]);
    if (bar) { bar.style.display = ''; bar.innerHTML = `Collection: <b>${h(COLLECTION.title)}</b> — filters pre-entered (<a href="../collections/${h(id)}.html">about this collection</a> · <a href="index.html">clear</a>)`; }
  } catch (e) { COLLECTION = null; }
}
function collectionClauses() {
  if (!COLLECTION) return [];
  const w = [];
  if (COLLECTION.studies && COLLECTION.studies.length) w.push(`s.study_accession IN (${COLLECTION.studies.map(a => `'${esc(String(a))}'`).join(',')})`);
  for (const [k, v] of Object.entries(COLLECTION.filters || {})) {
    if (k === 'min_samples_per_subject') { const mn = (v && typeof v === 'object') ? parseInt(v.min || 1) : parseInt(v); w.push(`(s.study_accession, s.subject_id) IN (SELECT study_accession, subject_id FROM s WHERE subject_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) >= ${mn})`); continue; }
    const col = k === 'collection_year' ? `TRY_CAST(substr(s.collection_date, 1, 4) AS INTEGER)` : (COLS.has(k) ? `s."${k}"` : null);
    if (!col) { w.push('FALSE'); continue; }
    if (v && typeof v === 'object' && !Array.isArray(v)) { if (v.min !== undefined) w.push(`TRY_CAST(${col} AS DOUBLE) >= ${parseFloat(v.min)}`); if (v.max !== undefined) w.push(`TRY_CAST(${col} AS DOUBLE) <= ${parseFloat(v.max)}`); }
    else { const vals = (Array.isArray(v) ? v : [v]).map(x => `'${esc(String(x))}'`); w.push(`CAST(${col} AS VARCHAR) IN (${vals.join(',')})`); }
  }
  return w;
}
const FROM = 'FROM s LEFT JOIN st USING (study_accession)';

function writeUrl() {
  const p = new URLSearchParams();
  if ($('f-q').value.trim()) p.set('q', $('f-q').value.trim());
  for (const sel of document.querySelectorAll('select[data-field], select[data-url], input[data-text], input[data-year]')) if (sel.value && !sel.disabled) p.set(sel.id.replace(/^f-/, ''), sel.value);
  if ($('f-infant').checked) p.set('infant', '1'); if ($('f-has_age').checked) p.set('has_age', '1'); if ($('f-has_subject').checked) p.set('has_subject', '1');
  if ($('f-min_fields').value !== '') p.set('min_fields', $('f-min_fields').value);
  if (page) p.set('page', page + 1);
  if (sortCol !== 'study_accession' || sortDir !== 'ASC') p.set('sort', sortCol + ':' + sortDir);
  if (COLLECTION) p.set('collection', COLLECTION.id);
  const keep = new URLSearchParams(location.search).get('sample'); if (keep && $('detail').classList.contains('open')) p.set('sample', keep);
  history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p.toString() : '') + location.hash);
}
function readUrl() {
  const p = new URLSearchParams(location.search);
  if (p.get('q')) $('f-q').value = p.get('q');
  if (p.get('study')) $('f-q').value = p.get('study');
  for (const sel of document.querySelectorAll('select[data-field], select[data-url], input[data-text], input[data-year]')) { const v = p.get(sel.id.replace(/^f-/, '')); if (v) sel.value = v; }
  if (p.get('infant')) $('f-infant').checked = true; if (p.get('has_age')) $('f-has_age').checked = true; if (p.get('has_subject')) $('f-has_subject').checked = true;
  if (p.get('min_fields')) $('f-min_fields').value = p.get('min_fields');
  if (p.get('page')) page = Math.max(0, parseInt(p.get('page')) - 1);
  if (p.get('sort')) { const [c, d] = p.get('sort').split(':'); if (SORTABLE.has(c)) { sortCol = c; sortDir = d === 'DESC' ? 'DESC' : 'ASC'; } }
}
function detUrl(acc) { return ['..', 'data', 'studies', h(acc) + '_determinations.csv.gz'].join('/'); }
function routeBadge(r, c) { return r ? `<span class="tag ${h(r)}" title="route ${h(r)}, confidence ${fmtV(c)}">${h(r)}</span>` : ''; }

async function run() {
  const where = whereClause();
  $('count').textContent = 'counting…';
  const c = await conn.query(`SELECT COUNT(*) AS n, COUNT(DISTINCT s.study_accession) AS k ${FROM} ${where}`);
  const c0 = c.toArray()[0].toJSON(); total = Number(c0.n);
  const maxPage = Math.max(0, Math.ceil(total / PAGE) - 1); if (page > maxPage) page = maxPage;
  const r = await conn.query(`SELECT s.* ${FROM} ${where} ORDER BY "${sortCol}" ${sortDir} NULLS LAST, sample_key LIMIT ${PAGE} OFFSET ${page * PAGE}`);
  const rows = r.toArray().map(x => x.toJSON());
  const thead = $('result-table').querySelector('thead'), tbody = $('result-table').querySelector('tbody');
  thead.innerHTML = '<tr>' + SHOW.map(col => SORTABLE.has(col)
    ? `<th data-col="${col}" tabindex="0" role="columnheader button" aria-sort="${col === sortCol ? (sortDir === 'ASC' ? 'ascending' : 'descending') : 'none'}" title="sort by ${col}" style="cursor:pointer">${HDR[col] || col}${col === sortCol ? (sortDir === 'ASC' ? ' ▲' : ' ▼') : ''}</th>`
    : `<th>${HDR[col] || col}</th>`).join('') + '</tr>';
  tbody.innerHTML = rows.map(row => `<tr data-key="${h(row.sample_key)}" tabindex="0" role="button" aria-label="open details for ${h(row.sample_key)}">` +
    `<td class="mono">${h(row.sample_key)}</td><td>${archiveLink(row.biosample_accession || row.sample_key, (row.biosample_accession || row.sample_key))}</td><td>${studyLink(row)}</td><td>${h(row.age_category)}</td><td class="num">${fmtV(row.age_at_collection_days)} ${routeBadge(row.age_at_collection_days__route, row.age_at_collection_days__confidence)}</td>` +
    `<td>${h(row.sex)}</td><td>${h(row.country)}</td><td>${h(row.health_condition)} ${routeBadge(row.health_condition__route, row.health_condition__confidence)}</td><td class="small">${h(row.intervention)}</td><td>${h(row.antibiotic_exposure)}</td><td class="small">${h(row.subject_id)}</td><td class="num" title="${h(row.seq_depth_source || '')}">${row.seq_gbp === null || row.seq_gbp === undefined ? '' : Number(row.seq_gbp).toFixed(2)}</td></tr>`).join('');
  $('count').textContent = `${total.toLocaleString()} samples match (${Number(c0.k).toLocaleString()} studies)`;
  $('pageinfo').textContent = total ? `page ${page + 1} / ${maxPage + 1}` : '';
  $('prev').disabled = page <= 0; $('next').disabled = page >= maxPage;
  writeUrl();
}
function studyLink(row) {
  const acc = row.study_accession;
  return `<a href="${CFG.studiesUrl}${h(acc)}.html">${h(acc)}</a>`;
}

async function download() {
  const where = whereClause(), fname = 'gut_samples_slice.csv';
  $('dl-status').textContent = `preparing ${fname} (${total.toLocaleString()} rows)…`;
  try {
    await conn.query(`COPY (SELECT s.*, st.study_title ${FROM} ${where} ORDER BY s.study_accession, s.sample_key) TO '${fname}' (HEADER, DELIMITER ',')`);
    const buf = await db.copyFileToBuffer(fname); await db.dropFile(fname);
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([buf], {type: 'text/csv'})); a.download = fname; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    $('dl-status').textContent = `${fname}: ${(buf.length / 1e6).toFixed(1)} MB`;
  } catch (e) { $('dl-status').textContent = 'download failed: ' + (e.message || e); console.error(e); }
}

async function showDetail(key) {
  const box = $('detail'), body = $('detail-body');
  if (!box.classList.contains('open')) lastFocus = document.activeElement;
  box.classList.add('open'); body.innerHTML = '<p class="status">loading…</p>'; box.focus();
  const r = await conn.query(`SELECT s.*, st.study_title ${FROM} WHERE s.sample_key = '${esc(key)}'`);
  const rows = r.toArray(); if (!rows.length) { body.innerHTML = `<p>No sample <b>${h(key)}</b>.</p>`; return; }
  const s = rows[0].toJSON();
  const p = new URLSearchParams(location.search); p.set('sample', key); history.replaceState(null, '', location.pathname + '?' + p.toString() + location.hash);
  let html = `<h2 style="margin-top:0">${h(key)}</h2><p>${studyLink(s)} · ${h(s.study_title)}</p>
  <p class="small">${archiveLink(s.biosample_accession || key, 'archive record ' + (s.biosample_accession || key))}${/^[SED]RR\d+$/.test(key) ? '' : ' · <a href="' + CFG.enaSampleUrl + h(s.biosample_accession || key) + '">ENA</a>'} · unit ${h(s.sample_unit)} · body site ${h(s.body_site_code)} (${h(s.body_site_class)}) · source ${h(s.curated_source)}${s.infant_scope ? ' · infant extension' : ''}</p>
  <h3>Age category</h3><p><b>${h(s.age_category)}</b> <span class="small">(basis: ${h(s.age_category_basis)})</span></p>
  <h3>Fields</h3><table class="tbl kv">`;
  const tierOf = f => (CFG.coreFields || []).includes(f) ? 'core' : 'key';
  for (const f of FIELDS) {
    if (f === 'detailed_location') { if (LOC_PARTS.some(p => COLS.has(p))) html += `<tr><td>detailed_location <span class="tag tier-key">key</span></td><td>${h(detailedLocation(s))}</td></tr>`; continue; }
    if (!COLS.has(f)) continue;   // column absent from this package → omitted
    html += `<tr><td>${f} <span class="tag tier-${tierOf(f)}">${tierOf(f)}</span></td><td>${fmtV(s[f])} ${routeBadge(s[f + '__route'], s[f + '__confidence'])}</td></tr>`;
    if (f === 'lifestyle' && COLS.has('lifestyle_detail') && s.lifestyle_detail) html += `<tr><td>lifestyle_detail</td><td class="small">${h(s.lifestyle_detail)}</td></tr>`;
  }
  if (s.health_condition_detail) html += `<tr><td>health_condition_detail</td><td class="small">${h(s.health_condition_detail)}</td></tr>`;
  if (COLS.has('latitude') && COLS.has('longitude') && s.latitude !== null && s.latitude !== undefined && s.longitude !== null && s.longitude !== undefined) {
    const la = Number(s.latitude), lo = Number(s.longitude);
    html += `<tr><td>latitude, longitude</td><td>${la.toFixed(4)}, ${lo.toFixed(4)} ${routeBadge(s.latitude__route, s.latitude__confidence)} · <a href="https://www.openstreetmap.org/?mlat=${la}&mlon=${lo}#map=8/${la}/${lo}">OpenStreetMap</a></td></tr>`;
  }
  html += '</table>';
  const inf = ['delivery_mode', 'feeding_mode', 'preterm_status', 'gestational_age_weeks', 'birth_weight_grams', 'maternal_antibiotics', 'probiotic_exposure', 'hmo_supplementation', 'nec_status'].filter(f => COLS.has(f) && s[f] !== null && s[f] !== undefined);
  if (inf.length) html += '<h3>Infant extension fields</h3><table class="tbl kv">' + inf.map(f => `<tr><td>${f}</td><td>${fmtV(s[f])}</td></tr>`).join('') + '</table>';
  html += `<h3>Evidence</h3><p class="small">Every value above has an evidence row (verbatim quote ≤ 12 words, labelled source, locator, route, confidence): <a href="${detUrl(s.study_accession)}">${h(s.study_accession)}_determinations.csv.gz</a> (this study) or <span class="mono">gut_sample_determinations.parquet</span> (all). Cohort-wide statements (R3/R4) are shown on the <a href="${CFG.studiesUrl}${h(s.study_accession)}.html">study page</a>.</p>`;
  body.innerHTML = html;
}

$('apply').addEventListener('click', () => { page = 0; run(); });
$('reset').addEventListener('click', () => { history.replaceState(null, '', location.pathname + location.hash); for (const el of document.querySelectorAll('.filters select, .filters input')) { if (el.type === 'checkbox') el.checked = false; else el.value = ''; } page = 0; sortCol = 'study_accession'; sortDir = 'ASC'; run(); });
$('prev').addEventListener('click', () => { page = Math.max(0, page - 1); run(); });
$('next').addEventListener('click', () => { page++; run(); });
$('dl-csv').addEventListener('click', download);
$('detail-close').addEventListener('click', closeDetail);
function activate(e) {
  const th = e.target.closest('th[data-col]'); if (th) { const c = th.dataset.col; if (sortCol === c) sortDir = sortDir === 'ASC' ? 'DESC' : 'ASC'; else { sortCol = c; sortDir = 'ASC'; } page = 0; run(); return; }
  if (e.target.closest('a')) return;
  const tr = e.target.closest('tr[data-key]'); if (tr) showDetail(tr.dataset.key);
}
$('result-table').addEventListener('click', activate);
$('result-table').addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { if (e.target.closest('th[data-col], tr[data-key]')) { e.preventDefault(); activate(e); } } });
function closeDetail() { $('detail').classList.remove('open'); const p = new URLSearchParams(location.search); p.delete('sample'); history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p.toString() : '') + location.hash); if (lastFocus) lastFocus.focus(); }
document.addEventListener('keydown', e => { if (e.key === 'Escape' && $('detail').classList.contains('open')) closeDetail(); });
for (const el of document.querySelectorAll('.filters input')) el.addEventListener('keydown', e => { if (e.key === 'Enter') { page = 0; run(); } });
for (const el of document.querySelectorAll('.filters select, .filters input[type=checkbox]')) el.addEventListener('change', () => { page = 0; run(); });

init();

// R2026.18: home-style search box + filters toggle
(function () {
  const tq = document.getElementById('top-q'), fq = document.getElementById('f-q'), tg = document.getElementById('toggle-filters'), ex = document.getElementById('exp');
  if (tq && fq) { let t = null; tq.value = fq.value || new URLSearchParams(location.search).get('q') || '';
    tq.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { fq.value = tq.value; const b = document.getElementById('apply'); if (b) b.click(); }, 300); }); }
  if (tg && ex) tg.addEventListener('click', () => { const off = ex.classList.toggle('nofilters'); tg.setAttribute('aria-expanded', off ? 'false' : 'true'); tg.textContent = off ? 'Filters' : 'Hide filters'; });
})();
