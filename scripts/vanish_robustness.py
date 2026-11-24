#!/usr/bin/env python3
"""vanish_robustness.py - the numbers for the six plain-language sentences of the VANISH/BloSSUM page,
and three exploratory checks on a finished run of vanish_blossum.py.

It changes nothing: no class, threshold, permutation result or stored number of the analysis is touched.
It only reads a finished run's output directory and writes two files into ``<vanish-out>/page/`` plus one
CSV per check.

Stages (--stage, one at a time, independent of each other):

  sentences  every number the six sentences on the page need      -> <vanish-out>/page/robustness_sentences.json
  checks     the three quick checks of the critique assessment    -> <vanish-out>/page/robustness.json
             (summaries only, well under 1 MB) and one CSV per check:
             robustness_lifestyle.csv, robustness_cohort.csv, robustness_depth.csv
             (a CSV over 500 kB is gzipped and named .csv.gz)

What it reads
-------------
From the run's own output directory (--vanish-out), so that the checks use exactly the samples, groups,
cohorts and depths of that run and never a fresh selection:

  results.csv            per-taxon model-rule results and classes (the classed taxa of check 1)
  analyses.csv           per-analysis counts and the thresholds the run used (Q, S_min, D_min, P_loco,
                         stil_min_prev, stil_max_score)
  groups.csv             the group composition the run's own selection produced; the reconstruction below
                         is verified against it and the script stops if it disagrees
  cohorts.csv            per-cohort counts and depths (read only for the cohort totals of the sentences)
  simple_results.csv     per-taxon simple-rule results and classes (the classed taxa of checks 2 and 3)
  simple_analyses.csv    the simple rule's subject counts; the reconstruction is verified against them
  _work/samples_clean.parquet   the run's cleaned sample table (one row per QC-clean gut specimen with a
                         profile): sample_key, study_accession, cohort, age_category, lifestyle, country,
                         person, root_coverage_sum, order_key. The group labels, the one-sample-per-person
                         choice, the per-cohort cap and the minimum cell size are reproduced by calling
                         ``select()`` and ``group_label()`` of vanish_blossum.py itself (--vanish-script),
                         so there is one definition of a group, not two.
  _work/<level>_long.parquet    the profile rows of the samples the run selected (check 1 and the
                         detection floor of sentence 4)
  _work/taxa_genus_full.parquet the genus -> family map of the whole profile table (family level of the
                         simple rule); rebuilt from --sandpiper-dir if the run did not store it

From outside the run:

  --package        catalog_sample_metadata_wide.parquet, for the recorded health condition of the
                   reference samples (sentence 5) and the package version
  --sandpiper-dir  the per-sample profile tables, for the subject-level carriage of checks 2 and 3 and for
                   the taxonomy version and the number of species the profiles hold (sentence 6).
                   Never read whole: DuckDB streams them with a join on the subjects and a filter on the
                   classed taxa, under ``SET memory_limit``.
  --hdi            the country HDI table (the band of the reference group and of the simple rule's
                   subjects). Pass the same file the run used, or the subject counts will not match.
  --external-labels  the external lifestyle labels of Carter et al. 2023 Supplemental Table S4, as the run
                   was given them; defaults to external_labels_carter_s4.csv next to this script.

Usage
-----
  python vanish_robustness.py --stage sentences --vanish-out DIR --package DIR --sandpiper-dir DIR \\
      --hdi FILE [--external-labels FILE] [--vanish-script FILE] [--memory-limit 2GB]
  python vanish_robustness.py --stage checks    --vanish-out DIR --package DIR --sandpiper-dir DIR \\
      --hdi FILE [--external-labels FILE] [--vanish-script FILE] [--memory-limit 2GB] [--threads 10]

Deterministic (no sampling, no random tie-breaks: every ordering is by an explicit key). No network, no R.
Prints its runtime and peak RSS at the end.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import os
import resource
import shutil
import sys
import time

import numpy as np
import pandas as pd

CLASSES = ("VANISH", "BloSSUM", "StIL")
HEADLINE = dict(level="species", contrast="C1", stratum="adult")
NOT_RECORDED = "no value recorded (external label only)"
# how a catalog lifestyle value is written on the page; anything else falls back to the value with its
# underscores turned into spaces
LIFESTYLE_LABEL = {"hunter_gatherer": "hunter-gatherer",
                   "indigenous_community": "indigenous community",
                   "pastoralist": "pastoralist",
                   "traditional_agriculturalist": "traditional agriculturalist",
                   "rural_non_industrialized": "rural non-industrialized",
                   "urban_industrialized": "urban-industrialized"}
GZIP_OVER = 500_000          # a check CSV larger than this is gzipped
MAX_JSON = 1_000_000         # robustness.json must stay well under this


# ---------------------------------------------------------------- small helpers

def log(msg):
    sys.stderr.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    sys.stderr.flush()


def peak_rss_gb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024.0 ** 3) if sys.platform == "darwin" else r / (1024.0 ** 2)


def load_vb(hint=None):
    """Import vanish_blossum.py, so that the groups are defined once and in the analysis's own code."""
    cands = []
    if hint:
        cands.append(hint)
    here = os.path.dirname(os.path.abspath(__file__))
    cands += [os.path.join(here, "vanish_blossum.py"),
              os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "vanish_blossum.py"),
              os.path.join(os.getcwd(), "vanish_blossum.py")]
    for c in cands:
        if c and os.path.exists(c):
            spec = importlib.util.spec_from_file_location("vanish_blossum", c)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["vanish_blossum"] = mod
            spec.loader.exec_module(mod)
            log("using the group definitions of %s" % c)
            return mod
    raise SystemExit("vanish_blossum.py not found next to this script; pass --vanish-script FILE")


def con_of(work, memory_limit, threads):
    import duckdb
    con = duckdb.connect()
    con.execute("SET memory_limit=?", [memory_limit])
    con.execute("SET threads=?", [threads])
    con.execute("SET preserve_insertion_order=false")
    tmp = os.path.join(work, "tmp_robustness")
    os.makedirs(tmp, exist_ok=True)
    con.execute("SET temp_directory=?", [tmp])
    return con


def qlist(values):
    """SQL list literal of strings (the taxon names are GTDB names: letters, digits, _ . - and spaces)."""
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in sorted(set(values)))


def med(series):
    s = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    return float(s.median()) if len(s) else None


def jnum(x):
    """JSON-safe number."""
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    return x


def clean(o):
    """Recursively replace non-finite floats with null, so the file is valid JSON for the page."""
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return float(o) if np.isfinite(o) else None
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def write_json(path, obj):
    txt = json.dumps(clean(obj), indent=1, sort_keys=True, default=jnum, allow_nan=False)
    with open(path, "w") as fh:
        fh.write(txt)
    return len(txt.encode("utf-8"))


