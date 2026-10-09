"""Release check (R2026.20 owner audit of PRJEB120967): every DNA BioSample of a catalog project must be accounted for —
either a catalog sample (gut_sample_metadata_wide), an exclusion (config/sample_exclusions.csv), or at least harvested
(registry_biosample_attributes; e.g. non-gut body sites). Unaccounted BioSamples were never downloaded, so they silently
drop out of the catalog. Writes the list to --out and exits 1 when a catalog project has ZERO samples but unharvested
BioSamples, or when the total exceeds --max."""
import argparse
import sys
import pandas as pd
import pyarrow.dataset as pds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wide", required=True); ap.add_argument("--studies", required=True); ap.add_argument("--runs", required=True)
    ap.add_argument("--attributes", required=True); ap.add_argument("--exclusions", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--max", type=int, default=200)
    a = ap.parse_args()
    st = pd.read_parquet(a.studies, columns=["study_accession", "release_retired"]); st = st[st.release_retired.isna()]
    acc = sorted(set(st.study_accession))
    w = pd.read_parquet(a.wide, columns=["sample_key", "biosample_accession", "study_accession", "release_retired"]); w = w[w.release_retired.isna()]
    rr = pd.read_parquet(a.runs, columns=["study_accession", "sample_accession", "library_source", "library_strategy"], filters=[("study_accession", "in", acc)])
    rr = rr[rr.library_source.isin(["METAGENOMIC", "GENOMIC"]) & ~rr.library_strategy.isin(["AMPLICON", "RNA-Seq"]) & rr.sample_accession.fillna("").ne("")]
    have = set(pds.dataset(a.attributes).to_table(columns=["sample_acc"]).column(0).to_pylist())
    ex = set(pd.read_csv(a.exclusions).biosample_accession)
    known = have | ex | set(w.sample_key) | set(w.biosample_accession.dropna())
    u = rr[~rr.sample_accession.isin(known)].drop_duplicates(["study_accession", "sample_accession"])
    n = w.groupby("study_accession").size()
    t = u.groupby("study_accession").size().rename("unharvested").to_frame().join(n.rename("catalog_samples")).fillna(0).astype(int)
    u.to_csv(a.out, index=False)
    zero = t[(t.catalog_samples == 0)]
    print(f"unharvested DNA BioSamples of catalog projects: {len(u)} in {len(t)} projects; projects with 0 catalog samples: {len(zero)} {list(zero.index)[:10]}")
    sys.exit(1 if (len(zero) or len(u) > a.max) else 0)


if __name__ == "__main__":
    main()
