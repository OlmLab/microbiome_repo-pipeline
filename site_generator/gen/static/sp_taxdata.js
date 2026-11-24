/* sp_taxdata.js — reader for the Taxon explorer payload (tree.bin, stats.bin, projects.bin).
 *
 * Dependency-free, ES5, no network calls: the page fetches the files and hands the buffers over.
 *   SPX.loadTree(bufferOrObject)   -> meta of the tree file
 *   SPX.loadStats(buffer)          -> meta of the statistics file
 *   SPX.loadProjects(buffer)       -> meta of the optional most-abundant-projects file
 *   SPX.taxon(id) / SPX.stats(id) / SPX.projects(id) / SPX.search(text, limit)
 *   SPX.node(i) / SPX.children(i) / SPX.path(i) / SPX.taxaAt(i) / SPX.depth(i) / SPX.root()
 * Every file format is described in FORMAT.md. Loadable in a browser (global SPX) and in Node
 * (module.exports = SPX) for the headless tests.
 */
(function (root) {
  'use strict';

  var T = null;      // tree state
  var S = null;      // stats state
  var P = null;      // projects state
  var RANK_OF = {};  // rank name -> code, filled on load

  /* ------------------------------------------------------------------ helpers */
  function bytesOf(buf) {
    if (buf instanceof Uint8Array) { return buf; }
    if (typeof ArrayBuffer !== 'undefined' && buf instanceof ArrayBuffer) { return new Uint8Array(buf); }
    if (buf && buf.buffer) { return new Uint8Array(buf.buffer, buf.byteOffset || 0, buf.byteLength); }
    throw new Error('sp_taxdata: expected an ArrayBuffer, a typed array or a Node Buffer');
  }

  function utf8(b, from, to) {
    var out = '', i = from, c, c2, c3;
    while (i < to) {
      c = b[i++];
      if (c < 0x80) { out += String.fromCharCode(c); }
      else if (c < 0xE0) { c2 = b[i++]; out += String.fromCharCode(((c & 0x1F) << 6) | (c2 & 0x3F)); }
      else if (c < 0xF0) {
        c2 = b[i++]; c3 = b[i++];
        out += String.fromCharCode(((c & 0x0F) << 12) | ((c2 & 0x3F) << 6) | (c3 & 0x3F));
      } else {                                   /* astral plane: surrogate pair */
        var c4 = b[i++], cp = ((c & 0x07) << 18) | ((b[i - 3] & 0x3F) << 12) | ((b[i - 2] & 0x3F) << 6) | (c4 & 0x3F);
        cp -= 0x10000;
        out += String.fromCharCode(0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF));
      }
    }
    return out;
  }

  /* magic (4) | format version uint16 | header length uint32 | header JSON | 8-byte aligned sections */
  function open(buf, magic) {
    var b = bytesOf(buf);
    var m = String.fromCharCode(b[0], b[1], b[2], b[3]);
    if (m !== magic) { throw new Error('sp_taxdata: expected ' + magic + ', found ' + m); }
    var dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
    var version = dv.getUint16(4, true), hlen = dv.getUint32(6, true);
    var header = JSON.parse(utf8(b, 10, 10 + hlen));
    var start = 10 + hlen;
    var abs = b.byteOffset + start;
    if (abs % 8 !== 0) {                        /* re-align so typed-array views are legal */
      var copy = new Uint8Array(b.byteLength - start);
      copy.set(b.subarray(start));
      b = copy; start = 0;
    }
    var buffer = b.buffer, base = b.byteOffset + start;
    return {
      version: version, header: header, bytes: b, start: start,
      u8: function (name) {
        var s = header.sections[name];
        return new Uint8Array(buffer, base + s[0], s[1]);
      },
      u32: function (name) {
        var s = header.sections[name];
        return new Uint32Array(buffer, base + s[0], s[1] / 4);
      },
      i32: function (name) {
        var s = header.sections[name];
        return new Int32Array(buffer, base + s[0], s[1] / 4);
      }
    };
  }

  function strAt(blob, offs, i) { return utf8(blob, offs[i], offs[i + 1]); }

  function bare(name) {                          /* 's__Blautia wexlerae' -> 'Blautia wexlerae' */
    var k = name.indexOf('__');
    return k > 0 ? name.substring(k + 2) : name;
  }

  function slugify(name) {                       /* the literature files of round 2 use this slug */
    return bare(name).replace(/ /g, '_').replace(/[^A-Za-z0-9_]/g, '_');
  }

  /* varint cursor over a Uint8Array */
  function readVarint(b, c) {
    var v = 0, s = 0, x;
    do { x = b[c.i++]; v += (x & 0x7F) * Math.pow(2, s); s += 7; } while (x & 0x80);
    return v;
  }

  /* ------------------------------------------------------------------- tree */
  function loadTree(buf) {
    GENUS_ID = null;
    if (buf && !(buf instanceof Uint8Array) && !(typeof ArrayBuffer !== 'undefined' && buf instanceof ArrayBuffer) &&
        !buf.buffer && typeof buf === 'object') {
      T = plainTree(buf);                        /* already-decoded object, used by tests */
    } else {
      var f = open(buf, 'SPXT'), h = f.header;
      T = {
        header: h, n: h.n_nodes, nTaxa: h.n_taxa,
        parent: f.i32('parent'), len: f.u32('len'), rank: f.u8('rank'), ntips: f.u32('ntips'),
        nodeCls: f.u8('nodeCls'), nameOff: f.u32('nameOff'), nameBlob: f.u8('nameBlob'),
        taxNode: f.u32('taxNode'), taxSamples: f.u32('taxSamples'), taxSite: f.u8('taxSite'),
        taxCls: f.u8('taxCls'), taxFlags: f.u8('taxFlags'),
        taxNameOff: f.u32('taxNameOff'), taxNameBlob: f.u8('taxNameBlob'),
        litIds: f.u32('litIds'), litNameOff: f.u32('litNameOff'), litNameBlob: f.u8('litNameBlob')
      };
    }
    var h2 = T.header, i;
    for (i = 0; i < h2.ranks.length; i++) { RANK_OF[h2.ranks[i]] = i; }
    T.lenScale = h2.len_scale;
    /* children in CSR form, taxa per node, cumulative branch length from the root */
    var n = T.n, cnt = new Uint32Array(n + 1), order = new Uint32Array(n), depth = new Float64Array(n);
    for (i = 1; i < n; i++) { cnt[T.parent[i]]++; }
    var starts = new Uint32Array(n + 1), acc = 0;
    for (i = 0; i < n; i++) { starts[i] = acc; acc += cnt[i]; }
    starts[n] = acc;
    var fill = new Uint32Array(n);
    for (i = 1; i < n; i++) {
      var p = T.parent[i];
      order[starts[p] + fill[p]++] = i;
      depth[i] = depth[p] + T.len[i] / T.lenScale;
    }
    T.kidStart = starts; T.kids = order; T.depthArr = depth;
    var at = {};
    for (i = 0; i < T.nTaxa; i++) {
      var nd = T.taxNode[i];
      if (at[nd] === undefined) { at[nd] = [i]; } else { at[nd].push(i); }
    }
    T.taxaAtNode = at;
    T.litName = {};
    for (i = 0; i < T.litIds.length; i++) {
      T.litName[T.litIds[i]] = strAt(T.litNameBlob, T.litNameOff, i);
    }
    T.names = null;                              /* search index, built on first search */
    return meta();
  }

  function plainTree(o) {                        /* {header, parent:[], len:[], ... } with plain arrays */
    var t = {}, k;
    for (k in o) { if (o.hasOwnProperty(k)) { t[k] = o[k]; } }
    t.n = o.header.n_nodes; t.nTaxa = o.header.n_taxa;
    return t;
  }

  function taxonName(id) { return strAt(T.taxNameBlob, T.taxNameOff, id); }

  function nodeName(i) {
    var s = strAt(T.nameBlob, T.nameOff, i);
    if (s) { return s; }
    var ids = T.taxaAtNode[i];                   /* a nameless node takes the name of the taxon sitting on it */
    return ids ? taxonName(ids[ids.length - 1]) : '';
  }

  function taxon(id) {
    if (!T || id === undefined || id === null || id < 0 || id >= T.nTaxa) { return null; }
    var node = T.taxNode[id], name = taxonName(id), flags = T.taxFlags[id];
    var site = T.taxSite[id];
    return {
      id: id, name: name, label: bare(name), slug: slugify(name),
      rank: id < T.header.n_genera ? 'genus' : 'species',
      node: node, lineage: lineage(lineageFor(id, node)), lineageParts: lineageFor(id, node),
      nSamples: T.taxSamples[id],
      site: site === 255 ? null : T.header.site_keys[site],
      siteLabel: site === 255 ? null : T.header.site_labels[site],
      cls: T.header.classes[T.taxCls[id]] || null,
      lit: (flags & 1) === 1, genusOnly: (flags & 2) === 2, unplaced: (flags & 4) === 4,
      noScope: (flags & 8) === 8,
      litName: T.litName[id] || null,
      nTips: T.ntips[node], depth: T.depthArr[node]
    };
  }

  /* the GTDB clade names carried by the nodes from the root down to node i, as [rank letter, bare name] */
  function lineageParts(i) {
    var out = [], p = i;
    while (p >= 0) {
      var nm = strAt(T.nameBlob, T.nameOff, p);
      if (nm) {
        var parts = nm.split(';');
        for (var k = parts.length - 1; k >= 0; k--) {
          var s = parts[k].replace(/^\s+|\s+$/g, '');
          if (s.length > 3 && s.charAt(1) === '_' && s.charAt(2) === '_') { out.push([s.charAt(0), bare(s)]); }
        }
      }
      p = p === 0 ? -1 : T.parent[p];
    }
    out.reverse();
    return out;
  }

  /* GTDB's own lineage of a taxon, from the taxonomy table in the tree header (round 4): domain to family from
     header.taxonomy, then the genus and, for a species, the species. null when the header has no table or the
     genus has no entry; the caller then reads the lineage off the tree's clade labels as before. */
  var GENUS_ID = null;
  function genusIdOf(id) {
    if (id < T.header.n_genera) { return id; }
    if (!GENUS_ID) {
      GENUS_ID = {};
      for (var g = 0; g < T.header.n_genera; g++) { GENUS_ID[taxonName(g)] = g; }
    }
    var nm = taxonName(id), sp = nm.indexOf(' ');
    var gn = 'g__' + (sp < 0 ? nm.slice(3) : nm.slice(3, sp));
    return GENUS_ID[gn] === undefined ? -1 : GENUS_ID[gn];
  }
  function taxonomyLineage(id) {
    var tx = T.header.taxonomy;
    if (!tx || !tx.higher || !tx.genus_parent) { return null; }
    var g = genusIdOf(id);
    if (g < 0) { return null; }
    var p = tx.genus_parent[g], out = [], guard = 0;
    if (p === undefined || p < 0) { return null; }
    while (p >= 0 && guard++ < 12) { out.push([tx.higher[p][0], tx.higher[p][1]]); p = tx.higher[p][2]; }
    out.reverse();
    out.push(['g', bare(taxonName(g))]);
    if (id >= T.header.n_genera) { out.push(['s', bare(taxonName(id))]); }
    return out;
  }
  function taxonomy() { return T && T.header.taxonomy ? T.header.taxonomy : null; }

  /* the same, with the taxa that sit on the node itself appended: the genus of a species whose node carries no
     g__ label of its own, and the taxon asked for (a tip may carry both a genus and its single species) */
  function lineageFor(id, i) {
    var tx = taxonomyLineage(id);
    if (tx) { return tx; }
    var out = lineageParts(i), seen = {}, k, ids = T.taxaAtNode[i] || [];
    for (k = 0; k < out.length; k++) { seen[out[k][0] + '__' + out[k][1]] = 1; }
    var sorted = ids.slice().sort(function (x, y) { return x - y; });
    for (k = 0; k < sorted.length; k++) {
      if (sorted[k] > id) { continue; }
      var nm = taxonName(sorted[k]);
      if (!seen[nm]) { out.push([nm.charAt(0), bare(nm)]); seen[nm] = 1; }
    }
    return out;
  }

  function lineage(parts) {
    var out = [];
    for (var k = 0; k < parts.length; k++) { out.push(parts[k][0] + '__' + parts[k][1]); }
    return out.join(';');
  }

  function node(i) {
    if (!T || i < 0 || i >= T.n) { return null; }
    var ids = T.taxaAtNode[i] || [];
    return {
      index: i, name: nodeName(i), label: bare(nodeName(i).split(';')[0]),
      rank: T.header.ranks[T.rank[i]], parent: i === 0 ? -1 : T.parent[i],
      len: T.len[i] / T.lenScale, depth: T.depthArr[i], nTips: T.ntips[i],
      isTip: T.kidStart[i + 1] === T.kidStart[i], taxa: ids,
      cls: T.header.classes[T.nodeCls[i]] || null
    };
  }

  function children(i) {
    var out = [];
    if (!T || i < 0 || i >= T.n) { return out; }
    for (var k = T.kidStart[i]; k < T.kidStart[i + 1]; k++) { out.push(T.kids[k]); }
    return out;
  }

  function path(i) {
    var out = [];
    if (!T || i < 0 || i >= T.n) { return out; }
    while (i > 0) { out.push(i); i = T.parent[i]; }
    out.push(0);
    out.reverse();
    return out;
  }

  function taxaAt(i) { return (T && T.taxaAtNode[i]) ? T.taxaAtNode[i].slice() : []; }
  function depth(i) { return T ? T.depthArr[i] : 0; }
  function rootIndex() { return 0; }

  /* ----------------------------------------------------------------- search */
  function buildSearch() {
    var n = T.nTaxa, names = new Array(n), lower = new Array(n), i;
    for (i = 0; i < n; i++) {
      names[i] = taxonName(i);
      lower[i] = bare(names[i]).toLowerCase();
    }
    var alias = [];                              /* literature names that differ from the GTDB name */
    for (i = 0; i < T.litIds.length; i++) {
      alias.push([T.litIds[i], T.litName[T.litIds[i]].toLowerCase()]);
    }
    T.names = names; T.lower = lower; T.alias = alias;
  }

  function search(text, limit) {
    if (!T) { return []; }
    if (!T.names) { buildSearch(); }
    var q = String(text || '').replace(/^\s+|\s+$/g, '').toLowerCase();
    limit = limit || 20;
    if (!q) { return []; }
    var hits = [], seen = {}, i, k, nm;
    function push(id, score, why) {
      if (seen[id] !== undefined) { return; }
      seen[id] = 1;
      hits.push({ id: id, score: score, via: why });
    }
    for (i = 0; i < T.nTaxa && hits.length < limit * 8; i++) {
      nm = T.lower[i];
      k = nm.indexOf(q);
      if (k === 0) { push(i, nm.length === q.length ? 0 : 1, 'name'); }
      else if (k > 0) { push(i, nm.charAt(k - 1) === ' ' ? 2 : 3, 'name'); }
    }
    for (i = 0; i < T.alias.length; i++) {
      k = T.alias[i][1].indexOf(q);
      if (k === 0) { push(T.alias[i][0], 1.5, 'literature'); }
      else if (k > 0) { push(T.alias[i][0], 3.5, 'literature'); }
    }
    hits.sort(function (a, b) {
      if (a.score !== b.score) { return a.score - b.score; }
      var da = T.taxSamples[b.id] - T.taxSamples[a.id];
      if (da !== 0) { return da; }
      return T.lower[a.id] < T.lower[b.id] ? -1 : 1;
    });
    var out = [];
    for (i = 0; i < hits.length && out.length < limit; i++) {
      var t = taxon(hits[i].id);
      t.via = hits[i].via;
      out.push(t);
    }
    return out;
  }

  /* ------------------------------------------------------------------ stats */
  function loadStats(buf) {
    var f = open(buf, 'SPXS');
    S = { header: f.header, index: f.u32('index'), cells: f.u8('cells'), dims: f.header.dims,
          groups: f.header.groups, scale: f.header.ra_scale, denoms: f.header.denoms,
          minN: f.header.min_cell_samples, minP: f.header.min_cell_projects };
    return meta();
  }

  function decodeCells(dim, k, c) {
    var g = [], d = [], m = [], p = [], i, prev = 0;
    for (i = 0; i < k; i++) { prev += readVarint(S.cells, c); g.push(prev); }
    for (i = 0; i < k; i++) { d.push(readVarint(S.cells, c)); }
    for (i = 0; i < k; i++) { m.push(S.cells[c.i] | (S.cells[c.i + 1] << 8)); c.i += 2; }
    for (i = 0; i < k; i++) { p.push(readVarint(S.cells, c)); }
    return { g: g, d: d, m: m, p: p };
  }

  /* The body-site dimension is over every sample; age, country and lifestyle are over the samples of the taxon's
     leading body site, whose denominators are in header.denoms[dim][site key]. A taxon with no leading site
     (taxFlags bit 8) gets empty arrays for those three dimensions and out.noScope === true. */
  function stats(id) {
    if (!S || id === undefined || id === null || id < 0 || id >= S.header.n_taxa) { return null; }
    if (!T) { throw new Error('sp_taxdata: load the tree before the statistics (the scope comes from it)'); }
    var site = T.taxSite[id], scopeKey = site === 255 ? null : S.header.site_keys[site];
    var out = { scope: scopeKey === null ? null
      : { site: scopeKey, label: T.header.site_labels[site], index: site }, noScope: scopeKey === null };
    var c = { i: S.index[id] }, end = S.index[id + 1], di, j, k;
    for (di = 0; di < S.dims.length; di++) {
      var dim = S.dims[di], gs = S.groups[dim], scoped = dim !== 'site';
      var dn = scoped && scopeKey !== null ? S.denoms[dim][scopeKey] : null;
      var rows = (scoped && scopeKey === null) ? [] : new Array(gs.length);
      for (j = 0; j < rows.length; j++) {
        var nn = scoped ? dn[j][0] : gs[j].n, np = scoped ? dn[j][1] : gs[j].proj;
        rows[j] = { key: gs[j].key, label: gs[j].label, n: nn, proj: np,
                    shown: scoped ? (nn >= S.minN && np >= S.minP) : gs[j].shown,
                    main: gs[j].main, iso2: gs[j].iso2, map: gs[j].map,
                    detected: 0, detectedProjects: 0, prevalence: 0, mean: 0 };
      }
      k = readVarint(S.cells, c);
      var cc = decodeCells(dim, k, c);
      for (j = 0; j < k; j++) {
        var r = rows[cc.g[j]];
        r.detected = cc.d[j];
        r.detectedProjects = cc.p[j];
        r.prevalence = r.n ? cc.d[j] / r.n : 0;
        r.mean = Math.pow(10, -cc.m[j] / S.scale);
      }
      out[dim] = rows;
    }
    if (c.i !== end) { throw new Error('sp_taxdata: stats record for taxon ' + id + ' did not end where the index says'); }
    return out;
  }

  /* --------------------------------------------------------------- projects */
  function loadProjects(buf) {
    var f = open(buf, 'SPXP'), d = f.u32('projDenom'), i;
    P = { header: f.header, index: f.u32('index'), cells: f.u8('cells'),
          accOff: f.u32('accOff'), accBlob: f.u8('accBlob'), titleOff: f.u32('titleOff'),
          titleBlob: f.u8('titleBlob'), scale: f.header.ra_scale, denom: {} };
    for (i = 0; i < d.length; i += 3) { P.denom[d[i] * 1000000 + d[i + 1]] = d[i + 2]; }
    return meta();
  }

  /* the list is scoped to the taxon's leading body site, so n is that project's sample count at that site */
  function projects(id) {
    if (!P || id === undefined || id === null || id < 0 || id >= P.header.n_taxa) { return null; }
    if (!T) { throw new Error('sp_taxdata: load the tree before the projects (the scope comes from it)'); }
    var site = T.taxSite[id];
    var c = { i: P.index[id] }, k = readVarint(P.cells, c), out = [], j;
    for (j = 0; j < k; j++) {
      var pi = readVarint(P.cells, c), det = readVarint(P.cells, c);
      var code = P.cells[c.i] | (P.cells[c.i + 1] << 8);
      c.i += 2;
      var n = P.denom[site * 1000000 + pi] || 0;
      out.push({ accession: strAt(P.accBlob, P.accOff, pi), title: strAt(P.titleBlob, P.titleOff, pi),
                 n: n, detected: det, prevalence: n ? det / n : 0, mean: Math.pow(10, -code / P.scale),
                 site: site === 255 ? null : P.header.site_keys[site] });
    }
    return out;
  }

  /* ------------------------------------------------------------------- meta */
  function meta() {
    return { tree: T ? T.header : null, stats: S ? S.header : null, projects: P ? P.header : null };
  }

  var SPX = {
    loadTree: loadTree, loadStats: loadStats, loadProjects: loadProjects,
    taxon: taxon, stats: stats, projects: projects, search: search,
    node: node, children: children, path: path, taxaAt: taxaAt, taxonomy: taxonomy, depth: depth, root: rootIndex,
    meta: meta, slugify: slugify, bare: bare,
    nTaxa: function () { return T ? T.nTaxa : 0; },
    nNodes: function () { return T ? T.n : 0; },
    byName: function (name) {                    /* exact GTDB name -> taxon id (linear, used by the tests) */
      if (!T) { return -1; }
      if (!T.names) { buildSearch(); }
      for (var i = 0; i < T.nTaxa; i++) { if (T.names[i] === name) { return i; } }
      return -1;
    }
  };

  if (typeof module !== 'undefined' && module.exports) { module.exports = SPX; }
  root.SPX = SPX;
}(typeof window !== 'undefined' ? window : this));
