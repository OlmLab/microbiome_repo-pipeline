#!/usr/bin/env python3
"""Rebuild the three condition-explorer tables from a data package and a condition-terms lookup.

    python build_condition_explorer.py --package DIR --terms condition_terms_r44.csv \
        --static DIR_WITH_THE_THREE_HANDED_OVER_TABLES --out DIR [--check]

Every count is computed from the package (current rows only: release_retired IS NULL).  From --static the
script takes only what the package cannot give and what belongs to the registered replication run:
`anchor`, `term_branch`, `disease_areas`, `broader_condition_ids`, `narrower_condition_ids`, the five
`replication_*` columns, `replicated_taxa`, `replicated_associations`, and `condition_explorer_associations.csv`,
which is copied through unchanged.

Term lookup for one sample, in this order (first hit wins):
    1. a row of --terms with that sample's (study_accession, health_condition, health_condition_detail)
    2. a row with an empty study_accession and the same (health_condition, health_condition_detail)
    3. a row with an empty study_accession, the same health_condition and health_condition_detail = '*'
A case is a sample whose resolved row has role `case` and a non-empty condition_id; it counts once, under
that condition_id.  Healthy controls are samples whose resolved row has role `healthy_control`, counted in
the projects that hold at least one case of the condition (they are not matched controls).

DuckDB runs with a 2 GB memory limit and no package table is read into memory whole.  No network.
"""
from __future__ import annotations

import argparse
import os
import sys

import duckdb
import pandas as pd

MAIN_COLS = ["condition_id", "anchor", "condition_name", "efo_id", "mondo_id", "efo_legacy_ids", "term_branch",
             "disease_areas", "projects", "cases", "cases_current", "cases_treated_or_remission", "cases_at_risk_or_pre",
             "cases_history", "case_people", "projects_with_healthy_controls", "healthy_controls",
             "healthy_controls_profiled", "n_countries", "countries", "cases_country_unknown", "cases_age_neonate",
             "cases_age_infant", "cases_age_child", "cases_age_adolescent", "cases_age_adult", "cases_age_elderly",
             "cases_age_unknown", "cases_profiled", "cases_profiled_qc_clean", "cases_gut_stool", "body_sites",
             "other_control_samples_with_condition", "n_label_strings", "cases_incl_narrower", "projects_incl_narrower",
             "broader_condition_ids", "narrower_condition_ids", "replication_scope", "replication_condition_id",
             "replication_condition_name", "replication_claims_testable", "replication_claims_replicated",
             "replicated_taxa", "replicated_associations"]
PROJ_COLS = ["condition_id", "condition_name", "study_accession", "project_title", "cases", "cases_current",
             "case_people", "cases_profiled", "cases_profiled_qc_clean", "healthy_controls", "healthy_controls_profiled",
             "healthy_control_people", "countries", "age_bands", "body_sites"]
STATIC_COLS = ["anchor", "term_branch", "disease_areas", "broader_condition_ids", "narrower_condition_ids",
               "replication_scope", "replication_condition_id", "replication_condition_name",
               "replication_claims_testable", "replication_claims_replicated", "replicated_taxa",
               "replicated_associations"]
AGE_BANDS = ["neonate", "infant", "child", "adolescent", "adult", "elderly"]
BODY_SITES_SHOWN = 6          # the main table lists the six most frequent body sites
PROJ_SITES_SHOWN = 4          # the project table lists the four most frequent body sites
PROJ_COUNTRIES_SHOWN = 10     # and at most ten countries
INT_COLS = [c for c in MAIN_COLS if c.startswith(("projects", "cases", "case_people", "healthy", "n_", "other_control",
                                                  "replication_claims", "replicated_taxa"))]


