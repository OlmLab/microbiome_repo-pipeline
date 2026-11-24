#!/usr/bin/env python3
"""verify_robustness.py - independent check of the numbers vanish_robustness.py wrote.

Nothing here imports vanish_robustness.py or vanish_blossum.py. Every quantity is recomputed from the
run's own prep output and the profile tables with SQL written for this file alone, and then compared with
``page/robustness.json`` and the three CSVs. Twenty classed taxa are drawn with a fixed seed (--seed,
default 20261123) and checked taxon by taxon; the group and stratum counts that the page prints are
checked in full.

What it recomputes
  check 1  the selection of the primary contrast (group label, one sample per person, the per-cohort cap,
           the minimum cell size), the lifestyle strata, and the per-taxon detections in each stratum
  check 2  the subject table, each subject's cohort, the cohorts that hold both development bands, and the
           per-taxon carrier counts pooled and in those cohorts
  check 3  the depth strata and the per-taxon carrier counts inside them

    python verify_robustness.py --vanish-out DIR --sandpiper-dir DIR --hdi FILE
        [--external-labels FILE] [--cap 100] [--seed 20261123] [--n-taxa 20]

Exit status 0 when every comparison agrees.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

import duckdb
import pandas as pd

NI_STRICT = ("hunter_gatherer", "indigenous_community", "pastoralist", "traditional_agriculturalist")
EXT_NI = ("H-G", "Transitional")
ADULT = ("adult", "elderly")
CAP_DEFAULT = 100
MIN_CELL = 10
K = 5

fails = []
ran = []


def ok(name, cond, extra=""):
    note = ("  — " + str(extra)[:400]) if (extra and not cond) else ""
    print(("PASS  " if cond else "FAIL  ") + name + note)
    ran.append(name)
    if not cond:
        fails.append(name)
    return cond


def lit(vals):
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vanish-out", required=True)
    ap.add_argument("--sandpiper-dir", required=True)
    ap.add_argument("--hdi", required=True)
    ap.add_argument("--external-labels", required=True)
    ap.add_argument("--cap", type=int, default=CAP_DEFAULT)
    ap.add_argument("--seed", type=int, default=20261123)
    ap.add_argument("--n-taxa", type=int, default=20)
    a = ap.parse_args()
    out = a.vanish_out
    page = os.path.join(out, "page")
    work = os.path.join(out, "_work")
    R = json.loads(open(os.path.join(page, "robustness.json")).read())
    t_ls = pd.read_csv(os.path.join(page, "robustness_lifestyle.csv"))
    t_co = pd.read_csv(os.path.join(page, "robustness_cohort.csv"))
    t_de = pd.read_csv(os.path.join(page, "robustness_depth.csv"))

    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    con.execute("SET threads=4")
    sc = os.path.join(work, "samples_clean.parquet").replace("'", "''")
    con.execute("CREATE TEMP TABLE ext AS SELECT * FROM read_csv_auto('%s')" % a.external_labels)
    con.execute("CREATE TEMP TABLE hdi AS SELECT * FROM read_csv_auto('%s')" % a.hdi)

    # ---------------------------------------------------------------- check 1: the primary selection
    # group label (METHOD v2 A2, contrast C1: NI = stated strict labels or an external H-G/Transitional
    # label, REF = high-HDI country with no lifestyle or urban_industrialized), then one sample per person
    # by order_key, a cap of --cap per cohort and group, and cells under MIN_CELL dropped.
    con.execute(f"""CREATE TEMP TABLE sel1 AS
      WITH s AS (
        SELECT c.*, e.ext_label,
               CASE WHEN c.age_category IN ({lit(ADULT)}) THEN 'adult' END AS stratum,
               (h.band = 'high') AS hdi_high
        FROM read_parquet('{sc}') c
        LEFT JOIN (SELECT DISTINCT sample_key, ext_label FROM ext) e ON e.sample_key = c.sample_key
        LEFT JOIN hdi h ON h.iso2 = c.country),
      g AS (
        SELECT *,
               CASE WHEN ext_label IN ({lit(EXT_NI)}) AND lifestyle = 'urban_industrialized' THEN NULL
                    WHEN lifestyle IN ({lit(NI_STRICT)}) OR ext_label IN ({lit(EXT_NI)}) THEN 'NI'
                    WHEN coalesce(hdi_high, false)
                         AND (lifestyle IS NULL OR lifestyle = 'urban_industrialized') THEN 'REF'
               END AS grp
        FROM s WHERE stratum = 'adult'),
      p AS (SELECT *, row_number() OVER (PARTITION BY person ORDER BY order_key) AS rn
            FROM g WHERE grp IS NOT NULL),
      c1 AS (SELECT *, row_number() OVER (PARTITION BY cohort, grp ORDER BY order_key) AS rk
             FROM p WHERE rn = 1),
      c2 AS (SELECT *, count(*) OVER (PARTITION BY cohort, grp) AS cell FROM c1 WHERE rk <= {a.cap})
      SELECT sample_key, cohort, country, lifestyle, ext_label, grp FROM c2 WHERE cell >= {MIN_CELL}""")
    got = con.execute("SELECT grp, count(*) n, count(DISTINCT cohort) c FROM sel1 GROUP BY 1").df()
    gn = got[got.grp == "NI"].iloc[0]
    ok("check 1: the non-industrialized group of the primary contrast is the one the page reports",
       int(gn.n) == R["lifestyle"]["n_ni"] and int(gn.c) == R["lifestyle"]["n_cohorts"],
       "recomputed %d samples / %d cohorts, page says %d / %d"
       % (gn.n, gn.c, R["lifestyle"]["n_ni"], R["lifestyle"]["n_cohorts"]))
    # the lifestyle strata, with the same display names the page uses
    con.execute("""CREATE TEMP TABLE ni1 AS
      SELECT sample_key, cohort, country,
             CASE lifestyle WHEN 'hunter_gatherer' THEN 'hunter-gatherer'
                            WHEN 'indigenous_community' THEN 'indigenous community'
                            WHEN 'pastoralist' THEN 'pastoralist'
                            WHEN 'traditional_agriculturalist' THEN 'traditional agriculturalist'
                            WHEN 'rural_non_industrialized' THEN 'rural non-industrialized'
                            WHEN NULL THEN NULL
                            ELSE coalesce(replace(lifestyle, '_', ' '),
                                          'no value recorded (external label only)') END AS ls
      FROM sel1 WHERE grp = 'NI'""")
    st = con.execute("""SELECT ls, count(*) n, count(DISTINCT cohort) c, count(DISTINCT country) k
                        FROM ni1 GROUP BY 1 ORDER BY n DESC, ls""").df()
    page_st = {s["lifestyle"]: s for s in R["lifestyle"]["strata"]}
    ok("check 1: every lifestyle stratum has the sample, cohort and country count the page prints",
       len(st) == len(page_st) and all(
           s.ls in page_st and page_st[s.ls]["n_samples"] == int(s.n)
           and page_st[s.ls]["n_cohorts"] == int(s.c) and page_st[s.ls]["n_countries"] == int(s.k)
           for s in st.itertuples()),
       st.to_dict("records"))

    rng = random.Random(a.seed)
    picks = {}
    for lev in sorted(set(t_ls.level)):
        pool = sorted(set(t_ls[t_ls.level == lev].taxon))
        picks[lev] = rng.sample(pool, min(a.n_taxa, len(pool)))
    n_picked = sum(len(v) for v in picks.values())
    ok("at least %d classed taxa drawn with seed %d" % (a.n_taxa, a.seed), n_picked >= a.n_taxa, n_picked)

    bad = []
    for lev, taxa in picks.items():
        lf = os.path.join(work, "%s_long.parquet" % lev).replace("'", "''")
        d = con.execute(f"""SELECT p.taxon, n.ls, count(DISTINCT p.sample_key) n_pos
                            FROM read_parquet('{lf}') p JOIN ni1 n ON n.sample_key = p.sample_key
                            WHERE p.relabund > 0 AND p.taxon IN ({lit(taxa)})
                            GROUP BY 1, 2""").df()
        mine = {(r.taxon, r.ls): int(r.n_pos) for r in d.itertuples()}
        sub = t_ls[(t_ls.level == lev) & (t_ls.taxon.isin(taxa))]
        for r in sub.itertuples():
            if int(r.n_detected) != mine.get((r.taxon, r.lifestyle), 0):
                bad.append((lev, r.taxon, r.lifestyle, int(r.n_detected),
                            mine.get((r.taxon, r.lifestyle), 0)))
    ok("check 1: the detections of the drawn taxa in every lifestyle stratum agree (%d rows)"
       % int(sum(len(t_ls[(t_ls.level == lv) & (t_ls.taxon.isin(tx))]) for lv, tx in picks.items())),
       not bad, bad[:5])

    # ---------------------------------------------------------------- checks 2 and 3: the subjects
    con.execute(f"""CREATE TEMP TABLE sub2 AS
      WITH s AS (SELECT sample_key, person, country, cohort, root_coverage_sum,
                        CASE WHEN age_category IN ('adult','elderly') THEN 'adult'
                             WHEN age_category IN ('infant','neonate') THEN 'infant' END AS stratum
                 FROM read_parquet('{sc}')),
           k AS (SELECT * FROM s WHERE stratum IS NOT NULL AND country IS NOT NULL),
           cc AS (SELECT person, stratum, country, count(*) n FROM k GROUP BY 1,2,3),
           rc AS (SELECT person, stratum, country,
                         row_number() OVER (PARTITION BY person, stratum ORDER BY n DESC, country ASC) rn
                  FROM cc),
           band AS (SELECT rc.person, rc.stratum, rc.country,
                           CASE WHEN h.hdi_2023 >= 0.80 THEN 'ind'
                                WHEN h.hdi_2023 < 0.70 THEN 'nonind' ELSE 'trans' END AS band
                    FROM rc JOIN hdi h ON h.iso2 = rc.country
                    WHERE rc.rn = 1 AND h.hdi_2023 IS NOT NULL),
           smp AS (SELECT b.person, b.stratum, b.band, s.cohort, s.sample_key, s.root_coverage_sum
                   FROM band b JOIN s ON s.person = b.person
                   WHERE (b.stratum = 'adult' AND s.stratum = 'adult')
                      OR (b.stratum = 'infant' AND s.stratum = 'infant')),
           ch AS (SELECT person, stratum, cohort, count(*) n FROM smp GROUP BY 1,2,3),
           rk AS (SELECT person, stratum, cohort,
                         row_number() OVER (PARTITION BY person, stratum ORDER BY n DESC, cohort ASC) rn
                  FROM ch),
           dp AS (SELECT person, stratum, sum(root_coverage_sum) dep FROM smp GROUP BY 1,2)
      SELECT b.person, b.stratum, b.band, rk.cohort, dp.dep
      FROM band b JOIN rk ON rk.person = b.person AND rk.stratum = b.stratum AND rk.rn = 1
      JOIN dp ON dp.person = b.person AND dp.stratum = b.stratum
      WHERE b.band IN ('ind','nonind')""")
    con.execute(f"""CREATE TEMP TABLE sub3 AS
      SELECT *, CAST(floor((row_number() OVER (PARTITION BY stratum ORDER BY dep, person) - 1) * {K}
                 / count(*) OVER (PARTITION BY stratum)) AS BIGINT) + 1 AS qd FROM sub2""")
    cnt = con.execute("SELECT stratum, band, count(*) n FROM sub3 GROUP BY 1,2").df()
    want = R["cohort"]["subject_counts"]
    ok("checks 2 and 3: the subject counts per age group and band agree",
       all(int(r.n) == want[r.stratum][r.band] for r in cnt.itertuples()), cnt.to_dict("records"))
    both = con.execute("""SELECT stratum, count(*) n_both, sum(n) n_sub FROM (
            SELECT stratum, cohort, count(*) n, count(DISTINCT band) nb FROM sub3 GROUP BY 1,2)
          WHERE nb = 2 GROUP BY 1""").df()
    pc = {c["stratum"]: c for c in R["cohort"]["cohorts"]}
    ok("check 2: the cohorts that hold subjects of both bands agree",
       all(int(r.n_both) == pc[r.stratum]["n_cohorts_both_bands"]
           and int(r.n_sub) == pc[r.stratum]["n_subjects_both_bands"] for r in both.itertuples()),
       both.to_dict("records"))
    qn = con.execute("SELECT stratum, qd, band, count(*) n FROM sub3 GROUP BY 1,2,3").df()
    pq = {(q["stratum"], q["depth_quintile"], q["band"]): q["n"] for q in R["depth"]["quintiles"]}
    ok("check 3: the size of every depth stratum agrees",
       all(pq.get((r.stratum, int(r.qd), r.band)) == int(r.n) for r in qn.itertuples()),
       qn.to_dict("records"))

    # per-taxon carriage of the drawn taxa, from the profile tables
    rng2 = random.Random(a.seed)
    bad2, bad3, n2, n3 = [], [], 0, 0
    for lev in sorted(set(t_co.level)):
        pool = sorted(set(t_co[t_co.level == lev].taxon))
        taxa = rng2.sample(pool, min(a.n_taxa, len(pool)))
        src, col = {"species": ("gut_sandpiper_sample_species.parquet", "species"),
                    "genus": ("gut_sandpiper_sample_genus.parquet", "genus"),
                    "family": ("gut_sandpiper_sample_genus.parquet", "genus")}[lev]
        f = os.path.join(a.sandpiper_dir, src).replace("'", "''")
        if lev == "family":
            con.execute(f"""CREATE OR REPLACE TEMP TABLE fmap AS
                SELECT genus AS taxon, regexp_extract(lineage, 'f__[^;]*') AS family
                FROM (SELECT genus, min(lineage) lineage FROM read_parquet('{f}')
                      WHERE genus <> 'unassigned_at_genus' GROUP BY 1)
                WHERE regexp_extract(lineage, 'f__[^;]*') IN ({lit(taxa)})""")
            q = (f"SELECT m.family AS taxon, s.person, s.stratum, s.band, s.cohort, s.qd "
                 f"FROM read_parquet('{f}') p JOIN fmap m ON m.taxon = p.{col} "
                 f"JOIN read_parquet('{sc}') x ON x.sample_key = p.sample_key "
                 f"JOIN sub3 s ON s.person = x.person AND s.stratum = "
                 f"  CASE WHEN x.age_category IN ('adult','elderly') THEN 'adult' "
                 f"       WHEN x.age_category IN ('infant','neonate') THEN 'infant' END "
                 f"WHERE p.relabund > 0")
        else:
            q = (f"SELECT p.{col} AS taxon, s.person, s.stratum, s.band, s.cohort, s.qd "
                 f"FROM read_parquet('{f}') p "
                 f"JOIN read_parquet('{sc}') x ON x.sample_key = p.sample_key "
                 f"JOIN sub3 s ON s.person = x.person AND s.stratum = "
                 f"  CASE WHEN x.age_category IN ('adult','elderly') THEN 'adult' "
                 f"       WHEN x.age_category IN ('infant','neonate') THEN 'infant' END "
                 f"WHERE p.relabund > 0 AND p.{col} IN ({lit(taxa)})")
        con.execute("CREATE OR REPLACE TEMP TABLE c2 AS SELECT DISTINCT * FROM (" + q + ")")
        pooled = con.execute("SELECT taxon, stratum, band, count(*) n FROM c2 GROUP BY 1,2,3").df()
        P = {(r.taxon, r.stratum, r.band): int(r.n) for r in pooled.itertuples()}
        for r in t_co[(t_co.level == lev) & (t_co.taxon.isin(taxa))].itertuples():
            n2 += 1
            if (int(r.pos_ind) != P.get((r.taxon, r.stratum, "ind"), 0)
                    or int(r.pos_nonind) != P.get((r.taxon, r.stratum, "nonind"), 0)):
                bad2.append((lev, r.taxon, r.stratum, int(r.pos_ind), int(r.pos_nonind),
                             P.get((r.taxon, r.stratum, "ind"), 0), P.get((r.taxon, r.stratum, "nonind"), 0)))
        byq = con.execute("SELECT taxon, stratum, qd, band, count(*) n FROM c2 GROUP BY 1,2,3,4").df()
        Q = {(r.taxon, r.stratum, int(r.qd), r.band): int(r.n) for r in byq.itertuples()}
        for r in t_de[(t_de.level == lev) & (t_de.taxon.isin(taxa))].itertuples():
            n3 += 1
            if (int(r.pos_ind) != Q.get((r.taxon, r.stratum, int(r.depth_quintile), "ind"), 0)
                    or int(r.pos_nonind) != Q.get((r.taxon, r.stratum, int(r.depth_quintile), "nonind"), 0)):
                bad3.append((lev, r.taxon, r.stratum, int(r.depth_quintile)))
    ok("check 2: the pooled carrier counts of the drawn taxa agree (%d rows)" % n2, not bad2, bad2[:5])
    ok("check 3: the carrier counts of the drawn taxa in every depth stratum agree (%d rows)" % n3,
       not bad3, bad3[:5])

    print("\n%d checks, %d failed" % (len(ran), len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
