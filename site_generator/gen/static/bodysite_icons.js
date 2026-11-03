/* Body-site symbols (R2026.34): one 20 x 20 line pictogram per body_site_code, drawn in currentColor.
   BSI.icon(code, label) -> inline <svg> with a <title>, so hovering names the site and screen readers read it. */
(function () {
  const P = {
    // colon frame (ascending, transverse, descending, rectum) with the small intestine inside
    gut_stool: '<path d="M6.6 16.7a2.3 2.3 0 0 1-2.3-2.3V6.8a2.6 2.6 0 0 1 2.6-2.6h6.2a2.6 2.6 0 0 1 2.6 2.6v7.6a2.3 2.3 0 0 1-2.3 2.3h-2.2v1.9"/><path d="M7.2 7.4h5.5a1.1 1.1 0 0 1 0 2.2H7.4a1.1 1.1 0 0 0 0 2.2h5.2a1.1 1.1 0 0 1 0 2.2H6.6"/>',
    // molar
    oral: '<path d="M7 3.5C4.6 3.5 3.6 5.4 3.8 7.4 4 9.4 5 10.6 5.4 12.6c.4 2.2.8 4.2 2 4.2s1.2-3.2 1.9-4.4c.3-.5 1.1-.5 1.4 0 .7 1.2.7 4.4 1.9 4.4s1.6-2 2-4.2c.4-2 1.4-3.2 1.6-5.2.2-2-.8-3.9-3.2-3.9-1.3 0-1.8.8-3 .8s-1.7-.8-3-.8z"/>',
    // nose, front view, with nostrils
    nasal_nasopharyngeal: '<path d="M8.6 3c0 4-1 6-2.8 8.6-1 1.5-.3 3.5 1.5 3.5.8 0 1.2-.5 2.7-.5s1.9.5 2.7.5c1.8 0 2.5-2 1.5-3.5C12.4 9 11.4 7 11.4 3"/><path d="M7.9 13.1q.7-.8 1.4 0M10.7 13.1q.7-.8 1.4 0"/>',
    // trachea, bronchi and two lungs
    respiratory_lower: '<path d="M10 2.8v6.7M10 9.5 7.8 11.3M10 9.5l2.2 1.8"/><path d="M7.8 6.2C5.2 5.6 3.2 9.6 3.2 13.6c0 2.2 1 3.4 2.6 3.4 1.4 0 2-.9 2-2.4zM12.2 6.2c2.6-.6 4.6 3.4 4.6 7.4 0 2.2-1 3.4-2.6 3.4-1.4 0-2-.9-2-2.4z"/>',
    // uterus with tubes and ovaries
    vaginal_urogenital: '<path d="M7 7.6h6c0 3-1 4.5-1.8 5.1V16H8.8v-3.3C8 12.1 7 10.6 7 7.6z"/><path d="M7 7.6C5.9 6.1 4.7 5.7 3.9 6.4M13 7.6c1.1-1.5 2.3-1.9 3.1-1.2"/><circle cx="3.7" cy="8.6" r="1.25"/><circle cx="16.3" cy="8.6" r="1.25"/>',
    // open hand
    skin: '<path d="M6.6 11.2V5.9a1.2 1.2 0 0 1 2.4 0V10M9 9.6V4.4a1.2 1.2 0 0 1 2.4 0v5.2M11.4 9.7V5.4a1.2 1.2 0 0 1 2.4 0v5.1M13.8 10.1V7.7a1.2 1.2 0 0 1 2.4 0v4.4c0 3-2.2 5.1-5 5.1h-.8c-1.8 0-3-.7-4-2.1l-2.6-3.6a1.2 1.2 0 0 1 2-1.3l.8 1"/>',
    // eye
    eye_ear: '<path d="M2.5 10C4.8 6.1 7.2 5 10 5s5.2 1.1 7.5 5c-2.3 3.9-4.7 5-7.5 5S4.8 13.9 2.5 10z"/><circle cx="10" cy="10" r="2.5"/><circle cx="10" cy="10" r=".7" fill="currentColor"/>',
    // drop, outlined (milk)
    milk: '<path d="M10 3.2S5.2 8.7 5.2 12a4.8 4.8 0 0 0 9.6 0C14.8 8.7 10 3.2 10 3.2z"/><path d="M7.6 12.6a2.4 2.4 0 0 0 2 2.1"/>',
    // drop, filled (blood or tissue)
    blood_tissue: '<path d="M10 3.2S5.2 8.7 5.2 12a4.8 4.8 0 0 0 9.6 0C14.8 8.7 10 3.2 10 3.2z" fill="currentColor"/>',
    // dashed ring: a stated site outside the groups above
    other_site: '<circle cx="10" cy="10" r="6" stroke-dasharray="2.2 2.2"/><circle cx="10" cy="10" r="1.3" fill="currentColor"/>',
    // three sites
    multi_site: '<circle cx="10" cy="5.6" r="2.3"/><circle cx="5.6" cy="13.6" r="2.3"/><circle cx="14.4" cy="13.6" r="2.3"/>',
  };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const icon = (code, label) => '<svg class="bsi" viewBox="0 0 20 20" width="18" height="18" role="img" aria-label="' + esc(label || code) + '" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><title>' + esc(label || code) + '</title>' + (P[code] || P.other_site) + '</svg>';
  window.BSI = {icon: icon, codes: Object.keys(P)};
})();
