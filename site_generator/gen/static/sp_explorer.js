/* sp_explorer.js — the taxon explorer (Atlas > Taxon explorer), round 3.
 *
 * One page for every genus and species in the catalog's community profiles. It needs, all loaded by the
 * template: window.d3 (7.9), window.topojson (3.1), window.SP (sp_core.js) and window.SPX (sp_taxdata.js),
 * plus window.SP_PAGE {root, dataDir, minSamples, minProjects, hasProjects, vanishHref, studiesHref}.
 * Loads nothing from another host. Nothing in sp_core.js is modified; every new drawing routine is here.
 *
 * What it draws
 *   - a rectangular phylogram of the pruned GTDB tree, branch lengths to scale, Bacteria and Archaea as two
 *     trees with a visible break and a scale bar each (their reference trees share no root, so one scale
 *     across the break would be meaningless);
 *   - level of detail: only clades whose drawn height reaches MIN_PX are expanded, largest first, up to
 *     DRAW_CAP clades per panel; everything else is a triangle whose height is its number of tips. The
 *     number of drawn clades is therefore bounded by DRAW_CAP + the largest degree, whatever the zoom;
 *   - tips sized by the number of samples carrying the taxon and coloured by VANISH / BloSSUM class (an
 *     unknown class string, e.g. a later 'StIL', gets the neutral 'other class' mark);
 *   - (round 4) a second way through the same taxa: the GTDB taxonomy as an outline that opens rank by rank
 *     (domain > phylum > class > order > family > genus > species), chosen with the "Browse as" switch;
 *   - (round 4) the taxon view takes the full width of the page and replaces the tree or the outline while
 *     it is open; "Back" returns to where the reader was. A bar of its charts links to the sample table
 *     filtered to that group (window.SP_PAGE.samplesHref);
 *   - a detail view that opens in place: summary line, four dimensions × two measures as bars, a globe,
 *     the literature summary (fetched on demand), the most-abundant projects (projects.bin, fetched when
 *     that section is opened) and, for a genus, its species.
 *
 * The address records the open taxon as #taxon=<GTDB name with prefix, URI-encoded>, e.g.
 * #taxon=s__Bacteroides%20uniformis. The VANISH/BloSSUM page links here with the same form. With no taxon
 * open, #view=list records the outline and #view=list&at=f__Lachnospiraceae the clade last opened in it.
 *
 * Note on the reader: sp_taxdata.js exposes the tree only through SPX.node(i) / SPX.children(i) /
 * SPX.taxon(id), one object per call; it has no typed-array accessor. This file therefore makes ONE pass
 * over all 64,513 nodes at load (about 20 ms, measured in Node) and copies parent, depth, tip count and
 * rank into its own typed arrays; names and per-taxon fields stay in the reader and are asked for only for
 * the few hundred nodes actually drawn, with a small cache.
 */
