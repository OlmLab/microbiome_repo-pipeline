"""prepare_catalog_inputs.py — turn the scope-classification outputs (R2026.30) into inputs of the ONE catalog build.

The catalog builder (catalog.scopes.build_gut_scope) takes its original study list and sample tier unchanged; this script
writes the additional inputs it reads with --extra-* so every in-scope project, whatever its body site or nucleic acid,
goes through the same build. Deterministic; no model calls; prints counts only.

Writes to OUT_DIR (default data/inputs/catalog/):
  extra_studies.parquet          study rows (columns of data/inputs/gut/gut_studies.parquet) for the added projects
  extra_biosamples.parquet       registry_biosamples-shaped rows for their specimens
  extra_determinations.parquet   R1 / R2 / R3 determination rows (condition, collection date, subject, timepoint,
                                 supplementary-table values, study-level lifestyle)
  study_facts.parquet            per-project facts read from the paper (methods, design, access, setting) — ALL projects
  exposure_items.parquet         living-conditions items read from the paper, one row per item
  study_papers_extra.parquet     study <-> paper rows for projects the registry paper table does not cover
  short_descriptions_extra.parquet
  runs_source.parquet            every run the build may use: registry runs + ENA run lists of every catalog project

usage: python scripts/prepare_catalog_inputs.py [--cache ~/catalog/cache] [--out data/inputs/catalog]
"""
import argparse, glob, json, os, re

import numpy as np
import pandas as pd

PIPE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DET_COLS = ["sample_key", "field_name", "study_accession", "field_value", "value_normalized", "confidence", "evidence_source",
            "evidence_locator", "evidence_quote", "evidence_limited_to_abstract", "determined_by", "route", "scope", "parse_note",
            "group_audit", "src_track", "release_added", "release_retired", "package_added"]
SRC = "gut_all_v1"   # internal source-track id of the catalog build (not shown on the site)
DATE = re.compile(r"^\s*((?:19|20)\d{2})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?")


def _q12(v):
    w = str(v or "").split()
    return " ".join(w[:12]) if w else None


def row(sample, field, study, raw, norm, conf, src, loc, route, by, scope="sample", note=""):
    return dict(sample_key=sample, field_name=field, study_accession=study, field_value=None if raw is None else str(raw),
                value_normalized=None if norm is None else str(norm), confidence=float(conf), evidence_source=src,
                evidence_locator=loc, evidence_quote=_q12(raw), evidence_limited_to_abstract=0.0, determined_by=by,
                route=route, scope=scope, parse_note=note, group_audit=None, src_track=SRC, release_added=None,
                release_retired=None, package_added=None)


def iso_date(raw):
    s = str(raw or "").strip()
    m = DATE.match(s)
    if not m:
        try:
            d = pd.to_datetime(s, errors="raise", dayfirst=False)
            return d.strftime("%Y-%m-%d") if 1980 <= d.year <= 2026 else None
        except Exception:
            return None
    y, mo, dd = m.group(1), m.group(2), m.group(3)
    if not 1980 <= int(y) <= 2026:
        return None
    if mo and 1 <= int(mo) <= 12:
        if dd and 1 <= int(dd) <= 31:
            return f"{y}-{int(mo):02d}-{int(dd):02d}"
        return f"{y}-{int(mo):02d}"
    return y


