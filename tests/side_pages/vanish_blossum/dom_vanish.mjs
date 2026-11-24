// dom_vanish.mjs — headless test of the VANISH/BloSSUM page: no browser, no bundler.
//
// Loads the rendered preview page into jsdom over an http://localhost base URL with a fetch shim that reads
// the preview folder from disk, evaluates the real vendored d3 and the real static/sp_core.js and
// static/sp_vanish.js, and asserts that the plot, the toggles, the search, the hover and the click-through do
// what the page promises. It writes the drawn SVG of each view to tests/out/*.svg so it can be rasterised and
// looked at (README: the CairoSVG step).
//
//   NODE_PATH=<side_pages>/work/node/node_modules node for_editor/vanish_blossum/tests/dom_vanish.mjs [root]
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
const { JSDOM, VirtualConsole } = createRequire(import.meta.url)("jsdom");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SIDE = path.dirname(path.dirname(path.dirname(HERE)));
const ROOT = process.argv[2] || path.join(SIDE, "previews", "site_r3");
const OUT = path.join(HERE, "out");
fs.mkdirSync(OUT, { recursive: true });
const BASE = "http://localhost:8765";
const REL = "atlas/vanish.html";

let pass = 0, fail = 0;
const ok = (name, cond, extra = "") => {
  if (cond) { pass++; console.log(`✓  ${name}`); }
  else { fail++; console.log(`✗ FAIL  ${name}${extra ? "  — " + extra : ""}`); }
};
const brief = (e) => String(e?.stack || e?.message || e).split("\n").slice(0, 3).join(" ⏎ ").slice(0, 300);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const payload = (f) => JSON.parse(fs.readFileSync(path.join(ROOT, "data", "vanish", f), "utf8"));

function openPage() {
  const file = path.join(ROOT, REL);
  const raw = fs.readFileSync(file, "utf8");
  const html = raw.replace(/<script[\s\S]*?<\/script>/g, "");
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => { if (!/Not implemented/.test(String(e))) errors.push("jsdom: " + brief(e)); });
  vc.on("error", (...a) => errors.push("console.error: " + a.map(brief).join(" ")));
  const dom = new JSDOM(html, { url: `${BASE}/${REL}`, runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc });
  const { window } = dom;
  window.addEventListener("error", (e) => errors.push("window.error: " + (e.error?.stack || e.message)));
  window.fetch = async (url) => {
    const u = new URL(String(url), `${BASE}/${REL}`);
    const p = path.join(ROOT, decodeURIComponent(u.pathname));
    if (!fs.existsSync(p)) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => JSON.parse(fs.readFileSync(p, "utf8")) };
  };
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  window.eval(fs.readFileSync(path.join(ROOT, "static", "vendor", "d3-7.9.0.min.js"), "utf8"));
  const cfg = raw.match(/window\.SP_PAGE=\{[\s\S]*?\};/);
  if (cfg) window.eval(cfg[0]);
  window.eval(fs.readFileSync(path.join(ROOT, "static", "sp_core.js"), "utf8"));
  const nav = [];
  window.SP.go = (u) => nav.push(u);
  window.eval(fs.readFileSync(path.join(ROOT, "static", "sp_vanish.js"), "utf8"));
  return { window, doc: window.document, errors, nav,
           q: (s) => window.document.querySelector(s), qa: (s) => Array.from(window.document.querySelectorAll(s)) };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const move = (w, el) => el.dispatchEvent(new w.MouseEvent("mousemove", { bubbles: true, clientX: 300, clientY: 200 }));
const seg = (p, id, value) => {
  const b = p.qa(`#${id} button`).find((x) => x.dataset.value === value);
  if (b) click(p.window, b);
  return !!b;
};
const saveSvg = (p, name) => {
  const svg = p.q("#vb-plot svg");
  if (svg) fs.writeFileSync(path.join(OUT, name + ".svg"), svg.outerHTML);
};

const P = openPage();
await sleep(60);
const meta = payload("meta.json");
const spAdultModel = payload("points_species_adult_model.json");

