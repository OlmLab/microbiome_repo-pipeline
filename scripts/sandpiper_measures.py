#!/usr/bin/env python
"""sandpiper_measures.py — the four live measures on the Sandpiper join (R2026.36; docs/SANDPIPER_ANALYSES_PLAN.md §2, items 1-4).

  1 coverage   share of current catalog samples with a Sandpiper profile, by body site and year first public, with the reason
               a sample has no profile (no DNA run; every DNA run public after Sandpiper's screening cut-off; every DNA run below
               0.1 Gbp, which Sandpiper does not profile; otherwise not yet profiled), and the largest unprofiled projects
  2 agreement  a classifier on genus profiles predicts body site (and, for gut samples, infant vs adult); predictions are
               out-of-fold with folds split BY PROJECT, so a project never contributes to the model that scores it. Agreement
               with the catalog label + confidently discordant samples / projects (a queue for metadata checks, not corrections)
  3 known      known_species_fraction (share of prokaryotic coverage assigned to a named GTDB species) by country (gut samples)
               and body site: where reference genomes still miss most of the community
  4 depth      summed prokaryotic coverage against sequencing depth (Gbp of the sample's profiled DNA runs) and QC-flag rates
               per project

Inputs: a built package (catalog_sample_metadata_wide, catalog_runs, catalog_sandpiper_sample_summary) and the Sandpiper genus
table (data/inputs/catalog/sandpiper/gut_sandpiper_sample_genus.parquet + pca_loadings for the genus set). Output directory:
measures.json (numbers + table rows the page shows), figures (<name>.png at 300 dpi, <name>_web.png), CSVs with every row.

usage: python scripts/sandpiper_measures.py --package build/package --sandpiper-dir data/inputs/catalog/sandpiper \
         --countries data/inputs/gut/atlas/countries.json --basemap site_generator/gen/static/vendor/countries-110m.json \
         --cutoff 2026-03-03 --out data/inputs/site/measures
Deterministic (seeded); ≈ 2 min and ≈ 11 GB RAM on the full catalog.
"""
import argparse
import json
import os
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

SITE_LABEL = {"gut_stool": "Gut (stool)", "oral": "Oral", "skin": "Skin", "vaginal_urogenital": "Vaginal / urogenital",
              "nasal_nasopharyngeal": "Nasal / nasopharyngeal", "respiratory_lower": "Lower respiratory", "blood_tissue": "Blood / tissue",
              "eye_ear": "Eye / ear", "milk": "Milk", "other_site": "Other site", "multi_site": "Several sites", "unknown_site": "Site not recorded"}
TRAIN_SITES = ["gut_stool", "oral", "skin", "vaginal_urogenital", "nasal_nasopharyngeal", "respiratory_lower", "blood_tissue", "eye_ear"]
QC_EXCLUDE = ["qc_low_depth", "qc_non_metagenome", "qc_synthetic", "qc_rna", "qc_predicted_ecological", "qc_no_genus_assigned", "qc_low_complexity"]
REASONS = [("profiled_clean", "Profiled, passes QC"), ("profiled_flagged", "Profiled, QC-flagged"),
           ("below_depth", "Not profiled: every DNA run < 0.1 Gbp"), ("after_cutoff", "Not profiled: public after Sandpiper's cut-off"),
           ("not_yet", "Not profiled: reason not known"), ("no_dna", "No DNA run (RNA only)")]
COL = {"profiled_clean": "#1f5aa6", "profiled_flagged": "#8fb3e3", "below_depth": "#c98a1b", "after_cutoff": "#9a9a9a",
       "not_yet": "#e8d7ae", "no_dna": "#ffffff"}
MIN_RUN_GBP = 0.1


def style():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.titlesize": 8, "axes.labelsize": 8,
                         "xtick.labelsize": 6, "ytick.labelsize": 6, "legend.fontsize": 7, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.titlelocation": "left", "axes.titleweight": "normal",
                         "legend.frameon": False, "savefig.dpi": 300, "figure.dpi": 100})


def save(fig, out, name):
    fig.savefig(os.path.join(out, f"{name}.png"), dpi=300, bbox_inches="tight")
    fig.savefig(os.path.join(out, f"{name}_web.png"), dpi=130, bbox_inches="tight")
    plt.close(fig)


def letter(ax, s):
    ax.text(-0.02, 1.06, s, transform=ax.transAxes, fontsize=10, fontweight="bold", ha="right", va="bottom")


