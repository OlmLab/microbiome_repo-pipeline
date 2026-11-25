#!/usr/bin/env python3
"""Analysis-ready exports of one Microbiome Repo release (release downloads, not site files).

  python build_exports.py --package <release package dir> --profiles-dir <dir with the two profile tables>
         --replication-claims <replication_claims.csv> --bugsigdb-claims <claims.parquet> --out <dir>

Writes into --out (<v> = package version):
  ml_samples_v<v>.parquet                   one row per sample: labels and fixed leave-project-out folds
  ml_genus_abundance_long_v<v>.parquet      the samples x genus table as sparse triplets (sample_index, genus_index, relabund)
  ml_genera_v<v>.parquet                    the genus axis (genus_index, genus, lineage, n_samples)
  taxon_prevalence_body_site_condition_v<v>.parquet   genus and species prevalence per body site and condition
  prevalence_denominators_v<v>.csv          samples and projects per body site and condition (the denominators)
  replication_signature_taxa_v<v>.csv       one row per registered claim (signature x taxon) with its BugSigDB source
  replication_signatures_v<v>.csv           one row per BugSigDB signature with result counts and its source link
  DATASET_CARD.md                           what the files are, with the counts of this build
  export_manifest.csv                       file, rows, bytes, sha256

Samples: current release, sample_type = specimen, with a profile, none of the seven QC flags (the same set the
taxon explorer uses). The script only reads the release tables; it changes nothing in the release.
Out of core: DuckDB with a memory limit and a temp directory; no table is loaded into Python.
"""
import argparse, csv, glob, hashlib, json, os, sys, time

QC_FLAGS = ["qc_low_depth", "qc_non_metagenome", "qc_synthetic", "qc_rna",
            "qc_predicted_ecological", "qc_no_genus_assigned", "qc_low_complexity"]
LABELS = ["body_site_code", "body_site_class", "age_category", "age_at_collection_days", "sex", "bmi",
          "country", "lifestyle", "health_condition", "comparison_group", "antibiotic_exposure",
          "collection_year"]
FOLDS = (5, 10)
NOT_RECORDED = "(not recorded)"
ALL = "(all)"
BUGSIGDB = "https://bugsigdb.org/"


def fold_of(project_group, k):
    """The fixed fold of a project group: first 8 hex digits of md5(project_group), modulo k."""
    return int(hashlib.md5(str(project_group).encode("utf-8")).hexdigest()[:8], 16) % k


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def q(s):
    return "'" + str(s).replace("'", "''") + "'"


