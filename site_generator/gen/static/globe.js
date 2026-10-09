/* Mini globe (R2026.18): an orthographic globe that shades the countries a project's samples come from and dots the
   sample coordinates; drag to rotate, hover for counts. Shared by the project pages and the Project sheet.
   Needs d3 v7 + topojson-client (pinned with SRI in the template) and the vendored world-110m TopoJSON. */
(function () {
  'use strict';
  const ROOT = window.GLOBE_ROOT || '../';
  let worldP = null, isoP = null;
  const world = () => (worldP = worldP || fetch(ROOT + 'static/vendor/countries-110m.json').then((r) => r.json()));
  const isoMap = () => (isoP = isoP || fetch(ROOT + 'data/atlas/countries.json').then((r) => r.ok ? r.json() : []).catch(() => []));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  async function render(el, spec) {
    const [w, cl] = await Promise.all([world(), isoMap()]);
    const numToIso = {}, isoName = {};
    (cl || []).forEach((c) => { if (c.num) numToIso[c.num] = c.iso2; isoName[c.iso2] = c.name; });
    const feats = topojson.feature(w, w.objects.countries).features.filter((f) => f.id !== '010');
    const size = spec.size || 200, R = size / 2 - 2;
    el.innerHTML = '';
    const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${size} ${size}`).attr('width', size).attr('height', size).attr('class', 'globe');
    const tip = d3.select(el).append('div').attr('class', 'globe-tip').style('display', 'none');
    const counts = spec.countries || {};
    const max = Math.max(1, ...Object.values(counts));
    // centre on the weighted centroid of the shaded countries / points
    let cx = 0, cy = 0, cw = 0;
    feats.forEach((f) => { const iso = numToIso[f.id]; const n = counts[iso]; if (n) { const c = d3.geoCentroid(f); cx += c[0] * n; cy += c[1] * n; cw += n; } });
    (spec.points || []).forEach((p) => { cx += p[0] * p[2]; cy += p[1] * p[2]; cw += p[2]; });
    let rot = cw ? [-cx / cw, -Math.max(-60, Math.min(60, cy / cw))] : [-10, -20];
    const proj = d3.geoOrthographic().scale(R).translate([size / 2, size / 2]).clipAngle(90).rotate(rot);
    const path = d3.geoPath(proj);
    const gS = svg.append('path').datum({ type: 'Sphere' }).attr('class', 'g-sphere');
    const gGr = svg.append('path').datum(d3.geoGraticule10()).attr('class', 'g-grat');
    const gC = svg.append('g').selectAll('path').data(feats).join('path').attr('class', 'g-land')
      .style('fill', (f) => { const n = counts[numToIso[f.id]]; return n ? d3.interpolateRgb('#efe4c4', '#a88b4a')(Math.sqrt(n / max)) : null; })
      .on('mousemove', (ev, f) => { const iso = numToIso[f.id]; const n = counts[iso]; if (!n) { tip.style('display', 'none'); return; }
        tip.style('display', 'block').html(`${esc(isoName[iso] || iso)}: <b>${n.toLocaleString()}</b> ${esc(spec.unit || 'samples')}`); })
      .on('mouseleave', () => tip.style('display', 'none'));
    const pts = svg.append('g').selectAll('circle').data(spec.points || []).join('circle').attr('class', 'g-pt')
      .attr('r', (p) => Math.max(1.6, Math.min(6, 1.4 + Math.sqrt(p[2]) * 0.6)))
      .on('mousemove', (ev, p) => tip.style('display', 'block').html(`${p[1].toFixed(1)}°, ${p[0].toFixed(1)}°: <b>${p[2].toLocaleString()}</b> ${esc(spec.unit || 'samples')}`))
      .on('mouseleave', () => tip.style('display', 'none'));
    function draw() {
      proj.rotate(rot); gS.attr('d', path); gGr.attr('d', path); gC.attr('d', path);
      pts.attr('transform', (p) => { const q = proj([p[0], p[1]]); return q ? `translate(${q[0]},${q[1]})` : 'translate(-99,-99)'; })
        .style('display', (p) => d3.geoDistance([p[0], p[1]], [-rot[0], -rot[1]]) > Math.PI / 2 ? 'none' : null);
    }
    svg.call(d3.drag().on('drag', (ev) => { rot = [rot[0] + ev.dx * 0.5, Math.max(-90, Math.min(90, rot[1] - ev.dy * 0.5))]; draw(); }));
    draw();
    return { update(newCounts) { Object.keys(counts).forEach((k) => delete counts[k]); Object.assign(counts, newCounts); const m = Math.max(1, ...Object.values(counts));
      gC.style('fill', (f) => { const n = counts[numToIso[f.id]]; return n ? d3.interpolateRgb('#efe4c4', '#a88b4a')(Math.sqrt(n / m)) : null; }); } };
  }
  window.MiniGlobe = { render };
})();