def kfmt(n):
    n = float(n)
    return f"{n / 1e6:.1f}M" if n >= 1e6 else (f"{n / 1e3:.0f}k" if n >= 1e4 else (f"{n / 1e3:.1f}k" if n >= 1e3 else f"{n:.0f}"))


# ---------------------------------------------------------------------------------------------------------------------- data
def load(a):
    t0 = time.time()
    W = pd.read_parquet(f"{a.package}/catalog_sample_metadata_wide.parquet",
                        columns=["sample_key", "study_accession", "release_retired", "body_site_code", "country", "age_category",
                                 "age_at_collection_days", "lifestyle", "package_added", "sample_type"])
    w = W[W.release_retired.isna()].drop(columns="release_retired").reset_index(drop=True)
    s = pd.read_parquet(f"{a.package}/catalog_sandpiper_sample_summary.parquet")
    runs = pd.read_parquet(f"{a.package}/catalog_runs.parquet", columns=["run_accession", "sample_key", "study_accession", "base_count",
                                                                          "first_public", "nucleic_acid"])
    runs = runs[runs.sample_key.isin(set(w.sample_key))].drop_duplicates("run_accession")
    st = pd.read_parquet(f"{a.package}/catalog_studies.parquet")
    tcol = "study_title" if "study_title" in st.columns else ("title" if "title" in st.columns else None)
    if "release_retired" in st.columns:
        st = st[st.release_retired.isna()]
    titles = st.drop_duplicates("study_accession").set_index("study_accession")[tcol] if tcol else pd.Series(dtype=str)
    print(f"[{time.time() - t0:.0f}s] current samples {len(w):,}, profiled {len(s):,}, runs {len(runs):,}", flush=True)
    return w, s, runs, titles


def coverage(w, s, runs, cutoff):
    dna = runs[runs.nucleic_acid.eq("dna")].assign(fp=pd.to_datetime(runs.first_public, errors="coerce"), gb=runs.base_count / 1e9)
    g = dna.groupby("sample_key")
    per = pd.DataFrame({"n_dna": g.size(), "fp_min": g.fp.min(), "gb_max_run": g.gb.max()})
    d = w.merge(per, left_on="sample_key", right_index=True, how="left")
    d["n_dna"] = d.n_dna.fillna(0).astype(int)
    ss = s.set_index("sample_key")
    prof = d.sample_key.isin(ss.index)
    flagged = d.sample_key.map(ss[QC_EXCLUDE].any(axis=1)).fillna(False).astype(bool)
    d["reason"] = np.select([prof & ~flagged, prof & flagged, d.n_dna == 0, d.fp_min > cutoff, d.gb_max_run < MIN_RUN_GBP],
                            ["profiled_clean", "profiled_flagged", "no_dna", "after_cutoff", "below_depth"], "not_yet")
    d["year_public"] = d.fp_min.dt.year
    return d


def matrix(s, sp_dir, keys):
    """CLR matrix of the PCA genera for the given samples. R2026.40: the long genus table (24 M rows) is no longer read into
    pandas (about 11 GB at peak); DuckDB joins it to integer row / column codes out of core and the transform runs in place in
    row chunks. Same values as before (same genera, same pseudo-count, same row order)."""
    import duckdb
    G = list(pd.read_parquet(f"{sp_dir}/gut_sandpiper_pca_loadings.parquet").genus)
    kdf = pd.DataFrame({"sample_key": sorted(set(keys))})
    kdf["si"] = np.arange(len(kdf), dtype=np.int32)
    gdf = pd.DataFrame({"genus": G, "gi": np.arange(len(G), dtype=np.int32)})
    con = duckdb.connect()
    con.execute("SET memory_limit='3GB'")
    con.execute("SET threads=4")
    con.register("kdf", kdf)
    con.register("gdf", gdf)
    r = con.execute(f"SELECT k.si AS si, g.gi AS gi, CAST(t.relabund AS FLOAT) AS v FROM read_parquet('{sp_dir}/gut_sandpiper_sample_genus.parquet') t "
                    "JOIN kdf k USING (sample_key) JOIN gdf g USING (genus)").fetchnumpy()
    con.close()
    X = np.zeros((len(kdf), len(G)), dtype=np.float32)
    X[r["si"], r["gi"]] = r["v"]
    del r
    ok = X.sum(1) > 0
    X = X[ok]
    si = pd.Index(kdf.sample_key[ok])
    dl = 1e-5
    for a0 in range(0, len(X), 50000):
        B = X[a0:a0 + 50000]
        B /= B.sum(1, keepdims=True)
        z = B == 0
        B *= (1 - z.sum(1) * dl)[:, None]
        B[z] = dl
        np.log(B, out=B)
        B -= B.mean(1, keepdims=True)
    return X, si