ok("page has the plot container and the controls",
   !!P.q("#vb-plot") && !!P.q("#vb-level button") && !!P.q("#vb-rule button") && !!P.q("#vb-stratum button"));
ok("at most two sentences above the plot", (P.q(".sp-lead")?.textContent.match(/\./g) || []).length <= 2,
   P.q(".sp-lead")?.textContent.slice(0, 80));
ok("no chart image anywhere in the page", P.qa("#main-chart img").length === 0);

const nC1 = spAdultModel.blocks.C1.n_tested;
ok(`species × adults × model draws one point per tested taxon (${nC1})`, P.qa("#vb-plot circle.sp-pt").length === nC1,
   `drew ${P.qa("#vb-plot circle.sp-pt").length}`);
ok("points carry positions", P.qa("#vb-plot circle.sp-pt").every((c) => c.getAttribute("cx") && c.getAttribute("cy")));
ok("the count line names both classes and the total",
   /VANISH.*BloSSUM.*of .* taxa tested/.test(P.q("#vb-count").textContent), P.q("#vb-count").textContent);
saveSvg(P, "species_adult_model");

const countBefore = P.q("#vb-count").textContent;
seg(P, "vb-level", "genus"); await sleep(40);
const genus = payload("points_genus_adult_model.json");
ok(`Level → genus redraws (${genus.blocks.C1.n_tested} points)`, P.qa("#vb-plot circle.sp-pt").length === genus.blocks.C1.n_tested,
   `drew ${P.qa("#vb-plot circle.sp-pt").length}`);
ok("Level → genus changes the count line", P.q("#vb-count").textContent !== countBefore);
seg(P, "vb-level", "species"); await sleep(40);

seg(P, "vb-ref", "C2"); await sleep(40);
ok(`Reference group → stated redraws (${spAdultModel.blocks.C2.n_tested} points)`,
   P.qa("#vb-plot circle.sp-pt").length === spAdultModel.blocks.C2.n_tested);
seg(P, "vb-ref", "C1"); await sleep(40);

seg(P, "vb-rule", "simple"); await sleep(400);
const simple = payload("points_species_adult_simple.json");
const sc = simple.blocks.S.cols;
// the page draws a taxon when it is carried by at least 1 % of either group, or when it is classed
const drawn = sc.n.filter((_, i) => Math.max(sc.pn[i], sc.pi[i]) >= 0.01 || sc.k[i]).length;
ok(`Rule → simple draws the taxa above the display threshold (${drawn} of ${simple.blocks.S.n_tested})`,
   P.qa("#vb-plot circle.sp-pt").length === drawn, `drew ${P.qa("#vb-plot circle.sp-pt").length}`);
ok("the note says how many taxa are not drawn and where they are",
   new RegExp(`${(simple.blocks.S.n_tested - drawn).toLocaleString("en-US")} taxa .* not drawn`).test(P.q("#vb-note").textContent)
   && /CSV/.test(P.q("#vb-note").textContent), P.q("#vb-note").textContent);
ok("the count line still reports every tested taxon, StIL candidates included",
   P.q("#vb-count").textContent === `${simple.blocks.S.n_vanish.toLocaleString("en-US")} VANISH, ` +
   `${simple.blocks.S.n_blossum.toLocaleString("en-US")} BloSSUM, ` +
   `${simple.blocks.S.n_stil.toLocaleString("en-US")} StIL candidates of ` +
   `${simple.blocks.S.n_tested.toLocaleString("en-US")} taxa tested`, P.q("#vb-count").textContent);
ok(`the simple rule draws the StIL candidates in their own colour (${simple.blocks.S.n_stil})`,
   P.qa("#vb-plot circle.sp-pt-S").length === simple.blocks.S.n_stil &&
   P.qa("#vb-plot circle.sp-pt-S").every((x) => x.getAttribute("fill") === "#8A4A1A"),
   `${P.qa("#vb-plot circle.sp-pt-S").length} StIL points`);
