// dom_conditions.mjs — headless test of Insights > Condition explorer: no browser, no bundler.
//
// Loads the built page into jsdom over an http://localhost base URL with a fetch shim that reads the site root
// from disk, evaluates the real static/sp_core.js and the real static/sp_conditions.js, and checks that the
// header totals, the table, the search, the sort, a condition's section (its counts, its project list and its
// list of replicated associations) and the four statements the page must carry are what the three CSV files say.
// Every number it compares is read from the CSV files in the site itself, never from the HTML.
//
//   NODE_PATH=<node_modules with jsdom> node tests/side_pages/conditions/dom_conditions.mjs <site root>
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
const { JSDOM, VirtualConsole } = createRequire(import.meta.url)("jsdom");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = process.argv[2] || path.join(HERE, "..", "..", "..", "build", "preview");
const BASE = "http://localhost:8765";
const REL = "atlas/conditions.html";

let pass = 0, fail = 0;
const ok = (name, cond, extra = "") => {
  if (cond) { pass++; console.log(`✓  ${name}`); }
  else { fail++; console.log(`✗ FAIL  ${name}${extra ? "  — " + extra : ""}`); }
};
const brief = (e) => String(e?.stack || e?.message || e).split("\n").slice(0, 3).join(" ⏎ ").slice(0, 300);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const num = (s) => Number(String(s === undefined || s === null ? "" : s).replace(/,/g, "")) || 0;
const fmt = (n) => Math.round(n).toLocaleString("en-US");

function csv(file) {            // RFC 4180 reader: quoted fields, doubled quotes, commas and newlines in quotes
  const s = fs.readFileSync(path.join(ROOT, "data", "conditions", file), "utf8");
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
  return rows.filter((r) => r.length > 1).map((r) => Object.fromEntries(head.map((h, i) => [h, r[i]])));
}
const CONDS = csv("condition_explorer.csv");
const PROJ = csv("condition_explorer_projects.csv");
const ASSOC = csv("condition_explorer_associations.csv");

async function openPage(hash = "") {
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
    if (!fs.existsSync(p)) return { ok: false, status: 404, text: async () => "", json: async () => ({}) };
    const body = fs.readFileSync(p, "utf8");
    return { ok: true, status: 200, text: async () => body, json: async () => JSON.parse(body) };
  };
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  const conf = raw.match(/window\.SP_PAGE=\{[\s\S]*?\};/);
  window.eval(conf[0]);
  window.eval(fs.readFileSync(path.join(ROOT, "static", "sp_core.js"), "utf8"));
  window.eval(fs.readFileSync(path.join(ROOT, "static", "sp_conditions.js"), "utf8"));
  await window.SP_CONDITIONS.ready;
  return { window, doc: window.document, errors, fetched, C: window.SP_CONDITIONS, raw,
           q: (s) => window.document.querySelector(s), qa: (s) => Array.from(window.document.querySelectorAll(s)) };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

// ---------------------------------------------------------------- the page as delivered
const P = await openPage();
const lead = P.q(".sp-lead").textContent.trim();
const sentences = lead.split(/(?<=[.!?])\s+(?=[A-Z<])/).filter(Boolean);
ok("at most two sentences above the search box", sentences.length <= 2, `${sentences.length} sentences`);
ok("one big search box for a condition or an id", !!P.q("#cn-q.searchbox.big") && /condition/i.test(P.q("#cn-q").placeholder));
ok("the page fetched exactly one data file before a condition is opened",
   P.fetched.length === 1 && P.fetched[0].endsWith("/data/conditions/condition_explorer.csv"), P.fetched.join(" "));
ok("the status line is hidden once the table is in", P.q("#cn-status").hidden === true);
ok("no image anywhere in the page body", P.qa("main img, .cn-tablebox img, .sp-details img").length === 0);

// ---------------------------------------------------------------- the header totals, against the CSV files
const CASES = CONDS.reduce((a, r) => a + num(r.cases), 0);
const NPROJ = new Set(PROJ.map((r) => r.study_accession)).size;
{
  const totals = P.q("#cn-totals").textContent;
  ok("the header gives the number of conditions, case samples and projects of the tables",
     totals.includes(fmt(CONDS.length)) && totals.includes(fmt(CASES)) && totals.includes(fmt(NPROJ)), totals);
  ok("the header names the release", /release R\d{4}\.\d+/.test(totals), totals);
  ok("the page read every row of the table", P.C.rows().length === CONDS.length, `${P.C.rows().length} vs ${CONDS.length}`);
  ok("the lead repeats the same three totals",
     lead.includes(fmt(CONDS.length)) && lead.includes(fmt(CASES)) && lead.includes(fmt(NPROJ)));
}

