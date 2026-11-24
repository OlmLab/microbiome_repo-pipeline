/* sp_core.js — shared front end for the side pages (taxon pages, VANISH/BloSSUM).
   Plain script, no modules, no bundler. Uses the globals the site already loads: window.d3 (d3 7.9) and
   window.topojson (topojson-client 3.1), both vendored and SRI-pinned by the page template. Loads nothing
   from another host. Every chart is drawn in the browser; no chart image is shipped.

   window.SP:
     esc(s)                        HTML-escape a string (every value from a data file goes through it)
     fmtInt(n) / fmtPct(x, dp)     "12,345" / "8.4 %"  (x is a share in 0..1; "<0.1 %" for small non-zero)
     getJSON(url)                  fetch + parse, memoised per URL (one request per file per page)
     tip()                         the page's single tooltip: .show(html, event), .move(event), .hide()
     segmented(el, spec)           segmented button group; spec {options:[{value,label,title}], value,
                                   onChange(value), label}. Returns {set(v), value()}. Arrow keys move.
     searchBox(spec)               {input, results, items (array or a function returning one), onPick(item),
                                   limit, empty} with keyboard
                                   support (ArrowUp/Down, Enter, Escape). item {name, sub, url, keys}.
     barChart(el, spec)            horizontal bars on a linear axis. spec {rows, label, max, colour,
                                   valueFormat(row), axisFormat(v), onHover(row)->html, onClick(row)}.
                                   row {key, label, value, ...}. Returns {update(rows, spec)}.
     globe(el, spec)               orthographic globe: drag to spin, wheel or the +/−/reset buttons to zoom,
                                   hover for numbers, click a country. spec {values:{iso:v}, names:{iso:name},
                                   numToIso, world, unit, format(v,iso), onClick(iso)}. Returns {update(values)}.
     go(url)                       the one place a chart navigates (a test replaces it to record the URL)
     scatter(el, spec)             zoomable, pannable scatter. spec {points, width, height, x(d), y(d),
                                   xDomain, yDomain, yScale ('linear'|'sqrt'), xLabel, yLabel, xFormat,
                                   yFormat, r (number or function), xEnds [left, right], xReverse, colour(d),
                                   classOf(d),
                                   tipHtml(d), onClick(d, ev), maxZoom}. Returns {update, focus(d),
                                   mark(pred), star(d), reset, api()}; api() has zoomTo(x, y, k) and the marks.
                                   star(d) marks one point with a star and its name without moving the view.
     icicle(el, spec)              zoomable icicle of a hierarchy. spec {tree, breadcrumb, onLeaf(node),
                                   tipHtml(node), height}. Click an internal node to zoom, a leaf to open it.
   Every chart helper accepts an explicit width, because an element has no layout in jsdom: that is how the
   tests draw the real charts without a browser. */
