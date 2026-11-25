/* sp_replication_x.js — the EXPLORATORY add-on of Atlas > Replication of published findings.
 *
 * Three checks were run after the registered test (no correction for multiple tests; an abundance floor; claims
 * whose condition has only one or two projects). They are not part of the plan and change no label. This file
 * keeps them apart from the registered page: it reads its own data file, data/replication/replication_exploratory.json,
 * fills the collapsed section #rp-explore, and adds one paragraph marked "Exploratory" to an open claim panel.
 * static/sp_replication.js is not changed and does not know this file exists; remove the script tag and the
 * section from the template and the page is the registered page again. If the exploratory file is missing or was
 * built for another replication.json, nothing is added.
 */
(function () {
  'use strict';
  var P = window.SP_PAGE || {}, SP = window.SP, R = window.SP_REPLICATION, esc = SP && SP.esc;
  var body = document.getElementById('rpx-body'), panel = document.getElementById('rp-panel');
  if (!SP || !R) return;
  var X = null, fewAt = Object.create(null);
  var FLOOR_WORD = ['replicated', 'not replicated', 'not testable'];

  function n(x) { return SP.fmtInt(x); }
  function pct(x, dp) { return (100 * x).toFixed(dp === undefined ? 1 : dp) + ' %'; }
  function share(k, m) { return n(k) + ' of ' + n(m) + ' (' + pct(k / m) + ')'; }
  function fp(p) {                     // a p-value as the data file rounded it (never across the 0.05 line)
    if (p === null || p === undefined) return 'p not available';
    if (p < 0.0001) return 'p < 0.0001';
    return 'p = ' + String(p);
  }
  function floorLabel(f) { return String(+(100 * f).toPrecision(3)) + ' %'; }
  function table(head, rows, numFrom) {
    return '<table class="small rpx-table"><thead><tr>' + head.map(function (h, i) {
      return '<th' + (i >= numFrom ? ' class="num"' : '') + '>' + esc(h) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (r) { return '<tr>' + r.map(function (c, i) {
        return '<td' + (i >= numFrom ? ' class="num"' : '') + '>' + esc(String(c)) + '</td>'; }).join('') + '</tr>'; }).join('') +
      '</tbody></table>';
  }

  function drawSection() {
    if (!body) return;
    var u = X.uncorrected, f = X.fewer, h = '';
    h += '<h3 class="rpx-h">1. Without the correction for multiple tests</h3>';
    h += '<p class="small" id="rpx-unc">The headline corrects for testing ' + n(u.testable) + ' claims at once. Without that correction, <b>' +
      share(u.n, u.testable) + '</b> have p &lt; 0.05 in the claimed direction, against ' + share(u.registered, u.testable) +
      ' with it' + (u.contains_registered ? ' (the same claims and ' + n(u.n - u.registered) + ' more)' : '') + '; ' + share(u.opposite, u.testable) +
      ' have p &lt; 0.05 in the opposite direction. With the case and control labels shuffled inside every project, ' +
      n(u.permutations) + ' reruns gave ' + pct(u.chance.mean) + ' on average (' + pct(u.chance.lo) + ' to ' + pct(u.chance.hi) +
      ' in 95 % of them). In ' + share(u.majority.n, u.testable) + ' claims more than half of the projects point in the claimed direction (' +
      pct(u.majority.chance_mean) + ' with shuffled labels). A p-value below 0.05 without the correction is not a replication by the plan.</p>';
    h += table(['Condition', 'Testable', 'Registered', 'Without correction', 'Opposite, without correction', 'Shuffled labels: mean', '97.5 % limit'],
      u.rows.map(function (r) {
        return [r[0], n(r[1]), n(r[2]) + ' (' + pct(r[2] / r[1]) + ')', n(r[3]) + ' (' + pct(r[3] / r[1]) + ')', n(r[4]), pct(r[5]), pct(r[6])];
      }), 1);
    h += '<h3 class="rpx-h">2. Ignoring very low abundances</h3>';
    h += '<p class="small" id="rpx-floor">Here a taxon counts as absent from a sample when it makes up less than the floor, and the registered test is rerun. ' +
      'A floor also makes claims untestable (the taxon is then carried by too few people), so each row compares the floor with the registered result ' +
      '<b>on the claims that stay testable</b>, not with the headline of ' + n(u.registered) + ' of ' + n(u.testable) + '. ' +
      'The floor is applied to the taxon’s summed abundance; profiles are not rescaled.</p>';
    h += table(['Floor', 'Non-zero values removed', 'Still testable', 'Replicated with the floor', 'Registered, same claims', 'Replicated → not', 'Not → replicated', 'No longer testable'],
      X.floors.map(function (r) {
        return [floorLabel(r.floor), pct(r.removed), n(r.testable), n(r.replicated) + ' (' + pct(r.replicated / r.testable) + ')',
                n(r.registered_same_claims) + ' (' + pct(r.registered_same_claims / r.testable) + ')', n(r.rep_to_not), n(r.not_to_rep),
                n(r.no_longer_testable) + ' (' + n(r.lost_replicated) + ' of them replicated before)'];
      }), 1);
    h += '<h3 class="rpx-h">3. Conditions with one or two projects</h3>';
    h += '<p class="small" id="rpx-few">' + n(f.candidates) + ' claims were not tested because fewer than three independent projects were left once the claim’s own project was set aside' +
      ' (for ' + n(f.three) + ' of them the condition has at least three and the claim’s own is among them). ' +
      n(f.candidates - f.no_cohort) + ' of them can be looked at in the one or two projects that are left; they are shown apart and are not added to the headline. ' +
      '<b>One project is one more study, not a replication across studies.</b> The claims are many but the projects are few, so these are not independent results.</p>';
    h += table(['', 'Claims', 'Conditions', 'Projects behind them', 'p < 0.05, claimed direction', 'p < 0.05, opposite direction', 'Shuffled labels, either direction: mean', 'After correction within the group'],
      f.groups.map(function (g) {
        return [g.cohorts === 1 ? 'Tested in one project' : 'Tested in two projects', n(g.tested), n(g.conditions),
                n(g.distinct_cohorts) + ' (the largest carries ' + n(g.largest) + ' claims)', share(g.uncorrected, g.tested),
                share(g.opposite, g.tested), pct(g.chance_mean) + ' claimed, ' + pct(g.chance_opp_mean) + ' opposite', share(g.bh, g.tested)];
      }), 1);
    h += '<p class="small" id="rpx-few-note">In a single project the opposite direction turns up in ' + pct(f.groups[0].opposite / f.groups[0].tested) + ' of the claims, against ' +
      pct(f.groups[0].chance_opp_mean) + ' with shuffled labels. Cases and controls of one study differ in more than the condition, and a test inside one study cannot tell the two apart; ' +
      'that is why the plan asked for three projects.' + (f.groups.length > 1 && f.groups[1].blocked !== null ? ' With two projects the result also depends on the test: ' +
      n(f.groups[1].blocked) + ' of the ' + n(f.groups[1].tested) + ' claims are below 0.05 under the stratified Wilcoxon test, which assumes the two projects agree.' : '') + '</p>';
    f.groups.forEach(function (g) {
      h += '<details class="rpx-sub"><summary>' + (g.cohorts === 1 ? 'One project' : 'Two projects') + ': by condition</summary>' +
        table(['Condition', 'Claims', 'p < 0.05, claimed direction', 'p < 0.05, opposite direction', 'After correction within the group'],
          g.rows.map(function (r) { return [r[0], n(r[1]), n(r[2]), n(r[3]), n(r[4])]; }), 1) + '</details>';
    });
    h += '<p class="small rpx-dl">Tables: ' + X.meta.downloads.map(function (d) {
      return '<a href="' + esc(P.dataDir + d) + '">' + esc(d) + '</a>'; }).join(' · ') + '</p>';
    body.innerHTML = h;
    body.dataset.ready = '1';
  }

  function noteFor(t) {                // t: the row of the open claim panel (claims tested on the same projects)
    var c = t.first, h = '';
    if (t.st < 5) {
      var i = c.i, code = X.claims.f[i], fl = [], k;
      for (k = X.floors.length - 1; k >= 0; k--) { fl.unshift(FLOOR_WORD[code % 3] + ' at ' + floorLabel(X.floors[k].floor)); code = Math.floor(code / 3); }
      h = 'Without the correction for ' + n(X.uncorrected.testable) + ' tests: ' + fp(X.claims.p[i]) + ' in the claimed direction, ' +
        (X.claims.p[i] < 0.05 ? '<b>below 0.05</b>' : 'not below 0.05') + '. ' + n(X.claims.d[i]) + ' of ' + n(c.k) + ' projects point in the claimed direction. ' +
        'With low abundances counted as absent: ' + fl.join(', ') + '.';
    } else {
      // A row of untested claims can hold claims of several papers, and a project that is usable for one paper's claim
      // is another paper's own. So every result names the claims it belongs to, and a claim whose only project is its
      // paper's own is said to have none.
      var F = X.fewer, pm = R.data().papers.pmid, byKey = Object.create(null), groups = [], own = [];
      t.claims.forEach(function (q) {
        var j = fewAt[q.i], id = pm[q.p];
        if (j === undefined) { if (own.indexOf(id) < 0) own.push(id); return; }
        var key = [F.k[j], F.auc[j], F.lo[j], F.hi[j], F.p[j], F.q[j], F.nc[j], F.nk[j]].join('|'), g = byKey[key];
        if (!g) { g = byKey[key] = { j: j, pmids: [] }; groups.push(g); }
        if (g.pmids.indexOf(id) < 0) g.pmids.push(id);
      });
      if (!groups.length) return '';
      var several = groups.length > 1 || own.length > 0;
      var parts = groups.map(function (g) {
        var j = g.j;
        return (several ? 'For the claim of PubMed ' + g.pmids.join(', ') + ': tested in <b>' : 'Tested in <b>') +
          (F.k[j] === 1 ? 'one project' : 'two projects') + '</b> (' + n(F.nc[j]) + ' cases, ' + n(F.nk[j]) +
          ' controls): the chance that a case has more of this taxon than a healthy control is ' + (F.auc[j] / 1000).toFixed(3) + ' (95 % CI ' +
          (F.lo[j] / 1000).toFixed(3) + '–' + (F.hi[j] / 1000).toFixed(3) + '); ' + fp(F.p[j]) + ' in the claimed direction without correction, ' +
          (F.q[j] < 0.05 ? 'still below 0.05' : 'not below 0.05') + ' after correcting within the ' + (F.k[j] === 1 ? 'one-project' : 'two-project') + ' group.';
      });
      if (own.length) parts.push('For the claim of PubMed ' + own.join(', ') + ' the only project is the paper’s own, so that claim is not looked at here.');
      h = parts.join(' ') + ' One or two projects are more studies, not a replication across studies; the label above stays “not tested”.';
    }
    return '<span class="rpx-tag">Exploratory</span> ' + h + ' <span class="rpx-after">Added after the test; not part of the plan.</span>';
  }
  function annotate() {
    if (!X || !panel || panel.hidden || !R.state.sel || panel.querySelector('.rpx-note')) return;
    var anchor = document.getElementById('rp-caveat'); if (!anchor) return;
    var html = noteFor(R.state.sel); if (!html) return;
    var p = document.createElement('p');
    p.className = 'sp-note rpx-note'; p.id = 'rpx-note'; p.innerHTML = html;
    anchor.parentNode.insertBefore(p, anchor.nextSibling);
  }

  function start() {
    return Promise.resolve(R.ready).then(function (D) {
      if (!D) return null;
      return SP.getJSON(P.dataDir + 'replication_exploratory.json').then(function (x) {
        var cl = D.claims, pr = x.pair;
        if (x.format !== 1 || pr.claims !== cl.id.length || pr.testable !== cl.n_testable || pr.first !== cl.id[0] ||
            pr.last_testable !== cl.id[cl.n_testable - 1] || x.claims.p.length !== cl.n_testable) throw new Error('built for another results file');
        X = x;
        x.fewer.i.forEach(function (i, j) { fewAt[i] = j; });
        drawSection();
        if (panel && window.MutationObserver) new window.MutationObserver(annotate).observe(panel, { childList: true });
        annotate();
        return x;
      });
    }).catch(function (e) {
      if (body) body.dataset.ready = 'failed';
      window.SP_REPLICATION_X.error = e;
      return null;
    });
  }
  window.SP_REPLICATION_X = { start: start, annotate: annotate, data: function () { return X; }, noteFor: noteFor };
  if (!P.manualStart) window.SP_REPLICATION_X.ready = start();
})();
