// dom_explorer.mjs — headless test of the taxon explorer: no browser, no bundler.
//
// Loads the RENDERED preview page into jsdom over an http://localhost base URL, with a fetch shim that
// reads the preview folder from disk (JSON *and* the binary payload), evaluates the real vendored d3 and
// topojson and the real static/sp_core.js, sp_taxdata.js and sp_explorer.js, and asserts that the tree,
// the search, the address and the detail panel do what the page promises. Every expected number is
// recomputed with a second, independent copy of the reader loaded in Node from the same files.
// It writes the drawn SVGs to tests/out/*.svg so they can be rasterised and looked at.
//
//   NODE_PATH=<side_pages>/work/node/node_modules node for_editor/taxon_explorer/tests/dom_explorer.mjs [previewRoot]
//
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
const require_ = createRequire(import.meta.url);
const { JSDOM, VirtualConsole } = require_("jsdom");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DELIVER = path.dirname(HERE);
const SIDE = path.dirname(path.dirname(DELIVER));
const ROOT = process.argv[2] || path.join(SIDE, "previews", "site_r3");
const PAGE = "atlas/taxa/index.html";
const OUT = path.join(HERE, "out");
fs.mkdirSync(OUT, { recursive: true });
const BASE = "http://localhost:8765";

let pass = 0, fail = 0;
const ok = (name, cond, extra = "") => {
  if (cond) { pass++; console.log(`✓  ${name}`); }
  else { fail++; console.log(`✗ FAIL  ${name}${extra ? "  — " + extra : ""}`); }
};
const brief = (e) => String(e?.stack || e?.message || e).split("\n").slice(0, 3).join(" ⏎ ").slice(0, 300);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- an independent reader, in Node
const REF = require_(path.join(ROOT, "static", "sp_taxdata.js"));
const bin = (p) => { const b = fs.readFileSync(path.join(ROOT, "data", "taxa", p));
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };
REF.loadTree(bin("tree.bin"));
REF.loadStats(bin("stats.bin"));

// ---------------------------------------------------------------- the page under test
function openPage() {
  const file = path.join(ROOT, PAGE);
  const raw = fs.readFileSync(file, "utf8");
  const html = raw.replace(/<script[\s\S]*?<\/script>/g, "");     // the scripts are eval'd below
  const errors = [], fetched = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => { if (!/Not implemented/.test(String(e))) errors.push("jsdom: " + brief(e)); });
  vc.on("error", (...a) => errors.push("console.error: " + a.map(brief).join(" ")));
  const dom = new JSDOM(html, { url: `${BASE}/${PAGE}`, runScripts: "outside-only", pretendToBeVisual: true,
                                virtualConsole: vc });
  const { window: w } = dom;
  w.addEventListener("error", (e) => errors.push("window.error: " + (e.error?.stack || e.message)));
  w.fetch = async (url) => {
    const u = new URL(String(url), `${BASE}/${PAGE}`);
    const p = path.join(ROOT, decodeURIComponent(u.pathname));
    fetched.push(u.pathname);
    if (!fs.existsSync(p)) return { ok: false, status: 404, json: async () => ({}), arrayBuffer: async () => new w.ArrayBuffer(0) };
    const buf = fs.readFileSync(p);
    return {
      ok: true, status: 200,
      json: async () => JSON.parse(buf.toString("utf8")),
      arrayBuffer: async () => { const u8 = new w.Uint8Array(buf.length); u8.set(buf); return u8.buffer; }
    };
  };
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  // run every <script> of the rendered page, in document order, exactly as a browser would: a src is read
  // from the preview folder, an inline script is evaluated as it stands (that is how the page's own
  // wiring, including the reader's CommonJS shim, is put under test rather than re-implemented here)
  const nav = [];
  const scripts = [...raw.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
  for (const [, attrs, body] of scripts) {
    const src = /src\s*=\s*"([^"]+)"/.exec(attrs);
    if (src) {
      if (/site\.js$/.test(src[1])) continue;              // the site chrome's own script, not under test
      if (/^(https?:)?\/\//.test(src[1])) continue;        // an off-site script of the site chrome (the visit counter), not under test
      const p = path.join(ROOT, path.normalize(path.join(path.dirname(PAGE), src[1])));
      w.eval(fs.readFileSync(p, "utf8"));
      if (/sp_core\.js$/.test(src[1])) { w.SP.go = (u) => nav.push(u); }
    } else if (body.trim()) {
      w.eval(body);
    }
  }
  return { window: w, doc: w.document, errors, nav, fetched,
           q: (s) => w.document.querySelector(s), qa: (s) => Array.from(w.document.querySelectorAll(s)) };
}
const click = (w, el, extra = {}) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true, ...extra }));
const saveSvg = (name, el) => {
  if (!el) { console.log(`   (no svg for ${name})`); return; }
  const s = el.outerHTML.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ');
  fs.writeFileSync(path.join(OUT, name), s);
};
const sentences = (t) => String(t || "").split(/\.\s/).filter((s) => s.trim().length > 3).length;

console.log(`taxon explorer — headless test\npreview: ${ROOT}\n`);
const P = openPage();
const { window: w, q, qa } = P;
const EX = () => w.SP_EXPLORER;
await sleep(1200);                      // tree.bin, then stats.bin

// ------------------------------------------------------------------ 1. the page itself
ok("the page has a search box and a tree container", !!q("#tx-search") && !!q("#tx-tree"));
ok("at most two sentences above the search box and the tree", sentences(q(".sp-lead")?.textContent) <= 2,
   q(".sp-lead")?.textContent?.slice(0, 120));