(function () {
  'use strict';
  var d3 = window.d3;

  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmtInt(n) {
    if (n === null || n === undefined || isNaN(n)) return '–';
    return Math.round(n).toLocaleString('en-US');
  }
  function fmtPct(x, dp) {
    if (x === null || x === undefined || isNaN(x)) return '–';
    dp = dp === undefined ? 1 : dp;
    var v = x * 100, floor = Math.pow(10, -dp);
    if (v > 0 && v < floor) return '<' + floor.toFixed(dp) + ' %';   // only when the asked precision cannot show it
    return v.toFixed(dp) + ' %';
  }

  var cache = {};
  function getJSON(url) {
    if (!cache[url]) {
      cache[url] = fetch(url).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url);
        return r.json();
      });
    }
    return cache[url];
  }

  var _tip = null;
  function tip() {
    if (!_tip) {
      var node = document.createElement('div');
      node.className = 'sp-tip';
      node.id = 'sp-tip';
      node.setAttribute('role', 'status');
      node.style.display = 'none';
      document.body.appendChild(node);
      _tip = {
        node: node,
        show: function (html, ev) { node.innerHTML = html; node.style.display = 'block'; _tip.move(ev); },
        move: function (ev) {
          if (!ev) return;
          var x = (ev.clientX || 0) + 14, y = (ev.clientY || 0) + 14;
          var w = node.offsetWidth || 240, h = node.offsetHeight || 80;
          var vw = window.innerWidth || 1200, vh = window.innerHeight || 800;
          if (x + w > vw - 8) x = Math.max(8, (ev.clientX || 0) - w - 14);
          if (y + h > vh - 8) y = Math.max(8, (ev.clientY || 0) - h - 14);
          node.style.left = x + 'px'; node.style.top = y + 'px';
        },
        hide: function () { node.style.display = 'none'; }
      };
    }
    return _tip;
  }

  // ---------------------------------------------------------------- segmented buttons
  function segmented(el, spec) {
    var value = spec.value !== undefined ? spec.value : (spec.options[0] || {}).value;
    el.classList.add('sp-seg');
    el.setAttribute('role', 'tablist');
    if (spec.label) el.setAttribute('aria-label', spec.label);
    el.innerHTML = '';
    var btns = spec.options.map(function (o) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'sp-seg-btn';
      b.textContent = o.label;
      b.setAttribute('role', 'tab');
      b.dataset.value = o.value;
      if (o.title) b.title = o.title;
      b.addEventListener('click', function () { set(o.value, true); });
      el.appendChild(b);
      return b;
    });
    function paint() {
      btns.forEach(function (b) {
        var on = b.dataset.value === String(value);
        b.classList.toggle('active', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
        b.tabIndex = on ? 0 : -1;
      });
    }
    function set(v, fire) {
      if (String(v) === String(value)) { paint(); return; }
      value = v; paint();
      if (fire !== false && spec.onChange) spec.onChange(v);
    }
    el.addEventListener('keydown', function (ev) {
      var i = btns.findIndex(function (b) { return b.dataset.value === String(value); });
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') {
        ev.preventDefault(); var n = btns[(i + 1) % btns.length]; n.focus(); set(n.dataset.value, true);
      }
      if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') {
        ev.preventDefault(); var p = btns[(i - 1 + btns.length) % btns.length]; p.focus(); set(p.dataset.value, true);
      }
    });
    paint();
    return { set: function (v) { set(v, true); }, value: function () { return value; } };
  }

  // ---------------------------------------------------------------- search box
  function norm(s) {
    return String(s === null || s === undefined ? '' : s).normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();
  }
  function searchBox(spec) {
    var input = spec.input, results = spec.results, limit = spec.limit || 12;
    function build(list) {
      return (list || []).map(function (it) {
        return { it: it, k: norm((it.keys || [it.name, it.sub]).join(' ')) };
      });
    }
    // spec.items may be an array (fixed list) or a function returning one (a list that changes with a toggle)
    var itemsOf = (typeof spec.items === 'function') ? spec.items : null;
    var items = itemsOf ? [] : build(spec.items);
    var hits = [], cur = -1;
    function paint() {
      if (!hits.length) {
        results.innerHTML = input.value.trim().length >= 2
          ? '<li class="sp-hit-none">' + esc(spec.empty || 'No taxon matches.') + '</li>' : '';
        return;
      }
      results.innerHTML = hits.map(function (h, i) {
        return '<li class="sp-hit' + (i === cur ? ' active' : '') + '" role="option" data-i="' + i +
          '" aria-selected="' + (i === cur ? 'true' : 'false') + '"><a href="' + esc(h.it.url) + '">' +
          (h.it.html || esc(h.it.name)) + '</a> <span class="sp-hit-sub">' + esc(h.it.sub || '') + '</span></li>';
      }).join('');
    }
    function run() {
      if (itemsOf) items = build(itemsOf());
      var words = norm(input.value).split(' ').filter(Boolean);
      hits = []; 
      if (!words.length || input.value.trim().length < 2) { cur = -1; paint(); return; }
      var first = [], other = [];
      for (var i = 0; i < items.length; i++) {
        var k = items[i].k, all = true;
        for (var w = 0; w < words.length; w++) { if (k.indexOf(words[w]) < 0) { all = false; break; } }
        if (!all) continue;
        (k.indexOf(words[0]) === 0 ? first : other).push(items[i].it);
        if (first.length >= limit) break;
      }
      hits = first.concat(other).slice(0, limit).map(function (it) { return { it: it }; });
      cur = hits.length ? 0 : -1;
      paint();
    }
    input.addEventListener('input', function () { clearTimeout(input._t); input._t = setTimeout(run, 90); });
    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowDown') { ev.preventDefault(); cur = Math.min(cur + 1, hits.length - 1); paint(); }
      else if (ev.key === 'ArrowUp') { ev.preventDefault(); cur = Math.max(cur - 1, 0); paint(); }
      else if (ev.key === 'Escape') { input.value = ''; hits = []; cur = -1; paint(); }
      else if (ev.key === 'Enter') {
        if (!hits.length) run();
        if (hits.length) { ev.preventDefault(); pick(hits[Math.max(0, cur)].it); }
      }
    });
    results.addEventListener('click', function (ev) {
      var li = ev.target.closest ? ev.target.closest('li.sp-hit') : null;
      if (!li) return;
      ev.preventDefault();
      pick(hits[+li.dataset.i].it);
    });
    function pick(it) { if (spec.onPick) spec.onPick(it); else if (it.url) window.SP.go(it.url); }
    return { run: run, hits: function () { return hits.map(function (h) { return h.it; }); } };
  }

  // ---------------------------------------------------------------- horizontal bar chart
  function barChart(el, spec) {
    var state = {};
    function draw(rows, s) {
      state.rows = rows; state.spec = s = s || state.spec || {};
      var width = s.width || el.clientWidth || 760;
      // room for the longest row label (11 px text, about 6.1 px per character), at most half the chart
      var longest = d3.max(rows, function (r) { return Math.min(34, String(r.label || '').length); }) || 0;
      var labelW = s.labelW || Math.min(Math.max(250, Math.round(width * 0.5)),
        Math.max(130, Math.round(width * 0.31), Math.round(longest * 6.1) + 16));
      if (labelW > width * 0.5) labelW = Math.round(width * 0.5);
      var rowH = s.rowH || 24, padT = 30, padB = 32, right = 58;
      var height = padT + padB + rowH * Math.max(rows.length, 1);
      var max = s.max !== undefined ? s.max : d3.max(rows, function (r) { return r.value; }) || 0;
      if (!(max > 0)) max = 1;
      el.innerHTML = '';
      var svg = d3.select(el).append('svg').attr('class', 'sp-chart')
        .attr('viewBox', '0 0 ' + width + ' ' + height).attr('width', '100%').attr('height', height)
        .attr('role', 'img').attr('aria-label', s.alt || s.label || 'bar chart');
      var x = d3.scaleLinear().domain([0, max]).range([labelW, width - right]).nice();
      svg.append('g').attr('class', 'sp-axis').attr('transform', 'translate(0,' + (padT - 8) + ')')
        .call(d3.axisTop(x).ticks(Math.max(2, Math.round((width - labelW - right) / 95)))
          .tickFormat(function (v) { return s.axisFormat ? s.axisFormat(v) : v; }));
      svg.append('text').attr('class', 'sp-axis-title').attr('x', labelW).attr('y', height - 9).text(s.label || '');
      var g = svg.append('g').attr('class', 'sp-bars').selectAll('g').data(rows).join('g')
        .attr('class', s.onClick ? 'sp-barg sp-clickable' : 'sp-barg')
        .attr('data-key', function (d) { return d.key; })
        .attr('transform', function (d, i) { return 'translate(0,' + (padT + i * rowH) + ')'; });
      g.append('text').attr('class', 'sp-bar-label').attr('x', labelW - 9).attr('y', rowH / 2 + 3)
        .attr('text-anchor', 'end')
        .text(function (d) { return d.label.length > 34 ? d.label.slice(0, 33) + '…' : d.label; });
      g.append('rect').attr('class', 'sp-bar-bg').attr('x', labelW).attr('y', 3)
        .attr('width', Math.max(1, width - right - labelW)).attr('height', rowH - 8);
      g.append('rect').attr('class', 'sp-bar-v').attr('x', labelW).attr('y', 3)
        .attr('width', function (d) { return Math.max(0, x(d.value) - labelW); })
        .attr('height', rowH - 8).attr('fill', s.colour || '#1f5aa6');
      g.append('text').attr('class', 'sp-bar-val').attr('x', function (d) { return x(d.value) + 6; })
        .attr('y', rowH / 2 + 3).text(function (d) { return s.valueFormat ? s.valueFormat(d) : d.value; });
      g.append('title').text(function (d) { return d.label; });
      var t = tip();
      g.on('mousemove', function (ev, d) { t.show(s.onHover ? s.onHover(d) : esc(d.label), ev); })
        .on('mouseleave', function () { t.hide(); })
        .on('click', function (ev, d) { if (s.onClick) s.onClick(d); });
      return svg;
    }
    draw(spec.rows || [], spec);
    return { update: function (rows, s) { draw(rows, s || state.spec); } };
  }

  // ---------------------------------------------------------------- globe
  function globe(el, spec) {
    var size = spec.size || 340, R0 = size / 2 - 4;
    var values = spec.values || {}, names = spec.names || {}, numToIso = spec.numToIso || {};
    var feats = window.topojson.feature(spec.world, spec.world.objects.countries).features
      .filter(function (f) { return String(f.id) !== '010'; });
    el.innerHTML = '';
    el.classList.add('sp-globe-wrap');
    var svg = d3.select(el).append('svg').attr('class', 'globe sp-globe')
      .attr('viewBox', '0 0 ' + size + ' ' + size).attr('width', size).attr('height', size)
      .attr('role', 'img').attr('aria-label', spec.alt || 'globe');
    var k = 1, rot = (spec.rotate || [-10, -20]).slice();
    if (!spec.rotate && spec.autoCentre !== false) {        // start on the weighted centre of the coloured countries
      var cx = 0, cy = 0, cw = 0;
      feats.forEach(function (f) {
        var v = (spec.values || {})[numToIso[f.id]];
        if (v) { var c = d3.geoCentroid(f); cx += c[0] * v; cy += c[1] * v; cw += v; }
      });
      if (cw) rot = [-cx / cw, -Math.max(-55, Math.min(55, cy / cw))];
    }
    var rot0 = rot.slice();
    var max = 1e-9;
    function remax() {
      max = 1e-9;
      Object.keys(values).forEach(function (i) { if (values[i] > max) max = values[i]; });
    }
    remax();
    var ramp = function (v) { return d3.interpolateRgb('#efe4c4', '#8a6d2f')(Math.sqrt(v / max)); };
    var proj = d3.geoOrthographic().scale(R0).translate([size / 2, size / 2]).clipAngle(90).rotate(rot);
    var path = d3.geoPath(proj);
    var gS = svg.append('path').datum({ type: 'Sphere' }).attr('class', 'g-sphere');
    var gG = svg.append('path').datum(d3.geoGraticule10()).attr('class', 'g-grat');
    var t = tip();
    var gC = svg.append('g').attr('class', 'sp-countries').selectAll('path').data(feats).join('path')
      .attr('class', 'g-land')
      .style('fill', function (f) { var v = values[numToIso[f.id]]; return v === undefined ? null : ramp(v); })
      .attr('data-iso', function (f) { return numToIso[f.id] || ''; })
      .attr('data-value', function (f) { var v = values[numToIso[f.id]]; return v === undefined ? '' : v; })
      .style('cursor', function (f) { return spec.onClick && values[numToIso[f.id]] !== undefined ? 'pointer' : null; })
      .on('mousemove', function (ev, f) {
        var iso = numToIso[f.id], v = values[iso];
        if (v === undefined) { t.hide(); return; }
        t.show('<b>' + esc(names[iso] || iso) + '</b><br>' + (spec.format ? spec.format(v, iso) : fmtPct(v)), ev);
      })
      .on('mouseleave', function () { t.hide(); })
      .on('click', function (ev, f) {
        var iso = numToIso[f.id];
        if (spec.onClick && iso && values[iso] !== undefined) spec.onClick(iso, names[iso] || iso);
      });
    function draw() {
      proj.rotate(rot).scale(R0 * k);
      gS.attr('d', path); gG.attr('d', path); gC.attr('d', path);
    }
    svg.call(d3.drag().on('drag', function (ev) {
      rot = [rot[0] + ev.dx * 0.5 / k, Math.max(-90, Math.min(90, rot[1] - ev.dy * 0.5 / k))];
      draw();
    }));
    function zoom(f) { k = Math.max(1, Math.min(6, k * f)); draw(); return k; }
    svg.on('wheel', function (ev) { ev.preventDefault(); zoom(ev.deltaY < 0 ? 1.15 : 1 / 1.15); });
    var ctrl = document.createElement('div');
    ctrl.className = 'sp-globe-ctrl';
    [['+', 'Zoom in', function () { zoom(1.4); }], ['−', 'Zoom out', function () { zoom(1 / 1.4); }],
     ['⟳', 'Reset the view', function () { k = 1; rot = rot0.slice(); draw(); }]]
      .forEach(function (c) {
        var b = document.createElement('button');
        b.type = 'button'; b.className = 'sp-globe-btn'; b.textContent = c[0];
        b.title = c[1]; b.setAttribute('aria-label', c[1]);
        b.addEventListener('click', c[2]);
        ctrl.appendChild(b);
      });
    el.appendChild(ctrl);
    draw();
    return {
      update: function (v) {
        values = v || {}; remax();
        gC.style('fill', function (f) { var x = values[numToIso[f.id]]; return x === undefined ? null : ramp(x); })
          .attr('data-value', function (f) { var x = values[numToIso[f.id]]; return x === undefined ? '' : x; })
          .style('cursor', function (f) { return spec.onClick && values[numToIso[f.id]] !== undefined ? 'pointer' : null; });
      },
      zoom: zoom,
      k: function () { return k; },
      svg: svg
    };
  }

  // ---------------------------------------------------------------- zoomable icicle
  function icicle(el, spec) {
    var width = spec.width || el.clientWidth || 960, height = spec.height || 560;
    var root = d3.hierarchy(spec.tree)
      .sum(function (d) { return d.children && d.children.length ? 0 : 1; })
      .sort(function (a, b) { return b.value - a.value || String(a.data.name).localeCompare(String(b.data.name)); });
    var cols = root.height + 1;
    d3.partition().size([height, cols * (width / cols)])(root);
    var focus = root;
    el.innerHTML = '';
    var svg = d3.select(el).append('svg').attr('class', 'sp-icicle')
      .attr('viewBox', '0 0 ' + width + ' ' + height).attr('width', '100%').attr('height', height)
      .attr('role', 'img').attr('aria-label', spec.alt || 'taxonomy of the taxa with pages');
    var colour = d3.scaleOrdinal(['#1f5aa6', '#8a6d2f', '#4b8a3f', '#7a5195', '#bc5090', '#ef8a3c',
                                  '#2e8b8b', '#a0522d', '#5b7f9e', '#6b6b6b']);
    function clade(d) { var n = d; while (n.depth > 1 && n.parent) n = n.parent; return n.data.name; }
    var t = tip();
    var cell = svg.append('g').attr('class', 'sp-ice-cells').selectAll('g')
      .data(root.descendants().slice(1)).join('g')
      .attr('class', function (d) { return 'sp-ice-cell' + (d.data.slug ? ' sp-ice-leaf' : ''); })
      .attr('data-name', function (d) { return d.data.name; })
      .attr('data-slug', function (d) { return d.data.slug || ''; })
      .attr('data-rank', function (d) { return d.data.rank || ''; })
      .attr('transform', function (d) { return 'translate(' + d.y0 + ',' + d.x0 + ')'; })
      .style('cursor', 'pointer')
      .on('mousemove', function (ev, d) { t.show(spec.tipHtml ? spec.tipHtml(d) : esc(d.data.name), ev); })
      .on('mouseleave', function () { t.hide(); })
      .on('click', function (ev, d) {
        if (ev.stopPropagation) ev.stopPropagation();
        if (d.data.slug) { if (spec.onLeaf) spec.onLeaf(d); return; }
        zoomTo(focus === d && d.parent ? d.parent : d);
      });
    cell.append('rect').attr('class', 'sp-ice-rect')
      .attr('width', function (d) { return Math.max(0, d.y1 - d.y0 - 1); })
      .attr('height', function (d) { return Math.max(0, d.x1 - d.x0 - 1); })
      .attr('fill', function (d) { return d.data.slug ? '#cfb87c' : colour(clade(d)); })
      .attr('fill-opacity', function (d) { return 0.4 + 0.45 * (1 - d.depth / cols); });
    cell.append('text').attr('class', 'sp-ice-text').attr('x', 6);
    cell.append('title').text(function (d) { return d.data.name; });
    // a label is drawn only when its block is tall enough for the type, centred in the block, and cut to its width
    function vis(h, w) { return (h >= 15 && w > 46) ? 1 : 0; }
    function paintText(box) {
      cell.select('text')
        .attr('y', function (d) { var b = box(d); return (b.x1 - b.x0) / 2 + 4; })
        .attr('fill-opacity', function (d) { var b = box(d); return vis(b.x1 - b.x0, b.y1 - b.y0); })
        .text(function (d) {
          var b = box(d), room = Math.floor((b.y1 - b.y0 - 12) / 6.1), s = d.data.label || d.data.name;
          return s.length > room ? (room > 2 ? s.slice(0, room - 1) + '…' : '') : s;
        });
    }
    paintText(function (d) { return d; });
    svg.on('click', function () { if (focus !== root) zoomTo(root); });
    function zoomTo(p) {
      focus = p;
      root.each(function (d) {
        d.target = {
          x0: (d.x0 - p.x0) / (p.x1 - p.x0) * height,
          x1: (d.x1 - p.x0) / (p.x1 - p.x0) * height,
          y0: d.y0 - p.y0,
          y1: d.y1 - p.y0
        };
      });
      var dur = spec.duration === undefined ? 450 : spec.duration;
      var sel = dur > 0 ? cell.transition().duration(dur) : cell;
      sel.attr('transform', function (d) { return 'translate(' + d.target.y0 + ',' + d.target.x0 + ')'; });
      (dur > 0 ? cell.select('rect').transition().duration(dur) : cell.select('rect'))
        .attr('height', function (d) { return Math.max(0, d.target.x1 - d.target.x0 - 1); })
        .attr('width', function (d) { return Math.max(0, d.target.y1 - d.target.y0 - 1); });
      // the attribute end state is also set directly, so a headless run (no animation frames) is correct
      cell.attr('transform', function (d) { return 'translate(' + d.target.y0 + ',' + d.target.x0 + ')'; });
      cell.select('rect')
        .attr('height', function (d) { return Math.max(0, d.target.x1 - d.target.x0 - 1); })
        .attr('width', function (d) { return Math.max(0, d.target.y1 - d.target.y0 - 1); });
      paintText(function (d) { return d.target; });
      root.each(function (d) { d.x0 = d.target.x0; d.x1 = d.target.x1; d.y0 = d.target.y0; d.y1 = d.target.y1; });
      if (spec.breadcrumb) crumbs();
      if (spec.onZoom) spec.onZoom(focus);
    }
    function crumbs() {
      var chain = focus.ancestors().reverse();
      spec.breadcrumb.innerHTML = chain.map(function (d, i) {
        return '<button type="button" class="sp-crumb" data-depth="' + i + '">' +
          esc(d.data.label || d.data.name) + '</button>';
      }).join('<span class="sp-crumb-sep">›</span>');
      Array.prototype.forEach.call(spec.breadcrumb.querySelectorAll('.sp-crumb'), function (b) {
        b.addEventListener('click', function () { zoomTo(chain[+b.dataset.depth]); });
      });
    }
    if (spec.breadcrumb) crumbs();
    return { zoomTo: zoomTo, focus: function () { return focus; }, root: root, svg: svg };
  }


  // ---------------------------------------------------------------- zoomable scatter
  function scatter(el, spec) {
    var state = {};
    function draw(points, s) {
      state.points = points = points || state.points || [];
      state.spec = s = s || state.spec || {};
      var width = s.width || el.clientWidth || 860, height = s.height || 460;
      var padL = 58, padR = 16, padT = 14, padB = 46;
      var xv = function (d) { return s.x ? s.x(d) : d.x; };
      var yv = function (d) { return s.y ? s.y(d) : d.y; };
      var xd = s.xDomain || d3.extent(points, xv);
      var yd = s.yDomain || [0, d3.max(points, yv) || 1];
      if (xd[0] === xd[1]) xd = [xd[0] - 1, xd[1] + 1];
      var x = d3.scaleLinear().domain(xd).nice()
        .range(s.xReverse ? [width - padR, padL] : [padL, width - padR]);   // xReverse: high values on the left
      var y = (s.yScale === 'sqrt' ? d3.scaleSqrt() : d3.scaleLinear()).domain(yd).nice().range([height - padB, padT]);
      el.innerHTML = '';
      var svg = d3.select(el).append('svg').attr('class', 'sp-chart sp-scatter')
        .attr('viewBox', '0 0 ' + width + ' ' + height).attr('width', '100%').attr('height', height)
        .attr('role', 'img').attr('aria-label', s.alt || 'scatter plot');
      var cid = 'sp-clip-' + (Math.random().toString(36).slice(2, 8));
      svg.append('defs').append('clipPath').attr('id', cid).append('rect')
        .attr('x', padL - 6).attr('y', padT - 6).attr('width', width - padL - padR + 12).attr('height', height - padT - padB + 12);   // 6 px slack so a point on the edge of the domain is not cut in half
      var gx = svg.append('g').attr('class', 'sp-axis sp-axis-x').attr('transform', 'translate(0,' + (height - padB) + ')');
      var gy = svg.append('g').attr('class', 'sp-axis sp-axis-y').attr('transform', 'translate(' + padL + ',0)');
      svg.append('text').attr('class', 'sp-axis-title').attr('x', (padL + width - padR) / 2)
        .attr('y', height - 8).attr('text-anchor', 'middle').text(s.xLabel || '');
      if (s.xEnds) {          // plain words at the two ends, instead of a formula
        svg.append('text').attr('class', 'sp-axis-end').attr('x', padL).attr('y', height - 8)
          .attr('text-anchor', 'start').text(s.xEnds[0]);
        svg.append('text').attr('class', 'sp-axis-end').attr('x', width - padR).attr('y', height - 8)
          .attr('text-anchor', 'end').text(s.xEnds[1]);
      }
      svg.append('text').attr('class', 'sp-axis-title').attr('transform', 'rotate(-90)')
        .attr('x', -(padT + height - padB) / 2).attr('y', 14).attr('text-anchor', 'middle').text(s.yLabel || '');
      var plot = svg.append('g').attr('clip-path', 'url(#' + cid + ')');
      var zero = plot.append('line').attr('class', 'sp-sc-zero').attr('y1', padT).attr('y2', height - padB);
      var pts = plot.append('g').attr('class', 'sp-pts');
      var marks = pts.selectAll('circle').data(points, function (d) { return d.key || d.n; }).join('circle')
        .attr('class', function (d) { return 'sp-pt sp-pt-' + (s.classOf ? s.classOf(d) : 'N'); })
        .attr('data-name', function (d) { return d.n; })
        .attr('r', function (d) { return (typeof s.r === 'function' ? s.r(d) : s.r) || 3.2; });
      if (s.colour) marks.attr('fill', function (d) { return s.colour(d); });   // explicit, like barChart
      // optional outline, e.g. "this point is on a published list". An inline style, because .sp-pt in the
      // stylesheet sets a white stroke and a stylesheet rule beats a presentation attribute.
      if (s.stroke) marks.each(function (d) {
        var col = s.stroke(d);
        this.style.stroke = col || '';
        this.style.strokeWidth = col ? String(s.strokeWidth || 1.8) : '';
      });
      var t = tip();
      marks.on('mousemove', function (ev, d) { t.show(s.tipHtml ? s.tipHtml(d) : esc(d.n), ev); })
        .on('mouseleave', function () { t.hide(); })
        .on('click', function (ev, d) { if (s.onClick) s.onClick(d, ev); });
      var zx = x, zy = y;
      // one point can be marked with a star and its name, e.g. the taxon found by the search box; the view
      // is left as it is, so the point keeps its context
      var starG = plot.append('g').attr('class', 'sp-star-layer'), starred = null;
      function placeStar() {
        starG.selectAll('*').remove();
        if (!starred) return;
        var cx = zx(xv(starred)), cy = zy(yv(starred)), d0 = starred;
        var shape = d3.symbol().type(d3.symbolStar).size(s.starSize || 420)();
        starG.append('path').attr('class', 'sp-star-halo').attr('d', shape)     // white edge, so it stands out
          .attr('transform', 'translate(' + cx + ',' + cy + ')')
          .attr('fill', 'none').attr('stroke', '#fff').attr('stroke-width', 5);
        starG.append('path').attr('class', 'sp-star').attr('data-name', d0.n)
          .attr('d', shape)
          .attr('transform', 'translate(' + cx + ',' + cy + ')')
          .attr('fill', s.colour ? s.colour(d0) : '#CFB87C')
          .on('mousemove', function (ev) { t.show(s.tipHtml ? s.tipHtml(d0) : esc(d0.n), ev); })
          .on('mouseleave', function () { t.hide(); })
          .on('click', function (ev) { if (s.onClick) s.onClick(d0, ev); });
        var left = cx > (padL + width - padR) / 2;
        starG.append('text').attr('class', 'sp-star-label').attr('x', cx + (left ? -17 : 17)).attr('y', cy + 4)
          .attr('text-anchor', left ? 'end' : 'start')
          .text(s.starLabel ? s.starLabel(d0) : String(d0.n));
      }
      function place() {
        marks.attr('cx', function (d) { return zx(xv(d)); }).attr('cy', function (d) { return zy(yv(d)); });
        placeStar();
        zero.attr('x1', zx(0)).attr('x2', zx(0)).attr('display', (zx(0) >= padL && zx(0) <= width - padR) ? null : 'none');
        gx.call(d3.axisBottom(zx).ticks(Math.max(2, Math.round((width - padL - padR) / 90)))
          .tickFormat(s.xFormat || null));
        gy.call(d3.axisLeft(zy).ticks(Math.max(2, Math.round((height - padT - padB) / 60)))
          .tickFormat(s.yFormat || null));
      }
      var zoom = d3.zoom().scaleExtent([1, s.maxZoom || 40])
        .translateExtent([[0, 0], [width, height]]).extent([[0, 0], [width, height]])
        .on('zoom', function (ev) { zx = ev.transform.rescaleX(x); zy = ev.transform.rescaleY(y); place(); });
      svg.call(zoom);
      place();
      state.api = {
        svg: svg, marks: marks, width: width, height: height,
        zoomTo: function (cx, cy, k) {
          k = k || 6;
          var tr = d3.zoomIdentity.translate(width / 2 - k * x(cx), height / 2 - k * y(cy)).scale(k);
          svg.call(zoom.transform, tr);
          return tr;
        },
        reset: function () { svg.call(zoom.transform, d3.zoomIdentity); },
        star: function (d) { starred = d || null; placeStar(); return starred; },
        mark: function (pred) {
          marks.classed('on', function (d) { return !!(pred && pred(d)); })
            .classed('dim', function (d) { return !!pred && !pred(d); });
        },
        focus: function (d, k) {
          if (!d) return null;
          state.api.mark(function (o) { return o === d || o.n === d.n; });
          return state.api.zoomTo(xv(d), yv(d), k || 6);
        }
      };
      return state.api;
    }
    draw(spec.points || [], spec);
    return {
      update: function (points, s) { return draw(points, s || state.spec); },
      api: function () { return state.api; },
      focus: function (d, k) { return state.api.focus(d, k); },
      mark: function (p) { return state.api.mark(p); },
      star: function (d) { return state.api.star(d); },
      reset: function () { return state.api.reset(); }
    };
  }

  function go(url) { window.location.href = url; }

  window.SP = {
    esc: esc, go: go, fmtInt: fmtInt, fmtPct: fmtPct, getJSON: getJSON, tip: tip, norm: norm,
    segmented: segmented, searchBox: searchBox, barChart: barChart, globe: globe, icicle: icicle,
    scatter: scatter
  };
})();
