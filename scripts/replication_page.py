#!/usr/bin/env python
"""replication_page.py — payload of Atlas > Replication of published findings.

Reads the results of the pre-registered replication run (projects/replication, package 1.34.0) and writes

    <out>/replication.json          the ONE data file the page reads in the browser
    <out>/replication_claims.csv.gz             downloads: every registered claim with its result
    <out>/replication_project_effects.csv.gz    every per-project estimate behind the pooled results
                                    (these two are gzip-compressed: the site is close to its size limit)
    <out>/replication_conditions.csv, replication_sensitivity.csv
    <out>/SAP.md, PREREGISTRATION.md, RUN_REPORT.md   the frozen plan, the registration, the run report
    <out>/build_manifest.json       sizes and counts (not shipped)

It computes NO new result: every number is copied from results/, tables/ and the frozen folder, and the
script fails if what it writes does not add up to the run's own headline. It writes only into <out>; nothing
under FROZEN_* or results/ is opened for writing. The results belong to package 1.34.0 — do NOT point this
script at a later package to "refresh" the page: a later package was not the release under test.

    python replication_page.py --replication ~/catalog/projects/replication --out <dir>

About 3 s, under 400 MB.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
import shutil
from pathlib import Path

import duckdb
import pandas as pd
from scipy.stats import norm

HERE = Path(__file__).resolve().parent
PRIMARY = "real_strict_primary_all"
# status codes used by the page (order = drawing order of the stacked bars)
STATUS = ["replicated", "replicated_smaller_than_sesoi", "inconclusive", "smaller_than_sesoi",
          "opposite_direction", "untestable_few_projects", "untestable_taxon_rare"]
REASON = {"fewer_than_3_projects_for_condition": 5, "taxon_too_rare_in_projects": 6}
SEQ = ["16S", "WMS", "PCR", "not recorded"]
SENSITIVITY = [
    ("primary", "Primary analysis", PRIMARY),
    ("s1_16s", "Only claims from 16S studies", "real_strict_primary_16S"),
    ("s1_wms", "Only claims from shotgun studies", "real_strict_primary_WMS"),
    ("s2", "Condition labels: either of the two readings is enough", "real_lenient_primary_all"),
    ("s3_strict", "Taxon rule: the exact GTDB taxon only", "real_strict_strict_all"),
    ("s3_broad", "Taxon rule: broad", "real_strict_broad_all"),
    ("s5", "Adjusted for age, sex and BMI", "real_strict_primary_all_adjusted"),
    ("s6", "Only projects with person identifiers for at least 90 % of persons", "real_strict_primary_all_subjid0.9"),
    ("s7", "Without removing the claim's own cohort", "real_strict_primary_all_nodiscoveryexclusion"),
    ("s8", "Colorectal adenoma left out", "real_strict_primary_all_dropEFO0005406"),
    ("s9", "Colorectal cancer (set aside before the run; not part of the headline)", "real_strict_primary_all_crconly"),
]
MIN_DETECTED = 10          # SAP: a project counts for a taxon when the taxon is detected in at least 10 persons
WORDS = {0: "replicated", 1: "replicated, effect smaller than the smallest effect of interest",
         2: "not replicated: inconclusive", 3: "not replicated: effect of meaningful size excluded",
         4: "not replicated: opposite direction", 5: "not tested: fewer than three projects for the condition",
         6: "not tested: taxon too rare in the projects"}


def J(p):
    return json.loads(Path(p).read_text())


def i6(x):
    """AUC-scale number as an integer in millionths. The page shows three decimals; where rounding to six
    decimals would change the third one, the stored value is moved by one millionth so that the page shows
    exactly what the result file rounds to."""
    x = float(x)
    v = int(round(x * 1e6))
    want = "%.3f" % x
    for w in (v, v - 1, v + 1, v - 2, v + 2):
        if "%.3f" % (w / 1e6) == want:
            return w
    return v


def write_csv_gz(df, path, **kw):
    """A gzip-compressed CSV that is byte-identical on every run (no time stamp, no file name in the header)."""
    raw = df.to_csv(index=False, **kw).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=buf, mode="wb", compresslevel=9, mtime=0) as g:
        g.write(raw)
    Path(path).write_bytes(buf.getvalue())


def short(title, n=140):
    """A project title cut on a word boundary, with an ellipsis when cut."""
    t = " ".join(str(title or "").split())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


def sig3(x, cut=0.05):
    """q-value at three significant digits, never moved across the 5 % line by the rounding."""
    x = float(x)
    y = float("%.3g" % x)
    if (y < cut) != (x < cut):
        y = 0.0499 if x < cut else 0.0501
    return y


def first_author(authors):
    if not isinstance(authors, str) or not authors.strip() or authors.strip() == "NA":
        return ""
    names = [a.strip() for a in authors.split(",") if a.strip()]
    tok = names[0].split()
    last = " ".join(tok[:-1]) if len(tok) > 1 and re.fullmatch(r"[A-Z\-]{1,4}", tok[-1]) else names[0]
    return last + (" et al." if len(names) > 1 else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replication", default=str(HERE.parent.parent), help="the projects/replication folder")
    ap.add_argument("--out", default="payload")
    ap.add_argument("--memory-limit", default="2GB")
    a = ap.parse_args()
    R, out = Path(a.replication).expanduser(), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = R / "results" / PRIMARY
    con = duckdb.connect()
    con.execute("SET memory_limit='%s'" % a.memory_limit)
    con.execute("SET threads=2")

    release = J(R / "RELEASE.json")
    m = re.search(r"package ([\d.]+) \(release (R[\d.]+), DOI ([^)]+)\)", release["label"])
    package, release_id, doi = m.group(1), m.group(2), m.group(3)
    pkg = Path(release["package_dir"])
    frozen = R / (R / "FROZEN_CURRENT.txt").read_text().strip()
    man = J(frozen / "MANIFEST.json")
    assert hashlib.sha256((frozen / "SHA256SUMS.txt").read_bytes()).hexdigest() == man["freeze_hash"], "freeze hash"

    H, S = J(res / "headline.json"), J(res / "summary.json")
    PERM = J(R / "results" / "permutation_reference.json")
    N = J(R / "tables" / "run_numbers.json")
    assert S["labels"] == "real" and PERM["observed_replicated"] == H["overall"]["replicated"]

    cr = pd.read_csv(res / "claim_results.csv", low_memory=False)
    rc = pd.read_csv(R / "tables" / "registered_claims.csv", low_memory=False)
    tm = pd.read_csv(R / "tables" / "taxon_map.csv", low_memory=False)
    assert cr.claim_id.is_unique and len(cr) == len(rc) == S["claims"]
    c = cr.merge(rc[["claim_id", "bsdb_id", "direction", "ncbi_name_used", "level", "year"]], on="claim_id",
                 validate="one_to_one")
    c["pmid"] = c.pmid.astype(float).astype(int).astype(str)
    c["st"] = [STATUS.index(s) if isinstance(s, str) else REASON[r] for s, r in zip(c.sublabel, c.reason)]
    # sign = +1 when the claim says "higher in cases"; BugSigDB's direction is group 1 against group 0, and the cases
    # are group 0 in some experiments, so the registered sign (not the direction column) is what the page shows
    assert set(c.sign.unique()) == {1, -1}
    c["seq"] = [SEQ.index(s) if s in SEQ else 3 for s in c.sequencing_type]
    # testable first (the testable-only columns are then short arrays), then by condition, taxon, paper
    c["untestable"] = c.st >= 5
    c["name_l"] = c.ncbi_name_used.str.lower()
    c["pmid_i"] = c.pmid.astype(int)
    c = c.sort_values(["untestable", "case_condition", "name_l", "pmid_i", "claim_id"], kind="mergesort").reset_index(drop=True)
    n_test = int((~c.untestable).sum())
    assert n_test == H["overall"]["testable"] and (c.st[:n_test] < 5).all()

    # ---- conditions
    cond_names = sorted(c.case_condition.unique(), key=str.lower)
    cond_ix = {n: i for i, n in enumerate(cond_names)}
    efo = c.groupby("case_condition").contrast_id.agg(lambda s: s.unique().tolist())
    assert all(len(v) == 1 for v in efo), "one contrast per condition"
    cond_of_efo = {v[0]: k for k, v in efo.items()}
    strict = pd.read_csv(R / "tables" / "contrasts.csv")
    strict = strict[strict.mapping == "strict"]

    # ---- per-project effects (only the groups behind a testable claim go into the page file)
    eff = con.execute("SELECT * FROM read_parquet(?)", [str(res / "project_effects.parquet")]).df()
    cpk = eff.groupby(["contrast_id", "unit"]).agg(study=("study_accession", "first"), nc=("n_case", "first"),
                                                 nk=("n_control", "first"), a=("n_case", "nunique"),
                                                 b=("n_control", "nunique"), s=("study_accession", "nunique"))
    assert (cpk[["a", "b", "s"]] == 1).all().all(), "persons per contrast and unit are constant across taxa"
    cpk = cpk.reset_index()
    cpk["cond"] = cpk.contrast_id.map(cond_of_efo)
    cpk = cpk.dropna(subset=["cond"]).sort_values(["cond", "study"]).reset_index(drop=True)
    cp_ix = {(r.contrast_id, r.unit): i for i, r in enumerate(cpk.itertuples())}
    projects = sorted(cpk.study.unique())
    proj_ix = {p: i for i, p in enumerate(projects)}
    titles = dict(con.execute("SELECT study_accession, study_title FROM read_parquet(?) WHERE study_accession IN "
                              "(SELECT unnest(?))", [str(pkg / "catalog_studies.parquet"), projects]).fetchall())

    tkeys = c[["taxon_key", "ncbi_name_used", "level"]].drop_duplicates()
    assert tkeys.taxon_key.is_unique
    tkeys = tkeys.sort_values("ncbi_name_used", key=lambda s: s.str.lower()).reset_index(drop=True)
    tax_ix = {k: i for i, k in enumerate(tkeys.taxon_key)}
    tm["taxon_key"] = tm.level + ":" + tm.ncbi_taxid_used.astype("Int64").astype(str)
    tmu = tm.dropna(subset=["gtdb_anchor"]).drop_duplicates("taxon_key").set_index("taxon_key")
    # which GTDB names the profiles of the release carry (the taxon explorer has an entry for exactly those)
    have = set()
    for lvl in ("genus", "species"):
        f = Path(release["profiles_dir"]) / ("gut_sandpiper_sample_%s.parquet" % lvl)
        have |= {r[0] for r in con.execute("SELECT DISTINCT %s FROM read_parquet(?)" % lvl, [str(f)]).fetchall()}
    t_gtdb, t_open, t_targets = [], [], {}
    for i, k in enumerate(tkeys.taxon_key):
        anchor = tmu.gtdb_anchor.get(k)
        assert isinstance(anchor, str), ("every registered claim has a GTDB anchor", k)
        targets = str(tmu.gtdb_targets_primary.get(k) or "").split(";")
        t_gtdb.append(anchor)
        opened = anchor if anchor in have else next((t for t in targets if t in have), None)
        # open: "" = the anchor itself has an explorer entry; a name = that taxon has; "-" = none carried
        t_open.append("" if opened == anchor else (opened or "-"))
        if len(targets) > 1:
            # the other GTDB groups counted for this taxon; "!" marks one that no profile of the release carries
            t_targets[str(i)] = [t if t in have else "!" + t for t in targets if t != anchor]

    # ---- effect groups
    gkeys = c[:n_test][["contrast_id", "taxon_key"]].drop_duplicates().sort_values(["contrast_id", "taxon_key"])
    g_ix, g_start, e_cp, e_auc, e_se, e_nz = {}, [0], [], [], [], []
    eg = {k: v for k, v in eff.groupby(["contrast_id", "taxon_key"])}
    rows_of = {}
    for gi, (cid, tk) in enumerate(gkeys.itertuples(index=False)):
        g_ix[(cid, tk)] = gi
        d = eg[(cid, tk)].sort_values("study_accession")
        rows_of[gi] = {}
        for r in d.itertuples():
            rows_of[gi][r.unit] = len(e_cp)
            e_cp.append(cp_ix[(cid, r.unit)])
            e_auc.append(i6(r.auc))
            e_se.append(i6(math.sqrt(r.var)))
            e_nz.append(int(r.n_nonzero))
        g_start.append(len(e_cp))

    # ---- claims: which projects of the group a claim did NOT use, and why
    links = pd.read_csv(R / "tables" / "discovery_links.csv")
    own = links.dropna(subset=["cohort_unit"]).groupby("exp_id").cohort_unit.agg(set).to_dict()
    excl = {}
    for ci, r in enumerate(c[:n_test].itertuples()):
        gi = g_ix[(r.contrast_id, r.taxon_key)]
        used = set(r.units_used.split(";"))
        rows = rows_of[gi]
        assert used <= set(rows), r.claim_id
        # the run removes the claim's own cohort first and then the projects in which the taxon is too rare
        disc = set(rows) & own.get(r.exp_id, set())
        rare = {u for u, j in rows.items() if e_nz[j] < MIN_DETECTED and u not in disc}
        assert used == set(rows) - disc - rare, ("projects used", r.claim_id)
        assert len(disc) == int(r.n_units_discovery_excluded) and len(rare) == int(r.n_units_taxon_too_rare), r.claim_id
        assert len(used) == int(r.k)
        if disc:
            excl[str(ci)] = sorted(rows[u] - g_start[gi] for u in disc)   # positions inside the group

    papers = sorted(c.pmid.unique(), key=int)
    pap_ix = {p: i for i, p in enumerate(papers)}
    dump = pd.read_csv(R / "inputs" / "bugsigdb_v1.3.1" / "full_dump.csv", skiprows=1, dtype=str,
                       usecols=["PMID", "Authors list", "Title", "Journal", "Year"]).dropna(subset=["PMID"])
    dump = dump[dump.PMID.isin(papers)].drop_duplicates("PMID").set_index("PMID")
    assert set(papers) <= set(dump.index), "every claim paper is in the BugSigDB export"
    txt = lambda v: "" if (not isinstance(v, str) or v == "NA") else v.strip()

    T = c[:n_test]
    data = dict(
        format=1,
        status=STATUS, seq=SEQ,
        conditions=dict(name=cond_names, efo=[efo[n][0] for n in cond_names], projects=[], cases=[], controls=[]),
        taxa=dict(key=tkeys.taxon_key.tolist(), name=tkeys.ncbi_name_used.tolist(),
                  rank=[0 if l == "genus" else 1 for l in tkeys.level], gtdb=t_gtdb, open=t_open, targets=t_targets),
        papers=dict(pmid=[int(p) for p in papers], year=[txt(dump.Year[p]) for p in papers],
                    author=[first_author(dump["Authors list"][p]) for p in papers],
                    journal=[txt(dump.Journal[p]) for p in papers], title=[txt(dump.Title[p]) for p in papers]),
        projects=dict(acc=projects, title=[short(titles.get(p)) for p in projects]),
        cp=dict(cond=[cond_ix[x] for x in cpk.cond], proj=[proj_ix[x] for x in cpk.study],
                nc=cpk.nc.astype(int).tolist(), nk=cpk.nk.astype(int).tolist()),
        groups=dict(start=g_start),
        effects=dict(cp=e_cp, auc=e_auc, se=e_se, nz=e_nz),
        claims=dict(
            n_testable=n_test,
            id=c.claim_id.tolist(), c=[cond_ix[x] for x in c.case_condition], t=[tax_ix[x] for x in c.taxon_key],
            p=[pap_ix[x] for x in c.pmid], s=[1 if x > 0 else 0 for x in c.sign], q=c.seq.tolist(), st=c.st.tolist(),
            g=[g_ix[(x, y)] for x, y in zip(T.contrast_id, T.taxon_key)],
            auc=[i6(x) for x in T.auc_pooled], lo=[i6(x) for x in T.ci95_low], hi=[i6(x) for x in T.ci95_high],
            tu=[i6(x) for x in T.theta_upper95], k=T.k.astype(int).tolist(), qr=[sig3(x) for x in T.q_rep], qo=[sig3(x) for x in T.q_opp],
            hk=[1 if x < 0.05 else 0 for x in T.q_rep_hksj], i2=[int(round(100 * x)) for x in T.I2], x=excl),
    )
    for n in cond_names:
        d = cpk[cpk.cond == n]
        data["conditions"]["projects"].append(int(len(d)) if len(d) else int((strict.efo_id == efo[n][0]).sum()))
        data["conditions"]["cases"].append(int(d.nc.sum()))
        data["conditions"]["controls"].append(int(d.nk.sum()))

    # ---- numbers for the headline, the "how firm" box and the collapsed sections (all read, none typed)
    o = H["overall"]
    by = H["by_condition"]
    counts = c.groupby(["case_condition", "st"]).size().unstack(fill_value=0).reindex(columns=range(7), fill_value=0)
    for n, b in by.items():        # the page's own counts must equal the run's summary
        assert int(counts.loc[n, [0, 1]].sum()) == b["replicated"] and int(counts.loc[n, range(5)].sum()) == b["testable"], n
    assert int(counts[[0, 1]].sum().sum()) == o["replicated"]
    sub = H["sublabels"]
    assert [int(counts[i].sum()) for i in range(5)] == [sub[s] for s in STATUS[:5]]
    assert sum(data["claims"]["hk"]) == H["side_tests"]["replicated_hartung_knapp"]
    big = max(by, key=lambda n: by[n]["testable"])
    s6 = J(R / "results" / "real_strict_primary_all_subjid0.9" / "headline.json")["overall"]
    s7 = J(R / "results" / "real_strict_primary_all_nodiscoveryexclusion" / "headline.json")["overall"]
    sens = []
    for key, label, d in SENSITIVITY:
        h, s = J(R / "results" / d / "headline.json"), J(R / "results" / d / "summary.json")
        sens.append(dict(key=key, label=label, claims=s["claims"], testable=h["overall"]["testable"],
                         replicated=h["overall"]["replicated"], rate=h["overall"]["rate"],
                         lo=h["overall"]["ci95_low"], hi=h["overall"]["ci95_high"],
                         opposite=h["sublabels"].get("opposite_direction", 0)))
    run, av = N["pilot_vs_run"]["run"], N["sensitivity"]["real_strict_primary_all_adjusted"]["extra"]
    sesoi = float(norm.cdf(0.2 / math.sqrt(2)) - 0.5)
    run_date = re.search(r"(20\d\d-\d\d-\d\d)", (R / "RUN_ORDER.md").read_text()).group(1)
    # where the freeze hash was made public before the run, and what the audit of the new readings disagreed on
    printed = sorted(f.stem.replace("RELEASE_NOTES_", "") for f in pkg.glob("RELEASE_NOTES_*.md")
                     if man["freeze_hash"] in f.read_text(errors="ignore"))
    assert printed, "the freeze hash is printed in a release note of the package"
    audit = [json.loads(l) for l in (R / "work" / "llm" / "cond_A_run" / "audit.jsonl").read_text().splitlines() if l.strip()]
    assert len(audit) == N["reader_audit"]["n"]
    audit_term = sum(1 for x in audit if x["verdict"] != "agree" and "efo" in x.get("changes", {}))
    audit_role = sum(1 for x in audit if x["verdict"] != "agree" and ({"role", "state"} & set(x.get("changes", {}))))
    downloads = ["replication_claims.csv.gz", "replication_project_effects.csv.gz", "replication_conditions.csv",
                 "replication_sensitivity.csv", "SAP.md", "PREREGISTRATION.md", "RUN_REPORT.md"]
    meta = dict(
        package=package, release_id=release_id, doi=doi, run_date=run_date,
        frozen_at=man["frozen_at_utc"], freeze_hash=man["freeze_hash"], freeze_verified=bool(N["freeze_verified"]),
        source="BugSigDB v1.3.1", source_doi="10.5281/zenodo.19740674", gtdb="R232",
        sesoi_auc=round(sesoi, 4), sesoi_d=0.2, min_projects=3, min_persons=15, min_detected=MIN_DETECTED,
        registered=S["claims"], testable=o["testable"], replicated=o["replicated"], rate=o["rate"],
        ci_low=o["ci95_low"], ci_high=o["ci95_high"], sublabels=sub, untestable=H["untestable_reasons"],
        units=H["test_units"], median_projects=H["median_projects_per_claim"],
        n_conditions=len(cond_names), n_conditions_tested=len(by), n_papers=len(papers),
        n_papers_tested=int(T.pmid.nunique()), n_projects=len(projects), n_taxa=len(tkeys),
        checks=dict(
            stricter=dict(replicated=H["side_tests"]["replicated_hartung_knapp"], testable=o["testable"]),
            looser=dict(replicated=H["side_tests"]["replicated_stratified_wilcoxon"], testable=o["testable"]),
            person_id=dict(replicated=s6["replicated"], testable=s6["testable"],
                           same_claims_primary=N["s6_same_claims"]["replicated_primary"]),
            largest=dict(name=big, testable=by[big]["testable"], replicated=by[big]["replicated"]),
            without_largest=dict(testable=o["testable"] - by[big]["testable"],
                                 replicated=o["replicated"] - by[big]["replicated"]),
            across_conditions=dict(rate=H["unweighted_mean_of_condition_rates"],
                                   n=len(H["conditions_in_unweighted_mean"]), min_claims=H["min_claims_for_mean"]),
            shuffled=dict(n=PERM["n_permutations"], maximum=PERM["maximum"], mean=PERM["mean"],
                          with_any=PERM["runs_with_any"], p=PERM["permutation_p_one_sided"]),
            own_cohort_kept=dict(replicated=s7["replicated"], testable=s7["testable"]),
            unadjusted=dict(claimed=H["claimed_direction_unadjusted_p_below_0_05"],
                            opposite=H["opposite_direction_unadjusted_p_below_0_05"]),
            covariates=dict(testable=av["claims_testable_in_both"], adjusted=av["replicated_adjusted"],
                            unadjusted=av["replicated_unadjusted"], changed=av["claims_whose_label_changes"]),
        ),
        by_rank=H["by_rank"], by_sequencing=H["by_sequencing_type_of_original"], sensitivity=sens,
        population=dict(samples=run["pop"]["samples"], projects=run["pop"]["projects"],
                        with_condition=run["pop"]["with_condition"], healthy=run["pop"]["healthy_control"],
                        healthy_strict=run["cond"]["healthy_control_strict_samples"],
                        condition_pairs=run["cond"]["strings"], contrasts=run["con"]["strict"]["contrasts"],
                        contrast_projects=run["con"]["strict"]["projects"], contrasts_used=S["contrasts"]),
        funnel=dict((k, run["fun"][k]) for k in ("tier1_claims", "genus_or_species", "taxon_mapped",
                                                  "taxon_mapped_and_case_vs_healthy_control", "confirmatory_candidates")),
        links=dict(papers=run["links"]["tier1_claim_papers"], linked=run["links"]["linked_to_any_project"],
                   wms_papers=run["links"]["wms_papers"], wms_linked=run["links"]["wms_papers_linked"]),
        reader_audit=dict(N["reader_audit"], on_case_or_control=audit_role, other_term=audit_term), wms_split=N["exploratory_wms_linked_split"],
        registered_16s=int((c.seq == 0).sum()), hash_printed_in=printed,
        downloads=downloads,
    )
    data["meta"] = meta
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    (out / "replication.json").write_text(blob, encoding="utf-8")

    # ---- downloads
    dl = c.assign(result=c.st.map(WORDS), claimed=c.sign.map(lambda s: "higher in cases" if s > 0 else "lower in cases"),
                  gtdb_anchor=c.taxon_key.map(lambda k: tmu.gtdb_anchor.get(k)),
                  gtdb_names_counted=c.taxon_key.map(lambda k: tmu.gtdb_targets_primary.get(k)))
    dl = dl[["claim_id", "bsdb_id", "pmid", "year", "sequencing_type", "case_condition", "contrast_id", "ncbi_name_used",
             "level", "gtdb_anchor", "gtdb_names_counted", "claimed", "result", "k", "auc_pooled", "ci95_low",
             "ci95_high", "theta", "se", "tau2", "I2", "p_rep", "q_rep", "p_opp", "q_opp", "p_rep_hksj", "q_rep_hksj",
             "n_units_all", "n_units_discovery_excluded", "n_units_taxon_too_rare", "units_used"]]
    dl = dl.rename(columns=dict(case_condition="condition", contrast_id="efo_id", ncbi_name_used="taxon",
                                level="rank", k="projects_used", units_used="cohort_units_used"))
    write_csv_gz(dl, out / "replication_claims.csv.gz", quoting=csv.QUOTE_MINIMAL, float_format="%.6g")
    pe = eff.assign(condition=eff.contrast_id.map(cond_of_efo), se=eff["var"] ** 0.5,
                    taxon=eff.taxon_key.map(dict(zip(tkeys.taxon_key, tkeys.ncbi_name_used))))
    pe = pe.dropna(subset=["condition"]).rename(columns=dict(contrast_id="efo_id", unit="cohort_unit",
                                                              n_nonzero="n_persons_taxon_detected"))
    pe = pe[["condition", "efo_id", "taxon", "taxon_key", "study_accession", "cohort_unit", "auc", "se", "n_case",
             "n_control", "n_persons_taxon_detected"]].sort_values(["condition", "taxon", "study_accession"])
    write_csv_gz(pe, out / "replication_project_effects.csv.gz", float_format="%.6g")
    cd = pd.DataFrame(dict(condition=cond_names, efo_id=data["conditions"]["efo"],
                           registered_claims=[int(counts.loc[n].sum()) for n in cond_names],
                           testable=[int(counts.loc[n, range(5)].sum()) for n in cond_names],
                           replicated=[int(counts.loc[n, [0, 1]].sum()) for n in cond_names],
                           replicated_small_effect=[int(counts.loc[n, 1]) for n in cond_names],
                           inconclusive=[int(counts.loc[n, 2]) for n in cond_names],
                           meaningful_effect_excluded=[int(counts.loc[n, 3]) for n in cond_names],
                           opposite_direction=[int(counts.loc[n, 4]) for n in cond_names],
                           not_tested_few_projects=[int(counts.loc[n, 5]) for n in cond_names],
                           not_tested_taxon_rare=[int(counts.loc[n, 6]) for n in cond_names],
                           projects=data["conditions"]["projects"], case_persons=data["conditions"]["cases"],
                           control_persons=data["conditions"]["controls"]))
    cd["rate"] = [by[n]["rate"] if n in by else None for n in cond_names]
    cd["ci95_low"] = [by[n]["ci95_low"] if n in by else None for n in cond_names]
    cd["ci95_high"] = [by[n]["ci95_high"] if n in by else None for n in cond_names]
    cd.to_csv(out / "replication_conditions.csv", index=False, float_format="%.4f")
    pd.DataFrame(sens).drop(columns="key").to_csv(out / "replication_sensitivity.csv", index=False, float_format="%.4f")
    for name, src in (("SAP.md", frozen / "SAP.md"), ("PREREGISTRATION.md", frozen / "PREREGISTRATION.md"),
                      ("RUN_REPORT.md", R / "RUN_REPORT.md")):
        shutil.copyfile(src, out / name)

    sizes = {f: (out / f).stat().st_size for f in ["replication.json"] + downloads}
    manifest = dict(package=package, release_id=release_id, freeze_hash=man["freeze_hash"], files=sizes,
                    bytes_page_data=sizes["replication.json"], bytes_downloads=sum(sizes.values()) - sizes["replication.json"],
                    bytes_total=sum(sizes.values()), claims=len(c), testable=n_test, effect_groups=len(g_ix),
                    effect_rows=len(e_cp), conditions=len(cond_names), taxa=len(tkeys), papers=len(papers),
                    projects=len(projects), claims_with_own_cohort_removed=len(excl),
                    taxa_without_explorer_entry=sum(1 for x in t_open if x == "-"),
                    sha256={f: hashlib.sha256((out / f).read_bytes()).hexdigest() for f in sizes})
    (out / "build_manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({k: v for k, v in manifest.items() if k != "sha256"}))


if __name__ == "__main__":
    main()
