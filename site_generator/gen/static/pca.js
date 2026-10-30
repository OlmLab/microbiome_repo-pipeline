// Atlas › PCA: Canvas2D scatter of every scored catalog sample. No libraries. Data: data/atlas/pca_points.bin (Float32
// n_points × n_pcs, row-major), pca_codes.bin (per-field Uint8/Uint16 code columns at byte offsets from pca_meta.json) and
// pca_meta.json (labels, sample keys, explained variance, loadings). Layout is documented in the page footer and pages/pca.py.
(function () {
  'use strict';
  const CFG = window.PCA_CFG || {data: '../data/atlas/', samplesUrl: '../samples/index.html', studiesUrl: '../studies/'};
  const $ = id => document.getElementById(id);
  const h = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  // colour-blind-safe qualitative palette (Okabe-Ito first, then Tableau 20 fill); 'other' / '(no value)' are greys
  const PALETTE = ['#E69F00', '#56B4E9', '#009E73', '#F0E442', '#0072B2', '#D55E00', '#CC79A7', '#000000', '#1F77B4', '#AEC7E8', '#FF7F0E', '#FFBB78', '#2CA02C', '#98DF8A', '#D62728', '#FF9896', '#9467BD', '#C5B0D5', '#8C564B', '#C49C94', '#E377C2', '#F7B6D2', '#7F7F7F', '#C7C7C7', '#BCBD22', '#DBDB8D', '#17BECF', '#9EDAE5'];
  const OTHER_COLOR = '#B0B0B0', NOVALUE_COLOR = '#E0E0E0', HILITE = '#C00000';
  // per-field cap of individually coloured categories (top-k by count; the rest fold into 'other')
  const TOPK = {study_accession: 20, country: 15, health_condition: 15, top_genus: 15, age_category: 20, body_site_class: 8, lifestyle: 12};

  let meta, pts, codes = {}, keys, n = 0, npcs = 0;
  let xi = 0, yi = 1, colorField = 'age_category', pointSize = 1.5;
  let view = {sx: 1, sy: 1, tx: 0, ty: 0};         // data -> pixel: px = x*sx + tx ; py = y*sy + ty
  let W = 0, H = 0, dpr = 1;
  let colorMap = null, dimmed = null, hiliteStudy = null, selection = null, dragging = null;
  let grid = null, gridCell = 12, gridCols = 0, gridRows = 0;
  const canvas = $('pca-canvas'), overlay = $('pca-overlay'), wrap = $('pca-canvas-wrap');
  const ctx = canvas.getContext('2d'), octx = overlay.getContext('2d');

  function setBoot(m) { const b = $('boot-msg'); if (b) b.textContent = m; }
  function bootFail(e) { $('boot').innerHTML = '<b>The PCA map could not start.</b> ' + h(e && e.message || e) + ' The scores are downloadable as catalog_sandpiper_pca_scores.parquet (Downloads).'; console.error(e); }

  async function init() {
    try {
      setBoot('metadata…');
      meta = await (await fetch(CFG.data + 'pca_meta.json')).json();
      n = meta.n_points; npcs = meta.n_pcs;
      setBoot('scores…');
      const pbuf = await (await fetch(CFG.data + 'pca_points.bin')).arrayBuffer();
      pts = new Float32Array(pbuf);
      if (pts.length !== n * npcs) throw new Error('pca_points.bin size mismatch: ' + pts.length + ' vs ' + n * npcs);
      setBoot('category codes…');
      const cbuf = await (await fetch(CFG.data + 'pca_codes.bin')).arrayBuffer();
      for (const f of meta.fields) codes[f.field] = f.dtype === 'uint16' ? new Uint16Array(cbuf, f.offset, n) : new Uint8Array(cbuf, f.offset, n);
      keys = meta.keys.split('\n');
      if (keys.length !== n) throw new Error('sample key count mismatch');
      $('boot').style.display = 'none'; $('pca').style.display = '';
      setupControls(); resize(); fitView(); buildColorMap(); buildLegend(); draw(); readUrl();
      window.__pcaReady = true;
    } catch (e) { bootFail(e); }
  }

  function field(name) { return meta.fields.find(f => f.field === name); }

  function setupControls() {
    $('pca-x').onchange = e => { xi = +e.target.value; fitView(); draw(); writeUrl(); };
    $('pca-y').onchange = e => { yi = +e.target.value; fitView(); draw(); writeUrl(); };
    $('pca-color').onchange = e => { colorField = e.target.value; dimmed = null; buildColorMap(); buildLegend(); draw(); writeUrl(); };
    $('pca-size').oninput = e => { pointSize = +e.target.value; draw(); };
    $('pca-reset').onclick = () => { fitView(); draw(); };
    $('pca-clear').onclick = () => { selection = null; dimmed = null; hiliteStudy = null; $('pca-study').value = ''; buildLegend(); draw(); reportSelection(); writeUrl(); };
    const sf = field('study_accession'); const dl = $('pca-study-list');
    if (sf) { const frag = document.createDocumentFragment(); sf.labels.slice(0, sf.labels.length - 2).forEach(a => { const o = document.createElement('option'); o.value = a; frag.appendChild(o); }); dl.appendChild(frag); }
    $('pca-study').oninput = e => { const v = e.target.value.trim().toUpperCase(); hiliteStudy = v && sf && sf.labels.indexOf(v) >= 0 && sf.labels.indexOf(v) < sf.labels.length - 2 ? sf.labels.indexOf(v) : null; draw(); reportSelection(); writeUrl(); };
    window.addEventListener('resize', () => { resize(); draw(); });
    wrap.addEventListener('mousemove', onMove); wrap.addEventListener('mouseleave', () => hideTip());
    wrap.addEventListener('mousedown', onDown); window.addEventListener('mouseup', onUp);
    wrap.addEventListener('wheel', onWheel, {passive: false});
    wrap.addEventListener('dblclick', () => { fitView(); draw(); });
  }

  function resize() {
    dpr = window.devicePixelRatio || 1; W = wrap.clientWidth; H = wrap.clientHeight;
    for (const c of [canvas, overlay]) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); octx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function fitView() {
    // robust range: 0.5–99.5 percentiles so a handful of outliers do not squash the cloud
    const xs = new Float32Array(n), ys = new Float32Array(n);
    for (let i = 0; i < n; i++) { xs[i] = pts[i * npcs + xi]; ys[i] = pts[i * npcs + yi]; }
    xs.sort(); ys.sort();
    const lo = Math.floor(n * 0.005), hi = Math.max(lo, Math.ceil(n * 0.995) - 1);
    const x0 = xs[lo], x1 = xs[hi], y0 = ys[lo], y1 = ys[hi];
    const pad = 0.06, dx = (x1 - x0) || 1, dy = (y1 - y0) || 1;
    view.sx = (W * (1 - 2 * pad)) / dx; view.sy = -(H * (1 - 2 * pad)) / dy;
    view.tx = W * pad - x0 * view.sx; view.ty = H * (1 - pad) - y0 * view.sy;
  }

  function topkOrder(f) {
    // categories ordered by count, top-k get colours, rest -> 'other'; the two trailing labels (other, no value) are greys
    const k = TOPK[f.field] || 15, nl = f.labels.length, other = nl - 2, nov = nl - 1;
    const idx = []; for (let i = 0; i < other; i++) idx.push(i);
    idx.sort((a, b) => f.counts[b] - f.counts[a]);
    return {top: idx.slice(0, k), rest: idx.slice(k), other, nov};
  }

  function buildColorMap() {
    const f = field(colorField); if (!f) return;
    const ord = topkOrder(f); colorMap = new Array(f.labels.length).fill(OTHER_COLOR);
    ord.top.forEach((ci, r) => colorMap[ci] = PALETTE[r % PALETTE.length]);
    colorMap[ord.other] = OTHER_COLOR; colorMap[ord.nov] = NOVALUE_COLOR;
    colorMap._ord = ord;
  }

  function buildLegend() {
    const f = field(colorField), el = $('pca-legend'); if (!f) { el.innerHTML = ''; return; }
    const ord = colorMap._ord; let restN = 0; ord.rest.forEach(ci => restN += f.counts[ci]);
    const rows = ord.top.map(ci => ({ci, label: f.labels[ci], col: colorMap[ci], count: f.counts[ci]}));
    rows.push({ci: ord.other, label: ord.rest.length ? 'other (' + ord.rest.length + ' more' + (f.counts[ord.other] ? ' + folded' : '') + ')' : 'other', col: OTHER_COLOR, count: restN + f.counts[ord.other], group: ord.rest.concat([ord.other])});
    rows.push({ci: ord.nov, label: '(no value)', col: NOVALUE_COLOR, count: f.counts[ord.nov]});
    el.innerHTML = rows.filter(r => r.count > 0).map(r => `<div class="li${dimmed && !inDim(r) ? ' dim' : ''}" data-ci="${r.ci}" data-group="${(r.group || [r.ci]).join(',')}" title="click to show only this category"><span class="sw" style="background:${r.col}"></span><span>${h(r.label)}</span><span class="n">${r.count.toLocaleString()}</span></div>`).join('');
    el.querySelectorAll('.li').forEach(li => li.onclick = () => { const g = li.dataset.group.split(',').map(Number); dimmed = (dimmed && sameSet(dimmed, g)) ? null : new Set(g); buildLegend(); draw(); });
  }
  function inDim(r) { const g = r.group || [r.ci]; return g.some(ci => dimmed.has(ci)); }
  function sameSet(s, arr) { return s.size === arr.length && arr.every(v => s.has(v)); }

  function alphaFor(count) { return Math.min(0.9, Math.max(0.06, 25000 / Math.max(1, count))); }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    const f = field(colorField), cc = codes[colorField], sz = pointSize;
    // draw per category (fewer fillStyle changes); dimmed categories first and lighter
    const byCat = new Map();
    for (let i = 0; i < n; i++) { const c = cc[i]; let a = byCat.get(c); if (!a) { a = []; byCat.set(c, a); } a.push(i); }
    let visible = 0; for (const [c, arr] of byCat) if (!dimmed || dimmed.has(c)) visible += arr.length;
    const alpha = alphaFor(visible);
    const order = [...byCat.keys()].sort((a, b) => byCat.get(b).length - byCat.get(a).length); // big classes underneath
    grid = new Map(); gridCell = Math.max(8, Math.ceil(sz * 4)); gridCols = Math.ceil(W / gridCell) + 1;
    const cx = new Float32Array(n), cy = new Float32Array(n);
    for (let i = 0; i < n; i++) { cx[i] = pts[i * npcs + xi] * view.sx + view.tx; cy[i] = pts[i * npcs + yi] * view.sy + view.ty; }
    for (const c of order) {
      const dim = dimmed && !dimmed.has(c);
      ctx.globalAlpha = dim ? 0.05 : alpha; ctx.fillStyle = dim ? '#DDDDDD' : colorMap[c];
      for (const i of byCat.get(c)) {
        const x = cx[i], y = cy[i]; if (x < -sz || y < -sz || x > W + sz || y > H + sz) continue;
        ctx.fillRect(x - sz / 2, y - sz / 2, sz, sz);
        if (!dim) { const gk = ((y / gridCell) | 0) * gridCols + ((x / gridCell) | 0); let g = grid.get(gk); if (!g) { g = []; grid.set(gk, g); } g.push(i); }
      }
    }
    ctx.globalAlpha = 1;
    if (hiliteStudy !== null) {
      const sc = codes.study_accession; ctx.fillStyle = HILITE; ctx.strokeStyle = '#FFFFFF'; ctx.lineWidth = 1; let m = 0;
      for (let i = 0; i < n; i++) if (sc[i] === hiliteStudy) { const x = cx[i], y = cy[i]; ctx.beginPath(); ctx.arc(x, y, Math.max(3, sz + 1.5), 0, 6.283); ctx.fill(); ctx.stroke(); m++; }
      ctx.fillStyle = HILITE; ctx.font = '13px system-ui, sans-serif'; ctx.fillText((field('study_accession').labels[hiliteStudy]) + ': ' + m.toLocaleString() + ' samples drawn on top', 10, H - 10);
    }
    // axis labels
    ctx.fillStyle = '#444'; ctx.font = '12px system-ui, sans-serif';
    ctx.fillText(axisLabel(xi), W - 120, H - 24); ctx.save(); ctx.translate(14, 120); ctx.rotate(-Math.PI / 2); ctx.fillText(axisLabel(yi), 0, 0); ctx.restore();
    ctx.fillStyle = '#777'; ctx.font = '11px system-ui, sans-serif'; ctx.fillText(visible.toLocaleString() + ' of ' + n.toLocaleString() + ' samples shown · alpha ' + alpha.toFixed(2), 10, 14);
    drawOverlay();
  }
  function axisLabel(i) { const p = meta.pcs[i]; return p ? p.name + ' (' + (p.explained_variance_ratio * 100).toFixed(1) + ' %)' : 'PC' + (i + 1); }

  function drawOverlay() {
    octx.clearRect(0, 0, W, H);
    if (selection) { octx.strokeStyle = '#000'; octx.setLineDash([4, 3]); octx.lineWidth = 1; octx.strokeRect(selection.x0, selection.y0, selection.x1 - selection.x0, selection.y1 - selection.y0); octx.setLineDash([]); octx.fillStyle = 'rgba(207,184,124,0.12)'; octx.fillRect(selection.x0, selection.y0, selection.x1 - selection.x0, selection.y1 - selection.y0); }
  }

  // ---- interaction ----
  function pos(e) { const r = wrap.getBoundingClientRect(); return {x: e.clientX - r.left, y: e.clientY - r.top}; }
  function nearest(px, py) {
    if (!grid) return -1; const gx = (px / gridCell) | 0, gy = (py / gridCell) | 0; let best = -1, bd = 36; // 6 px radius
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) { const g = grid.get((gy + dy) * gridCols + gx + dx); if (!g) continue;
      for (const i of g) { const x = pts[i * npcs + xi] * view.sx + view.tx, y = pts[i * npcs + yi] * view.sy + view.ty; const d = (x - px) * (x - px) + (y - py) * (y - py); if (d < bd) { bd = d; best = i; } } }
    return best;
  }
  function labelOf(fname, i) { const f = field(fname); return f ? f.labels[codes[fname][i]] : ''; }
  function onMove(e) {
    const p = pos(e);
    if (dragging) {
      if (dragging.mode === 'pan') { view.tx += p.x - dragging.last.x; view.ty += p.y - dragging.last.y; dragging.last = p; draw(); }
      else { selection = {x0: Math.min(dragging.start.x, p.x), y0: Math.min(dragging.start.y, p.y), x1: Math.max(dragging.start.x, p.x), y1: Math.max(dragging.start.y, p.y)}; drawOverlay(); }
      return;
    }
    const i = nearest(p.x, p.y); if (i < 0) { hideTip(); return; }
    const st = labelOf('study_accession', i), title = (meta.study_titles || {})[st] || '';
    const tip = $('pca-tip');
    tip.innerHTML = `<div><a href="${CFG.samplesUrl}?sample=${encodeURIComponent(keys[i])}"><b class="mono">${h(keys[i])}</b></a></div>` +
      `<div><span class="k">study</span> <a href="${CFG.studiesUrl}${encodeURIComponent(st)}.html" class="mono">${h(st)}</a>${title ? ' <span class="small">' + h(title) + '</span>' : ''}</div>` +
      ['age_category', 'country', 'health_condition', 'top_genus', 'lifestyle'].filter(f => field(f)).map(f => `<div><span class="k">${h(field(f).label.toLowerCase())}</span> ${h(labelOf(f, i))}</div>`).join('') +
      `<div class="k">${axisLabel(xi)} ${pts[i * npcs + xi].toFixed(2)} · ${axisLabel(yi)} ${pts[i * npcs + yi].toFixed(2)}</div>`;
    tip.style.display = ''; tip.style.left = Math.min(W - 350, p.x + 12) + 'px'; tip.style.top = Math.min(H - 140, p.y + 12) + 'px';
  }
  function hideTip() { $('pca-tip').style.display = 'none'; }
  function onDown(e) { if (e.button !== 0 || e.target.closest('.pca-tip')) return; const p = pos(e); dragging = $('pca-select-mode').checked || e.shiftKey ? {mode: 'select', start: p} : {mode: 'pan', last: p}; hideTip(); e.preventDefault(); }
  function onUp() { if (!dragging) return; const was = dragging; dragging = null; if (was.mode === 'select') reportSelection(); }
  function onWheel(e) { e.preventDefault(); const p = pos(e); const k = Math.exp(-e.deltaY * 0.0015); view.sx *= k; view.sy *= k; view.tx = p.x - (p.x - view.tx) * k; view.ty = p.y - (p.y - view.ty) * k; draw(); }

  function reportSelection() {
    const el = $('pca-selection'); if (!selection) { el.innerHTML = hiliteStudy !== null ? '' : ''; return; }
    const sc = codes.study_accession, sf = field('study_accession'), cc = codes[colorField], cf = field(colorField);
    const studies = new Map(), cats = new Map(); let m = 0;
    for (let i = 0; i < n; i++) {
      if (dimmed && !dimmed.has(cc[i])) continue;
      const x = pts[i * npcs + xi] * view.sx + view.tx, y = pts[i * npcs + yi] * view.sy + view.ty;
      if (x >= selection.x0 && x <= selection.x1 && y >= selection.y0 && y <= selection.y1) { m++; studies.set(sc[i], (studies.get(sc[i]) || 0) + 1); cats.set(cc[i], (cats.get(cc[i]) || 0) + 1); }
    }
    const topS = [...studies.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5).map(([c, k]) => `<a href="${CFG.studiesUrl}${encodeURIComponent(sf.labels[c])}.html" class="mono">${h(sf.labels[c])}</a> ${k.toLocaleString()}`).join(', ');
    const topC = [...cats.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([c, k]) => `${h(cf.labels[c])} ${k.toLocaleString()}`).join(', ');
    let html = `<b>${m.toLocaleString()}</b> samples selected from ${studies.size.toLocaleString()} stud${studies.size === 1 ? 'y' : 'ies'}.`;
    if (studies.size === 1 && m > 0) { const acc = sf.labels[[...studies.keys()][0]]; if (acc !== 'other' && acc !== '(no value)') html += ` <a class="btn xs" href="${CFG.samplesUrl}?study=${encodeURIComponent(acc)}">open selection in sample explorer</a>`; }
    if (m) html += `<div class="small">studies: ${topS}${studies.size > 5 ? ', …' : ''}</div><div class="small">${h(cf.label.toLowerCase())}: ${topC}</div>`;
    el.innerHTML = html;
  }

  function writeUrl() { const p = new URLSearchParams(); if (xi !== 0) p.set('x', xi + 1); if (yi !== 1) p.set('y', yi + 1); if (colorField !== 'age_category') p.set('color', colorField); if (hiliteStudy !== null) p.set('study', field('study_accession').labels[hiliteStudy]); history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p.toString() : '')); }
  function readUrl() {
    const p = new URLSearchParams(location.search); let redraw = false;
    if (p.get('x')) { xi = Math.min(npcs - 1, Math.max(0, +p.get('x') - 1)); $('pca-x').value = xi; redraw = true; }
    if (p.get('y')) { yi = Math.min(npcs - 1, Math.max(0, +p.get('y') - 1)); $('pca-y').value = yi; redraw = true; }
    if (p.get('color') && field(p.get('color'))) { colorField = p.get('color'); $('pca-color').value = colorField; buildColorMap(); buildLegend(); redraw = true; }
    if (p.get('study')) { $('pca-study').value = p.get('study'); $('pca-study').dispatchEvent(new Event('input')); }
    if (redraw) { fitView(); draw(); }
  }

  init();
})();