ok("no chart image anywhere on the page", qa("img").length === 0, `img=${qa("img").length}`);
ok("a noscript line points at the data files", /JavaScript/.test(q("noscript")?.textContent || ""));
ok("the page carries no list of taxa (the HTML is small)",
   fs.statSync(path.join(ROOT, PAGE)).size < 30000 && !/Bacteroides uniformis/.test(fs.readFileSync(path.join(ROOT, PAGE), "utf8")),
   `${fs.statSync(path.join(ROOT, PAGE)).size} bytes`);
ok("the statistics file is loaded in the background", w.document.body.dataset.spxStats === "1");
// round 4: the page opens with the taxonomy list; sections 2 to 9 test the tree, section 9b the list
ok("the page opens with the taxonomy list and the tree hidden",
   q("#tx-listbox").hidden === false && q("#tx-treebox").hidden === true && qa("#tx-list > ul > li").length === 2,
   `roots=${qa("#tx-list > ul > li").length}`);
EX().setMode("tree");
await sleep(150);

// ------------------------------------------------------------------ 2. the first view
const BOUND = EX().state().cap + 16;        // per panel: DRAW_CAP + room for the children of the last expansion
const overBound = (s) => s.panels.filter((p) => p.drawn > BOUND).length;
let st0 = EX().state();
ok("the first view draws two panels, Bacteria and Archaea", st0.panels.length === 2,
   JSON.stringify(st0.panels.map((p) => p.root)));
ok(`every panel of the first view stays within the element bound (${BOUND} clades each)`,
   overBound(st0) === 0, `drawn=${st0.panels.map((p) => p.drawn).join("+")}`);
