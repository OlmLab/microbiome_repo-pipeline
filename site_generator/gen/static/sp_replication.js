/* sp_replication.js — Atlas > Replication of published findings.
   Uses window.SP (sp_core.js: search box, segmented buttons, tooltip, bar chart, JSON cache) and window.d3.
   Reads ONE file, data/replication/replication.json, and draws everything in the browser; no chart image.

   Three views of one graphic, kept in the address so that a view can be shared and Back works:
     (none)                  every condition with a testable claim: the share of its claims with each result
     #condition=<name>       the claims of one condition, one row per claim, ordered by how well it held up
     #taxon=<name>           the claims about one taxon across conditions (same drawing)
     #claim=<BugSigDB id>    the list the claim belongs to, with the claim open in the panel: direction claimed,
                             result, the estimate of every project, the paper, the taxon in the taxon explorer
   A taxon opens in the taxon explorer as taxaHref + 'index.html#taxon=<GTDB name>'.
   The only absolute URL built here is https://www.ncbi.nlm.nih.gov/pubmed/<id>, used in <a href> only.
   All SVG marks carry their colours and type sizes as attributes (the site's style sheet has page-wide
   rules that would otherwise leak into them), and every class is prefixed rp-. */
(function () {
  'use strict';
  var P = window.SP_PAGE || {}, SP = window.SP, d3 = window.d3, esc = SP.esc;
  var $ = function (id) { return document.getElementById(id); };
  var el = {
    plot: $('rp-plot'), crumbs: $('rp-crumbs'), head: $('rp-viewhead'), status: $('rp-status'),
    sort: $('rp-sort'), sortWrap: $('rp-sort-wrap'), filter: $('rp-filter'), filterWrap: $('rp-filter-wrap'),
    more: $('rp-more'), moreBtn: $('rp-more-btn'), caption: $('rp-caption'), under: $('rp-under'),
    panel: $('rp-panel'), split: $('rp-split'), search: $('rp-search'), firm: $('rp-firm-chart'),
    firmBox: $('rp-firm')
  };
  // result codes 0..6, as in the data file's "status" list
  var COL = ['#2F5E8C', '#8FB3E3', '#D9DBDA', '#8E9392', '#B4521A', '#ffffff', '#ffffff'];
  var INK = ['#2F5E8C', '#4f7fb8', '#8b8f8e', '#5f6463', '#B4521A', '#9a9a9a', '#9a9a9a'];
  var LABEL = ['Replicated', 'Replicated, but the effect is small', 'Not replicated: inconclusive',
    'Not replicated: a meaningful effect is ruled out', 'Not replicated: opposite direction',
    'Not tested: too few projects', 'Not tested: taxon too rare'];
  var CAP = 40;                       // rows drawn before "show all"
  var PUBMED = 'https://www.ncbi.nlm.nih.gov/pubmed/';
  var D = null, M = null, claims = [], tests = [], conds = [], taxa = [];
  var condByName = Object.create(null), taxonByName = Object.create(null), claimById = Object.create(null);   // no inherited keys: an address is outside input
  var state = { view: 'all', cond: null, taxon: null, sel: null, selId: null, sort: 'claims', filter: 'all', showAll: false };

  function pct(x, dp) { return (100 * x).toFixed(dp === undefined ? 1 : dp) + ' %'; }
  function f3(x) { return x.toFixed(3); }
  function fq(q) {                    // "q = 0.012"; two significant digits, never rounded across the 5 % line
    if (q < 0.001) return 'q < 0.001';
    var r = q < 0.1 ? Number(q.toPrecision(2)) : Number(q.toFixed(2));
    if ((r < 0.05) !== (q < 0.05)) r = Number(q.toPrecision(3));
    return 'q = ' + r;
  }
  function wilson(k, n) {
    if (!n) return [0, 1];
    var z = 1.959963984540054, p = k / n, d = 1 + z * z / n, c = p + z * z / (2 * n);
    var h = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n));
    return [Math.max(0, (c - h) / d), Math.min(1, (c + h) / d)];
  }
  function plural(n, one, many) { return SP.fmtInt(n) + ' ' + (n === 1 ? one : (many || one + 's')); }
  function cut(s, room) { s = String(s); return s.length > room ? s.slice(0, Math.max(1, room - 1)) + '…' : s; }

  // ------------------------------------------------------------------ links
  function hashOf(kind, value) { return kind + '=' + encodeURIComponent(value); }
  function explorerUrl(t) {            // null when no profile of the release carries the taxon
    if (t.open === '-') return null;
    return P.taxaHref + 'index.html#taxon=' + encodeURIComponent(t.open || t.gtdb);
  }
  function gtdbLinks(t, limit) {       // the GTDB groups counted for a claim taxon, each a link into the taxon explorer
    var names = [(t.open === '-' || (t.open && t.open !== t.gtdb) ? '!' : '') + t.gtdb].concat(t.targets);
    var out = names.slice(0, limit).map(function (n) {
      var name = n.replace(/^!/, '');
      return n.charAt(0) === '!' ? '<code>' + esc(name) + '</code>'
        : '<a class="rp-explore" href="' + esc(P.taxaHref + 'index.html#taxon=' + encodeURIComponent(name)) + '"><code>' + esc(name) + '</code></a>';
    });
    if (names.length > limit) out.push('and ' + (names.length - limit) + ' more');
    return out.join(', ');
  }
  function projectUrl(acc) { return P.studiesHref + encodeURIComponent(acc) + '.html'; }
  function paperUrl(pmid) { return PUBMED + pmid; }

  // ------------------------------------------------------------------ index the data file
  function build(data) {
    D = data; M = data.meta;
    var cl = data.claims, n = cl.id.length, nT = cl.n_testable, map = {}, i;
    conds = data.conditions.name.map(function (name, i) {
      return { i: i, name: name, efo: data.conditions.efo[i], projects: data.conditions.projects[i],
               cases: data.conditions.cases[i], controls: data.conditions.controls[i],
               counts: [0, 0, 0, 0, 0, 0, 0], n: 0, testable: 0, replicated: 0, tests: [] };
    });
    taxa = data.taxa.name.map(function (name, i) {
      return { i: i, name: name, rank: data.taxa.rank[i] ? 'species' : 'genus', gtdb: data.taxa.gtdb[i],
               open: data.taxa.open[i], targets: data.taxa.targets[i] || [],
               counts: [0, 0, 0, 0, 0, 0, 0], n: 0, testable: 0, replicated: 0, tests: [], conds: {} };
    });
    claims = new Array(n); tests = [];
    for (i = 0; i < n; i++) {
      var c = { i: i, id: cl.id[i], c: cl.c[i], t: cl.t[i], p: cl.p[i], up: cl.s[i] === 1, q: cl.q[i], st: cl.st[i] };
      var key = c.c + '|' + c.t + '|' + cl.s[i] + '|';
      if (i < nT) {
        c.g = cl.g[i]; c.auc = cl.auc[i] / 1e6; c.lo = cl.lo[i] / 1e6; c.hi = cl.hi[i] / 1e6; c.tu = cl.tu[i] / 1e6; c.k = cl.k[i];
        c.qr = cl.qr[i]; c.qo = cl.qo[i]; c.hk = cl.hk[i] === 1; c.i2 = cl.i2[i]; c.x = cl.x[i] || [];
        key += c.g + '|' + c.x.join(',');
      } else key += 'u' + c.st;
      var t = map[key];
      if (!t) {          // claims of several papers tested on exactly the same projects are one row
        t = map[key] = { key: key, c: c.c, t: c.t, up: c.up, st: c.st, claims: [], first: c };
        if (i < nT) {
          t.g = c.g; t.auc = c.auc; t.lo = c.lo; t.hi = c.hi; t.k = c.k; t.qr = c.qr; t.qo = c.qo; t.hk = c.hk;
          t.i2 = c.i2; t.x = c.x; t.tu = c.tu;
          t.agree = c.up ? c.auc : 1 - c.auc;      // 0.5 = no difference; above 0.5 = as the paper claimed
          t.alo = c.up ? c.lo : 1 - c.hi; t.ahi = c.up ? c.hi : 1 - c.lo;
        }
        tests.push(t); conds[c.c].tests.push(t); taxa[c.t].tests.push(t);
      }
      t.claims.push(c); c.test = t; claims[i] = c; claimById[c.id] = c;
      [conds[c.c], taxa[c.t]].forEach(function (o) {
        o.counts[c.st]++; o.n++;
        if (c.st < 5) o.testable++;
        if (c.st < 2) o.replicated++;
      });
      taxa[c.t].conds[c.c] = 1;
    }
    conds.forEach(function (c) { condByName[c.name.toLowerCase()] = c; });
    taxa.forEach(function (t) {
      taxonByName[t.name.toLowerCase()] = t;
    });
    taxa.forEach(function (t) {      // a GTDB name finds the claim taxon of the same name first, else the first one counted as it
      var k = t.gtdb.toLowerCase(), same = t.gtdb.replace(/^[a-z]__/, '') === t.name;
      if (!taxonByName[k] || (same && taxonByName[k].gtdb.replace(/^[a-z]__/, '') !== taxonByName[k].name)) taxonByName[k] = t;
    });
  }
  function papersOf(t) {               // the row's claims grouped by paper: [{p, claims: [...]}], every claim kept
    var seen = {}, out = [];
    t.claims.forEach(function (c) {
      if (!seen[c.p]) { seen[c.p] = { p: c.p, claims: [] }; out.push(seen[c.p]); }
      seen[c.p].claims.push(c);
    });
    return out;
  }
  function sigOf(c) { return c.id.split('|')[0].replace(/^bsdb:/, ''); }    // BugSigDB study/experiment/signature

  // ------------------------------------------------------------------ address
  function parseHash() {
    var h = String(window.location.hash || '').replace(/^#/, '');
    var m = /^(condition|taxon|claim)=(.*)$/.exec(h);
    if (!m) return { view: 'all' };
    var v;
    try { v = decodeURIComponent(m[2]); } catch (e) { v = m[2]; }
    return { view: m[1], value: v };
  }
  function nav(hash) {                 // the one place a view changes: write the address, then draw from it
    var target = hash ? '#' + hash : '';
    if ((window.location.hash || '') !== target) {
      if (window.history && window.history.pushState) {
        window.history.pushState(null, '', target || (window.location.pathname + window.location.search));
      } else window.location.hash = target;
    }
    route();
  }
  function route() {
    var r = parseHash(), prev = state.view, prevKey = state.cond ? 'c' + state.cond.i : (state.taxon ? 't' + state.taxon.i : '');
    var missing = null;
    state.sel = null; state.selId = null;
    if (r.view === 'condition') {
      var c = condByName[String(r.value).toLowerCase()];
      if (c) { state.view = 'condition'; state.cond = c; state.taxon = null; } else missing = r.value;
    } else if (r.view === 'taxon') {
      var t = taxonByName[String(r.value).toLowerCase()];
      if (t) { state.view = 'taxon'; state.taxon = t; state.cond = null; } else missing = r.value;
    } else if (r.view === 'claim') {
      var cm = claimById[r.value];
      if (cm) {
        // stay in the taxon list when the claim was opened from it; otherwise show the claim's condition
        if (!(prev === 'taxon' && state.taxon && state.taxon.i === cm.t)) {
          state.view = 'condition'; state.cond = conds[cm.c]; state.taxon = null;
        }
        state.sel = cm.test; state.selId = cm.id;
      } else missing = r.value;
    }
    if (r.view === 'all' || missing !== null) { state.view = 'all'; state.cond = null; state.taxon = null; }
    var key = state.cond ? 'c' + state.cond.i : (state.taxon ? 't' + state.taxon.i : '');
    if (key !== prevKey) { state.filter = 'all'; state.showAll = false; }
    if (state.sel) {                   // the open claim must be among the rows drawn
      if (!passes(state.sel, state.filter)) state.filter = 'all';
      if (listRows().indexOf(state.sel) >= CAP) state.showAll = true;
    }
    render();
    if (missing !== null) {
      el.status.hidden = false;
      el.status.textContent = '“' + missing + '” is not among the registered claims. Search for a taxon or a condition above.';
    }
  }

  // ------------------------------------------------------------------ drawing: shared pieces
  function plotWidth() { return P.width || el.plot.clientWidth || 940; }
  function svgIn(node, w, h, label, cls) {
    node.innerHTML = '';
    return d3.select(node).append('svg').attr('class', 'sp-chart rp-chart ' + (cls || ''))
      .attr('viewBox', '0 0 ' + w + ' ' + h).attr('width', '100%').attr('height', h)
      .attr('role', 'img').attr('aria-label', label).attr('font-family', 'inherit');
  }
  function txt(g, x, y, s, o) {
    o = o || {};
    var t = g.append('text').attr('x', x).attr('y', y).attr('font-size', o.size || 12).attr('fill', o.fill || '#222')
      .text(s);
    if (o.anchor) t.attr('text-anchor', o.anchor);
    if (o.weight) t.attr('font-weight', o.weight);
    if (o.italic) t.attr('font-style', 'italic');
    if (o.cls) t.attr('class', o.cls);
    return t;
  }
  function tri(g, x, y, dir, fill) {   // a small drawn arrowhead: the charts do not depend on a font having arrow glyphs
    var a = 4, pts = dir === 'up' ? [x - a, y + a, x + a, y + a, x, y - a] : dir === 'down' ? [x - a, y - a, x + a, y - a, x, y + a]
      : dir === 'left' ? [x + a, y - a, x + a, y + a, x - a, y] : [x - a, y - a, x - a, y + a, x + a, y];
    return g.append('polygon').attr('class', 'rp-tri rp-tri-' + dir).attr('points', pts.join(' ')).attr('fill', fill || '#777');
  }
  function axisStyle(g) {
    g.selectAll('path,line').attr('stroke', '#d0d0d0').attr('fill', 'none');
    g.selectAll('text').attr('fill', '#777').attr('font-size', 10.5);
  }
  function clickable(g, fn, label) {
    g.attr('tabindex', 0).attr('role', 'button').attr('aria-label', label).style('cursor', 'pointer')
      .on('click', fn)
      .on('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); fn(ev); } });
  }
  function breakdown(o) {             // the five results of an object with counts[], as tooltip lines
    var h = '';
    for (var s = 0; s < 5; s++) {
      if (!o.counts[s]) continue;
      h += '<br><span class="rp-dot" style="background:' + COL[s] + '"></span>' + SP.fmtInt(o.counts[s]) + ' ' +
           esc(LABEL[s].toLowerCase());
    }
    var u = o.counts[5] + o.counts[6];
    if (u) h += '<br><span class="sp-tip-sub">' + SP.fmtInt(u) + ' more could not be tested</span>';
    return h;
  }
  function rateLine(o) {
    var ci = wilson(o.replicated, o.testable);
    return '<b>' + SP.fmtInt(o.replicated) + ' of ' + SP.fmtInt(o.testable) + '</b> testable claims replicated (' +
      pct(o.replicated / o.testable) + '; 95 % CI ' + (100 * ci[0]).toFixed(1) + '–' + (100 * ci[1]).toFixed(1) + ')';
  }

  // ------------------------------------------------------------------ view 1: every condition
  function drawOverview() {
    var W = plotWidth(), narrow = W < 640;
    var labelW = narrow ? Math.round(W * 0.36) : Math.min(250, Math.round(W * 0.27)), rightW = narrow ? 112 : 168;
    var rowH = 27, top = 40, minClaims = M.checks.across_conditions.min_claims;
    var main = conds.filter(function (c) { return c.testable >= minClaims; });
    var small = conds.filter(function (c) { return c.testable > 0 && c.testable < minClaims; });
    var byClaims = function (a, b) { return b.testable - a.testable || a.name.localeCompare(b.name); };
    main.sort(state.sort === 'rate'
      ? function (a, b) { return b.replicated / b.testable - a.replicated / a.testable || byClaims(a, b); } : byClaims);
    small.sort(byClaims);
    var all = { name: 'All conditions', counts: [0, 0, 0, 0, 0, 0, 0], testable: M.testable, replicated: M.replicated, all: true };
    conds.forEach(function (c) { for (var s = 0; s < 7; s++) all.counts[s] += c.counts[s]; });
    var rows = [{ o: all, y: top }], y = top + rowH + 12;
    main.forEach(function (c) { rows.push({ o: c, y: y }); y += rowH; });
    var yMainEnd = y, ySmall = null;
    if (small.length) {
      ySmall = y + 26; y += 38;
      small.forEach(function (c) { rows.push({ o: c, y: y, faint: true }); y += rowH; });
    }
    var H = y + 14;
    var svg = svgIn(el.plot, W, H, 'Share of testable claims with each result, by condition', 'rp-overview');
    var x = d3.scaleLinear().domain([0, 1]).range([labelW, W - rightW]);
    axisStyle(svg.append('g').attr('class', 'rp-axis').attr('transform', 'translate(0,' + (top - 8) + ')')
      .call(d3.axisTop(x).ticks(narrow ? 2 : 4).tickFormat(function (v) { return Math.round(v * 100) + ' %'; }).tickSizeOuter(0)));
    txt(svg, W - rightW + 26, top - 14, 'replicated', { size: 10.5, fill: '#777' });
    var xr = x(M.rate);                // all conditions together, as a reference through the rows
    svg.append('line').attr('class', 'rp-ref').attr('x1', xr).attr('x2', xr).attr('y1', top + rowH + 6)
      .attr('y2', yMainEnd + 12).attr('stroke', '#333').attr('stroke-width', 1).attr('stroke-dasharray', '3 3');
    if (ySmall !== null) {
      txt(svg, labelW, ySmall, 'Fewer than ' + minClaims + ' testable claims: too few for a rate',
        { size: 11, fill: '#777', cls: 'rp-subhead' });
    }
    var tip = SP.tip(), room = Math.floor((labelW - 14) / 6.9);
    rows.forEach(function (r) {
      var o = r.o;
      var g = svg.append('g').attr('class', 'rp-cond' + (o.all ? ' rp-cond-all' : '')).attr('data-key', o.name)
        .attr('data-testable', o.testable).attr('data-replicated', o.replicated)
        .attr('transform', 'translate(0,' + r.y + ')');
      g.append('rect').attr('class', 'rp-hit').attr('x', 0).attr('y', 0).attr('width', W).attr('height', rowH)
        .attr('fill', '#ffffff').attr('fill-opacity', 0);
      txt(g, labelW - 10, rowH / 2 + 4, cut(o.name, room), { anchor: 'end', weight: o.all ? 700 : 400, fill: r.faint ? '#666' : '#222' });
      var x0 = labelW, bh = o.all ? rowH - 7 : rowH - 10;
      for (var s = 0; s < 5; s++) {
        if (!o.counts[s]) continue;
        var w = (x(1) - x(0)) * o.counts[s] / o.testable;
        g.append('rect').attr('class', 'rp-seg rp-seg-' + s).attr('data-st', s).attr('data-n', o.counts[s])
          .attr('x', x0).attr('y', (rowH - bh) / 2).attr('width', Math.max(0, w)).attr('height', bh)
          .attr('fill', COL[s]).attr('fill-opacity', r.faint ? 0.6 : 1).attr('stroke', '#ffffff').attr('stroke-width', 0.6);
        x0 += w;
      }
      var label = g.append('text').attr('x', W - rightW + 10).attr('y', rowH / 2 + 4).attr('font-size', 12)
        .attr('fill', r.faint ? '#666' : '#222');
      if (r.faint) label.text(SP.fmtInt(o.replicated) + ' of ' + SP.fmtInt(o.testable));
      else {
        label.append('tspan').attr('font-weight', 700).text(pct(o.replicated / o.testable));
        label.append('tspan').attr('fill', '#666').attr('dx', narrow ? 4 : 6).attr('font-size', narrow ? 10.5 : 12)
          .text(SP.fmtInt(o.replicated) + (narrow ? '/' : ' of ') + SP.fmtInt(o.testable));
      }
      g.on('mousemove', function (ev) {
        tip.show('<b>' + esc(o.name) + '</b><br>' + rateLine(o) + breakdown(o) +
          (o.all ? '' : '<br><span class="sp-tip-sub">click to see these claims</span>'), ev);
      }).on('mouseleave', function () { tip.hide(); });
      if (!o.all) clickable(g, function () { tip.hide(); nav(hashOf('condition', o.name)); }, o.name + ': ' +
        o.replicated + ' of ' + o.testable + ' testable claims replicated. Open its claims.');
    });
    txt(svg, xr + 5, yMainEnd + 11, 'all conditions together', { size: 10, fill: '#555' });
    var bandKey = $('rp-key-band');
    if (bandKey) bandKey.hidden = true;          // the overview has no band
    el.plot.dataset.view = 'all';
    el.plot.dataset.ready = String(rows.length);
    el.caption.textContent = 'Each bar is one condition and shows what share of its testable claims got each result; ' +
      'the dashed line is all conditions together. Click a condition to see its claims.';
    drawUntested();
  }
  function drawUntested() {
    var none = conds.filter(function (c) { return c.testable === 0; });
    none.sort(function (a, b) { return b.n - a.n || a.name.localeCompare(b.name); });
    var nClaims = d3.sum(none, function (c) { return c.n; });
    el.under.innerHTML = '<details class="rp-untested" id="rp-untested"><summary>' + SP.fmtInt(none.length) +
      ' more conditions have registered claims (' + SP.fmtInt(nClaims) + ') but none that could be tested</summary>' +
      '<p class="sp-note">For these the catalog has fewer than three independent projects with enough cases and healthy controls. ' +
      'The number is the count of registered claims.</p><p class="rp-chips">' +
      none.map(function (c) {
        return '<a class="rp-chip" href="#' + esc(hashOf('condition', c.name)) + '" data-key="' + esc(c.name) + '">' +
          esc(c.name) + ' <span>' + c.n + '</span></a>';
      }).join(' ') + '</p></details>';
  }

  // ------------------------------------------------------------------ view 2: the claims of a condition or a taxon
  function passes(t, f) {
    return f === 'all' ? true : f === 'rep' ? t.st < 2 : f === 'not' ? (t.st >= 2 && t.st < 5) : t.st >= 5;
  }
  function source() { return state.view === 'condition' ? state.cond : state.taxon; }
  function rowName(t) { return state.view === 'condition' ? taxa[t.t].name : conds[t.c].name; }
  function listRows() {
    var src = source();
    if (!src) return [];
    var rows = src.tests.filter(function (t) { return passes(t, state.filter); });
    rows.sort(function (a, b) {
      var ta = a.st < 5, tb = b.st < 5;
      if (ta !== tb) return ta ? -1 : 1;
      if (ta && b.agree !== a.agree) return b.agree - a.agree;
      return rowName(a).localeCompare(rowName(b)) || (a.up === b.up ? 0 : (a.up ? -1 : 1));
    });
    return rows;
  }
  function drawHead() {
    var src = source(), h = '';
    var untested = src.counts[5] + src.counts[6];
    if (state.view === 'condition') {
      h += '<h2 class="rp-h2">' + esc(src.name) + '</h2><p class="rp-sum">';
      if (src.testable) {
        h += rateLine(src) + '. Tested in ' + plural(src.projects, 'independent project') + ' with ' +
          SP.fmtInt(src.cases) + ' cases and ' + SP.fmtInt(src.controls) + ' healthy controls' +
          (untested ? '; ' + plural(untested, 'more claim') + ' could not be tested' : '') + '.' + rowsNote(src);
      } else {
        h += 'None of the ' + plural(src.n, 'registered claim') + ' could be tested: the catalog has fewer than three ' +
          'independent projects with at least ' + M.min_persons + ' cases and ' + M.min_persons + ' healthy controls for this condition.';
      }
      h += '</p>';
    } else {
      var nc = Object.keys(src.conds).length, url = explorerUrl(src);
      h += '<h2 class="rp-h2"><i>' + esc(src.name) + '</i> <span class="rp-rank">' + src.rank + '</span></h2><p class="rp-sum">';
      h += plural(src.n, 'registered claim') + ' in ' + plural(nc, 'condition') + '. ';
      h += src.testable ? rateLine(src) + '.' + rowsNote(src) : 'None could be tested.';
      h += '</p><p class="rp-gtdb">Counted in the profiles as GTDB ' + gtdbLinks(src, 8) +
        (src.targets.length ? ' (GTDB groups that carry this name), added together' : '') + '. ' +
        (url ? 'The linked names open in the taxon explorer.'
             : 'No profile of this release carries it, so it has no entry in the taxon explorer.') + '</p>';
    }
    el.head.innerHTML = h;
  }
  function rowsNote(src) {             // the header counts claims, the chart draws rows: say how one becomes the other
    if (src.tests.length === src.n) return '';
    return ' The ' + SP.fmtInt(src.n) + ' claims are drawn as ' + plural(src.tests.length, 'row') +
      ': claims that were tested on exactly the same projects share a row, whether they come from one paper or several.';
  }
  function drawFilter() {              // counts are claims, the unit of the header above
    var src = source(), n = { all: src.n, rep: src.replicated, not: src.testable - src.replicated, un: src.n - src.testable };
    el.filterWrap.hidden = !(n.rep + n.not) || src.tests.length < 8;
    el.filter.innerHTML = '<div></div>';
    SP.segmented(el.filter.firstChild, { label: 'Show', value: state.filter, options: [
      { value: 'all', label: 'All ' + n.all }, { value: 'rep', label: 'Replicated ' + n.rep },
      { value: 'not', label: 'Not replicated ' + n.not }, { value: 'un', label: 'Not tested ' + n.un }],
      onChange: function (v) { state.filter = v; state.showAll = false; renderList(); } });
  }
  function tipOf(t) {
    var h = '<b><i>' + esc(taxa[t.t].name) + '</i> ' + (t.up ? 'higher' : 'lower') + ' in ' + esc(conds[t.c].name) + '</b>';
    h += '<br><span class="rp-dot" style="background:' + COL[t.st] + ';border:1px solid ' + INK[t.st] + '"></span>' + esc(LABEL[t.st]);
    if (t.st < 5) {
      h += '<br>on this axis ' + f3(t.agree) + ' (95 % CI ' + f3(t.alo) + '–' + f3(t.ahi) + ') over ' + plural(t.k, 'project');
      h += '<br><span class="sp-tip-sub">' + (t.up ? 'this is the pooled AUC: the chance that a case has more of the taxon than a control'
        : 'the pooled AUC is ' + f3(t.auc) + '; the claim is “lower”, so the axis shows 1 minus it') + '</span>';
    }
    var np = papersOf(t).length;
    if (t.st < 5 && t.x.length) h += '<br><span class="sp-tip-sub">the paper’s own cohort is left out (' + plural(t.x.length, 'project') + ')</span>';
    h += '<br><span class="sp-tip-sub">claimed by ' + plural(np, 'paper') + ' · click for the projects and the paper</span>';
    return h;
  }
  function drawList() {
    var W = plotWidth(), narrow = W < 640, src = source();
    var rows = listRows(), total = rows.length, shown = state.showAll ? rows : rows.slice(0, CAP);
    var labelW = narrow ? Math.round(W * 0.42) : Math.min(300, Math.round(W * 0.36)), rightW = narrow ? 8 : 132;
    var rowH = 22, top = 46, bottom = 40, dirW = 50;
    var anyTest = src.tests.some(function (t) { return t.st < 5; });
    var H = top + rowH * Math.max(shown.length, 1) + (anyTest ? bottom : 8);
    var svg = svgIn(el.plot, W, H, 'Claims of ' + src.name + ', ordered by how well the data agree with each', 'rp-list');
    var x = null;
    if (anyTest) {
      var lo = d3.min(src.tests, function (t) { return t.st < 5 ? t.alo : 0.5; });
      var hi = d3.max(src.tests, function (t) { return t.st < 5 ? t.ahi : 0.5; });
      lo = Math.max(0, Math.min(lo, 0.4) - 0.02); hi = Math.min(1, Math.max(hi, 0.6) + 0.02);
      x = d3.scaleLinear().domain([lo, hi]).range([labelW, W - rightW - 8]);
      var nTested = shown.filter(function (t) { return t.st < 5; }).length;
      var y0 = top - 6, y1 = top + rowH * nTested + 4, s = M.sesoi_auc;
      svg.append('rect').attr('class', 'rp-band').attr('x', x(0.5 - s)).attr('width', x(0.5 + s) - x(0.5 - s))
        .attr('y', y0).attr('height', y1 - y0).attr('fill', '#F3EFE2');
      svg.append('line').attr('class', 'rp-zero').attr('x1', x(0.5)).attr('x2', x(0.5)).attr('y1', y0).attr('y2', y1)
        .attr('stroke', '#9a9a9a').attr('stroke-width', 1);
      axisStyle(svg.append('g').attr('class', 'rp-axis').attr('transform', 'translate(0,' + (top - 10) + ')')
        .call(d3.axisTop(x).ticks(narrow ? 4 : 7).tickFormat(d3.format('.2f')).tickSizeOuter(0)));
      txt(svg, x(0.5), 11, 'no difference', { size: 10, fill: '#777', anchor: 'middle' });
      tri(svg, labelW + 4, H - 26, 'left', '#555');
      txt(svg, labelW + 13, H - 22, narrow ? 'against' : 'the data point the other way', { size: 11, fill: '#555' });
      tri(svg, W - rightW - 12, H - 26, 'right', '#555');
      txt(svg, W - rightW - 21, H - 22, narrow ? 'as claimed' : 'the data agree with the claim', { size: 11, fill: '#555', anchor: 'end' });
      if (!narrow) txt(svg, (labelW + W - rightW - 8) / 2, H - 6, 'chance that a case and a healthy control differ the way the paper said (0.5 = no difference)',
        { size: 10.5, fill: '#777', anchor: 'middle' });
      if (!narrow) txt(svg, W - rightW + 4, top - 14, 'projects · papers', { size: 10.5, fill: '#777' });
    }
    txt(svg, labelW - dirW - 4, top - 14, 'claimed', { size: 10.5, fill: '#777' });
    var tip = SP.tip(), room = Math.floor((labelW - dirW - 18) / 6.9), anyOwn = false;
    shown.forEach(function (t, i) {
      var name = rowName(t), np = papersOf(t).length;
      var g = svg.append('g').attr('class', 'rp-row' + (state.sel === t ? ' rp-row-on' : '')).attr('data-key', t.first.id)
        .attr('data-st', t.st).attr('data-name', name).attr('transform', 'translate(0,' + (top + i * rowH) + ')');
      g.append('rect').attr('class', 'rp-hit').attr('x', 0).attr('y', 0).attr('width', W).attr('height', rowH)
        .attr('fill', state.sel === t ? '#CFB87C' : '#ffffff').attr('fill-opacity', state.sel === t ? 0.32 : 0);
      // one asterisk per project left out as the claim's own cohort; its own text element, because a tspan inside
      // end-anchored text is placed differently by different renderers
      var mark = t.st < 5 ? '***'.slice(0, Math.min(3, t.x.length)) : '';
      txt(g, labelW - dirW - 12 - 7 * mark.length, rowH / 2 + 4, cut(name, room - mark.length), { anchor: 'end', italic: state.view === 'condition' });
      if (mark) { txt(g, labelW - dirW - 7, rowH / 2 + 7, mark, { size: 13, fill: '#8A4A1A', weight: 700, anchor: 'end', cls: 'rp-own' }); anyOwn = true; }
      tri(g, labelW - dirW + 1, rowH / 2, t.up ? 'up' : 'down', '#777');
      txt(g, labelW - dirW + 9, rowH / 2 + 4, t.up ? 'higher' : 'lower', { size: 10.5, fill: '#666', cls: 'rp-dir' });
      if (t.st < 5) {
        g.append('line').attr('class', 'rp-ci').attr('x1', x(t.alo)).attr('x2', x(t.ahi)).attr('y1', rowH / 2).attr('y2', rowH / 2)
          .attr('stroke', INK[t.st]).attr('stroke-width', 1.6);
        g.append('circle').attr('class', 'rp-est').attr('cx', x(t.agree)).attr('cy', rowH / 2).attr('r', 4.6)
          .attr('fill', COL[t.st]).attr('stroke', INK[t.st]).attr('stroke-width', 1.2)
          .attr('data-agree', t.agree.toFixed(6));
        if (!narrow) txt(g, W - rightW + 4, rowH / 2 + 4, t.k + ' · ' + np, { size: 11, fill: '#555' });
      } else {
        if (anyTest || t.st === 6) txt(g, labelW + 4, rowH / 2 + 4, t.st === 5 ? 'not tested: fewer than three projects for the condition'
          : 'not tested: too few people carry this taxon', { size: 10.5, fill: '#888', italic: true, cls: 'rp-why' });
      }
      g.on('mousemove', function (ev) { tip.show(tipOf(t), ev); }).on('mouseleave', function () { tip.hide(); });
      clickable(g, function () { tip.hide(); nav(hashOf('claim', t.first.id)); },
        name + ', claimed ' + (t.up ? 'higher' : 'lower') + ': ' + LABEL[t.st] + '. Open the claim.');
    });
    if (!shown.length) txt(svg, labelW, top + 14, 'No claim with this result.', { size: 11.5, fill: '#777' });
    el.plot.dataset.view = state.view;
    el.plot.dataset.ready = String(shown.length);
    el.more.hidden = !(total > CAP);
    el.moreBtn.textContent = state.showAll ? 'Show the first ' + CAP + ' rows only' : 'Show all ' + SP.fmtInt(total) + ' rows';
    var nShownClaims = d3.sum(rows, function (t) { return t.claims.length; });
    el.caption.textContent = (nShownClaims === total ? 'One row per claim. '
      : 'Each row is one claim, or several claims that were tested on exactly the same projects: ' + SP.fmtInt(nShownClaims) + ' claims in ' +
        SP.fmtInt(total) + ' rows here. ') +
      'The dot is the pooled estimate and the line its 95 % confidence interval. Rows are ordered from the claim the data agree ' +
      'with most to the one they contradict most, on a scale where 0.5 means no difference and values to the right agree with the claim ' +
      '(for a claim of “lower in cases” that is 1 minus the AUC). In the shaded band the difference is smaller than the smallest effect of interest. ' +
      (anyOwn ? 'Asterisks mark a row whose test left out the projects the claim itself came from, one asterisk per project, which is why a taxon can appear more than once with different numbers. ' : '') +
      'Click a row for the projects behind it and the paper.';
    var key = $('rp-key-band');
    if (key) key.hidden = !anyTest;
    el.under.innerHTML = '';
  }
  function renderList() { drawFilter(); drawList(); }

  // ------------------------------------------------------------------ the claim panel
  function forestRows(t) {
    var st = D.groups.start, e = D.effects, cp = D.cp, used = [], own = [], rare = 0;
    for (var j = st[t.g]; j < st[t.g + 1]; j++) {
      var k = e.cp[j], r = { acc: D.projects.acc[cp.proj[k]], title: D.projects.title[cp.proj[k]], nc: cp.nc[k], nk: cp.nk[k],
                             auc: e.auc[j] / 1e6, se: e.se[j] / 1e6, nz: e.nz[j] };
      if (t.x.indexOf(j - st[t.g]) >= 0) { r.own = true; own.push(r); }
      else if (r.nz < M.min_detected) rare++;
      else used.push(r);
    }
    used.sort(function (a, b) { return (b.nc + b.nk) - (a.nc + a.nk) || a.acc.localeCompare(b.acc); });
    return { used: used, own: own, rare: rare };
  }
  function drawForest(node, t, W) {
    var fr = forestRows(t), rows = fr.used.concat(fr.own);
    var labelW = 102, rightW = 72, rowH = 20, top = 40, H = top + rowH * (rows.length + 1) + 44;
    var svg = svgIn(node, W, H, 'Estimate of every project for this claim', 'rp-forest');
    var lo = d3.min(rows, function (r) { return r.auc - 1.96 * r.se; }), hi = d3.max(rows, function (r) { return r.auc + 1.96 * r.se; });
    lo = Math.max(0, Math.min(lo, t.lo, 0.4) - 0.02); hi = Math.min(1, Math.max(hi, t.hi, 0.6) + 0.02);
    var x = d3.scaleLinear().domain([lo, hi]).range([labelW, W - rightW]).clamp(true);
    var y0 = top - 6, y1 = top + rowH * (rows.length + 1) + 2, s = M.sesoi_auc;
    svg.append('rect').attr('class', 'rp-band').attr('x', x(0.5 - s)).attr('width', x(0.5 + s) - x(0.5 - s))
      .attr('y', y0).attr('height', y1 - y0).attr('fill', '#F3EFE2');
    svg.append('line').attr('x1', x(0.5)).attr('x2', x(0.5)).attr('y1', y0).attr('y2', y1).attr('stroke', '#9a9a9a');
    var tight = W < 400;
    tri(svg, labelW + 4, 8, 'left', t.up ? '#777' : '#222');
    txt(svg, labelW + 13, 12, (tight ? 'less' : 'less in cases') + (t.up ? '' : ' (claimed)'), { size: 10.5, fill: t.up ? '#777' : '#222', weight: t.up ? 400 : 700 });
    tri(svg, W - rightW - 4, 8, 'right', t.up ? '#222' : '#777');
    txt(svg, W - rightW - 13, 12, (t.up ? '(claimed) ' : '') + (tight ? 'more' : 'more in cases'), { size: 10.5, anchor: 'end', fill: t.up ? '#222' : '#777', weight: t.up ? 700 : 400 });
    txt(svg, W - 2, 26, 'cases / controls', { size: 9.5, fill: '#777', anchor: 'end' });
    txt(svg, 0, 26, 'project', { size: 9.5, fill: '#777' });
    var wmax = d3.max(rows, function (r) { return 1 / (r.se * r.se); }) || 1, tip = SP.tip();
    rows.forEach(function (r, i) {
      var g = svg.append('g').attr('class', 'rp-proj' + (r.own ? ' rp-proj-own' : '')).attr('data-acc', r.acc)
        .attr('data-auc', r.auc.toFixed(6)).attr('transform', 'translate(0,' + (top + i * rowH) + ')');
      g.append('rect').attr('x', 0).attr('y', 0).attr('width', W).attr('height', rowH).attr('fill', '#fff').attr('fill-opacity', 0);
      txt(g, 0, rowH / 2 + 4, r.acc + (r.own ? ' *' : ''), { size: 10.5, fill: r.own ? '#888' : '#1f5aa6', cls: 'rp-acc' });
      var a = Math.max(0, r.auc - 1.96 * r.se), b = Math.min(1, r.auc + 1.96 * r.se);
      g.append('line').attr('x1', x(a)).attr('x2', x(b)).attr('y1', rowH / 2).attr('y2', rowH / 2)
        .attr('stroke', r.own ? '#b9b9b9' : '#555').attr('stroke-width', 1.2);
      var side = 4 + 6 * Math.sqrt(1 / (r.se * r.se) / wmax);
      g.append('rect').attr('class', 'rp-sq').attr('x', x(r.auc) - side / 2).attr('y', rowH / 2 - side / 2).attr('width', side)
        .attr('height', side).attr('fill', r.own ? '#ffffff' : '#444').attr('stroke', r.own ? '#9a9a9a' : '#444');
      txt(g, W - 2, rowH / 2 + 4, SP.fmtInt(r.nc) + ' / ' + SP.fmtInt(r.nk), { size: 10.5, fill: r.own ? '#999' : '#555', anchor: 'end' });
      g.on('mousemove', function (ev) {
        tip.show('<b>' + esc(r.acc) + '</b>' + (r.title ? '<br>' + esc(r.title) : '') + '<br>AUC ' + f3(r.auc) + ' (95 % CI ' +
          f3(a) + '–' + f3(b) + ')<br>' + SP.fmtInt(r.nc) + ' cases, ' + SP.fmtInt(r.nk) + ' healthy controls; taxon found in ' +
          SP.fmtInt(r.nz) + (r.own ? '<br><b>the claim’s own cohort: not counted</b>' : '') +
          '<br><span class="sp-tip-sub">click to open the project</span>', ev);
      }).on('mouseleave', function () { tip.hide(); });
      clickable(g, function () { tip.hide(); SP.go(projectUrl(r.acc)); }, 'Project ' + r.acc + ', AUC ' + f3(r.auc));
    });
    var yp = top + rows.length * rowH, g = svg.append('g').attr('class', 'rp-pooled').attr('transform', 'translate(0,' + yp + ')');
    svg.append('line').attr('x1', 0).attr('x2', W).attr('y1', yp + 1).attr('y2', yp + 1).attr('stroke', '#e2e2e2');
    txt(g, 0, rowH / 2 + 6, 'Pooled', { size: 11, weight: 700 });
    var cy = rowH / 2 + 2;
    g.append('polygon').attr('class', 'rp-diamond').attr('data-auc', t.auc.toFixed(6))
      .attr('points', [x(t.lo), cy, x(t.auc), cy - 6, x(t.hi), cy, x(t.auc), cy + 6].join(' '))
      .attr('fill', COL[t.st]).attr('stroke', INK[t.st]).attr('stroke-width', 1.2);
    txt(g, W - 2, rowH / 2 + 6, f3(t.auc), { size: 11, weight: 700, anchor: 'end' });
    axisStyle(svg.append('g').attr('class', 'rp-axis').attr('transform', 'translate(0,' + (y1 + 6) + ')')
      .call(d3.axisBottom(x).ticks(4).tickFormat(d3.format('.2f')).tickSizeOuter(0)));
    txt(svg, (labelW + W - rightW) / 2, H - 4, tight ? 'AUC (0.5 = no difference)' : 'AUC: chance that a case has more of the taxon than a control',
      { size: 10, fill: '#777', anchor: 'middle' });
    return fr;
  }
  function resultSentence(t) {
    if (t.st === 5) {
      return 'Not tested: the catalog has fewer than three independent projects with at least ' + M.min_persons + ' cases and ' +
        M.min_persons + ' healthy controls for this condition, once the claim’s own cohort is left out.';
    }
    if (t.st === 6) {
      return 'Not tested: in too many of this condition’s projects fewer than ' + M.min_detected + ' people carry the taxon.';
    }
    var h = 'Pooled over <b>' + plural(t.k, 'independent project') + '</b>, the chance that a case has more of this taxon than a ' +
      'healthy control is <b>' + f3(t.auc) + '</b> (95 % CI ' + f3(t.lo) + '–' + f3(t.hi) + '; 0.5 means no difference). ';
    var n = SP.fmtInt(M.testable);
    if (t.st === 0) h += 'That is the claimed direction, and it holds after correcting for ' + n + ' tests (' + fq(t.qr) + ').';
    else if (t.st === 1) h += 'That is the claimed direction and it holds after correcting for ' + n + ' tests (' + fq(t.qr) +
      '), but the difference is small: even at the one-sided upper 95 % limit used for this label (a tighter bound than the interval above) it is ' +
      f3(t.tu) + ' away from 0.5, less than the smallest effect of interest (' + f3(M.sesoi_auc) + ').';
    else if (t.st === 2) h += 'The data cannot tell a small difference in the claimed direction from none (' + fq(t.qr) + ').';
    else if (t.st === 3) h += 'A difference of meaningful size in the claimed direction is ruled out: even at the one-sided upper 95 % limit used for this label ' +
      '(a tighter bound than the interval above) the difference is ' + (t.tu <= 0 ? 'not on the claimed side of 0.5 at all.'
        : f3(t.tu) + ' away from 0.5 on the claimed side, less than the smallest effect of interest (' + f3(M.sesoi_auc) + ').');
    else h += 'That is the opposite of the claim, and it holds after correcting for ' + n + ' tests (' + fq(t.qo) + ').';
    if (t.st < 2) h += ' Under the stricter Hartung-Knapp test it ' + (t.hk ? '<b>still replicates</b>.' : '<b>does not replicate</b>.');
    return h;
  }
  function drawPanel(t) {
    var tx = taxa[t.t], cond = conds[t.c], papers = papersOf(t), url = explorerUrl(tx);
    var h = '<div class="spx-panel-head"><button type="button" class="spx-close" id="rp-close" aria-label="Close this claim" title="Close">×</button>' +
      '<p class="rp-kicker">Claim</p><h2><i>' + esc(tx.name) + '</i> is ' + (t.up ? 'more' : 'less') + ' abundant in ' + esc(cond.name) +
      ' than in healthy controls</h2></div>';
    h += '<p class="rp-result"><span class="rp-badge rp-st-' + t.st + '" style="background:' + COL[t.st] + ';border-color:' + INK[t.st] +
      ';color:' + (t.st === 0 || t.st === 3 || t.st === 4 ? '#fff' : '#1a1a1a') + '">' + esc(LABEL[t.st]) + '</span></p>';
    h += '<p class="spx-sum" id="rp-sentence">' + resultSentence(t) + '</p>';
    h += '<p class="sp-note rp-caveat" id="rp-caveat">' + (t.st < 2
      ? 'This says the taxon is shifted the same way in independent public data. It does not say how large the shift is or that it is specific to this condition, and it says nothing about cause, diagnosis or treatment.'
      : t.st === 2 ? 'This is about the direction of a difference in public data. It says nothing about cause, diagnosis or treatment, and a claim that did not replicate here is not thereby shown to be false.'
      : t.st === 3 ? 'Here the data are informative: in these public data an effect of meaningful size in the claimed direction is excluded. That says nothing about cause, diagnosis or treatment.'
      : t.st === 4 ? 'Here the data are informative: in these public data the difference runs the other way. That says nothing about cause, diagnosis or treatment.'
      : 'A claim that could not be tested is neither confirmed nor contradicted.') + '</p>';
    if (t.st < 5) h += '<div id="rp-forest"></div><p class="sp-note" id="rp-forest-note"></p>';
    h += '<h3 class="spx-h3">' + (papers.length > 1 ? 'The papers' : 'The paper') + '</h3><ul class="rp-papers">';
    papers.slice().sort(function (a, b) { return String(D.papers.year[a.p]).localeCompare(String(D.papers.year[b.p])); })
      .forEach(function (g) {
        var p = g.p, pmid = D.papers.pmid[p], seqs = [], sigs = [];
        g.claims.forEach(function (c) {
          var q = D.seq[c.q] === 'WMS' ? 'shotgun' : D.seq[c.q];
          if (seqs.indexOf(q) < 0) seqs.push(q);
          if (sigs.indexOf(sigOf(c)) < 0) sigs.push(sigOf(c));
        });
        h += '<li>' + esc(D.papers.author[p] || 'Authors not recorded') + (D.papers.year[p] ? ' (' + esc(D.papers.year[p]) + ')' : '') + '. ' +
          esc(D.papers.title[p]) + ' <i>' + esc(D.papers.journal[p]) + '</i>. <a class="rp-paper" href="' + esc(paperUrl(pmid)) +
          '" rel="noopener">PubMed ' + esc(pmid) + '</a> <span class="rp-meta">' + esc(seqs.join(', ')) +
          ' · BugSigDB ' + plural(sigs.length, 'signature') + ': <span class="rp-sig">' + esc(sigs.join(', ')) + '</span></span></li>';
      });
    h += '</ul>';
    h += '<h3 class="spx-h3">The taxon</h3><p class="rp-gtdb">The paper’s <i>' + esc(tx.name) + '</i> (' + tx.rank + ') is counted in the profiles as GTDB ' +
      gtdbLinks(tx, 6) + (tx.targets.length ? ' (GTDB groups that carry this name), added together' : '') + '.' +
      (url ? ' The linked names open in the taxon explorer.' : ' No profile of this release carries it, so it has no entry in the taxon explorer.') +
      '</p><p class="rp-links">' +
      '<a href="#' + esc(hashOf('taxon', tx.name)) + '" class="rp-goto" data-hash="' + esc(hashOf('taxon', tx.name)) + '">All claims about this taxon</a> · ' +
      '<a href="#' + esc(hashOf('condition', cond.name)) + '" class="rp-goto" data-hash="' + esc(hashOf('condition', cond.name)) + '">All claims about ' +
      esc(cond.name) + '</a></p>';
    if (t.st < 5) h += '<details class="rp-projects" id="rp-projects"><summary>The projects</summary><ul id="rp-projlist"></ul></details>';
    h += '<div class="spx-share"><input class="spx-link" id="rp-link" type="text" readonly aria-label="Link to this claim">' +
      '<button type="button" class="sp-seg-btn spx-copy" id="rp-copy">Copy link</button><span class="spx-copied" id="rp-copied" hidden>copied</span></div>';
    el.panel.innerHTML = h;
    el.panel.dataset.key = t.first.id;
    if (t.st < 5) {
      var W = P.panelWidth || ($('rp-forest').clientWidth) || 420;
      var fr = drawForest($('rp-forest'), t, W), notes = [];
      notes.push('Squares are larger for more precise projects; lines are 95 % confidence intervals; the diamond is the pooled estimate; in the shaded band the difference is smaller than the smallest effect of interest.');
      if (fr.own.length) notes.push('* ' + plural(fr.own.length, 'project is', 'projects are') + ' the claim’s own cohort and ' +
        (fr.own.length === 1 ? 'is' : 'are') + ' not counted.');
      if (fr.rare) notes.push(plural(fr.rare, 'further project') + ' with enough cases and controls ' + (fr.rare === 1 ? 'has' : 'have') +
        ' this taxon in fewer than ' + M.min_detected + ' people and ' + (fr.rare === 1 ? 'is' : 'are') + ' not counted.');
      if (t.k > 2) notes.push('Disagreement between projects (I²): ' + t.i2 + ' %.');
      $('rp-forest-note').textContent = notes.join(' ');
      $('rp-projlist').innerHTML = fr.used.concat(fr.own).map(function (r) {
        return '<li><a href="' + esc(projectUrl(r.acc)) + '">' + esc(r.acc) + '</a> ' + esc(r.title) + ' <span class="rp-meta">' +
          SP.fmtInt(r.nc) + ' cases, ' + SP.fmtInt(r.nk) + ' controls' + (r.own ? ' · the claim’s own cohort, not counted' : '') + '</span></li>';
      }).join('');
    }
    $('rp-link').value = String(window.location.href).split('#')[0] + '#' + hashOf('claim', state.selId || t.first.id);
    $('rp-close').addEventListener('click', function () {
      nav(state.view === 'taxon' ? hashOf('taxon', state.taxon.name) : hashOf('condition', state.cond.name));
    });
    $('rp-copy').addEventListener('click', function () {
      var inp = $('rp-link'), done = function () { $('rp-copied').hidden = false; };
      if (window.navigator && window.navigator.clipboard && window.navigator.clipboard.writeText) {
        window.navigator.clipboard.writeText(inp.value).then(done, function () { inp.select(); });
      } else { inp.select(); try { document.execCommand('copy'); done(); } catch (e) { /* the text stays selected */ } }
    });
    Array.prototype.forEach.call(el.panel.querySelectorAll('a.rp-goto'), function (a) {
      a.addEventListener('click', function (ev) { ev.preventDefault(); nav(a.dataset.hash); });
    });
  }

  // ------------------------------------------------------------------ page furniture
  function drawCrumbs() {
    var h = '<button type="button" class="sp-crumb" data-hash="">All conditions</button>';
    if (state.view === 'condition') h += '<span class="sp-crumb-sep">›</span><button type="button" class="sp-crumb" data-hash="' +
      esc(hashOf('condition', state.cond.name)) + '">' + esc(state.cond.name) + '</button>';
    if (state.view === 'taxon') h += '<span class="sp-crumb-sep">›</span><button type="button" class="sp-crumb" data-hash="' +
      esc(hashOf('taxon', state.taxon.name)) + '"><i>' + esc(state.taxon.name) + '</i></button>';
    el.crumbs.innerHTML = h;
    Array.prototype.forEach.call(el.crumbs.querySelectorAll('.sp-crumb'), function (b) {
      b.addEventListener('click', function () { nav(b.dataset.hash); });
    });
  }
  function render() {
    el.status.hidden = true;
    drawCrumbs();
    var open = !!state.sel;
    el.panel.hidden = !open;                     // set the layout first: the chart is drawn at the width it leaves
    el.split.classList.toggle('spx-open', open);
    el.sortWrap.hidden = state.view !== 'all';
    if (state.view === 'all') {
      el.head.innerHTML = ''; el.filterWrap.hidden = true; el.more.hidden = true;
      drawOverview();
    } else { drawHead(); renderList(); }
    if (open) drawPanel(state.sel); else { el.panel.innerHTML = ''; delete el.panel.dataset.key; }
  }
  function drawFirm() {
    if (!el.firm) return;
    var c = M.checks, rows = [
      { key: 'headline', label: 'Headline: all testable claims', value: M.rate, k: M.replicated, n: M.testable },
      { key: 'conditions', label: 'Mean over ' + c.across_conditions.n + ' conditions', value: c.across_conditions.rate },
      { key: 'without', label: 'Without ' + c.largest.name, value: c.without_largest.replicated / c.without_largest.testable,
        k: c.without_largest.replicated, n: c.without_largest.testable },
      { key: 'person', label: 'Projects with person identifiers', value: c.person_id.replicated / c.person_id.testable,
        k: c.person_id.replicated, n: c.person_id.testable },
      { key: 'stricter', label: 'Stricter test (Hartung-Knapp)', value: c.stricter.replicated / c.stricter.testable,
        k: c.stricter.replicated, n: c.stricter.testable },
      { key: 'shuffled', label: 'Shuffled labels, worst of ' + SP.fmtInt(c.shuffled.n), value: c.shuffled.maximum / M.testable,
        k: c.shuffled.maximum, n: M.testable }];
    var fw = P.firmWidth || el.firm.clientWidth || 720;
    if (fw < 520) {                    // the shared bar chart does not shorten labels to the room it has
      var shortLabel = { headline: 'Headline', conditions: 'Mean of ' + c.across_conditions.n + ' conditions', without: 'Without the largest',
                         person: 'With person identifiers', stricter: 'Stricter test', shuffled: 'Shuffled labels, worst' };
      rows.forEach(function (r) { r.label = shortLabel[r.key]; });
    }
    SP.barChart(el.firm, { rows: rows, width: fw, colour: '#2F5E8C',
      label: 'replication rate', alt: 'The replication rate under stricter readings',
      axisFormat: function (v) { return Math.round(v * 100) + ' %'; },
      valueFormat: function (r) { return pct(r.value); },
      onHover: function (r) { return '<b>' + esc(r.label) + '</b><br>' + pct(r.value) + (r.n ? ' — ' + SP.fmtInt(r.k) + ' of ' + SP.fmtInt(r.n) : ''); } });
  }
  function searchItems() {
    var items = conds.map(function (c) {
      return { name: c.name, url: '#' + hashOf('condition', c.name), hash: hashOf('condition', c.name),
               sub: 'condition · ' + (c.testable ? SP.fmtInt(c.replicated) + ' of ' + SP.fmtInt(c.testable) + ' testable claims replicated'
                                                  : plural(c.n, 'claim') + ', none testable') };
    });
    taxa.forEach(function (t) {
      items.push({ name: t.name, keys: [t.name, t.gtdb.replace(/^[a-z]__/, ''), t.rank], html: '<i>' + esc(t.name) + '</i>',
                   url: '#' + hashOf('taxon', t.name), hash: hashOf('taxon', t.name),
                   sub: t.rank + ' · ' + plural(t.n, 'claim') + ' in ' + plural(Object.keys(t.conds).length, 'condition') +
                        (t.testable ? ', ' + SP.fmtInt(t.replicated) + ' of ' + SP.fmtInt(t.testable) + ' testable replicated' : ', none testable') });
    });
    return items;
  }

  function start() {
    return SP.getJSON(P.dataDir + 'replication.json').then(function (data) {
      build(data);
      SP.segmented(el.sort, { label: 'Order', value: state.sort, options: [
        { value: 'claims', label: 'Most claims first' }, { value: 'rate', label: 'Highest rate first' }],
        onChange: function (v) { state.sort = v; if (state.view === 'all') drawOverview(); } });
      var input = el.search.querySelector('input'), hits = el.search.querySelector('.sp-hits');
      SP.searchBox({ input: input, results: hits, limit: 10, items: searchItems(),
        empty: 'No registered claim is about a taxon or a condition of that name.',
        onPick: function (it) { input.value = ''; hits.innerHTML = ''; nav(it.hash); } });
      el.moreBtn.addEventListener('click', function () { state.showAll = !state.showAll; drawList(); });
      el.under.addEventListener('click', function (ev) {
        var a = ev.target.closest ? ev.target.closest('a.rp-chip') : null;
        if (a) { ev.preventDefault(); nav(hashOf('condition', a.dataset.key)); }
      });
      window.addEventListener('hashchange', route);
      window.addEventListener('popstate', route);
      var timer = null;
      window.addEventListener('resize', function () { clearTimeout(timer); timer = setTimeout(function () { render(); drawFirm(); }, 150); });
      if (el.firmBox) el.firmBox.addEventListener('toggle', drawFirm);
      drawFirm();
      route();
      return data;
    }).catch(function (e) {
      el.status.hidden = false;
      el.status.className = 'spx-status spx-status-error';
      el.status.textContent = 'The results file could not be read (' + (e && e.message ? e.message : e) + '). The tables are under Downloads at the foot of this page.';
      window.SP_REPLICATION.error = e;
      return null;
    });
  }

  window.SP_REPLICATION = {
    start: start, nav: nav, state: state, data: function () { return D; }, tests: function () { return tests; },
    claims: function () { return claims; }, conditions: function () { return conds; }, taxa: function () { return taxa; },
    rows: listRows, wilson: wilson, explorerUrl: explorerUrl, projectUrl: projectUrl, paperUrl: paperUrl
  };
  if (!P.manualStart) window.SP_REPLICATION.ready = start();
})();
