/* sp_conditions.js — Insights > Condition explorer. Plain script, no modules, no bundler, no d3: the page is a
   table and a section per condition, both built here from the three CSV files the page ships
   (data/conditions/*.csv). Uses window.SP (static/sp_core.js) for escaping and number formatting, and
   window.SP_PAGE for the paths. Nothing is fetched from another host; the ontology links are plain links to OLS.

   The main table is read at load (condition_explorer.csv); the two detail tables are fetched the first time a
   condition's section is opened, so the page costs one file until a reader asks for a condition.

   window.SP_CONDITIONS (also what tests/side_pages/conditions/dom_conditions.mjs drives):
     ready            promise, resolved when the main table is drawn
     rows()           every condition row, as read from the CSV
     shown()          the rows of the table as it stands (search + sort applied)
     state            {q, sortCol, sortDir, open}
     open(anchor)     open a condition's section, as clicking its name does
     close()          close it
     nav(hash)        follow an address ('#<anchor>'), as a reader arriving at it does
*/
(function () {
  'use strict';
  var SP = window.SP, CFG = window.SP_PAGE || {};
  var esc = SP.esc, fmtInt = SP.fmtInt;
  var $ = function (id) { return document.getElementById(id); };
  var AGE = [['cases_age_neonate', 'neonate'], ['cases_age_infant', 'infant'], ['cases_age_child', 'child'],
             ['cases_age_adolescent', 'adolescent'], ['cases_age_adult', 'adult'], ['cases_age_elderly', 'elderly'],
             ['cases_age_unknown', 'not stated']];
  var STATES = [['cases_current', 'current'], ['cases_treated_or_remission', 'treated or in remission'],
                ['cases_at_risk_or_pre', 'at risk or before onset'], ['cases_history', 'history of the condition']];
  var NUMCOL = ['projects', 'cases', 'healthy_controls', 'n_countries', 'cases_profiled'];
  var state = { q: '', sortCol: 'cases', sortDir: 'desc', open: null };
  // BY_ANCHOR / BY_ID are Maps, not objects: an address such as #__proto__ or #constructor must miss, not find
  // a member of Object.prototype.
  var ROWS = [], BY_ANCHOR = new Map(), BY_ID = new Map(), SHOWN = [], DETAIL = null, MAXCASES = 1;

  function num(v) { var x = Number(String(v === undefined || v === null ? '' : v).replace(/,/g, '')); return isNaN(x) ? 0 : x; }
  function words(s) { return SP.norm(s).split(' ').filter(Boolean); }

  // ---------------------------------------------------------------- CSV (RFC 4180: quoted fields, doubled quotes)
  function parseCSV(text) {
    var rows = [], row = [], cur = '', q = false, i, ch;
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    for (i = 0; i < text.length; i++) {
      ch = text[i];
      if (q) { if (ch === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; }
      else if (ch === '"') q = true;
      else if (ch === ',') { row.push(cur); cur = ''; }
      else if (ch === '\n') { row.push(cur); rows.push(row); row = []; cur = ''; }
      else if (ch !== '\r') cur += ch;
    }
    if (cur !== '' || row.length) { row.push(cur); rows.push(row); }
    var head = rows.shift() || [];
    return rows.filter(function (r) { return r.length > 1; }).map(function (r) {
      var o = {}; for (var k = 0; k < head.length; k++) o[head[k]] = r[k] === undefined ? '' : r[k];
      return o;
    });
  }
  function getCSV(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url);
      return r.text();
    }).then(parseCSV);
  }

  // ---------------------------------------------------------------- ids and links
  // OLS ontology of an identifier's prefix. Orphanet is served as "ordo"; a prefix OLS does not serve gets no link.
  var OLS = { mondo: 'mondo', efo: 'efo', hp: 'hp', orphanet: 'ordo', ordo: 'ordo', ncit: 'ncit', doid: 'doid' };
  function ontology(id) { return OLS[String(id).split(':')[0].toLowerCase()] || null; }
  function olsLink(id, tag) {
    if (!id) return '';
    var base = CFG.olsBase || 'https://www.ebi.ac.uk/ols4/ontologies/', o = ontology(id);
    var label = (tag ? '<span class="cn-idtag">' + esc(tag) + '</span> ' : '') + '<span class="mono">' + esc(id) + '</span>';
    if (!o) return '<span class="cn-noid" title="no page for this ontology at OLS">' + label + '</span>';
    return '<a href="' + esc(base + o + '/classes?obo_id=' + encodeURIComponent(id)) + '">' + label + '</a>';
  }
  function idsOf(r) {   // [id, label] pairs, EFO first: the condition is identified by its EFO id, else by its MONDO id
    var out = [];
    if (r.efo_id) out.push([r.efo_id, 'EFO']);
    if (r.mondo_id && r.mondo_id !== r.efo_id) out.push([r.mondo_id, 'MONDO']);
    return out;
  }
  function idLinks(r) { return idsOf(r).map(function (x) { return olsLink(x[0], x[1]); }); }
  function anchorHref(a) { return '#' + encodeURIComponent(a); }
  function taxonHref(t, rank) {
    var g = (rank === 'species' ? 's__' : 'g__') + t;
    return (CFG.taxaHref || 'taxa/index.html') + '#taxon=' + encodeURIComponent(g);
  }
  function countryTitle(r) {
    var parts = String(r.countries || '').split(';').filter(Boolean).map(function (c) { return c.replace(':', ' '); });
    if (r.cases_country_unknown && num(r.cases_country_unknown)) parts.push('not stated ' + fmtInt(num(r.cases_country_unknown)));
    return parts.join(' · ');
  }
  function caseTitle(r) {
    return 'cases: ' + STATES.map(function (s) { return s[1] + ' ' + fmtInt(num(r[s[0]])); }).join(' · ');
  }
  function ageTitle(r) {
    return AGE.map(function (b) { return b[1] + ' ' + fmtInt(num(r[b[0]])); }).join(' · ');
  }

  // ---------------------------------------------------------------- the small stacked age bar (linear scale)
  function ageBar(r) {
    var total = 0, i, v, bars = [], x = 0, W = 120, H = 12;
    for (i = 0; i < AGE.length; i++) total += num(r[AGE[i][0]]);
    if (!total) return '<span class="cn-nobar" title="no age band recorded">—</span>';
    var w = Math.max(1, W * (num(r.cases) || total) / MAXCASES);      // one linear scale over all rows
    for (i = 0; i < AGE.length; i++) {
      v = num(r[AGE[i][0]]);
      if (!v) continue;
      var seg = w * v / total;
      bars.push('<rect class="cn-b' + i + '" x="' + x.toFixed(2) + '" y="0" width="' + seg.toFixed(2) + '" height="' + H + '" data-band="' + AGE[i][1] + '" data-n="' + v + '"></rect>');
      x += seg;
    }
    return '<svg class="cn-agebar" viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" role="img" aria-label="' +
      esc(ageTitle(r)) + '"><title>' + esc(ageTitle(r)) + '</title>' + bars.join('') + '</svg>';
  }
  function ageMean(r) {   // weighted mean band index of the stated bands: youngest first when sorted ascending
    var s = 0, n = 0, i, v;
    for (i = 0; i < AGE.length - 1; i++) { v = num(r[AGE[i][0]]); s += v * i; n += v; }
    return n ? s / n : AGE.length;
  }

  // ---------------------------------------------------------------- the main table
  function replCell(r) {
    var scope = r.replication_scope || '';
    if (scope === 'this condition') {
      return '<td class="num cn-repl" data-scope="this">' + fmtInt(num(r.replication_claims_replicated)) + ' of ' +
        fmtInt(num(r.replication_claims_testable)) + '</td>';
    }
    if (scope === 'broader condition') {
      return '<td class="num cn-repl cn-repl-broader" data-scope="broader" title="tested as ' + esc(r.replication_condition_name) + '">' +
        fmtInt(num(r.replication_claims_replicated)) + ' of ' + fmtInt(num(r.replication_claims_testable)) +
        '<span class="cn-testedas">tested as ' + esc(r.replication_condition_name) + '</span></td>';
    }
    return '<td class="num cn-repl" data-scope="not tested"></td>';
  }
  function rowHtml(r) {
    var legacy = r.efo_legacy_ids || '';
    return '<tr data-anchor="' + esc(r.anchor) + '" data-id="' + esc(r.condition_id) + '" data-cases="' + num(r.cases) + '">' +
      '<td><a class="cn-name" href="' + anchorHref(r.anchor) + '">' + esc(r.condition_name) + '</a></td>' +
      '<td class="small cn-ids"' + (legacy ? ' title="retired EFO id: ' + esc(legacy.replace(/;/g, ', ')) + '"' : '') + '>' +
        idLinks(r).join(' ') + (legacy ? ' <span class="cn-legacy" title="this condition also has a retired EFO id">\u2020</span>' : '') + '</td>' +
      '<td class="num">' + fmtInt(num(r.projects)) + '</td>' +
      '<td class="num" title="' + esc(caseTitle(r)) + '">' + fmtInt(num(r.cases)) + '</td>' +
      '<td class="num" title="samples labelled healthy in the same projects">' + fmtInt(num(r.healthy_controls)) + '</td>' +
      '<td class="num" title="' + esc(countryTitle(r)) + '">' + fmtInt(num(r.n_countries)) + '</td>' +
      '<td class="cn-age">' + ageBar(r) + '</td>' +
      '<td class="num" title="case samples with a community profile">' + fmtInt(num(r.cases_profiled)) + '</td>' +
      replCell(r) + '</tr>';
  }
  function matches(r, ws) {
    if (!ws.length) return true;
    var hay = SP.norm([r.condition_name, r.condition_id, r.efo_id, r.mondo_id, r.efo_legacy_ids, r.disease_areas].join(' '));
    for (var i = 0; i < ws.length; i++) if (hay.indexOf(ws[i]) < 0) return false;
    return true;
  }
  function sortKey(r) {
    var c = state.sortCol;
    if (c === 'condition_name') return String(r.condition_name || '').toLowerCase();
    if (c === 'ids') return String(r.condition_id || '');
    if (c === 'age') return ageMean(r);
    if (c === 'replicated') {   // a result for a broader condition is not this condition's: it sorts under every tested one
      return r.replication_scope === 'this condition' ? num(r.replication_claims_replicated)
        : (r.replication_scope === 'broader condition' ? -0.5 : -1);
    }
    return num(r[c]);
  }
  function draw() {
    var ws = words(state.q);
    SHOWN = ROWS.filter(function (r) { return matches(r, ws); });
    SHOWN.sort(function (a, b) {
      var ka = sortKey(a), kb = sortKey(b), s;
      if (typeof ka === 'string' || typeof kb === 'string') s = String(ka).localeCompare(String(kb));
      else s = ka - kb;
      if (state.sortDir === 'desc') s = -s;
      if (!s) s = String(a.condition_id).localeCompare(String(b.condition_id));   // ties as the build script orders them
      return s;
    });
    $('cn-table').querySelector('tbody').innerHTML = SHOWN.map(rowHtml).join('');
    var heads = $('cn-table').querySelectorAll('thead th');
    Array.prototype.forEach.call(heads, function (th) {
      var on = th.dataset.col === state.sortCol;
      th.setAttribute('aria-sort', on ? (state.sortDir === 'asc' ? 'ascending' : 'descending') : 'none');
      th.classList.toggle('cn-sorted', on);
    });
    $('cn-count').textContent = state.q.trim()
      ? fmtInt(SHOWN.length) + ' of ' + fmtInt(ROWS.length) + ' conditions match · ' + fmtInt(SHOWN.reduce(function (a, r) { return a + num(r.cases); }, 0)) + ' case samples'
      : fmtInt(ROWS.length) + ' conditions';
  }

  // ---------------------------------------------------------------- one condition's section
  function detail() {
    if (DETAIL) return DETAIL;
    DETAIL = Promise.all([getCSV(CFG.dataDir + 'condition_explorer_projects.csv'),
                          getCSV(CFG.dataDir + 'condition_explorer_associations.csv')])
      .then(function (both) { return { projects: both[0], associations: both[1] }; });
    return DETAIL;
  }
  function relList(csvIds) {
    var out = String(csvIds || '').split(';').filter(Boolean).map(function (id) {
      var r = BY_ID.get(id);
      return r ? '<a href="' + anchorHref(r.anchor) + '">' + esc(r.condition_name) + '</a>' : '<span class="mono">' + esc(id) + '</span>';
    });
    return out.length ? out.join(' · ') : '<span class="muted">none in the catalog</span>';
  }
  function projectsHtml(rows) {
    if (!rows.length) return '<p class="small muted">No project list for this condition.</p>';
    return '<table class="tbl small cn-projects" id="cn-projects"><thead><tr><th>Project</th><th>Title</th><th class="num">Cases</th>' +
      '<th class="num">Healthy controls</th><th class="num">Profiled cases</th><th>Countries</th></tr></thead><tbody>' +
      rows.map(function (p) {
        return '<tr data-acc="' + esc(p.study_accession) + '"><td class="mono"><a href="' + esc((CFG.studyPage || '../studies/') + p.study_accession + '.html') + '">' +
          esc(p.study_accession) + '</a></td><td class="tclip">' + esc(p.project_title) + '</td><td class="num">' + fmtInt(num(p.cases)) +
          '</td><td class="num">' + fmtInt(num(p.healthy_controls)) + '</td><td class="num">' + fmtInt(num(p.cases_profiled)) +
          '</td><td class="small">' + esc(String(p.countries || '').replace(/;/g, ' ')) + '</td></tr>';
      }).join('') + '</tbody></table>';
  }
  function assocHtml(rows, r) {
    if (!rows.length) {
      return '<p class="small muted">' + ((r.replication_scope || '') === 'not tested'
        ? 'No published claim about this condition was tested here.'
        : 'No published association for this condition replicated.') + '</p>';
    }
    return '<table class="tbl small cn-assoc" id="cn-assoc"><thead><tr><th>Taxon</th><th>Rank</th><th>Direction in cases</th>' +
      '<th class="num">Published claims</th><th class="num">Papers</th><th class="num">Pooled AUC</th><th class="num">Projects tested</th><th class="num">q</th></tr></thead><tbody>' +
      rows.map(function (a) {
        return '<tr data-taxon="' + esc(a.taxon) + '"><td><a href="' + esc(taxonHref(a.taxon, a.rank)) + '"><i>' + esc(a.taxon) + '</i></a></td>' +
          '<td>' + esc(a.rank) + '</td><td>' + esc(a.direction) + '</td><td class="num">' + fmtInt(num(a.published_claims)) + '</td>' +
          '<td class="num">' + fmtInt(num(a.published_papers)) + '</td><td class="num">' + (a.pooled_auc ? Number(a.pooled_auc).toFixed(3) : '') + '</td>' +
          '<td class="num">' + fmtInt(num(a.catalog_projects_tested)) + '</td><td class="num">' + (a.q ? Number(a.q).toExponential(1) : '') + '</td></tr>';
      }).join('') + '</tbody></table>';
  }
  function countsHtml(r) {
    var li = [];
    li.push('<li><b>' + fmtInt(num(r.cases)) + '</b> case samples in <b>' + fmtInt(num(r.projects)) + '</b> projects (' +
      STATES.map(function (s) { return fmtInt(num(r[s[0]])) + ' ' + s[1]; }).join(' · ') + ')</li>');
    li.push('<li><b>' + fmtInt(num(r.case_people)) + '</b> people, counted within each project, where the project says which samples come from the same person (a person sampled in two projects counts twice)</li>');
    if (num(r.cases_incl_narrower) !== num(r.cases)) {
      li.push('<li><b>' + fmtInt(num(r.cases_incl_narrower)) + '</b> case samples in <b>' + fmtInt(num(r.projects_incl_narrower)) +
        '</b> projects including the narrower conditions below</li>');
    }
    li.push('<li><b>' + fmtInt(num(r.healthy_controls)) + '</b> healthy controls in the same projects (' + fmtInt(num(r.healthy_controls_profiled)) + ' with a community profile)</li>');
    li.push('<li><b>' + fmtInt(num(r.cases_profiled)) + '</b> case samples with a community profile, ' + fmtInt(num(r.cases_profiled_qc_clean)) + ' of them without a quality flag</li>');
    li.push('<li><b>' + fmtInt(num(r.n_countries)) + '</b> countries: ' + esc(countryTitle(r)) + '</li>');
    li.push('<li>Body sites: ' + esc(String(r.body_sites || '').split(';').filter(Boolean).map(function (b) { return b.replace(/_/g, ' ').replace(':', ' '); }).join(' · ') || 'not stated') + '</li>');
    li.push('<li>Age bands: ' + esc(ageTitle(r)) + '</li>');
    return '<ul class="cn-counts">' + li.join('') + '</ul>';
  }
  function replHtml(r) {
    var scope = r.replication_scope || '';
    if (scope === 'this condition') {
      return '<p class="small"><b>' + fmtInt(num(r.replication_claims_replicated)) + ' of ' + fmtInt(num(r.replication_claims_testable)) +
        '</b> testable published claims about this condition replicated in the catalog\u2019s own samples (<a href="' + esc(CFG.replicationHref || 'replication.html') +
        '">Replication of published findings</a>).</p>';
    }
    if (scope === 'broader condition') {
      return '<p class="small">Nothing was tested for this condition; the claims were tested as <b>' + esc(r.replication_condition_name) +
        '</b>, a broader condition, where ' + fmtInt(num(r.replication_claims_replicated)) + ' of ' + fmtInt(num(r.replication_claims_testable)) +
        ' testable claims replicated (<a href="' + esc(CFG.replicationHref || 'replication.html') + '">Replication of published findings</a>).</p>';
    }
    return '<p class="small">No published claim about this condition was tested here.</p>';
  }
  function renderSection(r, det) {
    var panel = $('cn-panel');
    var projects = det.projects.filter(function (p) { return p.condition_id === r.condition_id; })
      .sort(function (a, b) { return num(b.cases) - num(a.cases) || String(a.study_accession).localeCompare(String(b.study_accession)); });
    var assoc = det.associations.filter(function (a) { return a.condition_id === r.condition_id; });
    panel.innerHTML =
      '<button type="button" class="cn-close" id="cn-close" aria-label="close this condition">×</button>' +
      '<h2 class="cn-h" id="' + esc(r.anchor) + '">' + esc(r.condition_name) + '</h2>' +
      '<p class="small cn-idline">' + idLinks(r).join(' · ') +
        (r.efo_legacy_ids ? ' · <span class="muted">retired EFO id ' + esc(r.efo_legacy_ids.replace(/;/g, ', ')) + '</span>' : '') +
        (r.disease_areas ? ' · ' + esc(r.disease_areas.replace(/;/g, ' · ')) : '') + '</p>' +
      countsHtml(r) +
      '<p class="small cn-rel"><b>Broader:</b> ' + relList(r.broader_condition_ids) + '<br><b>Narrower:</b> ' + relList(r.narrower_condition_ids) + '</p>' +
      '<p class="cn-actions"><a class="btn sec" id="cn-samples" href="' + esc((CFG.samplesHref || '../samples/index.html') + '?condition=' + encodeURIComponent(r.anchor)) +
        '">Open these samples</a></p>' +
      replHtml(r) +
      '<h3>Projects with cases of this condition (' + fmtInt(projects.length) + ')</h3>' + projectsHtml(projects) +
      '<h3>Published associations that replicated (' + fmtInt(assoc.length) + ')</h3>' + assocHtml(assoc, r);
    panel.hidden = false;
    panel.dataset.anchor = r.anchor;
    $('cn-close').addEventListener('click', close);
    var tr = $('cn-table').querySelector('tr[data-anchor="' + (window.CSS && window.CSS.escape ? window.CSS.escape(r.anchor) : r.anchor) + '"]');
    Array.prototype.forEach.call($('cn-table').querySelectorAll('tr.cn-row-on'), function (x) { x.classList.remove('cn-row-on'); });
    if (tr) tr.classList.add('cn-row-on');
  }
  function open(anchor, push) {
    var r = BY_ANCHOR.get(anchor);
    if (!r) { close(); return Promise.resolve(null); }
    state.open = anchor;
    if (push !== false && decodeURIComponent(String(location.hash).slice(1)) !== anchor) {
      history.replaceState(null, '', location.pathname + location.search + anchorHref(anchor));
    }
    $('cn-panel').hidden = false;
    $('cn-panel').innerHTML = '<p class="spx-status">Loading ' + esc(r.condition_name) + '…</p>';
    return detail().then(function (det) { renderSection(r, det); return r; }).catch(function (e) {
      $('cn-panel').innerHTML = '<p class="sp-noscript">The detail tables could not be loaded: <a href="' + esc(CFG.dataDir) +
        'condition_explorer_projects.csv">condition_explorer_projects.csv</a>.</p>';
      return null;
    });
  }
  function close() {
    state.open = null;
    var panel = $('cn-panel');
    panel.hidden = true; panel.innerHTML = ''; delete panel.dataset.anchor;
    Array.prototype.forEach.call($('cn-table').querySelectorAll('tr.cn-row-on'), function (x) { x.classList.remove('cn-row-on'); });
    history.replaceState(null, '', location.pathname + location.search);
  }
  function fromHash() {
    var h = decodeURIComponent(String(location.hash || '').replace(/^#/, ''));
    if (!h) { close(); return Promise.resolve(null); }
    if (!BY_ANCHOR.has(h)) {
      close();
      $('cn-status').hidden = false;
      $('cn-status').textContent = 'No condition with the address ' + h + ' is in this table. Every condition of the catalog is listed below.';
      return Promise.resolve(null);
    }
    return open(h, false);
  }

  // ---------------------------------------------------------------- boot
  function wire() {
    $('cn-q').addEventListener('input', function () {
      clearTimeout($('cn-q')._t);
      $('cn-q')._t = setTimeout(function () { state.q = $('cn-q').value; draw(); if (state.open) open(state.open, false); }, 90);
    });
    $('cn-table').addEventListener('click', function (ev) {
      var th = ev.target.closest ? ev.target.closest('th[data-col]') : null;
      if (th) {
        var c = th.dataset.col;
        if (state.sortCol === c) state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
        else { state.sortCol = c; state.sortDir = (c === 'condition_name' || c === 'ids' || c === 'age') ? 'asc' : 'desc'; }
        draw();
        renderRowMark();
        return;
      }
      var a = ev.target.closest ? ev.target.closest('a.cn-name') : null;
      if (a) { ev.preventDefault(); open(a.closest('tr').dataset.anchor); }
    });
    $('cn-table').addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter' && ev.key !== ' ') return;
      var th = ev.target.closest ? ev.target.closest('th[data-col]') : null;
      if (th) { ev.preventDefault(); th.dispatchEvent(new window.MouseEvent('click', { bubbles: true })); }
    });
    $('cn-panel').addEventListener('click', function (ev) {
      var a = ev.target.closest ? ev.target.closest('a[href^="#"]') : null;
      if (!a) return;
      ev.preventDefault();
      open(decodeURIComponent(a.getAttribute('href').slice(1)));
      $('cn-panel').scrollIntoView ? $('cn-panel').scrollIntoView({ block: 'start' }) : null;
    });
    window.addEventListener('hashchange', function () { fromHash(); });
    Array.prototype.forEach.call($('cn-table').querySelectorAll('thead th[data-col]'), function (th) {
      th.tabIndex = 0; th.setAttribute('role', 'columnheader button'); th.title = 'sort by ' + th.textContent.toLowerCase();
    });
  }
  function renderRowMark() {
    var tr = state.open && $('cn-table').querySelector('tr[data-anchor="' + (window.CSS && window.CSS.escape ? window.CSS.escape(state.open) : state.open) + '"]');
    Array.prototype.forEach.call($('cn-table').querySelectorAll('tr.cn-row-on'), function (x) { x.classList.remove('cn-row-on'); });
    if (tr) tr.classList.add('cn-row-on');
  }

  var ready = getCSV((CFG.dataDir || '../data/conditions/') + 'condition_explorer.csv').then(function (rows) {
    ROWS = rows;
    ROWS.forEach(function (r) { BY_ANCHOR.set(r.anchor, r); BY_ID.set(r.condition_id, r); });
    MAXCASES = Math.max.apply(null, ROWS.map(function (r) { return num(r.cases); }).concat([1]));
    wire();
    draw();
    $('cn-status').hidden = true;
    return fromHash();
  }).catch(function (e) {
    $('cn-status').hidden = false;
    $('cn-status').innerHTML = 'The table of conditions could not be loaded. You can download it instead: <a href="' +
      esc(CFG.dataDir || '../data/conditions/') + 'condition_explorer.csv">condition_explorer.csv</a>.';
    if (window.console && console.warn) console.warn(e);
  });

  window.SP_CONDITIONS = {
    ready: ready,
    rows: function () { return ROWS; },
    shown: function () { return SHOWN; },
    detail: detail,
    state: state,
    open: open,
    close: close,
    nav: function (hash) {
      history.replaceState(null, '', location.pathname + location.search + (hash && hash[0] === '#' ? hash : '#' + hash));
      return fromHash();
    }
  };
})();
