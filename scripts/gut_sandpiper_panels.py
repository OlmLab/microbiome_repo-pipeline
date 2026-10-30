"""Per-project Sandpiper community-composition panels for every profiled catalog project (R2026.20; R2026.31 one body site
per panel).

Mean relative abundance of the top genera over the project's panel samples: catalog samples whose Sandpiper profile exists,
no QC flag for low depth, non-metagenome, synthetic or RNA input, and — so that one bar never averages different body
sites — the body site that most of the project's profiled samples come from (`panel_body_site`; samples without a stated
site count only when no profiled sample states one). Genus rows come straight from gut_sandpiper_sample_genus (complete profiles that sum
to 1 per sample, including `unassigned_at_genus`); phyla are the sum of genus rows by the phylum in the GTDB lineage.
Output schema follows sandpiper_study_panels (study_accession, rank, taxon, mean_rel_abundance, n_samples_panel, rank_order, ...)."""
import argparse
import pandas as pd

TOP = {"genus": 12, "phylum": 8}
DEF = ("mean rel_abundance over the project's profiled catalog samples of its most-profiled body site (panel_body_site) "
       "AND NOT (qc_low_depth OR qc_non_metagenome OR qc_synthetic OR qc_rna)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--genus", required=True); ap.add_argument("--summary", required=True)
    ap.add_argument("--wide", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import pyarrow.parquet as pq
    wc = [c for c in ("sample_key", "study_accession", "body_site_class", "body_site_code", "release_retired") if c in pq.read_schema(a.wide).names]
    w = pd.read_parquet(a.wide, columns=wc)
    if "release_retired" in w.columns:
        w = w[w.release_retired.isna()]
    sm = pd.read_parquet(a.summary, columns=["sample_key", "qc_low_depth", "qc_non_metagenome", "qc_synthetic", "qc_rna", "taxonomy_db", "taxonomy_version"])
    bad = sm[["qc_low_depth", "qc_non_metagenome", "qc_synthetic", "qc_rna"]].fillna(False).astype(bool).any(axis=1)
    w = w[w.body_site_class.isin(["primary", "unknown"])].merge(sm[~bad], on="sample_key", how="inner")
    if "body_site_code" in w.columns:   # one body site per panel: the project's most-profiled stated site
        site = w.body_site_code.where(w.body_site_code.notna() & (w.body_site_code != "unknown_site"))
        top = site.groupby(w.study_accession).agg(lambda v: v.value_counts().index[0] if v.notna().any() else None)
        w["panel_body_site"] = w.study_accession.map(top)
        w = w[w.panel_body_site.isna() | (site == w.panel_body_site)]
    else:
        w["panel_body_site"] = None
    tax = f"{w.taxonomy_db.dropna().iloc[0]}", f"{w.taxonomy_version.dropna().iloc[0]}"
    g = pd.read_parquet(a.genus, columns=["sample_key", "genus", "lineage", "relabund"], filters=[("sample_key", "in", sorted(set(w.sample_key)))])
    g = g.merge(w[["sample_key", "study_accession"]], on="sample_key")
    n = w.groupby("study_accession").sample_key.nunique().rename("n_samples_panel")
    g["phylum"] = g.lineage.str.extract(r"(p__[^;]+)", expand=False).fillna("unassigned_at_phylum").str.strip()
    out = []
    for rank, col in [("genus", "genus")]:
        s = g.groupby(["study_accession", col]).relabund.sum().rename("tot").reset_index().merge(n, on="study_accession")
        s["mean_rel_abundance"] = s.tot / s.n_samples_panel
        s = s.rename(columns={col: "taxon"})
        s["un"] = s.taxon.str.startswith("unassigned")
        s = s.sort_values(["study_accession", "mean_rel_abundance"], ascending=[True, False], kind="mergesort")
        s["r"] = s[~s.un].groupby("study_accession").cumcount()
        s = s[s.un | (s.r < TOP[rank])].copy()
        s["rank"] = rank
        out.append(s)
    p = pd.concat(out, ignore_index=True)
    p = p.sort_values(["study_accession", "rank", "un", "mean_rel_abundance"], ascending=[True, True, True, False], kind="mergesort")
    p["rank_order"] = p.groupby(["study_accession", "rank"]).cumcount()
    p["panel_definition"] = DEF
    p["taxonomy_db"], p["taxonomy_version"] = tax
    p["panel_body_site"] = p.study_accession.map(w.drop_duplicates("study_accession").set_index("study_accession").panel_body_site)
    p = p[["study_accession", "rank", "taxon", "mean_rel_abundance", "n_samples_panel", "rank_order", "panel_definition", "taxonomy_db", "taxonomy_version", "panel_body_site"]]
    p.to_parquet(a.out, index=False)
    print(f"catalog Sandpiper panels: {p.study_accession.nunique()} projects, {len(p)} rows")


if __name__ == "__main__":
    main()