def write_csv(page_dir, name, df):
    """One CSV per check; gzipped and renamed .csv.gz when it is over GZIP_OVER bytes."""
    p = os.path.join(page_dir, name)
    df.to_csv(p, index=False)
    size = os.path.getsize(p)
    if size > GZIP_OVER:
        with open(p, "rb") as fi, gzip.open(p + ".gz", "wb", mtime=0) as fo:
            shutil.copyfileobj(fi, fo)
        os.remove(p)
        return name + ".gz", os.path.getsize(p + ".gz")
    return name, size


def lifestyle_label(row):
    v = row.get("lifestyle")
    if v is None or (isinstance(v, float) and np.isnan(v)) or str(v) in ("", "nan", "<NA>", "None"):
        return NOT_RECORDED
    return LIFESTYLE_LABEL.get(str(v), str(v).replace("_", " "))


# ---------------------------------------------------------------- the run's own samples and groups

def run_samples(VB, vanish_out, hdi_path, external):
    """The run's cleaned sample table, with the columns load_samples() adds after its DuckDB step."""
    sc = os.path.join(vanish_out, "_work", "samples_clean.parquet")
    if not os.path.exists(sc):
        raise SystemExit("missing %s: --vanish-out must be a finished run with its _work/ directory" % sc)
    d = pd.read_parquet(sc)
    d["stratum"] = np.where(d.age_category.isin(VB.ADULT), "adult",
                            np.where(d.age_category.isin(VB.INFANT), "infant", None))
    d["depth_log10"] = np.log10(d.root_coverage_sum.astype(float))
    d["ext_label"] = pd.NA
    d["ext_source"] = pd.NA
    if external and os.path.exists(external):
        ex = pd.read_csv(external).drop_duplicates("sample_key")
        d = d.drop(columns=["ext_label", "ext_source"]).merge(
            ex[["sample_key", "ext_label", "ext_source"]], on="sample_key", how="left")
    else:
        log("WARNING: no external label file; the NI group of contrasts C1, C2 and C4 will be short")
    hdi = pd.read_csv(hdi_path)
    high = sorted(set(hdi.loc[hdi.band == "high", "iso2"].astype(str)))
    d["hdi_high"] = d.country.isin(high)
    return d.reset_index(drop=True)


def selection(VB, d, contrast, stratum, cap, groups):
    """The samples of one contrast x age group, from the run's own select(); verified against groups.csv."""
    ni_def, reference = {c[0]: (c[1], c[2]) for c in VB.CONTRASTS}[contrast]
    sub, info = VB.select(d, ni_def, reference, stratum, cap)
    g = groups[(groups.contrast == contrast) & (groups.stratum == stratum)]
    if len(g) == 1:
        g = g.iloc[0]
        got = (int(info["counts"]["NI"]["n"]), int(info["counts"]["REF"]["n"]),
               int(info["counts"]["NI"]["cohorts"]), int(info["counts"]["REF"]["cohorts"]))
        want = (int(g.n_ni), int(g.n_ref), int(g.cohorts_ni), int(g.cohorts_ref))
        if got != want:
            raise SystemExit(
                "the reconstructed selection for %s/%s is %s but groups.csv says %s. The samples, the HDI "
                "file or the external labels are not the ones this run used: pass the run's own --hdi and "
                "--external-labels." % (contrast, stratum, got, want))
    return sub, info


# ---------------------------------------------------------------- the simple rule's subjects

def simple_subjects(con, VB, vanish_out, hdi_path, simple_analyses):
    """The subject table of METHOD v2 section B, with a cohort and a sequencing depth per subject.

    Band, subject, stratum and country are built exactly as simple_stage() builds them. Two columns are
    added, both from the run's own sample table: the subject's cohort (the cohort most of their samples
    come from; ties by cohort name) and the subject's sequencing depth (the sum of root_coverage_sum over
    their samples in that age group, because carriage counts a taxon detected in any one of them).
    """
    sc = os.path.join(vanish_out, "_work", "samples_clean.parquet")
    hdi = pd.read_csv(hdi_path)
    hdi = hdi[hdi.hdi_2023.notna()].copy()
    hdi["band3"] = np.where(hdi.hdi_2023 >= VB.HDI_IND, "ind",
                            np.where(hdi.hdi_2023 < VB.HDI_NONIND, "nonind", "trans"))
    con.register("hdi", hdi[["iso2", "band3"]])
    con.execute(f"""CREATE OR REPLACE TEMP TABLE subj AS
        WITH s AS (SELECT sample_key, person, country,
                          CASE WHEN age_category IN ('adult','elderly') THEN 'adult'
                               WHEN age_category IN ('infant','neonate') THEN 'infant' END AS stratum
                   FROM read_parquet('{sc}')),
             k AS (SELECT * FROM s WHERE stratum IS NOT NULL AND country IS NOT NULL),
             c AS (SELECT person, stratum, country, count(*) n FROM k GROUP BY 1,2,3),
             r AS (SELECT person, stratum, country,
                          row_number() OVER (PARTITION BY person, stratum ORDER BY n DESC, country ASC) rn
                   FROM c)
        SELECT r.person, r.stratum, r.country, h.band3 AS band
        FROM r JOIN hdi h ON h.iso2 = r.country WHERE r.rn = 1""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE smp AS
        SELECT s.sample_key, s.person, u.stratum, u.country, u.band, s.cohort, s.root_coverage_sum
        FROM read_parquet('{sc}') s JOIN subj u ON u.person = s.person
        WHERE (u.stratum = 'adult' AND s.age_category IN ('adult','elderly'))
           OR (u.stratum = 'infant' AND s.age_category IN ('infant','neonate'))""")
    con.execute("""CREATE OR REPLACE TEMP TABLE subj2 AS
        WITH c AS (SELECT person, stratum, cohort, count(*) n FROM smp GROUP BY 1,2,3),
             r AS (SELECT person, stratum, cohort,
                          row_number() OVER (PARTITION BY person, stratum ORDER BY n DESC, cohort ASC) rn
                   FROM c),
             dep AS (SELECT person, stratum, sum(root_coverage_sum) dep, count(*) n_samples
                     FROM smp GROUP BY 1,2)
        SELECT u.person, u.stratum, u.country, u.band, r.cohort, dep.dep, dep.n_samples
        FROM subj u
        JOIN r ON r.person = u.person AND r.stratum = u.stratum AND r.rn = 1
        JOIN dep ON dep.person = u.person AND dep.stratum = u.stratum""")
    got = con.execute("SELECT stratum, band, count(*) n FROM subj2 GROUP BY 1,2").df()
    counts = {}
    for st in ("adult", "infant"):
        counts[st] = {b: int(got[(got.stratum == st) & (got.band == b)].n.sum())
                      for b in ("ind", "nonind", "trans")}
    ok = True
    for _, r in simple_analyses.iterrows():
        if not bool(r.get("estimable", True)):
            continue
        c = counts.get(str(r.stratum), {})
        if (int(r.n_ind), int(r.n_nonind)) != (c.get("ind"), c.get("nonind")):
            ok = False
            log("WARNING: subjects for %s are %s ind / %s nonind but simple_analyses.csv says %s / %s"
                % (r.stratum, c.get("ind"), c.get("nonind"), int(r.n_ind), int(r.n_nonind)))
    if not ok:
        raise SystemExit(
            "the reconstructed subject counts do not match simple_analyses.csv: the --hdi file is not the "
            "one this run used (a country whose HDI value was filled in later moves subjects between bands). "
            "Pass the run's own HDI table.")
    return counts