(function () {
  'use strict';

  var CFG = window.SP_PAGE || {};
  var SP = window.SP, SPX = window.SPX, d3 = window.d3;

  /* ----------------------------------------------------------------- constants */
  var MIN_PX = 3.5;          // a clade shorter than this on screen is folded into a triangle
  var DRAW_CAP = 300;        // clades drawn per panel at most (bound: DRAW_CAP + max degree). The
                             // budget is spent largest-first on what the window shows, so zooming in
                             // buys depth instead of more elements.
  var TIP_LABEL_PX = 9;      // a tip is named when its row is at least this tall
  var CLADE_LABEL_PX = 10;   // a collapsed clade is named when its triangle is at least this tall
  var OPEN_LABEL_PX = 30;    // an expanded named clade is named when it is at least this tall
  var PAD = { l: 10, r: 196, t: 18, b: 30 };
  var PANEL_GAP = 30;
  var REVEAL_TIPS = 90;      // when a taxon is chosen, show a clade of about this many tips around it
  var REVEAL_ROW_PX = 11;    // ... zoomed so that one tip row is at least this tall
  var CLS_CODE = { VANISH: 'V', BloSSUM: 'B', StIL: 'S' };
  /* the VANISH page's own colours (sp_vanish.js: V gold, B blue, S the site's warn brown, N neutral):
     change both files together. */
  var CLASS_COLOUR = { V: '#CFB87C', B: '#2F5E8C', S: '#8A4A1A', O: '#4a4a4a', N: '#ffffff' };
  var NEUTRAL = '#9aa3a0';
  /* eight muted, colour-blind-safe hues (Okabe-Ito, darkened), one per largest phylum; they sit beside the
     site's gold (#CFB87C) and blue (#2F5E8C) without repeating them */
  var PHYLUM_PALETTE = ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#117777', '#B07A00', '#7A6FAF', '#8C6D3F'];
  var OVERVIEW_WEDGES = 34;  // folded clades in the opening view of a domain (plus the Archaea panel's)
  var OVERVIEW_MIN_H = 13;   // px per folded clade there, so a small phylum is still a readable band
  var LIT_LABEL = 'Written by an AI model (Claude Opus 5) from the sources listed; not reviewed by a ' +
    'person. Statements about health conditions are published findings, not results from this catalog.';
  var DIMS = [
    { key: 'site', label: 'Body site' },
    { key: 'age', label: 'Age group' },
    { key: 'country', label: 'Country' },
    { key: 'life', label: 'Lifestyle' }
  ];

  /* ----------------------------------------------------------------- small helpers */
  function esc(s) { return SP.esc(s); }
  function num(n) { return SP.fmtInt(n); }
  function pct(x, dp) { return SP.fmtPct(x, dp === undefined ? 1 : dp); }
  function el(id) { return document.getElementById(id); }
  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

  function italicise(name) {               // 'g__Bacteroides' -> '<i>Bacteroides</i>'
    return '<i>' + esc(SPX.bare(name)) + '</i>';
  }

  /* ----------------------------------------------------------------- layout, built once */
  var L = null;                            // typed arrays for the whole tree
  var nodeCache = {};                      // node index -> {name, label, rank, taxa:[…]}
  var taxCache = {};                       // taxon id -> {nSamples, cls, lit}

  function buildLayout() {
    var n = SPX.nNodes(), i;
    var ranks = SPX.meta().tree.ranks, rankIx = {};
    for (i = 0; i < ranks.length; i++) { rankIx[ranks[i]] = i; }
    var par = new Int32Array(n), dep = new Float64Array(n), nt = new Uint32Array(n), rk = new Uint8Array(n);
    for (i = 0; i < n; i++) {
      var nd = SPX.node(i);
      par[i] = nd.parent; dep[i] = nd.depth; nt[i] = nd.nTips; rk[i] = rankIx[nd.rank] || 0;
    }
    var cnt = new Uint32Array(n);
    for (i = 1; i < n; i++) { cnt[par[i]]++; }
    var ks = new Uint32Array(n + 1), acc = 0;
    for (i = 0; i < n; i++) { ks[i] = acc; acc += cnt[i]; }
    ks[n] = acc;
    var fill = new Uint32Array(n), kids = new Uint32Array(acc);
    for (i = 1; i < n; i++) { var p = par[i]; kids[ks[p] + fill[p]++] = i; }
    /* nodes are in preorder, so a subtree occupies a contiguous run of tip rows */
    var y0 = new Float64Array(n), y1 = new Float64Array(n), c = 0;
    for (i = 0; i < n; i++) {
      y0[i] = c;
      if (ks[i + 1] === ks[i]) { y1[i] = c + 1; c += 1; } else { y1[i] = c + nt[i]; }
    }
    var mx = new Float64Array(n);
    for (i = 0; i < n; i++) { mx[i] = dep[i]; }
    for (i = n - 1; i > 0; i--) { var q = par[i]; if (mx[i] > mx[q]) { mx[q] = mx[i]; } }
    var dom = new Int32Array(n);
    dom[0] = -1;
    for (i = 1; i < n; i++) { dom[i] = par[i] === 0 ? i : dom[par[i]]; }
    /* one bottom-up pass for the wedge names and the phylum colours: w[i] = the largest number of samples
       any taxon below i is carried by; cntR/bestR = how many clades of each rank are inside i and which of
       them has the biggest w. Reading the per-taxon sample counts needs SPX.taxon once per taxon. */
    var rankKeys = ['phylum', 'class', 'order', 'family', 'genus', 'species'];
    var rankCode = rankKeys.map(function (k) { return rankIx[k]; });
    var nT = SPX.nTaxa(), ts = new Float64Array(nT), id, r, j;
    for (id = 0; id < nT; id++) { ts[id] = SPX.taxon(id).nSamples; }
    var w = new Float64Array(n), cntR = [], bestR = [];
    for (r = 0; r < rankKeys.length; r++) {
      cntR.push(new Uint32Array(n));
      var br = new Int32Array(n);
      for (i = 0; i < n; i++) { br[i] = -1; }
      bestR.push(br);
    }
    for (i = n - 1; i >= 0; i--) {
      var own = SPX.taxaAt(i), bw = 0;
      for (j = 0; j < own.length; j++) { if (ts[own[j]] > bw) { bw = ts[own[j]]; } }
      if (bw > w[i]) { w[i] = bw; }
      for (r = 0; r < rankKeys.length; r++) {
        if (rk[i] === rankCode[r]) { cntR[r][i] += 1; bestR[r][i] = i; }
      }
      var pp = par[i];
      if (pp >= 0) {
        if (w[i] > w[pp]) { w[pp] = w[i]; }
        for (r = 0; r < rankKeys.length; r++) {
          cntR[r][pp] += cntR[r][i];
          var bb = bestR[r][i];
          if (bb >= 0 && (bestR[r][pp] < 0 || w[bb] > w[bestR[r][pp]])) { bestR[r][pp] = bb; }
        }
      }
    }
    var phy = new Int32Array(n), phyla = [];
    phy[0] = -1;
    for (i = 1; i < n; i++) {
      phy[i] = rk[i] === rankCode[0] ? i : phy[par[i]];
      if (rk[i] === rankCode[0]) { phyla.push(i); }
    }
    phyla.sort(function (a, b) { return nt[b] - nt[a]; });
    var phyCol = {}, top = [];
    for (i = 0; i < phyla.length && i < PHYLUM_PALETTE.length; i++) {
      phyCol[phyla[i]] = PHYLUM_PALETTE[i];
      top.push(phyla[i]);
    }
    L = { n: n, par: par, dep: dep, nt: nt, rank: rk, rankIx: rankIx, ranks: ranks,
          rankKeys: rankKeys, cntR: cntR, bestR: bestR, w: w, phy: phy, phyCol: phyCol, topPhyla: top,
          ks: ks, kids: kids, y0: y0, y1: y1, maxDep: mx, dom: dom, nTips: c,
          isTip: function (i) { return ks[i + 1] === ks[i]; },
          childrenOf: function (i) { return kids.subarray(ks[i], ks[i + 1]); } };
    return L;
  }

  function nodeInfo(i) {
    var v = nodeCache[i];
    if (!v) {
      var nd = SPX.node(i);
      v = nodeCache[i] = { name: nd.name, label: nd.label, rank: nd.rank, taxa: SPX.taxaAt(i) };
    }
    return v;
  }
  function taxInfo(id) {
    var v = taxCache[id];
    if (!v) {
      var t = SPX.taxon(id);
      v = taxCache[id] = { name: t.name, label: t.label, rank: t.rank, nSamples: t.nSamples,
                           cls: t.cls, lit: t.lit, site: t.siteLabel };
    }
    return v;
  }
  /* the taxon a node stands for: the most specific one sitting on it (a tip may carry a genus and its one
     species; code for more, as FORMAT.md asks) */
  function taxonOfNode(i) {
    var ids = nodeInfo(i).taxa;
    return ids.length ? ids[ids.length - 1] : -1;
  }
  function clsCode(cls) {
    if (!cls) { return 'N'; }
    return CLS_CODE[cls] || 'O';           // an unknown class string gets the neutral 'other' mark
  }
  /* radius from the number of samples carrying the taxon; anchored on the population, not on a scan of the
     whole taxon table, so no second pass over 32,713 taxa is needed at load */
  var SAMP_TOP = 400000;
  function tipRadius(samples) {
    var f = Math.log10(1 + Math.max(0, samples)) / Math.log10(1 + SAMP_TOP);
    return 1.25 + 4.1 * Math.sqrt(clamp(f, 0, 1));
  }

  /* ----------------------------------------------------------------- the tree */
  var TREE = {
    el: null, panels: [], focus: 0, width: 960, height: 700,
    selNode: -1, selTaxon: -1, lastDraw: 0, lastCount: 0
  };

  function panelRoots(focus) {
    if (focus === 0) { return Array.prototype.slice.call(L.childrenOf(0)); }   // both domains
    return [focus];
  }

  function panelHeights(roots, H) {
    if (roots.length === 1) { return [H]; }
    var h1 = Math.max(200, Math.round((H - PANEL_GAP) * 0.27));
    return [H - PANEL_GAP - h1, h1];
  }

  /* --- which clades a view draws ---------------------------------------------------------------
     Two rules, both bounded:
     * the OPENING view of a domain (zoom 1) is deliberately coarse — it expands by number of tips,
       largest first, until it holds OVERVIEW_WEDGES folded clades, and then gives every one of them at
       least OVERVIEW_MIN_H pixels, so that a small phylum is still a readable band with a label. Heights
       in that view are therefore NOT proportional to tip counts; the page says so and every wedge carries
       its tip count;
     * every other view (any zoom, any clade) keeps the true tip-proportional scale and expands by drawn
       height, largest first, until DRAW_CAP clades or nothing reaches MIN_PX. */
  function heapView(rootI, keyOf, stop) {
    var expanded = [], frontier = [], heap = [], hs = [], k;
    function push(i) { var s = keyOf(i), p = heap.length; heap.push(i); hs.push(s);
      while (p > 0) { var q = (p - 1) >> 1; if (hs[q] >= hs[p]) { break; }
        var ti = heap[q]; heap[q] = heap[p]; heap[p] = ti; var ts = hs[q]; hs[q] = hs[p]; hs[p] = ts; p = q; } }
    function pop() { var top0 = heap[0], last = heap.pop(), ls = hs.pop();
      if (heap.length) { heap[0] = last; hs[0] = ls; var p = 0;
        for (;;) { var a = 2 * p + 1, b = a + 1, m = p;
          if (a < hs.length && hs[a] > hs[m]) { m = a; }
          if (b < hs.length && hs[b] > hs[m]) { m = b; }
          if (m === p) { break; }
          var ti = heap[m]; heap[m] = heap[p]; heap[p] = ti; var ts = hs[m]; hs[m] = hs[p]; hs[p] = ts; p = m; } }
      return top0; }
    push(rootI);
    var count = 1;
    while (heap.length) {
      var i0 = heap[0];
      if (L.isTip(i0)) { pop(); frontier.push(i0); continue; }   // a tip is always drawn as a tip
      if (stop(hs[0], count, heap.length + frontier.length)) { break; }
      var kids0 = L.childrenOf(i0), nvis = 0, vis = [];
      for (k = 0; k < kids0.length; k++) { if (keyOf(kids0[k]) !== null) { vis.push(kids0[k]); nvis++; } }
      if (count + nvis > DRAW_CAP) { break; }
      pop();
      expanded.push(i0);
      count += nvis;
      for (k = 0; k < vis.length; k++) { push(vis[k]); }
    }
    for (k = 0; k < heap.length; k++) { frontier.push(heap[k]); }
    return { expanded: expanded, frontier: frontier, count: expanded.length + frontier.length };
  }

  function viewNodes(rootI, zy, top, bottom) {
    var span = function (i) {
      if (zy(L.y1[i]) < top - 2 || zy(L.y0[i]) > bottom + 2) { return null; }   // outside the window
      return zy(L.y1[i]) - zy(L.y0[i]);
    };
    return heapView(rootI, span, function (best) { return best < MIN_PX; });
  }

  function overviewNodes(rootI, avail) {
    var target = clamp(Math.floor(avail / 16), 6, OVERVIEW_WEDGES);
    target = Math.min(target, Math.max(4, Math.floor(avail / OVERVIEW_MIN_H)));
    return heapView(rootI, function (i) { return L.nt[i]; },
                    function (best, count, open) { return open >= target || best <= 1; });
  }

  /* y in pixels for every drawn node: the true scale, or the minimum-height allocation of the opening view */
  function yMap(view, overview, zy, innerTop, innerBot) {
    var Y = {}, i, k;
    if (!overview) {
      view.expanded.forEach(function (n) { Y[n] = [zy(L.y0[n]), zy(L.y1[n])]; });
      view.frontier.forEach(function (n) { Y[n] = [zy(L.y0[n]), zy(L.y1[n])]; });
      return Y;
    }
    var f = view.frontier.slice().sort(function (a, b) { return L.y0[a] - L.y0[b]; });
    var avail = innerBot - innerTop, n = f.length || 1;
    var minH = Math.min(OVERVIEW_MIN_H, avail / n);
    var tips = 0;
    for (i = 0; i < f.length; i++) { tips += L.nt[f[i]]; }
    var spare = Math.max(0, avail - n * minH), at = innerTop;
    for (i = 0; i < f.length; i++) {
      var h = minH + (tips ? spare * L.nt[f[i]] / tips : spare / n);
      Y[f[i]] = [at, at + h];
      at += h;
    }
    var order = view.expanded.slice().sort(function (a, b) { return b - a; });   // children before parents
    for (i = 0; i < order.length; i++) {
      var kids = L.childrenOf(order[i]), lo = 1e9, hi = -1e9;
      for (k = 0; k < kids.length; k++) {
        var y = Y[kids[k]];
        if (!y) { continue; }
        if (y[0] < lo) { lo = y[0]; }
        if (y[1] > hi) { hi = y[1]; }
      }
      Y[order[i]] = hi > lo ? [lo, hi] : [innerTop, innerBot];
    }
    return Y;
  }

  function roundScale(span) {                   // a tidy scale-bar length: 1, 2 or 5 × a power of ten
    var want = span / 5, p = Math.pow(10, Math.floor(Math.log10(want))), f = want / p;
    return (f >= 5 ? 5 : (f >= 2 ? 2 : 1)) * p;
  }

  function drawPanel(P) {
    var t0 = (window.performance && performance.now) ? performance.now() : Date.now();
    var W = P.width, H = P.height, rootI = P.root;
    var innerTop = PAD.t, innerBot = H - PAD.b;
    var xMin = L.par[rootI] >= 0 ? L.dep[L.par[rootI]] : L.dep[rootI];
    var xMax = Math.max(L.maxDep[rootI], xMin + 1e-6);
    var x = d3.scaleLinear().domain([xMin, xMax]).range([PAD.l, W - PAD.r]);
    var yBase = d3.scaleLinear().domain([L.y0[rootI], L.y1[rootI]]).range([innerTop, innerBot]);
    var zy = P.t ? P.t.rescaleY(yBase) : yBase;
    var overview = L.par[rootI] === 0 && (!P.t || P.t.k < 1.02);
    var view = overview ? overviewNodes(rootI, innerBot - innerTop)
                        : viewNodes(rootI, zy, innerTop, innerBot);
    var Y = yMap(view, overview, zy, innerTop, innerBot);
    P.overview = overview;
    var mid = function (i) { var y = Y[i]; return y ? (y[0] + y[1]) / 2 : innerTop; };
    var hgt = function (i) { var y = Y[i]; return y ? y[1] - y[0] : 0; };

    var svg = P.svg;
    svg.selectAll('*').remove();
    svg.attr('viewBox', '0 0 ' + W + ' ' + H).attr('width', '100%').attr('height', H);

    /* faint grid of branch length, with the values below */
    var gridG = svg.append('g').attr('class', 'spx-grid');
    var ticks = x.ticks(Math.max(2, Math.round((W - PAD.l - PAD.r) / 110)));
    gridG.selectAll('line').data(ticks).join('line')
      .attr('x1', x).attr('x2', x).attr('y1', innerTop - 6).attr('y2', innerBot + 4);
    gridG.selectAll('text').data(ticks).join('text')
      .attr('x', x).attr('y', innerBot + 16).attr('text-anchor', 'middle')
      .text(function (v) { return d3.format('.2~f')(v); });

    /* branches: one path per expanded clade (its own edge plus the connector across its drawn children) */
    var paths = [];
    function edge(i) {
      var px = i === rootI ? x(xMin) : x(L.dep[L.par[i]]);
      return 'M' + px.toFixed(1) + ',' + mid(i).toFixed(1) + 'H' + x(L.dep[i]).toFixed(1);
    }
    view.expanded.forEach(function (i) {
      var kids = L.childrenOf(i), lo = 1e9, hi = -1e9, any = false, k;
      for (k = 0; k < kids.length; k++) {
        var c = kids[k];
        if (!Y[c]) { continue; }
        var m = mid(c);
        any = true;
        if (m < lo) { lo = m; }
        if (m > hi) { hi = m; }
      }
      var d = edge(i);
      if (any) { d += 'M' + x(L.dep[i]).toFixed(1) + ',' + lo.toFixed(1) + 'V' + hi.toFixed(1); }
      paths.push({ i: i, d: d });
    });
    svg.append('g').attr('class', 'spx-branches').selectAll('path').data(paths).join('path')
      .attr('class', 'spx-br').attr('data-node', function (d) { return d.i; })
      .style('stroke', function (d) { return branchColour(d.i); })
      .attr('d', function (d) { return d.d; });

    /* the frontier: tips as circles, clades as triangles whose height is their number of tips */
    var tips = [], wedges = [];
    view.frontier.forEach(function (i) {
      if (L.isTip(i)) { tips.push(i); } else { wedges.push(i); }
    });
    var tip = SP.tip();

    var wedgeG = svg.append('g').attr('class', 'spx-wedges');
    wedgeG.selectAll('path').data(wedges).join('path')
      .attr('class', 'spx-wedge')
      .attr('data-node', function (i) { return i; })
      .attr('data-tips', function (i) { return L.nt[i]; })
      .style('fill', function (i) { return phylumColour(i); })
      .style('stroke', function (i) { return phylumColour(i); })
      .style('fill-opacity', function (i) {
        return 0.22 + 0.5 * clamp(Math.log10(1 + L.nt[i]) / Math.log10(1 + 20000), 0, 1);
      })
      .attr('d', function (i) {
        var x0 = x(L.dep[i]), x1 = Math.max(x0 + 3, x(L.maxDep[i]));
        var a = Math.max(Y[i][0], innerTop - 4), b = Math.min(Y[i][1], innerBot + 4);
        if (b - a < 1.4) { var m = (a + b) / 2; a = m - 0.7; b = m + 0.7; }
        return 'M' + x0.toFixed(1) + ',' + ((a + b) / 2).toFixed(1) + 'L' + x1.toFixed(1) + ',' + a.toFixed(1) +
               'L' + x1.toFixed(1) + ',' + b.toFixed(1) + 'Z';
      })
      .on('mousemove', function (ev, i) { tip.show(wedgeTip(i), ev); })
      .on('mouseleave', function () { tip.hide(); })
      .on('click', function (ev, i) { if (ev.stopPropagation) { ev.stopPropagation(); } setFocus(i); });
    wedgeG.selectAll('path.spx-wedge-edge').data(wedges).join('path')
      .attr('class', 'spx-br spx-wedge-edge').style('stroke', function (i) { return branchColour(i); })
      .attr('d', function (i) { return edge(i); });

    var tipData = tips.map(function (i) {
      var id = taxonOfNode(i), ti = id >= 0 ? taxInfo(id) : null;
      return { i: i, id: id, t: ti, y: mid(i), x: x(L.dep[i]),
               r: tipRadius(ti ? ti.nSamples : 0), cls: clsCode(ti ? ti.cls : null) };
    });
    svg.append('g').attr('class', 'spx-tipedges').selectAll('path').data(tipData).join('path')
      .attr('class', 'spx-br').style('stroke', function (d) { return branchColour(d.i); })
      .attr('d', function (d) { return edge(d.i); });
    svg.append('g').attr('class', 'spx-tips').selectAll('circle').data(tipData).join('circle')
      .attr('class', function (d) { return 'spx-tip spx-cls-' + d.cls; })
      .attr('data-node', function (d) { return d.i; })
      .attr('data-taxon', function (d) { return d.id; })
      .attr('data-name', function (d) { return d.t ? d.t.name : ''; })
      .attr('cx', function (d) { return d.x; }).attr('cy', function (d) { return d.y; })
      .attr('r', function (d) { return d.r; })
      .style('fill', function (d) { return tipFill(d.t ? d.t.nSamples : 0); })
      .style('stroke', function (d) { return d.cls === 'N' ? '#ffffff' : CLASS_COLOUR[d.cls]; })
      .style('stroke-width', function (d) { return d.cls === 'N' ? 0.5 : 1.8; })
      .on('mousemove', function (ev, d) { tip.show(tipTipHtml(d), ev); })
      .on('mouseleave', function () { tip.hide(); })
      .on('click', function (ev, d) {
        if (ev.stopPropagation) { ev.stopPropagation(); }
        if (d.id >= 0) { openTaxon(d.id, { push: true }); }
      });

    /* --- labels: folded clades first (largest), then named open clades, then tips. A label that would
       overlap one already placed is dropped; every label gets a white backing copy underneath. */
    var placed = [], labels = [];
    function room(xx) { return W - 6 - xx; }
    function fits(bx, by, bw, tight) {
      /* tip names sit one per row: a box the height of the glyphs, so that rows 9-10 px apart all keep their name */
      var x2 = bx + bw, y1 = by - (tight ? 7.2 : 8.5), y2 = by + (tight ? 1.6 : 3.5), k;
      if (bx < 0 || x2 > W - 2) { return false; }
      for (k = 0; k < placed.length; k++) {
        var b = placed[k];
        if (bx < b[2] && b[0] < x2 && y1 < b[3] && b[1] < y2) { return false; }
      }
      placed.push([bx, y1, x2, y2]);
      return true;
    }
    function tryLabel(lab, main, sub, size) {
      var w1 = main.length * size, w2 = sub ? (sub.length * (size - 0.9) + 5) : 0;
      var avail = room(lab.x);
      if (avail < 34) { return; }
      if (w1 + w2 > avail) {
        if (sub && w1 + 5 <= avail) { sub = ''; w2 = 0; }                 // drop the tip count first
        else {
          var keep = Math.floor(avail / size) - 1;
          if (keep < 4) { return; }
          main = main.slice(0, keep) + '…'; w1 = (keep + 1) * size; sub = ''; w2 = 0;
        }
      }
      if (!fits(lab.x, lab.y, w1 + w2, lab.tight)) { return; }
      lab.main = main; lab.sub = sub; lab.w = w1 + w2;
      labels.push(lab);
    }
    wedges.slice().sort(function (a, b) { return L.nt[b] - L.nt[a]; }).forEach(function (i) {
      if (hgt(i) < CLADE_LABEL_PX) { return; }
      var nm = wedgeName(i);
      tryLabel({ i: i, x: Math.max(x(L.dep[i]) + 4, x(L.maxDep[i])) + 7, y: mid(i) + 3.5,
                 cls: 'spx-lab-clade', colour: phylumColour(i) }, nm,
               num(L.nt[i]) + (L.nt[i] === 1 ? ' tip' : ' tips'), 5.9);
    });
    view.expanded.slice().sort(function (a, b) { return L.rank[a] - L.rank[b]; }).forEach(function (i) {
      if (i === rootI) { return; }
      var r = L.ranks[L.rank[i]];
      if (r !== 'phylum' && r !== 'class' && r !== 'order' && r !== 'family') { return; }
      if (hgt(i) < OPEN_LABEL_PX) { return; }
      var info = nodeInfo(i);
      if (!info.label) { return; }
      /* just above the left end of this clade's own branch */
      tryLabel({ i: i, x: x(L.dep[L.par[i]]) + 3, y: mid(i) - 4.5,
                 cls: 'spx-lab-open spx-rank-' + r, colour: phylumColour(i) }, info.label, '', 5.6);
    });
    tipData.forEach(function (d) {
      if (hgt(d.i) < TIP_LABEL_PX || !d.t) { return; }
      tryLabel({ i: d.i, id: d.id, tight: true, x: d.x + d.r + 6, y: d.y + 3.4,
                 cls: 'spx-lab-tip' + (d.id === TREE.selTaxon ? ' on' : ''), colour: null },
               d.t.label, '', 5.9);
    });
    function paintLabels(g, halo) {
      g.selectAll('text').data(labels).join('text')
        .attr('class', function (d) { return (halo ? 'spx-lab-halo ' : '') + d.cls; })
        .attr('data-node', function (d) { return d.i; })
        .attr('data-w', function (d) { return d.w.toFixed(1); })
        .attr('x', function (d) { return d.x; }).attr('y', function (d) { return d.y; })
        .style('fill', function (d) { return halo ? null : (d.colour || null); })
        .each(function (d) {
          var t = d3.select(this);
          t.append('tspan').text(d.main);
          if (d.sub) { t.append('tspan').attr('class', 'spx-lab-n').attr('dx', 5).text(d.sub); }
        })
        .style('cursor', 'pointer')
        .on('click', function (ev, d) {
          if (ev.stopPropagation) { ev.stopPropagation(); }
          if (d.id !== undefined && d.id >= 0) { openTaxon(d.id, { push: true }); } else { setFocus(d.i); }
        });
    }
    paintLabels(svg.append('g').attr('class', 'spx-labels-halo'), true);
    paintLabels(svg.append('g').attr('class', 'spx-labels'), false);

    /* the selected taxon: a band across its tip range and a ring on its node */
    if (TREE.selNode >= 0) {
      var s = TREE.selNode;
      if (L.y1[s] > L.y0[rootI] && L.y0[s] < L.y1[rootI]) {
        var ys = Y[s] || [zy(L.y0[s]), zy(L.y1[s])];
        var a0 = clamp(ys[0], innerTop, innerBot), b0 = clamp(ys[1], innerTop, innerBot);
        var hl = svg.append('g').attr('class', 'spx-hl');
        /* a wide band is right for a tip; for a genus that fills the view it would wash the whole panel,
           so above 60 % of the height it becomes a bracket down the left edge */
        var tall = Math.abs(b0 - a0) > 0.6 * (innerBot - innerTop);
        hl.append('rect').attr('class', 'spx-hl-band').attr('x', PAD.l)
          .attr('y', Math.min(a0, b0) - 1)
          .attr('width', tall ? 5 : Math.max(2, W - PAD.l - 6))
          .attr('height', Math.max(3, Math.abs(b0 - a0) + 2));
        if (b0 - a0 < 400) {
          hl.append('circle').attr('class', 'spx-hl-ring').attr('cx', x(L.dep[s]))
            .attr('cy', clamp((a0 + b0) / 2, innerTop, innerBot)).attr('r', 7);
        }
      }
    }

    /* scale bar and the panel's own title */
    var sb = roundScale(xMax - xMin), sbx = W - PAD.r + 12, sby = H - 12;
    var sg = svg.append('g').attr('class', 'spx-scalebar');
    sg.append('line').attr('x1', sbx).attr('x2', sbx + (x(xMin + sb) - x(xMin))).attr('y1', sby).attr('y2', sby);
    sg.append('line').attr('x1', sbx).attr('x2', sbx).attr('y1', sby - 4).attr('y2', sby + 4);
    sg.append('line').attr('x1', sbx + (x(xMin + sb) - x(xMin))).attr('x2', sbx + (x(xMin + sb) - x(xMin)))
      .attr('y1', sby - 4).attr('y2', sby + 4);
    sg.append('text').attr('x', sbx).attr('y', sby - 8).text(d3.format('~g')(sb) + ' substitutions/site');
    svg.append('text').attr('class', 'spx-panel-title').attr('x', PAD.l).attr('y', 12)
      .text(P.title + ' · ' + num(L.nt[rootI]) + ' tips' +
            (overview ? ' · opening view: ' + wedges.length +
              ' folded clades, their heights not to scale' : '') +
            (P.t && P.t.k > 1.01 ? ' · zoom ×' + d3.format('.0f')(P.t.k) : ''));

    P.drawn = view.count;
    P.labels = labels.length;
    P.wedges = wedges.length;
    P.lastMs = ((window.performance && performance.now) ? performance.now() : Date.now()) - t0;
    svg.attr('data-drawn', view.count).attr('data-ms', P.lastMs.toFixed(1))
      .attr('data-wedges', wedges.length).attr('data-overview', overview ? '1' : '');
    return view.count;
  }

  /* --- names and colours ------------------------------------------------------------------------ */
  var RANK_PLURAL = { phylum: 'phyla', 'class': 'classes', order: 'orders', family: 'families',
                      genus: 'genera', species: 'species' };

  /* A folded clade is named after itself when it carries a GTDB rank name. Otherwise it is named after the
     clade inside it whose best-carried taxon reaches the most samples (the MAXIMUM of nSamples over the
     taxa below it, not the sum), at the highest rank that is present inside the wedge, and the other
     clades of that rank are counted: 'Bacteroidaceae and 11 other families'. */
  function wedgeName(i) {
    var info = nodeInfo(i);
    if (info.label) { return info.label; }
    for (var r = 0; r < L.rankKeys.length; r++) {
      if (L.cntR[r][i] > 0) {
        var best = L.bestR[r][i];
        var nm = best >= 0 ? nodeInfo(best).label : '';
        if (!nm) { continue; }
        var others = L.cntR[r][i] - 1;
        if (others <= 0) { return nm; }
        return nm + ' and ' + num(others) + ' other ' +
          (others === 1 ? L.rankKeys[r] : RANK_PLURAL[L.rankKeys[r]]);
      }
    }
    return cladeDisplayName(i);
  }

  /* the colour of a node is its phylum's; a clade ABOVE phylum level (a folded group of phyla) takes the
     colour of the phylum inside it whose best-carried taxon reaches the most samples */
  function branchColour(i) {
    var p = L.phy[i];
    return (p >= 0 && L.phyCol[p]) ? L.phyCol[p] : NEUTRAL;
  }
  function phylumColour(i) {
    var p = L.phy[i];
    if (p >= 0 && L.phyCol[p]) { return L.phyCol[p]; }
    if (p < 0) {
      var b = L.bestR[0][i];
      if (b >= 0 && L.phyCol[b]) { return L.phyCol[b]; }
    }
    return NEUTRAL;
  }
  function tipFill(samples) {
    var f = Math.log10(1 + Math.max(0, samples)) / Math.log10(1 + SAMP_TOP);
    return d3.interpolateRgb('#eceeed', '#2b3330')(clamp(f, 0, 1));
  }

  /* the name to show for a clade: its own GTDB name, else the nearest named ancestor ('within Bacteroides') */
  function cladeDisplayName(i) {
    var info = nodeInfo(i);
    if (info.label) { return info.label; }
    var p = L.par[i], guard = 0;
    while (p > 0 && guard++ < 80) {
      var up = nodeInfo(p);
      if (up.label) { return 'clade within ' + up.label; }
      p = L.par[p];
    }
    return 'unnamed clade';
  }

  function wedgeTip(i) {
    var r = L.ranks[L.rank[i]];
    return '<b>' + esc(wedgeName(i)) + '</b> <span class="sp-tip-sub">' + esc(r === 'clade' ? '' : r) +
      '</span><br>' + num(L.nt[i]) + ' tips inside<br><span class="sp-tip-sub">click to open this clade</span>';
  }
  function tipTipHtml(d) {
    if (!d.t) { return 'tip'; }
    var extra = d.t.cls ? '<br><b>' + esc(d.t.cls === 'StIL' ? 'StIL candidate' : d.t.cls) + '</b>' : '';
    return '<b><i>' + esc(d.t.label) + '</i></b> <span class="sp-tip-sub">' + esc(d.t.rank) + '</span><br>' +
      'carried by ' + num(d.t.nSamples) + ' samples' +
      (d.t.site ? '<br><span class="sp-tip-sub">leads in ' + esc(d.t.site.toLowerCase()) + '</span>' : '') +
      extra + '<br><span class="sp-tip-sub">click for its charts</span>';
  }

  /* ------------------------------------------------- the tree container, zoom, breadcrumb, legend */
  function panelTitle(rootI) {
    var info = nodeInfo(rootI), r = L.ranks[L.rank[rootI]];
    if (info.label && r !== 'clade' && r !== 'root') { return info.label + ' (' + r + ')'; }
    return wedgeName(rootI);
  }

  function buildPanels() {
    measure();                                  // the container changes width when the panel opens
    var host = TREE.el;
    host.innerHTML = '';
    var roots = panelRoots(TREE.focus), hs = panelHeights(roots, TREE.height);
    TREE.panels = roots.map(function (rootI, k) {
      var box = document.createElement('div');
      box.className = 'spx-tpanel';
      box.setAttribute('data-root', rootI);
      if (k > 0) {
        var brk = document.createElement('p');
        brk.className = 'spx-break';
        brk.textContent = 'separate tree — the two domains have no common scale';
        host.appendChild(brk);
      }
      host.appendChild(box);
      var P = { root: rootI, el: box, width: TREE.width, height: hs[k], title: panelTitle(rootI),
                t: d3.zoomIdentity, drawn: 0, lastMs: 0 };
      P.svg = d3.select(box).append('svg').attr('class', 'spx-tree')
        .attr('role', 'img').attr('tabindex', 0)
        .attr('aria-label', 'Phylogram of ' + P.title + ', ' + L.nt[rootI] + ' tips');
      P.zoom = d3.zoom().scaleExtent([1, Math.max(2, L.nt[rootI] / 4)])
        .translateExtent([[0, 0], [P.width, P.height]]).extent([[0, 0], [P.width, P.height]])
        .on('zoom', function (ev) { P.t = ev.transform; TREE.active = P; schedule(P); });
      P.svg.call(P.zoom);
      P.svg.on('click', function (ev) { TREE.active = P; });
      return P;
    });
    TREE.active = TREE.panels[0];
    TREE.panels.forEach(drawPanel);
    TREE.lastCount = TREE.panels.reduce(function (a, P) { return a + P.drawn; }, 0);
    TREE.lastDraw = TREE.panels.reduce(function (a, P) { return Math.max(a, P.lastMs); }, 0);
    host.dataset.ready = '1';
    host.dataset.drawn = String(TREE.lastCount);
    crumbs();
  }

  var _raf = {};
  function schedule(P) {                      // one redraw per frame while the wheel is turning
    if (_raf[P.root]) { return; }
    var run = function () { _raf[P.root] = 0; drawPanel(P); TREE.el.dataset.drawn = String(allDrawn()); };
    if (window.requestAnimationFrame) { _raf[P.root] = window.requestAnimationFrame(run); } else { run(); }
  }
  function allDrawn() { return TREE.panels.reduce(function (a, P) { return a + P.drawn; }, 0); }
  function redrawAll() {
    TREE.panels.forEach(drawPanel);
    TREE.lastCount = allDrawn();
    TREE.lastDraw = TREE.panels.reduce(function (a, P) { return Math.max(a, P.lastMs); }, 0);
    TREE.el.dataset.drawn = String(TREE.lastCount);
  }

  function setFocus(i, opts) {
    if (i === undefined || i === null || i < 0) { return; }
    if (L.isTip(i)) { i = L.par[i] >= 0 ? L.par[i] : i; }
    TREE.focus = i;
    buildPanels();
    if (!(opts && opts.quiet)) { TREE.el.dataset.focus = String(i); }
  }

  function crumbs() {
    var box = el('tx-crumbs');
    if (!box) { return; }
    var chain = SPX.path(TREE.focus).filter(function (i, k, arr) {
      if (i === 0 || k === arr.length - 1) { return true; }
      var r = L.ranks[L.rank[i]];
      return r !== 'clade';
    });
    box.innerHTML = chain.map(function (i) {
      var nm = i === 0 ? 'All life' : (nodeInfo(i).label || wedgeName(i));
      var col = i === 0 ? '' : '<i class="spx-dot" style="background:' + phylumColour(i) + '"></i>';
      return '<button type="button" class="sp-crumb" data-node="' + i + '">' + col + esc(nm) + '</button>';
    }).join('<span class="sp-crumb-sep">›</span>');
    Array.prototype.forEach.call(box.querySelectorAll('.sp-crumb'), function (b) {
      b.addEventListener('click', function () { setFocus(+b.dataset.node); });
    });
  }

  function zoomBy(f) {
    var P = TREE.active || TREE.panels[0];
    if (!P) { return; }
    P.svg.call(P.zoom.scaleBy, f);
  }
  function resetView() {
    TREE.panels.forEach(function (P) { P.svg.call(P.zoom.transform, d3.zoomIdentity); });
    setFocus(0);
  }
  function upOneLevel() {
    var f = TREE.focus;
    if (f === 0) { return; }
    setFocus(L.par[f] >= 0 ? L.par[f] : 0);
  }

  function buildZoomButtons() {
    var box = el('tx-zoom');
    if (!box) { return; }
    box.innerHTML = '';
    [['+', 'Zoom in', function () { zoomBy(1.8); }],
     ['−', 'Zoom out', function () { zoomBy(1 / 1.8); }],
     ['↑', 'Out one clade', upOneLevel],
     ['⟳', 'Whole tree', resetView]].forEach(function (c) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'spx-zbtn'; b.textContent = c[0];
      b.title = c[1]; b.setAttribute('aria-label', c[1]);
      b.addEventListener('click', c[2]);
      box.appendChild(b);
    });
  }

  function buildLegend() {
    var box = el('tx-legend');
    if (!box) { return; }
    var sizes = [[1, '1'], [300, '300'], [20000, '20 k'], [250000, '250 k']];
    var ramp = '<svg class="spx-legend-sizes" width="186" height="22" aria-hidden="true">' +
      sizes.map(function (s, k) {
        var cx = 13 + k * 45, r = tipRadius(s[0]);
        return '<circle class="spx-tip" cx="' + cx + '" cy="11" r="' + r.toFixed(2) + '" fill="' +
          tipFill(s[0]) + '" stroke="#ffffff" stroke-width="0.5"></circle>' +
          '<text class="spx-legend-t" x="' + (cx + 7) + '" y="15">' + s[1] + '</text>';
      }).join('') + '</svg>';
    var phyla = (L.topPhyla || []).map(function (i) {
      return '<span class="spx-key"><i class="spx-sw" style="background:' + L.phyCol[i] + '"></i>' +
        esc(nodeInfo(i).label) + '</span>';
    }).join('');
    box.innerHTML =
      '<div class="spx-legend-row"><span class="spx-key"><b>Phylum</b></span>' + phyla +
        '<span class="spx-key"><i class="spx-sw" style="background:' + NEUTRAL + '"></i>other phyla</span>' +
        '<span class="spx-key"><i class="spx-sw spx-sw-wedge"></i>folded clade (its colour, paler)</span></div>' +
      '<div class="spx-legend-row"><span class="spx-key"><b>Tip</b> samples carrying the taxon ' + ramp +
        '</span>' +
        '<span class="spx-key"><i class="spx-sw spx-ring-V"></i>VANISH</span>' +
        '<span class="spx-key"><i class="spx-sw spx-ring-B"></i>BloSSUM</span>' +
        '<span class="spx-key"><i class="spx-sw spx-ring-S"></i>StIL candidate</span>' +
        '<span class="spx-key"><i class="spx-sw spx-ring-N"></i>no class</span></div>';
  }

  /* bring a taxon into view: focus a clade of about REVEAL_TIPS tips around it, then zoom so its row is
     at least REVEAL_ROW_PX tall and centre on it */
  function revealTaxon(id) {
    var t = SPX.taxon(id);
    if (!t) { return; }
    TREE.selTaxon = id;
    TREE.selNode = t.node;
    var a = L.par[t.node] > 0 ? L.par[t.node] : t.node;
    while (L.par[a] > 0 && L.nt[L.par[a]] <= REVEAL_TIPS) { a = L.par[a]; }
    TREE.focus = a;
    buildPanels();
    var P = TREE.panels[0];
    if (!P) { return; }
    TREE.active = P;
    var innerTop = PAD.t, innerBot = P.height - PAD.b;
    var yBase = d3.scaleLinear().domain([L.y0[a], L.y1[a]]).range([innerTop, innerBot]);
    var rows = Math.max(1, L.y1[a] - L.y0[a]);
    var k = clamp(REVEAL_ROW_PX * rows / Math.max(1, innerBot - innerTop), 1, Math.max(2, rows / 4));
    var cy = (innerTop + innerBot) / 2;
    var ymid = (L.y0[t.node] + L.y1[t.node]) / 2;
    var tr = d3.zoomIdentity.translate(0, cy - k * yBase(ymid)).scale(k);
    P.svg.call(P.zoom.transform, tr);
    drawPanel(P);
    TREE.el.dataset.drawn = String(allDrawn());
  }

  /* ----------------------------------------------------------------- browse modes */
  var VIEW = { mode: 'list', at: '' };          // the taxonomy list is the opening view (round 4)
  var RANK_WORD = { d: 'domain', p: 'phylum', c: 'class', o: 'order', f: 'family', g: 'genus', s: 'species' };
  var modeSwitch = null;

  function buildModeSwitch() {
    var box = el('tx-mode');
    if (!box) { return; }
    modeSwitch = SP.segmented(box, {
      label: 'How to browse', value: VIEW.mode,
      options: [{ value: 'list', label: 'Taxonomy list', title: 'GTDB taxonomy, opened rank by rank, most carried first' },
                { value: 'tree', label: 'Tree', title: 'the GTDB reference tree, branch lengths to scale' }],
      onChange: function (v) { if (!VIEW.busy) { setMode(v); } }
    });
  }
  function setMode(mode, opts) {
    mode = mode === 'list' ? 'list' : 'tree';
    var changed = VIEW.mode !== mode;
    VIEW.mode = mode;
    var tb = el('tx-treebox'), lb = el('tx-listbox');
    if (tb) { tb.hidden = mode !== 'tree'; }
    if (lb) { lb.hidden = mode !== 'list'; }
    if (modeSwitch && modeSwitch.set && modeSwitch.value() !== mode) {
      VIEW.busy = true; modeSwitch.set(mode); VIEW.busy = false;   // keep the switch in step without a second pass
    }
    if (mode === 'list') { renderOutline(false); }
    else if (!el('tx-browse').hidden) { refitTree(); }
    document.body.dataset.spxMode = mode;
    if (!(opts && opts.quiet)) { setHashBody(browseHash(), false); }
  }

  /* The GTDB taxonomy as an outline. One entry per name: the names above genus come from the lineage of the
     genera (sp_taxdata reads it from the taxonomy table in the tree header), then every genus and species of
     the profiles. Built once, on the first use of the list (about a quarter of a second). */
  var OUT = null;
  function outline() {
    if (OUT) { return OUT; }
    var nodes = [], byPath = {}, byName = {}, genusNode = {}, taxNode = {};
    function add(path, r, name, par, tax) {
      var i = nodes.length;
      nodes.push({ i: i, r: r, name: name, par: par, kids: [], tax: tax, nGen: 0, nSp: 0, open: false, drawn: false });
      byPath[path] = i;
      if (byName[r + '__' + name] === undefined) { byName[r + '__' + name] = i; }
      if (par >= 0) { nodes[par].kids.push(i); }
      return i;
    }
    function walk(parts, upto) {                // make sure the first `upto` parts exist; return the last
      var par = -1, path = '', k;
      for (k = 0; k < upto; k++) {
        path += (k ? ';' : '') + parts[k][0] + '__' + parts[k][1];
        par = byPath[path] !== undefined ? byPath[path] : add(path, parts[k][0], parts[k][1], par, -1);
      }
      return par;
    }
    var nG = SPX.meta().tree.n_genera, nT = SPX.nTaxa(), id, t, par, i, p;
    for (id = 0; id < nG; id++) {
      t = SPX.taxon(id);
      par = walk(t.lineageParts, t.lineageParts.length - 1);
      i = add('G' + id, 'g', t.label, par, id);
      genusNode[t.label] = i; taxNode[id] = i;
      nodes[i].n = t.nSamples; nodes[i].cls = t.cls; nodes[i].lit = t.lit;
      for (p = par; p >= 0; p = nodes[p].par) { nodes[p].nGen++; }
    }
    for (id = nG; id < nT; id++) {
      t = SPX.taxon(id);
      var lp = t.lineageParts, g = lp.length >= 2 ? genusNode[lp[lp.length - 2][1]] : undefined;
      par = g !== undefined ? g : walk(lp, lp.length - 1);
      i = add('S' + id, 's', t.label, par, id);
      taxNode[id] = i;
      nodes[i].n = t.nSamples; nodes[i].cls = t.cls; nodes[i].lit = t.lit;
      for (p = par; p >= 0; p = nodes[p].par) { nodes[p].nSp++; }
    }
    /* samples carrying a name above genus: from the taxonomy table (samples with at least one of its genera
       detected); an older tree file has no such column, and the largest genus count below stands in */
    var tx = SPX.taxonomy(), hn = {};
    if (tx && tx.higher) {
      for (i = 0; i < tx.higher.length; i++) {
        var hk = tx.higher[i][0] + '__' + tx.higher[i][1];
        if (hn[hk] === undefined && tx.higher[i][3] !== undefined) { hn[hk] = tx.higher[i][3]; }
      }
    }
    for (i = nodes.length - 1; i >= 0; i--) {     // children come after their parents, so this runs bottom-up
      var nd0 = nodes[i];
      if (nd0.tax < 0) {
        var known = hn[nd0.r + '__' + nd0.name];
        nd0.n = known !== undefined ? known : (nd0.n || 0);
      }
      if (nd0.par >= 0 && nodes[nd0.par].tax < 0 && (nodes[nd0.par].n || 0) < nd0.n) { nodes[nd0.par].n = nd0.n; }
    }
    var roots = [];
    for (i = 0; i < nodes.length; i++) {
      if (nodes[i].par < 0) { roots.push(i); }
    }
    /* most widely carried first, the name as the tie-break */
    var byCarried = function (a, b) {
      return (nodes[b].n - nodes[a].n) || (nodes[a].name < nodes[b].name ? -1 : (nodes[a].name > nodes[b].name ? 1 : 0));
    };
    for (i = 0; i < nodes.length; i++) { nodes[i].kids.sort(byCarried); }
    roots.sort(byCarried);                                                // Bacteria before Archaea
    OUT = { nodes: nodes, roots: roots, byName: byName, taxNode: taxNode };
    return OUT;
  }
  function outlineIndexOfTaxon(id) { var o = outline(); return o.taxNode[id] === undefined ? -1 : o.taxNode[id]; }

  function outlineRow(nd) {
    var hasKids = nd.kids.length > 0, word = RANK_WORD[nd.r] || 'clade', nm, cnt;
    if (nd.tax >= 0) {
      nm = '<a class="spx-oname" href="#taxon=' + encodeURIComponent(nd.r + '__' + nd.name) + '" data-id="' +
        nd.tax + '"><i>' + esc(nd.name) + '</i></a>';
      cnt = 'carried by ' + num(nd.n) + ' samples' + (nd.r === 'g' && nd.nSp ? ' · ' + num(nd.nSp) + ' species' : '');
    } else {
      nm = '<button type="button" class="spx-oname spx-oname-btn" data-tog="' + nd.i + '">' + esc(nd.name) + '</button>';
      cnt = 'carried by ' + num(nd.n) + ' samples · ' + num(nd.nGen) + (nd.nGen === 1 ? ' genus' : ' genera') +
        ' · ' + num(nd.nSp) + ' species';
    }
    var cls = nd.cls ? ' <span class="spx-badge spx-badge-' + clsCode(nd.cls) + '">' +
      esc(nd.cls === 'StIL' ? 'StIL candidate' : nd.cls) + '</span>' : '';
    var lit = nd.lit ? ' <span class="spx-olit" title="has a summary from the literature">summary</span>' : '';
    return '<li class="spx-oli" data-k="' + nd.i + '"><div class="spx-orow">' +
      (hasKids ? '<button type="button" class="spx-otog" data-tog="' + nd.i + '" aria-expanded="false" ' +
        'aria-label="Open ' + esc(nd.name) + '">\u25B8</button>' : '<span class="spx-otog spx-otog-none"></span>') +
      '<span class="spx-orank spx-orank-' + nd.r + '" title="' + word + '">' + nd.r + '</span>' + nm +
      '<span class="spx-ocount">' + cnt + '</span>' + cls + lit + '</div>' +
      (hasKids ? '<ul class="spx-ol" hidden></ul>' : '') + '</li>';
  }
  function outlineLi(i) {
    var box = el('tx-list');
    return box ? box.querySelector('li[data-k="' + i + '"]') : null;
  }
  function renderOutline(force) {
    var box = el('tx-list');
    if (!box) { return; }
    if (box.dataset.ready === '1' && !force) { return; }
    var o = outline(), k;
    for (k = 0; k < o.nodes.length; k++) { o.nodes[k].open = false; }
    box.innerHTML = '<ul class="spx-ol spx-ol-root">' + o.roots.map(function (i) { return outlineRow(o.nodes[i]); }).join('') + '</ul>';
    if (box.dataset.wired !== '1') {
      box.dataset.wired = '1';
      box.addEventListener('click', function (ev) {
        var tgt = ev.target, guard = 0;
        while (tgt && tgt !== box && guard++ < 6) {
          if (tgt.dataset && tgt.dataset.tog !== undefined) { toggleOutline(+tgt.dataset.tog); return; }
          if (tgt.dataset && tgt.dataset.id !== undefined) {
            ev.preventDefault();
            openTaxon(+tgt.dataset.id, { push: true });
            return;
          }
          tgt = tgt.parentNode;
        }
      });
      var ca = el('tx-list-collapse');
      if (ca) { ca.addEventListener('click', function () { VIEW.at = ''; renderOutline(true); setHashBody(browseHash(), false); }); }
    }
    box.dataset.ready = '1';
    box.dataset.nodes = String(o.nodes.length);
    if (o.roots.length) { setOpen(o.roots[0], true); }       // Bacteria open to its phyla, as GTDB's page does
  }
  function setOpen(i, open) {
    var o = outline(), nd = o.nodes[i], li = outlineLi(i);
    if (!li || !nd.kids.length) { return; }
    var ul = li.querySelector('ul'), tog = li.querySelector('.spx-otog');
    if (open && !ul.dataset.filled) {
      ul.innerHTML = nd.kids.map(function (k) { return outlineRow(o.nodes[k]); }).join('');
      ul.dataset.filled = '1';
    }
    ul.hidden = !open;
    nd.open = open;
    if (tog) { tog.setAttribute('aria-expanded', open ? 'true' : 'false'); tog.textContent = open ? '\u25BE' : '\u25B8'; }
  }
  function toggleOutline(i) {
    var o = outline(), nd = o.nodes[i];
    if (!nd) { return; }
    setOpen(i, !nd.open);
    VIEW.at = nd.r + '__' + nd.name;
    setHashBody(browseHash(), false);
  }
  /* open every ancestor of entry i (and i itself when it has children), then bring it into view */
  function expandTo(i, flash) {
    var o = outline();
    if (i === undefined || i < 0 || !o.nodes[i]) { return; }
    renderOutline(false);
    var chain = [], p;
    for (p = o.nodes[i].par; p >= 0; p = o.nodes[p].par) { chain.push(p); }
    chain.reverse();
    chain.forEach(function (k) { setOpen(k, true); });
    if (o.nodes[i].kids.length) { setOpen(i, true); }
    var anc = o.nodes[i].tax >= 0 ? o.nodes[i].par : i;
    VIEW.at = anc >= 0 ? o.nodes[anc].r + '__' + o.nodes[anc].name : '';
    var li = outlineLi(i);
    if (li) {
      var old = el('tx-list').querySelector('.spx-oli-at');
      if (old) { old.classList.remove('spx-oli-at'); }
      li.classList.add('spx-oli-at');
      if (flash && li.scrollIntoView) { try { li.scrollIntoView({ block: 'center' }); } catch (e) { /* jsdom */ } }
    }
  }

  /* ----------------------------------------------------------------- search */
  function initSearch() {
    var input = el('tx-search'), hits = el('tx-hits');
    if (!input || !hits) { return; }
    /* sp_core's searchBox filters a JavaScript array and keeps the keyboard handling; the 32,713 names
       live in the binary file, so `items` is a function and the candidate list for the current query comes
       from SPX.search (prefix first, then word starts, ties by sample count) on every keystroke. */
    return SP.searchBox({
      input: input, results: hits, limit: 12,
      items: function () {
        var q = input.value.replace(/^\s+|\s+$/g, '');
        return q.length >= 2 ? SPX.search(q, 24).map(hitItem) : [];
      },
      empty: 'No genus or species of this release matches.',
      onPick: function (it) { openTaxon(it.id, { push: true }); if (input.blur) { input.blur(); } }
    });
  }
  function hitItem(t) {
    var sub = t.rank + ' · ' + num(t.nSamples) + ' samples' + (t.site ? ' · leads in ' + t.siteLabel : '') +
      (t.via === 'literature' ? ' · literature name: ' + t.litName : '');
    return { id: t.id, name: t.name, url: '#taxon=' + encodeURIComponent(t.name),
             html: '<i>' + esc(t.label) + '</i>', sub: sub, keys: [t.name, t.litName || ''] };
  }

  /* ----------------------------------------------------------------- the detail panel */
  var PANEL = { id: -1, dim: 'site', metric: 'prev', chart: null, globe: null, world: null, projLoaded: false };

  function statsReady() { return !!(SPX.meta() && SPX.meta().stats); }

  function panelSkeleton(t) {
    var cls = t.cls ? '<a class="spx-badge spx-badge-' + clsCode(t.cls) + '" href="' +
      esc(CFG.vanishHref || '../vanish.html') + '#taxon=' + encodeURIComponent(t.name) + '">' + esc(t.cls === 'StIL' ? 'StIL candidate' : t.cls) +
      '</a>' : '';
    var lin = t.lineageParts.map(function (p, k) {
      var full = p[0] + '__' + p[1];
      if (p[0] === 'g' || p[0] === 's') {
        return '<a href="#taxon=' + encodeURIComponent(full) + '" data-lin="' + esc(full) + '">' +
          esc(p[1]) + '</a>';
      }
      return '<a href="#view=list&amp;at=' + encodeURIComponent(full) + '" data-clade="' + esc(full) +
        '" title="Go to this ' + (RANK_WORD[p[0]] || 'clade') + '">' + esc(p[1]) + '</a>';
    }).join('<span class="spx-lin-sep">›</span>');
    var lead = '<i>' + esc(t.label) + '</i> is a ' + t.rank + ' carried by <b>' + num(t.nSamples) +
      '</b> of the ' + num(CFG.nSamples || 0) + ' QC-clean samples' +
      (t.site ? ', and leads in <b>' + esc(t.siteLabel.toLowerCase()) + '</b> samples' :
        ', and has no body site that passes the display threshold') + '.';
    var back = VIEW.mode === 'list' ? 'Back to the taxonomy list' : 'Back to the tree';
    return '' +
      '<div class="spx-detail-bar">' +
        '<button type="button" class="sp-seg-btn spx-back" id="txp-back">\u2190 ' + back + '</button>' +
        '<button type="button" class="sp-seg-btn" id="txp-in-tree">Show in the tree</button>' +
        '<button type="button" class="sp-seg-btn" id="txp-in-list">Show in the taxonomy list</button>' +
      '</div>' +
      '<div class="spx-panel-head">' +
        '<button type="button" class="spx-close" id="txp-close" aria-label="Close this taxon">×</button>' +
        '<h2 id="txp-title"><i>' + esc(t.label) + '</i> ' + cls + '</h2>' +
        '<p class="spx-lin">' + lin + '</p>' +
        '<p class="spx-sum" id="txp-sum">' + lead + '</p>' +
      '</div>' +
      '<div class="spx-detail-grid">' +
        '<div class="spx-detail-main">' +
          '<div class="sp-controls">' +
            '<span class="sp-ctl-label">Compare</span><div id="txp-dim"></div>' +
            '<span class="sp-ctl-label">Show</span><div id="txp-measure"></div>' +
          '</div>' +
          '<div id="txp-chart" data-ready=""></div>' +
          '<p class="sp-note" id="txp-note"></p>' +
          '<div id="txp-carriers" data-state=""></div>' +
        '</div>' +
        '<div class="spx-globe-row spx-detail-side"><div id="txp-globe" data-ready=""></div>' +
          '<p class="sp-caption" id="txp-globe-note"></p></div>' +
      '</div>' +
      '<div id="txp-lit"></div>' +
      '<section class="sp-details">' +
        (t.rank === 'genus' ? '<details class="sp-more" id="txp-species-details"><summary>Species of this ' +
          'genus in the catalog</summary><div id="txp-species"><p class="small muted">Opening…</p></div></details>' : '') +
        '<details class="sp-more" id="txp-projects-details"><summary>Projects where it is most abundant</summary>' +
          '<div id="txp-projects"><p class="small muted">Opening…</p></div></details>' +
      '</section>' +
      '<p class="spx-share"><label class="visually-hidden" for="txp-link">Link to this taxon</label>' +
        '<input id="txp-link" class="spx-link" type="text" readonly value="">' +
        '<button type="button" class="sp-seg-btn spx-copy" id="txp-copy">Copy link</button>' +
        '<span class="spx-copied" id="txp-copied" hidden>copied</span></p>';
  }

  function openTaxon(id, opts) {
    opts = opts || {};
    var t = SPX.taxon(id);
    if (!t) { return; }
    PANEL.id = id;
    PANEL.projLoaded = false;
    PANEL.chart = null;
    PANEL.globe = null;
    var box = el('tx-panel');
    box.hidden = false;
    box.dataset.taxon = t.name;
    box.dataset.state = 'taxon';
    el('tx-browse').hidden = true;              // the taxon view replaces the tree / the outline
    box.innerHTML = panelSkeleton(t);
    el('txp-close').addEventListener('click', closePanel);
    el('txp-back').addEventListener('click', closePanel);
    el('txp-in-tree').addEventListener('click', function () { showInTree(id); });
    el('txp-in-list').addEventListener('click', function () { showInList(id); });
    el('txp-copy').addEventListener('click', copyLink);
    Array.prototype.forEach.call(box.querySelectorAll('.spx-lin a'), function (a) {
      a.addEventListener('click', function (ev) {
        ev.preventDefault();
        if (a.dataset.lin) {
          var hit = SPX.byName(a.dataset.lin);
          if (hit >= 0) { openTaxon(hit, { push: true }); }
        } else if (a.dataset.clade) {
          gotoClade(a.dataset.clade, id);
        }
      });
    });
    SP.segmented(el('txp-dim'), {
      label: 'What to compare', value: PANEL.dim,
      options: DIMS.map(function (d) { return { value: d.key, label: d.label }; }),
      onChange: function (v) { PANEL.dim = v; drawPanelChart(); }
    });
    SP.segmented(el('txp-measure'), {
      label: 'What to show', value: PANEL.metric,
      options: [{ value: 'prev', label: 'Prevalence' }, { value: 'mean', label: 'Abundance' }],
      onChange: function (v) { PANEL.metric = v; drawPanelChart(); }
    });
    drawPanelChart();
    fillLiterature(t);
    wireDetails(t);
    TREE.selTaxon = id; TREE.selNode = t.node;  // marked in the tree when the reader goes back to it
    setHash(t.name, opts.push !== false);
    setLink();
    if (opts.scroll !== false && window.scrollTo) {
      var top = el('tx-search');
      try { (top && top.scrollIntoView) ? top.scrollIntoView({ block: 'start' }) : window.scrollTo(0, 0); } catch (e) { /* jsdom */ }
    }
  }

  /* ----------------------------------------------------------------- leaving the taxon view */
  function showBrowse() {
    var box = el('tx-panel');
    box.hidden = true;
    box.innerHTML = '';
    box.removeAttribute('data-taxon');
    box.dataset.state = '';
    PANEL.id = -1;
    el('tx-browse').hidden = false;
    setMode(VIEW.mode, { quiet: true });        // draws the list on its first use, refits the tree's width
  }
  function refitTree() {                        // the tree keeps its state while hidden; only its width may be stale
    var w = TREE.width;
    measure();
    if (Math.abs(w - TREE.width) > 8) { buildPanels(); } else { redrawAll(); }
  }
  function showInTree(id) {
    showBrowse();
    setMode('tree', { quiet: true });
    revealTaxon(id);
    setHashBody(browseHash(), true);
  }
  function showInList(id) {
    var t = SPX.taxon(id);
    showBrowse();
    setMode('list', { quiet: true });
    if (t) { expandTo(outlineIndexOfTaxon(id), true); }
    setHashBody(browseHash(), true);
  }
  /* a clade above genus, clicked in the lineage of the taxon view: go to it in the current browse mode */
  function gotoClade(full, fromTaxon) {
    showBrowse();
    var node = -1;
    if (VIEW.mode === 'tree' && fromTaxon >= 0) {
      var chain = SPX.path(SPX.taxon(fromTaxon).node), k;
      for (k = chain.length - 1; k >= 0 && node < 0; k--) {
        var names = String(nodeInfo(chain[k]).name || '').split(';');
        for (var j = 0; j < names.length; j++) { if (names[j].replace(/^\s+|\s+$/g, '') === full) { node = chain[k]; } }
      }
    }
    if (VIEW.mode === 'tree' && node >= 0) {
      setFocus(node);
    } else {                                    // the outline has every clade, also one the pruned tree lost
      setMode('list', { quiet: true });
      var o = outline();
      if (o.byName[full] !== undefined) { expandTo(o.byName[full], true); }
    }
    setHashBody(browseHash(), true);
  }

  function closePanel() {
    showBrowse();
    setHashBody(browseHash(), true);
  }

  function setLink() {
    var inp = el('txp-link');
    if (inp) { inp.value = String(window.location.href); }
  }
  function copyLink() {
    var inp = el('txp-link');
    if (!inp) { return; }
    inp.select && inp.select();
    var done = function () { var c = el('txp-copied'); if (c) { c.hidden = false; setTimeout(function () { c.hidden = true; }, 1600); } };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(inp.value).then(done, function () {});
    } else if (document.execCommand) {
      try { document.execCommand('copy'); done(); } catch (e) { /* nothing to do */ }
    }
  }

  /* ----------------------------------------------------------------- the panel's charts */
  function chartRows(st, dim, metric) {
    var rows = st[dim] || [], out = [], i;
    for (i = 0; i < rows.length; i++) {
      var r = rows[i];
      if (!r.main) { continue; }
      if (!r.shown) {
        if (dim !== 'country') { out.push({ key: r.key, label: r.label, value: 0, below: true, r: r }); }
        continue;
      }
      if (dim === 'country' && !r.detected) { continue; }
      out.push({ key: r.key, label: r.label, value: metric === 'prev' ? r.prevalence : r.mean, r: r });
    }
    out.sort(function (a, b) { return (b.below ? -1 : b.value) - (a.below ? -1 : a.value); });
    if (dim === 'country') { out = out.slice(0, 20); }
    return out;
  }

  function drawPanelChart() {
    var box = el('txp-chart'), note = el('txp-note');
    if (!box || PANEL.id < 0) { return; }
    if (!statsReady()) { box.innerHTML = '<p class="small muted">Loading the numbers…</p>'; return; }
    var id = PANEL.id, t = SPX.taxon(id), st = SPX.stats(id), dim = PANEL.dim, metric = PANEL.metric;
    var dimLabel = DIMS.filter(function (d) { return d.key === dim; })[0].label;
    if (dim !== 'site' && st.noScope) {
      box.innerHTML = '<p class="spx-empty">This taxon has no body site that passes the display threshold ' +
        '(' + CFG.minSamples + ' samples in ' + CFG.minProjects + ' projects), so it has no ' +
        esc(dimLabel.toLowerCase()) + ' numbers. Its body-site row is still there — switch to “Body site”.</p>';
      note.textContent = '';
      if (PANEL.globe) { PANEL.globe.update({}); }
      return;
    }
    var rows = chartRows(st, dim, metric);
    var label = metric === 'prev' ? 'Samples with the taxon detected (%)'
                                  : 'Mean relative abundance (%, zeros included)';
    var top = rows.reduce(function (m, r) { return Math.max(m, r.value); }, 0);
    var dp = top >= 0.1 ? 0 : (top >= 0.01 ? 1 : 2);
    var spec = {
      rows: rows, label: label, width: box.clientWidth || PANEL.width || 470, rowH: 23,
      colour: metric === 'prev' ? '#1f5aa6' : '#8a6d2f',
      max: top > 0 ? top : 1,
      axisFormat: function (v) { return (v * 100).toFixed(dp); },
      valueFormat: function (d) { return d.below ? 'too few samples' : pct(d.value, d.value < 0.01 ? 2 : 1); },
      onHover: function (d) {
        var r = d.r;
        if (d.below) {
          return '<b>' + esc(r.label) + '</b><br>' + num(r.n) + ' samples in ' + num(r.proj) +
            ' projects — below the display threshold';
        }
        return '<b>' + esc(r.label) + '</b><br>Detected in <b>' + pct(r.prevalence) + '</b> of ' + num(r.n) +
          ' samples<br>Mean relative abundance <b>' + pct(r.mean, 2) + '</b><br>' +
          '<span class="sp-tip-sub">' + num(r.detected) + ' samples · ' + num(r.detectedProjects) +
          ' of ' + num(r.proj) + ' projects</span>';
      },
      alt: label + ' by ' + dimLabel.toLowerCase() + ' for ' + t.name
    };
    var hov = spec.onHover;
    if (CFG.presenceDir && window.SPP) {         // list the carriers of the group, here on the page
      spec.onClick = function (d) { showCarriers(id, dim, d, st); };
      spec.onHover = function (d) {
        return hov(d) + '<br><span class="sp-tip-sub">click: list the samples of this group that carry it</span>';
      };
    } else if (CFG.samplesHref) {                // no presence files in this build: the group in the sample table
      spec.onClick = function (d) { SP.go(samplesUrl(dim, d.key, st)); };
      spec.onHover = function (d) {
        return hov(d) + '<br><span class="sp-tip-sub">click: open the sample table for this group</span>';
      };
    }
    if (CAR.tax !== id || CAR.dim !== dim) { clearCarriers(); }   // the list belongs to one taxon and one comparison
    box.innerHTML = '';
    PANEL.chart = SP.barChart(box, spec);
    box.dataset.ready = '1';
    box.dataset.rows = String(rows.length);
    var scope = dim === 'site' ? 'all QC-clean specimen samples'
                               : 'among ' + (st.scope ? st.scope.label.toLowerCase() : '') + ' samples';
    note.innerHTML = esc(dimLabel) + ' · ' + esc(scope) +
      (dim === 'country' ? ' · the ' + rows.length + ' highest; the globe has the rest' : '') +
      ' · groups with fewer than ' + (CFG.minSamples || 30) + ' samples or fewer than ' +
      (CFG.minProjects || 2) + ' projects are marked “too few samples”' +
      (CFG.presenceDir && window.SPP ? ' · click a bar to list the samples of that group that carry this taxon'
        : (CFG.samplesHref ? ' · click a bar to open the sample table for that group (every sample of the ' +
          'group, with or without this taxon)' : ''));
    drawGlobe(t, st);
  }

  /* the sample table (samples/index.html) reads its filters from the address; the explorer's group keys are
     the table's own codes. Age, country and lifestyle bars are computed within the taxon's leading body
     site, so that site goes into the address with them. The table cannot filter on a taxon. */
  var NULL_KEYS = { unknown_site: 1, no_site_attribute: 1, not_recorded: 1 };
  function samplesUrl(dim, key, st) {
    var q = [], val = NULL_KEYS[key] ? '__null__' : key;
    var add = function (k, v) { q.push(k + '=' + encodeURIComponent(v)); };
    if (dim === 'site') {
      add('body_site_code', val);
    } else {
      if (st && st.scope && st.scope.site) { add('body_site_code', st.scope.site); }
      add(dim === 'age' ? 'age_category' : (dim === 'country' ? 'country' : 'lifestyle'), val);
    }
    return CFG.samplesHref + '?' + q.join('&');
  }

  /* ----------------------------------------------------------------- the samples behind a bar (round 4) */
  /* A click on a bar lists the samples of that group that carry the taxon. The data are in data/taxa/presence/
     (sp_presence.js decodes them): index.bin is fetched once, on the first click, and one shard per taxon. */
  var PRES = { index: null, shards: {}, order: [] };
  var CAR = { tax: -1, dim: '', key: '', idx: null, shown: 0, label: '', url: null };
  var CAR_PAGE = 100;

  function fetchBin(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) { throw new Error(url.split('/').pop() + ': HTTP ' + r.status); }
      return r.arrayBuffer();
    });
  }
  function presenceIndex() {
    if (!PRES.index) {
      PRES.index = fetchBin(CFG.presenceDir + 'index.bin').then(function (b) { window.SPP.loadIndex(b); return true; });
      PRES.index.catch(function () { PRES.index = null; });
    }
    return PRES.index;
  }
  function presenceShard(k) {
    if (!PRES.shards[k]) {
      PRES.shards[k] = fetchBin(CFG.presenceDir + window.SPP.shardName(k)).then(function (b) { return new Uint8Array(b); });
      PRES.order.push(k);
      if (PRES.order.length > 8) { delete PRES.shards[PRES.order.shift()]; }     // a few megabytes at most
    }
    return PRES.shards[k];
  }
  function carriersOf(id) {                    // a promise of the sorted sample indices of every carrier
    return presenceIndex().then(function () {
      var k = window.SPP.shardOf(id);
      if (k < 0) { return new Uint32Array(0); }
      return presenceShard(k).then(function (bytes) { return window.SPP.carriers(id, bytes); });
    });
  }
  /* the group a bar stands for: a body site, or the taxon's leading body site and one age group, country or
     lifestyle — the same samples the bar's own numbers are computed on */
  function barCriteria(dim, key, st) {
    if (dim === 'site') { return { site: key }; }
    var c = { site: st && st.scope ? st.scope.site : null };
    c[dim] = key;
    return c;
  }
  function groupLabel(dim, key) {
    var g = (SPX.meta().stats.groups[dim] || []), i;
    for (i = 0; i < g.length; i++) { if (g[i].key === key) { return g[i].label; } }
    return key === null || key === undefined ? '\u2014' : String(key);
  }
  function clearCarriers() {
    var box = el('txp-carriers');
    if (box) { box.innerHTML = ''; box.dataset.state = ''; box.dataset.key = ''; }
    CAR.tax = -1; CAR.idx = null;
  }
  function showCarriers(id, dim, d, st) {
    var box = el('txp-carriers');
    if (!box) { return; }
    var t = SPX.taxon(id);
    var label = dim === 'site' ? d.label : ((st.scope ? st.scope.label + ' \u00b7 ' : '') + d.label);
    CAR = { tax: id, dim: dim, key: d.key, idx: null, shown: 0, label: label,
            url: CFG.samplesHref ? samplesUrl(dim, d.key, st) : null };
    box.dataset.state = 'loading';
    box.dataset.key = dim + ':' + d.key;
    box.innerHTML = '<h3 class="spx-h3">Samples carrying <i>' + esc(t.label) + '</i> \u2014 ' + esc(label) +
      '</h3><p class="small muted">Loading the list\u2026</p>';
    return carriersOf(id).then(function (all) {
      if (CAR.tax !== id || CAR.dim !== dim || CAR.key !== d.key) { return; }   // another bar was clicked meanwhile
      CAR.idx = window.SPP.filter(all, barCriteria(dim, d.key, st));
      renderCarriers();
    }).catch(function (e) {
      if (CAR.tax !== id) { return; }
      box.dataset.state = 'error';
      box.innerHTML = '<p class="small muted">The sample list could not be loaded (' +
        esc(e && e.message ? e.message : 'unknown error') + ').</p>';
    });
  }
  function renderCarriers() {
    var box = el('txp-carriers'), t = SPX.taxon(CAR.tax), n = CAR.idx.length, seen = {}, np = 0, i, p;
    for (i = 0; i < n; i++) { p = window.SPP.studyIndex(CAR.idx[i]); if (!seen[p]) { seen[p] = 1; np++; } }
    box.innerHTML = '<h3 class="spx-h3">Samples carrying <i>' + esc(t.label) + '</i> \u2014 ' + esc(CAR.label) + '</h3>' +
      '<p class="small"><b>' + num(n) + '</b> sample' + (n === 1 ? '' : 's') + ' in ' + num(np) + ' project' +
      (np === 1 ? '' : 's') + '.' +
      (n ? ' <button type="button" class="spx-linkbtn" id="txp-car-csv">Download the list (CSV)</button> \u00b7' : '') +
      (CAR.url ? ' <a href="' + esc(CAR.url) + '">Open the sample table for this group</a> (every sample of ' +
        'the group, with or without this taxon) \u00b7' : '') +
      ' <button type="button" class="spx-linkbtn" id="txp-car-close">Close the list</button></p>' +
      (n ? '<table class="small spx-cartable"><thead><tr><th>Sample</th><th>Project</th><th>Body site</th>' +
           '<th>Age group</th><th>Country</th><th>Lifestyle</th></tr></thead><tbody id="txp-car-rows"></tbody></table>' +
           '<p class="small" id="txp-car-foot"></p>'
         : '<p class="small">No sample of this group carries it.</p>');
    box.dataset.state = 'ready';
    box.dataset.count = String(n);
    box.dataset.projects = String(np);
    el('txp-car-close').addEventListener('click', clearCarriers);
    if (n) {
      el('txp-car-csv').addEventListener('click', downloadCarriers);
      CAR.shown = 0;
      moreCarriers();
    }
    if (box.scrollIntoView) { try { box.scrollIntoView({ block: 'nearest' }); } catch (e) { /* jsdom */ } }
  }
  function moreCarriers() {
    var body = el('txp-car-rows'), foot = el('txp-car-foot'), n = CAR.idx.length, html = '', i, s;
    var to = Math.min(n, CAR.shown + CAR_PAGE);
    for (i = CAR.shown; i < to; i++) {
      s = window.SPP.sample(CAR.idx[i]);
      html += '<tr><td class="mono"><a href="' + esc(CFG.samplePage + encodeURIComponent(s.key)) + '">' + esc(s.key) +
        '</a></td><td class="mono"><a href="' + esc(CFG.studyPage + encodeURIComponent(s.project) + '.html') + '">' +
        esc(s.project) + '</a></td><td>' + esc(groupLabel('site', s.site)) + '</td><td>' + esc(groupLabel('age', s.age)) +
        '</td><td>' + esc(s.country ? groupLabel('country', s.country) : '\u2014') + '</td><td>' +
        esc(groupLabel('life', s.life)) + '</td></tr>';
    }
    body.insertAdjacentHTML('beforeend', html);
    CAR.shown = to;
    foot.innerHTML = 'Showing ' + num(to) + ' of ' + num(n) + ', in order of project.' +
      (to < n ? ' <button type="button" class="spx-linkbtn" id="txp-car-more">Show ' + Math.min(CAR_PAGE, n - to) +
        ' more</button>' : '');
    var more = el('txp-car-more');
    if (more) { more.addEventListener('click', moreCarriers); }
  }
  function carriersCsv() {
    var rows = ['sample,project,body_site,age_category,country,lifestyle'], i, s;
    for (i = 0; i < CAR.idx.length; i++) {
      s = window.SPP.sample(CAR.idx[i]);
      rows.push([s.key, s.project, s.site, s.age, s.country || '', s.life].join(','));
    }
    return rows.join('\n') + '\n';
  }
  function downloadCarriers() {
    var t = SPX.taxon(CAR.tax);
    try {
      var blob = new Blob([carriersCsv()], { type: 'text/csv' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = t.slug + '_' + CAR.dim + '_' + CAR.key + '_samples.csv';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
    } catch (e) { /* no Blob support: the list stays on the page */ }
  }

  function globeValues(st, metric) {
    var v = {}, rows = st.country || [], i;
    for (i = 0; i < rows.length; i++) {
      if (rows[i].shown && rows[i].detected) { v[rows[i].key] = metric === 'prev' ? rows[i].prevalence : rows[i].mean; }
    }
    return v;
  }

  function drawGlobe(t, st) {
    var box = el('txp-globe'), note = el('txp-globe-note');
    if (!box) { return; }
    if (st.noScope || !(st.country || []).length) {
      box.innerHTML = '';
      if (note) { note.textContent = ''; }
      return;
    }
    var vals = globeValues(st, PANEL.metric);
    if (PANEL.globe && box.dataset.ready === '1') { PANEL.globe.update(vals); paintGlobeNote(st); return; }
    if (!PANEL.world) {
      SP.getJSON(CFG.root + 'static/vendor/countries-110m.json').then(function (w) {
        PANEL.world = w;
        drawGlobe(t, st);
      }).catch(function () { box.innerHTML = '<p class="small muted">The globe could not be drawn.</p>'; });
      return;
    }
    var groups = SPX.meta().stats.groups.country, numToIso = {}, names = {}, i;
    for (i = 0; i < groups.length; i++) {
      var g = groups[i];
      names[g.key] = g.label;
      if (g.map) {                       // the basemap writes ids both padded and unpadded
        numToIso[String(g.map)] = g.key;
        numToIso[String(+g.map)] = g.key;
      }
    }
    box.innerHTML = '';
    PANEL.globe = SP.globe(box, {
      world: PANEL.world, numToIso: numToIso, names: names, values: vals, size: 290,
      alt: 'Globe: ' + t.name + ' by country',
      format: function (v) {
        return PANEL.metric === 'prev' ? 'Detected in ' + pct(v) + ' of samples' : 'Mean abundance ' + pct(v, 2);
      },
      onClick: CFG.studiesHref ? function (iso) { SP.go(CFG.studiesHref + encodeURIComponent(iso)); } : null
    });
    box.dataset.ready = '1';
    paintGlobeNote(st);
  }
  function paintGlobeNote(st) {
    var note = el('txp-globe-note');
    if (!note) { return; }
    var n = Object.keys(globeValues(st, PANEL.metric)).length;
    note.innerHTML = 'Countries — ' + esc(st.scope ? st.scope.label.toLowerCase() : '') + ' samples, ' + n +
      ' above the threshold. Drag to spin, wheel or the buttons to zoom, click a country for its projects.' +
      '<span class="sp-legend"><span class="sp-ramp"></span>' +
      (PANEL.metric === 'prev' ? 'detected (%)' : 'mean relative abundance (%)') + '</span>';
  }

  /* ----------------------------------------------------------------- literature, on demand */
  function fillLiterature(t) {
    var box = el('txp-lit');
    if (!box) { return; }
    if (!t.lit) { box.innerHTML = ''; box.dataset.state = 'none'; return; }
    box.dataset.state = 'loading';
    box.innerHTML = '<p class="small muted">Loading the published summary…</p>';
    var want = t.id;
    SP.getJSON(CFG.dataDir + 'lit/' + t.slug + '.json').then(function (d) {
      if (PANEL.id !== want) { return; }
      var refs = (d.references || []).map(function (r) {
        return '<li value="' + (r.n || 1) + '">' + esc(r.authors || '') + ' ' + esc(r.title || '') +
          ' <i>' + esc(r.journal || '') + '</i>' + (r.year ? ' ' + esc(r.year) : '') + '.' +
          (r.doi ? ' <a href="https://doi.org/' + esc(r.doi) + '">doi:' + esc(r.doi) + '</a>' : '') +
          (r.pmid ? ' PMID ' + esc(r.pmid) : '') + '</li>';
      }).join('');
      var text = (d.sentences || []).map(function (s) {
        return esc(s.text) + (s.refs && s.refs.length ? ' [' + s.refs.join(', ') + ']' : '');
      }).join(' ');
      box.innerHTML = '<h3 class="spx-h3">Summary from the literature</h3><div class="sp-lit">' +
        '<p class="sp-ai-note">' + esc(d.label || LIT_LABEL) + '</p>' +
        (d.name_note ? '<p class="small">' + esc(d.name_note) + '</p>' : '') +
        '<p>' + text + '</p>' + (refs ? '<ol class="sp-refs">' + refs + '</ol>' : '') + '</div>';
      box.dataset.state = 'ready';
      box.dataset.refs = String((d.references || []).length);
    }).catch(function () {
      if (PANEL.id !== want) { return; }
      box.innerHTML = '<p class="small muted">The published summary for this taxon could not be loaded.</p>';
      box.dataset.state = 'error';
    });
  }

  /* ----------------------------------------------------------------- projects (optional file) and species */
  function wireDetails(t) {
    var pd = el('txp-projects-details');
    if (pd) {
      pd.addEventListener('toggle', function () { if (pd.open) { fillProjects(t); } });
    }
    var sd = el('txp-species-details');
    if (sd) {
      sd.addEventListener('toggle', function () { if (sd.open) { fillSpecies(t); } });
    }
  }

  var projectsState = 'cold';     // cold | loading | ready | absent
  function fillProjects(t) {
    var box = el('txp-projects');
    if (!box) { return; }
    var render = function () {
      if (PANEL.id !== t.id) { return; }
      if (projectsState !== 'ready') {
        box.innerHTML = '<p class="small muted">The most-abundant-project list is not published in this ' +
          'release.</p>';
        box.dataset.state = projectsState;
        return;
      }
      var rows = SPX.projects(t.id) || [];
      box.dataset.state = 'ready';
      box.dataset.rows = String(rows.length);
      if (!rows.length) {
        box.innerHTML = '<p class="small">No project at this body site has enough samples for this list.</p>';
        return;
      }
      box.innerHTML = '<p class="small">' + esc(t.siteLabel || '') + ' samples, projects with at least 10 of ' +
        'them, ranked by mean relative abundance. A small project reaches the top of this list easily.</p>' +
        '<table class="small"><thead><tr><th>Project</th><th class="num">Samples</th>' +
        '<th class="num">Detected</th><th class="num">Mean abundance</th><th>Title</th></tr></thead><tbody>' +
        rows.map(function (r) {
          return '<tr><td class="mono">' + esc(r.accession) + '</td><td class="num">' + num(r.n) +
            '</td><td class="num">' + pct(r.prevalence, 0) + '</td><td class="num">' + pct(r.mean, 2) +
            '</td><td>' + esc(r.title || '') + '</td></tr>';
        }).join('') + '</tbody></table>';
    };
    if (projectsState === 'ready' || projectsState === 'absent') { render(); return; }
    if (projectsState === 'loading') { return; }
    projectsState = 'loading';
    box.innerHTML = '<p class="small muted">Loading the project list…</p>';
    fetch(CFG.dataDir + 'projects.bin').then(function (r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.arrayBuffer();
    }).then(function (b) {
      SPX.loadProjects(b);
      projectsState = 'ready';
      render();
    }).catch(function () { projectsState = 'absent'; render(); });
  }

  function fillSpecies(t) {
    var box = el('txp-species');
    if (!box || t.rank !== 'genus') { return; }
    var prefix = 's__' + t.label + ' ';
    var out = [], stack = [t.node], guard = 0;
    while (stack.length && guard++ < 200000) {
      var i = stack.pop(), ids = nodeInfo(i).taxa, k;
      for (k = 0; k < ids.length; k++) {
        var ti = taxInfo(ids[k]);
        if (ti.rank === 'species' && ti.name.indexOf(prefix) === 0) { out.push({ id: ids[k], t: ti }); }
      }
      var kids = L.childrenOf(i);
      for (k = 0; k < kids.length; k++) { stack.push(kids[k]); }
    }
    out.sort(function (a, b) { return b.t.nSamples - a.t.nSamples; });
    box.dataset.count = String(out.length);
    if (!out.length) {
      box.innerHTML = '<p class="small">No species of this genus has its own row in the species profiles.</p>';
      return;
    }
    var shown = out.slice(0, 80);
    box.innerHTML = '<p class="small">' + num(out.length) + ' species, most carried first' +
      (out.length > shown.length ? ' — the first ' + shown.length + ' are listed' : '') + '.</p><ul class="spx-splist">' +
      shown.map(function (s) {
        return '<li><a href="#taxon=' + encodeURIComponent(s.t.name) + '" data-id="' + s.id + '"><i>' +
          esc(s.t.label) + '</i></a> <span class="muted small">' + num(s.t.nSamples) + ' samples</span></li>';
      }).join('') + '</ul>';
    Array.prototype.forEach.call(box.querySelectorAll('a[data-id]'), function (a) {
      a.addEventListener('click', function (ev) { ev.preventDefault(); openTaxon(+a.dataset.id, { push: true }); });
    });
  }

  /* ----------------------------------------------------------------- the address */
  function hashGet(key) {
    var h = String(window.location.hash || '');
    var m = new RegExp('(?:^#|&)' + key + '=([^&]*)').exec(h);
    if (!m) { return null; }
    try { return decodeURIComponent(m[1].replace(/\+/g, ' ')); } catch (e) { return m[1]; }
  }
  function hashName() { return hashGet('taxon'); }
  function browseHash() {                      // the address of the browse view: nothing for the plain list
    if (VIEW.mode === 'tree') { return 'view=tree'; }
    return VIEW.at ? 'view=list&at=' + encodeURIComponent(VIEW.at) : '';
  }
  var muteHash = false;
  function setHash(name, push) { setHashBody(name ? 'taxon=' + encodeURIComponent(name) : browseHash(), push); }
  function setHashBody(body, push) {
    muteHash = true;
    var url = window.location.pathname + window.location.search + (body ? '#' + body : '');
    try {
      if (push) { window.history.pushState({ hash: body }, '', url); }
      else { window.history.replaceState({ hash: body }, '', url); }
    } catch (e) { window.location.hash = body || ''; }
    setTimeout(function () { muteHash = false; }, 0);
  }
  function notFound(name) {
    var box = el('tx-panel');
    box.hidden = false;
    box.innerHTML = '<div class="spx-panel-head"><h2>Not in this release</h2>' +
      '<p class="spx-sum">No taxon named <span class="mono">' + esc(name) + '</span> is in the catalog\u2019s ' +
      'profiles for this release. Search for another name above.</p></div>';
    box.dataset.taxon = '';
    box.dataset.state = 'not-found';
    PANEL.id = -1;
    el('tx-browse').hidden = false;             // the message sits above the tree, which stays usable
    var s = el('tx-search');
    if (s && s.focus) { s.focus(); }
  }
  function applyHash(opts) {
    var name = hashName();
    if (!name) {
      var mode = hashGet('view') === 'tree' ? 'tree' : 'list', at = hashGet('at');
      if (!el('tx-panel').hidden) { showBrowse(); }
      setMode(mode, { quiet: true });
      if (mode === 'list' && at) {
        var o = outline();
        if (o.byName[at] !== undefined) { expandTo(o.byName[at], false); VIEW.at = at; }
      }
      return;
    }
    var id = SPX.byName(name);
    if (id < 0 && name.indexOf('__') < 0) {           // allow a bare name too
      var hit = SPX.search(name, 1);
      if (hit.length && hit[0].label.toLowerCase() === name.toLowerCase()) { id = hit[0].id; }
    }
    if (id < 0) { notFound(name); return; }
    openTaxon(id, { push: false, scroll: false });
  }

  /* ----------------------------------------------------------------- loading and boot */
  function status(msg, kind) {
    var s = el('tx-status');
    if (!s) { return; }
    s.textContent = msg || '';
    s.hidden = !msg;
    s.className = 'spx-status' + (kind ? ' spx-status-' + kind : '');
  }

  function measure() {
    var host = el('tx-tree');
    TREE.el = host;
    TREE.width = Math.max(320, host.clientWidth || +(host.dataset.width || 0) || 900);
    TREE.height = +(host.dataset.height || 700);
    if (window.innerWidth && window.innerWidth < 760) { TREE.height = Math.min(TREE.height, 520); }
  }

  function boot() {
    var host = el('tx-tree');
    if (!host || !SPX || !SP || !d3) { return; }
    status('Loading the taxa (2.5 MB)…');
    fetch(CFG.dataDir + 'tree.bin').then(function (r) {
      if (!r.ok) { throw new Error('tree.bin: HTTP ' + r.status); }
      return r.arrayBuffer();
    }).then(function (buf) {
      SPX.loadTree(buf);
      buildLayout();
      measure();
      buildZoomButtons();
      buildLegend();
      buildPanels();
      buildModeSwitch();
      initSearch();
      status('Loading the numbers…');
      window.addEventListener('hashchange', function () { if (!muteHash) { applyHash(); } });
      window.addEventListener('popstate', function () { applyHash(); });
      var t0 = null;
      window.addEventListener('resize', function () {
        clearTimeout(t0);
        t0 = setTimeout(function () {
          if (PANEL.id >= 0) { drawPanelChart(); return; }   // the taxon view is open: only its chart
          if (VIEW.mode !== 'tree') { return; }
          var w = TREE.width;
          measure();
          if (Math.abs(w - TREE.width) > 8) {
            TREE.panels.forEach(function (P) { P.width = TREE.width; });
            redrawAll();
          }
        }, 180);
      });
      applyHash();
      return fetch(CFG.dataDir + 'stats.bin').then(function (r) {
        if (!r.ok) { throw new Error('stats.bin: HTTP ' + r.status); }
        return r.arrayBuffer();
      }).then(function (b) {
        SPX.loadStats(b);
        document.body.dataset.spxStats = '1';
        status('');
        if (PANEL.id >= 0) { drawPanelChart(); }
      });
    }).catch(function (e) {
      status('The explorer could not load its data (' + (e && e.message ? e.message : 'unknown error') +
             '). The files are listed under Downloads at the foot of the page.', 'error');
      if (window.console) { console.error(e); }
    });
  }

  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', boot); } else { boot(); }

  /* the headless tests drive the page through this handle */
  window.SP_EXPLORER = {
    boot: boot, openTaxon: openTaxon, setFocus: setFocus, revealTaxon: revealTaxon,
    redrawAll: redrawAll, applyHash: applyHash, closePanel: closePanel,
    setMode: setMode, outline: outline, expandTo: expandTo, toggleOutline: toggleOutline,
    samplesUrl: samplesUrl, showInTree: showInTree, showInList: showInList, gotoClade: gotoClade,
    showCarriers: showCarriers, carriersCsv: carriersCsv, carriersOf: carriersOf, moreCarriers: moreCarriers,
    carriers: function () { return CAR; },
    view: function () { return VIEW; },
    state: function () {
      return { focus: TREE.focus, drawn: allDrawn(), ms: TREE.lastDraw, selected: PANEL.id,
               panels: TREE.panels.map(function (P) {
                 return { root: P.root, drawn: P.drawn, ms: P.lastMs, k: P.t ? P.t.k : 1,
                          height: P.height, width: P.width };
               }), cap: DRAW_CAP, minPx: MIN_PX };
    },
    layout: function () { return L; },
    tree: TREE
  };
})();