ok("classed points are drawn last and carry an explicit fill",
   (() => { const c = P.qa("#vb-plot circle.sp-pt");
            const last = c.slice(-simple.blocks.S.n_vanish - simple.blocks.S.n_blossum - simple.blocks.S.n_stil);
            return last.every((x) => x.getAttribute("fill") && x.getAttribute("fill") !== "#A2A4A3"); })());
// published-list marks: off by default, the control rings exactly the taxa a published list names
const ringed = () => P.qa("#vb-plot circle.sp-pt").filter((x) => x.style.stroke).length;
const nPub = sc.pb.filter((v, i) => v && (Math.max(sc.pn[i], sc.pi[i]) >= 0.01 || sc.k[i])).length;
ok("published-list marks are off by default", ringed() === 0, `${ringed()} ringed`);
seg(P, "vb-pub", "on"); await sleep(300);
ok(`Published lists → Mark rings the published taxa (${nPub})`, ringed() === nPub, `${ringed()} ringed`);
saveSvg(P, "species_adult_simple_published");
seg(P, "vb-pub", "off"); await sleep(300);
ok("Published lists → Not marked removes every ring", ringed() === 0, `${ringed()} ringed`);
ok("the simple rule hides the reference-group toggle", P.q("#vb-ref-wrap").hidden === true);
ok("the simple rule colours VANISH and BloSSUM points",
   P.qa("#vb-plot circle.sp-pt-V").length === simple.blocks.S.n_vanish &&
   P.qa("#vb-plot circle.sp-pt-B").length === simple.blocks.S.n_blossum,
   `${P.qa("#vb-plot circle.sp-pt-V").length}/${P.qa("#vb-plot circle.sp-pt-B").length} vs ${simple.blocks.S.n_vanish}/${simple.blocks.S.n_blossum}`);
saveSvg(P, "species_adult_simple");
seg(P, "vb-rule", "model"); await sleep(60);

seg(P, "vb-stratum", "infant"); await sleep(60);
const inf = payload("points_species_infant_model.json");
const infEst = inf.blocks.C1.estimable && inf.blocks.C1.n_tested > 0;
// round 4: where the main definition cannot be fitted for infants, the broad definition (C4) is drawn
const infAlt = !infEst && inf.blocks.C4 && inf.blocks.C4.estimable && inf.blocks.C4.n_tested > 0;
ok("infants × model: points of the main definition, or of the broad one with a note, or a plain not-estimable note",
   infEst ? P.qa("#vb-plot circle.sp-pt").length === inf.blocks.C1.n_drawn
     : infAlt ? (P.qa("#vb-plot circle.sp-pt").length === inf.blocks.C4.n_drawn &&
                 P.q("#vb-plot").dataset.block === "C4" && /broad definition/.test(P.q("#vb-note").textContent))
              : (P.qa("#vb-plot circle.sp-pt").length === 0 && /not estimable/i.test(P.q("#vb-note").textContent)),
   `${P.qa("#vb-plot circle.sp-pt").length} points · ${P.q("#vb-note").textContent.slice(0, 90)}`);
if (infAlt) {
  ok("infants × model: the classes drawn are the broad definition's",
     P.qa("#vb-plot circle.sp-pt-V").length === inf.blocks.C4.n_vanish &&
     P.qa("#vb-plot circle.sp-pt-B").length === inf.blocks.C4.n_blossum &&
     new RegExp("^" + inf.blocks.C4.n_vanish + " VANISH, " + inf.blocks.C4.n_blossum + " BloSSUM").test(P.q("#vb-count").textContent),
     P.q("#vb-count").textContent);
  ok("infants × model: the reference-group switch is hidden, since there is no choice",
     P.q("#vb-ref-wrap").hidden === true);
  saveSvg(P, "species_infant_model");
  for (const lev of ["genus", "family"]) {
    seg(P, "vb-level", lev); await sleep(80);
    const f = payload(`points_${lev}_infant_model.json`);
    ok(`infants × model × ${lev}: drawn from the broad definition`,
       P.qa("#vb-plot circle.sp-pt").length === f.blocks.C4.n_drawn && P.q("#vb-plot").dataset.block === "C4",
       `${P.qa("#vb-plot circle.sp-pt").length} vs ${f.blocks.C4.n_drawn}`);
  }
  seg(P, "vb-level", "species"); await sleep(80);
}
seg(P, "vb-stratum", "adult"); await sleep(60);