def depth_quintiles(con, k=5):
    """k equal-sized depth strata per age group over the tested bands, by rank. Deterministic: the sort key
    is (depth, person), both unique enough to leave no tie, and the split is by position."""
    d = con.execute("""SELECT person, stratum, band, dep FROM subj2
                       WHERE band IN ('ind','nonind')""").df()
    out = []
    for st, g in d.groupby("stratum", sort=True):
        g = g.sort_values(["dep", "person"], kind="mergesort").reset_index(drop=True)
        g["qd"] = (np.arange(len(g)) * k // len(g)) + 1
        out.append(g)
    q = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["person", "stratum", "qd"])
    con.register("qdep", q[["person", "stratum", "qd"]])
    con.execute("""CREATE OR REPLACE TEMP TABLE subjq AS
        SELECT s.*, q.qd FROM subj2 s JOIN qdep q ON q.person = s.person AND q.stratum = s.stratum""")
    return q


# ---------------------------------------------------------------- carriage of the classed taxa

def carriage_table(con, sandpiper_dir, work, level, taxa):
    """DISTINCT (taxon, person) carriage of the named taxa only, with band, cohort and depth stratum.

    The per-sample profile tables are streamed with a join on the subjects' samples and a filter on the
    taxon names; nothing is loaded whole.
    """
    src, col = {"species": ("gut_sandpiper_sample_species.parquet", "species"),
                "genus": ("gut_sandpiper_sample_genus.parquet", "genus"),
                "family": ("gut_sandpiper_sample_genus.parquet", "genus")}[level]
    f = os.path.join(sandpiper_dir, src)
    if level == "family":
        tgf = os.path.join(work, "taxa_genus_full.parquet")
        if not os.path.exists(tgf):
            con.execute(f"""COPY (SELECT genus AS taxon, min(lineage) AS lineage
                                  FROM read_parquet('{f}') WHERE genus <> 'unassigned_at_genus'
                                  GROUP BY 1) TO '{tgf}' (FORMAT PARQUET)""")
        tg = pd.read_parquet(tgf)
        fam = []
        for l in tg.lineage:
            fam.append(next((p.strip() for p in str(l).split(";") if p.strip().startswith("f__")), ""))
        tg["family"] = fam
        tg = tg[tg.family.isin(set(taxa))]
        con.register("fam_map", tg[["taxon", "family"]].sort_values(["family", "taxon"]))
        sel = (f"SELECT m.family AS taxon, s.person, s.stratum, s.band, s.cohort, s.qd "
               f"FROM read_parquet('{f}') p JOIN subjsmp s ON s.sample_key = p.sample_key "
               f"JOIN fam_map m ON m.taxon = p.{col} WHERE p.relabund > 0")
    else:
        sel = (f"SELECT p.{col} AS taxon, s.person, s.stratum, s.band, s.cohort, s.qd "
               f"FROM read_parquet('{f}') p JOIN subjsmp s ON s.sample_key = p.sample_key "
               f"WHERE p.relabund > 0 AND p.{col} IN ({qlist(taxa)})")
    con.execute("CREATE OR REPLACE TEMP TABLE carr AS SELECT DISTINCT * FROM (" + sel + ")")
    return int(con.execute("SELECT count(*) FROM carr").fetchone()[0])


# ================================================================ stage: sentences

