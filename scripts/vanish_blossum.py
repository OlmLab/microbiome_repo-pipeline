#!/usr/bin/env python3
"""vanish_blossum.py - a statistical definition of VANISH and BloSSUM taxa (METHOD v2).

Implements projects/side_pages/track_a/METHOD_v2.md: prevalence of each taxon in non-industrialized
(NI) versus reference (REF) stool metagenomes, cohort as a random effect, depth as a covariate,
adults and infants separately; p-values from cohort-label permutations; leave-one-cohort-out for a
stable class; and, next to it, the owner's simple HDI-band rule (METHOD v2 section B).

Stages (--stage, default all):
  prep     stage 1, out of core: samples, groups, profile rows of the selected samples only
  obs      observed fits per analysis (full, null, SE, abundance model) -> _work/obs/
  perm     cohort-label permutations -> _work/perm/<analysis>__b<lo>-<hi>.npz, RESUMABLE
  classes  permutation p, BH q, candidates, leave-one-cohort-out, classes, calibration grid,
           shuffled-label check, Carter comparison, all CSV outputs (rerunnable on its own)
  simple   the simple rule of section B -> simple_results.csv, simple_analyses.csv
  all      prep, obs, perm, classes, simple

usage:
  python vanish_blossum.py --package DIR --sandpiper-dir DIR --hdi FILE --out DIR
     [--external-labels FILE] [--levels species,genus,family] [--cap 100] [--jobs 4]
     [--memory-limit 3GB] [--permutations 200] [--permutations-sens 100] [--perm-block 25]
     [--q 0.05] [--min-score 2] [--min-dprev 0.10] [--p-loco 0.01]
     [--carter-mapped FILE] [--carter-lists FILE] [--carter-cohort-check FILE]
     [--method FILE] [--stage all]

Every stage appends timestamped lines to <out>/progress.log. No R, no network. Deterministic.
"""
import argparse, gzip, hashlib, json, os, sys, time
import numpy as np
import pandas as pd

QC_FLAGS = ["qc_low_depth", "qc_non_metagenome", "qc_synthetic", "qc_rna",
            "qc_predicted_ecological", "qc_no_genus_assigned", "qc_low_complexity"]
NI_STRICT = ["hunter_gatherer", "indigenous_community", "pastoralist", "traditional_agriculturalist"]
NI_BROAD = NI_STRICT + ["rural_non_industrialized"]
EXT_NI = ["H-G", "Transitional"]
ADULT = ["adult", "elderly"]
INFANT = ["infant", "neonate"]
# contrast name -> (ni_def, reference); C1 is primary and carries the headline class (METHOD v2 A2)
CONTRASTS = [("C1", "ext", "hdi"), ("C2", "ext", "stated"),
             ("C3", "strict", "hdi"), ("C4", "broad", "hdi")]
PRIMARY = "C1"
PEN_SD = 2.5            # A3: penalty -b1^2 / (2 * 2.5^2)
S_LO, S_HI = 0.01, 12.0  # A3 (v2): bound on the cohort sd
S_STARTS = (0.05, 0.5, 1.5)
PERM_SEED = 20261113     # A4: rng seed base
MIN_CELL = 10           # v1 2.6c
MIN_COHORTS = 3         # v1 2.7
MIN_SAMPLES = 30
TAXON_MIN_SAMPLES = 10  # v1 3
TAXON_MIN_COHORTS = 3
TAXON_MIN_PREV = 0.05
ABUND_MIN_DET = 20      # v1 5
ABUND_MIN_COHORTS = 2
# section B
HDI_IND, HDI_NONIND = 0.80, 0.70
SIMPLE_MIN_SUBJECTS = 30
SIMPLE_PREV_HI, SIMPLE_PREV_LO = 0.20, 0.05
SIMPLE_Q = 0.05
SIMPLE_MIN_COUNTRIES = 2


def plog(out, msg):
    line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    with open(os.path.join(out, "progress.log"), "a") as fh:
        fh.write(line + "\n")
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def group_label(d, ni_def, reference):
    """NI / REF / None per sample (METHOD v2 A2). Returns (labels, counts of conflicts)."""
    stated_ni = {"strict": NI_STRICT, "broad": NI_BROAD, "ext": NI_STRICT}[ni_def]
    is_ni = d.lifestyle.isin(stated_ni)
    ext_ni = d.ext_label.isin(EXT_NI) if "ext_label" in d.columns else pd.Series(False, index=d.index)
    conflict = ext_ni & (d.lifestyle == "urban_industrialized")
    if ni_def in ("ext", "broad"):
        is_ni = is_ni | (ext_ni & ~conflict)
    if ni_def == "broad":
        is_ni = is_ni | d.lifestyle.isin(["rural_non_industrialized"])
    if reference == "stated":
        is_ref = d.lifestyle == "urban_industrialized"
    else:
        is_ref = d.hdi_high & (d.lifestyle.isna() | (d.lifestyle == "urban_industrialized"))
    g = pd.Series(pd.NA, index=d.index, dtype="object")
    g[is_ref] = "REF"
    g[is_ni] = "NI"          # an NI label wins; REF is "not in the NI group of the contrast"
    g[conflict] = pd.NA      # stated urban_industrialized with an external NI label: no group
    src = pd.Series("", index=d.index, dtype="object")
    src[is_ni & d.lifestyle.isin(stated_ni + (["rural_non_industrialized"] if ni_def == "broad" else []))] = "stated"
    src[is_ni & ext_ni & (src == "")] = "external"
    src[is_ni & ext_ni & (src == "stated")] = "both"
    return g, src, int(conflict.sum())


