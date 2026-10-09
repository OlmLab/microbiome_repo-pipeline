// Shared behaviour: external links open in a new tab; prefilled GitHub issue URLs (B4).
document.addEventListener('DOMContentLoaded', () => {
  for (const a of document.querySelectorAll('a[href^="http"]')) { a.target = '_blank'; a.rel = 'noopener'; }
});
// Build a GitHub issue-form URL. Keys must equal the field ids of the installed issue form (config/site.yaml github.issues.template;
// site_generator/gen/issue_templates/simple-finding.yml: accession, release_id, page_url, problem, details).
window.catalogIssueUrl = function (fields) {
  const C = window.CATALOG || {issueRepo: 'https://github.com/OlmLab/microbiome_repo/issues/new', issueTemplate: 'simple-finding.yml', issueLabel: 'finding', release: ''};
  const p = [['template', C.issueTemplate], ['labels', C.issueLabel || 'finding']];
  if (fields.title) p.push(['title', String(fields.title).slice(0, 200)]);
  for (const k of Object.keys(fields).sort()) {
    if (k === 'title') continue;
    let v = fields[k]; if (v === null || v === undefined || v === '') continue;
    v = String(v); if (k === 'current_state') v = v.slice(0, 2500);
    p.push([k, v]);
  }
  const url = C.issueRepo + '?' + p.map(([k, v]) => k + '=' + encodeURIComponent(v)).join('&');
  return url.slice(0, 6000);
};

// R2026.15 drop-down navigation: click / tap toggles a menu (hover and keyboard focus are handled in CSS); Escape or an outside click closes it
(function(){
  const dds=[...document.querySelectorAll('.topnav .navdd')];
  const closeAll=(except)=>dds.forEach(d=>{if(d!==except){d.classList.remove('open');const b=d.querySelector('.navbtn');if(b)b.setAttribute('aria-expanded','false');}});
  dds.forEach(d=>{const b=d.querySelector('.navbtn'); if(!b) return;
    b.addEventListener('click',e=>{e.stopPropagation();const o=!d.classList.contains('open');closeAll(d);d.classList.toggle('open',o);b.setAttribute('aria-expanded',o?'true':'false');});});
  document.addEventListener('click',()=>closeAll(null));
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAll(null);});
})();

// R2026.20: instant tooltips. Native title tooltips appear late (or not at all on some browsers / touch screens), so every
// element with a title attribute — and SVG shapes with a <title> child — shows its text immediately on hover, focus or tap.
(function () {
  if (window.__tipInit) return; window.__tipInit = true;
  const tip = document.createElement('div'); tip.className = 'tip'; tip.setAttribute('role', 'tooltip'); tip.style.display = 'none';
  const ready = () => document.body.appendChild(tip);
  if (document.body) ready(); else document.addEventListener('DOMContentLoaded', ready);
  let cur = null;
  const textOf = (el) => {
    if (el.hasAttribute('title')) { const t = el.getAttribute('title'); el.removeAttribute('title'); if (t) el.setAttribute('data-tip', t); }
    if (el.hasAttribute('data-tip')) return el.getAttribute('data-tip');
    const st = el.querySelector && el.querySelector(':scope > title'); return st ? st.textContent : '';
  };
  const find = (n) => { for (let el = n; el && el !== document.body; el = el.parentNode) {
    if (el.nodeType !== 1) continue;
    if (el.hasAttribute('title') || el.hasAttribute('data-tip')) return el;
    if (el instanceof SVGElement && el.querySelector(':scope > title')) return el; } return null; };
  function show(el) {
    const t = textOf(el); if (!t) { hide(); return; }
    tip.textContent = t; tip.style.display = 'block'; cur = el;
    const r = el.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    let x = Math.min(Math.max(6, r.left + r.width / 2 - w / 2), window.innerWidth - w - 6), y = r.bottom + 6;
    if (y + h > window.innerHeight - 6) y = Math.max(6, r.top - h - 6);
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function hide() { tip.style.display = 'none'; cur = null; }
  document.addEventListener('mouseover', (e) => { const el = find(e.target); if (el) { if (el !== cur) show(el); } else if (cur) hide(); });
  document.addEventListener('focusin', (e) => { const el = find(e.target); if (el) show(el); });
  document.addEventListener('focusout', hide);
  document.addEventListener('touchstart', (e) => { const el = find(e.target); if (el) show(el); else hide(); }, { passive: true });
  window.addEventListener('scroll', hide, { passive: true });
})();
