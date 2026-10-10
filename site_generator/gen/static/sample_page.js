/* R2026.23 single-sample page (samples/sample.html?id=<sample | BioSample | run accession>). One static page for all
   588k samples: the accession is resolved through a small lookup shard (data/lookup/<last two digits>.json →
   project index), then the project's existing slice (data/studies/<PRJ>.csv.gz) and evidence file
   (<PRJ>_determinations.csv.gz) are read and only this sample is shown — no per-sample HTML files. */
(function () {
  'use strict';
  const C = window.SAMPLE_PAGE || {};
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const ROUTE = { R1: 'Sequence archive', R2: 'Supplementary table / external resource', R3: 'Paper full text', R4: 'Abstract / description' };
  const LABEL = C.labels || {};
  const SKIP = new Set(['sample_key', 'study_accession', 'biosample_accession', 'secondary_sample', 'release_added', 'release_retired', 'package_added', 'in_infant_catalog', 'curated_source', 'n_fields_with_value', 'sample_unit', 'body_site_basis', 'infant_scope', 'seq_depth_source', 'seq_reads', 'n_runs', 'seq_gbp', 'age_category_basis', 'sp_low_depth']);
  function parseCSV(text) {
    const rows = []; let row = [], f = '', q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) { if (c === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += c; }
      else if (c === '"') q = true; else if (c === ',') { row.push(f); f = ''; }
      else if (c === '\n') { row.push(f); rows.push(row); row = []; f = ''; } else if (c !== '\r') f += c;
    }
    if (f !== '' || row.length) { row.push(f); rows.push(row); }
    return rows;
  }
  async function gz(url) {
    const r = await fetch(url); if (!r.ok) throw new Error('HTTP ' + r.status + ' for ' + url);
    if (typeof DecompressionStream === 'undefined') throw new Error('this browser cannot read compressed files');
    return new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).text();
  }
  function table(text) {
    const rows = parseCSV(text); const hdr = rows.shift() || []; const ix = {}; hdr.forEach((h, i) => { ix[h] = i; });
    return { hdr, ix, rows: rows.filter((r) => r.length > 1), get: (r, k) => (ix[k] === undefined ? '' : (r[ix[k]] || '')) };
  }
  function shardOf(acc) { const m = /(\d)(\d)\D*$/.exec(acc) || /(\d)\D*$/.exec(acc); return m ? (m[2] !== undefined ? m[1] + m[2] : '0' + m[1]) : 'xx'; }
  function archiveUrl(a) {
    if (/^SAMN/.test(a)) return 'https://www.ncbi.nlm.nih.gov/biosample/' + a;
    if (/^SAME/.test(a)) return 'https://www.ebi.ac.uk/ena/browser/view/' + a;
    if (/^SAMD/.test(a)) return 'https://ddbj.nig.ac.jp/resource/biosample/' + a;
    if (/^[EDS]RR/.test(a)) return 'https://www.ebi.ac.uk/ena/browser/view/' + a;
    return '';
  }
  const link = (a) => { const u = archiveUrl(a); return u ? '<a class="mono" href="' + u + '">' + esc(a) + '</a>' : '<span class="mono">' + esc(a) + '</span>'; };
  const sref = (k) => '<a class="mono" href="sample.html?id=' + encodeURIComponent(k) + '">' + esc(k) + '</a>';
  function fmtVal(f, v) {
    if (f === 'age_at_collection_days' && v !== '' && !isNaN(+v)) { const d = +v; return Math.round(d) + ' days' + (d >= 730 ? ' (' + (d / 365.25).toFixed(1) + ' y)' : d >= 60 ? ' (' + (d / 30.44).toFixed(1) + ' mo)' : ''); }
    if (f === 'bmi' && v !== '' && !isNaN(+v)) return (+v).toFixed(1);
    if (f === 'subject_n_samples' && v !== '' && !isNaN(+v)) return String(Math.round(+v));
    if (f === 'days_since_first_sample' && v !== '' && !isNaN(+v)) return Math.round(+v) + ' days';
    return v;
  }
  async function main() {
    const q = new URLSearchParams(location.search); const id = (q.get('id') || '').trim().toUpperCase();
    const st = $('sp-status');
    if (!id) { st.innerHTML = 'Give a sample, BioSample or run accession, e.g. <a href="?id=' + esc(C.example || '') + '">' + esc(C.example || '') + '</a>.'; return; }
    document.title = id + ' — ' + (C.siteTitle || 'sample');
    try {
      const look = await (await fetch(C.root + 'data/lookup/' + shardOf(id) + '.json')).json();
      const hit = look[id];
      if (hit === undefined) { st.innerHTML = '<b class="mono">' + esc(id) + '</b> is not a catalog sample. It may be in the <a href="' + C.root + 'registry/index.html?q=' + encodeURIComponent(id) + '">registry</a> (every screened study) or excluded (non-human, control, isolate).'; return; }
      const [si, keyFromRun] = String(hit).split('|');
      const studies = await (await fetch(C.root + 'data/lookup/studies.json')).json();
      const acc = studies[+si][0], title = studies[+si][1];
      const key = keyFromRun || id;
      const S = table(await gz(C.root + 'data/studies/' + acc + '.csv.gz'));
      const row = S.rows.find((r) => S.get(r, 'sample_key') === key || S.get(r, 'biosample_accession') === key || S.get(r, 'secondary_sample') === key);
      if (!row) { st.textContent = 'Sample not found in its project file.'; return; }
      const sk = S.get(row, 'sample_key');
      st.textContent = '';
      $('sp-head').innerHTML = '<div class="pg-acc"><span class="mono">' + esc(sk) + '</span>' + (S.get(row, 'biosample_accession') && S.get(row, 'biosample_accession') !== sk ? ' <span class="muted">BioSample</span> ' + link(S.get(row, 'biosample_accession')) : '') + '</div>' +
        '<h1 class="pg-title">Sample ' + esc(sk) + '</h1><p class="pg-facts">Project <a class="mono" href="' + C.root + 'studies/' + esc(acc) + '.html">' + esc(acc) + '</a> — ' + esc(title) + '</p>' +
        '<p class="small muted">' + [S.get(row, 'sample_unit') === 'run' ? 'one sequencing run of a pooled BioSample' : '', S.get(row, 'n_runs') ? Math.round(+S.get(row, 'n_runs')) + ' run(s)' : '', S.get(row, 'seq_gbp') ? (+S.get(row, 'seq_gbp')).toFixed(2) + ' Gbp' : '', S.get(row, 'curated_source') === 'infant_catalog' ? 'infant extension' : ''].filter(Boolean).join(' · ') + '</p>' + '<p class="pg-links">' + (archiveUrl(sk) ? '<a href="' + archiveUrl(sk) + '">Archive record</a>' : '') + '<a href="' + C.root + 'samples/index.html?sample=' + encodeURIComponent(sk) + '">Open in the sample sheet</a><a href="' + C.root + 'studies/' + esc(acc) + '.html#sample-table">All samples of the project</a></p>';
      // evidence rows of this sample
      let ev = {};
      try { const D = table(await gz(C.root + 'data/studies/' + acc + '_determinations.csv.gz'));
        for (const r of D.rows) if (D.get(r, 'sample_key') === sk) ev[D.get(r, 'field_name')] = { src: D.get(r, 'evidence_source'), loc: D.get(r, 'evidence_locator'), quote: D.get(r, 'evidence_quote'), by: D.get(r, 'determined_by'), note: D.get(r, 'parse_note') };
      } catch (e) { /* evidence file optional */ }
      const fields = S.hdr.filter((h) => !h.includes('__') && !SKIP.has(h));
      const trs = [];
      for (const f of fields) {
        const v = S.get(row, f); if (!v || v === 'unknown' || v === 'False' && f.startsWith('qc_')) continue;
        const rt = S.get(row, f + '__route'), cf = S.get(row, f + '__confidence'), e = ev[f];
        const gen = e && /^generated:/.test(e.note || '');
        trs.push('<tr><td><a class="mono" href="' + C.root + 'fields/index.html#' + esc(f) + '" title="' + esc(LABEL[f] || f) + '">' + esc(f) + '</a></td><td>' + esc(fmtVal(f, v)) + (gen ? ' <span class="tag" title="' + esc(e.note) + '">generated</span>' : '') + '</td><td>' + (rt ? '<span class="tag ' + rt + '" title="' + esc(ROUTE[rt] || '') + '">' + rt + '</span>' : '<span class="muted small">derived</span>') + '</td><td class="num">' + (cf ? (+cf).toFixed(2) : '') + '</td><td class="small">' + (e ? '<span class="mono">' + esc(e.src) + '</span>' + (e.loc && /^http/.test(e.loc) ? ' <a href="' + esc(e.loc) + '">source</a>' : (e.loc ? ' <span class="muted">' + esc(e.loc) + '</span>' : '')) + (e.quote ? '<div class="quote">“' + esc(e.quote) + '”</div>' : '') : '') + '</td></tr>');
      }
      $('sp-fields').innerHTML = '<table class="tbl small compact"><thead><tr><th>field</th><th>value</th><th>route</th><th class="num">conf.</th><th>evidence</th></tr></thead><tbody>' + trs.join('') + '</tbody></table>';
      // same subject / same family within the project
      const subj = S.get(row, 'subject_id'), fam = S.get(row, 'family_id');
      const rel = [];
      const peer = (pred, label) => {
        const xs = S.rows.filter((r) => S.get(r, 'sample_key') !== sk && pred(r)).slice(0, 200);
        if (!xs.length) return;
        xs.sort((a, b) => (+S.get(a, 'age_at_collection_days') || 0) - (+S.get(b, 'age_at_collection_days') || 0) || S.get(a, 'collection_date').localeCompare(S.get(b, 'collection_date')));
        rel.push('<div class="small"><b>' + label + '</b> (' + xs.length + '): ' + xs.map((r) => sref(S.get(r, 'sample_key')) + ['family_role', 'timepoint_label', 'age_at_collection_days', 'collection_date'].map((k) => S.get(r, k)).filter((v) => v && v !== 'unknown').slice(0, 2).map((v, i) => ' <span class="muted">' + esc(i === 0 && /^\d+(\.\d+)?$/.test(v) ? Math.round(+v) + ' d' : v) + '</span>').join('')).join(' · ') + '</div>');
      };
      if (subj && !/^generated/.test((ev.subject_id || {}).note || '')) peer((r) => S.get(r, 'subject_id') === subj, 'Other samples of the same person (subject ' + esc(subj) + ')');
      if (fam) peer((r) => S.get(r, 'family_id') === fam && S.get(r, 'subject_id') !== subj, 'Samples of related people (family ' + esc(fam) + ')');
      $('sp-related').innerHTML = rel.join('');
    } catch (e) { st.textContent = 'Could not load this sample (' + e.message + ').'; }
  }
  main();
})();