def md5key(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _con(work, memory_limit, threads=4):
    import duckdb
    con = duckdb.connect()
    con.execute("SET memory_limit=?", [memory_limit])
    con.execute("SET threads=?", [threads])
    con.execute("SET preserve_insertion_order=false")
    os.makedirs(os.path.join(work, "tmp"), exist_ok=True)
    con.execute("SET temp_directory=?", [os.path.join(work, "tmp")])
    return con


def load_samples(package, sandpiper_dir, hdi_path, work, memory_limit="3GB", external=None):
    """Stage 1a, out of core: one row per QC-clean gut_stool specimen with a profile (METHOD 2.1-2.4, 2.8).

    Reads only the needed columns of the package tables through DuckDB and writes _work/samples_clean.parquet.
    """
    con = _con(work, memory_limit)
    md = os.path.join(package, "catalog_sample_metadata_wide.parquet")
    stu = os.path.join(package, "catalog_studies.parquet")
    summ = os.path.join(package, "catalog_sandpiper_sample_summary.parquet")
    qc = " AND ".join("NOT coalesce(q.%s, false)" % f for f in QC_FLAGS)
    qccols = ", ".join(QC_FLAGS)
    out = os.path.join(work, "samples_clean.parquet")
    con.execute(f"""
        COPY (
          SELECT m.sample_key, m.study_accession,
                 coalesce(nullif(s.study_group_id, ''), m.study_accession) AS cohort,
                 m.age_category, m.lifestyle, m.country,
                 coalesce(nullif(m.person_key, ''), m.sample_key) AS person,
                 q.root_coverage_sum, md5(m.sample_key) AS order_key
          FROM read_parquet('{md}') m
          JOIN (SELECT sample_key, root_coverage_sum, {qccols} FROM read_parquet('{summ}')) q
               USING (sample_key)
          LEFT JOIN (SELECT study_accession, study_group_id FROM read_parquet('{stu}')
                     WHERE release_retired IS NULL) s USING (study_accession)
          WHERE m.release_retired IS NULL AND m.body_site_code = 'gut_stool'
            AND m.sample_type = 'specimen' AND {qc}
        ) TO '{out}' (FORMAT PARQUET)""")
    counts = {
        "n_current_samples": int(con.execute(
            f"SELECT count(*) FROM read_parquet('{md}') WHERE release_retired IS NULL").fetchone()[0]),
        "n_current_projects": int(con.execute(
            f"SELECT count(*) FROM read_parquet('{stu}') WHERE release_retired IS NULL").fetchone()[0]),
        "n_profiled": int(con.execute(f"SELECT count(*) FROM read_parquet('{summ}')").fetchone()[0]),
    }
    d = con.execute(f"SELECT * FROM read_parquet('{out}')").df()
    con.close()
    counts["n_qc_clean_gut_specimen"] = int(len(d))
    d["stratum"] = np.where(d.age_category.isin(ADULT), "adult",
                            np.where(d.age_category.isin(INFANT), "infant", None))
    d["depth_log10"] = np.log10(d.root_coverage_sum.astype(float))
    d["ext_label"] = pd.NA
    d["ext_source"] = pd.NA
    if external and os.path.exists(external):
        ex = pd.read_csv(external).drop_duplicates("sample_key")
        d = d.drop(columns=["ext_label", "ext_source"]).merge(
            ex[["sample_key", "ext_label", "ext_source"]], on="sample_key", how="left")
        counts["n_external_labels_file"] = int(len(ex))
        counts["n_external_labels_matched"] = int(d.ext_label.notna().sum())
    hdi = pd.read_csv(hdi_path)
    high = sorted(set(hdi.loc[hdi.band == "high", "iso2"].astype(str)))
    d["hdi_high"] = d.country.isin(high)
    counts["n_high_hdi_countries"] = len(high)
    return d.reset_index(drop=True), counts


def select(d, ni_def, reference, stratum, cap):
    """v1 2.5-2.8: one sample per person, cap per cohort-group, drop small cells, z of depth."""
    g, src, n_conflict = group_label(d, ni_def, reference)
    sub = d[g.notna()].copy()
    sub["group"] = g[g.notna()].values
    sub["label_src"] = src[g.notna()].values
    lost = {}
    for grp in ("NI", "REF"):
        m = sub.group == grp
        lost[grp] = {"n_all_ages": int(m.sum()),
                     "n_no_stratum": int((m & sub.stratum.isna()).sum())}
    sub = sub[sub.stratum == stratum].copy()
    if len(sub):
        sub = sub.sort_values("order_key").drop_duplicates(subset=["person"], keep="first")
        sub = sub.sort_values("order_key").groupby(["cohort", "group"], sort=False).head(cap)
        cell = sub.groupby(["cohort", "group"]).sample_key.transform("size")
        sub = sub[cell >= MIN_CELL].copy()
    counts = {}
    for grp in ("NI", "REF"):
        m = sub.group == grp
        counts[grp] = {"n": int(m.sum()), "cohorts": int(sub.loc[m, "cohort"].nunique())}
    counts["distinct_cohorts"] = int(sub.cohort.nunique()) if len(sub) else 0
    counts["n_conflict"] = n_conflict
    ok = all(counts[g_]["n"] >= MIN_SAMPLES and counts[g_]["cohorts"] >= MIN_COHORTS for g_ in ("NI", "REF"))
    if len(sub):
        sub = sub.sort_values("order_key").reset_index(drop=True)
        z = sub.depth_log10.values
        sd = z.std(ddof=0)
        sub["z"] = (z - z.mean()) / (sd if sd > 0 else 1.0)
        sub["ni"] = (sub.group == "NI").astype(float)
        sub["cidx"] = pd.factorize(sub.cohort, sort=True)[0]
    return sub, {"estimable": bool(ok), "counts": counts, "lost": lost, "n_conflict": n_conflict}


# ---------------------------------------------------------------- prevalence model (METHOD 4)
# logit P(detected_ij) = b0 + b1*NI_ij + b2*z_ij + u_j,  u_j ~ N(0, s^2).
# Marginal likelihood with u integrated out by the Laplace approximation; inner Newton for u
# (vectorised over cohorts with bincount), outer L-BFGS-B over (b0, b1, b2, log s) with the
# analytic gradient of the Laplace objective (the implicit derivative of u_hat is carried only
# through the -0.5*log H term, because u_hat is a stationary point of the rest).

def _expit(x):
    out = np.empty_like(x)
    pos = x >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    e = np.exp(x[~pos])
    out[~pos] = e / (1.0 + e)
    return out


def _inner_newton(eta, y, cidx, nco, s2, u, maxit=100, tol=1e-10):
    for _ in range(maxit):
        p = _expit(eta + u[cidx])
        g = np.bincount(cidx, y - p, minlength=nco) - u / s2
        w = p * (1.0 - p)
        H = np.bincount(cidx, w, minlength=nco) + 1.0 / s2
        step = g / H
        np.clip(step, -4.0, 4.0, out=step)
        u = u + step
        if np.max(np.abs(step)) < tol:
            break
    p = _expit(eta + u[cidx])
    w = p * (1.0 - p)
    H = np.bincount(cidx, w, minlength=nco) + 1.0 / s2
    return u, p, w, H


def _laplace(theta, y, X, cidx, nco, state, pen_sd, want_grad=True):
    """Negative penalised Laplace log-likelihood and its gradient. theta = [b0, b1, b2, log s]."""
    beta = theta[:3]
    ls = theta[3]
    s = np.exp(ls)
    s2 = s * s
    eta = X @ beta
    u = _inner_newton(eta, y, cidx, nco, s2, state["u"].copy())
    u, p, w, H = u
    state["u"] = u
    e = eta + u[cidx]
    ll = float(np.sum(y * e - np.logaddexp(0.0, e)))
    F = ll - float(np.sum(u * u)) / (2.0 * s2) - 0.5 * nco * np.log(s2) - 0.5 * float(np.sum(np.log(H)))
    F -= (beta[1] ** 2) / (2.0 * pen_sd ** 2)
    if not want_grad:
        return -F
    r = y - p
    wp = w * (1.0 - 2.0 * p)                      # dw/deta
    A = np.bincount(cidx, wp, minlength=nco)      # dH_j/du_j
    gb = np.empty(3)
    for k in range(3):
        xk = X[:, k]
        B = np.bincount(cidx, wp * xk, minlength=nco)     # explicit dH_j/db_k
        C = np.bincount(cidx, w * xk, minlength=nco)      # du_j/db_k = -C/H
        gb[k] = float(xk @ r) - 0.5 * float(np.sum((B - A * C / H) / H))
    gb[1] -= beta[1] / pen_sd ** 2
    su2 = float(np.sum(u * u))
    dF_ds = su2 / s ** 3 - nco / s - 0.5 * float(np.sum((-2.0 / s ** 3 + A * (2.0 * u / s ** 3) / H) / H))
    g = np.empty(4)
    g[:3] = gb
    g[3] = dF_ds * s
    return -F, -g


def fit_glmm(y, X, cidx, nco, pen_sd=PEN_SD, start=None, free_b1=True, se=True,
             s_starts=S_STARTS):
    """Maximise the penalised Laplace likelihood. Returns dict with beta, log s, value, se.

    Every fit is run from several starting values of the cohort sd and the best (highest penalised
    log-likelihood) is kept. Without this, a fit started where s is at the lower bound stays there:
    the gradient in log s vanishes as s -> 0, so L-BFGS-B cannot climb out, and a null fit (b1 = 0)
    warm-started from a full fit whose s sat at the bound returned too low a log-likelihood and
    inflated the likelihood-ratio statistic.
    """
    from scipy.optimize import minimize
    if start is None:
        pbar = min(max(y.mean(), 1e-4), 1 - 1e-4)
        start = np.array([np.log(pbar / (1 - pbar)), 0.0, 0.0, np.log(0.8)])
    start = np.asarray(start, dtype=float).copy()
    if not free_b1:
        start[1] = 0.0
    bounds = [(-30, 30), (-30, 30), (-30, 30), (np.log(S_LO), np.log(S_HI))]
    starts = [start]
    for s0 in s_starts:
        v = start.copy()
        v[3] = np.log(s0)
        starts.append(v)

    def run(t0):
        state = {"u": np.zeros(nco)}
        if free_b1:
            res = minimize(lambda t: _laplace(t, y, X, cidx, nco, state, pen_sd), t0, jac=True,
                           method="L-BFGS-B", bounds=bounds,
                           options={"maxiter": 400, "ftol": 1e-11, "gtol": 1e-7})
            return res, np.asarray(res.x, dtype=float), state
        idx = [0, 2, 3]

        def fun(t3):
            t = np.array([t3[0], 0.0, t3[1], t3[2]])
            v, gr = _laplace(t, y, X, cidx, nco, state, pen_sd)
            return v, gr[idx]
        res = minimize(fun, t0[idx], jac=True, method="L-BFGS-B", bounds=[bounds[i] for i in idx],
                       options={"maxiter": 400, "ftol": 1e-11, "gtol": 1e-7})
        return res, np.array([res.x[0], 0.0, res.x[1], res.x[2]]), state

    best = None
    for t0 in starts:
        res, theta, state = run(t0)
        if best is None or res.fun < best[0].fun:
            best = (res, theta, state)
    res, theta, state = best
    out = {"beta": theta[:3].copy(), "log_s": float(theta[3]), "s": float(np.exp(theta[3])),
           "value": float(-res.fun), "nit": int(res.nit), "ok": bool(res.success),
           "n_starts": len(starts)}
    if se and free_b1:
        st2 = {"u": state["u"].copy()}
        h = 1e-4
        Hm = np.zeros((4, 4))
        for k in range(4):
            tp = theta.copy(); tp[k] += h
            tm = theta.copy(); tm[k] -= h
            _, gp = _laplace(tp, y, X, cidx, nco, st2, pen_sd)
            _, gm = _laplace(tm, y, X, cidx, nco, st2, pen_sd)
            Hm[:, k] = (gp - gm) / (2 * h)
        Hm = 0.5 * (Hm + Hm.T)
        try:
            cov = np.linalg.inv(Hm)
            v = cov[1, 1]
            out["se_b1"] = float(np.sqrt(v)) if v > 0 else float("nan")
        except np.linalg.LinAlgError:
            out["se_b1"] = float("nan")
    return out


# ---------------------------------------------------------------- abundance model (METHOD 5)
# log10(relabund) = a0 + a1*NI + a2*z + v_j + e, v_j ~ N(0,t^2), e ~ N(0,r^2), ML.
# Closed-form profile likelihood: with lam = t^2/r^2, V_j^-1 = (1/r^2)(I - lam/(1+n_j lam) J),
# so every quantity follows from per-cohort sums of X and y.

def _lmm_profile(lam, X, yv, cidx, nco):
    n, k = X.shape
    nj = np.bincount(cidx, minlength=nco).astype(float)
    f = lam / (1.0 + nj * lam)
    Sx = np.zeros((nco, k))
    for c in range(k):
        Sx[:, c] = np.bincount(cidx, X[:, c], minlength=nco)
    Sy = np.bincount(cidx, yv, minlength=nco)
    XtX = X.T @ X - Sx.T @ (Sx * f[:, None])
    Xty = X.T @ yv - Sx.T @ (Sy * f)
    yty = float(yv @ yv) - float(np.sum(f * Sy * Sy))
    beta = np.linalg.solve(XtX, Xty)
    Q = yty - float(Xty @ beta)
    r2 = max(Q / n, 1e-300)
    ll = -0.5 * (n * np.log(2 * np.pi) + n * np.log(r2) + float(np.sum(np.log1p(nj * lam))) + n)
    return ll, beta, r2, XtX


def fit_lmm(X, yv, cidx, nco):
    from scipy.optimize import minimize_scalar
    f = lambda t: -_lmm_profile(np.exp(t), X, yv, cidx, nco)[0]
    best = None
    for t0 in (-6.0, -2.0, 0.0, 2.0):
        try:
            r = minimize_scalar(f, bracket=None, bounds=(t0 - 2.5, t0 + 2.5), method="bounded",
                                options={"xatol": 1e-6})
            if best is None or r.fun < best.fun:
                best = r
        except Exception:
            pass
    lam = float(np.exp(best.x))
    ll, beta, r2, XtX = _lmm_profile(lam, X, yv, cidx, nco)
    cov = np.linalg.inv(XtX) * r2
    return {"ll": float(ll), "beta": beta, "se": float(np.sqrt(max(cov[1, 1], 0.0))),
            "lam": lam, "r2": float(r2)}


# ---------------------------------------------------------------- taxa (METHOD 3)

def _f_element(lineage):
    for part in str(lineage).split(";"):
        part = part.strip()
        if part.startswith("f__"):
            return part
    return ""


def extract_profiles(sandpiper_dir, keys, levels, work, memory_limit="3GB"):
    """Stage 1b, out of core: profile rows of the selected samples only.

    Writes _work/<level>_long.parquet (sample_key, taxon, relabund) and _work/taxa_<level>.parquet
    (taxon, lineage). The multi-GB per-sample tables are never read into memory: DuckDB streams them
    with a semi-join on the selected sample keys.
    """
    con = _con(work, memory_limit)
    sel = pd.DataFrame({"sample_key": list(keys)})
    con.register("sel", sel)
    need_genus = ("genus" in levels) or ("family" in levels)
    files = {}
    for lev, fname, col in (("species", "gut_sandpiper_sample_species.parquet", "species"),
                            ("genus", "gut_sandpiper_sample_genus.parquet", "genus")):
        if lev == "genus" and not need_genus:
            continue
        if lev == "species" and "species" not in levels:
            continue
        src_f = os.path.join(sandpiper_dir, fname)
        lf = os.path.join(work, lev + "_long.parquet")
        con.execute(f"""
            COPY (SELECT p.sample_key, p.{col} AS taxon, CAST(p.relabund AS FLOAT) AS relabund
                  FROM read_parquet('{src_f}') p SEMI JOIN sel s ON p.sample_key = s.sample_key
                  WHERE p.{col} <> 'unassigned_at_{lev}'
                  ORDER BY p.sample_key) TO '{lf}' (FORMAT PARQUET)""")
        tf = os.path.join(work, "taxa_" + lev + ".parquet")
        con.execute(f"""
            COPY (SELECT p.{col} AS taxon, min(p.lineage) AS lineage
                  FROM read_parquet('{src_f}') p SEMI JOIN sel s ON p.sample_key = s.sample_key
                  WHERE p.{col} <> 'unassigned_at_{lev}' GROUP BY 1) TO '{tf}' (FORMAT PARQUET)""")
        files[lev] = (lf, tf)
    if "family" in levels:
        tg = pd.read_parquet(files["genus"][1])
        tg["family"] = [_f_element(l) for l in tg.lineage]
        fam = []
        for f, l in zip(tg.family, tg.lineage):
            parts = [x.strip() for x in str(l).split(";")]
            fam.append("; ".join(parts[: parts.index(f) + 1]) if f in parts else f)
        tg["family_lineage"] = fam
        tg = tg[tg.family.astype(str) != ""]
        con.register("fam_map", tg[["taxon", "family", "family_lineage"]])
        lf = os.path.join(work, "family_long.parquet")
        gl = files["genus"][0]
        con.execute(f"""
            COPY (SELECT g.sample_key, m.family AS taxon, CAST(sum(g.relabund) AS FLOAT) AS relabund
                  FROM read_parquet('{gl}') g JOIN fam_map m ON g.taxon = m.taxon
                  GROUP BY 1, 2 ORDER BY 1) TO '{lf}' (FORMAT PARQUET)""")
        tf = os.path.join(work, "taxa_family.parquet")
        con.execute(f"""COPY (SELECT DISTINCT family AS taxon, family_lineage AS lineage FROM fam_map)
                        TO '{tf}' (FORMAT PARQUET)""")
        files["family"] = (lf, tf)
    rowcounts = {lev: int(con.execute(f"SELECT count(*) FROM read_parquet('{files[lev][0]}')").fetchone()[0])
                 for lev in files}
    con.close()
    return {lev: files[lev] for lev in levels if lev in files}, rowcounts


def level_arrays(files, lev, keys, work, memory_limit="3GB"):
    """Sparse (sample position, taxon code, relabund) arrays for one level. No dense matrix."""
    con = _con(work, memory_limit)
    lf, tf = files[lev]
    tax = pd.read_parquet(tf).sort_values("taxon").reset_index(drop=True)
    tax["tcode"] = np.arange(len(tax), dtype=np.int32)
    sel = pd.DataFrame({"sample_key": list(keys), "upos": np.arange(len(keys), dtype=np.int32)})
    con.register("sel", sel)
    con.register("tax", tax[["taxon", "tcode"]])
    arr = con.execute(f"""
        SELECT s.upos AS pos, t.tcode AS code, p.relabund
        FROM read_parquet('{lf}') p JOIN sel s ON p.sample_key = s.sample_key
        JOIN tax t ON p.taxon = t.taxon""").df()
    con.close()
    return {"pos": arr.pos.values.astype(np.int32), "code": arr.code.values.astype(np.int32),
            "relabund": arr.relabund.values.astype(np.float64),
            "names": list(tax.taxon), "lineage": list(tax.lineage)}


def bh(p):
    p = np.asarray(p, dtype=float)
    ok = np.isfinite(p)
    q = np.full(p.shape, np.nan)
    pp = p[ok]
    n = pp.size
    if n == 0:
        return q
    o = np.argsort(pp)
    s = pp[o]
    # step-up: q_(i) = min over j >= i of (n / j) * p_(j), j the ascending rank 1..n
    ranked = s * n / np.arange(1, n + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[o] = np.minimum(adj, 1.0)
    q[ok] = out
    return q


def _zs(v):
    sd = v.std(ddof=0)
    return (v - v.mean()) / (sd if sd > 0 else 1.0)


def analyse_taxon(rows, ra, n, ni, dlog, cidx, nco, do_abund=True):
    """Full fit of one taxon in one contrast-stratum (METHOD 4 and 5)."""
    from scipy.stats import chi2
    y = np.zeros(n)
    y[rows] = 1.0
    X = np.column_stack([np.ones(n), ni, _zs(dlog)])
    free = fit_glmm(y, X, cidx, nco)
    start = np.concatenate([free["beta"], [free["log_s"]]])
    null = fit_glmm(y, X, cidx, nco, start=start, free_b1=False, se=False)
    T = max(2.0 * (free["value"] - null["value"]), 0.0)
    p = float(chi2.sf(T, 1))
    r = {"ll_full": float(free["value"]), "ll_null": float(null["value"]),
         "b1": float(free["beta"][1]), "b0": float(free["beta"][0]), "b2": float(free["beta"][2]),
         "sd_cohort": free["s"], "se_b1": float(free.get("se_b1", np.nan)), "lr": float(T), "p": p,
         "theta": start.tolist()}
    r.update({"abund_a1": np.nan, "abund_se": np.nan, "abund_p": np.nan})
    if do_abund and len(rows) >= 2 * ABUND_MIN_DET:
        nir = ni[rows]
        cr, uq = pd.factorize(cidx[rows], sort=True)
        okg = True
        for g in (0.0, 1.0):
            m = nir == g
            if m.sum() < ABUND_MIN_DET or len(np.unique(cr[m])) < ABUND_MIN_COHORTS:
                okg = False
        if okg:
            X2 = np.column_stack([np.ones(len(rows)), nir, _zs(dlog[rows])])
            yv = np.log10(np.maximum(ra, 1e-12))
            m1 = fit_lmm(X2, yv, cr, len(uq))
            m0 = fit_lmm(X2[:, [0, 2]], yv, cr, len(uq))
            r["abund_a1"] = float(m1["beta"][1])
            r["abund_se"] = float(m1["se"])
            r["abund_p"] = float(chi2.sf(max(2 * (m1["ll"] - m0["ll"]), 0.0), 1))
    return r


def loco_taxon(rows, n, ni, dlog, cidx, nco, leave, start=None):
    """Refit with each cohort in `leave` left out; z recomputed (METHOD 7)."""
    from scipy.stats import chi2
    y = np.zeros(n)
    y[rows] = 1.0
    res = []
    for c in leave:
        m = cidx != c
        if m.sum() < MIN_SAMPLES:
            continue
        cc, uq = pd.factorize(cidx[m], sort=True)
        yy = y[m]
        if yy.sum() == 0 or yy.sum() == yy.size:
            res.append({"cohort": int(c), "b1": np.nan, "p": 1.0})
            continue
        X = np.column_stack([np.ones(m.sum()), ni[m], _zs(dlog[m])])
        f = fit_glmm(yy, X, cc, len(uq), start=start, se=False)
        s2 = np.concatenate([f["beta"], [f["log_s"]]])
        f0 = fit_glmm(yy, X, cc, len(uq), start=s2, free_b1=False, se=False)
        T = max(2.0 * (f["value"] - f0["value"]), 0.0)
        res.append({"cohort": int(c), "b1": float(f["beta"][1]), "p": float(chi2.sf(T, 1))})
    return res


def _chunk_full(tasks, n, ni, dlog, cidx, nco):
    return [(t, analyse_taxon(rows, ra, n, ni, dlog, cidx, nco)) for t, rows, ra in tasks]


def _chunk_loco(tasks, n, ni, dlog, cidx, nco, leave):
    return [(t, loco_taxon(rows, n, ni, dlog, cidx, nco, leave, start=start)) for t, rows, start in tasks]


def _par(jobs):
    from joblib import Parallel, delayed
    return Parallel(n_jobs=jobs, backend="loky", verbose=0), delayed


def taxon_stats(sub, L, n_union):
    """Per-taxon and per-cohort counts for one contrast-stratum and level (METHOD 3 and 6)."""
    n = len(sub)
    cidx = sub.cidx.values.astype(np.int64)
    nco = int(cidx.max()) + 1
    ni = sub.ni.values
    local = np.full(n_union, -1, dtype=np.int64)
    local[sub.upos.values] = np.arange(n)
    lp = local[L["pos"]]
    m = lp >= 0
    rowpos = lp[m]
    code = L["code"][m].astype(np.int64)
    ra = L["relabund"][m]
    ntax = len(L["names"])
    gr = ni[rowpos]
    det, ncell, stats = {}, {}, {}
    for gname, gval in (("ni", 1.0), ("ref", 0.0)):
        sel = gr == gval
        det[gname] = np.bincount(code[sel] * nco + cidx[rowpos[sel]],
                                 minlength=ntax * nco).reshape(ntax, nco).astype(float)
        ncell[gname] = np.bincount(cidx[ni == gval], minlength=nco).astype(float)
    for gname in ("ni", "ref"):
        cells = ncell[gname] > 0
        d = det[gname]
        stats["n_" + gname] = int(ncell[gname].sum())
        stats["cohorts_" + gname] = int(cells.sum())
        stats["ndet_" + gname] = d.sum(axis=1)
        stats["cohorts_det_" + gname] = (d > 0).sum(axis=1)
        pc = np.zeros_like(d)
        pc[:, cells] = d[:, cells] / ncell[gname][cells]
        stats["prev_pooled_" + gname] = stats["ndet_" + gname] / max(ncell[gname].sum(), 1)
        stats["prev_cmean_" + gname] = pc[:, cells].mean(axis=1) if cells.sum() else np.zeros(ntax)
    stats["det"] = det
    stats["ncell"] = ncell
    # "in at least 3 cohorts" counts distinct cohorts: a cohort holding both NI and REF samples
    # is one cohort, not two (see AMBIGUITIES.md item 16).
    stats["cohorts_det"] = ((det["ni"] + det["ref"]) > 0).sum(axis=1)
    stats["tested"] = ((stats["ndet_ni"] + stats["ndet_ref"]) >= TAXON_MIN_SAMPLES) & \
        (stats["cohorts_det"] >= TAXON_MIN_COHORTS) & \
        (np.maximum(stats["prev_pooled_ni"], stats["prev_pooled_ref"]) >= TAXON_MIN_PREV)
    order = np.argsort(code, kind="stable")
    stats["_rows"] = rowpos[order]
    stats["_ra"] = ra[order]
    stats["_start"] = np.searchsorted(code[order], np.arange(ntax + 1))
    stats["nco"] = nco
    return stats



# ---------------------------------------------------------------- analysis plumbing

def ana_key(level, cname, stratum):
    return "%s__%s__%s" % (level, cname, stratum)


def _chunk_perm(tasks, X0, labmat, cidx, nco):
    """One block of permutations for a set of taxa. Returns (taxon, T row, b1 row)."""
    out = []
    nb = labmat.shape[0]
    for t, rows, theta, ll_null in tasks:
        y = np.zeros(X0.shape[0])
        y[rows] = 1.0
        T = np.empty(nb)
        B1 = np.empty(nb)
        X = X0.copy()
        for b in range(nb):
            X[:, 1] = labmat[b]
            f = fit_glmm(y, X, cidx, nco, start=theta, se=False)
            T[b] = max(2.0 * (f["value"] - ll_null), 0.0)
            B1[b] = f["beta"][1]
        out.append((t, T, B1))
    return out


def perm_labels(sub, nperm, seed=PERM_SEED):
    """A4: cohort label by majority (ties NI), cohorts sorted by id; permutation b uses seed + b."""
    cidx = sub.cidx.values.astype(np.int64)
    nco = int(cidx.max()) + 1
    ni = sub.ni.values
    share = np.bincount(cidx, ni, minlength=nco) / np.maximum(np.bincount(cidx, minlength=nco), 1)
    lab = (share >= 0.5).astype(float)          # ties count as NI
    out = np.empty((nperm, nco))
    for b in range(1, nperm + 1):
        pi = np.random.default_rng(seed + b).permutation(nco)
        out[b - 1] = lab[pi]
    return lab, out


def observed_stage(sub, L, n_union, jobs, key, work, out):
    """Observed fits of one level x contrast x stratum (A3), cached in _work/obs/."""
    f_obs = os.path.join(work, "obs", key + ".parquet")
    f_cnt = os.path.join(work, "obs", key + "_counts.npz")
    st = taxon_stats(sub, L, n_union)
    if os.path.exists(f_obs) and os.path.exists(f_cnt):
        return pd.read_parquet(f_obs), st
    t0 = time.time()
    n = len(sub)
    ni = sub.ni.values
    dlog = sub.depth_log10.values
    cidx = sub.cidx.values.astype(np.int64)
    nco = st["nco"]
    idx = np.where(st["tested"])[0]
    tasks = [(int(t), st["_rows"][st["_start"][t]:st["_start"][t + 1]],
              st["_ra"][st["_start"][t]:st["_start"][t + 1]]) for t in idx]
    par, delayed = _par(jobs)
    nch = max(1, min(len(tasks), jobs * 8))
    chunks = [c for c in (tasks[i::nch] for i in range(nch)) if c]
    fits = {}
    for part in par(delayed(_chunk_full)(c, n, ni, dlog, cidx, nco) for c in chunks):
        for t, r in part:
            fits[t] = r
    rows = []
    for t in idx:
        t = int(t)
        f = fits[t]
        rows.append(dict(code=t, taxon=L["names"][t], lineage=L["lineage"][t],
                         n_ni=st["n_ni"], n_ref=st["n_ref"], cohorts_ni=st["cohorts_ni"],
                         cohorts_ref=st["cohorts_ref"],
                         cohorts_ni_detected=int(st["cohorts_det_ni"][t]),
                         cohorts_ref_detected=int(st["cohorts_det_ref"][t]),
                         prev_ni_pooled=float(st["prev_pooled_ni"][t]),
                         prev_ref_pooled=float(st["prev_pooled_ref"][t]),
                         prev_ni_cohort_mean=float(st["prev_cmean_ni"][t]),
                         prev_ref_cohort_mean=float(st["prev_cmean_ref"][t]),
                         b1=f["b1"], score_log2or=f["b1"] / np.log(2.0),
                         se_log2or=(f["se_b1"] / np.log(2.0)) if np.isfinite(f["se_b1"]) else np.nan,
                         lr_stat=f["lr"], p_lrt=f["p"], sd_cohort=f["sd_cohort"], b2_depth=f["b2"],
                         ll_null=f["ll_null"], ll_full=f["ll_full"],
                         abund_a1_log10=f["abund_a1"], abund_se=f["abund_se"], abund_p=f["abund_p"],
                         n_pos_ni=int(st["ndet_ni"][t]), n_pos_ref=int(st["ndet_ref"][t]),
                         theta0=f["theta"][0], theta1=f["theta"][1], theta2=f["theta"][2],
                         theta3=f["theta"][3]))
    obs = pd.DataFrame(rows)
    obs["abund_q"] = bh(obs.abund_p.values) if len(obs) else []
    os.makedirs(os.path.dirname(f_obs), exist_ok=True)
    obs.to_parquet(f_obs, index=False)
    det_tot = (st["det"]["ni"] + st["det"]["ref"])[idx].astype(np.int32) if len(idx) else np.zeros((0, nco), np.int32)
    np.savez_compressed(f_cnt, codes=idx.astype(np.int32),
                        ncell_tot=(st["ncell"]["ni"] + st["ncell"]["ref"]).astype(np.int32),
                        det_tot=det_tot, cohorts=np.array(
                            sub.drop_duplicates("cidx").sort_values("cidx").cohort.astype(str).values))
    plog(out, "obs %s: %d tested, %.0f s" % (key, len(idx), time.time() - t0))
    return obs, st


def perm_stage(sub, obs, st, jobs, key, work, out, nperm, block):
    """A4: cohort-label permutations, resumable in blocks of `block` permutations."""
    d = os.path.join(work, "perm")
    os.makedirs(d, exist_ok=True)
    n = len(sub)
    cidx = sub.cidx.values.astype(np.int64)
    nco = int(cidx.max()) + 1
    X0 = np.column_stack([np.ones(n), sub.ni.values, _zs(sub.depth_log10.values)])
    lab, perms = perm_labels(sub, nperm)
    codes = obs.code.values
    for lo in range(0, nperm, block):
        hi = min(lo + block, nperm)
        f = os.path.join(d, "%s__b%d-%d.npz" % (key, lo + 1, hi))
        if os.path.exists(f):
            try:
                z = np.load(f)
                if z["codes"].size == codes.size and np.array_equal(z["codes"], codes):
                    continue
            except Exception:
                pass
        t0 = time.time()
        labmat = perms[lo:hi][:, cidx]
        tasks = [(int(r.code), st["_rows"][st["_start"][int(r.code)]:st["_start"][int(r.code) + 1]],
                  np.array([r.theta0, r.theta1, r.theta2, r.theta3]), float(r.ll_null))
                 for r in obs.itertuples()]
        par, delayed = _par(jobs)
        nch = max(1, min(len(tasks), jobs * 4))
        chunks = [c for c in (tasks[i::nch] for i in range(nch)) if c]
        res = {}
        for part in par(delayed(_chunk_perm)(c, X0, labmat, cidx, nco) for c in chunks):
            for t, T, B1 in part:
                res[t] = (T, B1)
        T = np.vstack([res[int(c)][0] for c in codes])
        B1 = np.vstack([res[int(c)][1] for c in codes])
        np.savez_compressed(f, codes=codes.astype(np.int32), T=T.astype(np.float32),
                            b1=B1.astype(np.float32), lo=lo + 1, hi=hi)
        plog(out, "perm %s b%d-%d: %d taxa, %.0f s (%.2f s per permutation)"
             % (key, lo + 1, hi, len(codes), time.time() - t0, (time.time() - t0) / (hi - lo)))


def load_perm(key, work, nperm, codes):
    """Stack the stored permutation blocks; returns (T, b1) or None when incomplete."""
    d = os.path.join(work, "perm")
    Ts, Bs, got = [], [], 0
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not f.startswith(key + "__b") or not f.endswith(".npz"):
            continue
        z = np.load(os.path.join(d, f))
        if not np.array_equal(z["codes"], codes.astype(np.int32)):
            continue
        Ts.append((int(z["lo"]), z["T"]))
        Bs.append((int(z["lo"]), z["b1"]))
    if not Ts:
        return None, None, 0
    Ts.sort(key=lambda x: x[0])
    Bs.sort(key=lambda x: x[0])
    T = np.hstack([x[1] for x in Ts])
    B1 = np.hstack([x[1] for x in Bs])
    return T, B1, T.shape[1]



# ---------------------------------------------------------------- A5-A7: classes, calibration

def _cand_mask(q, score, dprev, Q, S, D):
    return (q < Q) & (np.abs(score) >= S) & (np.sign(dprev) == np.sign(score)) & (np.abs(dprev) >= D)


def _stable_from_loco(lc, sign, S, P):
    if not lc:
        return False
    b1 = np.array([x["b1"] for x in lc], dtype=float)
    pp = np.array([x["p"] for x in lc], dtype=float)
    if not np.all(np.isfinite(b1)):
        return False
    return bool(np.all(np.sign(b1) == sign) and np.all(np.abs(b1) / np.log(2.0) >= S)
                and np.all(pp <= P))


def _stil_pre_mask(cand, prev_ni, prev_ref, score, se, Pst, E):
    """METHOD v2 D1 a-c: not a VANISH/BloSSUM candidate, common in both groups, score interval inside (-E, +E)."""
    with np.errstate(invalid="ignore"):
        common = (prev_ni >= Pst) & (prev_ref >= Pst)
        tight = np.isfinite(se) & ((score + 1.96 * se) < E) & ((score - 1.96 * se) > -E)
    return (~np.asarray(cand)) & common & tight


def _stil_from_loco(lc, E):
    """METHOD v2 D1d: in every leave-one-cohort-out refit the score stays inside (-E, +E)."""
    if not lc:
        return False
    b1 = np.array([x["b1"] for x in lc], dtype=float)
    if not np.all(np.isfinite(b1)):
        return False
    return bool(np.all(np.abs(b1) / np.log(2.0) < E))


def loco_for(codes, sub, st, obs, jobs, key, work, ni_override=None, cache=True):
    """Leave-one-cohort-out refits (A5) for a set of taxa, cached per analysis in _work/loco/."""
    f = os.path.join(work, "loco", key + ".json")
    os.makedirs(os.path.dirname(f), exist_ok=True)
    have = {}
    if cache and os.path.exists(f):
        have = {int(k): v for k, v in json.load(open(f)).items()}
    need = [int(c) for c in codes if int(c) not in have]
    if need:
        n = len(sub)
        ni = sub.ni.values if ni_override is None else ni_override
        dlog = sub.depth_log10.values
        cidx = sub.cidx.values.astype(np.int64)
        nco = st["nco"]
        ref_coh = np.unique(cidx[ni == 0.0])
        leave = sorted(set(int(c) for c in list(np.unique(cidx[ni == 1.0])) +
                           (list(ref_coh) if len(ref_coh) <= 30 else [])))
        th = {int(r.code): np.array([r.theta0, r.theta1, r.theta2, r.theta3]) for r in obs.itertuples()}
        tasks = [(t, st["_rows"][st["_start"][t]:st["_start"][t + 1]], th.get(t)) for t in need]
        par, delayed = _par(jobs)
        nch = max(1, min(len(tasks), jobs * 4))
        chunks = [c for c in (tasks[i::nch] for i in range(nch)) if c]
        for part in par(delayed(_chunk_loco)(c, n, ni, dlog, cidx, nco, leave) for c in chunks):
            for t, r in part:
                have[int(t)] = r
        if cache:
            json.dump({str(k): v for k, v in have.items()}, open(f, "w"))
    return have, len(need)


def shuffled_label_check(sub, st, obs, T, B1, cnt, jobs, key, work, Q, S, D, P, nmax=20):
    """A7: treat each of the first `nmax` permutations as if it were the data."""
    B = T.shape[1]
    if B < 2:
        return []
    lab, perms = perm_labels(sub, B)
    cidx = sub.cidx.values.astype(np.int64)
    ncell = cnt["ncell_tot"].astype(float)
    det = cnt["det_tot"].astype(float)
    rows = []
    for bstar in range(min(nmax, B)):
        Ti = T[:, bstar]
        other = np.delete(T, bstar, axis=1)
        ne = (other >= Ti[:, None]).sum(axis=1)
        p = (1.0 + ne) / (1.0 + other.shape[1])
        q = bh(p)
        score = B1[:, bstar] / np.log(2.0)
        lb = perms[bstar]
        pm = {}
        for g, v in (("ni", 1.0), ("ref", 0.0)):
            m = (lb == v) & (ncell > 0)
            pm[g] = (det[:, m] / ncell[m]).mean(axis=1) if m.sum() else np.zeros(det.shape[0])
        dprev = pm["ni"] - pm["ref"]
        cand = _cand_mask(q, score, dprev, Q, S, D)
        nst = 0
        if cand.any():
            nip = lb[cidx]
            have, _ = loco_for(obs.code.values[cand], sub, st, obs, jobs, key, work,
                               ni_override=nip, cache=False)
            for i in np.where(cand)[0]:
                nst += int(_stable_from_loco(have.get(int(obs.code.values[i])), np.sign(score[i]), S, P))
        rows.append(dict(permutation=bstar + 1, n_tested=int(len(p)), n_q05=int((q < Q).sum()),
                         n_candidates=int(cand.sum()), n_stable=nst))
    return rows


CALIB_GRID = [("default", {}), ("S_min=1.585", {"S": 1.585}), ("S_min=1.0", {"S": 1.0}),
              ("D_min=0.05", {"D": 0.05}), ("P_loco=0.05", {"P": 0.05}), ("Q=0.10", {"Q": 0.10})]


def calibration(obs, q, score, dprev, sub, st, jobs, key, work, base, carter_sets):
    """A6: the default thresholds and each single relaxation, with agreement with Carter's lists."""
    Q0, S0, D0, P0 = base
    rows = []
    for name, ch in CALIB_GRID:
        Q, S, D, P = ch.get("Q", Q0), ch.get("S", S0), ch.get("D", D0), ch.get("P", P0)
        cand = _cand_mask(q, score, dprev, Q, S, D)
        have, _ = loco_for(obs.code.values[cand], sub, st, obs, jobs, key, work)
        cls = []
        for i in np.where(cand)[0]:
            sg = np.sign(score[i])
            cls.append("VANISH" if (_stable_from_loco(have.get(int(obs.code.values[i])), sg, S, P) and sg > 0)
                       else ("BloSSUM" if (_stable_from_loco(have.get(int(obs.code.values[i])), sg, S, P) and sg < 0)
                             else ""))
        taxa = obs.taxon.values[cand]
        v = set(taxa[[c == "VANISH" for c in cls]])
        b = set(taxa[[c == "BloSSUM" for c in cls]])
        rec = dict(setting=name, Q=Q, S_min=S, D_min=D, P_loco=P, n_candidates=int(cand.sum()),
                   n_vanish=len(v), n_blossum=len(b))
        for lst, s_ in (("VANISH", carter_sets.get("VANISH", set())),
                        ("BloSSUM", carter_sets.get("BloSSUM", set()))):
            ours = v if lst == "VANISH" else b
            rec["n_%s_in_carter" % lst.lower()] = len(ours & s_)
            rec["share_%s_in_carter" % lst.lower()] = round(len(ours & s_) / len(ours), 4) if ours else float("nan")
            rec["n_carter_%s_recovered" % lst.lower()] = len(ours & s_)
            rec["n_carter_%s_mapped" % lst.lower()] = len(s_)
        rows.append(rec)
    return rows
def carter_context(carter_lists, cohort_check):
    """Recounts that frame the Carter comparison: what can be mapped, and cohort overlap."""
    out = {}
    if carter_lists and os.path.exists(carter_lists):
        cl = pd.read_csv(carter_lists)
        nm = cl.species_name_as_printed.astype(str).str.replace("^s__", "", regex=True).str.strip()
        ph = nm.str.endswith(" sp.")
        out["rows_by_list"] = {k: int(v) for k, v in cl.groupby("list").size().items()}
        out["rows_without_species_name"] = {k: int(v) for k, v in cl.assign(ph=ph).groupby("list").ph.sum().items()}
        out["rows_mappable_at_all"] = {k: int(out["rows_by_list"][k] - out["rows_without_species_name"][k])
                                       for k in out["rows_by_list"]}
    if cohort_check and os.path.exists(cohort_check):
        cc = pd.read_csv(cohort_check)
        lif = cc.carter_lifestyles.fillna("")
        ni = cc[lif.str.contains("H-G") | lif.str.contains("Transitional")].copy()
        strict = ni.catalog_labels.fillna("").apply(
            lambda v: any(x.strip() in NI_STRICT for x in str(v).split(";")))
        out["carter_cohorts"] = {"n_non_industrial_bioprojects": int(len(ni)),
                                 "in_catalog": int(ni.in_catalog.sum()),
                                 "in_ni_strict_group": int(strict.sum()),
                                 "not_in_ni_strict_group": int((~strict).sum()),
                                 "no_lifestyle_label": int(ni.catalog_labels.isna().sum())}
    return out


def _style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"figure.autolayout": False, "font.size": 8, "axes.titlesize": 8,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "legend.frameon": False, "savefig.dpi": 300, "figure.dpi": 100})
    return plt


