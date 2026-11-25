#!/usr/bin/env python
"""data_check_x.py — replication_exploratory.json (what the add-on reads) against the RUN's own result files.

Where it can, this recomputes the exploratory numbers from results/ without the exploratory scripts or their tables:
item 1 from results/real_strict_primary_all/claim_results.csv and the 1,000 permuted claim_results.csv; item 3's
one-cohort tests from project_effects.parquet with a normal test written here. Item 2 (floors) and the two-cohort
pooling need the abundance matrix and the frozen pooling code, so there the file is compared with the per-claim
tables in the payload and their internal sums. It also checks that replication.json is the registered file.

    python tests/data_check_x.py --payload <payload dir> --replication ~/catalog/projects/replication [--permutations 1000]
"""
import argparse
import gzip
import hashlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ap = argparse.ArgumentParser()
ap.add_argument("--payload", required=True)
ap.add_argument("--replication", required=True)
ap.add_argument("--permutations", type=int, default=1000)
ap.add_argument("--registered-sha256", default="60398c79e310c8ba00852cf67fdba7c266e4cab766fc159df9383ed2efbdbad6")
a = ap.parse_args()
R, P = Path(a.replication).expanduser(), Path(a.payload)
main_bytes = (P / "replication.json").read_bytes()
D, X = json.loads(main_bytes), json.loads((P / "replication_exploratory.json").read_text(encoding="utf-8"))
n_pass = n_fail = 0


def ok(name, cond, extra=""):
    global n_pass, n_fail
    if cond:
        n_pass += 1
        print("PASS ", name)
    else:
        n_fail += 1
        print("FAIL ", name, "--", extra)


def same(shown, true, rel=0.06):
    """A p-value rounded to two significant digits, never across 0.05."""
    return shown is not None and abs(shown - true) <= rel * abs(true) + 1e-300 and (shown < 0.05) == (true < 0.05)


def gzcsv(name):
    return pd.read_csv(io.BytesIO(gzip.decompress((P / name).read_bytes())))


sha = hashlib.sha256(main_bytes).hexdigest()
ok("replication.json is the registered page file (sha256 %s...)" % a.registered_sha256[:12], sha == a.registered_sha256, sha)
ok("the exploratory file was built for it", X["pair"]["main_sha256"] == sha and X["pair"]["claims"] == len(D["claims"]["id"]) and X["pair"]["testable"] == D["claims"]["n_testable"])
ok("the file says what it is", X["label"].lower().startswith("exploratory") and X["meta"]["package"] == D["meta"]["package"])
ids, nT = D["claims"]["id"], D["claims"]["n_testable"]
res = pd.read_csv(R / "results" / "real_strict_primary_all" / "claim_results.csv", low_memory=False).set_index("claim_id")
t = res.loc[ids[:nT]]
ok("item 1: the first %d claims of the page are the run's testable claims" % nT, (t.status == "testable").all() and (res.status == "testable").sum() == nT)
ok("item 1: per-claim p-values equal the run's p_rep, rounded, never across 0.05", all(same(s, v) for s, v in zip(X["claims"]["p"], t.p_rep)),
   str([(s, v) for s, v in zip(X["claims"]["p"], t.p_rep) if not same(s, v)][:3]))
ok("item 1: per-claim opposite-direction p-values equal the run's p_opp", all(same(s, v) for s, v in zip(X["claims"]["po"], t.p_opp)))
ok("item 1: projects in the claimed direction equal the run's n_units_claimed_direction", X["claims"]["d"] == t.n_units_claimed_direction.astype(int).tolist())
u = X["uncorrected"]
ok("item 1: %d testable, %d registered, %d without correction, %d opposite" % (u["testable"], u["registered"], u["n"], u["opposite"]),
   u["testable"] == nT and u["registered"] == int((t.outcome == "replicated").sum()) == D["meta"]["replicated"] and u["n"] == int((t.p_rep < 0.05).sum()) and u["opposite"] == int((t.p_opp < 0.05).sum()))
ok("item 1: the count without correction is the one the registered page already prints", u["n"] == D["meta"]["checks"]["unadjusted"]["claimed"]["n"] if isinstance(D["meta"]["checks"]["unadjusted"]["claimed"], dict) and "n" in D["meta"]["checks"]["unadjusted"]["claimed"]
   else abs(u["n"] / nT - D["meta"]["checks"]["unadjusted"]["claimed"]["share"]) < 5e-4)
