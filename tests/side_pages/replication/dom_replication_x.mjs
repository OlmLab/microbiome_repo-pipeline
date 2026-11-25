// dom_replication_x.mjs — headless test of the EXPLORATORY add-on (static/sp_replication_x.js) of the replication page.
//
// Same harness as dom_replication.mjs (jsdom, real d3, real sp_core.js, real sp_replication.js), plus the add-on.
// It checks that the collapsed section is drawn from replication_exploratory.json, that an open claim panel gets
// ONE paragraph marked "Exploratory" and nothing else changes, that every claim opens, and that the page is the
// registered page when the exploratory file is missing or belongs to another results file. Expected numbers come
// from the CSV downloads in the preview, not from the JSON the add-on reads.
//
//   NODE_PATH=<node_modules with jsdom> node tests/dom_replication_x.mjs <preview root>
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";
import { createRequire } from "node:module";
const { JSDOM, VirtualConsole } = createRequire(import.meta.url)("jsdom");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = process.argv[2] || path.join(path.dirname(HERE), "preview_site");
const BASE = "http://localhost:8931";
const REL = "atlas/replication.html";
let pass = 0, fail = 0;
const ok = (name, cond, extra = "") => {
  if (cond) { pass++; console.log(`✓  ${name}`); }
  else { fail++; console.log(`✗ FAIL  ${name}${extra ? "  — " + extra : ""}`); }
};
const brief = (e) => String(e?.stack || e?.message || e).split("\n").slice(0, 3).join(" ⏎ ").slice(0, 300);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function csv(file) {
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
const UNC = new Map(csv("replication_exploratory_uncorrected.csv.gz").map((r) => [r.claim_id, r]));
const FEW = new Map(csv("replication_exploratory_fewer_projects.csv.gz").map((r) => [r.claim_id, r]));
const FLOORS = csv("replication_exploratory_floors.csv.gz");
const BYC = csv("replication_exploratory_by_condition.csv");
const fmt = (x) => Number(x).toLocaleString("en-US");

async function openPage(hash = "", opt = {}) {
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
    if (!fs.existsSync(p) || (opt.missing && u.pathname.endsWith(opt.missing))) return { ok: false, status: 404, json: async () => ({}) };
    let j = JSON.parse(fs.readFileSync(p, "utf8"));
    if (opt.tamper && u.pathname.endsWith("replication_exploratory.json")) j = opt.tamper(j);
    return { ok: true, status: 200, json: async () => j };
  };
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  window.eval(fs.readFileSync(path.join(ROOT, "static", "vendor", "d3-7.9.0.min.js"), "utf8"));
  window.eval(raw.match(/window\.SP_PAGE=\{[\s\S]*?\};/)[0]);
  window.eval(fs.readFileSync(path.join(ROOT, "static", "sp_core.js"), "utf8"));
  window.SP.go = () => {};
  window.eval(fs.readFileSync(path.join(ROOT, "static", "sp_replication.js"), "utf8"));
  if (!opt.noAddon) {
    window.eval(fs.readFileSync(path.join(ROOT, "static", "sp_replication_x.js"), "utf8"));
    await window.SP_REPLICATION_X.ready;
  } else await window.SP_REPLICATION.ready;
  return { window, doc: window.document, errors, fetched, R: window.SP_REPLICATION, RX: window.SP_REPLICATION_X, raw,
           q: (s) => window.document.querySelector(s), qa: (s) => Array.from(window.document.querySelectorAll(s)) };
}
async function show(P, id) { P.R.nav("claim=" + encodeURIComponent(id)); await sleep(0); await sleep(0); }
const strip = (P) => { const c = P.q("#rp-panel").cloneNode(true); c.querySelectorAll(".rpx-note").forEach((e) => e.remove()); return c.innerHTML; };

