#!/usr/bin/env python
"""data_check.py — replication.json (what the page reads) against the run's own result files.

Decodes the page file with its own small reader (no code shared with replication_page.py) and compares, claim
by claim and project by project, with results/real_strict_primary_all/claim_results.csv,
project_effects.parquet, headline.json, the sensitivity headlines, results/permutation_reference.json,
tables/registered_claims.csv, tables/taxon_map.csv and tables/discovery_links.csv. Nothing is written.

    python tests/data_check.py --payload <payload dir> --replication ~/catalog/projects/replication
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import duckdb
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--payload", required=True)
ap.add_argument("--replication", required=True)
a = ap.parse_args()
R, P = Path(a.replication).expanduser(), Path(a.payload)
D = json.loads((P / "replication.json").read_text(encoding="utf-8"))
M = D["meta"]
res = R / "results" / "real_strict_primary_all"
n_pass = n_fail = 0
fails = []


def ok(name, cond, extra=""):
    global n_pass, n_fail
    if cond:
        n_pass += 1
    else:
        n_fail += 1
        fails.append("%s %s" % (name, extra))


cr = pd.read_csv(res / "claim_results.csv", low_memory=False).set_index("claim_id")
rc = pd.read_csv(R / "tables" / "registered_claims.csv", low_memory=False).set_index("claim_id")
tm = pd.read_csv(R / "tables" / "taxon_map.csv", low_memory=False)
tm["taxon_key"] = tm.level + ":" + tm.ncbi_taxid_used.astype("Int64").astype(str)
tm = tm.dropna(subset=["gtdb_anchor"]).drop_duplicates("taxon_key").set_index("taxon_key")
eff = duckdb.connect().execute("SELECT * FROM read_parquet(?)", [str(res / "project_effects.parquet")]).df()
eff_ix = {(r.contrast_id, r.taxon_key, r.study_accession): r for r in eff.itertuples()}
unit_of = {(r.contrast_id, r.study_accession): r.unit for r in eff.itertuples()}
links = pd.read_csv(R / "tables" / "discovery_links.csv").dropna(subset=["cohort_unit"])
own = links.groupby("exp_id").cohort_unit.agg(set).to_dict()
H = json.loads((res / "headline.json").read_text())

cl, cond, taxa, pap, cp, eg, ef = D["claims"], D["conditions"], D["taxa"], D["papers"], D["cp"], D["groups"], D["effects"]
n, nT = len(cl["id"]), cl["n_testable"]
ok("claim count", n == len(cr) == M["registered"], str(n))
ok("claim ids are the registered ids", set(cl["id"]) == set(cr.index) and len(set(cl["id"])) == n)
ok("testable count", nT == H["overall"]["testable"] == M["testable"])
STATUS = D["status"]
code = {"fewer_than_3_projects_for_condition": "untestable_few_projects", "taxon_too_rare_in_projects": "untestable_taxon_rare"}
bad = Counter()
for i, cid in enumerate(cl["id"]):
    r, g = cr.loc[cid], rc.loc[cid]
    ci, ti = cl["c"][i], cl["t"][i]
    if cond["name"][ci] != r.case_condition or cond["efo"][ci] != r.contrast_id: bad["condition"] += 1
    if taxa["key"][ti] != r.taxon_key or taxa["name"][ti] != g.ncbi_name_used: bad["taxon"] += 1
    if ("species" if taxa["rank"][ti] else "genus") != g.level: bad["rank"] += 1
    if pap["pmid"][cl["p"][i]] != int(float(r.pmid)): bad["paper"] += 1
    if (cl["s"][i] == 1) != (int(r.sign) == 1): bad["direction"] += 1
    want = r.sublabel if isinstance(r.sublabel, str) else code[r.reason]
    if STATUS[cl["st"][i]] != want: bad["result label"] += 1
    seq = D["seq"][cl["q"][i]]
    if seq != (r.sequencing_type if isinstance(r.sequencing_type, str) else "not recorded"): bad["sequencing type"] += 1
    if (i < nT) != (r.status == "testable"): bad["testable flag"] += 1
    if i >= nT: continue
    for k, col in (("auc", "auc_pooled"), ("lo", "ci95_low"), ("hi", "ci95_high")):
        if abs(cl[k][i] / 1e6 - r[col]) > 2.1e-6: bad[col] += 1
        if "%.3f" % (cl[k][i] / 1e6) != "%.3f" % r[col]: bad["three decimals as shown"] += 1
    if abs(cl["tu"][i] / 1e6 - r.theta_upper95) > 2.1e-6: bad["upper limit"] += 1
    if cl["k"][i] != int(r.k): bad["k"] += 1
    if abs(cl["qr"][i] - r.q_rep) > 0.006 * max(r.q_rep, 1e-300) + 1e-12: bad["q_rep"] += 1
    if abs(cl["qo"][i] - r.q_opp) > 0.006 * max(r.q_opp, 1e-300) + 1e-12: bad["q_opp"] += 1
    if (cl["hk"][i] == 1) != (r.q_rep_hksj < 0.05): bad["stricter test flag"] += 1
    if cl["i2"][i] != int(round(100 * r.I2)): bad["I2"] += 1
    # projects: the page's rule (group rows minus own cohort minus rare) must give exactly the units the run used
    gi = cl["g"][i]
    rows = list(range(eg["start"][gi], eg["start"][gi + 1]))
    excl = set(cl["x"].get(str(i), []))
    used, own_drawn = set(), set()
    for pos, j in enumerate(rows):
        acc = D["projects"]["acc"][cp["proj"][ef["cp"][j]]]
        if cp["cond"][ef["cp"][j]] != ci: bad["project row belongs to another condition"] += 1
        e = eff_ix.get((r.contrast_id, r.taxon_key, acc))
        if e is None: bad["project row not in project_effects"] += 1; continue
        if abs(ef["auc"][j] / 1e6 - e.auc) > 2.1e-6 or abs(ef["se"][j] / 1e6 - e.var ** 0.5) > 2.1e-6: bad["project estimate"] += 1
        if "%.3f" % (ef["auc"][j] / 1e6) != "%.3f" % e.auc: bad["three decimals as shown"] += 1
        if ef["nz"][j] != e.n_nonzero or cp["nc"][ef["cp"][j]] != e.n_case or cp["nk"][ef["cp"][j]] != e.n_control: bad["project counts"] += 1
        u = unit_of[(r.contrast_id, acc)]
        if pos in excl: own_drawn.add(u)
        elif ef["nz"][j] >= M["min_detected"]: used.add(u)
    if used != set(r.units_used.split(";")): bad["projects used"] += 1
    if len(rows) != int(r.n_units_all): bad["projects in group"] += 1
    if own_drawn != (own.get(r.exp_id, set()) & {unit_of[(r.contrast_id, D["projects"]["acc"][cp["proj"][ef["cp"][j]]])] for j in rows}): bad["own cohort"] += 1
    if len(own_drawn) != int(r.n_units_discovery_excluded): bad["own cohort count"] += 1
for k in ["condition", "taxon", "rank", "paper", "direction", "result label", "sequencing type", "testable flag", "auc_pooled", "ci95_low",
          "ci95_high", "three decimals as shown", "upper limit", "k", "q_rep", "q_opp", "stricter test flag", "I2", "project row belongs to another condition",
          "project row not in project_effects", "project estimate", "project counts", "projects used", "projects in group", "own cohort",
          "own cohort count"]:
    ok("every claim: " + k, bad[k] == 0, "%d differ" % bad[k])

# conditions, headline, checks
cnt = Counter((cl["c"][i], cl["st"][i]) for i in range(n))
for name, b in H["by_condition"].items():
    ci = cond["name"].index(name)
    ok("condition " + name, cnt[(ci, 0)] + cnt[(ci, 1)] == b["replicated"] and sum(cnt[(ci, s)] for s in range(5)) == b["testable"])
ok("headline", M["replicated"] == H["overall"]["replicated"] and abs(M["rate"] - H["overall"]["rate"]) < 1e-12)
ok("result counts", all(sum(cnt[(c, s)] for c in range(len(cond["name"]))) == H["sublabels"][STATUS[s]] for s in range(5)))
ok("untestable counts", sum(v for (c, s), v in cnt.items() if s == 5) == H["untestable_reasons"]["fewer_than_3_projects_for_condition"]
   and sum(v for (c, s), v in cnt.items() if s == 6) == H["untestable_reasons"]["taxon_too_rare_in_projects"])
C = M["checks"]
ok("stricter test", C["stricter"]["replicated"] == H["side_tests"]["replicated_hartung_knapp"] == int((cr.q_rep_hksj < 0.05).sum()))
s6 = json.loads((R / "results" / "real_strict_primary_all_subjid0.9" / "headline.json").read_text())["overall"]
ok("person identifiers", C["person_id"]["replicated"] == s6["replicated"] and C["person_id"]["testable"] == s6["testable"])
s6c = pd.read_csv(R / "results" / "real_strict_primary_all_subjid0.9" / "claim_results.csv", low_memory=False)
same = cr.loc[s6c[s6c.status == "testable"].claim_id]
ok("person identifiers, same claims in the primary analysis", C["person_id"]["same_claims_primary"] == int((same.outcome == "replicated").sum()))
big = max(H["by_condition"], key=lambda k: H["by_condition"][k]["testable"])
ok("largest condition", C["largest"]["name"] == big and C["largest"]["testable"] == H["by_condition"][big]["testable"]
   and C["without_largest"]["replicated"] == H["overall"]["replicated"] - H["by_condition"][big]["replicated"])
ok("mean over conditions", abs(C["across_conditions"]["rate"] - H["unweighted_mean_of_condition_rates"]) < 1e-12)
perm = json.loads((R / "results" / "permutation_reference.json").read_text())
ok("shuffled labels", C["shuffled"]["maximum"] == max(perm["replicated_per_seed"]) and C["shuffled"]["n"] == len(perm["replicated_per_seed"]) == 1000
   and perm["testable"] == M["testable"])
for s in M["sensitivity"]:
    d = {"primary": "real_strict_primary_all", "s1_16s": "real_strict_primary_16S", "s1_wms": "real_strict_primary_WMS", "s2": "real_lenient_primary_all",
         "s3_strict": "real_strict_strict_all", "s3_broad": "real_strict_broad_all", "s5": "real_strict_primary_all_adjusted",
         "s6": "real_strict_primary_all_subjid0.9", "s7": "real_strict_primary_all_nodiscoveryexclusion",
         "s8": "real_strict_primary_all_dropEFO0005406", "s9": "real_strict_primary_all_crconly"}[s["key"]]
    h = json.loads((R / "results" / d / "headline.json").read_text())
    ok("sensitivity " + s["key"], s["testable"] == h["overall"]["testable"] and s["replicated"] == h["overall"]["replicated"])
# taxa and papers
tb = 0
for i, k in enumerate(taxa["key"]):
    t = tm.loc[k]
    if taxa["gtdb"][i] != t.gtdb_anchor: tb += 1
    extra = [x.lstrip("!") for x in taxa["targets"].get(str(i), [])]
    if set(extra) | {t.gtdb_anchor} != set(t.gtdb_targets_primary.split(";")): tb += 1
ok("taxa: GTDB anchor and the groups counted", tb == 0, "%d differ" % tb)
yr = rc.reset_index().assign(pmid=lambda d: d.pmid.astype(float).astype(int)).drop_duplicates("pmid").set_index("pmid").year
ok("papers: year", all(str(pap["year"][i]) == str(int(yr[p])) for i, p in enumerate(pap["pmid"]) if pd.notna(yr[p])))
ok("freeze hash", M["freeze_hash"] == json.loads((R / (R / "FROZEN_CURRENT.txt").read_text().strip() / "MANIFEST.json").read_text())["freeze_hash"])
print("%d pass, %d fail" % (n_pass, n_fail))
for f in fails[:20]:
    print("  FAIL", f)
sys.exit(1 if n_fail else 0)
