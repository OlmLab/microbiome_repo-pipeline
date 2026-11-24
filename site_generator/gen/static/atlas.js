/* Atlas front-end (R2026.12). Loads data/atlas/{taxa,countries,studies}.json once, then one matrix_<rank>.bin per
   rank on demand (little-endian uint32 stream, see taxa.json meta.format).  D3 v7 + topojson-client are pinned from
   jsDelivr with SRI in the template; the world-110m TopoJSON is vendored under static/vendor/. */
(function () {
  'use strict';
  const ROOT = window.ATLAS_ROOT || '../';
  const DATA = ROOT + 'data/atlas/';
  const MIN_N = 30;
  const AGES = ['neonate', 'infant', 'child', 'adolescent', 'adult', 'elderly', 'unknown'];
  const AGE_COL = { neonate: '#7A6A3C', infant: '#CFB87C', child: '#A88B4A', adolescent: '#565A5C', adult: '#A2A4A3', elderly: '#000', unknown: '#D9D9D9' };
  const state = { rank: 'genus', metric: 'prev', scale: 'rel', proj: 'flat', taxon: null, country: null, rot: [0, 0] };
  const st = { taxa: null, byRank: {}, countries: null, studies: null, world: null, mats: {}, sel: null, ISO: {} };

  const $ = (s) => document.querySelector(s);
  const fmtPct = (x) => (x * 100 < 0.1 && x > 0 ? '<0.1 %' : (x * 100).toFixed(x >= 0.1 ? 0 : 1) + ' %');
  const fmtAb = (x) => (x >= 0.01 ? (x * 100).toFixed(1) + ' %' : x >= 1e-4 ? (x * 100).toFixed(2) + ' %' : (x * 100).toExponential(1) + ' %');
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const pretty = (name) => name.replace(/^[a-z]__/, '');

  // ---------- data ----------
  async function loadCore() {
    const [taxa, countries, studies, world] = await Promise.all([
      fetch(DATA + 'taxa.json').then((r) => r.json()), fetch(DATA + 'countries.json').then((r) => r.json()),
      fetch(DATA + 'studies.json').then((r) => r.json()), fetch(ROOT + 'static/vendor/countries-110m.json').then((r) => r.json())]);
    st.taxa = taxa.taxa; st.meta = taxa.meta; st.countries = countries; st.studies = studies; st.world = world;
    st.taxa.forEach((t, i) => { t.idx = i; (st.byRank[t.rank] = st.byRank[t.rank] || []).push(t); });
    countries.forEach((c) => { st.ISO[c.iso2] = c; });
  }
  // parse one rank's binary into {taxonLocalIndex: {c: Uint32Array(nC*3), s: Uint32Array(nS*3)}}
  async function loadMatrix(rank) {
    if (st.mats[rank]) return st.mats[rank];
    const buf = await fetch(DATA + 'matrix_' + rank + '.bin').then((r) => r.arrayBuffer());
    const u = new Uint32Array(buf); const out = []; let p = 0;
    const n = st.byRank[rank].length;
    for (let i = 0; i < n; i++) {
      const nC = u[p], nS = u[p + 1]; p += 2;
      out.push({ c: u.subarray(p, p + nC * 3), s: u.subarray(p + nC * 3, p + nC * 3 + nS * 3) }); p += (nC + nS) * 3;
    }
    st.mats[rank] = out; return out;
  }
  function taxonRows(t) { // → {countries: Map(iso → {n, prev, mean}), studies: [{study, n, prev, mean}]}
    const m = st.mats[t.rank][st.byRank[t.rank].indexOf(t)];
    const countries = new Map();
    for (let i = 0; i < m.c.length; i += 3) { const c = st.countries[m.c[i]]; countries.set(c.iso2, { c, n: m.c[i + 1], prev: m.c[i + 1] / c.n, mean: m.c[i + 2] / 1e6 }); }
    const studies = [];
    for (let i = 0; i < m.s.length; i += 3) { const s = st.studies[m.s[i]]; studies.push({ s, n: m.s[i + 1], prev: m.s[i + 1] / s.n, mean: m.s[i + 2] / 1e6 }); }
    return { countries, studies };
  }

  // ---------- map ----------
  const svg = d3.select('#map'); const W = 960, H = 500;
  const gS = svg.append('g'), gG = svg.append('g'), gC = svg.append('g'), gP = svg.append('g');
  let projection, path, features, numToIso = {};
  function setProjection() {
    projection = state.proj === 'globe' ? d3.geoOrthographic().scale(235).translate([W / 2, H / 2]).rotate(state.rot).clipAngle(90)
      : d3.geoNaturalEarth1().scale(175).translate([W / 2, H / 2 + 10]);
    path = d3.geoPath(projection);
  }
  function drawBase() {
    features = topojson.feature(st.world, st.world.objects.countries).features.filter((f) => f.id !== '010');
    st.countries.forEach((c) => { if (c.num) numToIso[c.num] = c.iso2; });
    setProjection();
    gS.selectAll('path').data([{ type: 'Sphere' }]).join('path').attr('class', 'sphere').attr('d', path);
    gG.selectAll('path').data([d3.geoGraticule10()]).join('path').attr('class', 'graticule').attr('d', path);
    gC.selectAll('path').data(features).join('path').attr('class', 'country').attr('d', path)
      .on('mousemove', (ev, f) => tip(ev, numToIso[f.id])).on('mouseleave', hideTip).on('click', (ev, f) => selectCountry(numToIso[f.id]));
    const pts = st.countries.filter((c) => c.ll);
    gP.selectAll('circle').data(pts).join('circle').attr('r', 5).attr('class', 'country')
      .on('mousemove', (ev, c) => tip(ev, c.iso2)).on('mouseleave', hideTip).on('click', (ev, c) => selectCountry(c.iso2));
    positionPoints();
    svg.call(d3.drag().filter(() => state.proj === 'globe').on('drag', (ev) => { state.rot = [state.rot[0] + ev.dx * 0.4, Math.max(-90, Math.min(90, state.rot[1] - ev.dy * 0.4))]; redrawGeometry(); }));
  }
  function positionPoints() {
    gP.selectAll('circle').attr('transform', (c) => { const p = projection(c.ll); return p ? `translate(${p[0]},${p[1]})` : 'translate(-100,-100)'; })
      .style('display', (c) => { if (state.proj !== 'globe') return null; const d = d3.geoDistance(c.ll, [-state.rot[0], -state.rot[1]]); return d > Math.PI / 2 ? 'none' : null; });
  }
  function redrawGeometry() { setProjection(); gS.selectAll('path').attr('d', path); gG.selectAll('path').attr('d', path); gC.selectAll('path').attr('d', path); positionPoints(); }

  let cur = null; // {t, rows, vals: Map(iso→value)}
  function colourScale() {
    const vals = cur ? [...cur.vals.values()] : [0];
    const max = state.scale === 'fix' ? (state.metric === 'prev' ? 1 : Math.max(...vals, 1e-6)) : Math.max(...vals, 1e-6);
    return { max, f: d3.scaleSequential([0, max], d3.interpolateViridis) };
  }
  function recolour() {
    if (!cur) return;
    const cs = colourScale();
    const fill = (iso) => { const c = st.ISO[iso]; if (!c || c.n < MIN_N) return '#e6e6e6'; const v = cur.vals.get(iso); return v == null ? cs.f(0) : cs.f(v); };
    gC.selectAll('path').attr('fill', (f) => fill(numToIso[f.id])).classed('sel', (f) => numToIso[f.id] === state.country);
    gP.selectAll('circle').attr('fill', (c) => fill(c.iso2)).classed('sel', (c) => c.iso2 === state.country);
    legend(cs);
  }
  function legend(cs) {
    const lab = state.metric === 'prev' ? 'prevalence (share of samples ≥ threshold)' : 'mean relative abundance';
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((q) => q * cs.max);
    const grad = d3.range(0, 1.001, 0.1).map((q) => `<span style="flex:1;background:${cs.f(q * cs.max)}"></span>`).join('');
    $('#legend').innerHTML = `<div>${esc(lab)} · ${state.scale === 'fix' ? 'fixed' : 'relative'} scale</div><div style="display:flex;height:9px;width:220px;border:1px solid #ccc">${grad}</div>` +
      `<div style="display:flex;justify-content:space-between;width:220px">${ticks.map((v) => `<span>${state.metric === 'prev' ? (v * 100).toFixed(0) + '%' : fmtAb(v)}</span>`).join('')}</div>` +
      `<div style="margin-top:.2rem"><span style="display:inline-block;width:10px;height:10px;background:#e6e6e6;border:1px solid #ccc"></span> &lt; ${MIN_N} samples</div>`;
  }
  function tip(ev, iso) {
    const c = st.ISO[iso]; const el = $('#tip'); if (!c) { hideTip(); return; }
    const r = cur && cur.rows.countries.get(iso);
    el.style.display = 'block';
    const box = $('#map-wrap').getBoundingClientRect();
    el.style.left = (ev.clientX - box.left + 12) + 'px'; el.style.top = (ev.clientY - box.top + 12) + 'px';
    el.innerHTML = `<b>${esc(c.name)}</b> · ${c.n.toLocaleString()} samples · ${c.nstud} studies` + (c.n < MIN_N ? ' (too few samples)' : '') +
      (cur ? `<br>${esc(pretty(cur.t.name))}: prevalence ${r ? fmtPct(r.prev) : '0 %'} · mean ${r ? fmtAb(r.mean) : '0'}` : '') + (c.hdi != null ? `<br>HDI ${c.hdi.toFixed(2)} (${c.band})` : '');
  }
  function hideTip() { $('#tip').style.display = 'none'; }

  // ---------- taxon selection ----------
  async function selectTaxon(t) {
    state.taxon = t; state.rank = t.rank; $('#rank-sel').value = t.rank;
    await loadMatrix(t.rank);
    const rows = taxonRows(t);
    cur = { t, rows, vals: new Map() };
    for (const [iso, r] of rows.countries) cur.vals.set(iso, state.metric === 'prev' ? r.prev : r.mean);
    recolour(); taxonHead(); countryTable(); if (state.country) selectCountry(state.country, true);
    document.querySelectorAll('#taxon-hits li').forEach((li) => li.classList.toggle('sel', li.dataset.idx == t.idx));
    if (location.hash !== '#' + encodeURIComponent(t.name)) history.replaceState(null, '', '#' + encodeURIComponent(t.name));
  }
  function taxonHead() {
    const t = cur.t; const parent = t.parent ? st.taxa.find((x) => x.name === t.parent) : null;
    const kids = st.taxa.filter((x) => x.parent === t.name).sort((a, b) => b.n - a.n).slice(0, 12);
    $('#taxon-head').innerHTML = `<h2><i>${esc(pretty(t.name))}</i> <span class="tag">${esc(t.rank)}</span></h2>` +
      `<div class="mini">${esc(t.lineage || '')}</div>` +
      `<div>Detected in <b>${t.n.toLocaleString()}</b> samples (${fmtPct(t.prev)}) across <b>${t.nstud.toLocaleString()}</b> studies · mean relative abundance ${fmtAb(t.mean)}` +
      (parent ? ` · parent: <a href="#" data-taxon="${esc(parent.name)}">${esc(pretty(parent.name))}</a>` : '') + '</div>' +
      (kids.length ? `<div class="mini">Children: ${kids.map((k) => `<a href="#" data-taxon="${esc(k.name)}">${esc(pretty(k.name))}</a>`).join(' · ')}</div>` : '');
  }
  function countryTable() {
    const rows = [...cur.rows.countries.values()].filter((r) => r.c.n >= MIN_N).sort((a, b) => (state.metric === 'prev' ? b.prev - a.prev : b.mean - a.mean));
    $('#country-table').innerHTML = `<details><summary class="small">Country table (${rows.length} countries with ≥ ${MIN_N} samples)</summary><table class="tbl compact"><thead><tr><th>Country</th><th class="num">n samples</th><th class="num">n studies</th><th class="num">prevalence</th><th class="num">mean abundance</th><th class="num">HDI</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td><a href="#" data-country="${r.c.iso2}">${esc(r.c.name)}</a></td><td class="num">${r.c.n.toLocaleString()}</td><td class="num">${r.c.nstud}</td><td class="num">${fmtPct(r.prev)}</td><td class="num">${fmtAb(r.mean)}</td><td class="num">${r.c.hdi != null ? r.c.hdi.toFixed(2) : '—'}</td></tr>`).join('') + '</tbody></table></details>';
  }
  function ageBar(ages) {
    const tot = ages.reduce((a, b) => a + b, 0) || 1;
    return `<div class="agebar">${ages.map((n, i) => n ? `<span title="${AGES[i]}: ${n}" style="width:${(n / tot * 100).toFixed(1)}%;background:${AGE_COL[AGES[i]]}"></span>` : '').join('')}</div>` +
      `<div class="mini">${ages.map((n, i) => n ? `${AGES[i]} ${n}` : '').filter(Boolean).join(' · ')}</div>`;
  }
  function selectCountry(iso, keep) {
    if (!iso || !st.ISO[iso]) return;
    state.country = iso; recolour();
    const c = st.ISO[iso]; const el = $('#dossier');
    if (!cur) { el.innerHTML = `<b>${esc(c.name)}</b><div class="mini">${c.n.toLocaleString()} samples · ${c.nstud} studies. Pick a taxon first.</div>`; return; }
    const studies = cur.rows.studies.filter((r) => r.s.countries.includes(iso)).sort((a, b) => b.n - a.n);
    const r = cur.rows.countries.get(iso);
    el.innerHTML = `<b>${esc(c.name)}</b> <span class="mini">${c.n.toLocaleString()} samples · ${c.nstud} studies${c.hdi != null ? ' · HDI ' + c.hdi.toFixed(2) : ''}</span>` +
      `<div><i>${esc(pretty(cur.t.name))}</i>: prevalence <b>${r ? fmtPct(r.prev) : '0 %'}</b>, mean abundance ${r ? fmtAb(r.mean) : '0'}</div>` +
      `<div class="mini" style="margin-top:.4rem">Studies with the taxon detected (click for dossier):</div>` +
      `<table class="tbl compact"><thead><tr><th>Study</th><th class="num">n</th><th class="num">prev.</th></tr></thead><tbody>` +
      studies.slice(0, 40).map((x) => `<tr><td><a href="#" data-study="${x.s.i}">${esc(x.s.acc)}</a></td><td class="num">${x.s.n}</td><td class="num">${fmtPct(x.prev)}</td></tr>`).join('') +
      `</tbody></table>${studies.length > 40 ? `<div class="mini">… ${studies.length - 40} more</div>` : ''}<div id="study-dossier"></div>`;
  }
  function studyDossier(i) {
    const s = st.studies[i]; const x = cur.rows.studies.find((r) => r.s.i === i);
    const el = $('#study-dossier') || $('#dossier');
    el.innerHTML = `<hr><b><a href="${ROOT}studies/${esc(s.acc)}.html">${esc(s.acc)}</a></b>${s.title ? `<div class="mini">${esc(s.title)}</div>` : ''}` +
      `<table class="tbl compact kv"><tr><td>samples profiled</td><td>${s.n.toLocaleString()}${s.n_pkg ? ` of ${s.n_pkg.toLocaleString()} in the catalog` : ''}</td></tr>` +
      `<tr><td>countries</td><td>${s.countries.map((k) => esc(st.ISO[k] ? st.ISO[k].name : k)).join(', ') || '—'}</td></tr>` +
      `<tr><td><i>${esc(pretty(cur.t.name))}</i> prevalence</td><td>${x ? fmtPct(x.prev) + ` (${x.n} samples)` : 'not detected'}</td></tr>` +
      `<tr><td>mean abundance</td><td>${x ? fmtAb(x.mean) : '0'}</td></tr>` +
      (s.rich != null ? `<tr><td>median genus richness</td><td>${s.rich.toFixed(0)}</td></tr><tr><td>median root coverage</td><td>${s.depth.toFixed(0)}</td></tr>` : '') +
      `</table><div class="mini">Life stages</div>${ageBar(s.ages)}<a class="btn xs sec" href="${ROOT}studies/${esc(s.acc)}.html">Open study page</a>`;
  }

  // ---------- sidebar ----------
  function listTaxa(q) {
    const ul = $('#taxon-hits'); let items;
    if (q) { const ql = q.toLowerCase(); items = st.taxa.filter((t) => t.name.toLowerCase().includes(ql)).sort((a, b) => b.n - a.n).slice(0, 60); }
    else items = (st.byRank[state.rank] || []).slice().sort((a, b) => b.n - a.n).slice(0, 200);
    ul.innerHTML = items.map((t) => `<li data-idx="${t.idx}"><span>${esc(pretty(t.name))}</span><span class="r">${q ? t.rank + ' · ' : ''}${fmtPct(t.prev)}</span></li>`).join('') || '<li class="mini">no match</li>';
  }
  function toggle(id, key, cb) {
    $(id).addEventListener('click', (ev) => { const b = ev.target.closest('button'); if (!b) return; state[key] = b.dataset.v; $(id).querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b)); cb(); });
  }
  function wire() {
    $('#taxon-q').addEventListener('input', (ev) => listTaxa(ev.target.value.trim()));
    $('#rank-sel').addEventListener('change', (ev) => { state.rank = ev.target.value; $('#taxon-q').value = ''; listTaxa(''); });
    $('#taxon-hits').addEventListener('click', (ev) => { const li = ev.target.closest('li[data-idx]'); if (li) selectTaxon(st.taxa[+li.dataset.idx]); });
    toggle('#metric-toggle', 'metric', () => { if (cur) selectTaxon(cur.t); });
    toggle('#scale-toggle', 'scale', recolour);
    toggle('#proj-toggle', 'proj', () => { redrawGeometry(); recolour(); });
    document.body.addEventListener('click', (ev) => {
      const a = ev.target.closest('a[data-taxon],a[data-country],a[data-study]'); if (!a) return; ev.preventDefault();
      if (a.dataset.taxon) { const t = st.taxa.find((x) => x.name === a.dataset.taxon); if (t) selectTaxon(t); }
      else if (a.dataset.country) selectCountry(a.dataset.country);
      else if (a.dataset.study) studyDossier(+a.dataset.study);
    });
  }
  async function main() {
    await loadCore(); drawBase(); wire(); listTaxa('');
    const h = decodeURIComponent(location.hash.slice(1));
    const start = (h && st.taxa.find((t) => t.name === h)) || st.taxa.find((t) => t.name === 'g__Prevotella') || st.byRank.genus[0];
    if (start) await selectTaxon(start);
  }
  if (typeof d3 === 'undefined' || typeof topojson === 'undefined') { $('#taxon-head').innerHTML = '<div class="note">The map needs D3 and topojson-client from cdn.jsdelivr.net; they did not load (offline or blocked). The data files under data/atlas/ are still downloadable.</div>'; return; }
  main().catch((e) => { $('#taxon-head').innerHTML = `<div class="note">The taxon map failed to load: ${esc(e.message)}</div>`; console.error(e); });
})();