bc = t.groupby("case_condition").apply(lambda g: (len(g), int((g.outcome == "replicated").sum()), int((g.p_rep < 0.05).sum()), int((g.p_opp < 0.05).sum())))
ok("item 1: by-condition rows equal the run's results (%d conditions)" % len(bc), len(u["rows"]) == len(bc) and all(tuple(r[1:5]) == bc[r[0]] for r in u["rows"]))
ok("item 1: more than half of the projects in the claimed direction", u["majority"]["n"] == int((t.n_units_claimed_direction / t.k > 0.5).sum()) and u["majority"]["all"] == int((t.n_units_claimed_direction == t.k).sum()))
if a.permutations:
    rate, by = [], {r[0]: [] for r in u["rows"]}
    for s in range(1, a.permutations + 1):
        p = pd.read_csv(R / "results" / ("permuted_strict_primary_all_seed%d" % s) / "claim_results.csv", usecols=["case_condition", "status", "p_rep"])
        p = p[p.status == "testable"]
        rate.append(float((p.p_rep < 0.05).mean()))
        m = (p.p_rep < 0.05).groupby(p.case_condition).mean()
        for c in by:
            by[c].append(float(m.get(c, np.nan)))
    rate = np.array(rate)
    ok("item 1: chance level from %d shuffled runs: mean %.4f, 2.5 %% %.4f, 97.5 %% %.4f" % (len(rate), rate.mean(), np.quantile(rate, 0.025), np.quantile(rate, 0.975)),
       u["permutations"] == a.permutations and abs(u["chance"]["mean"] - rate.mean()) < 1e-9 and abs(u["chance"]["lo"] - np.quantile(rate, 0.025)) < 1e-9
       and abs(u["chance"]["hi"] - np.quantile(rate, 0.975)) < 1e-9 and abs(u["chance"]["max"] - rate.max()) < 1e-9
       and abs(u["chance"]["p"] - (1 + (rate >= u["n"] / nT).sum()) / (1 + len(rate))) < 1e-9)
    ok("item 1: chance level by condition", all(abs(r[5] - np.nanmean(by[r[0]])) < 1e-5 and abs(r[6] - np.nanquantile(by[r[0]], 0.975)) < 1e-5 for r in u["rows"]),
       str([(r[0], r[5], float(np.nanmean(by[r[0]]))) for r in u["rows"] if abs(r[5] - np.nanmean(by[r[0]])) >= 1e-5][:3]))

