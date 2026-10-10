/* Project-page sample table (R2026.18): rendered in the browser from data/studies/<ACC>.csv.gz (the same file as the
   download), so every sample and every field is shown without inflating the static pages. Columns come from the page
   (window.STUDY_TABLE.cols: only the fields this project has values for). Each cell's tooltip names the
   field, value, route and confidence. */
(function () {
  'use strict';
  const cfg = window.STUDY_TABLE; if (!cfg) return;
  cfg.cols = (cfg.cols || []).filter((c) => !c.empty);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const ROUTE = { R1: 'archive', R2: 'supplementary table / external', R3: 'paper full text', R4: 'abstract / description' };
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
  function archiveUrl(a) {
    if (!a) return '';
    if (/^SAMN/.test(a)) return 'https://www.ncbi.nlm.nih.gov/biosample/' + a;
    if (/^SAME/.test(a)) return 'https://www.ebi.ac.uk/ena/browser/view/' + a;
    if (/^SAMD/.test(a)) return 'https://ddbj.nig.ac.jp/resource/biosample/' + a;
    return '';
  }
  async function load() {
    const st = document.getElementById('st-status');
    try {
      const r = await fetch(cfg.csv); if (!r.ok) throw new Error('HTTP ' + r.status);
      let text;
      if (typeof DecompressionStream !== 'undefined') text = await new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).text();
      else throw new Error('this browser cannot read the compressed table');
      const rows = parseCSV(text); const hdr = rows.shift(); const ix = {}; hdr.forEach((h, i) => { ix[h] = i; });
      const get = (row, k) => (ix[k] === undefined ? '' : (row[ix[k]] || ''));
      const val = (row, f) => {
        if (f === 'detailed_location') return ['location_site', 'location_locality', 'location_region'].map((k) => get(row, k)).filter(Boolean).join(', ');
        let v = get(row, f); if (v === 'unknown') return '';
        if (f === 'age_at_collection_days' && v !== '' && !isNaN(+v)) v = String(Math.round(+v));
        if ((f === 'seq_gbp' || f === 'bmi') && v !== '' && !isNaN(+v)) v = (+v).toFixed(f === 'bmi' ? 1 : 2);
        return v;
      };
      const data = rows.filter((x) => x.length > 1).map((row) => {
        const key = get(row, 'sample_key'), bs = get(row, 'biosample_accession');
        const out = [key, bs];
        for (const c of cfg.cols) out.push(c.empty ? '' : val(row, c.f));
        out.push(val(row, 'seq_gbp')); out.push(get(row, 'n_runs'));
        out._row = row; return out;
      });
      const tipOf = (row, c, v) => { if (!v) return c.label; const rt = get(row, c.f + '__route'), cf = get(row, c.f + '__confidence');
        return c.label + ': ' + v + (rt ? ' · ' + rt + ' (' + (ROUTE[rt] || '') + ')' : '') + (cf ? ' · confidence ' + (+cf).toFixed(2) : ''); };
      const columns = [
        { title: 'sample', render: (d, t) => t === 'display' ? '<a class="mono" href="../samples/sample.html?id=' + encodeURIComponent(d) + '">' + esc(d) + '</a>' : d },
        { title: 'archive', render: (d, t) => { if (t !== 'display') return d; const u = archiveUrl(d); return u ? '<a class="mono" href="' + u + '">' + esc(d) + '</a>' : esc(d); } }];
      cfg.cols.forEach((c, j) => columns.push(c.empty
        ? { title: '<span>' + esc(c.label) + '</span>', className: 'ecol', orderable: false, render: () => '' }
        : { title: esc(c.label), className: c.num ? 'num' : 'tclip', render: (d, t, row) => t === 'display' ? (d ? '<span title="' + esc(tipOf(row._row, c, d)) + '">' + esc(d) + '</span>' : '') : d }));
      columns.push({ title: 'Gbp', className: 'num' }); columns.push({ title: 'runs', className: 'num' });
      st.textContent = '';
      new DataTable('#samples', { data, columns, pageLength: 25, lengthMenu: [25, 100, 500, 2000], order: [], autoWidth: false, deferRender: true,
        headerCallback: (thead) => { thead.querySelectorAll('th').forEach((th, i) => { const c = cfg.cols[i - 2]; if (c) th.title = c.f; }); } });
    } catch (e) {
      st.innerHTML = 'The sample table could not be loaded here (' + esc(e.message) + '). Download it below or open the <a href="../samples/index.html?q=' + encodeURIComponent(cfg.acc) + '">sample sheet</a>.';
    }
  }
  load();
})();