// ---------------------------------------------------------------- the table
{
  const rows = P.qa("#cn-table tbody tr");
  ok(`one row per condition (${CONDS.length})`, rows.length === CONDS.length, `drew ${rows.length}`);
  const order = rows.map((t) => num(t.dataset.cases));
  ok("sorted by case samples, largest first, by default", order.every((v, i) => i === 0 || v <= order[i - 1]));
  const bad = [];
  for (const tr of rows.slice(0, 60)) {
    const c = CONDS.find((r) => r.anchor === tr.dataset.anchor);
    if (!c) { bad.push(tr.dataset.anchor + " not in the CSV"); continue; }
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent.trim());
    if (cells[0] !== c.condition_name) bad.push(`${c.anchor} name`);
    if (cells[2] !== fmt(num(c.projects))) bad.push(`${c.anchor} projects`);
    if (cells[3] !== fmt(num(c.cases))) bad.push(`${c.anchor} cases`);
    if (cells[4] !== fmt(num(c.healthy_controls))) bad.push(`${c.anchor} healthy controls`);
    if (cells[5] !== fmt(num(c.n_countries))) bad.push(`${c.anchor} countries`);
    if (cells[7] !== fmt(num(c.cases_profiled))) bad.push(`${c.anchor} profiled`);
    const rep = tr.querySelector("td.cn-repl");
    if (rep.dataset.scope === "not tested" && rep.textContent.trim() !== "") bad.push(`${c.anchor} blank replication cell`);
    if (c.replication_scope === "this condition" &&
        !rep.textContent.includes(`${fmt(num(c.replication_claims_replicated))} of ${fmt(num(c.replication_claims_testable))}`)) bad.push(`${c.anchor} replication`);
    if (c.replication_scope === "broader condition" && !rep.textContent.includes("tested as " + c.replication_condition_name)) bad.push(`${c.anchor} tested-as`);
  }
  ok("every cell of the first 60 rows equals condition_explorer.csv", bad.length === 0, bad.slice(0, 4).join("; "));
  ok("the ontology ids link to OLS, one link per distinct id",
     P.qa("#cn-table tbody tr").slice(0, 40).every((tr) => {
       const c = CONDS.find((r) => r.anchor === tr.dataset.anchor);
       const want = [...new Set([c.efo_id, c.mondo_id].filter(Boolean))];
       const got = Array.from(tr.querySelectorAll("td.cn-ids a")).map((a) => a.getAttribute("href"));
       return got.length === want.length && got.every((u, i) => u.startsWith("https://www.ebi.ac.uk/ols4/ontologies/") && u.includes(encodeURIComponent(want[i])));
     }));
  // the age bars: one linear scale from zero over every row, segments in the order of the bands
  const withBar = P.qa("#cn-table tbody tr").filter((tr) => tr.querySelector("svg.cn-agebar"));
  const per = withBar.slice(0, 50).map((tr) => {
    const w = Array.from(tr.querySelectorAll("svg.cn-agebar rect")).reduce((a, r) => a + Number(r.getAttribute("width")), 0);
    return w / num(tr.dataset.cases);
  });
  ok("every age bar is drawn to one linear scale from zero (length ∝ case samples)",
     Math.max(...per) - Math.min(...per) < 1e-3, `${Math.min(...per)}…${Math.max(...per)}`);
  ok("the age bands are stacked in the order neonate → elderly → not stated",
     withBar.slice(0, 40).every((tr) => {
       const bands = Array.from(tr.querySelectorAll("svg.cn-agebar rect")).map((r) => Number(r.getAttribute("class").replace("cn-b", "")));
       return bands.every((b, i) => i === 0 || b > bands[i - 1]);
     }));
  ok("the table offers the three tables for download",
     P.q("#cn-dl").getAttribute("href").endsWith("condition_explorer.csv") &&
     P.qa("#cn-dl2 a").map((a) => a.getAttribute("href")).join(" ").includes("condition_explorer_associations.csv"));
}