def _cap(ix, y, g, per_proj, per_class, seed=0):
    df = pd.DataFrame({"i": ix, "y": y, "g": g}).sample(frac=1, random_state=seed)
    return df.groupby(["y", "g"]).head(per_proj).groupby("y").head(per_class).i.to_numpy()


def oof_predict(C, y, groups, rows, classes_all_rows, per_proj=150, per_class=20000, folds=5):
    """Out-of-fold class probabilities for every row of the projects in `rows`' folds (project-grouped); rows of projects
    without any labelled row are scored by a model fit on all labelled rows. Returns (proba [n, k], classes)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold
    from sklearn.preprocessing import StandardScaler
    import warnings
    warnings.filterwarnings("ignore", category=Warning)
    classes = sorted(set(y[rows]))
    out = np.full((len(C), len(classes)), np.nan, dtype=np.float32)
    scope = classes_all_rows   # indices of rows to score
    for tr, te in GroupKFold(n_splits=folds).split(rows, groups=groups[rows]):
        tri = _cap(rows[tr], y[rows[tr]], groups[rows[tr]], per_proj, per_class)
        sc = StandardScaler().fit(C[tri])
        m = LogisticRegression(max_iter=500, C=0.5, class_weight="balanced").fit(sc.transform(C[tri]), y[tri])
        te_all = scope[np.isin(groups[scope], list(set(groups[rows[te]])))]
        out[np.ix_(te_all, [classes.index(c) for c in m.classes_])] = m.predict_proba(sc.transform(C[te_all]))
    rest = scope[np.isnan(out[scope, 0])]
    if len(rest):
        tri = _cap(rows, y[rows], groups[rows], per_proj, per_class)
        sc = StandardScaler().fit(C[tri])
        m = LogisticRegression(max_iter=500, C=0.5, class_weight="balanced").fit(sc.transform(C[tri]), y[tri])
        out[np.ix_(rest, [classes.index(c) for c in m.classes_])] = m.predict_proba(sc.transform(C[rest]))
    return out, classes


# --------------------------------------------------------------------------------------------------------------------- figures
def fig_coverage(d, out, cutoff):
    order = [k for k in SITE_LABEL if k in set(d.body_site_code)]
    order = sorted(order, key=lambda k: -int((d.body_site_code == k).sum()))
    tab = pd.crosstab(d.body_site_code, d.reason).reindex(index=order, columns=[r for r, _ in REASONS], fill_value=0)
    share = tab.div(tab.sum(1), axis=0)
    yr = pd.crosstab(d.year_public, d.reason).reindex(columns=[r for r, _ in REASONS], fill_value=0)
    yr = yr[(yr.index >= 2010)]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.4), gridspec_kw=dict(width_ratios=[1.15, 1], wspace=0.5))
    left = np.zeros(len(order))
    for r, lab in REASONS:
        a1.barh(range(len(order)), share[r].values * 100, left=left, color=COL[r], edgecolor="#555555" if r == "no_dna" else "none", linewidth=0.5, label=lab)
        left += share[r].values * 100
    a1.set_yticks(range(len(order)), [f"{SITE_LABEL[k]}  (n={kfmt(tab.loc[k].sum())})" for k in order])
    a1.invert_yaxis(); a1.set_xlim(0, 100); a1.set_xlabel("Share of current samples (%)")
    a1.set_title("Gut samples are mostly profiled; other sites lag")
    letter(a1, "a")
    bottom = np.zeros(len(yr))
    for r, lab in REASONS:
        a2.bar(yr.index, yr[r].values / 1e3, bottom=bottom, color=COL[r], edgecolor="#555555" if r == "no_dna" else "none", linewidth=0.5, width=0.8)
        bottom += yr[r].values / 1e3
    a2.set_xlabel("Year the sample's first DNA run became public"); a2.set_ylabel("Samples (thousands)")
    a2.set_title(f"Runs public after {cutoff:%-d %b %Y} wait for the next snapshot")
    a2.set_xticks(range(2010, int(yr.index.max()) + 1, 2)); a2.margins(x=0.02); letter(a2, "b")
    h, l_ = a1.get_legend_handles_labels()
    fig.legend(h, l_, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.12), handlelength=1.2, columnspacing=1.2)
    save(fig, out, "coverage")
    return tab, yr


def fig_agreement(cm, cm_n, life, out):
    labs = [SITE_LABEL[c] for c in cm.index]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.5), gridspec_kw=dict(width_ratios=[1.35, 1], wspace=0.75))
    im = a1.imshow(cm.values * 100, cmap="Blues", vmin=0, vmax=100, aspect="auto")
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            v = cm.values[i, j] * 100
            a1.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=6, color="white" if v >= 55 else "#222222")
    a1.set_xticks(range(len(labs)), [SITE_LABEL[c] for c in cm.columns], rotation=40, ha="right")
    a1.set_yticks(range(len(labs)), [f"{l}  (n={kfmt(n)})" for l, n in zip(labs, cm_n)])
    a1.set_xlabel("Body site predicted from the genus profile (cells: % of the row)"); a1.set_ylabel("Body site in the catalog")
    a1.set_title("Profiles agree with gut and vaginal labels; airway sites overlap")
    a1.spines[:].set_visible(False)
    letter(a1, "a")
    y = np.arange(len(life))
    a2.barh(y, life.infant_like * 100, color="#1f5aa6", height=0.65)
    a2.set_yticks(y, [f"{r.label}  (n={kfmt(r.n)})" for r in life.itertuples()]); a2.invert_yaxis()
    a2.set_xlim(0, 100); a2.set_xlabel("Gut samples with an infant-like profile (%)")
    for yi, v in zip(y, life.infant_like * 100):
        a2.text(v + 1.5, yi, f"{v:.0f}", va="center", fontsize=6)
    a2.set_title("Infant-like profiles fade with age")
    letter(a2, "b")
    save(fig, out, "agreement")


def _decode_topo(topo):
    tr = topo["transform"]; sx, sy = tr["scale"]; tx, ty = tr["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0; pts = []
        for dx, dy in arc:
            x += dx; y += dy; pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(ix):
        pts = []
        for i in ix:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            pts.extend(a if not pts else a[1:])
        return pts
    shapes = {}
    for g in topo["objects"]["countries"]["geometries"]:
        polys = g["arcs"] if g["type"] == "MultiPolygon" else ([g["arcs"]] if g["type"] == "Polygon" else [])
        shapes[str(g.get("id"))] = [[ring(r) for r in poly] for poly in polys]
    return shapes


def _unwrap(r):
    xs = [p[0] for p in r]
    if max(xs) - min(xs) > 180:   # ring crosses the antimeridian: move its western points east by 360 degrees
        return [((x + 360) if x < 0 else x, y) for x, y in r]
    return r


def fig_known(kc, ks, out, basemap, num_of, names):
    from matplotlib.collections import PolyCollection
    from matplotlib import cm as mcm, colors as mcolors
    shapes = _decode_topo(json.load(open(basemap)))
    fig = plt.figure(figsize=(7.2, 3.6))
    a1 = fig.add_axes([0.03, 0.12, 0.6, 0.78]); a2 = fig.add_axes([0.80, 0.14, 0.19, 0.76])
    norm = mcolors.Normalize(vmin=max(0, 10 * np.floor(kc.median_known.min() / 10)), vmax=100); cmap = matplotlib.colormaps["cividis"]
    val = {num_of.get(k): v for k, v in kc.set_index("country").median_known.items() if num_of.get(k)}
    polys, cols = [], []
    for num, pl in shapes.items():
        v = val.get(num)
        for poly in pl:
            for r_i, r in enumerate(poly[:1]):
                if len(r) > 2 and not (num == "010"):
                    polys.append(_unwrap(r)); cols.append(cmap(norm(v)) if v is not None else (0.9, 0.9, 0.9, 1))
    a1.add_collection(PolyCollection(polys, facecolors=cols, edgecolors="white", linewidths=0.2))
    a1.set_xlim(-170, 190); a1.set_ylim(-58, 84); a1.set_aspect(1.25); a1.axis("off")
    low = [names.get(c, c) for c in kc.sort_values("median_known").country.head(3)]
    a1.set_title(f"Least-described gut communities: {low[0]}, {low[1]} and {low[2]}", loc="left")
    sm = mcm.ScalarMappable(norm=norm, cmap=cmap)
    cax = fig.add_axes([0.08, 0.06, 0.4, 0.025]); cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.set_label("Named-species share of gut communities (%; median over projects with ≥ 10 samples); grey: fewer than 2 such projects", fontsize=6)
    cb.ax.tick_params(labelsize=6)
    letter(a1, "a")
    order = ks.sort_values("median").index[::-1]
    data = [ks.loc[k, "values"] for k in order]
    bp = a2.boxplot(data, vert=False, widths=0.6, showfliers=False, patch_artist=True, medianprops=dict(color="#111111", linewidth=1))
    for b in bp["boxes"]:
        b.set_facecolor("#8fb3e3"); b.set_edgecolor("#1f5aa6"); b.set_linewidth(0.6)
    for el in ("whiskers", "caps"):
        for l_ in bp[el]:
            l_.set_color("#1f5aa6"); l_.set_linewidth(0.6)
    a2.set_yticks(range(1, len(order) + 1), [f"{SITE_LABEL[k]}  (n={kfmt(ks.loc[k, 'n'])})" for k in order]); a2.invert_yaxis()
    a2.set_xlim(0, 100); a2.set_xlabel("Known-species share (%)")
    lows = [SITE_LABEL[k].split(" /")[0].lower() for k in order[::-1][:2]]
    a2.set_title(f"Least described: {lows[0]} and {lows[1]}")
    a2.text(-0.02, 1.06, "b", transform=a2.transAxes, fontsize=10, fontweight="bold", ha="right", va="bottom")
    save(fig, out, "known")


def fig_depth(dp, spf, out):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.3), gridspec_kw=dict(width_ratios=[1.15, 1], wspace=0.85))
    x = dp.gbp.clip(lower=1e-3); y = dp.root_coverage_sum.clip(lower=1e-2)
    hb = a1.hexbin(x, y, xscale="log", yscale="log", gridsize=70, bins="log", cmap="Blues", mincnt=1, linewidths=0)
    a1.axhline(2, color="#c98a1b", lw=0.8, ls="--"); a1.text(900, 2 * 1.2, "2× (low-depth flag)", fontsize=6, color="#8a5a00", ha="right")
    a1.set_xlabel("Sequencing depth of the profiled DNA runs (Gbp)"); a1.set_ylabel("Summed prokaryotic coverage (×)")
    a1.set_xticks([0.01, 0.1, 1, 10, 100], ["0.01", "0.1", "1", "10", "100"]); a1.set_yticks([0.1, 1, 10, 100, 1000], ["0.1", "1", "10", "100", "1k"])
    a1.set_title("Prokaryotic coverage rises with depth")
    cax = a1.inset_axes([0.06, 0.86, 0.32, 0.035]); cb = fig.colorbar(hb, cax=cax, orientation="horizontal")
    cb.set_label("Samples per hexagon", fontsize=6); cb.ax.xaxis.set_label_position("top"); cb.ax.tick_params(labelsize=6)
    letter(a1, "a")
    order = spf.sort_values("median").index[::-1]
    bp = a2.boxplot([spf.loc[k, "values"] for k in order], vert=False, widths=0.6, showfliers=False, patch_artist=True,
                    medianprops=dict(color="#111111", linewidth=1))
    for b in bp["boxes"]:
        b.set_facecolor("#8fb3e3"); b.set_edgecolor("#1f5aa6"); b.set_linewidth(0.6)
    for el in ("whiskers", "caps"):
        for l_ in bp[el]:
            l_.set_color("#1f5aa6"); l_.set_linewidth(0.6)
    a2.set_yticks(range(1, len(order) + 1), [f"{SITE_LABEL[k]}  (n={kfmt(spf.loc[k, 'n'])})" for k in order]); a2.invert_yaxis()
    a2.set_xlim(0, 100); a2.set_xlabel("Prokaryotic share of reads (%)")
    lows = [SITE_LABEL[k].split(" /")[0].lower() for k in order[::-1][:3]]
    a2.set_title(f"Host DNA dominates {lows[0]}, {lows[1]} and {lows[2]} samples")
    letter(a2, "b")
    save(fig, out, "depth")


# ------------------------------------------------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True); ap.add_argument("--sandpiper-dir", required=True)
    ap.add_argument("--countries", required=True); ap.add_argument("--basemap", required=True)
    ap.add_argument("--cutoff", default="2026-03-03", help="Sandpiper screening cut-off (runs public up to this date)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True); style(); t0 = time.time()
    cutoff = pd.Timestamp(a.cutoff)
    w, s, runs, titles = load(a)
    vj = json.load(open(f"{a.package}/VERSION.json"))
    M = {"package_version": vj.get("package_version"), "release_id": vj.get("release_id"), "build_date": vj.get("build_date"),
         "sandpiper_version": str(s.sandpiper_version.iloc[0]), "zenodo_record": str(s.zenodo_record.iloc[0]), "cutoff": a.cutoff}

    # 1 coverage
    d = coverage(w, s, runs, cutoff)
    tab, yr = fig_coverage(d, a.out, cutoff)
    rc = d.reason.value_counts()
    M["coverage"] = {"n_current": len(d), "n_profiled": int(rc.get("profiled_clean", 0) + rc.get("profiled_flagged", 0)),
                     "reasons": {r: int(rc.get(r, 0)) for r, _ in REASONS}, "reason_labels": dict(REASONS)}
    tab.assign(n=tab.sum(1)).rename(index=SITE_LABEL).to_csv(f"{a.out}/coverage_by_site.csv")
    yr.to_csv(f"{a.out}/coverage_by_year.csv")
    cc = pd.crosstab(d.country.fillna("not recorded"), d.reason.isin(["profiled_clean", "profiled_flagged"]))
    cc.columns = ["not_profiled" if not c else "profiled" for c in cc.columns]
    cc = cc.assign(n=cc.sum(1), share=lambda x: (x.profiled / x.n).round(4)).sort_values("n", ascending=False)
    cc.to_csv(f"{a.out}/coverage_by_country.csv")
    up = d[~d.reason.isin(["profiled_clean", "profiled_flagged"])]
    pj = pd.crosstab(up.study_accession, up.reason).reindex(columns=[r for r, _ in REASONS[2:]], fill_value=0)
    pj["n_unprofiled"] = pj.sum(1); pj["n_samples"] = d.groupby("study_accession").size().reindex(pj.index)
    pj["title"] = pj.index.map(titles); pj = pj.sort_values("n_unprofiled", ascending=False)
    pj.to_csv(f"{a.out}/unprofiled_projects.csv")
    M["coverage"]["top_unprofiled"] = pj.head(25).reset_index().fillna("").to_dict("records")
    M["coverage"]["by_country_top"] = cc.head(25).reset_index().rename(columns={"index": "country"}).to_dict("records")
    rel = d.groupby(d.package_added.fillna("?")).reason.agg(n="size", profiled=lambda r: int(r.isin(["profiled_clean", "profiled_flagged"]).sum()))
    M["coverage"]["by_package_added"] = rel.reset_index().to_dict("records")
    # deep, metagenome-public-before-cutoff runs Sandpiper has not profiled: a list for the Sandpiper maintainers
    prof_runs = set(s.runs_profiled.dropna().str.split(";").explode())
    rr = runs[runs.nucleic_acid.eq("dna")].assign(fp=pd.to_datetime(runs.first_public, errors="coerce"), gbp=runs.base_count / 1e9)
    nr = rr[(rr.fp <= cutoff) & (rr.gbp >= 1) & ~rr.run_accession.isin(prof_runs)]
    nr[["run_accession", "study_accession", "sample_key", "first_public", "gbp"]].to_csv(f"{a.out}/runs_not_yet_profiled_ge1gbp.csv", index=False)
    M["coverage"]["runs_not_yet_profiled_ge1gbp"] = len(nr)
    print(f"[{time.time() - t0:.0f}s] coverage done", flush=True)

    # 2 agreement
    ss = s.set_index("sample_key")
    clean = ss.index[~ss[QC_EXCLUDE].any(axis=1)]
    C, si = matrix(s, a.sandpiper_dir, clean)
    meta = w.set_index("sample_key").loc[si, ["study_accession", "body_site_code", "age_category", "age_at_collection_days"]].reset_index(drop=True)
    meta.insert(0, "sample_key", np.asarray(si))
    grp = meta.study_accession.to_numpy()
    site = meta.body_site_code.to_numpy(object)
    lab_rows = np.where(np.isin(site, TRAIN_SITES))[0]
    pr, cls = oof_predict(C, site, grp, lab_rows, np.arange(len(meta)))
    meta["pred_site"] = np.array(cls)[np.argmax(pr, 1)]; meta["p_site"] = pr.max(1)
    ev = meta.iloc[lab_rows]
    cm = pd.crosstab(ev.body_site_code, ev.pred_site, normalize="index").reindex(index=TRAIN_SITES, columns=TRAIN_SITES, fill_value=0)
    cm_n = ev.body_site_code.value_counts().reindex(TRAIN_SITES).fillna(0).astype(int).tolist()
    agree = float((ev.pred_site == ev.body_site_code).mean())
    per_site = {k: round(float(cm.loc[k, k]), 4) for k in TRAIN_SITES}
    disc = ev[(ev.pred_site != ev.body_site_code) & (ev.p_site >= 0.9)]
    gut = np.where(site == "gut_stool")[0]
    age = meta.age_at_collection_days.to_numpy(float)
    ya = np.full(len(meta), None, dtype=object); ya[age < 365] = "infant"; ya[age >= 18 * 365.25] = "adult"
    rows2 = gut[pd.notna(ya[gut])]
    p2, cls2 = oof_predict(C, ya, grp, rows2, gut, per_class=25000)
    meta["p_infant"] = np.nan
    meta.loc[gut, "p_infant"] = p2[gut, cls2.index("infant")]
    e2 = meta.iloc[rows2]
    life_agree = float(((e2.p_infant >= 0.5) == (ya[rows2] == "infant")).mean())
    gm = meta.iloc[gut]
    AC = [("neonate", "Neonate"), ("infant", "Infant"), ("child", "Child"), ("adolescent", "Adolescent"), ("adult", "Adult"), ("elderly", "Older adult")]
    life = pd.DataFrame([{"age_category": k, "label": lbl, "n": int((gm.age_category == k).sum()),
                          "infant_like": float((gm[gm.age_category == k].p_infant >= 0.5).mean()) if (gm.age_category == k).any() else np.nan} for k, lbl in AC])
    fig_agreement(cm, cm_n, life, a.out)
    disc2 = gm[gm.age_category.isin(["adult", "elderly"]) & (gm.p_infant >= 0.9)]
    dl = pd.concat([disc.assign(kind="body_site", catalog_value=disc.body_site_code, profile_suggests=disc.pred_site, probability=disc.p_site),
                    disc2.assign(kind="life_stage", catalog_value=disc2.age_category, profile_suggests="infant-like", probability=disc2.p_infant)])
    dl = dl[["sample_key", "study_accession", "kind", "catalog_value", "profile_suggests", "probability"]].round({"probability": 3})
    dl.to_csv(f"{a.out}/discordant_samples.csv", index=False)
    n_eval = ev.groupby("study_accession").size()
    dpj = dl.groupby(["study_accession", "kind"]).size().unstack(fill_value=0).reindex(columns=["body_site", "life_stage"], fill_value=0)
    dpj["n_profiled_clean"] = meta.groupby("study_accession").size().reindex(dpj.index)
    dpj["share"] = ((dpj.body_site + dpj.life_stage) / dpj.n_profiled_clean).round(3)
    dpj["main_suggestion"] = dl.groupby("study_accession").profile_suggests.agg(lambda x: x.value_counts().index[0])
    dpj["title"] = dpj.index.map(titles)
    dpj = dpj.sort_values(["share", "n_profiled_clean"], ascending=False)
    dpj = dpj[(dpj.body_site + dpj.life_stage) >= 20]
    dpj.to_csv(f"{a.out}/discordant_projects.csv")
    unk = meta[meta.body_site_code.isin(["unknown_site", "other_site"]) & (meta.p_site >= 0.9)]
    unk[["sample_key", "study_accession", "body_site_code", "pred_site", "p_site"]].round(3).to_csv(f"{a.out}/unlabelled_site_suggestions.csv", index=False)
    M["agreement"] = {"n_evaluated": len(ev), "agreement": round(agree, 4), "balanced_agreement": round(float(np.mean(list(per_site.values()))), 4),
                      "per_site": per_site, "n_discordant_site": len(disc), "n_discordant_life": len(disc2), "life_n_evaluated": len(e2),
                      "life_agreement": round(life_agree, 4), "life_table": life.round(4).to_dict("records"),
                      "n_unlabelled_site_suggestions": len(unk), "projects": dpj.head(30).reset_index().fillna("").to_dict("records"),
                      "n_projects_flagged": len(dpj)}
    pd.DataFrame(cm.values, index=TRAIN_SITES, columns=TRAIN_SITES).round(4).to_csv(f"{a.out}/agreement_confusion.csv")
    del C
    print(f"[{time.time() - t0:.0f}s] agreement {agree:.4f} (life {life_agree:.4f})", flush=True)

    # 3 known species
    sk = s.merge(w[["sample_key", "body_site_code", "country", "lifestyle"]], on="sample_key").set_index("sample_key").loc[clean.intersection(s.sample_key)]
    gk = sk[sk.body_site_code == "gut_stool"]
    # one value per project (median), projects with >= 10 QC-clean gut samples in the country; countries with >= 2 such projects
    pm = gk.groupby(["country", "study_accession"]).known_species_fraction.agg(pn="size", pmed="median").reset_index()
    pm = pm[pm.pn >= 10]
    kc = pm.groupby("country").agg(n=("pn", "sum"), n_projects=("study_accession", "nunique"), median_known=("pmed", "median"),
                                   min_project=("pmed", "min"), max_project=("pmed", "max")).reset_index()
    kc = kc[(kc.n >= 30) & (kc.n_projects >= 2)].sort_values("median_known")
    kc.round(2).to_csv(f"{a.out}/known_by_country_gut.csv", index=False)
    ks = sk[sk.body_site_code.isin(TRAIN_SITES)].groupby("body_site_code").known_species_fraction.agg(n="size", median="median")
    ks["values"] = [sk[sk.body_site_code == k].known_species_fraction.dropna().to_numpy() for k in ks.index]
    cj = json.load(open(a.countries)); num_of = {c["iso2"]: str(c["num"]).lstrip("0") or "0" for c in cj if c.get("num")}
    num_of = {k: v for k, v in num_of.items()}
    shapes_ids = set(_decode_topo(json.load(open(a.basemap))).keys())
    num_of = {k: (v if v in shapes_ids else v.zfill(3)) for k, v in num_of.items()}
    names = {c["iso2"]: c.get("name", c["iso2"]) for c in cj}
    kc["name"] = kc.country.map(names)
    fig_known(kc, ks, a.out, a.basemap, num_of, names)
    kl = sk[sk.body_site_code == "gut_stool"].groupby(sk.lifestyle.fillna("not recorded")).known_species_fraction.agg(n="size", median="median").round(2)
    ks.drop(columns="values").round(2).rename(index=SITE_LABEL).to_csv(f"{a.out}/known_by_site.csv")
    kl.to_csv(f"{a.out}/known_by_lifestyle_gut.csv")
    M["known"] = {"n_gut": len(gk), "n_countries": len(kc), "lowest": kc.head(8).round(2).to_dict("records"), "highest": kc.tail(8).round(2).to_dict("records"),
                  "largest": kc.sort_values("n", ascending=False).head(10).round(2).to_dict("records"),
                  "by_site": ks.drop(columns="values").round(2).reset_index().to_dict("records"), "by_lifestyle_gut": kl.reset_index().to_dict("records")}
    print(f"[{time.time() - t0:.0f}s] known done", flush=True)

    # 4 depth and QC
    prof_runs_df = runs[runs.run_accession.isin(prof_runs)]
    gb = prof_runs_df.groupby("sample_key").base_count.sum() / 1e9
    dp = s.assign(gbp=s.sample_key.map(gb)).merge(w[["sample_key", "body_site_code"]], on="sample_key")
    dp = dp[dp.gbp.notna() & (dp.gbp > 0)]
    sp_ = dp[dp.body_site_code.isin(TRAIN_SITES) & dp.spf.notna()]
    spf = sp_.groupby("body_site_code").spf.agg(n="size", median="median")
    spf["values"] = [sp_[sp_.body_site_code == k].spf.to_numpy() for k in spf.index]
    fig_depth(dp, spf, a.out)
    pq_ = s.groupby("study_accession").agg(n_profiled=("sample_key", "size"), median_coverage=("root_coverage_sum", "median"),
                                           low_depth=("qc_low_depth", "mean"), low_complexity=("qc_low_complexity", "mean"),
                                           non_metagenome=("qc_non_metagenome", "mean"), any_flag=("qc_any_flag", "mean"))
    pq_["median_gbp"] = dp.groupby("study_accession").gbp.median().reindex(pq_.index)
    pq_["title"] = pq_.index.map(titles)
    pq_ = pq_.round(3).sort_values("n_profiled", ascending=False)
    pq_.to_csv(f"{a.out}/depth_qc_projects.csv")
    M["depth"] = {"n": len(dp), "share_ge2": round(float((dp.root_coverage_sum >= 2).mean()), 4), "share_ge10": round(float((dp.root_coverage_sum >= 10).mean()), 4),
                  "median_gbp": round(float(dp.gbp.median()), 2), "median_coverage": round(float(dp.root_coverage_sum.median()), 1),
                  "spearman_gbp_coverage": round(float(dp[["gbp", "root_coverage_sum"]].corr(method="spearman").iloc[0, 1]), 3),
                  "spf_by_site": spf.drop(columns="values").round(2).reset_index().to_dict("records"),
                  "flag_rates": {c: round(float(s[c].mean()), 4) for c in ["qc_low_depth", "qc_low_complexity", "qc_non_metagenome", "qc_readfraction_warning", "qc_partial", "qc_any_flag"]},
                  "worst_projects": pq_[pq_.n_profiled >= 50].sort_values("any_flag", ascending=False).head(25).reset_index().fillna("").to_dict("records")}
    M["seconds"] = round(time.time() - t0, 1)
    json.dump(M, open(f"{a.out}/measures.json", "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if not isinstance(vv, (list, dict))}) for k, v in M.items()}, default=str))


if __name__ == "__main__":
    main()
