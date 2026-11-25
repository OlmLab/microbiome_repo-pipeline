#!/usr/bin/env python3
"""Second, optional data file of the replication page: three EXPLORATORY checks added after the registered test.

    python replication_exploratory.py --payload <payload dir with replication.json> [--tables <replication>/exploratory/out]

Writes into the payload directory
    replication_exploratory.json                     what the page's collapsed "Exploratory checks" section and the
                                                     exploratory line of a claim panel are drawn from
    replication_exploratory_uncorrected.csv.gz       item 1, one row per testable claim
    replication_exploratory_floors.csv.gz            item 2, one row per claim and floor
    replication_exploratory_fewer_projects.csv.gz    item 3, one row per claim tested in one or two cohorts
    replication_exploratory_by_condition.csv         items 1 and 3 by condition
    EXPLORATORY.md                                   what was done and what it found

replication.json is NOT touched: it is read only for the order of the claims, and its sha256 is recorded so the
page can refuse a mismatched pair. Every number comes from the CSV tables written by the exploratory scripts
(x02_uncorrected.py, x03_floor.py, x04_fewer_projects.py, x08_tables.py); none is typed here. Nothing here is part of
the registered analysis and nothing changes a label of the registered run."""
import argparse
import gzip
import hashlib
import io
import json
import math
import shutil
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
LABEL = "Exploratory: added after the registered test, not part of the plan"