const names = spAdultModel.blocks.C1.cols.n;
const target = names.find((n) => /Prevotella|Bacteroides|Blautia/.test(n)) || names[0];
const input = P.q("#vb-search input");
input.value = String(target).replace(/^[a-z]__/, "").slice(0, 18);
input.dispatchEvent(new P.window.Event("input", { bubbles: true }));
await sleep(200);
const hits = P.qa("#vb-search .sp-hit");
ok(`search lists a hit for ${target}`, hits.length > 0, `hits ${hits.length}`);
if (hits.length) { click(P.window, hits[0]); await sleep(60); }
// round 4: the search marks the taxon with a star and its name; nothing is zoomed, dimmed or moved
{
  const stars = P.qa("#vb-plot path.sp-star"), c0 = P.qa("#vb-plot circle.sp-pt").find((x) => x.dataset.name === target);
  const tr = stars[0] ? stars[0].getAttribute("transform") : "";
  ok("the searched taxon is marked with one star, at its own point",
     stars.length === 1 && stars[0].dataset.name === target &&
     tr === `translate(${c0.getAttribute("cx")},${c0.getAttribute("cy")})`, `${stars.length} stars · ${tr}`);
  ok("the star carries the taxon's name", (P.q("#vb-plot .sp-star-label")?.textContent || "") === String(target).replace(/^[a-z]__/, ""),
     P.q("#vb-plot .sp-star-label")?.textContent);
  const z = P.window.d3.zoomTransform(P.q("#vb-plot svg"));
  ok("the search leaves the view whole: no zoom, no dimmed points",
     z.k === 1 && z.x === 0 && z.y === 0 && P.qa("#vb-plot circle.sp-pt.dim").length === 0 &&
     P.qa("#vb-plot circle.sp-pt").length === spAdultModel.blocks.C1.n_drawn, `k=${z.k} x=${z.x} y=${z.y}`);
  saveSvg(P, "species_adult_model_star");
  seg(P, "vb-rule", "simple"); await sleep(120);
  ok("the star follows the taxon into another view", P.qa("#vb-plot path.sp-star").length ===
     (P.window.SP_VANISH.points().some((o) => o.n === target) ? 1 : 0), `${P.qa("#vb-plot path.sp-star").length} stars`);
  seg(P, "vb-rule", "model"); await sleep(120);
  input.value = "";
  input.dispatchEvent(new P.window.Event("input", { bubbles: true }));
  await sleep(120);
  ok("emptying the search box removes the star", P.qa("#vb-plot path.sp-star").length === 0);
}

// round 3: a click on a genus or species opens the taxon explorer at that taxon; no taxa/index.json
ok("the page no longer refers to the old taxon index", !/data\/taxa\/index\.json/.test(
   fs.readFileSync(path.join(ROOT, "static", "sp_vanish.js"), "utf8") +
   fs.readFileSync(path.join(ROOT, REL), "utf8")));
{
  const target2 = names.find((n) => /Prevotella|Bacteroides|Blautia/.test(n)) || names[0];
  const c = P.qa("#vb-plot circle.sp-pt").find((x) => x.dataset.name === target2);
  click(P.window, c);
  await sleep(30);
  ok(`click on ${target2} opens the explorer at that taxon`,
     P.nav[P.nav.length - 1] === `taxa/index.html#taxon=${encodeURIComponent(target2)}`,
     P.nav[P.nav.length - 1]);
  ok("the explorer page the click opens exists in the preview",
     fs.existsSync(path.join(ROOT, "atlas", "taxa", "index.html")));
}
seg(P, "vb-level", "family"); await sleep(120);
{
  const before = P.nav.length;
  click(P.window, P.qa("#vb-plot circle.sp-pt")[0]);
  await sleep(30);
  ok("a family does not navigate; it pins its details instead",
     P.nav.length === before && P.qa("#vb-plot circle.sp-pt.on").length === 1,
     `${P.nav.length - before} navigations, ${P.qa("#vb-plot circle.sp-pt.on").length} pinned`);
}
seg(P, "vb-level", "species"); await sleep(120);