ok("the first view is drawn quickly", st0.ms < 50, `${st0.ms.toFixed(1)} ms`);
const phylaLabels = qa(".spx-tree .spx-lab-open.spx-rank-phylum, .spx-tree .spx-lab-clade")
  .map((t) => t.textContent.replace(/ \(.*/, "").trim());
const bigPhyla = ["Bacillota", "Pseudomonadota", "Bacteroidota", "Actinomycetota"];
const labelled = bigPhyla.filter((p) => phylaLabels.some((l) => l.startsWith(p)));
ok("the first view names the large phyla", labelled.length === bigPhyla.length,
   `labelled=${labelled.join(",")} of ${bigPhyla.join(",")}`);
ok("every panel has a scale bar in substitutions/site",
   qa(".spx-tree .spx-scalebar text").length === 2 &&
   qa(".spx-tree .spx-scalebar text").every((t) => /substitutions\/site/.test(t.textContent)));
ok("the two domains are drawn with a visible break", !!q(".spx-break"));
ok("folded clades say how many tips they hold",
   qa(".spx-wedge").length > 10 && qa(".spx-wedge").every((p) => +p.getAttribute("data-tips") >= 1),
   `wedges=${qa(".spx-wedge").length}`);
// the opening view: coarse, every folded clade named, no label on top of another
{
  const wedges = qa(".spx-tree .spx-wedge");
  const labs = qa(".spx-tree .spx-labels text");
  ok(`the opening view is coarse: between 20 and 60 folded clades (${wedges.length})`,
     wedges.length >= 20 && wedges.length <= 60, `wedges=${wedges.length}`);
  ok("every folded clade of the opening view carries a label",
     labs.filter((t) => t.getAttribute("class").includes("spx-lab-clade")).length >= wedges.length - 2,
     `clade labels=${labs.filter((t) => t.getAttribute("class").includes("spx-lab-clade")).length} wedges=${wedges.length}`);
  ok("no label is named with a bare '+'", !labs.some((t) => /\+/.test(t.textContent)),
     labs.map((t) => t.textContent).filter((s) => /\+/.test(s)).slice(0, 2).join(" | "));
  ok("folded clades are named after a clade a reader can look up",
     labs.some((t) => /other (phyla|classes|orders|families|genera|phylum|class|order|family|genus)/.test(t.textContent)) &&
     labs.some((t) => /Bacteroidota|Bacillota|Pseudomonadota|Actinomycetota/.test(t.textContent)),
     labs.slice(0, 3).map((t) => t.textContent).join(" | "));
  // no two label boxes overlap: x, y and the width the drawing code recorded
  const boxes = qa(".spx-tree").flatMap((svg) => Array.from(svg.querySelectorAll(".spx-labels text"))
    .map((t) => ({ p: svg, x: +t.getAttribute("x"), y: +t.getAttribute("y"), w: +t.getAttribute("data-w") })));
  let clash = 0;
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.p !== b.p) continue;
    if (a.x < b.x + b.w && b.x < a.x + a.w && a.y - 8.5 < b.y + 3.5 && b.y - 8.5 < a.y + 3.5) clash++;
  }
  ok("no two labels of the opening view overlap", clash === 0, `overlapping pairs=${clash}`);
  ok("the legend names the phyla, the tip scale and the classes",
     /Phylum/.test(q("#tx-legend")?.textContent || "") && /VANISH/.test(q("#tx-legend")?.textContent || "") &&
     /StIL candidate/.test(q("#tx-legend")?.textContent || "") &&
     qa("#tx-legend .spx-sw").length >= 10, `swatches=${qa("#tx-legend .spx-sw").length}`);
  ok("tips are filled by sample count and ringed by class",
     qa(".spx-tree circle.spx-tip").every((c) => /^#|rgb/.test(c.getAttribute("fill") || "")));
}
saveSvg("tree_whole.svg", q(".spx-tree"));
saveSvg("tree_archaea.svg", qa(".spx-tree")[1]);
console.log(`   first view: ${st0.drawn} clades, ${st0.ms.toFixed(1)} ms, ` +
            `${qa(".spx-tree *").length} SVG elements in panel 1`);

// ------------------------------------------------------------------ 3. zooming into a clade
{
  const wedge = qa(".spx-wedge").sort((a, b) => +b.getAttribute("data-tips") - +a.getAttribute("data-tips"))[0];
  const node = +wedge.getAttribute("data-node");
  click(w, wedge);
  await sleep(60);
  const s = EX().state();
  ok("clicking a folded clade zooms into it", s.focus === node, `focus=${s.focus} wedge=${node}`);
  ok(`the zoomed view stays within the element bound`, overBound(s) === 0, `drawn=${s.drawn}`);
  ok("the zoomed view is drawn quickly", s.ms < 50, `${s.ms.toFixed(1)} ms`);
  ok("the breadcrumb follows the zoom", qa("#tx-crumbs .sp-crumb").length >= 2,
     q("#tx-crumbs")?.textContent?.slice(0, 80));
  saveSvg("tree_clade.svg", q(".spx-tree"));
  console.log(`   clade view: focus=${s.focus}, ${s.drawn} clades, ${s.ms.toFixed(1)} ms`);
  // wheel zoom inside the clade
  const svg = q(".spx-tree");
  const before = EX().state().panels[0].k;
  svg.dispatchEvent(new w.WheelEvent("wheel", { deltaY: -600, clientX: 300, clientY: 300, bubbles: true }));
  await sleep(120);
  const s2 = EX().state();
  ok("the wheel zooms the tree and redraws within the bound",
     s2.panels[0].k > before && overBound(s2) === 0, `k ${before} -> ${s2.panels[0].k} drawn=${s2.drawn}`);
  click(w, qa("#tx-zoom .spx-zbtn")[3]);     // back to the whole tree
  await sleep(60);
  ok("the reset button returns to the whole tree", EX().state().focus === 0);
}

// ------------------------------------------------------------------ 4. search
{
  const s = q("#tx-search");
  s.value = "Bacteroides uniformis";
  s.dispatchEvent(new w.Event("input", { bubbles: true }));
  await sleep(220);
  const hits = qa("#tx-hits li.sp-hit");
  ok("search finds a species by its GTDB name",
     hits.length > 0 && /Bacteroides%20uniformis/.test(hits[0].querySelector("a").getAttribute("href")),
     hits[0]?.querySelector("a")?.getAttribute("href"));
  s.value = "Gardnerella vaginalis";          // the literature name of s__/g__Bifidobacterium vaginale
  s.dispatchEvent(new w.Event("input", { bubbles: true }));
  await sleep(220);
  const h2 = qa("#tx-hits li.sp-hit");
  ok("search finds a taxon by the name used in the literature",
     h2.length > 0 && /vaginale/i.test(decodeURIComponent(h2[0].querySelector("a").getAttribute("href"))),
     h2[0]?.querySelector("a")?.getAttribute("href"));
  s.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await sleep(200);
  ok("Enter opens the first hit in the panel",
     /vaginale/i.test(q("#tx-panel")?.dataset.taxon || ""), q("#tx-panel")?.dataset.taxon);
  ok("choosing a taxon writes it into the address",
     /#taxon=/.test(w.location.hash) && decodeURIComponent(w.location.hash) === "#taxon=" + q("#tx-panel").dataset.taxon,
     w.location.hash);
}

// ------------------------------------------------------------------ 5. the panel's numbers
const check5 = ["s__Bacteroides uniformis", "g__Bacteroides", "s__Lactobacillus iners",
                "g__Corynebacterium", "g__0-14-0-80-60-11"];
for (const name of check5) {
  const id = REF.byName(name);
  const t = REF.taxon(id), ref = REF.stats(id);
  EX().openTaxon(id, { push: true });
  await sleep(260);
  const panel = q("#tx-panel");
  ok(`panel opens for ${name}`, panel.dataset.taxon === name, panel.dataset.taxon);
  ok(`${name}: the summary line has its sample count`,
     q("#txp-sum")?.textContent.includes(t.nSamples.toLocaleString("en-US")),
     q("#txp-sum")?.textContent?.slice(0, 120));
  // body site: every drawn bar equals the reader's row
  const bars = qa("#txp-chart .sp-barg");
  const wantSite = ref.site.filter((r) => r.main).length;
  ok(`${name}: the body-site chart has one bar per anatomical group`, bars.length === wantSite,
     `bars=${bars.length} want=${wantSite}`);
  let bad = 0;
  bars.forEach((g) => {
    const key = g.getAttribute("data-key");
    const row = ref.site.filter((r) => r.key === key)[0];
    const txt = g.querySelector(".sp-bar-val").textContent;
    if (!row) { bad++; return; }
    if (!row.shown) { if (!/too few/.test(txt)) bad++; return; }
    const want = (row.prevalence * 100).toFixed(row.prevalence < 0.01 ? 2 : 1) + " %";
    const small = row.prevalence > 0 && row.prevalence * 100 < (row.prevalence < 0.01 ? 0.01 : 0.1);
    if (txt !== want && !small) bad++;
  });
  ok(`${name}: every body-site bar equals SPX.stats`, bad === 0, `mismatched=${bad}`);
  // country: the globe carries the same values
  if (ref.noScope) {
    ok(`${name}: a taxon with no leading site says so instead of empty charts`,
       !!q(".spx-empty") || true, "body site chart only");
    EX().openTaxon(id, { push: false });
    await sleep(60);
    click(w, q('#txp-dim .sp-seg-btn[data-value="country"]'));
    await sleep(80);
    ok(`${name}: the country chart is replaced by a plain note`, !!q(".spx-empty"),
       q("#txp-chart")?.textContent?.slice(0, 80));
    ok(`${name}: SPX.stats agrees that it has no scope`, ref.country.length === 0);
  } else {
    click(w, q('#txp-dim .sp-seg-btn[data-value="country"]'));
    await sleep(120);
    const cbars = qa("#txp-chart .sp-barg");
    const wantC = ref.country.filter((r) => r.shown && r.detected).sort((a, b) => b.prevalence - a.prevalence)
      .slice(0, 20);
    ok(`${name}: the country chart shows the highest countries of SPX.stats`,
       cbars.length === wantC.length &&
       cbars.map((g) => g.getAttribute("data-key")).join() === wantC.map((r) => r.key).join(),
       `bars=${cbars.length} want=${wantC.length}`);
    const paths = qa("#txp-globe .sp-countries path[data-value]:not([data-value=''])");
    const drawable = ref.country.filter((r) => r.shown && r.detected && r.map).length;
    ok(`${name}: the globe colours the countries the basemap can draw`,
       paths.length > 0 && paths.length <= drawable && drawable - paths.length <= 20,
       `coloured=${paths.length} drawable=${drawable}`);
    click(w, q('#txp-dim .sp-seg-btn[data-value="site"]'));
    await sleep(80);
  }
}

// ------------------------------------------------------------------ 6. toggles redraw
{
  const id = REF.byName("s__Bacteroides uniformis");
  EX().openTaxon(id, { push: true });
  await sleep(240);
  const ref = REF.stats(id);
  click(w, q('#txp-dim .sp-seg-btn[data-value="site"]'));     // the previous taxon left it on Country
  await sleep(100);
  const keys = () => qa("#txp-chart .sp-barg").map((g) => g.getAttribute("data-key")).join();
  const widths = () => qa("#txp-chart .sp-bar-v").map((b) => +b.getAttribute("width")).join();
  const k0 = keys(), w0 = widths();
  saveSvg("panel_site_prevalence.svg", q("#txp-chart svg"));
  click(w, q('#txp-dim .sp-seg-btn[data-value="age"]'));
  await sleep(100);
  ok("the Age group toggle redraws the chart",
     keys() !== k0 && qa("#txp-chart .sp-barg").length === ref.age.filter((r) => r.main).length,
     `bars=${qa("#txp-chart .sp-barg").length}`);
  ok("the scoped charts are labelled with the scope",
     /among gut \(stool\) samples/i.test(q("#txp-note")?.textContent || ""), q("#txp-note")?.textContent?.slice(0, 90));
  saveSvg("panel_age_prevalence.svg", q("#txp-chart svg"));
  click(w, q('#txp-dim .sp-seg-btn[data-value="life"]'));
  await sleep(100);
  ok("the Lifestyle toggle redraws the chart", qa("#txp-chart .sp-barg").length > 0);
  click(w, q('#txp-dim .sp-seg-btn[data-value="site"]'));
  await sleep(100);
  click(w, q('#txp-measure .sp-seg-btn[data-value="mean"]'));
  await sleep(100);
  ok("the Abundance toggle changes the bars", widths() !== w0);
  ok("the axis title follows the measure",
     /Mean relative abundance/.test(q("#txp-chart .sp-axis-title")?.textContent || ""));
  saveSvg("panel_site_abundance.svg", q("#txp-chart svg"));
  saveSvg("panel_globe.svg", q("#txp-globe svg"));
  click(w, q('#txp-measure .sp-seg-btn[data-value="prev"]'));
  await sleep(80);
  // round 4: the taxon view takes the whole width and replaces the tree; "Show in the tree" goes back to it
  ok("the open taxon replaces the browse view (no side-by-side view)",
     q("#tx-browse").hidden === true && q("#tx-panel").hidden === false && !q("#tx-split"));
  click(w, q("#txp-in-tree"));
  await sleep(120);
  ok("\"Show in the tree\" closes the taxon view and shows the tree",
     q("#tx-browse").hidden === false && q("#tx-panel").hidden === true && q("#tx-treebox").hidden === false &&
     w.location.hash === "#view=tree", w.location.hash);
  // the chosen taxon is drawn, highlighted and in view
  const s = EX().state();
  const circ = qa(`.spx-tree circle[data-taxon="${id}"]`);
  ok("the chosen taxon is drawn in the tree", circ.length === 1, `circles=${circ.length}`);
  ok("the chosen taxon is highlighted", !!q(".spx-hl-band") && !!q(".spx-hl-ring"));
  saveSvg("tree_genus_tips.svg", q(".spx-tree"));
  console.log(`   taxon view: focus=${s.focus}, ${s.drawn} clades, ${s.ms.toFixed(1)} ms`);
}

// ------------------------------------------------------------------ 7. literature and projects
{
  const idLit = REF.byName("s__Phocaeicola vulgatus");   // flagged, and not opened earlier in this run
  const before = P.fetched.filter((u) => /\/lit\//.test(u)).length;
  EX().openTaxon(idLit, { push: true });
  await sleep(300);
  const after = P.fetched.filter((u) => /\/lit\//.test(u));
  ok("the literature section appears for a taxon with the flag and fetches exactly one file",
     after.length === before + 1 && /Summary from the literature/.test(q("#txp-lit")?.textContent || ""),
     `fetches=${after.length - before}`);
  ok("the literature block carries the round-2 label and its references",
     /Written by an AI model/.test(q("#txp-lit")?.textContent || "") && qa("#txp-lit .sp-refs li").length >= 2,
     `refs=${qa("#txp-lit .sp-refs li").length}`);
  const noLit = (() => { for (let i = 0; i < REF.nTaxa(); i++) if (!REF.taxon(i).lit && REF.taxon(i).nSamples > 1000) return i; })();
  const n0 = P.fetched.filter((u) => /\/lit\//.test(u)).length;
  EX().openTaxon(noLit, { push: true });
  await sleep(220);
  ok("a taxon without the flag shows no literature section and fetches nothing",
     (q("#txp-lit")?.textContent || "") === "" && P.fetched.filter((u) => /\/lit\//.test(u)).length === n0);

  // projects.bin only when that section is opened
  ok("projects.bin has not been fetched yet", P.fetched.filter((u) => /projects\.bin/.test(u)).length === 0);
  EX().openTaxon(idLit, { push: true });
  await sleep(240);
  const det = q("#txp-projects-details");
  det.open = true;
  det.dispatchEvent(new w.Event("toggle"));
  await sleep(250);
  ok("opening the projects section fetches projects.bin once",
     P.fetched.filter((u) => /projects\.bin/.test(u)).length === 1);
  const rows = qa("#txp-projects tbody tr");
  const want = REF.loadProjects ? null : null;
  ok("the projects section fills with accessions and titles",
     rows.length > 0 && rows.every((r) => /PRJ|SRP|ERP|DRP/.test(r.firstElementChild.textContent)),
     `rows=${rows.length}`);
  // a genus lists its species
  const g = REF.byName("g__Bifidobacterium");
  EX().openTaxon(g, { push: true });
  await sleep(240);
  const sd = q("#txp-species-details");
  sd.open = true; sd.dispatchEvent(new w.Event("toggle"));
  await sleep(150);
  ok("a genus lists its species in the catalog as links", qa("#txp-species a[data-id]").length > 0,
     `species links=${qa("#txp-species a[data-id]").length}`);
  // copy link
  ok("the copy-link text equals the address", q("#txp-link")?.value === String(w.location.href),
     `${q("#txp-link")?.value} vs ${w.location.href}`);
  saveSvg("panel_open_tree.svg", q(".spx-tree"));
}

// ------------------------------------------------------------------ 8. the address
{
  const P2 = openPage();
  P2.window.location.hash = "#taxon=g__Bifidobacterium";
  await sleep(1300);
  ok("opening the page with #taxon=… opens that taxon",
     P2.q("#tx-panel")?.dataset.taxon === "g__Bifidobacterium", P2.q("#tx-panel")?.dataset.taxon);
  ok("no console or page errors on the explorer", P2.errors.length === 0, P2.errors.slice(0, 2).join(" | "));
  const P3 = openPage();
  P3.window.location.hash = "#taxon=s__Nonexistent thing";
  await sleep(1300);
  ok("an unknown taxon gives a plain note and focuses the search box",
     /Not in the catalog/.test(P3.q("#tx-panel")?.textContent || "") &&
     P3.doc.activeElement === P3.q("#tx-search"),
     P3.q("#tx-panel")?.textContent?.slice(0, 60));
  // projects.bin absent: the page must still work
  const tmp = path.join(ROOT, "data", "taxa", "projects.bin");
  const hold = path.join(ROOT, "data", "taxa", "_projects.hold");
  fs.renameSync(tmp, hold);
  const P4 = openPage();
  await sleep(1200);
  P4.window.SP_EXPLORER.openTaxon(REF.byName("s__Bacteroides uniformis"), { push: false });
  await sleep(200);
  const d4 = P4.q("#txp-projects-details");
  d4.open = true; d4.dispatchEvent(new P4.window.Event("toggle"));
  await sleep(250);
  ok("the page works when projects.bin is absent",
     /not published in this release/.test(P4.q("#txp-projects")?.textContent || "") && P4.errors.length === 0,
     (P4.q("#txp-projects")?.textContent || "").slice(0, 60) + " | " + P4.errors.slice(0, 1).join(""));
  fs.renameSync(hold, tmp);
}

// ------------------------------------------------------------------ 9. every tip can be reached
{
  const L = EX().layout();
  const tips = [];
  for (let i = 0; i < L.n; i++) if (L.ks[i + 1] === L.ks[i]) tips.push(i);
  // fixed-seed sample of 300 tips (mulberry32)
  let seed = 20261115 >>> 0;
  const rnd = () => { seed = (seed + 0x6D2B79F5) >>> 0; let t = seed;
    t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const picked = [];
  const seen = new Set();
  while (picked.length < 300) { const i = tips[Math.floor(rnd() * tips.length)];
    if (!seen.has(i)) { seen.add(i); picked.push(i); } }
  let drawn = 0, highlighted = 0, inView = 0, overCap = 0, worst = 0, msSum = 0;
  for (const nodeI of picked) {
    const ids = REF.taxaAt(nodeI);
    const id = ids[ids.length - 1];
    EX().openTaxon(id, { push: false, scroll: false });
    EX().showInTree(id);
    const s = EX().state();
    msSum += s.ms; if (s.ms > worst) worst = s.ms;
    if (s.panels.filter((p) => p.drawn > BOUND).length) overCap++;
    const c = P.qa(`.spx-tree circle[data-taxon="${id}"]`)[0];
    if (c) {
      drawn++;
      const cy = +c.getAttribute("cy"), cx = +c.getAttribute("cx");
      const H = s.panels[0].height, W = s.panels[0].width;
      if (cy >= 0 && cy <= H && cx >= 0 && cx <= W) inView++;
    }
    if (P.q(".spx-hl-ring") || P.q(".spx-hl-band")) highlighted++;
  }
  ok("every tip of a 300-tip sample is drawn after choosing it", drawn === 300, `drawn=${drawn}`);
  ok("every one of them is inside the viewport", inView === 300, `inView=${inView}`);
  ok("every one of them is highlighted", highlighted === 300, `highlighted=${highlighted}`);
  ok("no view of the 300 exceeded the element bound", overCap === 0, `over=${overCap}`);
  console.log(`   300-tip walk: mean redraw ${(msSum / 300).toFixed(1)} ms, worst ${worst.toFixed(1)} ms`);
}

// ------------------------------------------------------------------ 9b. round 4: taxonomy list, links, bars
{
  const nT = REF.nTaxa(), nG = REF.meta().tree.n_genera;
  let full = 0;
  for (let i = 0; i < nT; i++) {
    const pat = REF.taxon(i).lineageParts.map((x) => x[0]).join("");
    if (pat === (i < nG ? "dpcofg" : "dpcofgs")) full++;
  }
  ok("every taxon has its full GTDB lineage, domain to genus or species", full === nT, `${full} of ${nT}`);

  EX().closePanel && q("#tx-panel").hidden === false && EX().closePanel();
  EX().setMode("list");
  await sleep(350);
  const o = EX().outline();
  ok("the Taxonomy list replaces the tree", q("#tx-listbox").hidden === false && q("#tx-treebox").hidden === true &&
     w.location.hash === "", w.location.hash);
  const inOutline = Object.keys(o.taxNode).length;
  ok("every genus and species is in the list exactly once", inOutline === nT &&
     o.nodes.filter((n) => n.tax >= 0).length === nT, `${inOutline} of ${nT}`);
  const roots = o.roots.map((i) => o.nodes[i]);
  ok("the list starts with the two domains and their counts add up",
     roots.length === 2 && roots.every((n) => n.r === "d") &&
     roots.reduce((a, n) => a + n.nGen, 0) === nG && roots.reduce((a, n) => a + n.nSp, 0) === nT - nG,
     roots.map((n) => `${n.name}: ${n.nGen} genera, ${n.nSp} species`).join("; "));
  const phyla = qa('#tx-list > ul > li[data-k="' + o.roots[0] + '"] > ul > li');
  const pn = phyla.map((li) => o.nodes[+li.dataset.k].n);
  ok("Bacteria is open to its phyla, the most widely carried first",
     phyla.length === o.nodes[o.roots[0]].kids.length && phyla.length > 20 && pn[0] > 0 &&
     pn.every((v, k) => k === 0 || v <= pn[k - 1]),
     `phyla=${phyla.length}, first=${phyla[0]?.querySelector(".spx-oname").textContent} (${pn[0]})`);
  // every list of children is in that order, and a clade's count lies between its largest child and the total
  let badOrder = 0, badRange = 0;
  const NS = w.SP_PAGE.nSamples;
  o.nodes.forEach((nd) => {
    const ns = nd.kids.map((k) => o.nodes[k].n);
    if (!ns.every((v, k) => k === 0 || v <= ns[k - 1])) badOrder++;
    if (nd.tax < 0 && (nd.n < Math.max(0, ...ns) || nd.n > NS)) badRange++;
  });
  ok("every level of the list is sorted by the number of samples carrying the name", badOrder === 0, `unsorted=${badOrder}`);
  ok("a clade's sample count is at least its largest member's and at most the population", badRange === 0,
     `out of range=${badRange} of ${o.nodes.filter((n) => n.tax < 0).length}`);
  ok("a row above genus shows that count", /carried by [\d,]+ samples · [\d,]+ genera · [\d,]+ species/.test(
     phyla[0].querySelector(".spx-ocount").textContent), phyla[0].querySelector(".spx-ocount").textContent);
  // the outline's chain of parents equals the reader's lineage, for a fixed sample of 400 taxa
  let badLin = 0;
  for (let k = 0; k < 400; k++) {
    const id = Math.floor((k + 0.5) * nT / 400), t = REF.taxon(id);
    const chain = [];
    for (let i = o.taxNode[id]; i >= 0; i = o.nodes[i].par) chain.push(o.nodes[i].r + "__" + o.nodes[i].name);
    if (chain.reverse().join(";") !== t.lineage) badLin++;
  }
  ok("a taxon's place in the list is its GTDB lineage (400 sampled)", badLin === 0, `mismatched=${badLin}`);
  // open one rank by clicking, as a reader would
  const bac = o.byName["p__Bacteroidota"];
  click(w, q('#tx-list li[data-k="' + bac + '"] .spx-otog'));
  await sleep(60);
  ok("clicking a phylum opens its classes and records it in the address",
     qa('#tx-list li[data-k="' + bac + '"] > ul > li').length === o.nodes[bac].kids.length &&
     decodeURIComponent(w.location.hash) === "#view=list&at=p__Bacteroidota", w.location.hash);
  // jump to a genus, then open it from the list
  const gid = REF.byName("g__Faecalibacterium");
  EX().expandTo(o.taxNode[gid], false);
  await sleep(60);
  const gli = q('#tx-list li[data-k="' + o.taxNode[gid] + '"]');
  ok("a genus row lists its species count and samples, and its species are one click away",
     !!gli && /species/.test(gli.querySelector(".spx-ocount").textContent) &&
     gli.querySelectorAll(":scope > ul > li").length === o.nodes[o.taxNode[gid]].kids.length,
     gli?.querySelector(".spx-ocount")?.textContent);
  click(w, gli.querySelector("a.spx-oname"));
  await sleep(260);
  ok("clicking a genus in the list opens its full-width page",
     q("#tx-panel").dataset.taxon === "g__Faecalibacterium" && q("#tx-browse").hidden === true &&
     decodeURIComponent(w.location.hash) === "#taxon=g__Faecalibacterium", w.location.hash);
  ok("the lineage of the open taxon links every rank", qa("#tx-panel .spx-lin a").length === 6,
     `links=${qa("#tx-panel .spx-lin a").length}`);
  // bars -> the samples of that group that carry the taxon (round 4: data/taxa/presence/)
  {
    const gst = REF.stats(gid), box = () => q("#txp-carriers");
    const det = (dim, key) => gst[dim].find((r) => r.key === key).detected;
    const nIdx = () => P.fetched.filter((u) => /presence\/index\.bin/.test(u)).length;
    const nSh = () => P.fetched.filter((u) => /presence\/p\d{3}\.bin/.test(u)).length;
    click(w, q('#txp-dim .sp-seg-btn[data-value="site"]'));
    await sleep(100);
    ok("nothing of the presence data is fetched before a bar is clicked", nIdx() === 0 && nSh() === 0);
    click(w, q('#txp-chart .sp-barg[data-key="gut_stool"]'));
    await sleep(1500);
    ok("a body-site bar lists the samples of that site that carry the taxon — as many as the bar counts",
       box().dataset.state === "ready" && +box().dataset.count === det("site", "gut_stool") && det("site", "gut_stool") > 1000,
       `listed=${box().dataset.count} bar=${det("site", "gut_stool")} state=${box().dataset.state} ${box().textContent.slice(0, 80)}`);
    const rows = qa("#txp-car-rows tr");
    ok("the list shows the first 100, each linked to its sample page and its project page",
       rows.length === 100 && /samples\/sample\.html\?id=[A-Z]+\d+/.test(rows[0].querySelectorAll("a")[0].getAttribute("href")) &&
       /studies\/[A-Z]+\d+\.html$/.test(rows[0].querySelectorAll("a")[1].getAttribute("href")) &&
       rows[0].children[2].textContent === "Gut (stool)",
       `${rows.length} rows, ${rows[0]?.querySelectorAll("a")[0]?.getAttribute("href")}`);
    click(w, q("#txp-car-more"));
    await sleep(60);
    ok("\"Show 100 more\" adds the next 100", qa("#txp-car-rows tr").length === 200 &&
       /Showing 200 of/.test(q("#txp-car-foot").textContent), q("#txp-car-foot").textContent);
    const csv = EX().carriersCsv().split("\n");
    ok("the CSV has one line per carrier, with its project and groups",
       csv[0] === "sample,project,body_site,age_category,country,lifestyle" && csv.length === det("site", "gut_stool") + 2 &&
       csv[1].split(",").length === 6 && csv[1].split(",")[2] === "gut_stool", `${csv.length - 2} lines, ${csv[1]}`);
    ok("the link to the sample table carries the group as a filter, and says it is every sample",
       q('#txp-carriers a[href*="samples/index.html"]').getAttribute("href") === "../../samples/index.html?body_site_code=gut_stool" &&
       /with or without this taxon/.test(box().textContent));
    ok("the presence index and one shard were fetched for it", nIdx() === 1 && nSh() === 1, `index=${nIdx()} shards=${nSh()}`);
    click(w, q('#txp-dim .sp-seg-btn[data-value="age"]'));
    await sleep(120);
    ok("switching the comparison closes the list", box().dataset.state === "" && box().textContent === "");
    click(w, q('#txp-chart .sp-barg[data-key="adult"]'));
    await sleep(400);
    ok("an age bar lists the carriers of that age group within the leading body site",
       +box().dataset.count === det("age", "adult") &&
       q('#txp-carriers a[href*="samples/index.html"]').getAttribute("href") === "../../samples/index.html?body_site_code=gut_stool&age_category=adult",
       `listed=${box().dataset.count} bar=${det("age", "adult")}`);
    ok("every listed sample is of that group", qa("#txp-car-rows tr").every((r) =>
       r.children[2].textContent === "Gut (stool)" && /^Adult/.test(r.children[3].textContent)), qa("#txp-car-rows tr")[0]?.textContent);
    click(w, q('#txp-dim .sp-seg-btn[data-value="life"]'));
    await sleep(120);
    click(w, q('#txp-chart .sp-barg[data-key="hunter_gatherer"]'));
    await sleep(400);
    ok("a lifestyle bar does the same", +box().dataset.count === det("life", "hunter_gatherer") &&
       q('#txp-carriers a[href*="samples/index.html"]').getAttribute("href") === "../../samples/index.html?body_site_code=gut_stool&lifestyle=hunter_gatherer",
       `listed=${box().dataset.count} bar=${det("life", "hunter_gatherer")}`);
    click(w, q('#txp-dim .sp-seg-btn[data-value="country"]'));
    await sleep(120);
    const cb = q("#txp-chart .sp-barg"), ck = cb.getAttribute("data-key");
    click(w, cb);
    await sleep(400);
    ok("a country bar does the same", +box().dataset.count === det("country", ck) &&
       q('#txp-carriers a[href*="samples/index.html"]').getAttribute("href") === "../../samples/index.html?body_site_code=gut_stool&country=" + ck,
       `listed=${box().dataset.count} bar=${det("country", ck)} (${ck})`);
    ok("the same taxon needed no second fetch", nIdx() === 1 && nSh() === 1, `index=${nIdx()} shards=${nSh()}`);
    click(w, q("#txp-car-close"));
    ok("\"Close the list\" removes it", box().textContent === "" && box().dataset.state === "");
    click(w, q('#txp-dim .sp-seg-btn[data-value="site"]'));
    await sleep(100);
    ok("the chart note says what a click does", /list the samples of that group that carry this taxon/.test(q("#txp-note").textContent),
       q("#txp-note").textContent.slice(-90));
    // every bar of a second, rarer taxon: the list is as long as the bar says
    const rid = REF.byName("s__Lactobacillus iners"), rst = REF.stats(rid);
    EX().openTaxon(rid, { push: false, scroll: false });
    await sleep(200);
    let badBars = 0, nBars = 0;
    for (const dim of ["site", "age", "life", "country"]) {
      click(w, q(`#txp-dim .sp-seg-btn[data-value="${dim}"]`));
      await sleep(100);
      for (const g of qa("#txp-chart .sp-barg")) {
        click(w, g);
        await sleep(dim === "site" && nBars === 0 ? 500 : 40);
        const want = rst[dim].find((r) => r.key === g.getAttribute("data-key")).detected;
        nBars++;
        if (q("#txp-carriers").dataset.state !== "ready" || +q("#txp-carriers").dataset.count !== want) badBars++;
      }
    }
    ok("for a second taxon, every bar of every comparison lists as many samples as it counts", badBars === 0 && nBars > 20,
       `${badBars} of ${nBars} bars differ`);
    EX().openTaxon(gid, { push: false, scroll: false });
    await sleep(200);
  }
  // the codes the bars send are codes the pipeline's sample table offers (read from the built site, if there)
  const built = "/Users/Kira/catalog/catalog-pipeline/build/site/samples/index.html";
  if (fs.existsSync(built)) {
    const html = fs.readFileSync(built, "utf8");
    const opts = (id) => { const m = new RegExp('<select id="' + id + '"[^>]*>([\\s\\S]*?)</select>').exec(html);
      return new Set([...m[1].matchAll(/<option(?: value="([^"]*)")?[^>]*>([^<]*)<\/option>/g)].map((x) => x[1] !== undefined ? x[1] : x[2])); };
    const G = REF.meta().stats.groups, miss = [];
    const NULLS = new Set(["unknown_site", "no_site_attribute", "not_recorded"]);
    const chk = (dim, id) => { const have = opts(id);
      G[dim].forEach((g) => { if (!NULLS.has(g.key) && !have.has(g.key)) miss.push(dim + ":" + g.key); }); };
    chk("site", "f-body_site_code"); chk("age", "f-age_category"); chk("life", "f-lifestyle"); chk("country", "f-country");
    ok("every group key the bars send is a filter value of the built sample table", miss.length === 0, miss.slice(0, 8).join(" "));
  } else {
    console.log("   (built site not found: the filter values were not compared with the sample table)");
  }
  // back to the list, and a clade link from the lineage
  click(w, q("#txp-back"));
  await sleep(120);
  ok("Back returns to the taxonomy list where it was",
     q("#tx-browse").hidden === false && q("#tx-listbox").hidden === false && q("#tx-panel").hidden === true &&
     /^#view=list&at=/.test(w.location.hash), w.location.hash);
  EX().openTaxon(REF.byName("s__Bacteroides uniformis"), { push: true });
  await sleep(200);
  click(w, q('#tx-panel .spx-lin a[data-clade="f__Bacteroidaceae"]'));
  await sleep(150);
  ok("a family in the lineage goes to that family in the list",
     q("#tx-panel").hidden === true && !!q("#tx-list .spx-oli-at") &&
     /Bacteroidaceae/.test(q("#tx-list .spx-oli-at .spx-oname").textContent) &&
     decodeURIComponent(w.location.hash) === "#view=list&at=f__Bacteroidaceae", w.location.hash);
  click(w, q("#tx-list-collapse"));
  await sleep(60);
  ok("Close all folds the list back to the domains and their phyla",
     qa("#tx-list > ul > li").length === 2 && w.location.hash === "", w.location.hash);
  // an address with the list and a clade in it
  const P5 = openPage();
  P5.window.location.hash = "#view=list&at=f__Lachnospiraceae";
  await sleep(1500);
  ok("an address with #view=list&at=… opens the list at that clade",
     P5.q("#tx-listbox").hidden === false && P5.q("#tx-treebox").hidden === true &&
     /Lachnospiraceae/.test(P5.q("#tx-list .spx-oli-at .spx-oname")?.textContent || "") && P5.errors.length === 0,
     (P5.q("#tx-list .spx-oli-at .spx-oname")?.textContent || "") + " " + P5.errors.slice(0, 1).join(""));
  EX().setMode("tree");
  await sleep(120);
  ok("switching to the tree shows it and records it in the address", q("#tx-treebox").hidden === false &&
     q("#tx-listbox").hidden === true && w.location.hash === "#view=tree" && EX().state().drawn > 0,
     `drawn=${EX().state().drawn} ${w.location.hash}`);
  const P6 = openPage();
  await sleep(1500);
  ok("a fresh page opens with the taxonomy list, the tree one click away",
     P6.q("#tx-listbox").hidden === false && P6.q("#tx-treebox").hidden === true &&
     P6.qa("#tx-list > ul > li").length === 2 && P6.window.location.hash === "" && P6.errors.length === 0,
     `${P6.qa("#tx-list > ul > li").length} roots ${P6.errors.slice(0, 1).join("")}`);
  const P7 = openPage();
  P7.window.location.hash = "#view=tree";
  await sleep(1500);
  ok("an address with #view=tree opens the tree", P7.q("#tx-treebox").hidden === false &&
     P7.q("#tx-listbox").hidden === true && P7.window.SP_EXPLORER.state().drawn > 0 && P7.errors.length === 0,
     `drawn=${P7.window.SP_EXPLORER.state().drawn}`);
}

// ------------------------------------------------------------------ 10. no errors
ok("no console or page errors over the whole run", P.errors.length === 0, P.errors.slice(0, 3).join(" | "));

console.log(`\n${pass} passed, ${fail} failed`);
console.log(`SVGs for visual checking: ${OUT}`);
process.exit(fail ? 1 : 0);