def sig2(x, cut=0.05):
    """Two significant digits, never rounded across the 0.05 line."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    x = float(x)
    if x == 0:
        return 0.0
    r = float("%.2g" % x)
    if (r < cut) != (x < cut):
        r = float("%.3g" % x)
        if (r < cut) != (x < cut):
            r = float("%.6g" % x)
    return r


def gz(df, path):
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as f:
        f.write(df.to_csv(index=False, float_format="%.6g", lineterminator="\n").encode("utf-8"))
    Path(path).write_bytes(buf.getvalue())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default=str(HERE / "payload"))
    ap.add_argument("--tables", default=str(HERE.parent.parent / "exploratory" / "out"))
    a = ap.parse_args()
    out, T = Path(a.payload), Path(a.tables)
    main_bytes = (out / "replication.json").read_bytes()
    D = json.loads(main_bytes)
    ids, nT = D["claims"]["id"], D["claims"]["n_testable"]
    pos = {c: i for i, c in enumerate(ids)}
    S = pd.read_csv(T / "exploratory_summary.csv", dtype=str, keep_default_na=False)
    S = {(r.analysis, r.key): r.value for r in S.itertuples()}

    def num(analysis, key, kind=float):
        return kind(float(S[(analysis, key)]))

    assert S[("validation", "reproduces_registered_result")] == "True", "the exploratory runner must reproduce the registered result"
    assert num("uncorrected", "testable", int) == nT == D["meta"]["testable"]
    assert num("uncorrected", "registered_replicated", int) == D["meta"]["replicated"]

    # ---- item 1: without the correction for multiple tests
    u = pd.read_csv(T / "uncorrected_claims.csv").set_index("claim_id").reindex(ids[:nT])
    assert u.p_rep.notna().all(), "every testable claim of the page has an uncorrected p-value"
    assert int(u.uncorrected_p_below_0_05.sum()) == num("uncorrected", "uncorrected", int)
    assert int(u.registered_replicated.sum()) == D["meta"]["replicated"]
    assert (u.k.astype(int).values == pd.Series(D["claims"]["k"]).values).all()
    ub = pd.read_csv(T / "uncorrected_by_condition.csv")
    unc = dict(
        testable=nT, registered=D["meta"]["replicated"], n=num("uncorrected", "uncorrected", int), opposite=num("uncorrected", "uncorrected_opposite", int),
        permutations=num("uncorrected", "permutations", int), contains_registered=num("uncorrected", "registered_not_uncorrected", int) == 0,
        opposite_chance_mean=num("uncorrected", "uncorrected_opposite_chance.chance_mean"),
        chance=dict(mean=num("uncorrected", "uncorrected_chance.chance_mean"), lo=num("uncorrected", "uncorrected_chance.chance_q025"),
                    hi=num("uncorrected", "uncorrected_chance.chance_q975"), max=num("uncorrected", "uncorrected_chance.chance_max"),
                    p=num("uncorrected", "uncorrected_chance.permutation_p")),
        majority=dict(n=num("uncorrected", "share_projects_claimed_direction.claims_with_majority", int),
                      all=num("uncorrected", "share_projects_claimed_direction.claims_with_all_projects", int),
                      chance_mean=num("uncorrected", "share_projects_claimed_direction.chance_share_of_claims_with_majority.chance_mean"),
                      all_chance_mean=num("uncorrected", "share_projects_claimed_direction.chance_share_of_claims_with_all_projects.chance_mean")),
        across_conditions=dict(n=num("uncorrected", "conditions_in_unweighted_mean", int), uncorrected=num("uncorrected", "unweighted_mean_of_condition_rates_uncorrected"),
                               registered=num("uncorrected", "unweighted_mean_of_condition_rates_registered")),
        rows=[[r.condition, int(r.testable), int(r.registered_replicated), int(r.uncorrected), int(r.uncorrected_opposite),
               round(float(r.chance_mean), 6), round(float(r.chance_q975), 6), sig2(r.permutation_p)] for r in ub.itertuples()])
    assert sum(r[1] for r in unc["rows"]) == nT and sum(r[3] for r in unc["rows"]) == unc["n"]

    # ---- item 2: abundance floors
    fc = pd.read_csv(T / "floor_claims.csv")
    floors, code = [], pd.Series(0, index=ids[:nT])
    fvals = sorted(fc.floor.unique())
    for j, f in enumerate(fvals):
        g = fc[fc.floor == f].set_index("claim_id").reindex(ids[:nT])
        st = g.status.eq("testable").values
        rep = g.outcome.eq("replicated").values
        code = code * 3 + pd.Series([0 if r else (1 if t else 2) for r, t in zip(rep, st)], index=ids[:nT])
        k = "floors.%d." % j
        assert abs(num("floor", k + "floor") - f) < 1e-12
        d = dict(floor=f, removed=round(num("floor", k + "share_of_nonzero_values_removed"), 6), testable=num("floor", k + "testable", int),
                 replicated=num("floor", k + "replicated", int), no_longer_testable=num("floor", k + "no_longer_testable", int),
                 lost_replicated=num("floor", k + "of_which_registered_replicated", int), rep_to_not=num("floor", k + "replicated_to_not", int),
                 not_to_rep=num("floor", k + "not_to_replicated", int), same=num("floor", k + "same_outcome", int),
                 registered_same_claims=num("floor", k + "registered_replicated_on_same_claims", int))
        assert int(st.sum()) == d["testable"] and int(rep.sum()) == d["replicated"], f
        assert d["same"] + d["rep_to_not"] + d["not_to_rep"] == d["testable"]
        assert d["registered_same_claims"] == d["replicated"] + d["rep_to_not"] - d["not_to_rep"] == int((u.registered_replicated.values & st).sum())
        floors.append(d)

    # ---- item 3: one or two cohorts
    fp = pd.read_csv(T / "fewer_projects_claims.csv")
    fp["i"] = fp.claim_id.map(pos)
    assert fp.i.notna().all() and (fp.i >= nT).all() and all(D["claims"]["st"][int(i)] == 5 for i in fp.i)
    fp = fp.sort_values("i")
    fb = pd.read_csv(T / "fewer_projects_by_condition.csv")
    groups = []
    for kk, name in ((1, "tested_in_one_cohort"), (2, "tested_in_two_cohorts")):
        k = "groups.%s." % name
        g = fp[fp.cohorts == kk]
        d = dict(cohorts=kk, tested=num("fewer_projects", k + "tested", int), conditions=num("fewer_projects", k + "conditions", int),
                 papers=num("fewer_projects", k + "papers", int), uncorrected=num("fewer_projects", k + "uncorrected", int),
                 opposite=num("fewer_projects", k + "uncorrected_opposite", int), bh=num("fewer_projects", k + "bh", int),
                 bh_opposite=num("fewer_projects", k + "bh_opposite", int), chance_mean=num("fewer_projects", k + "chance_uncorrected_rate_mean"),
                 chance_hi=num("fewer_projects", k + "chance_uncorrected_rate_q975"), chance_p=num("fewer_projects", k + "permutation_p_uncorrected"),
                 chance_bh_mean=num("fewer_projects", k + "chance_bh_mean"), chance_bh_max=num("fewer_projects", k + "chance_bh_max", int),
                 chance_opp_mean=num("fewer_projects", k + "chance_uncorrected_opposite_rate_mean"), chance_opp_hi=num("fewer_projects", k + "chance_uncorrected_opposite_rate_q975"),
                 distinct_cohorts=num("fewer_projects", k + "distinct_cohorts", int), largest=num("fewer_projects", k + "tests_from_largest_cohort", int),
                 three=num("fewer_projects", k + "condition_has_three_or_more_cohorts", int),
                 blocked=num("fewer_projects", k + "two_cohorts_blocked_p_below_0_05", int) if kk == 2 else None,
                 tau2_zero=num("fewer_projects", k + "two_cohorts_tau2_zero", int) if kk == 2 else None,
                 median_cases=num("fewer_projects", k + "median_case_persons"), median_controls=num("fewer_projects", k + "median_control_persons"),
                 rows=[[r.case_condition, int(r.tested), int(r.uncorrected), int(r.uncorrected_opposite), int(r.bh)] for r in fb[fb.cohorts == kk].sort_values(["tested", "case_condition"], ascending=[False, True]).itertuples()])
        assert len(g) == d["tested"] and int((g.p_rep < 0.05).sum()) == d["uncorrected"] and int((g.q_rep < 0.05).sum()) == d["bh"]
        per_unit = g.units_used.str.split(";").explode().value_counts()
        assert d["distinct_cohorts"] == len(per_unit) and d["largest"] == int(per_unit.iloc[0]) and d["three"] == int((g.n_units_all >= 3).sum())
        groups.append(d)
    few = dict(permutations=num("fewer_projects", "permutations", int), candidates=num("fewer_projects", "registered_untestable_for_fewer_than_3_projects", int),
               no_cohort=num("fewer_projects", "of_which_with_no_usable_cohort", int),
               three=num("fewer_projects", "of_which_condition_has_three_or_more_cohorts", int), groups=groups,
               i=[int(v) for v in fp.i], k=fp.cohorts.astype(int).tolist(), auc=[int(round(1000 * v)) for v in fp.auc_pooled],
               lo=[int(round(1000 * v)) for v in fp.ci95_low], hi=[int(round(1000 * v)) for v in fp.ci95_high],
               p=[sig2(v) for v in fp.p_rep], q=[sig2(v) for v in fp.q_rep], po=[sig2(v) for v in fp.p_opp],
               nc=fp.n_case.astype(int).tolist(), nk=fp.n_control.astype(int).tolist())
    assert few["candidates"] == D["meta"]["untestable"]["fewer_than_3_projects_for_condition"]

    downloads = ["replication_exploratory_uncorrected.csv.gz", "replication_exploratory_floors.csv.gz", "replication_exploratory_fewer_projects.csv.gz",
                 "replication_exploratory_by_condition.csv", "EXPLORATORY.md"]
    X = dict(format=1, label=LABEL,
             pair=dict(main_sha256=hashlib.sha256(main_bytes).hexdigest(), claims=len(ids), testable=nT, first=ids[0], last_testable=ids[nT - 1]),
             meta=dict(package=D["meta"]["package"], made=S.get(("meta", "made"), ""), downloads=downloads),
             uncorrected=unc, floors=floors, fewer=few,
             claims=dict(p=[sig2(v) for v in u.p_rep], po=[sig2(v) for v in u.p_opp], d=u.n_units_claimed_direction.astype(int).tolist(), f=[int(v) for v in code]))
    (out / "replication_exploratory.json").write_text(json.dumps(X, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    gz(pd.read_csv(T / "uncorrected_claims.csv"), out / downloads[0])
    gz(fc, out / downloads[1])
    gz(pd.read_csv(T / "fewer_projects_claims.csv"), out / downloads[2])
    bc = pd.concat([ub.assign(analysis="uncorrected (testable claims)"), fb.rename(columns=dict(case_condition="condition")).assign(
        analysis=fb.cohorts.map({1: "tested in one cohort", 2: "tested in two cohorts"}))], ignore_index=True)
    bc[["analysis", "condition"] + [c for c in bc.columns if c not in ("analysis", "condition", "cohorts")]].to_csv(out / downloads[3], index=False, float_format="%.6g", lineterminator="\n")
    shutil.copyfile(T.parent / "EXPLORATORY.md", out / "EXPLORATORY.md")
    sizes = {f: (out / f).stat().st_size for f in ["replication_exploratory.json"] + downloads}
    print(json.dumps(dict(files=sizes, bytes_total=sum(sizes.values()), claims_with_uncorrected=nT, claims_one_or_two_cohorts=len(fp),
                          sha256_exploratory_json=hashlib.sha256((out / "replication_exploratory.json").read_bytes()).hexdigest(),
                          sha256_main_json=X["pair"]["main_sha256"])))


if __name__ == "__main__":
    main()
