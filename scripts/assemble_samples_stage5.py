"""assemble_samples_stage5.py — per-sample core values for the in-scope new projects of R2026.30 (deterministic, free).

Inputs: the fielded attributes (stage5_attrs_fielded.parquet), the existing pair map (registry_pairs_normalised.parquet) and
the new pair map (stage5/pairs_out/*.parquet). Uses catalog.registry.build_biosamples.build_biosamples for body site,
life stage / age, sex, country and collection date (same rules as the gut catalog), then adds condition_code from the new
map with the same pick rule (highest confidence >= 0.5, then the pair seen most often).
Output: stage5/sample_values.parquet (one row per BioSample) and a JSON line of coverage counts.

usage: python scripts/assemble_samples_stage5.py LARGE_UPDATE_DIR
"""
import glob, json, os, sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
from catalog.registry.build_biosamples import build_biosamples, MIN_CONF


def main(L):
    att = pd.read_parquet(f"{L}/stage5_attrs_fielded.parquet").rename(columns={"sample_key": "sample_acc"})
    att["acc_resolved"] = att["sample_acc"]
    old = pd.read_parquet(os.path.join(HERE, "..", "data", "inputs", "registry", "registry_pairs_normalised.parquet"))
    new = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(f"{L}/stage5/pairs_out/*.parquet"))], ignore_index=True)
    new = new[(new.status == "ok") & ~new.null_output.astype(bool)].copy()
    cnt = att.groupby(["field", "attr_key_norm", "attr_value"]).sample_acc.nunique().rename("n_samples").reset_index()
    new = new.drop(columns=[c for c in ("n_samples",) if c in new.columns]).merge(cnt, on=["field", "attr_key_norm", "attr_value"], how="left")
    cols = ["field", "attr_key_norm", "attr_value", "n_samples", "body_site_code", "life_stage", "age_days", "sex", "country_iso2",
            "confidence", "null_output"]
    norm = pd.concat([old[[c for c in cols if c in old.columns]], new[[c for c in cols if c in new.columns]]], ignore_index=True)
    norm = norm.sort_values("confidence", ascending=False).drop_duplicates(["field", "attr_key_norm", "attr_value"])
    bios = build_biosamples(att, norm)
    # health condition from the new map
    dz = att[(att.field == "disease") & ~att.is_placeholder.astype(bool)].merge(
        new[new.field == "disease"][["attr_key_norm", "attr_value", "condition_code", "confidence", "n_samples"]],
        on=["attr_key_norm", "attr_value"], how="inner")
    dz = dz[dz.condition_code.notna() & (dz.confidence >= MIN_CONF)]
    dz = dz.sort_values(["sample_acc", "confidence", "n_samples"], ascending=[True, False, False]).drop_duplicates("sample_acc")
    bios["condition_code"] = bios.sample_accession.map(dz.set_index("sample_acc").condition_code)
    os.makedirs(f"{L}/stage5", exist_ok=True)
    bios.to_parquet(f"{L}/stage5/sample_values.parquet", index=False)
    norm.to_parquet(f"{L}/stage5/pairs_normalised_combined.parquet", index=False)
    real = lambda c, bad=(): int((bios[c].notna() & ~bios[c].isin(bad)).sum())
    print(json.dumps({"samples": len(bios), "projects": int(bios.study_accession.nunique()),
                      "body_site": real("body_site_code", ("unknown_site",)), "life_stage": real("life_stage", ("unknown_age",)),
                      "age_days": real("age_days"), "sex": int(bios.sex.isin(["female", "male"]).sum()),
                      "country": real("country_iso2"), "collection_year": real("collection_year"), "condition": real("condition_code"),
                      "body_sites": bios.body_site_code.value_counts().head(12).to_dict(),
                      "conditions": bios.condition_code.value_counts().head(12).to_dict()}))


if __name__ == "__main__":
    main(sys.argv[1])