def connect(package: str, terms: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET memory_limit='2GB'")
    con.execute("SET preserve_insertion_order=false")
    con.execute(f"""CREATE TABLE terms AS SELECT
        COALESCE(study_accession,'') AS study_accession, COALESCE(health_condition,'') AS health_condition,
        COALESCE(health_condition_detail,'') AS health_condition_detail, COALESCE("role",'') AS "role",
        COALESCE("state",'') AS "state", COALESCE(condition_name,'') AS condition_name, COALESCE(efo_id,'') AS efo_id,
        COALESCE(mondo_id,'') AS mondo_id, COALESCE(condition_id,'') AS condition_id,
        COALESCE(efo_legacy_ids,'') AS efo_legacy_ids
        FROM read_csv_auto('{terms}', header=true, all_varchar=true)""")
    w = os.path.join(package, "catalog_sample_metadata_wide.parquet")
    sp = os.path.join(package, "catalog_sandpiper_sample_summary.parquet")
    con.execute(f"""CREATE TABLE s AS
        WITH w AS (SELECT sample_key, study_accession, COALESCE(health_condition,'') AS hc,
                          COALESCE(health_condition_detail,'') AS hcd, person_key, country, age_category, body_site_code
                   FROM '{w}' WHERE release_retired IS NULL)
        SELECT w.sample_key, w.study_accession, w.hc, w.hcd, w.person_key, w.country, w.age_category, w.body_site_code,
               COALESCE(pp."role", gg."role", st."role", '') AS role,
               COALESCE(pp.condition_id, gg.condition_id, st.condition_id, '') AS cid,
               COALESCE(pp."state", gg."state", st."state", '') AS state,
               COALESCE(sp.n_runs_profiled, 0) > 0 AS profiled,
               COALESCE(sp.n_runs_profiled, 0) > 0 AND NOT COALESCE(sp.qc_any_flag, true) AS qc_clean
        FROM w
        LEFT JOIN terms pp ON pp.study_accession = w.study_accession AND pp.health_condition = w.hc
                          AND pp.health_condition_detail = w.hcd
        LEFT JOIN terms gg ON gg.study_accession = '' AND gg.health_condition = w.hc
                          AND gg.health_condition_detail = w.hcd
        LEFT JOIN terms st ON st.study_accession = '' AND st.health_condition = w.hc
                          AND st.health_condition_detail = '*'
        LEFT JOIN '{sp}' sp ON sp.sample_key = w.sample_key""")
    con.execute("CREATE TABLE cases AS SELECT * FROM s WHERE role='case' AND cid<>''")
    con.execute("CREATE TABLE cproj AS SELECT DISTINCT cid, study_accession FROM cases")
    return con


def term_fields(con) -> pd.DataFrame:
    """condition_name / efo_id / mondo_id / efo_legacy_ids per condition_id, from the label strings that carry it.

    condition_name: the name most label strings give.  efo_id: the value they agree on, else the condition_id when that
    is one of them, else blank.  mondo_id: the condition_id when it is a MONDO id; blank for a phenotype (HP) condition;
    otherwise the one MONDO id every label string of the condition carries, else blank.  efo_legacy_ids: the single
    non-blank value, else the first in sort order.
    """
    d = con.execute("""SELECT condition_id, condition_name, efo_id, mondo_id, efo_legacy_ids, COUNT(*) n
                       FROM terms WHERE condition_id<>'' GROUP BY 1,2,3,4,5""").df()
    out = []
    for cid, g in d.groupby("condition_id"):
        name = g.sort_values(["n", "condition_name"], ascending=[False, True]).condition_name.iloc[0]
        efo = set(g.efo_id)
        efo = efo.pop() if len(efo) == 1 else (cid if cid in efo else "")
        if cid.startswith("MONDO:"):
            mondo = cid
        elif cid.startswith("HP:"):
            mondo = ""
        else:
            mv = set(g.mondo_id)
            mondo = mv.pop() if (len(mv) == 1 and "" not in mv) else ""
        leg = sorted(v for v in set(g.efo_legacy_ids) if v)
        out.append(dict(condition_id=cid, condition_name=name, efo_id=efo, mondo_id=mondo,
                        efo_legacy_ids=leg[0] if leg else ""))
    return pd.DataFrame(out)


def label_strings(con) -> pd.DataFrame:
    """n_label_strings: distinct (health_condition, health_condition_detail) label strings mapped to the condition."""
    return con.execute("""SELECT condition_id, COUNT(*) AS n_label_strings FROM
        (SELECT DISTINCT condition_id, health_condition, health_condition_detail FROM terms
         WHERE condition_id<>'' AND health_condition_detail<>'*') GROUP BY 1""").df()


def counts(con) -> pd.DataFrame:
    age = ", ".join(f"SUM(CASE WHEN age_category='{b}' THEN 1 ELSE 0 END) AS cases_age_{b}" for b in AGE_BANDS)
    main = con.execute(f"""
        SELECT cid AS condition_id, COUNT(*) AS cases, COUNT(DISTINCT study_accession) AS projects,
               COUNT(DISTINCT person_key) AS case_people,
               SUM(CASE WHEN state='current' THEN 1 ELSE 0 END) AS cases_current,
               SUM(CASE WHEN state='treated_or_remission' THEN 1 ELSE 0 END) AS cases_treated_or_remission,
               SUM(CASE WHEN state='at_risk_or_pre' THEN 1 ELSE 0 END) AS cases_at_risk_or_pre,
               SUM(CASE WHEN state='history' THEN 1 ELSE 0 END) AS cases_history,
               COUNT(DISTINCT CASE WHEN country IS NOT NULL AND country<>'' THEN country END) AS n_countries,
               SUM(CASE WHEN country IS NULL OR country='' THEN 1 ELSE 0 END) AS cases_country_unknown,
               {age},
               SUM(CASE WHEN age_category IS NULL OR age_category NOT IN ({','.join(repr(b) for b in AGE_BANDS)})
                        THEN 1 ELSE 0 END) AS cases_age_unknown,
               SUM(CASE WHEN profiled THEN 1 ELSE 0 END) AS cases_profiled,
               SUM(CASE WHEN qc_clean THEN 1 ELSE 0 END) AS cases_profiled_qc_clean,
               SUM(CASE WHEN body_site_code='gut_stool' THEN 1 ELSE 0 END) AS cases_gut_stool
        FROM cases GROUP BY 1""").df()
    oc = con.execute("""SELECT cid AS condition_id, COUNT(*) AS other_control_samples_with_condition
                        FROM s WHERE role='other_control' AND cid<>'' GROUP BY 1""").df()
    hc = con.execute("""SELECT p.cid AS condition_id, COUNT(*) AS healthy_controls,
                               SUM(CASE WHEN h.profiled THEN 1 ELSE 0 END) AS healthy_controls_profiled,
                               COUNT(DISTINCT h.study_accession) AS projects_with_healthy_controls
                        FROM cproj p JOIN s h ON h.study_accession = p.study_accession AND h.role='healthy_control'
                        GROUP BY 1""").df()
    return (main.merge(oc, on="condition_id", how="left").merge(hc, on="condition_id", how="left")
                .merge(label_strings(con), on="condition_id", how="left"))


def lists(con) -> pd.DataFrame:
    ctry = con.execute("""SELECT cid AS condition_id, country, COUNT(*) n FROM cases
                          WHERE country IS NOT NULL AND country<>'' GROUP BY 1,2""").df()
    site = con.execute("""SELECT cid AS condition_id, COALESCE(NULLIF(body_site_code,''),'unknown') AS site, COUNT(*) n
                          FROM cases GROUP BY 1,2""").df()
    out = {}
    for cid, g in ctry.groupby("condition_id"):
        g = g.sort_values(["n", "country"], ascending=[False, True])
        out.setdefault(cid, {})["countries"] = ";".join(f"{r.country}:{r.n}" for r in g.itertuples())
    for cid, g in site.groupby("condition_id"):
        g = g.sort_values(["n", "site"], ascending=[False, True]).head(BODY_SITES_SHOWN)
        out.setdefault(cid, {})["body_sites"] = ";".join(f"{r.site}:{r.n}" for r in g.itertuples())
    return pd.DataFrame([dict(condition_id=k, **v) for k, v in out.items()])


def projects_table(con, names: dict, package: str) -> pd.DataFrame:
    st = os.path.join(package, "catalog_studies.parquet")
    base = con.execute(f"""
        WITH t AS (SELECT study_accession, short_title, study_title FROM '{st}' WHERE release_retired IS NULL),
             c AS (SELECT cid, study_accession, COUNT(*) cases,
                          SUM(CASE WHEN state='current' THEN 1 ELSE 0 END) cases_current,
                          COUNT(DISTINCT person_key) case_people,
                          SUM(CASE WHEN profiled THEN 1 ELSE 0 END) cases_profiled,
                          SUM(CASE WHEN qc_clean THEN 1 ELSE 0 END) cases_profiled_qc_clean
                   FROM cases GROUP BY 1,2),
             h AS (SELECT study_accession, COUNT(*) healthy_controls,
                          SUM(CASE WHEN profiled THEN 1 ELSE 0 END) healthy_controls_profiled,
                          COUNT(DISTINCT person_key) healthy_control_people
                   FROM s WHERE role='healthy_control' GROUP BY 1)
        SELECT c.cid AS condition_id, c.study_accession,
               COALESCE(NULLIF(t.short_title,''), t.study_title, '') AS project_title,
               c.cases, c.cases_current, c.case_people, c.cases_profiled, c.cases_profiled_qc_clean,
               COALESCE(h.healthy_controls,0) AS healthy_controls,
               COALESCE(h.healthy_controls_profiled,0) AS healthy_controls_profiled,
               COALESCE(h.healthy_control_people,0) AS healthy_control_people
        FROM c LEFT JOIN t ON t.study_accession=c.study_accession LEFT JOIN h ON h.study_accession=c.study_accession""").df()
    ctry = con.execute("""SELECT cid, study_accession, country FROM cases
                          WHERE country IS NOT NULL AND country<>'' GROUP BY 1,2,3""").df()
    cs = {k: ";".join(sorted(g.country)[:PROJ_COUNTRIES_SHOWN]) for k, g in ctry.groupby(["cid", "study_accession"])}
    age = con.execute("""SELECT cid, study_accession, COALESCE(NULLIF(age_category,''),'unknown') AS b, COUNT(*) n
                         FROM cases GROUP BY 1,2,3""").df()
    order = {b: i for i, b in enumerate(AGE_BANDS + ["unknown"])}
    ag = {k: ";".join(f"{r.b}:{r.n}" for r in g.assign(o=g.b.map(lambda x: order.get(x, 99)))
                                              .sort_values(["n", "o"], ascending=[False, True]).itertuples())
          for k, g in age.groupby(["cid", "study_accession"])}
    site = con.execute("""SELECT cid, study_accession, COALESCE(NULLIF(body_site_code,''),'unknown') AS site, COUNT(*) n
                          FROM cases GROUP BY 1,2,3""").df()
    bs = {k: ";".join(f"{r.site}:{r.n}" for r in g.sort_values(["n", "site"], ascending=[False, True])
                                                 .head(PROJ_SITES_SHOWN).itertuples())
          for k, g in site.groupby(["cid", "study_accession"])}
    key = list(zip(base.condition_id, base.study_accession))
    base["countries"] = [cs.get(k, "") for k in key]
    base["age_bands"] = [ag.get(k, "") for k in key]
    base["body_sites"] = [bs.get(k, "") for k in key]
    base["condition_name"] = base.condition_id.map(lambda c: names.get(c, ""))
    for c in ["cases", "cases_current", "case_people", "cases_profiled", "cases_profiled_qc_clean", "healthy_controls",
              "healthy_controls_profiled", "healthy_control_people"]:
        base[c] = pd.to_numeric(base[c], errors="coerce").fillna(0).astype(int)
    return base.sort_values(["condition_id", "cases", "study_accession"], ascending=[True, False, True])[PROJ_COLS]


def build(a) -> dict:
    con = connect(a.package, a.terms)
    static = pd.read_csv(os.path.join(a.static, "condition_explorer.csv"), dtype=str).fillna("")
    tf = term_fields(con)
    m = counts(con).merge(lists(con), on="condition_id", how="left").merge(tf, on="condition_id", how="left")
    keep = static[(static.replication_scope == "this condition") & (~static.condition_id.isin(m.condition_id))]
    if len(keep):                       # tested conditions with no case of their own keep a row with zero counts
        z = pd.DataFrame({"condition_id": keep.condition_id.values})
        for c in tf.columns:
            if c != "condition_id":
                z[c] = [static.set_index("condition_id")[c].get(x, "") if c in static.columns else "" for x in z.condition_id]
        m = pd.concat([m, z], ignore_index=True)
    m = m.merge(static[["condition_id"] + STATIC_COLS], on="condition_id", how="left")
    m["anchor"] = [x if isinstance(x, str) and x else str(c).replace(":", "_") for x, c in zip(m.anchor, m.condition_id)]
    for c in MAIN_COLS:
        if c not in m.columns:
            m[c] = ""
    m[INT_COLS] = m[INT_COLS].apply(pd.to_numeric, errors="coerce").fillna(0).astype(int)
    m[[c for c in MAIN_COLS if c not in INT_COLS]] = m[[c for c in MAIN_COLS if c not in INT_COLS]].fillna("")
    narrower = {r.condition_id: [x for x in str(r.narrower_condition_ids).split(";") if x] for r in m.itertuples()}
    cs_by_id = dict(zip(m.condition_id, m.cases))
    pr = con.execute("SELECT cid, study_accession FROM cproj").df()
    proj_by_id = {k: set(g.study_accession) for k, g in pr.groupby("cid")}
    m["cases_incl_narrower"] = [cs_by_id.get(c, 0) + sum(cs_by_id.get(x, 0) for x in narrower.get(c, [])) for c in m.condition_id]
    m["projects_incl_narrower"] = [len(set().union(proj_by_id.get(c, set()), *[proj_by_id.get(x, set()) for x in narrower.get(c, [])] or [set()]))
                                   for c in m.condition_id]
    m = m.sort_values(["cases", "condition_id"], ascending=[False, True])[MAIN_COLS]
    p = projects_table(con, dict(zip(m.condition_id, m.condition_name)), a.package)
    assoc = pd.read_csv(os.path.join(a.static, "condition_explorer_associations.csv"), dtype=str).fillna("")
    os.makedirs(a.out, exist_ok=True)
    m.to_csv(os.path.join(a.out, "condition_explorer.csv"), index=False)
    p.to_csv(os.path.join(a.out, "condition_explorer_projects.csv"), index=False)
    assoc.to_csv(os.path.join(a.out, "condition_explorer_associations.csv"), index=False)
    return dict(main=m, projects=p, assoc=assoc, static=static, con=con)


def check(m: pd.DataFrame, p: pd.DataFrame) -> list:
    """The spec's build checks. Returns the lines of the report; raises nothing."""
    out = []
    out.append(f"rows: {len(m)} conditions, {len(p)} condition-project rows")
    out.append(f"condition_id unique: {m.condition_id.is_unique}; anchor unique: {m.anchor.is_unique}")
    t = m[m.replication_scope == "this condition"]
    out.append(f"'this condition' rows: {len(t)} (expected 24); testable {int(t.replication_claims_testable.sum())}"
               f" (expected 1918); replicated {int(t.replication_claims_replicated.sum())} (expected 515)")
    out.append(f"sum of cases: {int(m.cases.sum())}; projects with a case: "
               f"{p.study_accession.nunique()}; case samples in the project table: {int(p.cases.sum())}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--terms", required=True)
    ap.add_argument("--static", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    r = build(a)
    print(f"wrote {a.out}/condition_explorer.csv ({len(r['main'])} rows), "
          f"condition_explorer_projects.csv ({len(r['projects'])} rows), "
          f"condition_explorer_associations.csv ({len(r['assoc'])} rows, copied from --static)")
    if a.check:
        for line in check(r["main"], r["projects"]):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