# ---- item 2
fc = gzcsv("replication_exploratory_floors.csv.gz")
fv = sorted(fc.floor.unique())
ok("item 2: %d floors, each with every registered claim" % len(fv), len(X["floors"]) == len(fv) and all((fc.floor == f).sum() == len(ids) for f in fv))
reg = (t.outcome == "replicated")
codes = np.array(X["claims"]["f"])
for j, f in enumerate(fv):
    g = fc[fc.floor == f].set_index("claim_id")
    d = X["floors"][j]
    gt = g.loc[ids[:nT]]
    st, rep = (gt.status == "testable").values, (gt.outcome == "replicated").values
    ok("item 2: floor %g: testable %d, replicated %d, lost %d, replicated->not %d, not->replicated %d" % (f, d["testable"], d["replicated"], d["no_longer_testable"], d["rep_to_not"], d["not_to_rep"]),
       abs(d["floor"] - f) < 1e-12 and d["testable"] == int((g.status == "testable").sum()) == int(st.sum()) and d["replicated"] == int(rep.sum())
       and d["no_longer_testable"] == int((~st).sum()) and d["lost_replicated"] == int((~st & reg.values).sum())
       and d["rep_to_not"] == int((st & ~rep & reg.values).sum()) and d["not_to_rep"] == int((rep & ~reg.values).sum()) and d["same"] == int((st & (rep == reg.values)).sum()))
    ok("item 2: floor %g: registered count on the claims that stay testable = %d" % (f, d["registered_same_claims"]), d["registered_same_claims"] == int((st & reg.values).sum()))
    code = (codes // 3 ** (len(fv) - 1 - j)) % 3
    ok("item 2: floor %g: per-claim codes equal the table" % f, (code == np.where(rep, 0, np.where(st, 1, 2))).all())
    ok("item 2: floor %g: a floor never makes an untestable claim testable, and never lowers a testable count below the next floor" % f,
       not (g.status == "testable").reindex(ids[nT:]).any() and (j == 0 or d["testable"] <= X["floors"][j - 1]["testable"]))

# ---- item 3
F = X["fewer"]
fp = gzcsv("replication_exploratory_fewer_projects.csv.gz").set_index("claim_id")
ok("item 3: %d claims, all registered as not tested for fewer than 3 projects" % len(F["i"]),
   len(F["i"]) == len(fp) and all(res.reason[ids[i]] == "fewer_than_3_projects_for_condition" and D["claims"]["st"][i] == 5 for i in F["i"])
   and F["candidates"] == int((res.reason == "fewer_than_3_projects_for_condition").sum()) and F["no_cohort"] == F["candidates"] - len(fp))
ok("item 3: %d of those claims have a condition with three cohorts, one of them the claim's own" % F["three"],
   F["three"] == int(((res.reason == "fewer_than_3_projects_for_condition") & (res.n_units_all >= 3)).sum()))
ok("item 1: the claims below 0.05 without correction contain every registered one", u["contains_registered"] == bool((t.p_rep[t.outcome == "replicated"] < 0.05).all()))
eff = pd.read_parquet(R / "results" / "real_strict_primary_all" / "project_effects.parquet").set_index(["contrast_id", "taxon_key", "unit"])
links = pd.read_csv(R / "tables" / "discovery_links.csv")
links = links[links.in_analysis_population == True]  # noqa: E712
own = links.assign(unit=links.cohort_unit.fillna(links.study_accession)).groupby("exp_id").unit.apply(set).to_dict()
exp = pd.read_csv(R / "tables" / "testable_claims.csv", usecols=["claim_id", "exp_id"], low_memory=False).set_index("claim_id").exp_id
bad, n1 = [], 0
for j, i in enumerate(F["i"]):
    r, cid = fp.loc[ids[i]], ids[i]
    units = str(r.units_used).split(";")
    if len(units) != F["k"][j] or set(units) & own.get(exp[cid], set()):
        bad.append((cid, "units"))
        continue
    e = [eff.loc[(r.contrast_id, r.taxon_key, un)] for un in units]
    if any(x.n_nonzero < 10 for x in e) or F["nc"][j] != sum(int(x.n_case) for x in e) or F["nk"][j] != sum(int(x.n_control) for x in e):
        bad.append((cid, "persons"))
    if F["k"][j] == 1:           # the one-cohort test, written here: normal test of the registered per-project estimate
        n1 += 1
        x = e[0]
        z = r.sign * (x.auc - 0.5) / np.sqrt(x["var"])
        if not (same(F["p"][j], norm.sf(z)) and same(F["po"][j], norm.cdf(z)) and abs(F["auc"][j] / 1000 - x.auc) < 5.1e-4
                and abs(F["lo"][j] / 1000 - (x.auc - 1.959964 * np.sqrt(x["var"]))) < 6e-4 and abs(F["hi"][j] / 1000 - (x.auc + 1.959964 * np.sqrt(x["var"]))) < 6e-4):
            bad.append((cid, "one-cohort test"))
    elif not (same(F["p"][j], r.p_rep) and abs(F["auc"][j] / 1000 - r.auc_pooled) < 5.1e-4 and min(x.auc for x in e) - 1e-9 <= r.auc_pooled <= max(x.auc for x in e) + 1e-9):
        bad.append((cid, "two-cohort test"))
    if not same(F["q"][j], r.q_rep):
        bad.append((cid, "q"))
ok("item 3: every claim uses cohorts that are not its own and carry the taxon; %d one-cohort tests recomputed from the run's project estimates; two-cohort estimates lie between their two cohorts" % n1, not bad, str(bad[:5]))


def bh(p):
    p = np.asarray(p, float)
    o = np.argsort(p)
    r = p[o] * len(p) / (np.arange(len(p)) + 1)
    r = np.minimum.accumulate(r[::-1])[::-1]
    out = np.empty(len(p))
    out[o] = np.minimum(r, 1)
    return out


for g in F["groups"]:
    d = fp[fp.cohorts == g["cohorts"]]
    q = bh(d.p_rep.values)
    ok("item 3: %d cohort(s): %d claims, %d with p < 0.05, %d opposite, %d after correction within the group (recomputed here)" % (g["cohorts"], g["tested"], g["uncorrected"], g["opposite"], g["bh"]),
       g["tested"] == len(d) and g["uncorrected"] == int((d.p_rep < 0.05).sum()) and g["opposite"] == int((d.p_opp < 0.05).sum()) and g["bh"] == int((q < 0.05).sum())
       and np.allclose(q, d.q_rep.values, rtol=1e-4, atol=1e-9) and g["conditions"] == d.case_condition.nunique() and sum(r[1] for r in g["rows"]) == len(d)
       and sum(r[2] for r in g["rows"]) == g["uncorrected"])
    ok("item 3: %d cohort(s): the chance levels are shares between 0 and 0.15 and below the observed share" % g["cohorts"],
       0 < g["chance_mean"] < 0.15 and g["chance_mean"] < g["uncorrected"] / g["tested"] and 0 < g["chance_opp_mean"] < 0.15)
    units = d.units_used.str.split(";").explode().value_counts()
    ok("item 3: %d cohort(s): %d distinct cohorts, the largest carries %d claims" % (g["cohorts"], g["distinct_cohorts"], g["largest"]),
       g["distinct_cohorts"] == len(units) and g["largest"] == int(units.iloc[0]) and (g["cohorts"] == 1 or g["blocked"] == int((d.p_rep_blocked < 0.05).sum())))
ok("downloads listed in the file exist", all((P / f).exists() for f in X["meta"]["downloads"]))
ok("EXPLORATORY.md says what it is in its first lines", "EXPLORATORY" in (P / "EXPLORATORY.md").read_text()[:300])
print("\n%d pass, %d fail" % (n_pass, n_fail))
sys.exit(1 if n_fail else 0)