move(P.window, P.qa("#vb-plot circle.sp-pt")[0]);
await sleep(30);
const tipHtml = P.q(".sp-tip")?.innerHTML || "";
ok("hover shows name, score, q, prevalence and class",
   /score/.test(tipHtml) && /q /.test(tipHtml) && /detected in/.test(tipHtml) && /(VANISH|BloSSUM|neither)/.test(tipHtml),
   tipHtml.slice(0, 120));

// the held classes run: the StIL section says so under the model rule, with the number waiting
{
  const txt = P.qa("details.sp-more").map((d) => d.textContent).join(" ");
  // round 4: once the classes run has been made, the section gives the model rule's counts instead
  const a1 = (lev) => meta.analyses.find((x) => x.level === lev && x.contrast === "C1" && x.stratum === "adult");
  ok("the StIL section names the paper and says where the model rule's class stands",
     /10\.1038\/s41586-026-11106-1/.test(txt) && /10\.1101\/2025\.08\.15\.670570/.test(txt) &&
     (meta.stil.computed_model_rule
       ? (!/not computed in this build/.test(txt) &&
          new RegExp(`${a1("species").n_stil} species, ${a1("genus").n_stil} genera and ${a1("family").n_stil} famil`).test(txt))
       : (/not computed in this build/.test(txt) &&
          new RegExp(`${meta.stil.pending_model_rule_total} taxa`).test(txt))),
     `computed=${meta.stil.computed_model_rule}`);
  if (meta.stil.computed_model_rule) {
    const f = payload("points_species_adult_model.json");
    seg(P, "vb-stratum", "adult"); seg(P, "vb-rule", "model"); seg(P, "vb-level", "species"); await sleep(120);
    ok("the model rule draws its StIL candidates", f.blocks.C1.n_stil > 0 &&
       P.qa("#vb-plot circle.sp-pt-S").length === f.blocks.C1.n_stil &&
       new RegExp(`${f.blocks.C1.n_stil} StIL candidates`).test(P.q("#vb-count").textContent),
       `${P.qa("#vb-plot circle.sp-pt-S").length} vs ${f.blocks.C1.n_stil}`);
  }
  ok("the groups section says which HDI table the results stand on",
     /HDI table (as it stood at the run of|of the package it was computed on)/.test(txt) && /Taiwan/.test(txt));   // R2026.42: a full run reads the release's own table
}
// round 3: the point files carry only the drawn points; every tested taxon stays in the CSVs
{
  const dir = path.join(ROOT, "data", "vanish");
  const fls = fs.readdirSync(dir).filter((f) => /^points_.*\.json$/.test(f));
  let okSize = true, big = "";
  for (const f of fls) {
    const sz = fs.statSync(path.join(dir, f)).size;
    if (sz > 200000) { okSize = false; big = `${f} ${sz}`; }
    const j = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
    for (const b of Object.values(j.blocks)) {
      if (b.estimable === false) continue;
      if ((b.cols[j.fields[0]] || []).length !== b.n_drawn) { okSize = false; big = `${f} points ≠ n_drawn`; }
    }
  }
  ok(`each of the ${fls.length} point files holds only the drawn points and stays under 200 kB`, okSize, big);
}
ok("every collapsed section is present",
   P.qa("details.sp-more").length >= 7, `${P.qa("details.sp-more").length} sections`);