// ---------------------------------------------------------------- search
async function search(p, text) {
  p.q("#cn-q").value = text;
  p.q("#cn-q").dispatchEvent(new p.window.Event("input", { bubbles: true }));
  await sleep(160);
  return p.qa("#cn-table tbody tr");
}
{
  let rows = await search(P, "crohn");
  const want = CONDS.filter((c) => [c.condition_name, c.condition_id, c.efo_id, c.mondo_id, c.efo_legacy_ids, c.disease_areas].join(" ").toLowerCase().includes("crohn"));
  ok(`search by name keeps the matching conditions (${want.length})`, rows.length === want.length && rows.length > 0, `${rows.length} vs ${want.length}`);
  ok("…and says how many matched", P.q("#cn-count").textContent.includes(fmt(rows.length)), P.q("#cn-count").textContent);
  const anId = CONDS.find((c) => c.mondo_id);
  rows = await search(P, anId.mondo_id);
  ok("search by ontology id finds the condition", rows.some((tr) => tr.dataset.id === anId.condition_id), `${rows.length} rows for ${anId.mondo_id}`);
  const area = CONDS.find((c) => c.disease_areas);
  rows = await search(P, area.disease_areas.split(";")[0]);
  ok("search by disease area finds its conditions", rows.length > 0 && rows.some((tr) => tr.dataset.id === area.condition_id));
  rows = await search(P, "zzzznotacondition");
  ok("a search with no match empties the table and says so", rows.length === 0 && /0 of/.test(P.q("#cn-count").textContent), P.q("#cn-count").textContent);
  await search(P, "");
  ok("clearing the search box restores every row", P.qa("#cn-table tbody tr").length === CONDS.length);
}

// ---------------------------------------------------------------- sort
{
  const th = (col) => P.qa("#cn-table thead th").find((x) => x.dataset.col === col);
  click(P.window, th("condition_name"));
  let names = P.qa("#cn-table tbody tr td:first-child").map((td) => td.textContent.trim().toLowerCase());
  ok("sorting by condition sorts the names", names.every((v, i) => i === 0 || v.localeCompare(names[i - 1]) >= 0) &&
     th("condition_name").getAttribute("aria-sort") === "ascending", names.slice(0, 2).join(" | "));
  click(P.window, th("condition_name"));
  names = P.qa("#cn-table tbody tr td:first-child").map((td) => td.textContent.trim().toLowerCase());
  ok("clicking it again reverses the order", names.every((v, i) => i === 0 || v.localeCompare(names[i - 1]) <= 0));
  click(P.window, th("projects"));
  const projs = P.C.shown().map((r) => num(r.projects));
  ok("sorting by projects puts the most-studied condition first", projs.every((v, i) => i === 0 || v <= projs[i - 1]) &&
     projs[0] === Math.max(...CONDS.map((r) => num(r.projects))), `${projs[0]}`);
  click(P.window, th("cases"));
  ok("sorting by cases restores the default order", num(P.C.shown()[0].cases) === Math.max(...CONDS.map((r) => num(r.cases))));
}