def load_dossier_archive(cache, ids):
    out = {}
    for d in ("dossiers_all", "dossiers_recovered", "dossiers_delta"):
        for acc in ids:
            if acc in out:
                continue
            fp = os.path.join(cache, "large_update", d, f"{acc}.json")
            if os.path.exists(fp):
                try:
                    out[acc] = (json.load(open(fp)) or {}).get("archive") or {}
                except ValueError:
                    pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=os.path.expanduser("~/catalog/cache"))
    ap.add_argument("--out", default=os.path.join(PIPE, "data", "inputs", "catalog"))
    ap.add_argument("--extra-facts-dirs", nargs="*", default=[],
                    help="further summarise_paper_passes output prefixes whose fulltext/link/exposure tables are merged in")
    a = ap.parse_args()
    L = os.path.join(a.cache, "large_update")
    os.makedirs(a.out, exist_ok=True)
    stats = {}

    gut_in = pd.read_parquet(os.path.join(PIPE, "data", "inputs", "gut", "gut_studies.parquet"))
    gut_acc = set(gut_in.study_accession)
    cl = pd.read_parquet(os.path.join(L, "stage1", "classify_all.parquet"))
    keep = cl[cl.in_scope.isin(["yes", "partly"]) & ~cl.study_accession.isin(gut_acc)].copy()
    # R2026.31: projects of the original study list that fail its old gut-DNA rule, classified with the same scope
    # classification (stage 14); the ones in scope are admitted like the other classified projects
    rel_p = os.path.join(L, "stage14_relisted", "final.parquet")
    relisted = set()
    if os.path.exists(rel_p):
        rl = pd.read_parquet(rel_p)
        rl = rl[rl.in_scope.isin(["yes", "partly"])]
        relisted = set(rl.study_accession)
        keep = pd.concat([keep, rl.reindex(columns=keep.columns)], ignore_index=True).drop_duplicates("study_accession")
        stats["relisted_admitted"] = len(relisted)
    new_acc = set(keep.study_accession)
    stats["added_projects"] = len(new_acc)

    # ---------------- studies
    rs = pd.read_parquet(os.path.join(PIPE, "build", "package", "registry_studies.parquet"))
    if "release_retired" in rs.columns:
        rs = rs[rs.release_retired.isna()]
    base = rs[rs.study_accession.isin(new_acc)].copy()
    missing = sorted(new_acc - set(base.study_accession))
    arch = load_dossier_archive(a.cache, missing)
    extra_rows = []
    for acc in missing:
        r = dict(arch.get(acc) or {})
        r["study_accession"] = acc
        extra_rows.append(r)
    st = pd.concat([base, pd.DataFrame(extra_rows)], ignore_index=True, sort=False)
    kc = keep.set_index("study_accession")
    sites = kc.body_sites.reindex(st.study_accession).fillna("").values
    st["body_sites"] = np.where(sites != "", sites, st.get("body_sites"))
    st["host_human"] = st.get("host_human").fillna("yes") if "host_human" in st.columns else "yes"
    st["classification_stage"] = st.get("classification_stage").fillna("scope_classification") \
        if "classification_stage" in st.columns else "scope_classification"
    st["in_infant_catalog"] = None
    st["is_infant_curated"] = False
    if "description" not in st.columns or st["description"].isna().all():
        st["description"] = st.get("description_short")
    for c in gut_in.columns:
        if c not in st.columns:
            st[c] = None
    st = st[gut_in.columns.tolist()].drop_duplicates("study_accession")
    for c in st.columns:   # keep parquet-writable dtypes (numeric columns of the original study table stay numeric)
        if pd.api.types.is_numeric_dtype(gut_in[c].dtype) and not pd.api.types.is_bool_dtype(gut_in[c].dtype):
            st[c] = pd.to_numeric(st[c], errors="coerce")
        elif st[c].dtype == object:
            st[c] = st[c].map(lambda v: v if v is None or isinstance(v, (str, bool, int, float)) else json.dumps(v))
    st.to_parquet(os.path.join(a.out, "extra_studies.parquet"), index=False)
    stats["extra_studies"] = len(st)
    stats["extra_studies_from_dossier"] = len(missing)

    # ---------------- specimens
    sv = pd.read_parquet(os.path.join(L, "stage5", "sample_values.parquet"))
    sv = sv[sv.study_accession.isin(new_acc)].drop_duplicates("sample_accession")
    # RNA-only specimens of the original projects (per-project run refetch; human-metagenome taxa only; stage 15)
    rna_sv_p = os.path.join(L, "stage15_gutrna", "sample_values.parquet")
    if os.path.exists(rna_sv_p):
        sv2 = pd.read_parquet(rna_sv_p)
        sv2 = sv2[sv2.study_accession.isin(gut_acc) & ~sv2.sample_accession.isin(set(sv.sample_accession))]
        sv = pd.concat([sv, sv2], ignore_index=True).drop_duplicates("sample_accession")
        stats["rna_specimens_original_projects"] = len(sv2)
    # samples of the classified projects published without samples (stage 16): harvested attributes → values; a sample
    # with an in-scope run but no attribute record still enters, without values
    s16 = os.path.join(L, "stage16_delta_samples")
    if os.path.exists(os.path.join(s16, "sample_values.parquet")):
        sv3 = pd.read_parquet(os.path.join(s16, "sample_values.parquet"))
        sv3 = sv3[sv3.study_accession.isin(new_acc) & ~sv3.sample_accession.isin(set(sv.sample_accession))]
        todo = pd.read_csv(os.path.join(s16, "samples_todo.csv"), dtype=str)
        bare = todo[todo.study_accession.isin(new_acc) & ~todo.sample_accession.isin(set(sv.sample_accession) | set(sv3.sample_accession))]
        sv = pd.concat([sv, sv3, bare.assign(source="no_attribute_record")], ignore_index=True).drop_duplicates("sample_accession")
        stats["delta_project_samples"] = len(sv3) + len(bare)
        stats["delta_project_samples_without_attributes"] = len(bare)
    rbio = pd.read_parquet(os.path.join(PIPE, "build", "package", "registry_biosamples.parquet"))
    rb_cols = rbio.columns
    if relisted:   # their samples are registry BioSamples (already harvested and normalised by the registry tier)
        rr_b = rbio[rbio.study_accession.isin(relisted) & ~rbio.sample_accession.isin(set(sv.sample_accession))]
        if "release_retired" in rr_b.columns:
            rr_b = rr_b[rr_b.release_retired.isna()]
        sv = pd.concat([sv, rr_b], ignore_index=True).drop_duplicates("sample_accession")
        stats["relisted_samples"] = len(rr_b)
    bio = sv.reindex(columns=[c for c in rb_cols])
    bio["release_added"], bio["release_retired"], bio["package_added"] = None, None, None
    bio.to_parquet(os.path.join(a.out, "extra_biosamples.parquet"), index=False)
    stats["extra_specimens"] = len(bio)

    # ---------------- determinations
    dets = []
    for r in sv.itertuples(index=False):
        s, p = r.sample_accession, r.study_accession
        if isinstance(r.condition_code, str) and r.condition_code not in ("unknown", ""):
            dets.append(row(s, "health_condition", p, r.disease_raw, r.condition_code, 0.85, "sample.attr.disease",
                            "biosample_attr", "R1", "registry_norm_v1:condition"))
        if isinstance(r.disease_raw, str) and r.disease_raw.strip():
            dets.append(row(s, "health_condition_detail", p, r.disease_raw[:120], r.disease_raw[:120], 0.85,
                            "sample.attr.disease", "biosample_attr", "R1", "registry_attr_v1:condition_detail"))
        d = iso_date(r.collection_date_raw)
        if d:
            dets.append(row(s, "collection_date", p, r.collection_date_raw, d, 0.9, "sample.attr.collection_date",
                            "biosample_attr", "R1", "registry_attr_v1:collection_date"))
    stp = pd.read_parquet(os.path.join(L, "stage5", "subject_timepoint.parquet"))
    stp = stp[stp.study_accession.isin(new_acc)]
    for st_p in (os.path.join(L, "stage15_gutrna", "subject_timepoint.parquet"), os.path.join(L, "stage16_delta_samples", "subject_timepoint.parquet")):
        if os.path.exists(st_p):
            stp = pd.concat([stp, pd.read_parquet(st_p)], ignore_index=True).drop_duplicates("sample_key")
    for r in stp.itertuples(index=False):
        if isinstance(r.subject_id, str):
            dets.append(row(r.sample_key, "subject_id", r.study_accession, r.subject_id, r.subject_id, 0.9,
                            f"sample.attr.{r.subject_id_key}", "biosample_attr", "R1", "registry_attr_v1:subject"))
        if isinstance(r.timepoint_label, str):
            dets.append(row(r.sample_key, "timepoint_label", r.study_accession, r.timepoint_label, r.timepoint_label, 0.85,
                            f"sample.attr.{r.timepoint_label_key}", "biosample_attr", "R1", "registry_attr_v1:timepoint"))
    sp = pd.read_parquet(os.path.join(L, "supp_sample_values_all.parquet"))
    sp = sp[sp.study_accession.isin(new_acc)]
    fmap = {"sex": "sex", "age_days": "age_at_collection_days", "condition_code": "health_condition",
            "country_iso2": "country", "subject_id": "subject_id", "timepoint_label": "timepoint_label", "bmi": "bmi"}
    for r in sp[sp.field_name.isin(fmap)].itertuples(index=False):
        dets.append(row(r.sample_key, fmap[r.field_name], r.study_accession, r.value, r.value,
                        min(float(r.confidence or 0.8), 0.85), str(r.source), "supplementary_table", "R2",
                        "supp_join_v1"))
    ft = pd.read_parquet(os.path.join(L, "stage11_merged", "fulltext_summary.parquet"))
    for x in a.extra_facts_dirs:
        p = f"{x}fulltext_summary.parquet"
        if os.path.exists(p):
            f2 = pd.read_parquet(p)
            ft = pd.concat([ft[~ft.study_accession.isin(set(f2.study_accession))], f2], ignore_index=True)
    lf = ft[ft.study_accession.isin(new_acc) & ft.lifestyle.notna() & ~ft.lifestyle.isin(["unknown", "not_stated"])]
    for r in lf.itertuples(index=False):
        dets.append(row(None, "lifestyle", r.study_accession, r.lifestyle, r.lifestyle, 0.6, "paper.fulltext.methods",
                        "methods", "R3", "fulltext_setting_v1", scope="study_all",
                        note="checked against config/vocab/lifestyle.yaml match terms"))
    det = pd.DataFrame(dets, columns=DET_COLS)
    det.to_parquet(os.path.join(a.out, "extra_determinations.parquet"), index=False)
    stats["extra_determinations"] = det.field_name.value_counts().to_dict()

    # ---------------- study facts (all projects) + exposure items + papers
    la = pd.read_parquet(os.path.join(L, "stage11_merged", "link_audit.parquet"))
    ex = pd.read_parquet(os.path.join(L, "stage11_merged", "exposure_items.parquet"))
    for x in a.extra_facts_dirs:
        for name, cur in (("link_audit", "la"), ("exposure_items", "ex")):
            p = f"{x}{name}.parquet"
            if os.path.exists(p):
                d2 = pd.read_parquet(p)
                if cur == "la":
                    la = pd.concat([la[~la.study_accession.isin(set(d2.study_accession))], d2], ignore_index=True)
                else:
                    ex = pd.concat([ex[~ex.study_accession.isin(set(d2.study_accession))], d2], ignore_index=True)
    gen = la[la.relation == "generated"].groupby("study_accession").paper_id.apply(lambda s: ";".join(sorted(set(map(str, s)))))
    facts = ft.rename(columns={"nucleic_acids": "nucleic_acid_paper", "metadata_access": "metadata_access_paper",
                               "lifestyle": "lifestyle_paper"}).copy()
    facts = facts.merge(gen.rename("paper_ids_generated").reset_index(), on="study_accession", how="outer")
    facts = facts.merge(ex.groupby("study_accession").size().rename("n_exposure_items").reset_index(),
                        on="study_accession", how="left")
    cls_cols = keep[["study_accession", "processing", "study_shape", "nucleic_acid"]].rename(
        columns={"study_shape": "study_shape_classification", "nucleic_acid": "nucleic_acid_classification"})
    facts = facts.merge(cls_cols, on="study_accession", how="outer")
    facts.to_parquet(os.path.join(a.out, "study_facts.parquet"), index=False)
    ex.to_parquet(os.path.join(a.out, "exposure_items.parquet"), index=False)
    stats["study_facts"] = len(facts)
    stats["exposure_items"] = len(ex)

    pc = pd.read_parquet(os.path.join(L, "paper_candidates_new.parquet"))
    dpc = os.path.join(L, "delta_paper_candidates.parquet")
    if os.path.exists(dpc):
        pc = pd.concat([pc, pd.read_parquet(dpc)], ignore_index=True)
    for p in glob.glob(os.path.join(L, "stage*_recheck", "paper_candidates.parquet")) + \
             glob.glob(os.path.join(L, "stage9_recall", "paper_candidates.parquet")):
        pc = pd.concat([pc, pd.read_parquet(p)], ignore_index=True)
    rel = la[la.relation.isin(["generated", "reused"])][["study_accession", "paper_id", "relation"]]
    pp = rel.merge(pc.drop_duplicates(["study_accession", "paper_id"]), on=["study_accession", "paper_id"], how="left")
    pp["relation"] = pp.relation.map({"generated": "own_data", "reused": "reuse"})
    pp = pp[pp.study_accession.isin(new_acc | gut_acc)]
    cols = ["study_accession", "paper_id", "pmid", "pmcid", "doi", "title", "relation", "year", "journal"]
    pp.reindex(columns=cols).drop_duplicates(["study_accession", "paper_id"]).to_parquet(
        os.path.join(a.out, "study_papers.parquet"), index=False)
    stats["study_papers"] = len(pp)

    sds = sorted(glob.glob(os.path.join(L, "stage12_descriptions", "out_*.parquet")))
    if sds:
        sd = pd.concat([pd.read_parquet(f) for f in sds], ignore_index=True).drop_duplicates("study_accession", keep="last")
        sd.to_parquet(os.path.join(a.out, "short_descriptions_extra.parquet"), index=False)
        stats["short_descriptions_extra"] = len(sd)

    # ---------------- runs
    rcols = ["run_accession", "study_accession", "sample_accession", "secondary_sample_accession", "experiment_accession",
             "library_name", "library_strategy", "library_source", "library_selection", "library_layout",
             "instrument_platform", "instrument_model", "read_count", "base_count", "first_public"]
    rr = pd.read_parquet(os.path.join(PIPE, "data", "inputs", "registry", "registry_runs.parquet"),
                         columns=[c for c in rcols])
    # run_origin: registry = the enumeration's runs; refetch = the per-project ENA filereport of the original projects
    # (the builder takes only their RNA libraries); delta = runs of projects the scope classification added
    parts = [rr.assign(run_origin="registry")]
    for p, origin in [("gut_all_runs_ena.parquet", "refetch"), ("relisted_runs_ena.parquet", "delta"), ("delta_runs_fetched.parquet", "delta"),
                      ("delta_runs_recovered.parquet", "delta"), ("delta_runs_recovered2.parquet", "delta")]:
        fp = os.path.join(L, p)
        if os.path.exists(fp):
            parts.append(pd.read_parquet(fp).reindex(columns=rcols).assign(run_origin=origin))
    runs = pd.concat(parts, ignore_index=True)
    for c in ("read_count", "base_count"):
        runs[c] = pd.to_numeric(runs[c], errors="coerce")
    runs = runs.drop_duplicates("run_accession", keep="first")
    runs.to_parquet(os.path.join(a.out, "runs_source.parquet"), index=False)
    stats["runs_source"] = len(runs)
    stats["runs_source_rna_sources"] = int(runs.library_source.astype(str).str.upper().isin(
        ["TRANSCRIPTOMIC", "METATRANSCRIPTOMIC", "VIRAL RNA"]).sum())
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