ok("downloads link to the payload files",
   P.qa('details a[href*="data/vanish/"]').length >= meta.downloads.length - 1);
ok("no console or page errors", P.errors.length === 0, P.errors.slice(0, 2).join(" | "));

// ============================================================ round 5: the response to the critique
// State (iii), both payload files present: the six sentences and the three computed checks. The block and
// the section are plain server-rendered markup, so they are read from the document, not from the plot.
const fmt = (n) => Number(n).toLocaleString("en-US");
const text = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : "");
const dataFile = (f) => fs.existsSync(path.join(ROOT, "data", "vanish", f));
const hasSentences = fs.existsSync(path.join(ROOT, "data", "vanish", "robustness_sentences.json"));
const hasChecks = fs.existsSync(path.join(ROOT, "data", "vanish", "robustness.json"));
ok("state (iii): both robustness payload files were copied into the page's data folder",
   hasSentences && hasChecks, `sentences=${hasSentences} checks=${hasChecks}`);
if (hasSentences) {
  const S = payload("robustness_sentences.json");
  const block = P.q("#vb-reading");
  ok("the six sentences: the headed block sits under the plot's note line, not above the plot",
     !!block && !!P.q("#main-chart #vb-reading") &&
     (P.q("#vb-note").compareDocumentPosition(block) & 4) === 4 &&
     /How to read this plot/.test(text(block.querySelector("h2"))), text(block).slice(0, 60));
  ok("at most two sentences above the plot, still",
     (P.q(".sp-lead")?.textContent.match(/\./g) || []).length <= 2);
  const sp = S.s4.floors.find((f) => f.level === "species");
  ok("sentence on what “detected” means carries the rule and the measured floor",
     /detected/.test(text(P.q("#vb-read-detected"))) &&
     /above zero/.test(text(P.q("#vb-read-detected"))) &&
     text(P.q("#vb-read-detected")).includes(String(Number(sp.min_relabund_pct.toPrecision(3)))),
     text(P.q("#vb-read-detected")).slice(0, 160));
  ok("sentence on the reference group gives its size and its recorded conditions",
     text(P.q("#vb-read-reference")).includes(fmt(S.s5.n_ref)) &&
     (S.s5.variant !== "measured" || text(P.q("#vb-read-reference")).includes(fmt(S.s5.n_condition))) &&
     /patients/.test(text(P.q("#vb-read-reference"))), text(P.q("#vb-read-reference")).slice(0, 160));
  ok("sentence on the names is with the class definition and gives the detection counts",
     !!P.q("details.sp-more #vb-read-names") &&
     /not for absence/.test(text(P.q("#vb-read-names"))) &&
     text(P.q("#vb-read-names")).includes(String(S.s1.n_vanish)) &&
     text(P.q("#vb-read-names")).includes(fmt(Math.trunc(S.s1.med_samples_ref))) &&
     text(P.q("#vb-read-names")).includes(fmt(S.s1.n_ref)), text(P.q("#vb-read-names")).slice(0, 200));
  ok("sentence on the simple rule is beside the simple rule and names both omissions",
     !!P.q("details.sp-more #vb-read-simple") &&
     /no adjustment for sequencing depth/.test(text(P.q("#vb-read-simple"))) &&
     /which study a subject came from/.test(text(P.q("#vb-read-simple"))));
  ok("sentence on the pooled lifestyles gives the composition of the group",
     !!P.q("details.sp-more #vb-read-lifestyles") &&
     text(P.q("#vb-read-lifestyles")).includes(String(S.s3.cohorts_ni)) &&
     text(P.q("#vb-read-lifestyles")).includes(String(S.s3.countries_ni)) &&
     S.s3.lifestyles.every((l) => text(P.q("#vb-read-lifestyles")).includes(`${l.n} ${l.label}`)),
     text(P.q("#vb-read-lifestyles")).slice(0, 200));
  ok("sentence on the reference taxonomy is in the limits section",
     !!P.q("details.sp-more #vb-read-taxonomy") &&
     text(P.q("#vb-read-taxonomy")).includes(String(S.s6.taxonomy_version)) &&
     text(P.q("#vb-read-taxonomy")).includes(fmt(S.s6.n_species_profiled)),
     text(P.q("#vb-read-taxonomy")).slice(0, 160));
}
if (hasChecks) {
  const R = payload("robustness.json");
  const sec = P.q("#vb-robustness");
  ok("the Robustness section is one collapsed section of the page's own kind",
     !!sec && sec.tagName === "DETAILS" && sec.classList.contains("sp-more") && !sec.hasAttribute("open"));
  ok("its summary line says the checks are exploratory and that no class depends on them",
     /exploratory/i.test(text(sec.querySelector("summary"))) &&
     /no class/i.test(text(sec.querySelector("summary"))), text(sec.querySelector("summary")));
  const h3 = Array.from(sec.querySelectorAll("h3")).map((x) => text(x));
  ok("it heads the three computed checks and the three that are not done",
     h3.length === 4 && /lifestyle/i.test(h3[0]) && /cohort/i.test(h3[1]) && /depth/i.test(h3[2]) &&
     /not yet done/i.test(h3[3]), h3.join(" | "));
  const items = Array.from(sec.querySelectorAll("ul li")).map((x) => text(x));
  ok("the three long checks are listed as not done, one line each",
     items.length === 3 && /threshold/i.test(items[0]) && /hunter-gatherer/i.test(items[1]) &&
     /condition/i.test(items[2]), `${items.length} items`);
  const links = Array.from(sec.querySelectorAll('a[href*="data/vanish/"]')).map((a) => a.getAttribute("href"));
  ok("each check offers its CSV and every one of those files is there",
     R.csv_files.length === 3 && R.csv_files.every((f) => links.some((h) => h.endsWith(f)) && dataFile(f)),
     links.join(" "));
  // the drawn tables must agree with the JSON they are rendered from
  const tbls = Array.from(sec.querySelectorAll("table"));
  ok("the three checks draw a table each", tbls.length === 3, `${tbls.length} tables`);
  const rows = (t) => Array.from(t.querySelectorAll("tbody tr")).map(
    (tr) => Array.from(tr.children).map((td) => text(td)));
  const r1 = rows(tbls[0]);
  ok(`check 1 table: one row per lifestyle stratum (${R.lifestyle.strata.length}) with its sample count`,
     r1.length === R.lifestyle.strata.length &&
     R.lifestyle.strata.every((s, i) => r1[i][0] === s.lifestyle && r1[i][1] === fmt(s.n_samples)),
     r1.map((r) => r[0]).join(" | "));
  ok("check 1 table: the VANISH cells are the counts in robustness.json",
     R.lifestyle.strata.every((s, i) => {
       const c = s.classes["species/VANISH"];
       return c ? r1[i][4] === `${c.n_hold} of ${c.n_taxa}` : /not judged/.test(r1[i][4]);
     }), r1.map((r) => r[4]).join(" | "));
  const r2 = rows(tbls[1]);
  ok(`check 2 table: one row per level, age group and class (${R.cohort.summary.length})`,
     r2.length === R.cohort.summary.length &&
     R.cohort.summary.every((s, i) => r2[i][0] === s.level && r2[i][1] === s.stratum &&
       r2[i][2] === s.taxon_class && r2[i][3] === String(s.n_taxa) &&
       r2[i][8] === `${s.n_holds_without_top_cohort} of ${s.n_taxa}`),
     r2.map((r) => r.slice(0, 3).join("/")).join(" | "));
  const r3 = rows(tbls[2]);
  ok(`check 3 table: one row per depth stratum (${R.depth.k})`, r3.length === R.depth.k, `${r3.length} rows`);
  ok("check 3 table: the subject counts and the VANISH cells are the ones in robustness.json",
     r3.every((row, i) => {
       const k = i + 1;
       const qi = R.depth.quintiles.find((q) => q.stratum === "adult" && q.depth_quintile === k && q.band === "ind");
       const qn = R.depth.quintiles.find((q) => q.stratum === "adult" && q.depth_quintile === k && q.band === "nonind");
       const c = R.depth.by_quintile.find((q) => q.level === "species" && q.stratum === "adult" &&
                                                 q.taxon_class === "VANISH" && q.depth_quintile === k);
       return row[1] === fmt(qi.n) && row[2] === fmt(qn.n) && row[5] === `${c.n_hold} of ${c.n_taxa}`;
     }), r3.map((r) => r[5]).join(" | "));
  // the headline results have to be on the page in words, not only in the table
  const body = text(sec);
  const lsV = R.lifestyle.summary.find((s) => s.level === "species" && s.taxon_class === "VANISH");
  const deV = R.depth.summary.find((s) => s.level === "species" && s.stratum === "adult" && s.taxon_class === "VANISH");
  const coA = R.cohort.cohorts.find((c) => c.stratum === "adult");
  ok("the paragraphs state the result of each check, including where a class does not hold",
     body.includes(`${lsV.n_hold_all} of the ${lsV.n_taxa} VANISH species hold in all`) &&
     body.includes(`only ${coA.n_cohorts_both_bands} of the ${fmt(coA.n_cohorts)} cohorts`) &&
     body.includes(`${deV.n_hold_all} of the ${deV.n_taxa} VANISH species meet the rule in all`),
     body.slice(0, 120));
}
ok("nothing a visitor reads calls this an Atlas page",
   !/Atlas/.test(P.doc.body.textContent) && !/Atlas/.test(fs.readFileSync(path.join(ROOT, REL), "utf8")));