def stage_sentences(a, VB):
    t0 = time.time()
    out, work = a.vanish_out, os.path.join(a.vanish_out, "_work")
    page = os.path.join(out, "page")
    os.makedirs(page, exist_ok=True)
    res = pd.read_csv(os.path.join(out, "results.csv"))
    ana = pd.read_csv(os.path.join(out, "analyses.csv"))
    groups = pd.read_csv(os.path.join(out, "groups.csv"))
    sa = pd.read_csv(os.path.join(out, "simple_analyses.csv"))
    sr = pd.read_csv(os.path.join(out, "simple_results.csv"))
    lev, con_, st = HEADLINE["level"], HEADLINE["contrast"], HEADLINE["stratum"]
    H = res[(res.level == lev) & (res.contrast == con_) & (res.stratum == st)]
    A = ana[(ana.level == lev) & (ana.contrast == con_) & (ana.stratum == st)]
    if not len(A):
        raise SystemExit("no %s/%s/%s analysis in analyses.csv" % (lev, con_, st))
    A = A.iloc[0]
    V = H[H["class"] == "VANISH"]
    B = H[H["class"] == "BloSSUM"]
    S = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "vanish_out": os.path.basename(os.path.normpath(str(os.path.realpath(out)))),
         "scope": dict(HEADLINE), "stage": "sentences"}

    # ---- sentence 1: the names are names for a difference, not for a disappearance
    n_absent_v = int((V.n_pos_ref.fillna(0) <= 0).sum())
    n_absent_b = int((B.n_pos_ni.fillna(0) <= 0).sum())
    S["s1"] = dict(
        n_vanish=int(len(V)), n_vanish_absent_from_ref=n_absent_v,
        n_vanish_detected_in_ref=int(len(V)) - n_absent_v,
        med_samples_ref=med(V.n_pos_ref), min_samples_ref=int(V.n_pos_ref.min()) if len(V) else None,
        max_samples_ref=int(V.n_pos_ref.max()) if len(V) else None,
        n_ref=int(A.n_ref), cohorts_ref=int(A.cohorts_ref),
        med_cohorts_ref=med(V.cohorts_ref_detected),
        n_blossum=int(len(B)), n_blossum_absent_from_ni=n_absent_b,
        med_samples_ni=med(B.n_pos_ni), min_samples_ni=int(B.n_pos_ni.min()) if len(B) else None,
        n_ni=int(A.n_ni), cohorts_ni=int(A.cohorts_ni),
        variant=("none_absent" if (n_absent_v == 0 and n_absent_b == 0) else "some_absent"))

    # ---- sentence 2: what the simple rule does not adjust for (no number of its own)
    S["s2"] = dict(permutations=int(A.n_perm) if pd.notna(A.n_perm) else None,
                   cohorts_total=int(A.cohorts_distinct))

    # ---- sentence 3: the non-industrialized group pools several lifestyles
    d = run_samples(VB, out, a.hdi, a.external_labels)
    sub, info = selection(VB, d, con_, st, a.cap, groups)
    ni = sub[sub.group == "NI"].copy()
    ni["ls"] = [lifestyle_label(r) for _, r in ni.iterrows()]
    comp = (ni.groupby("ls").agg(n=("sample_key", "size"), cohorts=("cohort", "nunique"),
                                countries=("country", "nunique"))
            .reset_index().sort_values(["n", "ls"], ascending=[False, True]))
    g1 = groups[(groups.contrast == con_) & (groups.stratum == st)].iloc[0]
    g3 = groups[(groups.contrast == "C3") & (groups.stratum == st)]
    S["s3"] = dict(
        n_ni=int(len(ni)), cohorts_ni=int(ni.cohort.nunique()), countries_ni=int(ni.country.nunique()),
        n_ni_stated=int(g1.n_ni_stated), cohorts_ni_stated=int(g1.cohorts_ni_stated),
        n_ni_external_only=int(g1.n_ni_external), cohorts_ni_external=int(g1.cohorts_ni_external),
        lifestyles=[dict(label=r.ls, n=int(r.n), cohorts=int(r.cohorts)) for r in comp.itertuples()],
        n_lifestyle_values=int(len(comp)),
        c3_n_ni=int(g3.iloc[0].n_ni) if len(g3) else None,
        c3_cohorts_ni=int(g3.iloc[0].cohorts_ni) if len(g3) else None,
        c3_estimable=bool(g3.iloc[0].estimable) if len(g3) else None)

    con = con_of(work, a.memory_limit, a.threads)

    # ---- sentence 4: what "detected" means, and the smallest value the profiles record
    floors = []
    for lv in ("species", "genus", "family"):
        f = os.path.join(work, "%s_long.parquet" % lv)
        if os.path.exists(f):
            mn = con.execute(f"SELECT min(relabund) FROM read_parquet('{f}') WHERE relabund > 0").fetchone()[0]
            if mn is not None:
                floors.append(dict(level=lv, min_relabund=float(mn), min_relabund_pct=100.0 * float(mn)))
    S["s4"] = dict(rule="relabund > 0", floors=floors,
                   threshold_sweep_done=False)

    # ---- sentence 5: the reference group holds both healthy people and patients
    md = os.path.join(a.package, "catalog_sample_metadata_wide.parquet")
    ref = sub[sub.group == "REF"][["sample_key"]].reset_index(drop=True)
    s5 = dict(n_ref=int(A.n_ref), cohorts_ref=int(A.cohorts_ref), variant="unmeasured")
    if os.path.exists(md):
        cols = [c for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{md}')").df().column_name]
        if "health_condition" in cols:
            con.register("refk", ref)
            hc = con.execute(f"""
                SELECT coalesce(m.health_condition, '(no value)') AS hc, count(*) AS n
                FROM read_parquet('{md}') m JOIN refk r ON r.sample_key = m.sample_key
                WHERE m.release_retired IS NULL AND m.sample_type = 'specimen'
                GROUP BY 1 ORDER BY 2 DESC, 1""").df()
            tot = int(hc.n.sum())
            healthy = int(hc.loc[hc.hc == "healthy_control", "n"].sum())
            none_ = int(hc.loc[hc.hc == "(no value)", "n"].sum())
            cond = tot - healthy - none_
            named = hc[~hc.hc.isin(["healthy_control", "(no value)"])]
            s5 = dict(n_ref=int(A.n_ref), cohorts_ref=int(A.cohorts_ref), n_matched=tot,
                      n_condition=cond, n_healthy=healthy, n_no_value=none_,
                      pct_condition=round(100.0 * cond / tot, 1) if tot else None,
                      pct_healthy=round(100.0 * healthy / tot, 1) if tot else None,
                      n_condition_values=int(len(named)),
                      top_conditions=[dict(condition=r.hc.replace("_", " "), n=int(r.n))
                                      for r in named.head(5).itertuples()],
                      variant="measured")
    S["s5"] = s5

    # ---- sentence 6: the results are statements about one reference taxonomy
    spf = os.path.join(a.sandpiper_dir, "gut_sandpiper_sample_species.parquet")
    tax = dict(taxonomy_db=None, taxonomy_version=None, sandpiper_version=None)
    if os.path.exists(spf):
        cols = [c for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{spf}')").df().column_name]
        have = [c for c in ("taxonomy_db", "taxonomy_version", "sandpiper_version") if c in cols]
        if have:
            t = con.execute("SELECT DISTINCT %s FROM read_parquet('%s') ORDER BY 1"
                            % (", ".join(have), spf)).df()
            for c in have:
                vals = sorted(str(x) for x in t[c].dropna().unique())
                tax[c] = vals[0] if len(vals) == 1 else "; ".join(vals)
    n_sp_prof = None
    tsf = os.path.join(work, "taxa_species_full.parquet")
    if os.path.exists(tsf):
        n_sp_prof = int(con.execute(f"SELECT count(*) FROM read_parquet('{tsf}')").fetchone()[0])
    elif os.path.exists(spf):
        n_sp_prof = int(con.execute(f"""SELECT count(DISTINCT species) FROM read_parquet('{spf}')
                                        WHERE species <> 'unassigned_at_species'""").fetchone()[0])
    srh = sr[(sr.level == lev) & (sr.stratum == st)]
    S["s6"] = dict(n_species_profiled=n_sp_prof, n_species_tested_model=int(A.n_tested),
                   n_species_tested_simple=int(len(srh)) if len(srh) else None, **tax)

    # provenance: the package and release the run was built from, for the page's footer line
    vj = os.path.join(a.package, "VERSION.json")
    if os.path.exists(vj):
        v = json.loads(open(vj).read())
        S["package_version"] = v.get("package_version")
        S["release_id"] = v.get("release_id")
    con.close()
    n = write_json(os.path.join(page, "robustness_sentences.json"), S)
    log("robustness_sentences.json written (%d bytes, %.0f s)" % (n, time.time() - t0))
    return S


# ================================================================ check 1: prevalence by lifestyle

def check_lifestyle(a, VB, con, res, ana, groups, d):
    """Each classed taxon's prevalence in the non-industrialized group, split by the stated lifestyle.

    Samples: exactly the samples of the run's primary contrast (C1) in the chosen age group. Strata: the
    sample's lifestyle value in the catalog, or "not recorded (external label only)" for a sample that is
    in the group only because Carter et al. 2023 Table S4 labels it. A taxon's class is said to HOLD in a
    stratum when the prevalence in that stratum is still on the right side of the reference group's mean
    per-cohort prevalence by the run's own D_min margin (StIL: when both are at or above the run's own
    stil_min_prev). Strata with fewer than MIN_CELL samples are not judged.
    """
    con_, st = HEADLINE["contrast"], HEADLINE["stratum"]
    sub, info = selection(VB, d, con_, st, a.cap, groups)
    ni = sub[sub.group == "NI"].copy()
    ni["ls"] = [lifestyle_label(r) for _, r in ni.iterrows()]
    strata = (ni.groupby("ls").agg(n_samples=("sample_key", "size"), n_cohorts=("cohort", "nunique"),
                                  n_countries=("country", "nunique"))
              .reset_index().sort_values(["n_samples", "ls"], ascending=[False, True]))
    src = ni.groupby(["ls", "label_src"]).size().unstack(fill_value=0)
    strata["n_stated"] = [int(src.loc[l].get("stated", 0)) if l in src.index else 0 for l in strata.ls]
    strata["n_external"] = [int(src.loc[l].get("external", 0) + src.loc[l].get("both", 0))
                            if l in src.index else 0 for l in strata.ls]
    strata["eligible"] = strata.n_samples >= VB.MIN_CELL
    con.register("nis", ni[["sample_key", "ls"]].sort_values("sample_key").reset_index(drop=True))

    rows = []
    for lev in sorted(set(res.level)):
        H = res[(res.level == lev) & (res.contrast == con_) & (res.stratum == st)]
        cl = H[H.taxon_class.isin(CLASSES)]
        if not len(cl):
            continue
        A = ana[(ana.level == lev) & (ana.contrast == con_) & (ana.stratum == st)]
        A = A.iloc[0]
        dmin = float(A.D_min)
        smin = float(A.stil_min_prev) if "stil_min_prev" in A and pd.notna(A.stil_min_prev) else 0.20
        lf = os.path.join(a.vanish_out, "_work", "%s_long.parquet" % lev)
        if not os.path.exists(lf):
            log("WARNING: %s not found, skipping level %s of check 1" % (lf, lev))
            continue
        pos = con.execute(f"""
            SELECT p.taxon, n.ls, count(DISTINCT p.sample_key) AS n_pos
            FROM read_parquet('{lf}') p JOIN nis n ON n.sample_key = p.sample_key
            WHERE p.relabund > 0 AND p.taxon IN ({qlist(cl.taxon)})
            GROUP BY 1, 2""").df()
        pv = {(r.taxon, r.ls): int(r.n_pos) for r in pos.itertuples()}
        for t in cl.itertuples():
            pref = float(t.prev_ref_cohort_mean)
            for s in strata.itertuples():
                n_pos = pv.get((t.taxon, s.ls), 0)
                prev = n_pos / s.n_samples if s.n_samples else float("nan")
                cls = t.taxon_class
                if cls == "VANISH":
                    holds = bool(prev - pref >= dmin)
                    bar = "prevalence at least %.2f above the reference mean" % dmin
                elif cls == "BloSSUM":
                    holds = bool(pref - prev >= dmin)
                    bar = "prevalence at least %.2f below the reference mean" % dmin
                else:
                    holds = bool(prev >= smin and pref >= smin)
                    bar = "prevalence at least %.2f in both groups" % smin
                rows.append(dict(level=lev, taxon=t.taxon, taxon_class=cls, lifestyle=s.ls,
                                 n_samples=int(s.n_samples), n_cohorts=int(s.n_cohorts),
                                 n_detected=n_pos, prevalence=round(prev, 6),
                                 prev_ni_cohort_mean=round(float(t.prev_ni_cohort_mean), 6),
                                 prev_ref_cohort_mean=round(pref, 6),
                                 score_log2or=round(float(t.score_log2or), 4),
                                 hold_rule=bar, judged=bool(s.eligible), holds=holds if s.eligible else None))
    tab = pd.DataFrame(rows)
    if len(tab):
        tab = tab.sort_values(["level", "taxon_class", "taxon", "lifestyle"]).reset_index(drop=True)

    # ---- summaries for the page
    summary, by_stratum = [], []
    for lev in sorted(set(tab.level)) if len(tab) else []:
        for cls in CLASSES:
            x = tab[(tab.level == lev) & (tab.taxon_class == cls) & (tab.judged)]
            if not len(x):
                continue
            per = x.groupby("taxon").holds.agg(["sum", "size"])
            summary.append(dict(level=lev, taxon_class=cls, n_taxa=int(len(per)),
                                n_strata_judged=int(per["size"].max()),
                                n_hold_all=int((per["sum"] == per["size"]).sum()),
                                n_hold_none=int((per["sum"] == 0).sum()),
                                med_strata_hold=float(per["sum"].median())))
    for s in strata.itertuples():
        row = dict(lifestyle=s.ls, n_samples=int(s.n_samples), n_cohorts=int(s.n_cohorts),
                   n_countries=int(s.n_countries), n_stated=int(s.n_stated),
                   n_external=int(s.n_external), judged=bool(s.eligible), classes={})
        for lev in sorted(set(tab.level)) if len(tab) else []:
            for cls in CLASSES:
                x = tab[(tab.level == lev) & (tab.taxon_class == cls) & (tab.lifestyle == s.ls)]
                if len(x) and bool(s.eligible):
                    row["classes"]["%s/%s" % (lev, cls)] = dict(n_hold=int(x.holds.sum()), n_taxa=int(len(x)))
        by_stratum.append(row)
    return tab, dict(n_ni=int(len(ni)), n_cohorts=int(ni.cohort.nunique()),
                     n_countries=int(ni.country.nunique()), contrast=con_, stratum=st,
                     min_stratum_size=int(VB.MIN_CELL), strata=by_stratum, summary=summary)


# ================================================================ check 2: the simple rule by cohort

def _fisher(a_, b_, c_, d_):
    from scipy.stats import fisher_exact
    return float(fisher_exact([[int(a_), int(b_)], [int(c_), int(d_)]], alternative="two-sided")[1])


def _or(pos_n, n_n, pos_i, n_i):
    """Haldane-corrected odds ratio of carriage, non-industrialized against industrialized."""
    a_, b_, c_, d_ = pos_n + 0.5, n_n - pos_n + 0.5, pos_i + 0.5, n_i - pos_i + 0.5
    return float((a_ * d_) / (b_ * c_))


def _class_holds(cls, prev_n, prev_i, lfc, VB, stil_min_prev, stil_max_score):
    """The run's own prevalence thresholds (METHOD v2 section B), re-applied to a subset of subjects."""
    if cls == "VANISH":
        return bool(prev_n >= VB.SIMPLE_PREV_HI and prev_i <= VB.SIMPLE_PREV_LO)
    if cls == "BloSSUM":
        return bool(prev_i >= VB.SIMPLE_PREV_HI and prev_n <= VB.SIMPLE_PREV_LO)
    return bool(prev_n >= stil_min_prev and prev_i >= stil_min_prev and abs(lfc) < stil_max_score)


def check_cohort(a, VB, con, sr, sa, carr_levels):
    """The simple rule's 2x2 table stratified by cohort, combined by Cochran-Mantel-Haenszel.

    A stratum is a cohort (study group); it can contribute only if it holds subjects of both HDI bands.
    Reported beside it: what happens to the two band prevalences when the cohort that contributes the most
    carriers in a band is left out, which is the same question (is one study carrying the result) asked in
    a way that every taxon can answer.
    """
    from statsmodels.stats.contingency_tables import StratifiedTable
    tot = con.execute("""SELECT stratum, cohort, band, count(*) n FROM subjq GROUP BY 1,2,3""").df()
    T = {(r.stratum, r.cohort, r.band): int(r.n) for r in tot.itertuples()}
    coh_by_st = {}
    for st in sorted(set(tot.stratum)):
        x = tot[tot.stratum == st].pivot_table(index="cohort", columns="band", values="n",
                                               aggfunc="sum", fill_value=0)
        for b in ("ind", "nonind"):
            if b not in x.columns:
                x[b] = 0
        coh_by_st[st] = x.sort_index()
    rows, prov = [], []
    for lev, carr in carr_levels.items():
        pooled = carr["pooled"]          # taxon, stratum, band -> carriers
        bycoh = carr["cohort"]           # taxon, stratum, cohort, band -> carriers
        for st in sorted(set(sr.stratum)):
            cl = sr[(sr.level == lev) & (sr.stratum == st) & (sr.taxon_class.isin(CLASSES))]
            if not len(cl):
                continue
            arow = sa[(sa.level == lev) & (sa.stratum == st)]
            smin = float(arow.iloc[0].stil_min_prev) if len(arow) and "stil_min_prev" in arow else 0.20
            smax = float(arow.iloc[0].stil_max_score) if len(arow) and "stil_max_score" in arow else 1.0
            X = coh_by_st.get(st)
            for t in cl.itertuples():
                cls = t.taxon_class
                n_i, n_n = int(t.n_ind), int(t.n_nonind)
                p_i, p_n = int(t.pos_ind), int(t.pos_nonind)
                got_i = int(pooled.get((t.taxon, st, "ind"), 0))
                got_n = int(pooled.get((t.taxon, st, "nonind"), 0))
                prov.append(dict(level=lev, stratum=st, taxon=t.taxon,
                                 pooled_matches_run=bool(got_i == p_i and got_n == p_n),
                                 pos_ind_run=p_i, pos_ind_here=got_i,
                                 pos_nonind_run=p_n, pos_nonind_here=got_n))
                tabs, n_sub_inf, n_coh_inf, n_car_inf = [], 0, 0, 0
                for coh in X.index:
                    ni_c, nn_c = int(X.at[coh, "ind"]), int(X.at[coh, "nonind"])
                    if ni_c == 0 or nn_c == 0:
                        continue
                    ci = int(bycoh.get((t.taxon, st, coh, "ind"), 0))
                    cn = int(bycoh.get((t.taxon, st, coh, "nonind"), 0))
                    n_coh_inf += 1
                    n_sub_inf += ni_c + nn_c
                    n_car_inf += ci + cn
                    tabs.append(np.array([[cn, nn_c - cn], [ci, ni_c - ci]], dtype=float))
                or_pool = _or(p_n, n_n, p_i, n_i)
                mh_or, mh_p, same = None, None, None
                usable = [m for m in tabs if m.sum() > 0 and m[:, 0].sum() > 0 and m[:, 1].sum() > 0]
                if len(usable):
                    try:
                        sti = StratifiedTable([m for m in usable])
                        mh_or = float(sti.oddsratio_pooled)
                        mh_p = float(sti.test_null_odds(correction=True).pvalue)
                        same = bool(np.sign(np.log(mh_or)) == np.sign(np.log(or_pool))) \
                            if (mh_or > 0 and np.isfinite(mh_or)) else None
                    except Exception as e:                       # a degenerate stack of tables
                        log("CMH failed for %s/%s/%s: %s" % (lev, st, t.taxon, e))
                # the largest contributing cohort of each band, and the prevalences without it
                drop = {}
                for b, n_b, p_b in (("ind", n_i, p_i), ("nonind", n_n, p_n)):
                    cc = {coh: int(bycoh.get((t.taxon, st, coh, b), 0)) for coh in X.index}
                    top = max(cc, key=lambda k: (cc[k], k)) if cc else None
                    topn = cc.get(top, 0)
                    n_top = int(T.get((st, top, b), 0))
                    drop[b] = dict(cohort=top, carriers=topn,
                                   share=(topn / p_b) if p_b else None,
                                   n_left=n_b - n_top, pos_left=p_b - topn,
                                   prev_left=((p_b - topn) / (n_b - n_top)) if (n_b - n_top) > 0 else None)
                pn2, pi2 = drop["nonind"]["prev_left"], drop["ind"]["prev_left"]
                lfc2 = (float(np.log2((pn2 + 0.001) / (pi2 + 0.001)))
                        if (pn2 is not None and pi2 is not None) else None)
                holds_drop = (_class_holds(cls, pn2, pi2, lfc2, VB, smin, smax)
                              if lfc2 is not None else None)
                rows.append(dict(
                    level=lev, stratum=st, taxon=t.taxon, taxon_class=cls,
                    n_ind=n_i, n_nonind=n_n, pos_ind=p_i, pos_nonind=p_n,
                    prev_ind=round(float(t.prev_ind), 6), prev_nonind=round(float(t.prev_nonind), 6),
                    or_pooled=round(or_pool, 4), q_run=float(t.q),
                    n_cohorts_both_bands=n_coh_inf, n_subjects_both_bands=n_sub_inf,
                    n_carriers_both_bands=n_car_inf,
                    mh_odds_ratio=None if mh_or is None else round(mh_or, 4),
                    cmh_p=None if mh_p is None else float(mh_p),
                    mh_same_direction=same,
                    cmh_computable=bool(len(usable) > 0),
                    top_cohort_ind=drop["ind"]["cohort"],
                    share_ind_carriers_top_cohort=None if drop["ind"]["share"] is None
                    else round(drop["ind"]["share"], 4),
                    top_cohort_nonind=drop["nonind"]["cohort"],
                    share_nonind_carriers_top_cohort=None if drop["nonind"]["share"] is None
                    else round(drop["nonind"]["share"], 4),
                    prev_ind_without_top=None if pi2 is None else round(pi2, 6),
                    prev_nonind_without_top=None if pn2 is None else round(pn2, 6),
                    class_holds_without_top_cohort=holds_drop))
    tab = pd.DataFrame(rows)
    if len(tab):
        tab = tab.sort_values(["level", "stratum", "taxon_class", "taxon"]).reset_index(drop=True)
    pv = pd.DataFrame(prov)
    # ---- summaries
    cohort_counts = []
    for st in sorted(coh_by_st):
        X = coh_by_st[st]
        both = X[(X["ind"] > 0) & (X["nonind"] > 0)]
        cohort_counts.append(dict(stratum=st, n_cohorts=int(len(X)),
                                  n_cohorts_both_bands=int(len(both)),
                                  n_subjects=int(X[["ind", "nonind"]].to_numpy().sum()),
                                  n_subjects_both_bands=int(both[["ind", "nonind"]].to_numpy().sum())))
    summary = []
    if len(tab):
        for (lev, st, cls), x in tab.groupby(["level", "stratum", "taxon_class"], sort=True):
            c = x[x.cmh_computable]
            summary.append(dict(
                level=lev, stratum=st, taxon_class=cls, n_taxa=int(len(x)),
                n_cmh_computable=int(len(c)),
                n_same_direction=int((c.mh_same_direction == True).sum()),  # noqa: E712
                n_opposite_direction=int((c.mh_same_direction == False).sum()),  # noqa: E712
                n_cmh_p_lt_05=int((c.cmh_p < 0.05).sum()) if len(c) else 0,
                med_share_ind_carriers_top_cohort=med(x.share_ind_carriers_top_cohort),
                med_share_nonind_carriers_top_cohort=med(x.share_nonind_carriers_top_cohort),
                n_holds_without_top_cohort=int((x.class_holds_without_top_cohort == True).sum()),  # noqa: E712
                n_fails_without_top_cohort=int((x.class_holds_without_top_cohort == False).sum())))  # noqa: E712
    return tab, dict(cohorts=cohort_counts, summary=summary,
                     pooled_mismatches=int((~pv.pooled_matches_run).sum()) if len(pv) else 0,
                     n_taxa=int(len(tab)))


# ================================================================ check 3: the simple rule by depth

def check_depth(a, VB, con, sr, sa, carr_levels, qd, k=5):
    """The simple rule inside equal-sized strata of sequencing depth.

    Depth strata are quintiles of the subject's total root coverage within the age group, over the subjects
    of the two tested bands. Inside each quintile the 2x2 table is rebuilt and Fisher's exact test is run
    again; BH is applied inside the quintile over the classed taxa only, so the q here is not the run's q
    (which was over every tested taxon). A class HOLDS in a quintile when the run's own prevalence
    thresholds are met there (and, for VANISH and BloSSUM, q < the run's SIMPLE_Q).
    """
    tot = con.execute("SELECT stratum, qd, band, count(*) n FROM subjq GROUP BY 1,2,3").df()
    dep = con.execute("""SELECT stratum, qd, band, min(dep) lo, max(dep) hi, median(dep) md,
                                count(*) n FROM subjq GROUP BY 1,2,3""").df()
    N = {(r.stratum, int(r.qd), r.band): int(r.n) for r in tot.itertuples()}
    rows = []
    for lev, carr in carr_levels.items():
        byq = carr["qd"]
        for st in sorted(set(sr.stratum)):
            cl = sr[(sr.level == lev) & (sr.stratum == st) & (sr.taxon_class.isin(CLASSES))]
            if not len(cl):
                continue
            arow = sa[(sa.level == lev) & (sa.stratum == st)]
            smin = float(arow.iloc[0].stil_min_prev) if len(arow) and "stil_min_prev" in arow else 0.20
            smax = float(arow.iloc[0].stil_max_score) if len(arow) and "stil_max_score" in arow else 1.0
            for q_ in range(1, k + 1):
                ni_q, nn_q = N.get((st, q_, "ind"), 0), N.get((st, q_, "nonind"), 0)
                if ni_q == 0 or nn_q == 0:
                    continue
                rec = []
                for t in cl.itertuples():
                    cls = t.taxon_class
                    ci = int(byq.get((t.taxon, st, q_, "ind"), 0))
                    cn = int(byq.get((t.taxon, st, q_, "nonind"), 0))
                    pi_, pn_ = ci / ni_q, cn / nn_q
                    p = _fisher(cn, nn_q - cn, ci, ni_q - ci)
                    rec.append((t.taxon, cls, ci, cn, pi_, pn_, p))
                qs = VB.bh(np.array([r[6] for r in rec]))
                for (tax, cls, ci, cn, pi_, pn_, p), qq in zip(rec, qs):
                    lfc = float(np.log2((pn_ + 0.001) / (pi_ + 0.001)))
                    thr = _class_holds(cls, pn_, pi_, lfc, VB, smin, smax)
                    holds = bool(thr and (qq < VB.SIMPLE_Q)) if cls in ("VANISH", "BloSSUM") else bool(thr)
                    rows.append(dict(
                        level=lev, stratum=st, taxon=tax, taxon_class=cls, depth_quintile=q_,
                        n_ind=ni_q, n_nonind=nn_q, pos_ind=ci, pos_nonind=cn,
                        prev_ind=round(pi_, 6), prev_nonind=round(pn_, 6), lfc=round(lfc, 4),
                        p=p, q_within_quintile=float(qq),
                        thresholds_met=bool(thr), holds=holds))
    tab = pd.DataFrame(rows)
    if len(tab):
        tab = tab.sort_values(["level", "stratum", "taxon_class", "taxon", "depth_quintile"]).reset_index(drop=True)
    # ---- summaries
    quint = []
    for r in dep.sort_values(["stratum", "qd", "band"]).itertuples():
        quint.append(dict(stratum=r.stratum, depth_quintile=int(r.qd), band=r.band, n=int(r.n),
                          median_root_coverage=round(float(r.md), 1),
                          min_root_coverage=round(float(r.lo), 1), max_root_coverage=round(float(r.hi), 1)))
    band_depth = con.execute("""SELECT stratum, band, median(dep) md, count(*) n FROM subjq
                                GROUP BY 1,2 ORDER BY 1,2""").df()
    summary = []
    if len(tab):
        for (lev, st, cls), x in tab.groupby(["level", "stratum", "taxon_class"], sort=True):
            per = x.groupby("taxon").holds.agg(["sum", "size"])
            thr = x.groupby("taxon").thresholds_met.agg(["sum", "size"])
            summary.append(dict(level=lev, stratum=st, taxon_class=cls, n_taxa=int(len(per)),
                                n_quintiles=int(per["size"].max()),
                                n_hold_all=int((per["sum"] == per["size"]).sum()),
                                n_hold_none=int((per["sum"] == 0).sum()),
                                med_quintiles_hold=float(per["sum"].median()),
                                n_thresholds_all=int((thr["sum"] == thr["size"]).sum()),
                                med_quintiles_thresholds=float(thr["sum"].median())))
        per_q = []
        for (lev, st, cls, q_), x in tab.groupby(["level", "stratum", "taxon_class", "depth_quintile"],
                                                 sort=True):
            # which of the rule's two bounds fails: the floor in the band where the taxon should be
            # common, or the ceiling in the other one (not defined for a StIL candidate)
            if cls == "VANISH":
                floor_, ceil_ = int((x.prev_nonind < VB.SIMPLE_PREV_HI).sum()), \
                    int((x.prev_ind > VB.SIMPLE_PREV_LO).sum())
            elif cls == "BloSSUM":
                floor_, ceil_ = int((x.prev_ind < VB.SIMPLE_PREV_HI).sum()), \
                    int((x.prev_nonind > VB.SIMPLE_PREV_LO).sum())
            else:
                floor_, ceil_ = None, None
            per_q.append(dict(level=lev, stratum=st, taxon_class=cls, depth_quintile=int(q_),
                              n_hold=int(x.holds.sum()), n_taxa=int(len(x)),
                              n_thresholds_met=int(x.thresholds_met.sum()),
                              n_below_floor=floor_, n_above_ceiling=ceil_,
                              mean_prev_nonind=round(float(x.prev_nonind.mean()), 5),
                              mean_prev_ind=round(float(x.prev_ind.mean()), 5)))
    else:
        per_q = []
    return tab, dict(k=k, quintiles=quint, by_quintile=per_q, summary=summary,
                     thresholds=dict(prev_hi=float(VB.SIMPLE_PREV_HI), prev_lo=float(VB.SIMPLE_PREV_LO),
                                     q=float(VB.SIMPLE_Q)),
                     band_depth=[dict(stratum=r.stratum, band=r.band, n=int(r.n),
                                      median_root_coverage=round(float(r.md), 1))
                                 for r in band_depth.itertuples()])


# ================================================================ stage: checks

def stage_checks(a, VB):
    t0 = time.time()
    out, work = a.vanish_out, os.path.join(a.vanish_out, "_work")
    page = os.path.join(out, "page")
    os.makedirs(page, exist_ok=True)
    res = pd.read_csv(os.path.join(out, "results.csv")).rename(columns={"class": "taxon_class"})
    ana = pd.read_csv(os.path.join(out, "analyses.csv"))
    groups = pd.read_csv(os.path.join(out, "groups.csv"))
    sr = pd.read_csv(os.path.join(out, "simple_results.csv")).rename(columns={"class": "taxon_class"})
    sa = pd.read_csv(os.path.join(out, "simple_analyses.csv"))
    con = con_of(work, a.memory_limit, a.threads)
    R = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": "checks",
         "vanish_out": os.path.basename(os.path.normpath(str(os.path.realpath(out)))), "exploratory": True,
         "scope": dict(HEADLINE), "csv_files": []}
    vj = os.path.join(a.package, "VERSION.json")
    if os.path.exists(vj):
        v = json.loads(open(vj).read())
        R["package_version"] = v.get("package_version")
        R["release_id"] = v.get("release_id")

    # ---- check 1 (model rule, the run's own samples)
    d = run_samples(VB, out, a.hdi, a.external_labels)
    t_ls, j_ls = check_lifestyle(a, VB, con, res, ana, groups, d)
    log("check 1 (lifestyle) done: %d rows, %.0f s, peak RSS %.2f GB"
        % (len(t_ls), time.time() - t0, peak_rss_gb()))

    # ---- the simple rule's subjects, once, for checks 2 and 3
    simple_counts = simple_subjects(con, VB, out, a.hdi, sa)
    qd = depth_quintiles(con, k=a.depth_strata)
    con.execute("""CREATE OR REPLACE TEMP TABLE subjsmp AS
        SELECT m.sample_key, m.person, m.stratum, s.band, s.cohort, s.qd
        FROM smp m JOIN subjq s ON s.person = m.person AND s.stratum = m.stratum""")
    carr_levels = {}
    for lev in sorted(set(sr.level)):
        taxa = sorted(set(sr[(sr.level == lev) & (sr.taxon_class.isin(CLASSES))].taxon))
        if not taxa:
            continue
        n = carriage_table(con, a.sandpiper_dir, work, lev, taxa)
        pooled = con.execute("SELECT taxon, stratum, band, count(*) n FROM carr GROUP BY 1,2,3").df()
        bycoh = con.execute("SELECT taxon, stratum, cohort, band, count(*) n FROM carr GROUP BY 1,2,3,4").df()
        byq = con.execute("SELECT taxon, stratum, qd, band, count(*) n FROM carr GROUP BY 1,2,3,4").df()
        carr_levels[lev] = dict(
            pooled={(r.taxon, r.stratum, r.band): int(r.n) for r in pooled.itertuples()},
            cohort={(r.taxon, r.stratum, r.cohort, r.band): int(r.n) for r in bycoh.itertuples()},
            qd={(r.taxon, r.stratum, int(r.qd), r.band): int(r.n) for r in byq.itertuples()})
        log("carriage of %d classed %s taxa: %d subject rows, %.0f s, peak RSS %.2f GB"
            % (len(taxa), lev, n, time.time() - t0, peak_rss_gb()))

    t_co, j_co = check_cohort(a, VB, con, sr, sa, carr_levels)
    log("check 2 (cohort) done: %d rows, %.0f s" % (len(t_co), time.time() - t0))
    t_de, j_de = check_depth(a, VB, con, sr, sa, carr_levels, qd, k=a.depth_strata)
    log("check 3 (depth) done: %d rows, %.0f s" % (len(t_de), time.time() - t0))
    con.close()

    if j_co["pooled_mismatches"]:
        log("WARNING: %d classed taxa whose pooled carriage here differs from simple_results.csv; the "
            "profile tables given are not the ones the run used" % j_co["pooled_mismatches"])
    j_co["subject_counts"] = simple_counts

    for name, tab, key in (("robustness_lifestyle.csv", t_ls, "lifestyle"),
                           ("robustness_cohort.csv", t_co, "cohort"),
                           ("robustness_depth.csv", t_de, "depth")):
        fn, size = write_csv(page, name, tab)
        R["csv_files"].append(fn)
        dict(lifestyle=j_ls, cohort=j_co, depth=j_de)[key]["csv"] = fn
        dict(lifestyle=j_ls, cohort=j_co, depth=j_de)[key]["csv_bytes"] = size
        dict(lifestyle=j_ls, cohort=j_co, depth=j_de)[key]["n_rows"] = int(len(tab))
    R["lifestyle"], R["cohort"], R["depth"] = j_ls, j_co, j_de
    n = write_json(os.path.join(page, "robustness.json"), R)
    if n > MAX_JSON:
        raise SystemExit("robustness.json is %d bytes, over the %d-byte limit" % (n, MAX_JSON))
    log("robustness.json written (%d bytes), CSVs %s" % (n, ", ".join(R["csv_files"])))
    return R


# ================================================================ main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", required=True, choices=["sentences", "checks"])
    ap.add_argument("--vanish-out", required=True, help="the output directory of a finished run")
    ap.add_argument("--package", required=True)
    ap.add_argument("--sandpiper-dir", required=True)
    ap.add_argument("--hdi", required=True, help="the HDI table the run used")
    ap.add_argument("--external-labels", default=None,
                    help="external lifestyle labels as the run was given them "
                         "(default: external_labels_carter_s4.csv next to this script)")
    ap.add_argument("--vanish-script", default=None, help="path to vanish_blossum.py")
    ap.add_argument("--cap", type=int, default=100, help="the run's --cap (per cohort and group)")
    ap.add_argument("--memory-limit", default="2GB", help="DuckDB memory limit")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--depth-strata", type=int, default=5, help="number of equal-sized depth strata")
    a = ap.parse_args()
    if a.external_labels is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "external_labels_carter_s4.csv")
        a.external_labels = p if os.path.exists(p) else None
    t0 = time.time()
    VB = load_vb(a.vanish_script)
    if a.stage == "sentences":
        stage_sentences(a, VB)
    else:
        stage_checks(a, VB)
    log("stage %s finished in %.0f s, peak RSS %.2f GB" % (a.stage, time.time() - t0, peak_rss_gb()))
    print(json.dumps(dict(stage=a.stage, seconds=round(time.time() - t0, 1),
                          peak_rss_gb=round(peak_rss_gb(), 2))))


if __name__ == "__main__":
    main()