def slug(s):
    out = "".join(c if c.isalnum() else "_" for c in str(s).lower())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--package", required=True)
    ap.add_argument("--profiles-dir", required=True,
                    help="directory holding *sandpiper_sample_genus*.parquet and *sandpiper_sample_species*.parquet")
    ap.add_argument("--replication-claims", default=None, help="replication_claims.csv of the replication run")
    ap.add_argument("--bugsigdb-claims", default=None, help="claims.parquet of the replication run (BugSigDB rows)")
    ap.add_argument("--replication-label", default="", help="text for the card: which run the replication rows come from")
    ap.add_argument("--out", required=True)
    ap.add_argument("--memory-limit", default="4GB")
    ap.add_argument("--threads", type=int, default=3)
    a = ap.parse_args()
    import duckdb
    t0 = time.time()
    os.makedirs(a.out, exist_ok=True)
    tmp = os.path.join(a.out, "_tmp")
    os.makedirs(tmp, exist_ok=True)
    ver = json.load(open(os.path.join(a.package, "VERSION.json")))
    v = ver.get("package_version", "unknown")
    rel = ver.get("release_id", "unknown")
    tag = "_v" + v
    con = duckdb.connect()
    con.execute("SET memory_limit=%s" % q(a.memory_limit))
    con.execute("SET threads=%d" % a.threads)
    con.execute("SET temp_directory=%s" % q(tmp))
    con.execute("SET preserve_insertion_order=false")

    def log(msg):
        sys.stderr.write("%s  %s\n" % (time.strftime("%H:%M:%S"), msg))
        sys.stderr.flush()

    def profile(level):
        hits = sorted(glob.glob(os.path.join(a.profiles_dir, "*sandpiper_sample_%s*.parquet" % level)))
        if not hits:
            sys.exit("no %s profile table in %s" % (level, a.profiles_dir))
        return "read_parquet([%s])" % ", ".join(q(h) for h in hits)

    md = "read_parquet(%s)" % q(os.path.join(a.package, "catalog_sample_metadata_wide.parquet"))
    stu = "read_parquet(%s)" % q(os.path.join(a.package, "catalog_studies.parquet"))
    summ = "read_parquet(%s)" % q(os.path.join(a.package, "catalog_sandpiper_sample_summary.parquet"))
    qc = " AND ".join("NOT coalesce(q.%s, false)" % f for f in QC_FLAGS)
    labels = ", ".join("m." + c for c in LABELS)
    files = []     # (name, rows)

    # ---------------------------------------------------------------- 1. samples, labels, folds
    con.execute(f"""
        CREATE TEMP TABLE s0 AS
        SELECT m.sample_key, m.study_accession,
               coalesce(nullif(s.study_group_id, ''), m.study_accession) AS project_group,
               coalesce(nullif(m.person_key, ''), m.sample_key) AS person_key,
               {labels},
               q.root_coverage_sum, q.richness_genus, q.shannon_genus
        FROM {md} m
        JOIN (SELECT sample_key, root_coverage_sum, richness_genus, shannon_genus, {", ".join(QC_FLAGS)}
              FROM {summ}) q USING (sample_key)
        LEFT JOIN (SELECT study_accession, study_group_id FROM {stu} WHERE release_retired IS NULL) s
               USING (study_accession)
        WHERE m.release_retired IS NULL AND m.sample_type = 'specimen' AND {qc}""")
    groups = [r[0] for r in con.execute("SELECT DISTINCT project_group FROM s0 ORDER BY 1").fetchall()]
    con.execute("CREATE TEMP TABLE folds (project_group VARCHAR, %s)" % ", ".join("fold_%d TINYINT" % k for k in FOLDS))
    con.executemany("INSERT INTO folds VALUES (?, %s)" % ", ".join("?" for _ in FOLDS),
                    [[g] + [fold_of(g, k) for k in FOLDS] for g in groups])
    con.execute(f"""
        CREATE TEMP TABLE samples AS
        SELECT CAST(row_number() OVER (ORDER BY s0.sample_key) - 1 AS INTEGER) AS sample_index, s0.*,
               {", ".join("f.fold_%d" % k for k in FOLDS)}
        FROM s0 JOIN folds f USING (project_group)""")
    n_samples = con.execute("SELECT count(*) FROM samples").fetchone()[0]
    log("samples: %d" % n_samples)

    # ---------------------------------------------------------------- 2. genus axis and sparse triplets
    pg = profile("genus")
    con.execute(f"""
        CREATE TEMP TABLE genera AS
        SELECT CAST(row_number() OVER (ORDER BY genus) - 1 AS INTEGER) AS genus_index, genus, lineage, n_samples
        FROM (SELECT p.genus, min(p.lineage) AS lineage, count(DISTINCT p.sample_key) AS n_samples
              FROM {pg} p SEMI JOIN samples s ON p.sample_key = s.sample_key
              WHERE p.genus <> 'unassigned_at_genus' GROUP BY 1)""")
    f_long = os.path.join(a.out, "ml_genus_abundance_long%s.parquet" % tag)
    con.execute(f"""
        COPY (SELECT s.sample_index, g.genus_index, CAST(sum(p.relabund) AS FLOAT) AS relabund
              FROM {pg} p JOIN samples s ON p.sample_key = s.sample_key JOIN genera g ON p.genus = g.genus
              GROUP BY 1, 2 ORDER BY 1, 2)
        TO {q(f_long)} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 1000000)""")
    n_long = con.execute(f"SELECT count(*) FROM read_parquet({q(f_long)})").fetchone()[0]
    files.append((os.path.basename(f_long), n_long))
    f_gen = os.path.join(a.out, "ml_genera%s.parquet" % tag)
    con.execute(f"COPY (SELECT * FROM genera ORDER BY genus_index) TO {q(f_gen)} (FORMAT PARQUET, COMPRESSION ZSTD)")
    n_genera = con.execute("SELECT count(*) FROM genera").fetchone()[0]
    files.append((os.path.basename(f_gen), n_genera))
    f_s = os.path.join(a.out, "ml_samples%s.parquet" % tag)
    con.execute(f"""
        COPY (SELECT s.*, coalesce(d.n, 0) AS n_genera_detected
              FROM samples s LEFT JOIN (SELECT sample_index, count(*) AS n FROM read_parquet({q(f_long)}) GROUP BY 1) d
                   USING (sample_index) ORDER BY s.sample_index)
        TO {q(f_s)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
    files.append((os.path.basename(f_s), n_samples))
    log("genus table: %d genera, %d non-zero cells" % (n_genera, n_long))

    # ---------------------------------------------------------------- 3. prevalence by body site and condition
    con.execute(f"""
        CREATE TEMP TABLE sc AS
        SELECT sample_key, study_accession, coalesce(body_site_code, {q(NOT_RECORDED)}) AS site,
               coalesce(health_condition, {q(NOT_RECORDED)}) AS cond FROM samples""")
    con.execute(f"""
        CREATE TEMP TABLE cells AS
        SELECT site, CASE WHEN grouping(cond) = 1 THEN {q(ALL)} ELSE cond END AS cond,
               count(*) AS n_samples, count(DISTINCT study_accession) AS n_projects
        FROM sc GROUP BY GROUPING SETS ((site, cond), (site))""")
    parts, below = [], {}
    for level in ("genus", "species"):
        src = profile(level)
        con.execute(f"""
            CREATE TEMP TABLE prev_{level} AS
            SELECT {q(level)} AS level, p.{level} AS taxon, min(p.lineage) AS lineage, s.site,
                   CASE WHEN grouping(s.cond) = 1 THEN {q(ALL)} ELSE s.cond END AS cond,
                   count(DISTINCT p.sample_key) AS n_detected, avg(p.relabund) AS mean_relabund_detected,
                   count(DISTINCT s.study_accession) AS n_projects_detected
            FROM {src} p JOIN sc s ON p.sample_key = s.sample_key
            WHERE p.{level} <> 'unassigned_at_{level}'
            GROUP BY GROUPING SETS ((p.{level}, s.site, s.cond), (p.{level}, s.site))""")
        below[level] = con.execute(f"""SELECT avg(CAST(p.relabund < 0.001 AS INTEGER))
                                       FROM {src} p SEMI JOIN sc s ON p.sample_key = s.sample_key
                                       WHERE p.{level} <> 'unassigned_at_{level}'""").fetchone()[0]
        parts.append("SELECT * FROM prev_%s" % level)
        log("prevalence %s done" % level)
    f_prev = os.path.join(a.out, "taxon_prevalence_body_site_condition%s.parquet" % tag)
    con.execute(f"""
        COPY (SELECT t.level, t.taxon, t.lineage, t.site AS body_site_code, t.cond AS health_condition,
                     c.n_samples, t.n_detected, CAST(t.n_detected AS DOUBLE) / c.n_samples AS prevalence,
                     CAST(t.mean_relabund_detected AS FLOAT) AS mean_relabund_detected,
                     c.n_projects, t.n_projects_detected
              FROM ({" UNION ALL ".join(parts)}) t JOIN cells c ON t.site = c.site AND t.cond = c.cond
              ORDER BY t.level, t.taxon, t.site, t.cond)
        TO {q(f_prev)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
    n_prev = con.execute(f"SELECT count(*) FROM read_parquet({q(f_prev)})").fetchone()[0]
    files.append((os.path.basename(f_prev), n_prev))
    f_den = os.path.join(a.out, "prevalence_denominators%s.csv" % tag)
    con.execute(f"""COPY (SELECT site AS body_site_code, cond AS health_condition, n_samples, n_projects
                          FROM cells ORDER BY 1, 2) TO {q(f_den)} (HEADER, DELIMITER ',')""")
    files.append((os.path.basename(f_den), con.execute("SELECT count(*) FROM cells").fetchone()[0]))

    # ---------------------------------------------------------------- 4. replication results as signature rows
    rep = None
    if a.replication_claims and a.bugsigdb_claims:
        rc = "read_csv(%s, header=true, all_varchar=true)" % q(a.replication_claims)
        bs = "read_parquet(%s)" % q(a.bugsigdb_claims)
        con.execute(f"""
            CREATE TEMP TABLE src AS
            SELECT bsdb_id, any_value(study) AS bugsigdb_study, any_value(experiment) AS bugsigdb_experiment,
                   any_value(signature_page_name) AS bugsigdb_signature, any_value(doi) AS doi,
                   any_value(body_site) AS bugsigdb_body_site, any_value(statistical_test) AS statistical_test,
                   any_value(group_0_sample_size) AS group_0_sample_size,
                   any_value(group_1_sample_size) AS group_1_sample_size
            FROM {bs} GROUP BY 1""")
        con.execute(f"""
            CREATE TEMP TABLE rep AS
            SELECT r.*, s.bugsigdb_study, s.bugsigdb_experiment, s.bugsigdb_signature,
                   {q(BUGSIGDB)} || replace(s.bugsigdb_study, ' ', '_') || '/' || replace(s.bugsigdb_experiment, ' ', '_')
                       || '/' || replace(s.bugsigdb_signature, ' ', '_') AS bugsigdb_url,
                   CASE WHEN r.pmid IS NOT NULL AND r.pmid <> '' THEN 'https://pubmed.ncbi.nlm.nih.gov/' || r.pmid || '/' END AS pubmed_url,
                   s.doi, s.bugsigdb_body_site, s.statistical_test, s.group_0_sample_size, s.group_1_sample_size
            FROM {rc} r LEFT JOIN src s USING (bsdb_id)""")
        f_rt = os.path.join(a.out, "replication_signature_taxa%s.csv" % tag)
        con.execute(f"COPY (SELECT * FROM rep ORDER BY bsdb_id, claim_id) TO {q(f_rt)} (HEADER, DELIMITER ',')")
        n_rt = con.execute("SELECT count(*) FROM rep").fetchone()[0]
        files.append((os.path.basename(f_rt), n_rt))
        results = [r[0] for r in con.execute("SELECT DISTINCT result FROM rep ORDER BY 1").fetchall()]
        pivots = ", ".join("count(*) FILTER (WHERE result = %s) AS %s" % (q(r), '"n_' + slug(r) + '"') for r in results)
        f_rs = os.path.join(a.out, "replication_signatures%s.csv" % tag)
        con.execute(f"""
            COPY (SELECT bsdb_id, any_value(bugsigdb_url) AS bugsigdb_url, any_value(bugsigdb_study) AS bugsigdb_study,
                         any_value(bugsigdb_experiment) AS bugsigdb_experiment,
                         any_value(bugsigdb_signature) AS bugsigdb_signature, any_value(pmid) AS pmid,
                         any_value(pubmed_url) AS pubmed_url, any_value(doi) AS doi, any_value(year) AS year,
                         any_value(sequencing_type) AS sequencing_type, any_value(condition) AS condition,
                         any_value(efo_id) AS efo_id, any_value(claimed) AS claimed,
                         count(*) AS n_taxa_registered, {pivots},
                         string_agg(taxon, '; ' ORDER BY taxon) AS taxa
                  FROM rep GROUP BY bsdb_id ORDER BY bsdb_id)
            TO {q(f_rs)} (HEADER, DELIMITER ',')""")
        n_rs = con.execute("SELECT count(DISTINCT bsdb_id) FROM rep").fetchone()[0]
        files.append((os.path.basename(f_rs), n_rs))
        rep = dict(n_claims=n_rt, n_signatures=n_rs,
                   n_no_source=con.execute("SELECT count(*) FROM rep WHERE bugsigdb_url IS NULL").fetchone()[0],
                   results=con.execute("SELECT result, count(*) FROM rep GROUP BY 1 ORDER BY 2 DESC").fetchall(),
                   n_papers=con.execute("SELECT count(DISTINCT pmid) FROM rep").fetchone()[0])
        log("replication rows: %d claims, %d signatures" % (n_rt, n_rs))

    # ---------------------------------------------------------------- 5. dataset card and manifest
    one = lambda sql: con.execute(sql).fetchone()[0]
    n_projects = one("SELECT count(DISTINCT study_accession) FROM samples")
    n_groups = one("SELECT count(DISTINCT project_group) FROM samples")
    n_persons = one("SELECT count(DISTINCT person_key) FROM samples")
    sites = con.execute("""SELECT coalesce(body_site_code, '(not recorded)'), count(*), count(DISTINCT study_accession)
                           FROM samples GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    cover = [(c, one('SELECT count("%s") FROM samples' % c)) for c in LABELS]
    foldrows = con.execute("""SELECT fold_5, count(*), count(DISTINCT project_group), count(DISTINCT study_accession)
                              FROM samples GROUP BY 1 ORDER BY 1""").fetchall()
    n_cond = one("SELECT count(DISTINCT health_condition) FROM samples")
    prev_counts = con.execute(f"""SELECT level, count(*), count(DISTINCT taxon) FROM read_parquet({q(f_prev)})
                                  GROUP BY 1 ORDER BY 1""").fetchall()
    L = []
    L.append("# Microbiome Repo — analysis-ready exports, release %s (data package %s)" % (rel, v))
    L.append("")
    L.append("Built by `build_exports.py` on %s from the release tables. These files are release downloads; "
             "the website does not read them." % time.strftime("%Y-%m-%d"))
    L.append("")
    L.append("## What is in the samples × genus table")
    L.append("")
    L.append("- **Samples:** %s samples from %s projects (%s project groups, %s persons). A sample is included when it is "
             "in the current release, is a specimen, has a taxonomic profile and carries none of the seven quality flags "
             "(%s)." % (f"{n_samples:,}", f"{n_projects:,}", f"{n_groups:,}", f"{n_persons:,}", ", ".join("`%s`" % f for f in QC_FLAGS)))
    L.append("- **Genera:** %s GTDB genera; %s non-zero cells. Values are relative abundances (fractions of the "
             "profile); the unassigned fraction is left out, so a sample's values sum to at most 1." % (f"{n_genera:,}", f"{n_long:,}"))
    L.append("- **Layout:** the table is stored as sparse triplets, because %s × %s cells are almost all zero. "
             "`ml_samples` is the row axis, `ml_genera` the column axis, `ml_genus_abundance_long` the non-zero cells." % (f"{n_samples:,}", f"{n_genera:,}"))
    L.append("")
    L.append("```python")
    L.append("import pandas as pd, scipy.sparse as sp")
    L.append("s = pd.read_parquet('ml_samples%s.parquet')" % tag)
    L.append("g = pd.read_parquet('ml_genera%s.parquet')" % tag)
    L.append("t = pd.read_parquet('ml_genus_abundance_long%s.parquet')" % tag)
    L.append("X = sp.csr_matrix((t.relabund, (t.sample_index, t.genus_index)), shape=(len(s), len(g)))")
    L.append("train, test = s.fold_5 != 0, s.fold_5 == 0      # row i of X is row i of s")
    L.append("```")
    L.append("")
    L.append("| Body site | Samples | Projects |")
    L.append("|---|---|---|")
    for site, n, p in sites:
        L.append("| %s | %s | %s |" % (site, f"{n:,}", f"{p:,}"))
    L.append("")
    L.append("## Labels")
    L.append("")
    L.append("Labels are the release's curated sample fields, unchanged. A missing value means not recorded, not negative.")
    L.append("")
    L.append("| Column | Samples with a value | Share |")
    L.append("|---|---|---|")
    for c, n in cover:
        L.append("| `%s` | %s | %.1f %% |" % (c, f"{n:,}", 100.0 * n / max(n_samples, 1)))
    L.append("")
    L.append("Other columns: `sample_key`, `study_accession`, `project_group`, `person_key`, `root_coverage_sum` (profile depth), "
             "`richness_genus`, `shannon_genus`, `n_genera_detected`.")
    L.append("")
    L.append("## Fixed leave-project-out splits")
    L.append("")
    L.append("- `project_group` joins projects that share participants (the release's study group); otherwise it is the project accession. "
             "Use it as the group for leave-one-project-out validation.")
    L.append("- `fold_5` and `fold_10` put every project group into one fold: the first 8 hex digits of `md5(project_group)`, as an integer, modulo 5 or 10. "
             "A project group keeps its fold in every later release. Folds are not balanced in size.")
    L.append("- A person can have several samples. For one sample per person, keep the first row per `person_key`.")
    L.append("")
    L.append("| fold_5 | Samples | Project groups | Projects |")
    L.append("|---|---|---|---|")
    for f, n, gcount, p in foldrows:
        L.append("| %d | %s | %s | %s |" % (f, f"{n:,}", f"{gcount:,}", f"{p:,}"))
    L.append("")
    L.append("## Taxon × body site × condition prevalence")
    L.append("")
    L.append("`taxon_prevalence_body_site_condition%s.parquet`: one row per taxon, body site and condition in which the taxon was detected at least once." % tag)
    L.append("")
    for level, n, nt in prev_counts:
        L.append("- %s: %s rows, %s taxa." % (level, f"{n:,}", f"{nt:,}"))
    L.append("- `health_condition` is the release's condition field (%d values); `%s` = no condition recorded; `%s` = all samples of the body site." % (n_cond, NOT_RECORDED, ALL))
    L.append("- `prevalence` = `n_detected` / `n_samples`, counted over samples, not persons. `n_projects_detected` shows how many projects carry the detections.")
    L.append("- A taxon without a row for a body site and condition was not detected there; the denominators are in `prevalence_denominators%s.csv`." % tag)
    L.append("- The same samples as the genus table. Conditions are as recorded by the source projects; a condition group is not a matched case group.")
    L.append("")
    if rep:
        L.append("## Replication results as signature rows")
        L.append("")
        if a.replication_label:
            L.append("Source: %s." % a.replication_label)
            L.append("")
        L.append("- `replication_signature_taxa%s.csv`: %s registered claims (one taxon of one BugSigDB signature) from %s papers." % (tag, f"{rep['n_claims']:,}", f"{rep['n_papers']:,}"))
        L.append("- `replication_signatures%s.csv`: the same results as %s signature rows with a count per result." % (tag, f"{rep['n_signatures']:,}"))
        L.append("- Every row carries `bsdb_id` and `bugsigdb_url` (the signature's page at BugSigDB), `pmid` and `doi`. Rows without a source link: %d." % rep["n_no_source"])
        L.append("")
        L.append("| Result | Claims |")
        L.append("|---|---|")
        for r, n in rep["results"]:
            L.append("| %s | %s |" % (r, f"{n:,}"))
        L.append("")
    L.append("## Limits")
    L.append("")
    L.append("- Profiles come from one profiler and one reference taxonomy; names are GTDB names.")
    L.append("- Detection depends on the profile tables: %.2f %% of species rows and %.2f %% of genus rows of these samples are below a relative abundance of 0.001. "
             "Where the species share is near zero the species table has a detection floor that the genus table lacks." % (100.0 * (below.get("species") or 0), 100.0 * (below.get("genus") or 0)))
    L.append("- Sequencing depth differs between projects by orders of magnitude (`root_coverage_sum`); prevalence is not depth-adjusted.")
    L.append("- Labels are incomplete and differ in coverage between projects; see the table above.")
    L.append("- Samples, not persons, are counted; projects with many samples per person weigh more.")
    L.append("")
    L.append("## Citation and licence")
    L.append("")
    L.append("Cite the release (release id %s, data package %s) as given on the website's About page, and the upstream sources named there. The licence is the licence of the release." % (rel, v))
    L.append("")
    L.append("## Files")
    L.append("")
    L.append("| File | Rows | Bytes |")
    L.append("|---|---|---|")
    man = []
    for name, rows in files:
        p = os.path.join(a.out, name)
        man.append((name, rows, os.path.getsize(p), sha256(p)))
        L.append("| `%s` | %s | %s |" % (name, f"{rows:,}", f"{os.path.getsize(p):,}"))
    L.append("")
    with open(os.path.join(a.out, "DATASET_CARD.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    with open(os.path.join(a.out, "export_manifest.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "rows", "bytes", "sha256", "release_id", "package_version"])
        for name, rows, size, h in man:
            w.writerow([name, rows, size, h, rel, v])
    con.close()
    try:
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)
    except OSError:
        pass
    log("done in %.0f s: %d files, %.1f MB" % (time.time() - t0, len(man), sum(m[2] for m in man) / 1e6))


if __name__ == "__main__":
    main()