// States (ii) and (i), from preview roots built from the same run with one or both payload files removed.
function openStatic(root) {
  const raw = fs.readFileSync(path.join(root, REL), "utf8");
  const dom = new JSDOM(raw.replace(/<script[\s\S]*?<\/script>/g, ""), { url: `${BASE}/${REL}` });
  return dom.window.document;
}
const ROOT2 = process.argv[3], ROOT1 = process.argv[4];
if (ROOT2) {
  const d = openStatic(ROOT2);
  const sec = d.querySelector("#vb-robustness");
  ok("state (ii): with the sentences only, the six sentences are still shown",
     !!d.querySelector("#vb-reading") && !!d.querySelector("#vb-read-names") &&
     !!d.querySelector("#vb-read-simple") && !!d.querySelector("#vb-read-lifestyles") &&
     !!d.querySelector("#vb-read-detected") && !!d.querySelector("#vb-read-reference") &&
     !!d.querySelector("#vb-read-taxonomy"));
  ok("state (ii): the Robustness section says the three quick checks are not computed",
     !!sec && /have not been computed/i.test(text(sec)) && sec.querySelectorAll("table").length === 0,
     text(sec).slice(0, 120));
  ok("state (ii): it still lists the three long checks and links no check CSV",
     !!sec && sec.querySelectorAll("ul li").length === 3 &&
     sec.querySelectorAll('a[href*="robustness_"]').length === 0);
  ok("state (ii): nothing refers to a robustness table file that is not in the payload",
     !fs.existsSync(path.join(ROOT2, "data", "vanish", "robustness.json")) &&
     fs.existsSync(path.join(ROOT2, "data", "vanish", "robustness_sentences.json")));
}
if (ROOT1) {
  const d = openStatic(ROOT1);
  ok("state (i): with neither file, the page is as it was — no block, no Robustness section",
     !d.querySelector("#vb-reading") && !d.querySelector("#vb-robustness") &&
     !d.querySelector("#vb-read-names") && !!d.querySelector("#vb-plot") &&
     d.querySelectorAll("details.sp-more").length >= 7);
  ok("state (i): no robustness file reached the page's data folder",
     !fs.existsSync(path.join(ROOT1, "data", "vanish", "robustness.json")) &&
     !fs.existsSync(path.join(ROOT1, "data", "vanish", "robustness_sentences.json")));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