// ---------------------------------------------------------------- one condition's section, by its own address
const WITH_ASSOC = (() => {
  const by = {};
  ASSOC.forEach((a) => { by[a.condition_id] = (by[a.condition_id] || 0) + 1; });
  const id = Object.keys(by).sort((a, b) => by[b] - by[a])[0];
  return CONDS.find((c) => c.condition_id === id);
})();
async function checkSection(p, c) {
  const bad = [];
  const panel = p.q("#cn-panel");
  if (panel.hidden) return ["section not open"];
  if (panel.dataset.anchor !== c.anchor) bad.push("panel anchor " + panel.dataset.anchor);
  const h2 = panel.querySelector("h2.cn-h");
  if (!h2) return ["no heading"];
  if (h2.id !== c.anchor) bad.push(`the heading's id is not the anchor (${h2.id})`);
  if (h2.textContent.trim() !== c.condition_name) bad.push("heading text");
  const counts = panel.querySelector("ul.cn-counts").textContent;
  for (const [col, what] of [["cases", "cases"], ["projects", "projects"], ["healthy_controls", "healthy controls"],
                             ["cases_profiled", "profiled"], ["case_people", "people"], ["n_countries", "countries"]]) {
    if (!counts.includes(fmt(num(c[col])))) bad.push(`${what} (${fmt(num(c[col]))}) not in the counts`);
  }
  if (num(c.cases_incl_narrower) !== num(c.cases) && !counts.includes(fmt(num(c.cases_incl_narrower)))) bad.push("cases including narrower terms");
  const rel = panel.querySelector("p.cn-rel");
  for (const which of ["broader_condition_ids", "narrower_condition_ids"]) {
    for (const id of String(c[which] || "").split(";").filter(Boolean).slice(0, 8)) {
      const other = CONDS.find((r) => r.condition_id === id);
      if (other && !Array.from(rel.querySelectorAll("a")).some((a) => decodeURIComponent(a.getAttribute("href")) === "#" + other.anchor)) {
        bad.push(`no link to ${which.split("_")[0]} ${id}`);
      }
    }
  }
  const want = PROJ.filter((r) => r.condition_id === c.condition_id).sort((a, b) => num(b.cases) - num(a.cases) || a.study_accession.localeCompare(b.study_accession));
  const got = Array.from(panel.querySelectorAll("#cn-projects tbody tr"));
  if (got.length !== want.length) bad.push(`project list ${got.length} vs ${want.length}`);
  got.forEach((tr, i) => {
    const w = want[i]; if (!w) return;
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent.trim());
    if (tr.dataset.acc !== w.study_accession) bad.push(`project ${i} order: ${tr.dataset.acc} vs ${w.study_accession}`);
    if (cells[2] !== fmt(num(w.cases)) || cells[3] !== fmt(num(w.healthy_controls)) || cells[4] !== fmt(num(w.cases_profiled))) bad.push(`project ${w.study_accession} counts`);
    const href = tr.querySelector("a").getAttribute("href");
    if (!href.endsWith(`studies/${w.study_accession}.html`)) bad.push(`project link ${href}`);
  });
  const wantA = ASSOC.filter((a) => a.condition_id === c.condition_id);
  const gotA = Array.from(panel.querySelectorAll("#cn-assoc tbody tr"));
  if (gotA.length !== wantA.length) bad.push(`associations ${gotA.length} vs ${wantA.length}`);
  gotA.forEach((tr, i) => {
    const w = wantA[i]; if (!w) return;
    const cells = Array.from(tr.querySelectorAll("td")).map((td) => td.textContent.trim());
    if (tr.dataset.taxon !== w.taxon) bad.push(`association ${i} taxon`);
    if (cells[2] !== w.direction) bad.push(`association ${w.taxon} direction`);
    if (w.pooled_auc && cells[5] !== Number(w.pooled_auc).toFixed(3)) bad.push(`association ${w.taxon} AUC ${cells[5]}`);
    if (cells[6] !== fmt(num(w.catalog_projects_tested))) bad.push(`association ${w.taxon} projects tested`);
    const href = tr.querySelector("a").getAttribute("href");
    if (!/taxa\/index\.html#taxon=(g|s)__/.test(decodeURIComponent(href))) bad.push(`taxon link ${href}`);
  });
  const sam = panel.querySelector("#cn-samples").getAttribute("href");
  if (!sam.includes("samples/index.html?condition=" + encodeURIComponent(c.anchor))) bad.push("samples link " + sam);
  return bad;
}
{
  const c = WITH_ASSOC;
  await P.C.nav("#" + c.anchor);
  const bad = await checkSection(P, c);
  ok(`the section of ${c.condition_name} shows its counts, its projects and its replicated associations`, bad.length === 0, bad.slice(0, 5).join("; "));
  ok("the open condition's row is marked in the table", P.qa("#cn-table tbody tr.cn-row-on").length === 1 &&
     P.q("#cn-table tbody tr.cn-row-on").dataset.anchor === c.anchor);
  ok("the address is the condition's anchor", decodeURIComponent(P.window.location.hash) === "#" + c.anchor, P.window.location.hash);
  ok("the detail tables were fetched only now, and only once",
     P.fetched.filter((u) => /condition_explorer_(projects|associations)\.csv$/.test(u)).length === 2, P.fetched.join(" "));
  const untested = CONDS.find((r) => r.replication_scope === "not tested" && PROJ.some((p) => p.condition_id === r.condition_id));
  await P.C.nav("#" + untested.anchor);
  const badU = await checkSection(P, untested);
  ok("a condition no claim was tested for says so in its section", /No published claim was tested/.test(P.q("#cn-panel").textContent) && badU.length === 0, badU.slice(0, 3).join("; "));
  const tr = P.qa("#cn-table tbody tr").find((x) => x.dataset.anchor === WITH_ASSOC.anchor);
  click(P.window, tr.querySelector("a.cn-name"));
  await sleep(40);
  ok("clicking a condition in the table opens its section", P.q("#cn-panel").dataset.anchor === WITH_ASSOC.anchor);
  click(P.window, P.q("#cn-close"));
  ok("closing the section clears the address", P.q("#cn-panel").hidden && P.window.location.hash === "");
}