// ---------------------------------------------------------------- the template
const P = await openPage();
ok("no script error", P.errors.length === 0, P.errors.join(" | "));
ok("the page loads the add-on after the registered script", /sp_replication\.js"><\/script>\s*<script src="[^"]*sp_replication_x\.js"/.test(P.raw));
ok("the exploratory section is a closed disclosure at the foot, below the graphic", !!P.q("section.sp-details details#rp-explore") && !P.q("#rp-explore").open);
ok("its summary and first paragraph say it is not part of the plan", /Exploratory/.test(P.q("#rp-explore > summary").textContent) && /Not part of the plan/.test(P.q("#rp-explore > p").textContent));
ok("the lead above the graphic is unchanged by the add-on", !/xploratory/.test(P.q(".sp-lead").textContent));
ok("two data files were fetched, the registered one first", P.fetched.length === 2 && P.fetched[0].endsWith("/replication.json") && P.fetched[1].endsWith("/replication_exploratory.json"), P.fetched.join(" "));
ok("no image in the section", P.qa("#rp-explore img").length === 0);

// ---------------------------------------------------------------- the section
const body = P.q("#rpx-body");
ok("the section is drawn", body.dataset.ready === "1" && P.qa("#rpx-body table").length === 5, `${P.qa("#rpx-body table").length} tables`);
const unc = [...UNC.values()];
const nUnc = unc.filter((r) => r.uncorrected_p_below_0_05 === "True").length, nReg = unc.filter((r) => r.registered_replicated === "True").length;
const nOpp = unc.filter((r) => r.uncorrected_opposite_p_below_0_05 === "True").length;
const t1 = P.q("#rpx-unc").textContent;
ok(`item 1 sentence: ${nUnc} of ${unc.length} without correction, ${nReg} with it, ${nOpp} opposite`,
   t1.includes(`${fmt(nUnc)} of ${fmt(unc.length)} (${(100 * nUnc / unc.length).toFixed(1)} %)`) && t1.includes(`${fmt(nReg)} of ${fmt(unc.length)}`) && t1.includes(`${fmt(nOpp)} of ${fmt(unc.length)}`), t1.slice(0, 200));
const condRows = BYC.filter((r) => r.analysis.startsWith("uncorrected"));
const tr1 = Array.from(P.qa("#rpx-body table")[0].querySelectorAll("tbody tr"));
ok(`item 1 table: one row per condition with a testable claim (${condRows.length})`, tr1.length === condRows.length);
let bad = 0;
tr1.forEach((tr) => {
  const td = Array.from(tr.children).map((c) => c.textContent), r = condRows.find((x) => x.condition === td[0]);
  const want = unc.filter((x) => x.case_condition === td[0]);
  if (!r || td[1] !== fmt(want.length) || !td[2].startsWith(fmt(want.filter((x) => x.registered_replicated === "True").length) + " (") ||
      !td[3].startsWith(fmt(want.filter((x) => x.uncorrected_p_below_0_05 === "True").length) + " (") ||
      td[5] !== (100 * Number(r.chance_mean)).toFixed(1) + " %") bad++;
});
ok("item 1 table: every row equals the per-claim table counted by condition", bad === 0, `${bad} rows differ`);
const floors = [...new Set(FLOORS.map((r) => r.floor))].sort((a, b) => a - b);
const tr2 = Array.from(P.qa("#rpx-body table")[1].querySelectorAll("tbody tr"));
bad = 0;
floors.forEach((f, j) => {
  const g = FLOORS.filter((r) => r.floor === f), td = Array.from(tr2[j].children).map((c) => c.textContent);
  const nt = g.filter((r) => r.status === "testable" && UNC.has(r.claim_id)).length, nr = g.filter((r) => r.outcome === "replicated").length;
  const r2n = g.filter((r) => r.status === "testable" && r.outcome !== "replicated" && UNC.get(r.claim_id)?.registered_replicated === "True").length;
  const n2r = g.filter((r) => r.outcome === "replicated" && UNC.get(r.claim_id)?.registered_replicated === "False").length;
  const regSame = g.filter((r) => r.status === "testable" && UNC.get(r.claim_id)?.registered_replicated === "True").length;
  if (td[2] !== fmt(nt) || !td[3].startsWith(fmt(nr) + " (") || td[5] !== fmt(r2n) || td[6] !== fmt(n2r) || !td[7].startsWith(fmt(unc.length - nt) + " (") ||
      !td[4].startsWith(`${fmt(regSame)} (${(100 * regSame / nt).toFixed(1)} %)`) || regSame !== nr + r2n - n2r) bad++;
});
ok(`item 2 table: ${floors.length} floors, counts equal the per-claim table, each beside the registered count on the same claims`, tr2.length === floors.length && bad === 0, `${bad} rows differ`);
ok("item 2 says the comparison is on the claims that stay testable, not with the headline", /on the claims that stay testable/.test(P.q("#rpx-floor").textContent));
const few = [...FEW.values()];
const tr3 = Array.from(P.qa("#rpx-body table")[2].querySelectorAll("tbody tr"));
bad = 0;
[1, 2].forEach((k, j) => {
  const g = few.filter((r) => Number(r.cohorts) === k), td = Array.from(tr3[j].children).map((c) => c.textContent);
  const u = g.filter((r) => Number(r.p_rep) < 0.05).length, o = g.filter((r) => Number(r.p_opp) < 0.05).length, b = g.filter((r) => Number(r.q_rep) < 0.05).length;
  const cohorts = new Map(); g.forEach((r) => r.units_used.split(";").forEach((x) => cohorts.set(x, (cohorts.get(x) || 0) + 1)));
  if (td[1] !== fmt(g.length) || !td[4].startsWith(`${fmt(u)} of ${fmt(g.length)}`) || !td[5].startsWith(`${fmt(o)} of ${fmt(g.length)}`) || !td[7].startsWith(`${fmt(b)} of ${fmt(g.length)}`) ||
      !td[3].startsWith(`${cohorts.size} (the largest carries ${fmt(Math.max(...cohorts.values()))} claims)`)) bad++;
});
ok("item 3 table: one-cohort and two-cohort rows equal the per-claim table, kept apart", tr3.length === 2 && bad === 0 && /one cohort/i.test(tr3[0].textContent) && /two cohorts/i.test(tr3[1].textContent));
ok("item 3 says one cohort is not a replication, that the results are not independent, and what the own cohort has to do with it",
   /not a replication across studies/.test(P.q("#rpx-few").textContent) && /not independent/.test(P.q("#rpx-few").textContent) && /claim’s own cohort was set aside/.test(P.q("#rpx-few").textContent));
const few1 = few.filter((r) => r.cohorts === "1");
ok("item 3 gives the opposite-direction share of the one-cohort group beside its shuffled-label level",
   P.q("#rpx-few-note").textContent.includes((100 * few1.filter((r) => Number(r.p_opp) < 0.05).length / few1.length).toFixed(1) + " %") && /with shuffled labels/.test(P.q("#rpx-few-note").textContent));
ok("item 1 says an uncorrected p-value is not a replication", /not a replication by the plan/.test(P.q("#rpx-unc").textContent));
const links = P.qa("#rpx-body .rpx-dl a").map((a) => a.getAttribute("href"));
ok("every download of the section exists in the preview", links.length === 5 && links.every((h) => fs.existsSync(path.join(ROOT, "atlas", h))), links.join(" "));

// ---------------------------------------------------------------- the claim panel
const claims = P.R.claims();
const P0 = await openPage("", { noAddon: true });
const firstRep = claims.find((c) => c.st === 0), firstInc = claims.find((c) => c.st === 2);
for (const c of [firstRep, firstInc]) {
  await show(P, c.id); await show(P0, c.id);
  const note = P.q("#rpx-note"), r = UNC.get(c.id);
  ok(`testable claim ${c.id}: one exploratory paragraph, tagged, right after the caveat`,
     P.qa(".rpx-note").length === 1 && note.previousElementSibling?.id === "rp-caveat" && note.querySelector(".rpx-tag")?.textContent === "Exploratory");
  const p = Number(r.p_rep), txt = note.textContent;
  const shown = txt.match(/p (=|<) ([0-9.e-]+)/);
  ok(`  its p-value is the table's (${r.p_rep}) and on the same side of 0.05`,
     shown && (shown[1] === "<" ? p < 0.0001 : Math.abs(Number(shown[2]) - p) <= 0.06 * p && (Number(shown[2]) < 0.05) === (p < 0.05)) &&
     txt.includes(p < 0.05 ? "below 0.05" : "not below 0.05") && (p < 0.05 || !/: p [=<] [0-9.e-]+ in the claimed direction, below/.test(txt)), txt.slice(0, 160));
  ok(`  projects in the claimed direction: ${r.n_units_claimed_direction} of ${r.k}`, txt.includes(`${r.n_units_claimed_direction} of ${r.k} projects point in the claimed direction`));
  const fl = floors.map((f) => { const g = FLOORS.find((x) => x.floor === f && x.claim_id === c.id); return g.outcome === "replicated" ? "replicated" : g.status === "testable" ? "not replicated" : "not testable"; });
  ok(`  floors: ${fl.join(", ")}`, floors.every((f, j) => txt.includes(`${fl[j]} at ${+(100 * f).toPrecision(3)} %`)), txt.slice(160, 400));
  ok("  everything else in the panel is the registered panel, byte for byte", strip(P) === P0.q("#rp-panel").innerHTML);
}
const one = claims.find((c) => FEW.get(c.id)?.cohorts === "1"), two = claims.find((c) => FEW.get(c.id)?.cohorts === "2");
for (const c of [one, two]) {
  await show(P, c.id); await show(P0, c.id);
  const r = FEW.get(c.id), txt = P.q("#rpx-note")?.textContent || "";
  ok(`untested claim ${c.id}: exploratory paragraph says "${r.cohorts === "1" ? "one cohort" : "two cohorts"}", ${r.n_case} cases, ${r.n_control} controls`,
     (r.cohorts === "1" ? /tested in one cohort/i : /tested in two cohorts/i).test(txt) && txt.includes(`${fmt(r.n_case)} cases, ${fmt(r.n_control)} controls`), txt.slice(0, 200));
  ok(`  estimate ${Number(r.auc_pooled).toFixed(3)} and interval as in the table; the label stays "not tested"`,
     txt.includes(Number(r.auc_pooled).toFixed(3)) && txt.includes(`${Number(r.ci95_low).toFixed(3)}–${Number(r.ci95_high).toFixed(3)}`) && /stays “not tested”/.test(txt) &&
     /Not tested/.test(P.q("#rp-panel .rp-badge").textContent) && strip(P) === P0.q("#rp-panel").innerHTML);
}
const none = claims.find((c) => c.st === 5 && !c.test.claims.some((q) => FEW.has(q.id))), rare = claims.find((c) => c.st === 6);
await show(P, none.id);
ok("an untested claim with no usable cohort gets no exploratory paragraph", P.qa(".rpx-note").length === 0);
await show(P, rare.id);
ok("a claim whose taxon is too rare gets no exploratory paragraph", P.qa(".rpx-note").length === 0);

// every claim: the paragraph is there exactly when the tables have something for the row, and never twice
// A row of untested claims can hold several papers' claims; a cohort usable for one is another paper's own. Every
// result must name the claims it belongs to, and a claim without a result of its own must be told so.
const PMID = P.R.data().papers.pmid;
let wrong = 0, nNote = 0, errs0 = P.errors.length, nOwn = 0, wrongOwn = 0;
for (const c of claims) {
  await show(P, c.id);
  const k = P.qa(".rpx-note").length, want = c.st < 5 ? UNC.has(c.id) : c.test.claims.some((q) => FEW.has(q.id));
  if (k !== (want ? 1 : 0)) wrong++;
  if (k) nNote++;
  if (c.st === 5 && k) {
    const txt = P.q("#rpx-note").textContent;
    const tested = new Set(), own = new Set();
    for (const m of txt.matchAll(/For the claim of PubMed ([0-9, ]+): tested in/g)) m[1].split(", ").forEach((x) => tested.add(x));
    for (const m of txt.matchAll(/For the claim of PubMed ([0-9, ]+) the only cohort is the paper’s own/g)) m[1].split(", ").forEach((x) => own.add(x));
    const wantTested = new Set(c.test.claims.filter((q) => FEW.has(q.id)).map((q) => String(PMID[q.p])));
    const wantOwn = new Set(c.test.claims.filter((q) => !FEW.has(q.id)).map((q) => String(PMID[q.p])));
    const results = new Set(c.test.claims.filter((q) => FEW.has(q.id)).map((q) => { const r = FEW.get(q.id); return [r.cohorts, r.auc_pooled, r.p_rep, r.n_case, r.n_control].join("|"); }));
    const eq = (a, b) => a.size === b.size && [...a].every((x) => b.has(x));
    const plain = wantOwn.size === 0 && results.size === 1;          // one result for every claim of the row: no paper needs naming
    if (!(plain ? tested.size === 0 && own.size === 0 : eq(tested, wantTested) && eq(own, wantOwn))) wrongOwn++;
    if (!FEW.has(c.id)) { nOwn++; if (!own.has(String(PMID[c.p]))) wrongOwn++; }
  }
  if (c.st < 5 && k) {
    const p = Number(UNC.get(c.id).p_rep);
    if (P.q("#rpx-note").textContent.includes("not below 0.05") !== !(p < 0.05)) wrong++;
  }
}
ok(`all ${claims.length} claims open; the exploratory paragraph appears exactly where it should (${nNote} panels)`, wrong === 0 && P.errors.length === errs0, `${wrong} wrong; ${P.errors.slice(errs0).join(" | ")}`);
ok(`no panel shows a one- or two-cohort result as if it were the open claim's when the cohort is that claim's own (${nOwn} such claims, each told so by name)`, wrongOwn === 0, `${wrongOwn} wrong`);
ok("every testable claim and every claim tested in one or two cohorts is reachable", claims.filter((c) => c.st < 5).length === UNC.size && [...FEW.keys()].every((id) => claims.some((c) => c.id === id && c.st === 5)));

// ---------------------------------------------------------------- without the exploratory file, or with a wrong one
const Pm = await openPage("", { missing: "replication_exploratory.json" });
await show(Pm, firstRep.id); await show(P0, firstRep.id);
ok("exploratory file missing: the page is the registered page, no paragraph, no error", Pm.errors.length === 0 && Pm.qa(".rpx-note").length === 0 &&
   Pm.q("#rpx-body").dataset.ready === "failed" && Pm.q("#rp-panel").innerHTML === P0.q("#rp-panel").innerHTML && Pm.q("#rp-status").hidden === true);
ok("  the section then keeps its links to the tables", Pm.qa("#rpx-body a").length === 5);
const Pt = await openPage("", { tamper: (j) => { j.pair.last_testable = "bsdb:0/0/0|0"; return j; } });
await show(Pt, firstRep.id);
ok("exploratory file built for another results file: nothing is shown from it", Pt.qa(".rpx-note").length === 0 && Pt.q("#rpx-body").dataset.ready === "failed" && Pt.qa("#rpx-body table").length === 0);
const src = fs.readFileSync(path.join(ROOT, "static", "sp_replication_x.js"), "utf8");
ok("no eval, no Function constructor, no inline handlers, no outside address in the add-on", !/\beval\(|new Function|\son[a-z]+=|https?:\/\//.test(src));

console.log(`\n${pass} checks passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
