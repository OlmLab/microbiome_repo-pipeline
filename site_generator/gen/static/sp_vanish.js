/* sp_vanish.js — the VANISH/BloSSUM page: one interactive plot of every tested taxon.
   Uses window.SP (sp_core.js): segmented toggles, search box, tooltip, zoomable scatter, JSON cache.
   Data: data/vanish/meta.json and one points file per level x age group x rule, fetched on demand. Each
   points file holds only the points that are drawn (round 3); the count line and the CSV downloads carry
   every tested taxon.
   Horizontal axis: the model score (log2 odds ratio, non-industrialized vs reference) or, for the simple
   rule, lfc. Vertical axis: the prevalence in the group where the taxon is more common — with permutation
   p-values the q-values tie at the resolution floor, so a volcano axis would stack the strongest taxa on one
   line. The vertical scale is a square root so that the many rare taxa do not pile up on the axis.
   A click opens the taxon in the taxon explorer (genus and species; families have no entry there).
   Round 4: the search marks the taxon with a star and its name and leaves the view whole; for infants the
   model rule is drawn from the broad definition (block C4), the only one with enough cohorts. */
(function () {
  'use strict';
  var P = window.SP_PAGE || {}, SP = window.SP;
  var el = {
    plot: document.getElementById('vb-plot'), count: document.getElementById('vb-count'),
    note: document.getElementById('vb-note'), ready: document.getElementById('vb-ready'),
    level: document.getElementById('vb-level'), stratum: document.getElementById('vb-stratum'),
    rule: document.getElementById('vb-rule'), ref: document.getElementById('vb-ref'),
    refWrap: document.getElementById('vb-ref-wrap'), pub: document.getElementById('vb-pub'),
    search: document.getElementById('vb-search')
  };
  var state = { level: 'species', stratum: 'adult', rule: 'model', ref: 'C1', pub: 'off', star: null, shown: 'C1' };
  var meta = null, chart = null, current = [], pubs = {};

  // every genus and species has an entry in the taxon explorer; families do not
  function hasEntry() { return state.level !== 'family'; }
  function taxonUrl(name) { return P.taxaHref + 'index.html#taxon=' + encodeURIComponent(String(name)); }

  function unpack(file, blockKey) {
    var fields = file.fields, cols = file.blocks[blockKey].cols, n = (cols[fields[0]] || []).length, out = [];
    for (var i = 0; i < n; i++) {
      var d = {};
      for (var f = 0; f < fields.length; f++) {
        var k = fields[f];
        d[k] = (k === 'c') ? (file.clades[cols[k][i]] || '') : cols[k][i];
      }
      d.key = d.n;
      out.push(d);
    }
    return out;
  }
  function pointsUrl() {
    return P.dataDir + 'points_' + state.level + '_' + state.stratum + '_' + state.rule + '.json';
  }
  // the block that is drawn: the chosen reference group when it can be estimated; otherwise (model rule,
  // infants) the first definition that can — the page stage adds the broad definition C4 for that case
  function blockKey(file) {
    if (state.rule === 'simple') return 'S';
    var b = file && file.blocks ? file.blocks : {};
    if (b[state.ref] && b[state.ref].estimable && b[state.ref].n_tested) return state.ref;
    var alt = ['C1', 'C2', 'C4'];
    for (var i = 0; i < alt.length; i++) {
      if (b[alt[i]] && b[alt[i]].estimable && b[alt[i]].n_tested) return alt[i];
    }
    return state.ref;
  }
  function applyStar(resetView) {     // the searched taxon: a star and its name, the view stays whole
    if (!chart) return;
    var d = null;
    if (state.star) d = current.filter(function (o) { return o.n === state.star; })[0] || null;
    if (resetView && d) chart.reset();
    chart.star(d);
    el.plot.dataset.star = d ? String(d.n) : '';
  }
  // non-industrialized on the LEFT: the score and lfc are positive when a taxon is more common there,
  // so the axis runs from high to low (spec.xReverse) and the tick numbers stay the true score.
  function xOf(d) { return state.rule === 'simple' ? d.l : d.s; }
  // the group where the taxon is more common: the pooled share would be dominated by the much larger
  // reference group, which pushes every VANISH taxon onto the floor.
  function yOf(d) { return Math.max(d.pn || 0, (state.rule === 'simple' ? d.pi : d.pr) || 0); }
  function classOf(d) { return d.k === 'V' ? 'V' : (d.k === 'B' ? 'B' : (d.k === 'S' ? 'S' : 'N')); }
  var COLOUR = { V: '#CFB87C', B: '#2F5E8C', S: '#8A4A1A', N: '#A2A4A3' };  // site gold, blue, warn, neutral
  var RING = { V: '#8A7536', B: '#2F5E8C', M: '#1a1a1a' };                  // a published list's direction
  function colourOf(d) { return COLOUR[classOf(d)]; }
  function pubOf(d) { return d.pb ? pubs[d.pb] : null; }
  function strokeOf(d) {
    if (state.pub !== 'on') return null;
    var p = pubOf(d);
    return p ? (RING[p.dir] || RING.M) : null;
  }
  function className(k) {
    return k === 'V' ? 'VANISH' : k === 'B' ? 'BloSSUM' : k === 'S' ? 'StIL candidate' : 'neither class';
  }

  function tipHtml(d) {
    var h = '<b>' + SP.esc(String(d.n).replace(/^[a-z]__/, '')) + '</b>';
    if (d.c) h += '<br><span class="small">' + SP.esc(d.c) + '</span>';
    if (state.rule === 'simple') {
      h += '<br>lfc ' + d.l.toFixed(2) + ' <span class="small">(log2 fold change in carriage, non-industrialized vs industrialized)</span> · q ' + fmtQ(d.q);
      h += '<br>carried by ' + SP.fmtPct(d.pn) + ' of non-industrialized subjects, ' + SP.fmtPct(d.pi) + ' of industrialized';
      h += '<br>' + d.nc + ' countries with a carrier';
    } else {
      h += '<br>score ' + (d.s > 0 ? '+' : '') + d.s.toFixed(2) + (d.se != null ? ' ± ' + d.se.toFixed(2) : '') +
           ' <span class="small">(log2 odds ratio of detection, non-industrialized vs reference)</span> · q ' + fmtQ(d.q);
      h += '<br>detected in ' + SP.fmtPct(d.pn) + ' of non-industrialized samples, ' + SP.fmtPct(d.pr) + ' of reference (mean over cohorts)';
      h += '<br>cohorts with detection: ' + d.cn + ' non-industrialized, ' + d.cr + ' reference';
    }
    h += '<br>' + (d.k ? '<b>' + className(d.k) + '</b>' : 'neither class');
    var p = pubOf(d);
    if (p) h += '<br><span class="small">on the ' + SP.esc(p.label) + '</span>';
    h += '<br><span class="small">' + (hasEntry() ? 'click to open this taxon in the explorer'
                                                  : 'families have no entry in the taxon explorer') + '</span>';
    return h;
  }
  function fmtQ(q) {
    if (q === null || q === undefined) return '–';
    return q < 0.001 ? q.toExponential(1) : q.toFixed(3);
  }
  function onClick(d) {
    if (hasEntry()) SP.go(taxonUrl(d.n));
    else if (chart) chart.mark(function (o) { return o.n === d.n; });
  }

  function countLine(blk) {    // always the whole tested set, not only the taxa drawn
    var t = SP.fmtInt(blk.n_vanish) + ' VANISH, ' + SP.fmtInt(blk.n_blossum) + ' BloSSUM';
    if (blk.n_stil) t += ', ' + SP.fmtInt(blk.n_stil) + ' StIL candidates';
    el.count.textContent = t + ' of ' + SP.fmtInt(blk.n_tested) + ' taxa tested';
  }

  function render(file) {
    pubs = {};
    (file.pubs || []).forEach(function (p) { pubs[p.k] = p; });
    var key = blockKey(file), blk = file.blocks[key];
    state.shown = key;
    var fallback = state.rule !== 'simple' && key !== state.ref;
    setRefVisibility(fallback);
    el.note.textContent = '';
    if (!blk || !blk.estimable || !blk.n_tested) {
      current = [];
      el.plot.innerHTML = '';
      el.plot.dataset.ready = '0';
      el.count.textContent = '';
      el.note.textContent = (blk && blk.note) ? blk.note : 'not estimable in this release';
      if (el.ready) el.ready.textContent = '0';
      return;
    }
    current = unpack(file, key);
    // draw the classed taxa last so that a classed point is never hidden under a grey one
    current.sort(function (a, b) { return (a.k === '' ? 0 : 1) - (b.k === '' ? 0 : 1); });
    var xEnds = ['more common in non-industrialized people', 'more common in industrialized people'];
    var spec = {
      points: current, width: P.width || 900, height: P.height || 470,
      x: xOf, y: yOf, xEnds: xEnds, xReverse: true, xLabel: '',
      yLabel: 'carried by (in the group where it is more common)',
      yFormat: function (v) { return Math.round(v * 100) + ' %'; },
      classOf: classOf, colour: colourOf, stroke: strokeOf, tipHtml: tipHtml, onClick: onClick, yScale: 'sqrt',
      starLabel: function (d) { return String(d.n).replace(/^[a-z]__/, ''); },
      r: function (d) { return d.k ? 5 : (current.length > 4000 ? 2.4 : 3.4); },
      alt: 'Every tested taxon from more common in non-industrialized people on the left to more common in industrialized people on the right'
    };
    if (chart) chart.update(current, spec); else chart = SP.scatter(el.plot, spec);
    el.plot.dataset.ready = String(current.length);
    if (el.ready) el.ready.textContent = String(current.length);
    countLine(blk);
    var hidden = blk.n_hidden || 0;
    el.note.textContent = (fallback && blk.view_note ? blk.view_note + ' ' : '') + (hidden
      ? SP.fmtInt(hidden) + ' taxa carried by fewer than 1 % of either group are not drawn; they are in the CSV downloads.' : '');
    el.plot.dataset.block = key;
    applyStar(false);
  }

  function load() {
    el.note.textContent = 'Loading…';
    return SP.getJSON(pointsUrl()).then(render).catch(function (e) {
      el.note.textContent = 'This combination has no data file in this release.';
      el.plot.innerHTML = ''; el.count.textContent = ''; current = [];
      if (el.ready) el.ready.textContent = '0';
    });
  }

  function setRefVisibility(fallback) {   // no choice of reference group under the simple rule or a fallback view
    var hide = (state.rule === 'simple') || !!fallback;
    if (el.refWrap) el.refWrap.hidden = hide;
    var lab = document.getElementById('vb-ref-label');
    if (lab) lab.hidden = hide;
  }

  function start() {
    SP.segmented(el.level, { label: 'Level', value: state.level, options: [
      { value: 'species', label: 'Species' }, { value: 'genus', label: 'Genus' }, { value: 'family', label: 'Family' }],
      onChange: function (v) { state.level = v; load(); } });
    SP.segmented(el.stratum, { label: 'Age group', value: state.stratum, options: [
      { value: 'adult', label: 'Adults' }, { value: 'infant', label: 'Infants' }],
      onChange: function (v) { state.stratum = v; load(); } });
    SP.segmented(el.rule, { label: 'Rule', value: state.rule, options: [
      { value: 'model', label: 'Model-based', title: 'cohort random effect, permutation p-values, leave one cohort out' },
      { value: 'simple', label: 'Simple', title: 'subject-level carriage by HDI band, Fisher exact test' }],
      onChange: function (v) { state.rule = v; setRefVisibility(); load(); } });
    SP.segmented(el.ref, { label: 'Reference group', value: state.ref, options: [
      { value: 'C1', label: 'High-HDI countries' }, { value: 'C2', label: 'Stated urban industrialized' }],
      onChange: function (v) { state.ref = v; render_cached(); } });
    if (el.pub) SP.segmented(el.pub, { label: 'Published lists', value: state.pub, options: [
      { value: 'off', label: 'Not marked' },
      { value: 'on', label: 'Mark', title: 'ring the taxa that a published VANISH or BloSSUM list names' }],
      onChange: function (v) { state.pub = v; render_cached(); } });
    setRefVisibility();
    SP.searchBox({
      input: el.search.querySelector('input'), results: el.search.querySelector('.sp-hits'),
      limit: 8, empty: 'No taxon of that name in this view.',
      items: function () {           // the list changes with every toggle, so it is built at each search
        return current.map(function (d) {
          return { name: String(d.n).replace(/^[a-z]__/, ''), sub: d.c, d: d,
                   url: hasEntry() ? taxonUrl(d.n) : '#',
                   html: '<i>' + SP.esc(String(d.n).replace(/^[a-z]__/, '')) + '</i>' };
        });
      },
      onPick: function (it) { state.star = it.d.n; applyStar(true); }
    });
    var sin = el.search.querySelector('input');
    sin.addEventListener('input', function () {      // an emptied search box takes the star away
      if (!sin.value.replace(/\s+/g, '') && state.star) { state.star = null; applyStar(false); }
    });
    return SP.getJSON(P.dataDir + 'meta.json').then(function (m) { meta = m; }).then(load);
  }
  function render_cached() { return SP.getJSON(pointsUrl()).then(render); }

  window.SP_VANISH = { start: start, state: state, points: function () { return current; },
                       taxonUrl: taxonUrl, chart: function () { return chart; } };
  if (!P.manualStart) start();
})();
