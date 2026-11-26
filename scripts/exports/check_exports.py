#!/usr/bin/env python3
"""Checks the files written by build_exports.py against the release tables they were built from.

  python check_exports.py --out <exports dir> --package <release package dir> --profiles-dir <dir>
         [--replication-claims <replication_claims.csv>]

Every check recomputes its number with SQL written separately from the build script. Exit code 1 on any failure.
"""
import argparse, csv, glob, hashlib, os, sys

FLAGS = ["qc_low_depth", "qc_non_metagenome", "qc_synthetic", "qc_rna",
         "qc_predicted_ecological", "qc_no_genus_assigned", "qc_low_complexity"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--profiles-dir", required=True)
    ap.add_argument("--replication-claims", default=None)
    ap.add_argument("--cells", type=int, default=40, help="prevalence cells recomputed from the source")
    ap.add_argument("--threads", type=int, default=3)
    a = ap.parse_args()
    import duckdb
    con = duckdb.connect()
    con.execute("SET threads=%d" % a.threads)
    con.execute("SET memory_limit='4GB'")
    os.makedirs(os.path.join(a.out, "_tmp_check"), exist_ok=True)
    con.execute("SET temp_directory='%s'" % os.path.join(a.out, "_tmp_check").replace("'", "''"))
    npass, fails = [0], []

    def ok(cond, what):
        if cond:
            npass[0] += 1
        else:
            fails.append(what)
            sys.stderr.write("FAIL: %s\n" % what)

    def one(sql, p=()):
        return con.execute(sql, list(p)).fetchone()[0]

    def f(pattern):
        hits = sorted(glob.glob(os.path.join(a.out, pattern)))
        if len(hits) != 1:
            sys.exit("expected exactly one %s in %s, found %d" % (pattern, a.out, len(hits)))
        return "'" + hits[0].replace("'", "''") + "'"

    def src(level):
        hits = sorted(glob.glob(os.path.join(a.profiles_dir, "*sandpiper_sample_%s*.parquet" % level)))
        return "read_parquet([%s])" % ", ".join("'" + h.replace("'", "''") + "'" for h in hits)

    # 1. manifest
    with open(os.path.join(a.out, "export_manifest.csv"), newline="") as fh:
        man = list(csv.DictReader(fh))
    for m in man:
        p = os.path.join(a.out, m["file"])
        ok(os.path.exists(p) and os.path.getsize(p) == int(m["bytes"]), "manifest size of %s" % m["file"])
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for b in iter(lambda: fh.read(1 << 20), b""):
                h.update(b)
        ok(h.hexdigest() == m["sha256"], "manifest sha256 of %s" % m["file"])
    ok(os.path.exists(os.path.join(a.out, "DATASET_CARD.md")), "dataset card present")

    S, G, T = "read_parquet(%s)" % f("ml_samples_v*.parquet"), "read_parquet(%s)" % f("ml_genera_v*.parquet"), \
        "read_parquet(%s)" % f("ml_genus_abundance_long_v*.parquet")
    P = "read_parquet(%s)" % f("taxon_prevalence_body_site_condition_v*.parquet")
    D = "read_csv(%s, header=true)" % f("prevalence_denominators_v*.csv")
    wide = "read_parquet('%s')" % os.path.join(a.package, "catalog_sample_metadata_wide.parquet").replace("'", "''")
    summ = "read_parquet('%s')" % os.path.join(a.package, "catalog_sandpiper_sample_summary.parquet").replace("'", "''")

    # 2. the sample set, recounted with a differently written rule
    flagged = " OR ".join("%s IS TRUE" % x for x in FLAGS)
    con.execute(f"""CREATE TEMP TABLE keep AS
        SELECT sample_key FROM {wide} w WHERE release_retired IS NULL AND sample_type = 'specimen'
          AND EXISTS (SELECT 1 FROM {summ} q WHERE q.sample_key = w.sample_key)
          AND NOT EXISTS (SELECT 1 FROM {summ} q WHERE q.sample_key = w.sample_key AND ({flagged}))""")
    n = one(f"SELECT count(*) FROM {S}")
    ok(n == one("SELECT count(*) FROM keep"), "sample count equals the recount (%d)" % n)
    ok(one(f"SELECT count(*) FROM {S} s ANTI JOIN keep k USING (sample_key)") == 0, "no sample outside the recounted set")
    ok(one(f"SELECT count(DISTINCT sample_key) FROM {S}") == n, "sample_key unique")
    ok(one(f"SELECT min(sample_index) = 0 AND max(sample_index) = count(*) - 1 AND count(DISTINCT sample_index) = count(*) FROM {S}"),
       "sample_index is 0..n-1")
    ok(one(f"SELECT count(*) FROM (SELECT sample_key, sample_index, row_number() OVER (ORDER BY sample_key) - 1 AS r FROM {S}) WHERE r <> sample_index") == 0,
       "sample_index follows sample_key order")
    ok(one(f"""SELECT count(*) FROM {S} s JOIN {wide} w USING (sample_key)
               WHERE w.release_retired IS NULL AND w.sample_type = 'specimen'
                 AND (s.health_condition IS DISTINCT FROM w.health_condition OR s.body_site_code IS DISTINCT FROM w.body_site_code
                  OR s.country IS DISTINCT FROM w.country OR s.age_category IS DISTINCT FROM w.age_category)""") == 0,
       "labels equal the release's sample table")

    ok(one(f"SELECT count(*) FROM {S} WHERE person_id IS DISTINCT FROM study_accession || '|' || person_key") == 0,
       "person_id is project accession | person_key")
    persons = one(f"""SELECT count(*) FROM (SELECT DISTINCT w.study_accession, coalesce(nullif(w.person_key, ''), w.sample_key)
                        FROM {wide} w SEMI JOIN keep k ON k.sample_key = w.sample_key
                        WHERE w.release_retired IS NULL AND w.sample_type = 'specimen')""")
    ok(one(f"SELECT count(DISTINCT person_id) FROM {S}") == persons, "persons counted per project (%d)" % persons)
    with open(os.path.join(a.out, "DATASET_CARD.md"), encoding="utf-8") as fh:
        ok(("%s persons" % f"{persons:,}") in fh.read(), "the dataset card states the person count %d" % persons)
    # 3. folds
    groups = con.execute(f"SELECT project_group, min(fold_5), max(fold_5), min(fold_10), max(fold_10) FROM {S} GROUP BY 1").fetchall()
    bad = 0
    for g, a5, b5, a10, b10 in groups:
        h = int(hashlib.md5(str(g).encode("utf-8")).hexdigest()[:8], 16)
        bad += int(not (a5 == b5 == h % 5 and a10 == b10 == h % 10))
    ok(bad == 0, "every project group in one fold, equal to the md5 rule (%d groups)" % len(groups))
    ok(one(f"SELECT count(*) FROM (SELECT study_accession FROM {S} GROUP BY 1 HAVING count(DISTINCT fold_5) > 1)") == 0,
       "no project split across folds")
    ok(one(f"SELECT count(DISTINCT fold_5) FROM {S}") == 5, "five folds in use")

    # 4. the sparse genus table
    nt = one(f"SELECT count(*) FROM {T}")
    ok(one(f"SELECT count(*) FROM (SELECT sample_index, genus_index FROM {T} GROUP BY 1, 2 HAVING count(*) > 1)") == 0, "no duplicate cell")
    ng = one(f"SELECT count(*) FROM {G}")
    ok(one(f"SELECT min(sample_index) >= 0 AND max(sample_index) < {n} AND min(genus_index) >= 0 AND max(genus_index) < {ng} FROM {T}"), "indexes in range")
    ok(one(f"SELECT min(relabund) > 0 AND max(relabund) <= 1.0001 FROM {T}"), "relative abundances in (0, 1]")
    ok(one(f"SELECT max(t) <= 1.001 FROM (SELECT sum(relabund) AS t FROM {T} GROUP BY sample_index)"), "per-sample sums at most 1")
    direct = one(f"""SELECT count(*) FROM (SELECT DISTINCT p.sample_key, p.genus FROM {src('genus')} p
                     SEMI JOIN keep k ON p.sample_key = k.sample_key WHERE p.genus <> 'unassigned_at_genus')""")
    ok(nt == direct, "non-zero cells equal the source (%d vs %d)" % (nt, direct))
    ok(one(f"SELECT count(*) FROM {G} g JOIN (SELECT genus_index, count(*) AS c FROM {T} GROUP BY 1) t USING (genus_index) WHERE g.n_samples <> t.c") == 0,
       "genus n_samples equals the cells")
    ok(one(f"SELECT count(*) FROM {S} s LEFT JOIN (SELECT sample_index, count(*) AS c FROM {T} GROUP BY 1) t USING (sample_index) WHERE s.n_genera_detected <> coalesce(t.c, 0)") == 0,
       "n_genera_detected equals the cells")
    diff = one(f"""WITH pick AS (SELECT * FROM {T} USING SAMPLE reservoir(300 ROWS) REPEATABLE (7))
                   SELECT count(*) FROM pick t JOIN {S} s USING (sample_index) JOIN {G} g USING (genus_index)
                   JOIN (SELECT sample_key, genus, sum(relabund) AS r FROM {src('genus')} GROUP BY 1, 2) p
                        ON p.sample_key = s.sample_key AND p.genus = g.genus
                   WHERE abs(p.r - t.relabund) <= 1e-6""")
    ok(diff == 300, "300 sampled cells equal the source value (%d)" % diff)

    # 5. prevalence
    ok(one(f"SELECT count(*) FROM {P} WHERE n_detected > n_samples OR n_detected < 1 OR abs(prevalence - n_detected / n_samples::DOUBLE) > 1e-9") == 0,
       "prevalence = n_detected / n_samples, 1 <= n_detected <= n_samples")
    ok(one(f"SELECT count(*) FROM (SELECT level, taxon, body_site_code, health_condition FROM {P} GROUP BY ALL HAVING count(*) > 1)") == 0,
       "one row per taxon, body site and condition")
    ok(one(f"""SELECT count(*) FROM (SELECT level, taxon, body_site_code,
                      sum(CASE WHEN health_condition = '(all)' THEN n_detected ELSE 0 END) AS a,
                      sum(CASE WHEN health_condition <> '(all)' THEN n_detected ELSE 0 END) AS b
                      FROM {P} GROUP BY 1, 2, 3) WHERE a <> b""") == 0, "condition rows add up to the (all) row")
    ok(one(f"""SELECT count(*) FROM (SELECT body_site_code, sum(CASE WHEN health_condition = '(all)' THEN n_samples ELSE 0 END) AS a,
                      sum(CASE WHEN health_condition <> '(all)' THEN n_samples ELSE 0 END) AS b FROM {D} GROUP BY 1) WHERE a <> b""") == 0,
       "denominators add up per body site")
    ok(one(f"SELECT sum(n_samples) FROM {D} WHERE health_condition = '(all)'") == n, "denominators cover every sample")
    cells = con.execute(f"""SELECT level, taxon, body_site_code, health_condition, n_samples, n_detected, n_projects_detected
                            FROM {P} WHERE health_condition <> '(all)' USING SAMPLE reservoir({int(a.cells)} ROWS) REPEATABLE (11)""").fetchall()
    bad = 0
    for level, taxon, site, cond, n_s, n_d, n_pd in cells:
        w = ["k.sample_key = w.sample_key", "w.release_retired IS NULL", "w.sample_type = 'specimen'"]
        params = []
        w.append("w.body_site_code IS NULL" if site == "(not recorded)" else "w.body_site_code = ?")
        params += [] if site == "(not recorded)" else [site]
        w.append("w.health_condition IS NULL" if cond == "(not recorded)" else "w.health_condition = ?")
        params += [] if cond == "(not recorded)" else [cond]
        grp = f"SELECT w.sample_key, w.study_accession FROM {wide} w, keep k WHERE " + " AND ".join(w)
        r = con.execute(f"""WITH grp AS ({grp})
                            SELECT (SELECT count(*) FROM grp), count(DISTINCT p.sample_key), count(DISTINCT g.study_accession)
                            FROM {src(level)} p JOIN grp g ON p.sample_key = g.sample_key WHERE p.{level} = ?""",
                        params + [taxon]).fetchone()
        bad += int(tuple(r) != (n_s, n_d, n_pd))
    ok(bad == 0, "%d sampled prevalence cells recomputed from the source (%d differ)" % (len(cells), bad))

    # 6. replication rows
    rt = glob.glob(os.path.join(a.out, "replication_signature_taxa_v*.csv"))
    if rt and a.replication_claims:
        RT = "read_csv('%s', header=true, all_varchar=true)" % rt[0].replace("'", "''")
        RS = "read_csv(%s, header=true, all_varchar=true)" % f("replication_signatures_v*.csv")
        RC = "read_csv('%s', header=true, all_varchar=true)" % a.replication_claims.replace("'", "''")
        nc = one(f"SELECT count(*) FROM {RC}")
        ok(one(f"SELECT count(*) FROM {RT}") == nc, "replication rows equal the run's claims (%d)" % nc)
        ok(one(f"SELECT count(*) FROM {RT} t JOIN {RC} c USING (claim_id) WHERE t.result IS DISTINCT FROM c.result OR t.auc_pooled IS DISTINCT FROM c.auc_pooled") == 0,
           "results unchanged from the run")
        # bsdb:<study>/<e>/<s> -> https://bugsigdb.org/<study>/Experiment_<e>/Signature_<s>; BugSigDB names a study
        # page by its PubMed id, or "Study_<n>" when it has none. The link is rebuilt here from the id alone.
        ok(one(f"""SELECT count(*) FROM (
                     SELECT bugsigdb_url, split_part(replace(bsdb_id, 'bsdb:', ''), '/', 1) AS a,
                            split_part(bsdb_id, '/', 2) AS e, split_part(bsdb_id, '/', 3) AS s FROM {RT})
                   WHERE bugsigdb_url IS NULL OR a = '' OR e = '' OR s = ''
                      OR bugsigdb_url NOT IN ('https://bugsigdb.org/' || a || '/Experiment_' || e || '/Signature_' || s,
                                              'https://bugsigdb.org/Study_' || a || '/Experiment_' || e || '/Signature_' || s)""") == 0,
           "every row has a BugSigDB signature link that agrees with its signature id")
        ok(one(f"SELECT count(*) FROM {RS}") == one(f"SELECT count(DISTINCT bsdb_id) FROM {RC}"), "one row per signature")
        ok(one(f"SELECT sum(CAST(n_taxa_registered AS INTEGER)) FROM {RS}") == nc, "signature rows add up to the claims")
        ncols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {RS}").fetchall() if r[0].startswith("n_") and r[0] != "n_taxa_registered"]
        tot = one("SELECT " + " + ".join('sum(CAST("%s" AS INTEGER))' % c for c in ncols) + f" FROM {RS}")
        ok(tot == nc, "result counts of the signature rows add up to the claims")
    con.close()
    try:
        for x in os.listdir(os.path.join(a.out, "_tmp_check")):
            os.remove(os.path.join(a.out, "_tmp_check", x))
        os.rmdir(os.path.join(a.out, "_tmp_check"))
    except OSError:
        pass
    print("check_exports: %d passed, %d failed" % (npass[0], len(fails)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
