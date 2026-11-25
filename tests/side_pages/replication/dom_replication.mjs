// dom_replication.mjs — headless test of Atlas > Replication of published findings: no browser, no bundler.
//
// Loads the rendered preview page into jsdom over an http://localhost base URL with a fetch shim that reads
// the preview folder from disk, evaluates the real vendored d3, the real static/sp_core.js and the real
// static/sp_replication.js, and checks that the graphic, the search, the click-through, the claim panel and
// the address do what the page promises. Every number it compares comes from the CSV downloads in the
// preview (written from the run's result files), not from the JSON the page reads. It writes the SVG of each
// view to tests/out/*.svg so the drawing can be rasterised and looked at (tests/svg_png.py).
//
//   NODE_PATH=<node_modules with jsdom> node tests/dom_replication.mjs <preview root>
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";
import { createRequire } from "node:module";
const { JSDOM, VirtualConsole } = createRequire(import.meta.url)("jsdom");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = process.argv[2] || path.join(path.dirname(HERE), "preview_site");
const OUT = process.env.RP_OUT || path.join(HERE, "out");      // where the SVG of each view is written
fs.mkdirSync(OUT, { recursive: true });
const BASE = "http://localhost:8765";
const REL = "atlas/replication.html";

let pass = 0, fail = 0;
const ok = (name, cond, extra = "") => {
  if (cond) { pass++; console.log(`✓  ${name}`); }
  else { fail++; console.log(`✗ FAIL  ${name}${extra ? "  — " + extra : ""}`); }
};
const brief = (e) => String(e?.stack || e?.message || e).split("\n").slice(0, 3).join(" ⏎ ").slice(0, 300);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function csv(file) {            // RFC 4180 reader: quoted fields, doubled quotes, commas and newlines in quotes
  const buf = fs.readFileSync(path.join(ROOT, "data", "replication", file));
  const s = (file.endsWith(".gz") ? zlib.gunzipSync(buf) : buf).toString("utf8");
  const rows = []; let row = [], cur = "", q = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (q) { if (ch === '"') { if (s[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += ch; }
    else if (ch === '"') q = true;
    else if (ch === ",") { row.push(cur); cur = ""; }
    else if (ch === "\n") { row.push(cur); rows.push(row); row = []; cur = ""; }
    else if (ch !== "\r") cur += ch;
  }
  if (cur !== "" || row.length) { row.push(cur); rows.push(row); }
  const head = rows.shift();
  return rows.map((r) => Object.fromEntries(head.map((h, i) => [h, r[i]])));
}
const CONDS = csv("replication_conditions.csv");
const CLAIMS = csv("replication_claims.csv.gz");
const EFFECTS = csv("replication_project_effects.csv.gz");
const claimById = new Map(CLAIMS.map((r) => [r.claim_id, r]));
const effKey = (cond, taxon, acc) => `${cond}|${taxon}|${acc}`;
const effBy = new Map(EFFECTS.map((r) => [effKey(r.condition, r.taxon, r.study_accession), r]));
const META = JSON.parse(fs.readFileSync(path.join(ROOT, "data", "replication", "replication.json"), "utf8")).meta;

async function openPage(hash = "", cfg = {}) {
  const raw = fs.readFileSync(path.join(ROOT, REL), "utf8");
  const html = raw.replace(/<script[\s\S]*?<\/script>/g, "");
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => { if (!/Not implemented/.test(String(e))) errors.push("jsdom: " + brief(e)); });
  vc.on("error", (...a) => errors.push("console.error: " + a.map(brief).join(" ")));
  const dom = new JSDOM(html, { url: `${BASE}/${REL}${hash}`, runScripts: "outside-only", pretendToBeVisual: true, virtualConsole: vc });
  const { window } = dom;
  window.addEventListener("error", (e) => errors.push("window.error: " + (e.error?.stack || e.message)));
  const fetched = [];
  window.fetch = async (url) => {
    const u = new URL(String(url), `${BASE}/${REL}`);
    fetched.push(u.pathname);
    const p = path.join(ROOT, decodeURIComponent(u.pathname));
    if (!fs.existsSync(p)) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => JSON.parse(fs.readFileSync(p, "utf8")) };
  };
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  window.eval(fs.readFileSync(path.join(ROOT, "static", "vendor", "d3-7.9.0.min.js"), "utf8"));
  const conf = raw.match(/window\.SP_PAGE=\{[\s\S]*?\};/);
  window.eval(conf[0]);
  Object.assign(window.SP_PAGE, cfg);
  window.eval(fs.readFileSync(path.join(ROOT, "static", "sp_core.js"), "utf8"));
  const nav = [];
  window.SP.go = (u) => nav.push(u);
  window.eval(fs.readFileSync(path.join(ROOT, "static", "sp_replication.js"), "utf8"));
  await window.SP_REPLICATION.ready;
  return { window, doc: window.document, errors, nav, fetched, R: window.SP_REPLICATION, raw,
           q: (s) => window.document.querySelector(s), qa: (s) => Array.from(window.document.querySelectorAll(s)) };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const saveSvg = (p, sel, name) => {
  const svg = p.q(sel);
  if (svg) fs.writeFileSync(path.join(OUT, name + ".svg"), svg.outerHTML.replace(/font-family="inherit"/g, ""));
  return !!svg;
};
const num = (s) => Number(String(s).replace(/,/g, ""));

// ---------------------------------------------------------------- the page as delivered
const P = await openPage();
const lead = P.q(".sp-lead").textContent.trim();
const sentences = lead.split(/(?<=[.!?])\s+(?=[A-Z<])/).filter(Boolean);
ok("at most two sentences above the graphic", sentences.length <= 2, `${sentences.length} sentences`);
ok("the lead states the headline", lead.includes(`${META.replicated.toLocaleString("en-US")} of the ${META.testable.toLocaleString("en-US")}`), lead.slice(0, 120));
// R2026.43: the package moved from the lead to the stamp line under it (website review F275, F288)
ok("the line under the lead names the package the test ran on", Array.from(P.qa(".sp-head p, main p")).some((x) => /Computed on data package/.test(x.textContent) && x.textContent.includes(META.package)));
ok("nothing but the lead, the search box and one closed disclosure stands between the title and the graphic",
   !P.q("#rp-firm").open && P.qa(".sp-head p.sp-lead").length === 1);
ok("no image anywhere in the page body", P.qa("main img, #main-chart img, .sp-details img, #rp-firm img").length === 0 && !/<img\b/.test(P.raw.split("</header>").pop().split("<footer")[0]));
ok("one big search box for a taxon or a condition", !!P.q("#rp-q.searchbox.big") && /taxon or a condition/.test(P.q("#rp-q").placeholder));
ok("the page fetched exactly one data file", P.fetched.length === 1 && P.fetched[0].endsWith("/data/replication/replication.json"), P.fetched.join(" "));
ok("the status line is hidden once the data are in", P.q("#rp-status").hidden === true);

// ---------------------------------------------------------------- view 1: every condition
const tested = CONDS.filter((c) => num(c.testable) > 0);
const condRows = P.qa("#rp-plot g.rp-cond");
ok(`overview: one bar per condition with a testable claim (${tested.length}) plus all conditions`, condRows.length === tested.length + 1, `drew ${condRows.length}`);
const allRow = P.q('#rp-plot g.rp-cond-all');
ok("the all-conditions bar carries the headline", num(allRow.dataset.replicated) === META.replicated && num(allRow.dataset.testable) === META.testable);
ok("the all-conditions segments add up to every testable claim",
   Array.from(allRow.querySelectorAll("rect.rp-seg")).reduce((a, r) => a + num(r.dataset.n), 0) === META.testable);
let badCond = [];
for (const c of tested) {
  const g = condRows.find((r) => r.dataset.key === c.condition);
  if (!g) { badCond.push(c.condition + " missing"); continue; }
  const seg = Object.fromEntries(Array.from(g.querySelectorAll("rect.rp-seg")).map((r) => [r.dataset.st, num(r.dataset.n)]));
  const want = { 0: num(c.replicated) - num(c.replicated_small_effect), 1: num(c.replicated_small_effect), 2: num(c.inconclusive),
                 3: num(c.meaningful_effect_excluded), 4: num(c.opposite_direction) };
  for (const s of [0, 1, 2, 3, 4]) if ((seg[s] || 0) !== want[s]) badCond.push(`${c.condition} st${s}: ${seg[s] || 0} vs ${want[s]}`);
  if (num(g.dataset.replicated) !== num(c.replicated) || num(g.dataset.testable) !== num(c.testable)) badCond.push(c.condition + " totals");
  const widths = Array.from(g.querySelectorAll("rect.rp-seg")).map((r) => [num(r.dataset.n), Number(r.getAttribute("width"))]);
  const per = widths.map(([n, w]) => w / n);
  if (Math.max(...per) - Math.min(...per) > 1e-6) badCond.push(c.condition + " widths not proportional");
}
ok("every condition bar equals replication_conditions.csv, result by result, and is drawn to scale", badCond.length === 0, badCond.slice(0, 4).join("; "));
const nameOrder = () => P.qa("#rp-plot g.rp-cond:not(.rp-cond-all)").map((g) => g.dataset.key);
const big = tested.filter((c) => num(c.testable) >= META.checks.across_conditions.min_claims);
ok("default order: most testable claims first", nameOrder()[0] === [...big].sort((a, b) => num(b.testable) - num(a.testable))[0].condition, nameOrder()[0]);
saveSvg(P, "#rp-plot svg", "overview");
click(P.window, P.qa("#rp-sort button").find((b) => b.dataset.value === "rate"));
const best = [...big].sort((a, b) => num(b.replicated) / num(b.testable) - num(a.replicated) / num(a.testable))[0].condition;
ok(`order by rate puts ${best} first`, nameOrder()[0] === best, nameOrder()[0]);
ok("conditions with fewer than 10 testable claims stay in their own group at the end",
   nameOrder().slice(big.length).every((n) => num(tested.find((c) => c.condition === n).testable) < 10) && nameOrder().length === tested.length);
const chips = P.qa("#rp-untested a.rp-chip");
ok(`the untested conditions are listed, closed by default (${CONDS.length - tested.length})`, chips.length === CONDS.length - tested.length && !P.q("#rp-untested").open, `${chips.length}`);
const firmBars = P.qa("#rp-firm-chart g.sp-barg");
ok("the how-firm box draws six bars on a linear axis from zero", firmBars.length === 6 && !!P.q("#rp-firm-chart .sp-axis"));
{
  const txt = P.q("#rp-firm").textContent, c = META.checks, f = (n) => n.toLocaleString("en-US");
  ok("the how-firm box states the stricter test, the person-identifier figure and the largest condition's share",
     txt.includes(`${f(c.stricter.replicated)} of ${f(c.stricter.testable)}`) && txt.includes(`${f(c.person_id.replicated)} of ${f(c.person_id.testable)}`) &&
     txt.includes(`${f(c.largest.testable)} of the ${f(META.testable)}`) && txt.includes(`${f(c.without_largest.replicated)} of ${f(c.without_largest.testable)}`) && txt.includes(c.largest.name));
}
saveSvg(P, "#rp-firm-chart svg", "firm");

// ---------------------------------------------------------------- view 2: one condition
const PD = META.checks.largest.name;
click(P.window, P.qa("#rp-plot g.rp-cond").find((g) => g.dataset.key === PD));
ok("clicking a condition writes it into the address", decodeURIComponent(P.window.location.hash) === `#condition=${PD}`, P.window.location.hash);
ok("…and shows its claims", P.q("#rp-plot").dataset.view === "condition" && P.qa("#rp-plot g.rp-row").length === 40);
const pd = CONDS.find((c) => c.condition === PD);
ok("the condition header gives its rate, projects, cases and controls",
   P.q("#rp-viewhead").textContent.includes(`${num(pd.replicated)} of ${num(pd.testable)}`) &&
   P.q("#rp-viewhead").textContent.includes(`${num(pd.projects)} independent projects`) &&
   P.q("#rp-viewhead").textContent.includes(num(pd.case_persons).toLocaleString("en-US")), P.q("#rp-viewhead").textContent.slice(0, 200));
{
  const head = P.q("#rp-viewhead").textContent, nRows = P.R.conditions().find((c) => c.name === PD).tests.length;
  const lab = Object.fromEntries(P.qa("#rp-filter button").map((b) => [b.dataset.value, num(b.textContent.replace(/[^0-9,]/g, ""))]));
  const pdAll = CLAIMS.filter((r) => r.condition === PD).length;
  ok("the header says how many rows the claims are drawn as", head.includes(`${pdAll} claims are drawn as ${nRows} rows`), head.slice(-200));
  ok("the filter buttons count claims, the unit of the header", lab.all === pdAll && lab.rep === num(pd.replicated) && lab.not === num(pd.testable) - num(pd.replicated) && lab.un === pdAll - num(pd.testable), JSON.stringify(lab));
  ok("rows whose test left out the claim's own cohort carry a mark, and the caption explains it", P.qa("#rp-plot text.rp-own").length > 0 && /Asterisks/.test(P.q("#rp-caption").textContent) &&
     P.q("#rp-caption").textContent.includes(`${pdAll} claims in ${nRows} rows`));
}
saveSvg(P, "#rp-plot svg", "condition_first40");
click(P.window, P.q("#rp-more-btn"));
const allRows = P.qa("#rp-plot g.rp-row");
const pdClaims = CLAIMS.filter((r) => r.condition === PD);
ok("show all: the rows hold every registered claim of the condition", P.R.rows().reduce((a, t) => a + t.claims.length, 0) === pdClaims.length && allRows.length === P.R.rows().length,
   `${allRows.length} rows, ${P.R.rows().reduce((a, t) => a + t.claims.length, 0)} claims vs ${pdClaims.length}`);
{
  const est = allRows.filter((g) => Number(g.dataset.st) < 5).map((g) => Number(g.querySelector("circle.rp-est").dataset.agree));
  ok("rows run from the claim the data agree with most to the one they contradict most", est.every((v, i) => i === 0 || v <= est[i - 1] + 1e-9));
  const st = allRows.map((g) => Number(g.dataset.st));
  ok("untested claims come last and carry no estimate", st.every((s, i) => i === 0 || !(s < 5 && st[i - 1] >= 5)) && allRows.filter((g) => Number(g.dataset.st) >= 5).every((g) => !g.querySelector("circle")));
  let bad = 0;
  for (const g of allRows) {
    const r = claimById.get(g.dataset.key), s = Number(g.dataset.st);
    if (s >= 5) continue;
    const agree = r.claimed === "higher in cases" ? Number(r.auc_pooled) : 1 - Number(r.auc_pooled);
    if (Math.abs(agree - Number(g.querySelector("circle.rp-est").dataset.agree)) > 3e-6) bad++;
    if ((g.querySelector("text.rp-dir").textContent.includes("higher")) !== (r.claimed === "higher in cases")) bad++;
  }
  ok("every drawn estimate and claimed direction equals replication_claims.csv", bad === 0, `${bad} differ`);
}
const seg = (v) => click(P.window, P.qa("#rp-filter button").find((b) => b.dataset.value === v));
seg("rep");
ok("filter: replicated only", P.qa("#rp-plot g.rp-row").length > 0 && P.qa("#rp-plot g.rp-row").every((g) => Number(g.dataset.st) < 2));
seg("un");
ok("filter: not tested only", P.qa("#rp-plot g.rp-row").length > 0 && P.qa("#rp-plot g.rp-row").every((g) => Number(g.dataset.st) >= 5));
seg("all");

// ---------------------------------------------------------------- view 3: one claim
const firstRow = P.qa("#rp-plot g.rp-row")[0];
const firstId = firstRow.dataset.key;
click(P.window, firstRow);
ok("clicking a claim writes it into the address and opens the panel", decodeURIComponent(P.window.location.hash) === `#claim=${firstId}` && !P.q("#rp-panel").hidden && P.q("#rp-split").classList.contains("spx-open"));
ok("the open claim's row is marked in the list", P.qa("#rp-plot g.rp-row.rp-row-on").length === 1 && P.q("#rp-plot g.rp-row.rp-row-on").dataset.key === firstId);
function checkPanel(p, id) {            // the open panel against the two CSV downloads; returns a list of differences
  const r = claimById.get(id), bad = [], panel = p.q("#rp-panel");
  if (panel.hidden || panel.dataset.key === undefined) return ["panel not open"];
  const t = p.R.claims().find((c) => c.id === id).test;
  const h2 = panel.querySelector("h2").textContent;
  if (!h2.includes(r.taxon) || !h2.includes(r.condition)) bad.push("title");
  if (h2.includes("more abundant") !== (r.claimed === "higher in cases")) bad.push("claimed direction");
  const label = panel.querySelector(".rp-badge").textContent.toLowerCase();
  const want = r.result.startsWith("replicated,") ? "small" : r.result.startsWith("replicated") ? "replicated" : r.result.includes("inconclusive") ? "inconclusive"
    : r.result.includes("meaningful") ? "ruled out" : r.result.includes("opposite") ? "opposite" : "not tested";
  if (!label.includes(want) || (want === "replicated" && label.includes("small")) || (want === "replicated" && label.includes("not"))) bad.push(`label ${label} vs ${r.result}`);
  const papers = Array.from(panel.querySelectorAll("a.rp-paper")).map((a) => a.getAttribute("href"));
  const wantPapers = new Set(t.claims.map((c) => "https://www.ncbi.nlm.nih.gov/pubmed/" + claimById.get(c.id).pmid));
  if (papers.length !== wantPapers.size || !papers.every((u) => wantPapers.has(u))) bad.push("paper links");
  const ex = Array.from(panel.querySelectorAll("a.rp-explore")).map((a) => a.getAttribute("href"));
  if (ex.length && ex[0] !== "taxa/index.html#taxon=" + encodeURIComponent(r.gtdb_anchor) && !r.gtdb_names_counted.split(";").some((n) => ex[0] === "taxa/index.html#taxon=" + encodeURIComponent(n))) bad.push("explorer link " + ex[0]);
  if (!ex.every((u) => r.gtdb_names_counted.split(";").some((n) => u === "taxa/index.html#taxon=" + encodeURIComponent(n)))) bad.push("an explorer link is not one of the GTDB names counted");
  if (r.result.startsWith("not tested")) {
    if (panel.querySelector("#rp-forest svg")) bad.push("forest for an untested claim");
    if (!panel.querySelector("#rp-caveat")) bad.push("no caveat line");
    if (panel.querySelector("#rp-link").value !== p.window.location.href) bad.push("copy-link");
    return bad;
  }
  if (!panel.querySelector("#rp-sentence").textContent.includes(Number(r.auc_pooled).toFixed(3))) bad.push("pooled AUC in the sentence");
  if (Math.abs(Number(panel.querySelector(".rp-diamond").dataset.auc) - Number(r.auc_pooled)) > 3e-6) bad.push("diamond");
  const used = new Set(r.cohort_units_used.split(";"));
  const rows = Array.from(panel.querySelectorAll("#rp-forest g.rp-proj"));
  const drawnUsed = rows.filter((g) => !g.classList.contains("rp-proj-own"));
  if (drawnUsed.length !== num(r.projects_used)) bad.push(`projects drawn ${drawnUsed.length} vs ${r.projects_used}`);
  for (const g of rows) {
    const e = effBy.get(effKey(r.condition, r.taxon, g.dataset.acc));
    if (!e) { bad.push("no effect row for " + g.dataset.acc); continue; }
    if (Math.abs(Number(e.auc) - Number(g.dataset.auc)) > 3e-6) bad.push("project AUC " + g.dataset.acc);
    if (!g.classList.contains("rp-proj-own") && !used.has(e.cohort_unit)) bad.push("a project drawn as used is not in units_used: " + g.dataset.acc);
    if (g.classList.contains("rp-proj-own") && used.has(e.cohort_unit)) bad.push("own-cohort mark on a used project");
  }
  if (rows.filter((g) => g.classList.contains("rp-proj-own")).length !== num(r.n_units_discovery_excluded)) bad.push("own-cohort projects drawn");
  const sigs = panel.querySelector(".rp-papers").textContent;
  if (!t.claims.every((c) => sigs.includes(c.id.split("|")[0].replace(/^bsdb:/, "")))) bad.push("a BugSigDB signature of the row is not shown");
  const cav = panel.querySelector("#rp-caveat")?.textContent || "";
  if (!cav) bad.push("no caveat line");
  if (/not thereby shown to be false/.test(cav) !== r.result.includes("inconclusive")) bad.push("the not-shown-false caveat belongs to inconclusive claims only");
  if (r.result.includes("meaningful") && !/excluded/.test(cav)) bad.push("caveat of a ruled-out claim");
  if (r.result.includes("opposite") && !/other way/.test(cav)) bad.push("caveat of an opposite claim");
  if (panel.querySelector("#rp-link").value !== p.window.location.href || !p.window.location.href.endsWith(encodeURIComponent(id))) bad.push("copy-link is not this claim's address");
  const listed = panel.querySelectorAll("#rp-projlist li").length;
  if (listed !== num(r.projects_used) + num(r.n_units_discovery_excluded)) bad.push(`project list ${listed}`);
  return bad;
}
{
  const bad = checkPanel(P, firstId);
  ok("the panel shows the direction claimed, the result, each project's estimate, the pooled estimate and the paper", bad.length === 0, bad.join("; "));
  const link = P.q("#rp-link").value;
  ok("the copy-link field holds the address of this claim", link === P.window.location.href, link);
  click(P.window, P.q("#rp-forest g.rp-proj"));
  ok("a project in the forest opens its project page", P.nav.length === 1 && /^\.\.\/studies\/PRJ[A-Z0-9]+\.html$/.test(P.nav[0]), P.nav.join(" "));
  ok("the project list links to the same pages", Array.from(P.qa("#rp-projlist a")).every((a) => /^\.\.\/studies\/PRJ[A-Z0-9]+\.html$/.test(a.getAttribute("href"))));
  saveSvg(P, "#rp-forest svg", "claim_forest");
  saveSvg(P, "#rp-plot svg", "condition_with_claim_open");
}
click(P.window, P.q("#rp-close"));
ok("closing the panel returns to the condition", P.q("#rp-panel").hidden && decodeURIComponent(P.window.location.hash) === `#condition=${PD}`);

// EVERY registered claim, opened through its own address
{
  const bad = [];
  let ownSeen = 0, t0 = Date.now();
  for (const r of CLAIMS) {
    P.R.nav("claim=" + encodeURIComponent(r.claim_id));
    const b = checkPanel(P, r.claim_id);
    if (P.q("#rp-plot g.rp-row.rp-row-on") === null) b.push("row not drawn or not marked");
    ownSeen += P.qa("#rp-forest g.rp-proj-own").length;
    if (b.length) bad.push(r.claim_id + ": " + b.join(", "));
  }
  ok(`all ${CLAIMS.length} claims opened through their address (${((Date.now() - t0) / 1000).toFixed(0)} s): panel equals the CSV downloads`, bad.length === 0, `${bad.length} differ: ` + bad.slice(0, 3).join(" | "));
  ok("own-cohort projects are drawn, marked and not counted", ownSeen > 0, `${ownSeen}`);
  ok("no script error while opening every claim", P.errors.length === 0, P.errors.slice(0, 2).join(" | "));
}

// ---------------------------------------------------------------- search, taxon view, the address
async function search(p, text) {
  const inp = p.q("#rp-q");
  inp.value = text;
  inp.dispatchEvent(new p.window.Event("input", { bubbles: true }));
  await sleep(140);
  return p.qa("#rp-hits li.sp-hit");
}
{
  let hits = await search(P, "roseb");
  ok("search finds a taxon by the start of its name", hits.length > 0 && /Roseburia/.test(hits[0].textContent), hits[0]?.textContent);
  click(P.window, hits[0].querySelector("a"));
  ok("picking it opens the taxon's claims across conditions", P.q("#rp-plot").dataset.view === "taxon" && /^#taxon=Roseburia/.test(P.window.location.hash) && P.qa("#rp-plot g.rp-row").length > 0, P.window.location.hash);
  const tx = P.R.state.taxon;
  const exLinks = P.qa("#rp-viewhead a.rp-explore").map((a) => a.getAttribute("href"));
  ok("the taxon header links every counted GTDB group to the taxon explorer by its GTDB name",
     exLinks.length === 1 + tx.targets.filter((n) => n[0] !== "!").length && exLinks[0] === "taxa/index.html#taxon=" + encodeURIComponent(tx.gtdb) &&
     exLinks.every((u) => /^taxa\/index\.html#taxon=g__Roseburia/.test(u)), exLinks.join(" "));
  ok("taxon rows are labelled by condition", P.qa("#rp-plot g.rp-row").every((g) => CONDS.some((c) => c.condition === g.dataset.name)));
  saveSvg(P, "#rp-plot svg", "taxon_view");
  const row = P.qa("#rp-plot g.rp-row")[0];
  click(P.window, row);
  ok("a claim opened from the taxon list keeps the taxon list", P.q("#rp-plot").dataset.view === "taxon" && !P.q("#rp-panel").hidden);
  P.window.history.back(); await sleep(60);
  ok("Back closes the claim and stays on the taxon", P.q("#rp-panel").hidden && /^#taxon=/.test(P.window.location.hash), P.window.location.hash);
  hits = await search(P, "crohn");
  ok("search finds a condition", hits.length > 0 && /Crohn/.test(hits[0].textContent) && /condition/.test(hits[0].textContent));
  click(P.window, hits[0].querySelector("a"));
  ok("picking it opens the condition and clears the search box", P.q("#rp-plot").dataset.view === "condition" && P.q("#rp-q").value === "" && P.qa("#rp-hits li").length === 0);
  saveSvg(P, "#rp-plot svg", "condition_crohn");
  hits = await search(P, "zzzz");
  ok("a search with no match says so", P.qa("#rp-hits li.sp-hit-none").length === 1);
  click(P.window, P.q("#rp-crumbs .sp-crumb"));
  ok("the first breadcrumb returns to all conditions", P.q("#rp-plot").dataset.view === "all" && P.window.location.hash === "");
  // R2026.43: the band key left the colour legend; the caption explains the band (website review F277)
  ok("the colour legend has no key for the shaded band", P.q("#rp-key-band") === null);
  click(P.window, P.q("#rp-untested a.rp-chip"));
  ok("an untested condition opens with its claims listed as not tested and no axis",
     P.q("#rp-plot").dataset.view === "condition" && P.qa("#rp-plot g.rp-row").length > 0 && P.qa("#rp-plot g.rp-row").every((g) => Number(g.dataset.st) >= 5) && !P.q("#rp-plot .rp-axis") && /None of the/.test(P.q("#rp-viewhead").textContent));
  ok("…and the reason is given once, in the header, not on every row", P.qa("#rp-plot text.rp-why").length === 0);
  saveSvg(P, "#rp-plot svg", "condition_untested");
}
ok("no script error during the whole session", P.errors.length === 0, P.errors.slice(0, 2).join(" | "));

// fresh loads with an address
{
  const id = CLAIMS.find((r) => r.result === "replicated" && num(r.n_units_discovery_excluded) > 0).claim_id;
  const A = await openPage("#claim=" + encodeURIComponent(id));
  ok("loading a claim's address opens its condition with the claim in the panel", A.q("#rp-plot").dataset.view === "condition" && A.q("#rp-panel").dataset.key !== undefined && checkPanel(A, id).length === 0 && !!A.q("#rp-plot g.rp-row.rp-row-on"), checkPanel(A, id).join("; "));
  saveSvg(A, "#rp-forest svg", "claim_forest_own_cohort");
  const B = await openPage("#taxon=" + encodeURIComponent(P.R.taxa().find((t) => t.name === "Roseburia").gtdb));
  ok("a taxon can be addressed by its GTDB name too", B.q("#rp-plot").dataset.view === "taxon" && B.R.state.taxon.name === "Roseburia");
  const C = await openPage("#condition=" + encodeURIComponent("Not a condition"));
  ok("an unknown address shows all conditions and says the name is not among the claims", C.q("#rp-plot").dataset.view === "all" && !C.q("#rp-status").hidden && /No published claim about .* was tested here/.test(C.q("#rp-status").textContent));
  for (const h of ["#condition=__proto__", "#taxon=constructor", "#claim=__proto__", "#taxon=%E0%A4%A", "#condition=" + encodeURIComponent("<img src=x onerror=alert(1)>")]) {
    const X = await openPage(h);
    ok(`odd address ${h.slice(0, 28)}: all conditions, a plain message, no error, nothing injected`, X.q("#rp-plot").dataset.view === "all" && !X.q("#rp-status").hidden && X.errors.length === 0 && X.qa("#rp-status img, #rp-crumbs img").length === 0, X.errors.join(" | "));
  }
  const N = await openPage("", { width: 380, panelWidth: 340 });
  ok("narrow layout (380 px): the overview draws every condition", N.qa("#rp-plot g.rp-cond").length === tested.length + 1);
  saveSvg(N, "#rp-plot svg", "overview_narrow");
  N.R.nav("claim=" + encodeURIComponent(id));
  ok("narrow layout: list and forest draw", N.qa("#rp-plot g.rp-row").length > 0 && N.qa("#rp-forest g.rp-proj").length > 0);
  saveSvg(N, "#rp-plot svg", "condition_narrow"); saveSvg(N, "#rp-forest svg", "claim_forest_narrow");
  ok("no script error in the fresh loads", [A, B, C, N].every((p) => p.errors.length === 0), [A, B, C, N].flatMap((p) => p.errors).slice(0, 2).join(" | "));
}

// labels must fit: estimate each text's box from its length and compare with the room it has
{
  const W = 940, over = [];
  for (const g of P.qa("#rp-plot g.rp-row, #rp-plot g.rp-cond")) {
    for (const t of g.querySelectorAll("text")) {
      const size = Number(t.getAttribute("font-size")) || 12, w = t.textContent.length * size * 0.56, x = Number(t.getAttribute("x"));
      const a = t.getAttribute("text-anchor");
      const left = a === "end" ? x - w : a === "middle" ? x - w / 2 : x, right = a === "end" ? x : a === "middle" ? x + w / 2 : x + w;
      if (left < -1 || right > W + 1) over.push(t.textContent);
    }
  }
  ok("no label of the current view runs outside the drawing (estimated from its length)", over.length === 0, over.slice(0, 3).join(" | "));
}

// the script itself
{
  const js = fs.readFileSync(path.join(ROOT, "static", "sp_replication.js"), "utf8");
  const hosts = [...new Set((js.match(/https?:\/\/[A-Za-z0-9.\-]+/g) || []))];
  ok("the only absolute URL in the script is the PubMed link prefix", hosts.length === 1 && hosts[0] === "https://www.ncbi.nlm.nih.gov", hosts.join(" "));
  ok("no eval, no Function constructor, no inline handlers", !/\beval\(|new Function\(/.test(js) && !/\son[a-z]+="/.test(P.raw));
}

console.log(`\n${pass} checks passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