def _save(fig, out, name):
    fig.savefig(os.path.join(out, name + ".png"), dpi=300, bbox_inches="tight")
    fig.savefig(os.path.join(out, name + "_web.png"), dpi=130, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)


def fig_small_multiples(cp, res, out, name, title):
    """Per-cohort prevalence for the 20 highest and 20 lowest scoring species (linear axes)."""
    plt = _style()
    top = res.nlargest(20, "score_log2or")
    bot = res.nsmallest(20, "score_log2or")
    sel = pd.concat([top, bot])
    fig, axes = plt.subplots(8, 5, figsize=(11.5, 15), sharey=True)
    rng = np.random.default_rng(7)
    for ax, (_, row) in zip(axes.ravel(), sel.iterrows()):
        c = cp[cp.taxon == row.taxon]
        for gi, g in enumerate(("NI", "REF")):
            cg = c[c.group == g]
            if not len(cg):
                continue
            x = gi + rng.uniform(-0.17, 0.17, len(cg))
            ax.scatter(x, cg.n_detected / cg.n, s=np.clip(cg.n.values * 0.25, 3, 45),
                       color=("#b2182b" if g == "NI" else "#2166ac"), alpha=0.55,
                       edgecolor="none", zorder=3)
        ax.set_xlim(-0.6, 1.6)
        ax.set_ylim(-0.05, 1.05)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["NI", "REF"])
        ax.set_title("%s\nscore %+.1f" % (row.taxon.replace("s__", "")[:30], row.score_log2or), fontsize=7)
        ax.grid(axis="y", color="#eeeeee", zorder=0)
    for ax in axes.ravel()[len(sel):]:
        ax.set_visible(False)
    for ax in axes[:, 0]:
        ax.set_ylabel("prevalence")
    fig.suptitle(title, y=0.995, fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    _save(fig, out, name)


def fig_score_agreement(res, out, name):
    plt = _style()
    a = res[(res.level == "species") & (res.ni_def == "strict") & (res.stratum == "adult")]
    s1 = a[a.reference == "stated"].set_index("taxon").score_log2or
    s2 = a[a.reference == "hdi"].set_index("taxon").score_log2or
    j = pd.concat([s1.rename("stated"), s2.rename("hdi")], axis=1).dropna()
    fig, ax = plt.subplots(figsize=(4.2, 4.2))
    ax.axhline(0, color="#bbbbbb", lw=0.6)
    ax.axvline(0, color="#bbbbbb", lw=0.6)
    lim = max(1.0, np.ceil(max(j.stated.abs().max(), j.hdi.abs().max())))
    ax.plot([-lim, lim], [-lim, lim], color="#dddddd", lw=0.8, zorder=1)
    ax.scatter(j.stated, j.hdi, s=10, alpha=0.6, color="#4d4d4d", edgecolor="none", zorder=2)
    ax.set_xlabel("score, reference = stated urban industrialized")
    ax.set_ylabel("score, reference = high-HDI countries")
    ax.set_title("Species scores under the two references\n(n = %d tested in both, r = %.2f)"
                 % (len(j), j.stated.corr(j.hdi)))
    _save(fig, out, name)




# ---------------------------------------------------------------- B: the simple rule

def simple_stage(package, sandpiper_dir, hdi_path, levels, work, out, memory_limit="3GB",
                 stil_min_prev=0.20, stil_max_score=1.0):
    """METHOD v2 section B: subject-level carriage by HDI band, Fisher's exact, out of core."""
    from scipy.stats import fisher_exact
    t0 = time.time()
    con = _con(work, memory_limit)
    sc = os.path.join(work, "samples_clean.parquet")
    hdi = pd.read_csv(hdi_path)
    hdi = hdi[hdi.hdi_2023.notna()].copy()   # a country with no HDI value has no band: its subjects are excluded
    hdi["band3"] = np.where(hdi.hdi_2023 >= HDI_IND, "ind",
                            np.where(hdi.hdi_2023 < HDI_NONIND, "nonind", "trans"))
    con.register("hdi", hdi[["iso2", "band3"]])
    # subject = person; stratum; country = most frequent (ties first alphabetically)
    con.execute(f"""CREATE TEMP TABLE subj AS
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
    con.execute(f"""CREATE TEMP TABLE smp AS
        SELECT s.sample_key, s.person, u.stratum, u.country, u.band
        FROM read_parquet('{sc}') s JOIN subj u ON u.person = s.person
        WHERE (u.stratum = 'adult' AND s.age_category IN ('adult','elderly'))
           OR (u.stratum = 'infant' AND s.age_category IN ('infant','neonate'))""")
    nsub = con.execute("SELECT stratum, band, count(*) n FROM subj GROUP BY 1,2").df()
    ncty = con.execute("SELECT stratum, country, count(*) n FROM subj GROUP BY 1,2").df()
    rows, aro = [], []
    for lev in levels:
        src, col = ({"species": ("gut_sandpiper_sample_species.parquet", "species"),
                     "genus": ("gut_sandpiper_sample_genus.parquet", "genus"),
                     "family": ("gut_sandpiper_sample_genus.parquet", "genus")}[lev])
        f = os.path.join(sandpiper_dir, src)
        if lev == "family":
            tgf = os.path.join(work, "taxa_genus_full.parquet")
            if not os.path.exists(tgf):
                gsrc = os.path.join(sandpiper_dir, "gut_sandpiper_sample_genus.parquet")
                con.execute(f"""COPY (SELECT genus AS taxon, min(lineage) AS lineage
                                      FROM read_parquet('{gsrc}') WHERE genus <> 'unassigned_at_genus'
                                      GROUP BY 1) TO '{tgf}' (FORMAT PARQUET)""")
            tg = pd.read_parquet(tgf)
            tg["family"] = [_f_element(l) for l in tg.lineage]
            fam = []
            for ff, l in zip(tg.family, tg.lineage):
                parts = [x.strip() for x in str(l).split(";")]
                fam.append("; ".join(parts[: parts.index(ff) + 1]) if ff in parts else ff)
            tg["family_lineage"] = fam
            con.register("fam_map", tg[tg.family.astype(str) != ""][["taxon", "family", "family_lineage"]])
            sel = ("SELECT m.family AS taxon, s.person, s.stratum, s.band, s.country "
                   f"FROM read_parquet('{f}') p JOIN smp s ON s.sample_key = p.sample_key "
                   f"JOIN fam_map m ON m.taxon = p.{col} WHERE p.relabund > 0")
        else:
            sel = (f"SELECT p.{col} AS taxon, s.person, s.stratum, s.band, s.country "
                   f"FROM read_parquet('{f}') p JOIN smp s ON s.sample_key = p.sample_key "
                   f"WHERE p.relabund > 0 AND p.{col} <> 'unassigned_at_{lev}'")
        carr = con.execute("CREATE OR REPLACE TEMP TABLE carr AS SELECT DISTINCT taxon, person, stratum, "
                           "band, country FROM (" + sel + ")")
        pb = con.execute("SELECT taxon, stratum, band, count(*) n FROM carr GROUP BY 1,2,3").df()
        pc = con.execute("SELECT taxon, stratum, count(DISTINCT country) nc FROM carr GROUP BY 1,2").df()
        if lev == "family":
            linmap = dict(zip(tg.family, tg.family_lineage))
        else:
            tf = os.path.join(work, "taxa_%s_full.parquet" % lev)
            if not os.path.exists(tf):
                con.execute(f"""COPY (SELECT {col} AS taxon, min(lineage) AS lineage
                                      FROM read_parquet('{f}') WHERE {col} <> 'unassigned_at_{lev}'
                                      GROUP BY 1) TO '{tf}' (FORMAT PARQUET)""")
            lin = pd.read_parquet(tf)
            linmap = dict(zip(lin.taxon, lin.lineage))
        for stratum in ("adult", "infant"):
            N = {b: int(nsub[(nsub.stratum == stratum) & (nsub.band == b)].n.sum()) for b in ("ind", "nonind", "trans")}
            est = N["ind"] >= SIMPLE_MIN_SUBJECTS and N["nonind"] >= SIMPLE_MIN_SUBJECTS
            w = pb[pb.stratum == stratum].pivot_table(index="taxon", columns="band", values="n",
                                                      aggfunc="sum", fill_value=0)
            for b in ("ind", "nonind", "trans"):
                if b not in w.columns:
                    w[b] = 0
            nc = pc[pc.stratum == stratum].set_index("taxon").nc
            if not est or not len(w):
                aro.append(dict(level=lev, stratum=stratum, design="hdi_band", estimable=False,
                                n_ind=N["ind"], n_nonind=N["nonind"], n_trans=N["trans"],
                                n_tested=0, n_vanish=0, n_blossum=0, n_stil=0, note="not estimable"))
                continue
            keep = w[(w["ind"] + w["nonind"]) > 0]
            pv = np.ones(len(keep))
            for i, (tx, r) in enumerate(keep.iterrows()):
                pv[i] = fisher_exact([[int(r["nonind"]), N["nonind"] - int(r["nonind"])],
                                      [int(r["ind"]), N["ind"] - int(r["ind"])]], alternative="two-sided")[1]
            qv = bh(pv)
            for i, (tx, r) in enumerate(keep.iterrows()):
                prevI = int(r["ind"]) / N["ind"] if N["ind"] else 0.0
                prevN = int(r["nonind"]) / N["nonind"] if N["nonind"] else 0.0
                prevT = int(r["trans"]) / N["trans"] if N["trans"] else 0.0
                ncc = int(nc.get(tx, 0))
                lfc = float(np.log2((prevN + 0.001) / (prevI + 0.001)))
                cls = ""
                if qv[i] < SIMPLE_Q and ncc >= SIMPLE_MIN_COUNTRIES:
                    if prevN >= SIMPLE_PREV_HI and prevI <= SIMPLE_PREV_LO:
                        cls = "VANISH"
                    elif prevI >= SIMPLE_PREV_HI and prevN <= SIMPLE_PREV_LO:
                        cls = "BloSSUM"
                # METHOD v2 D2: common in both bands, carriage differing by less than two-fold, no test
                if not cls and prevN >= stil_min_prev and prevI >= stil_min_prev \
                        and abs(lfc) < stil_max_score and ncc >= SIMPLE_MIN_COUNTRIES:
                    cls = "StIL"
                rows.append(dict(level=lev, stratum=stratum, taxon=tx, lineage=linmap.get(tx, ""),
                                 n_ind=N["ind"], n_nonind=N["nonind"], n_trans=N["trans"],
                                 pos_ind=int(r["ind"]), pos_nonind=int(r["nonind"]), pos_trans=int(r["trans"]),
                                 prev_ind=prevI, prev_nonind=prevN, prev_trans=prevT,
                                 p=float(pv[i]), q=float(qv[i]),
                                 lfc=lfc,
                                 n_countries=ncc, **{"class": cls}))
            aro.append(dict(level=lev, stratum=stratum, design="hdi_band", estimable=True,
                            n_ind=N["ind"], n_nonind=N["nonind"], n_trans=N["trans"],
                            n_tested=int(len(keep)),
                            n_vanish=int(sum(1 for x in rows if x["level"] == lev and x["stratum"] == stratum
                                             and x["class"] == "VANISH")),
                            n_blossum=int(sum(1 for x in rows if x["level"] == lev and x["stratum"] == stratum
                                              and x["class"] == "BloSSUM")),
                            n_stil=int(sum(1 for x in rows if x["level"] == lev and x["stratum"] == stratum
                                           and x["class"] == "StIL")),
                            stil_min_prev=stil_min_prev, stil_max_score=stil_max_score,
                            note=""))
        plog(out, "simple %s done (%.0f s)" % (lev, time.time() - t0))
    con.close()
    sr = pd.DataFrame(rows)
    sa = pd.DataFrame(aro)
    sr.to_csv(os.path.join(out, "simple_results.csv"), index=False)
    sa.to_csv(os.path.join(out, "simple_analyses.csv"), index=False)
    ncty.to_csv(os.path.join(out, "simple_countries.csv"), index=False)
    plog(out, "simple rule: %d rows, %.0f s" % (len(sr), time.time() - t0))
    return sr, sa


# ---------------------------------------------------------------- page payload (--stage page)

def _clade(lineage):
    """Short clade string for a tooltip: phylum and family, from a GTDB lineage."""
    ph, fa = "", ""
    for part in str(lineage).split(";"):
        part = part.strip()
        if part.startswith("p__"):
            ph = part
        elif part.startswith("f__"):
            fa = part
    return " / ".join([x for x in (ph, fa) if x])


def _cols(points, fields, clades):
    """Columnar encoding: one array per field, clade strings shared through an index."""
    cols = {f: [] for f in fields}
    for pt in points:
        for f in fields:
            v = pt.get(f)
            if f == "c":
                if v not in clades:
                    clades[v] = len(clades)
                v = clades[v]
            cols[f].append(v)
    return cols


MODEL_FIELDS = ["n", "c", "s", "se", "q", "pn", "pr", "yy", "cn", "cr", "k", "pb"]
SIMPLE_FIELDS = ["n", "c", "l", "q", "pn", "pi", "yy", "nc", "k", "pb"]

MIN_PREV_DRAW = 0.01      # the page draws a taxon carried by at least this share of one group...
MAX_POINTS_DRAW = 1500    # ...once a view has more points than this (and always under the simple rule)

# The type genus of each family that a published list names, for the case where GTDB R232 has no family of
# that name. Nomenclature, not data: the genus that gives the family its name.
TYPE_GENUS = {"Prevotellaceae": "Prevotella", "Succinivibrionaceae": "Succinivibrio",
              "Succinovibrionaceae": "Succinivibrio",   # Tamburini 2022 prints this spelling
              "Spirochaetaceae": "Spirochaeta", "Bacteroidaceae": "Bacteroides",
              "Enterobacteriaceae": "Escherichia"}


def _fam_of(lineage):
    for part in str(lineage).split(";"):
        part = part.strip()
        if part.startswith("f__"):
            return part
    return ""


def published_comparison(published_lists, carter_mapped, res, sres):
    """One row per published entry: how it maps to GTDB R232 and what this analysis says about it.

    Species rows are taken from the Carter mapping made earlier (`carter_mapped.csv`, r95 -> R232); they are
    not re-mapped here. Family rows are mapped by EXACT R232 family name only; when R232 has no family of
    that name, the row says so and reports where R232 places the family's type genus instead.
    """
    pl = pd.read_csv(published_lists)
    cm = pd.read_csv(carter_mapped) if (carter_mapped and os.path.exists(carter_mapped)) else pd.DataFrame()
    cmap = {}
    if len(cm):
        for r in cm.itertuples():
            cmap[(r.list, str(r.species_name_as_printed))] = (
                None if (not isinstance(r.r232_species, str) or not r.r232_species) else r.r232_species,
                str(getattr(r, "flag", "")))
    # the universe of names the catalog's profiles carry, from the simple rule (every carried taxon)
    fam_present, gen_lineage = set(), {}
    if len(sres):
        fam_present = set(sres.loc[sres.level == "family", "taxon"].astype(str))
        for r in sres[sres.level == "genus"].itertuples():
            gen_lineage.setdefault(str(r.taxon), str(r.lineage))
    # what this analysis says, per level
    mod, sim = {}, {}
    if len(res):
        m = res[(res.contrast == "C1") & (res.stratum == "adult")]
        mod = {(r.level, str(r.taxon)): (float(r.score_log2or), str(c) if isinstance(c, str) else "")
               for r, c in zip(m.itertuples(), m["class"].fillna("").astype(str))}
    if len(sres):
        sa = sres[sres.stratum == "adult"]
        sim = {(r.level, str(r.taxon)): (float(r.lfc), str(c) if isinstance(c, str) else "")
               for r, c in zip(sa.itertuples(), sa["class"].fillna("").astype(str))}
    rows, marks = [], {}
    for r in pl.itertuples():
        lev = str(r.rank)
        printed = str(r.name_as_printed)
        mapped, note = None, ""
        if lev == "species":
            got = cmap.get((str(r.list), printed))
            if got is None:
                note = "not in the mapping table (no species name in the published GTDB r95 lineage)"
            elif got[0] is None:
                note = "no R232 species: %s" % (got[1] or "unmapped")
            else:
                mapped, note = got[0], got[1]
        else:
            cand = "f__" + printed
            if cand in fam_present:
                mapped, note = cand, "exact GTDB R232 family name"
            else:
                tg = TYPE_GENUS.get(printed)
                lin = gen_lineage.get("g__" + tg) if tg else None
                where = _fam_of(lin) if lin else ""
                note = ("no GTDB R232 family of that name in the catalog's profiles; its type genus g__%s "
                        "sits in %s" % (tg, where)) if (tg and where) else \
                       ("no GTDB R232 family of that name in the catalog's profiles; its type genus g__%s "
                        "is not in the catalog's profiles either" % tg if tg else
                        "no GTDB R232 family of that name in the catalog's profiles")
        mv = mod.get((lev, mapped)) if mapped else None
        sv = sim.get((lev, mapped)) if mapped else None
        rows.append(dict(source=r.source_key, citation=r.citation_short, doi=r.doi, list=r.list, rank=lev,
                         name_as_printed=printed, taxonomy_release=r.taxonomy_release,
                         mapped_r232=mapped or "", map_note=note,
                         tested_model_c1=("yes" if mv else "no"),
                         score_model_c1=(round(mv[0], 3) if mv else ""),
                         class_model_c1=(mv[1] if mv else ""),
                         tested_simple=("yes" if sv else "no"),
                         lfc_simple=(round(sv[0], 3) if sv else ""),
                         class_simple=(sv[1] if sv else "")))
        if mapped:
            marks.setdefault((lev, mapped), set()).add((str(r.source_key), str(r.list)))
    out = pd.DataFrame(rows)
    # per source x list summary, for both rules
    summ = []
    for (src, lst), g in out.groupby(["source", "list"], sort=False):
        for rule, tcol, ccol in (("model rule, high-HDI countries, adults", "tested_model_c1", "class_model_c1"),
                                 ("simple rule, adults", "tested_simple", "class_simple")):
            t = g[g[tcol] == "yes"]
            cl = t[ccol].astype(str)
            summ.append(dict(source=src, citation=g.citation.iloc[0], doi=g.doi.iloc[0], list=lst, rule=rule,
                             n_entries=int(len(g)), n_mapped=int((g.mapped_r232 != "").sum()),
                             n_tested=int(len(t)), n_same=int((cl == lst).sum()),
                             n_opposite=int(cl.isin({"VANISH", "BloSSUM"} - {lst}).sum()),
                             n_stil=int((cl == "StIL").sum()),
                             n_not_classed=int((~cl.isin(["VANISH", "BloSSUM", "StIL"])).sum())))
    return out, pd.DataFrame(summ), marks


GZIP_DOWNLOADS = ("results.csv", "simple_results.csv")   # written to the page payload as .csv.gz


def page_stage(out, work, package, method=None, background=None, second=None, calibration_choice=None,
               levels=("species", "genus", "family"), published=None, carter_mapped=None,
               stil_min_prev=0.20, stil_max_score=1.0):
    """Write the page payload from the stored results. No refitting, no profile tables, no figures."""
    import shutil
    t0 = time.time()
    pdir = os.path.join(out, "page")
    os.makedirs(pdir, exist_ok=True)

    def rd(name):
        f = os.path.join(out, name)
        return pd.read_csv(f) if os.path.exists(f) else pd.DataFrame()

    res, ana = rd("results.csv"), rd("analyses.csv")
    sres, sana = rd("simple_results.csv"), rd("simple_analyses.csv")
    groups, cal, pcl, ccmp = rd("groups.csv"), rd("calibration.csv"), rd("permutation_classes.csv"), rd("carter_comparison.csv")
    ver = json.load(open(os.path.join(package, "VERSION.json"))) if package else {}
    KMAP = {"VANISH": "V", "BloSSUM": "B", "StIL": "S"}
    pubs_out, pub_summary, marks = pd.DataFrame(), pd.DataFrame(), {}
    if published and os.path.exists(published):
        pubs_out, pub_summary, marks = published_comparison(published, carter_mapped, res, sres)
        pubs_out.to_csv(os.path.join(out, "published_comparison.csv"), index=False)

    def pub_entry(level, name, reg):
        """Index of this taxon's published-list membership in the file's small `pubs` table (0 = none)."""
        got = marks.get((level, str(name)))
        if not got:
            return 0
        kk = tuple(sorted(got))
        if kk not in reg:
            lists = sorted({l for _, l in kk})
            reg[kk] = dict(k=len(reg) + 1, dir=("V" if lists == ["VANISH"] else
                                                ("B" if lists == ["BloSSUM"] else "M")),
                           label="; ".join("%s list of %s" % (l, CITE.get(s, s)) for s, l in kk))
        return reg[kk]["k"]

    def pub_table(reg):
        return [v for _, v in sorted(reg.items(), key=lambda kv: kv[1]["k"])]

    CITE = {}
    if len(pubs_out):
        CITE = dict(zip(pubs_out.source.astype(str), pubs_out.citation.astype(str)))
    strata = ["adult", "infant"]
    files = []
    for lev in levels:
        for stratum in strata:
            pay = {"level": lev, "stratum": stratum, "model": {}, "simple": None}
            pubreg = {}
            # round 4: for infants the primary definition has too few non-industrialized cohorts, so the page
            # also gets the broad definition (C4), the only one the model can be fitted for
            for cname in (("C1", "C2", "C4") if stratum == "infant" else ("C1", "C2")):
                a = ana[(ana.level == lev) & (ana.contrast == cname) & (ana.stratum == stratum)]
                if not len(a):
                    continue
                a = a.iloc[0]
                blk = {"contrast": cname, "estimable": bool(a.estimable),
                       "n_ni": int(a.n_ni), "n_ref": int(a.n_ref), "cohorts_ni": int(a.cohorts_ni),
                       "cohorts_ref": int(a.cohorts_ref), "cohorts_distinct": int(a.cohorts_distinct),
                       "note": ("" if a.estimable else "This comparison could not be computed: too few non-industrialized samples at this level."), "points": []}
                g = res[(res.level == lev) & (res.contrast == cname) & (res.stratum == stratum)] if len(res) else res
                # "class" is a Python keyword, so itertuples renames it: take the column directly
                kvals = g["class"].fillna("").astype(str).tolist() if len(g) else []
                for i_, r in enumerate(g.itertuples()):
                    blk["points"].append({
                        "n": r.taxon, "c": _clade(r.lineage),
                        "s": round(float(r.score_log2or), 3),
                        "se": None if not np.isfinite(r.se_log2or) else round(float(r.se_log2or), 3),
                        "q": float("%.3g" % r.q),
                        "pn": round(float(r.prev_ni_cohort_mean), 4), "pr": round(float(r.prev_ref_cohort_mean), 4),
                        "yy": round(float((r.n_pos_ni + r.n_pos_ref) / max(r.n_ni + r.n_ref, 1)), 4),
                        "cn": int(r.cohorts_ni_detected), "cr": int(r.cohorts_ref_detected),
                        "k": KMAP.get(kvals[i_], ""), "pb": pub_entry(lev, r.taxon, pubreg)})
                blk["n_tested"] = len(blk["points"])
                blk["n_vanish"] = sum(1 for x in blk["points"] if x["k"] == "V")
                blk["n_blossum"] = sum(1 for x in blk["points"] if x["k"] == "B")
                blk["n_stil"] = sum(1 for x in blk["points"] if x["k"] == "S")
                # only the drawn points are written: a taxon under 1 % in both groups is not drawn once a
                # view is crowded, and the CSV downloads carry every tested taxon
                if len(blk["points"]) > MAX_POINTS_DRAW:
                    blk["points"] = [x for x in blk["points"]
                                     if max(x["pn"] or 0, x["pr"] or 0) >= MIN_PREV_DRAW or x["k"]]
                blk["n_drawn"] = len(blk["points"])
                blk["n_hidden"] = blk["n_tested"] - blk["n_drawn"]
                if cname == "C4" and stratum == "infant" and bool(a.estimable):
                    a1 = ana[(ana.level == lev) & (ana.contrast == "C1") & (ana.stratum == stratum)]
                    n1 = int(a1.iloc[0].n_ni) if len(a1) else 0
                    c1 = int(a1.iloc[0].cohorts_ni) if len(a1) else 0
                    blk["view_note"] = (
                        "Infants, model rule: the main definition of non-industrialized leaves %s infant samples "
                        "from %d project%s, too few to fit the model. This view uses the broad definition, which "
                        "also counts samples whose stated lifestyle is rural and non-industrialized: %s samples "
                        "from %d projects, against %s from %d projects in high-HDI countries (%d permutations). "
                        "With %d projects it is a first look, not a settled list."
                        % (format(n1, ","), c1, "" if c1 == 1 else "s", format(int(a.n_ni), ","),
                           int(a.cohorts_ni), format(int(a.n_ref), ","), int(a.cohorts_ref),
                           int(a.n_perm), int(a.cohorts_ni)))
                pay["model"][cname] = blk
            if len(sana):
                sa = sana[(sana.level == lev) & (sana.stratum == stratum)]
                if len(sa):
                    sa = sa.iloc[0]
                    sb = {"estimable": bool(sa.estimable), "n_ind": int(sa.n_ind), "n_nonind": int(sa.n_nonind),
                          "n_trans": int(sa.n_trans), "points": [],
                          "note": ("" if sa.estimable else "This comparison could not be computed: too few non-industrialized samples at this level.")}
                    g = sres[(sres.level == lev) & (sres.stratum == stratum)] if len(sres) else sres
                    kvals = g["class"].fillna("").astype(str).tolist() if len(g) else []
                    for i_, r in enumerate(g.itertuples()):
                        sb["points"].append({
                            "n": r.taxon, "c": _clade(r.lineage),
                            "l": round(float(r.lfc), 3), "q": float("%.3g" % r.q),
                            "pn": round(float(r.prev_nonind), 4), "pi": round(float(r.prev_ind), 4),
                            "yy": round(float((r.pos_ind + r.pos_nonind) / max(r.n_ind + r.n_nonind, 1)), 4),
                            "nc": int(r.n_countries), "k": KMAP.get(kvals[i_], ""),
                            "pb": pub_entry(lev, r.taxon, pubreg)})
                    sb["n_tested"] = len(sb["points"])
                    sb["n_vanish"] = sum(1 for x in sb["points"] if x["k"] == "V")
                    sb["n_blossum"] = sum(1 for x in sb["points"] if x["k"] == "B")
                    sb["n_stil"] = sum(1 for x in sb["points"] if x["k"] == "S")
                    sb["points"] = [x for x in sb["points"]
                                    if max(x["pn"] or 0, x["pi"] or 0) >= MIN_PREV_DRAW or x["k"]]
                    sb["n_drawn"] = len(sb["points"])
                    sb["n_hidden"] = sb["n_tested"] - sb["n_drawn"]
                    pay["simple"] = sb
            # one file per rule, loaded on demand, columnar so each stays small
            if pay["model"]:
                clades, blocks = {}, {}
                for cname, blk in pay["model"].items():
                    b = {k: v for k, v in blk.items() if k != "points"}
                    b["cols"] = _cols(blk["points"], MODEL_FIELDS, clades)
                    blocks[cname] = b
                fn = "points_%s_%s_model.json" % (lev, stratum)
                json.dump({"level": lev, "stratum": stratum, "rule": "model", "fields": MODEL_FIELDS,
                           "clades": [k for k, _ in sorted(clades.items(), key=lambda kv: kv[1])],
                           "pubs": pub_table(pubreg), "blocks": blocks},
                          open(os.path.join(pdir, fn), "w"), separators=(",", ":"))
                files.append(fn)
            if pay["simple"]:
                clades = {}
                b = {k: v for k, v in pay["simple"].items() if k != "points"}
                b["cols"] = _cols(pay["simple"]["points"], SIMPLE_FIELDS, clades)
                fn = "points_%s_%s_simple.json" % (lev, stratum)
                json.dump({"level": lev, "stratum": stratum, "rule": "simple", "fields": SIMPLE_FIELDS,
                           "clades": [k for k, _ in sorted(clades.items(), key=lambda kv: kv[1])],
                           "pubs": pub_table(pubreg), "blocks": {"S": b}},
                          open(os.path.join(pdir, fn), "w"), separators=(",", ":"))
                files.append(fn)
    # Round 3: taxa that pass D1 a-c from the stored fits but still await the leave-one-cohort-out
    # refits of D1d (that run reads the profile tables); they are listed, not classed.
    pend = rd("stil_model_pending.csv")
    pend_counts = ({str(k): int(v) for k, v in pend.groupby("level").size().items()}
                   if len(pend) else {})
    # R2026.42 (Editor): the full run of a release reads the release's own HDI table, so the round-2 note
    # ("as it stood at the run of 2026-11-14 ... enter at the next full run") no longer applies.
    hdi_note = ("Every result on this page uses the country HDI table of the package it was computed on. "
                "Three territories without a UNDP value take one from another published source, recorded "
                "in that table (Taiwan, Puerto Rico, French Guiana).")
    bg = json.load(open(background)) if (background and os.path.exists(background)) else {}
    att = bg.get("attribution_summary", {})
    cc = os.path.join(out, "carter_context.json")
    meta = {
        "generated": time.strftime("%Y-%m-%d"),
        "package_version": ver.get("package_version"), "release_id": ver.get("release_id"),
        "build_date": ver.get("build_date"), "method": "METHOD_v2.md",
        "levels": list(levels), "strata": strata, "files": files,
        "contrast_labels": {"C1": "high-HDI countries", "C2": "stated urban industrialized",
                            "C3": "high-HDI countries (catalog labels only)",
                            "C4": "high-HDI countries (broad lifestyle)"},
        "thresholds": {}, "calibration_choice": calibration_choice,
        "analyses": json.loads(ana.to_json(orient="records")) if len(ana) else [],
        "simple_analyses": json.loads(sana.to_json(orient="records")) if len(sana) else [],
        "groups": json.loads(groups.to_json(orient="records")) if len(groups) else [],
        "calibration": json.loads(cal.to_json(orient="records")) if len(cal) else [],
        "permutation_classes": json.loads(pcl.to_json(orient="records")) if len(pcl) else [],
        "carter_comparison": json.loads(ccmp.to_json(orient="records")) if len(ccmp) else [],
        "carter_context": json.load(open(cc)) if os.path.exists(cc) else {},
        "background": {"vanish_quote": att.get("VANISH", {}).get("verbatim_quote"),
                       "source": att.get("VANISH", {}).get("earliest_source_in_which_a_definition_COULD_BE_READ"),
                       "unverified": att.get("VANISH", {}).get("unverified"),
                       "operational": (att.get("operational_quantitative_definition") or {}).get("verbatim_quote")},
        "second_implementation": json.load(open(second)) if (second and os.path.exists(second)) else None,
        "published_summary": json.loads(pub_summary.to_json(orient="records")) if len(pub_summary) else [],
        "published_sources": (pubs_out.drop_duplicates("source")[["source", "citation", "doi"]]
                              .to_dict("records") if len(pubs_out) else []),
        "stil": {"min_prev": stil_min_prev, "max_score": stil_max_score,
                 "doi": "10.1038/s41586-026-11106-1",
                 "citation": "Carter, Liu, Olm et al., Nature 658 (2026)",
                 "computed_model_rule": bool(len(res) and (res["class"].astype(str) == "StIL").any()),
                 "computed_simple_rule": bool(len(sres) and (sres["class"].astype(str) == "StIL").any()),
                 "pending_model_rule": pend_counts, "pending_model_rule_total": sum(pend_counts.values())},
        "hdi_note": hdi_note,
        "downloads": [],
    }
    if len(ana):
        row = ana[ana.estimable == True]
        if len(row):
            r0 = row.iloc[0]
            meta["thresholds"] = {k: (float(r0[k]) if k in row.columns else None)
                                  for k in ("Q", "S_min", "D_min", "P_loco")}
            meta["thresholds"]["B_primary"] = int(ana[ana.contrast == "C1"].n_perm.max()) if "n_perm" in ana.columns else None
            meta["thresholds"]["B_sensitivity"] = int(ana[ana.contrast == "C3"].n_perm.max()) if "n_perm" in ana.columns else None
    # catalog counts, from the stage-1 table (no profile table is read)
    sc = os.path.join(work, "samples_clean.parquet")
    if os.path.exists(sc):
        con = _con(work, "1GB")
        meta["n_qc_clean_gut"] = int(con.execute(f"SELECT count(*) FROM read_parquet('{sc}')").fetchone()[0])
        con.close()
    for f in ["results.csv", "analyses.csv", "simple_results.csv", "simple_analyses.csv", "groups.csv",
              "cohorts.csv", "calibration.csv", "permutation_classes.csv", "carter_comparison.csv",
              "published_comparison.csv", "stil_model_pending.csv"]:
        src = os.path.join(out, f)
        if os.path.exists(src):
            if f in GZIP_DOWNLOADS:      # the two per-taxon tables are megabytes as CSV; the site has a size limit
                import gzip
                with open(src, "rb") as fi, open(os.path.join(pdir, f + ".gz"), "wb") as fo:
                    with gzip.GzipFile(filename=f, mode="wb", fileobj=fo, compresslevel=9, mtime=0) as gz:
                        gz.write(fi.read())
                meta["downloads"].append(f + ".gz")
            else:
                shutil.copyfile(src, os.path.join(pdir, f))
                meta["downloads"].append(f)
    if method and os.path.exists(method):
        shutil.copyfile(method, os.path.join(pdir, os.path.basename(method)))
        meta["downloads"].append(os.path.basename(method))
    json.dump(meta, open(os.path.join(pdir, "meta.json"), "w"), indent=1)
    plog(out, "page payload: %d point files, %d downloads, %.0f s" % (len(files), len(meta["downloads"]), time.time() - t0))
    return meta


# ---------------------------------------------------------------- main

def carter_sets(carter_mapped):
    out = {}
    if carter_mapped and os.path.exists(carter_mapped):
        cm = pd.read_csv(carter_mapped)
        cm = cm[cm.r232_species.notna()]
        for lst in ("VANISH", "BloSSUM"):
            out[lst] = set(cm.loc[cm.list == lst, "r232_species"].astype(str))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--package", required=True)
    ap.add_argument("--sandpiper-dir", required=True)
    ap.add_argument("--hdi", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--external-labels", default=None)
    ap.add_argument("--levels", default="species,genus,family")
    ap.add_argument("--cap", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--memory-limit", default="3GB")
    ap.add_argument("--permutations", type=int, default=200)
    ap.add_argument("--permutations-sens", type=int, default=100)
    ap.add_argument("--perm-block", type=int, default=25)
    ap.add_argument("--q", type=float, default=0.05)
    ap.add_argument("--min-score", type=float, default=2.0)
    ap.add_argument("--min-dprev", type=float, default=0.10)
    ap.add_argument("--p-loco", type=float, default=0.01)
    ap.add_argument("--stil-min-prev", type=float, default=0.20,
                    help="METHOD v2 D1b/D2: minimum prevalence in BOTH groups for a StIL candidate")
    ap.add_argument("--stil-max-score", type=float, default=1.0,
                    help="METHOD v2 D1c/D2: the score interval (and |lfc|) must stay inside (-E, +E)")
    ap.add_argument("--published-lists", default=None,
                    help="track_a/lit/published_lists.csv, for the page's published-list comparison")
    ap.add_argument("--carter-mapped", default=None)
    ap.add_argument("--carter-lists", default=None)
    ap.add_argument("--carter-cohort-check", default=None)
    ap.add_argument("--method", default=None)
    ap.add_argument("--stage", default="all",
                    choices=["all", "prep", "obs", "perm", "classes", "simple", "page"])
    ap.add_argument("--background", default=None, help="track_a/lit/background.json, for the page's first section")
    ap.add_argument("--second-implementation-summary", default=None,
                    help="JSON with the agreement summary of the independent implementation")
    ap.add_argument("--calibration-choice", default=None,
                    help="which single threshold relaxation of A6 was adopted, or 'default'")
    a = ap.parse_args()
    t_start = time.time()
    levels = [x.strip() for x in a.levels.split(",") if x.strip()]
    os.makedirs(a.out, exist_ok=True)
    work = os.path.join(a.out, "_work")
    os.makedirs(work, exist_ok=True)
    base = (a.q, a.min_score, a.min_dprev, a.p_loco)
    do_obs = a.stage in ("all", "obs", "perm", "classes")
    do_perm = a.stage in ("all", "perm")
    do_class = a.stage in ("all", "classes")
    do_simple = a.stage in ("all", "simple")
    if a.stage == "page":
        page_stage(a.out, work, a.package, method=a.method, background=a.background,
                   second=a.second_implementation_summary, calibration_choice=a.calibration_choice,
                   levels=levels, published=a.published_lists, carter_mapped=a.carter_mapped,
                   stil_min_prev=a.stil_min_prev, stil_max_score=a.stil_max_score)
        return
    plog(a.out, "start stage=%s jobs=%d levels=%s B=%d/%d" % (a.stage, a.jobs, ",".join(levels),
                                                              a.permutations, a.permutations_sens))

    d, counts = load_samples(a.package, a.sandpiper_dir, a.hdi, work, a.memory_limit, a.external_labels)
    sels, ameta = {}, {}
    for cname, ni_def, ref in CONTRASTS:
        for stratum in ("adult", "infant"):
            sub, meta = select(d, ni_def, ref, stratum, a.cap)
            sels[(cname, stratum)] = sub
            ameta[(cname, stratum)] = dict(meta, ni_def=ni_def, reference=ref)
    keys = sorted(set().union(*[set(s.sample_key) for s in sels.values() if len(s)]))
    keyser = pd.Series(np.arange(len(keys), dtype=np.int32), index=keys)
    for k, s in sels.items():
        if len(s):
            s["upos"] = keyser.reindex(s.sample_key.values).values
    files, rowcounts = extract_profiles(a.sandpiper_dir, keys, levels, work, a.memory_limit)
    plog(a.out, "prep: %d samples selected, profile rows %s" % (len(keys), rowcounts))

    # groups.csv and cohorts.csv (label source per cohort)
    grows, crows = [], []
    for (cname, stratum), sub in sels.items():
        m = ameta[(cname, stratum)]
        if not len(sub):
            continue
        ni = sub[sub.group == "NI"]
        rec = dict(contrast=cname, ni_def=m["ni_def"], reference=m["reference"], stratum=stratum,
                   n_ni=int(len(ni)), n_ref=int((sub.group == "REF").sum()),
                   cohorts_ni=int(ni.cohort.nunique()),
                   cohorts_ref=int(sub.loc[sub.group == "REF", "cohort"].nunique()),
                   cohorts_distinct=int(sub.cohort.nunique()),
                   n_conflict_dropped=m.get("n_conflict", 0), estimable=m["estimable"])
        for src in ("stated", "external", "both"):
            rec["n_ni_" + src] = int((ni.label_src == src).sum())
            rec["cohorts_ni_" + src] = int(ni.loc[ni.label_src == src, "cohort"].nunique())
        grows.append(rec)
        for (co, grp), g in sub.groupby(["cohort", "group"]):
            srcs = sorted(set(g.label_src) - {""})
            crows.append(dict(cohort=co, contrast=cname, stratum=stratum, group=grp, n=len(g),
                              label_source=("both" if len(srcs) > 1 or "both" in srcs else
                                            (srcs[0] if srcs else "")),
                              studies=",".join(sorted(set(g.study_accession))),
                              countries=",".join(sorted(set(g.country.dropna().astype(str)))),
                              lifestyles=",".join(sorted(set(g.lifestyle.dropna().astype(str)))),
                              ext_labels=",".join(sorted(set(g.ext_label.dropna().astype(str)))),
                              depth_log10_median=round(float(g.depth_log10.median()), 3)))
    if do_class or a.stage == "prep":
        pd.DataFrame(grows).to_csv(os.path.join(a.out, "groups.csv"), index=False)
        pd.DataFrame(crows).to_csv(os.path.join(a.out, "cohorts.csv"), index=False)

    cset = carter_sets(a.carter_mapped)
    cctx = carter_context(a.carter_lists, a.carter_cohort_check)
    results, analyses, calib, a7rows = [], [], [], []
    if do_obs:
        for lev in levels:
            L = level_arrays(files, lev, keys, work, a.memory_limit)
            for cname, ni_def, ref in CONTRASTS:
                for stratum in ("adult", "infant"):
                    sub = sels[(cname, stratum)]
                    m = ameta[(cname, stratum)]
                    key = ana_key(lev, cname, stratum)
                    row0 = dict(level=lev, contrast=cname, ni_def=ni_def, reference=ref, stratum=stratum,
                                n_ni=m["counts"]["NI"]["n"], n_ref=m["counts"]["REF"]["n"],
                                cohorts_ni=m["counts"]["NI"]["cohorts"],
                                cohorts_ref=m["counts"]["REF"]["cohorts"],
                                cohorts_distinct=m["counts"]["distinct_cohorts"])
                    if not m["estimable"]:
                        analyses.append(dict(row0, estimable=False, note="not estimable"))
                        continue
                    obs, st = observed_stage(sub, L, len(keys), a.jobs, key, work, a.out)
                    nperm = a.permutations if cname in ("C1", "C2") else a.permutations_sens
                    if do_perm:
                        perm_stage(sub, obs, st, a.jobs, key, work, a.out, nperm, a.perm_block)
                    if not do_class:
                        continue
                    T, B1, B = load_perm(key, work, nperm, obs.code.values)
                    if T is None or B == 0:
                        analyses.append(dict(row0, estimable=True, note="permutations missing"))
                        continue
                    Tobs = obs.lr_stat.values
                    nex = (T >= Tobs[:, None]).sum(axis=1)
                    p = (1.0 + nex) / (1.0 + B)
                    q = bh(p)
                    score = obs.score_log2or.values
                    dprev = (obs.prev_ni_cohort_mean - obs.prev_ref_cohort_mean).values
                    grid = [base] if not (lev == "species" and cname == PRIMARY and stratum == "adult") else None
                    cand = _cand_mask(q, score, dprev, *base[:3])
                    union = set(obs.code.values[cand])
                    # METHOD v2 section D: StIL candidates, adults only (D1), refits added to the same union
                    if stratum == "adult":
                        spre = _stil_pre_mask(cand, obs.prev_ni_cohort_mean.values,
                                              obs.prev_ref_cohort_mean.values, score,
                                              obs.se_log2or.values, a.stil_min_prev, a.stil_max_score)
                    else:
                        spre = np.zeros(len(obs), dtype=bool)
                    union |= set(obs.code.values[spre])
                    if grid is None:
                        for _, ch in CALIB_GRID:
                            cm2 = _cand_mask(q, score, dprev, ch.get("Q", a.q), ch.get("S", a.min_score),
                                             ch.get("D", a.min_dprev))
                            union |= set(obs.code.values[cm2])
                    have, nnew = loco_for(sorted(union), sub, st, obs, a.jobs, key, work)
                    cls, stable = [], []
                    for i in range(len(obs)):
                        c = int(obs.code.values[i])
                        if not cand[i]:
                            stable.append(False)
                            cls.append("StIL" if (spre[i] and _stil_from_loco(have.get(c), a.stil_max_score))
                                       else "")
                            continue
                        sg = np.sign(score[i])
                        ok = _stable_from_loco(have.get(c), sg, a.min_score, a.p_loco)
                        stable.append(ok)
                        cls.append(("VANISH" if sg > 0 else "BloSSUM") if ok else "")
                    r = obs.copy()
                    r["level"] = lev; r["contrast"] = cname; r["ni_def"] = ni_def
                    r["reference"] = ref; r["stratum"] = stratum
                    r["p"] = p; r["q"] = q; r["n_perm"] = B; r["n_exceed"] = nex
                    r["candidate"] = cand; r["stable"] = stable; r["class"] = cls
                    lc = [have.get(int(c)) for c in obs.code.values]
                    r["loco_n"] = [len(x) if x else 0 for x in lc]
                    r["loco_min_abs_score"] = [float(np.nanmin(np.abs([y["b1"] for y in x])) / np.log(2.0))
                                               if x else np.nan for x in lc]
                    r["loco_max_p"] = [float(np.nanmax([y["p"] for y in x])) if x else np.nan for x in lc]
                    r["loco_same_sign"] = [bool(x and np.all(np.sign([y["b1"] for y in x]) == np.sign(obs.b1.values[i])))
                                           for i, x in enumerate(lc)]
                    results.append(r)
                    analyses.append(dict(row0, estimable=True, n_tested=int(len(obs)), n_perm=B,
                                         Q=a.q, S_min=a.min_score, D_min=a.min_dprev, P_loco=a.p_loco,
                                         n_candidates=int(cand.sum()),
                                         n_vanish=int(sum(1 for x in cls if x == "VANISH")),
                                         n_blossum=int(sum(1 for x in cls if x == "BloSSUM")),
                                         n_stil=int(sum(1 for x in cls if x == "StIL")),
                                         stil_min_prev=a.stil_min_prev, stil_max_score=a.stil_max_score,
                                         note=""))
                    if grid is None:
                        calib += [dict(level=lev, contrast=cname, stratum=stratum, **x) for x in
                                  calibration(obs, q, score, dprev, sub, st, a.jobs, key, work, base, cset)]
                    if lev == "species" and cname in ("C1", "C2") and stratum == "adult":
                        cnt = np.load(os.path.join(work, "obs", key + "_counts.npz"))
                        a7rows += [dict(contrast=cname, **x) for x in
                                   shuffled_label_check(sub, st, obs, T, B1, cnt, a.jobs, key, work,
                                                        a.q, a.min_score, a.min_dprev, a.p_loco)]
                    del st, obs
            del L
    if do_class and results:
        res = pd.concat(results, ignore_index=True)
        res["headline_class"] = ""
        prim = res[res.contrast == PRIMARY].set_index(["level", "stratum", "taxon"])["class"]
        idx = pd.MultiIndex.from_arrays([res.level, res.stratum, res.taxon])
        res["headline_class"] = prim.reindex(idx).fillna("").values
        cols = ["level", "taxon", "lineage", "contrast", "ni_def", "reference", "stratum", "n_ni", "n_ref",
                "cohorts_ni", "cohorts_ref", "cohorts_ni_detected", "cohorts_ref_detected",
                "prev_ni_pooled", "prev_ref_pooled", "prev_ni_cohort_mean", "prev_ref_cohort_mean",
                "b1", "score_log2or", "se_log2or", "lr_stat", "p_lrt", "p", "q", "n_perm", "n_exceed",
                "sd_cohort", "b2_depth", "abund_a1_log10", "abund_se", "abund_p", "abund_q",
                "n_pos_ni", "n_pos_ref", "candidate", "loco_n", "loco_min_abs_score", "loco_max_p",
                "loco_same_sign", "stable", "class", "headline_class"]
        res[cols].to_csv(os.path.join(a.out, "results.csv"), index=False)
        pd.DataFrame(analyses).to_csv(os.path.join(a.out, "analyses.csv"), index=False)
        if calib:
            pd.DataFrame(calib).to_csv(os.path.join(a.out, "calibration.csv"), index=False)
        if a7rows:
            pd.DataFrame(a7rows).to_csv(os.path.join(a.out, "permutation_classes.csv"), index=False)
        if cctx:
            json.dump(cctx, open(os.path.join(a.out, "carter_context.json"), "w"), indent=1)
        if cset:
            cc = []
            for (lev, cname, stratum), g in res.groupby(["level", "contrast", "stratum"]):
                if lev != "species":
                    continue
                for lst in ("VANISH", "BloSSUM"):
                    ours = set(g.loc[g["class"] == lst, "taxon"])
                    tested = set(g.taxon)
                    cc.append(dict(source="model", level=lev, contrast=cname, stratum=stratum, list=lst,
                                   n_ours=len(ours), n_carter_mapped=len(cset[lst]),
                                   n_carter_tested_here=len(cset[lst] & tested),
                                   n_overlap=len(ours & cset[lst]),
                                   share_ours_in_carter=round(len(ours & cset[lst]) / len(ours), 4) if ours else np.nan))
            pd.DataFrame(cc).to_csv(os.path.join(a.out, "carter_comparison.csv"), index=False)
        plog(a.out, "classes: %d result rows, %d analyses" % (len(res), len(analyses)))
    elif do_class:
        pd.DataFrame(analyses).to_csv(os.path.join(a.out, "analyses.csv"), index=False)

    if do_simple:
        sr, sa = simple_stage(a.package, a.sandpiper_dir, a.hdi, levels, work, a.out, a.memory_limit,
                              stil_min_prev=a.stil_min_prev, stil_max_score=a.stil_max_score)
        if cset and len(sr):
            cc = []
            for (lev, stratum), g in sr.groupby(["level", "stratum"]):
                if lev != "species":
                    continue
                for lst in ("VANISH", "BloSSUM"):
                    ours = set(g.loc[g["class"] == lst, "taxon"])
                    cc.append(dict(source="simple", level=lev, contrast="hdi_band", stratum=stratum,
                                   list=lst, n_ours=len(ours), n_carter_mapped=len(cset[lst]),
                                   n_carter_tested_here=len(cset[lst] & set(g.taxon)),
                                   n_overlap=len(ours & cset[lst]),
                                   share_ours_in_carter=round(len(ours & cset[lst]) / len(ours), 4) if ours else np.nan))
            f = os.path.join(a.out, "carter_comparison.csv")
            cc = pd.DataFrame(cc)
            if os.path.exists(f):   # a rerun of the simple stage replaces its own rows instead of appending them again
                prev = pd.read_csv(f)
                cc = pd.concat([prev[prev.source != "simple"], cc], ignore_index=True)
            cc.to_csv(f, index=False)
    if a.method and os.path.exists(a.method):
        import shutil
        shutil.copyfile(a.method, os.path.join(a.out, os.path.basename(a.method)))
    import resource
    peak = max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
               resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) / 1e9
    json.dump({"seconds": round(time.time() - t_start, 1), "jobs": a.jobs, "stage": a.stage,
               "peak_rss_gb_self_and_children": round(peak, 2)},
              open(os.path.join(a.out, "runtime_%s.json" % a.stage), "w"), indent=1)
    plog(a.out, "done stage=%s in %.0f s, peak rss %.2f GB" % (a.stage, time.time() - t_start, peak))


if __name__ == "__main__":
    main()