// ---------------------------------------------------------------- the four statements the page must carry
{
  const full = P.doc.body.textContent.replace(/\s+/g, " ");
  const inDetails = (re) => P.qa("p, li").filter((el) => re.test(el.textContent.replace(/\s+/g, " ")))
    .every((el) => !!(el.closest && el.closest("details.sp-more")));
  const tested = CONDS.filter((c) => c.replication_scope === "this condition");
  const repl = tested.reduce((a, c) => a + num(c.replication_claims_replicated), 0);
  const testable = tested.reduce((a, c) => a + num(c.replication_claims_testable), 0);
  ok("statement 1: what the counts count, and how the labels were mapped",
     /names the condition as the sampled person's main condition/.test(full) &&
     /mapped to EFO [\d.]+ and MONDO \(\d{4}-\d{2}-\d{2}\)/.test(full) && /archive records and the paper text/.test(full));
  ok("statement 2: healthy controls are not matched controls",
     /Healthy controls are the samples labelled healthy in the projects that also hold cases of the condition; they are not matched controls/.test(full));
  ok("statement 3: the replication numbers are the registered result, with its date and its totals",
     /registered result of the test run on \d{4}-\d{2}-\d{2}/.test(full) && full.includes(`${fmt(repl)} of ${fmt(testable)}`) &&
     /tested as.{0,40}broader condition shows the result for that broader condition/.test(full), `${fmt(repl)} of ${fmt(testable)}`);
  ok("statement 4: a condition with no row is not necessarily absent",
     /A condition with no row is not necessarily absent/.test(full) &&
     /label strings name a condition for which no ontology term was found/.test(full) && /are unclear or unsettled/.test(full));
  ok("every statement stands in a collapsed disclosure at the foot, not above the table",
     P.qa("details.sp-more").length >= 3 && P.qa("details.sp-more").every((d) => !d.open) &&
     inDetails(/not matched controls/) && inDetails(/registered result of the test run/) &&
     inDetails(/A condition with no row is not necessarily absent/) && inDetails(/sampled person's main condition/));
  ok("the page names the package it was computed on", /Computed on data package \S+ \(release R\d{4}\.\d+\)/.test(full));
}

ok("no script error during the whole session", P.errors.length === 0, P.errors.slice(0, 2).join(" | "));

// ---------------------------------------------------------------- fresh loads with an address
{
  const A = await openPage("#" + WITH_ASSOC.anchor);
  const badA = await checkSection(A, WITH_ASSOC);
  ok("loading a condition's address opens its section", A.q("#cn-panel").dataset.anchor === WITH_ASSOC.anchor && badA.length === 0, badA.slice(0, 3).join("; "));
  for (const h of ["#__proto__", "#constructor", "#" + encodeURIComponent("<img src=x onerror=alert(1)>"), "#MONDO_9999999"]) {
    const X = await openPage(h);
    ok(`odd address ${h.slice(0, 26)}: the table stands, a plain message, no error, nothing injected`,
       X.qa("#cn-table tbody tr").length === CONDS.length && !X.q("#cn-status").hidden && X.errors.length === 0 &&
       X.qa("#cn-status img, #cn-panel img").length === 0, X.errors.join(" | "));
  }
}

// ---------------------------------------------------------------- the script itself
{
  const js = fs.readFileSync(path.join(ROOT, "static", "sp_conditions.js"), "utf8");
  const hosts = [...new Set((js.match(/https?:\/\/[A-Za-z0-9.\-]+/g) || []))];
  ok("the only absolute URL in the script is the OLS link prefix", hosts.length <= 1 && (hosts.length === 0 || hosts[0] === "https://www.ebi.ac.uk"), hosts.join(" "));
  ok("no eval, no Function constructor, no inline handlers", !/\beval\(|new Function\(/.test(js) && !/\son[a-z]+="/.test(P.raw));
}

console.log(`\n${pass} checks passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
