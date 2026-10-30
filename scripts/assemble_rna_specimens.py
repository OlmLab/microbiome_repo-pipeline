"""assemble_rna_specimens.py — per-sample core values for the RNA-only specimens of the original projects (R2026.31).
Same rules as assemble_samples_stage5.py: fielded attributes + the registry / stage-5 / this stage's pair maps →
catalog.registry.build_biosamples (body site, life stage / age, sex, country, collection date) + condition_code.
usage: python scripts/assemble_rna_specimens.py LARGE_UPDATE_DIR [STAGE_SUBDIR]   (default stage15_gutrna; reads
       STAGE/attrs_fielded.parquet + STAGE/pairs_out.parquet, writes STAGE/sample_values.parquet). Also used for the
       samples of the 128 projects published without samples (stage16_delta_samples).
"""
import glob, json, os, sys
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from catalog.registry.build_biosamples import build_biosamples, MIN_CONF


def main(L, sub="stage15_gutrna"):
    S = f"{L}/{sub}"
    att = pd.read_parquet(f"{S}/attrs_fielded.parquet").rename(columns={"sample_key": "sample_acc"})
    att = att[att.field.notna()].copy()
    for c in ("sample_acc", "attr_key_norm", "attr_value", "field", "study_accession", "attr_key", "attr_units", "source"):
        if c in att.columns:
            att[c] = att[c].astype(object).where(att[c].notna(), None)
    att["acc_resolved"] = att["sample_acc"]
    old = pd.read_parquet(os.path.join(HERE, "..", "data", "inputs", "registry", "registry_pairs_normalised.parquet"))
    st5 = pd.read_parquet(f"{L}/stage5/pairs_normalised_combined.parquet")
    new = pd.concat([pd.read_parquet(f) for f in [f"{S}/pairs_out.parquet"] + [g for g in glob.glob(f"{L}/stage1[5-9]*/pairs_out.parquet") if g != f"{S}/pairs_out.parquet"]], ignore_index=True)
    new = new[(new.status == "ok") & ~new.null_output.astype(bool)].copy()
    cnt = att.groupby(["field", "attr_key_norm", "attr_value"]).sample_acc.nunique().rename("n_samples").reset_index()
    new = new.drop(columns=[c for c in ("n_samples",) if c in new.columns]).merge(cnt, on=["field", "attr_key_norm", "attr_value"], how="left")
    cols = ["field", "attr_key_norm", "attr_value", "n_samples", "body_site_code", "life_stage", "age_days", "sex", "country_iso2",
            "confidence", "null_output"]
    norm = pd.concat([old[[c for c in cols if c in old.columns]], st5[[c for c in cols if c in st5.columns]],
                      new[[c for c in cols if c in new.columns]]], ignore_index=True)
    norm = norm.sort_values("confidence", ascending=False).drop_duplicates(["field", "attr_key_norm", "attr_value"])
    bios = build_biosamples(att, norm)
    cond = pd.concat([pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(f"{L}/stage5/pairs_out/*.parquet"))], ignore_index=True), new],
                     ignore_index=True)
    cond = cond[(cond.field == "disease") & cond.condition_code.notna()]
    dz = att[att.field == "disease"].merge(cond[["attr_key_norm", "attr_value", "condition_code", "confidence"]], on=["attr_key_norm", "attr_value"], how="inner")
    dz = dz[dz.confidence >= MIN_CONF].sort_values(["sample_acc", "confidence"], ascending=[True, False]).drop_duplicates("sample_acc")
    bios["condition_code"] = bios.sample_accession.map(dz.set_index("sample_acc").condition_code)
    bios.to_parquet(f"{S}/sample_values.parquet", index=False)
    real = lambda c, bad=(): int((bios[c].notna() & ~bios[c].isin(bad)).sum())
    print(json.dumps({"samples": len(bios), "projects": int(bios.study_accession.nunique()),
                      "body_site": real("body_site_code", ("unknown_site",)), "life_stage": real("life_stage", ("unknown_age",)),
                      "age_days": real("age_days"), "sex": int(bios.sex.isin(["female", "male"]).sum()),
                      "country": real("country_iso2"), "collection_year": real("collection_year"), "condition": real("condition_code"),
                      "body_sites": bios.body_site_code.value_counts().head(8).to_dict()}))


if __name__ == "__main__":
    main(*sys.argv[1:3])
