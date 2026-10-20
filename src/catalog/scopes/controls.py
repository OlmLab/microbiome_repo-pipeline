"""Control and non-specimen samples inside human projects (R2026.26, owner request 2026-10-20).

Deterministic detection from the archive's run records (sample_title | library_name | isolation_source and scientific_name):
  negative_control      blanks, no-template / extraction / kit / reagent / water controls
  mock_community        sequenced community standards (ZymoBIOMICS D63xx, ATCC MSA-100x, NIST, 'mock community')
  positive_control      'positive control' samples (spiked or reference stool, DNA standards)
  synthetic_community   'synthetic metagenome' samples without a mock cue (lab-assembled defined communities)
Samples that are synthetic/defined communities from an environmental source (rhizosphere, soil ...) are not human-associated and are
returned with action='exclude'. 'mock FMT', sham and placebo arms are human specimens and never match.
"""
import re
import pandas as pd

NEG = r"\bblanks?\b|\bblank[\W_]|negative[\W_]*control|(?<![a-z0-9])ntc(?![a-z])|\bneg[\W_]*(?:ctrl|control)|no[\W_]*template|extraction[\W_]*(?:control|blank|negative)|kit[\W_]*(?:control|blank)|reagent[\W_]*(?:control|blank)|water[\W_]*(?:control|blank)|\bneg[\W_]*ext"
MOCK = r"mock[\W_]*(?:comm|microbial|dna|standard|sample|\d)|zymo\w*[\W_]*(?:mock|standard|comm|d63)|zymomock|community[\W_]*(?:dna[\W_]*)?standard|\bd63(?:00|05|06|10|11|31)\b|atcc[\W_]*msa|\bmsa[\W_]*-?100\d|\bnist[\W_]*(?:stool|rm)|\bmbarc|\bmock\b"
POS = r"positive[\W_]*control|\bpos[\W_]*(?:ctrl|control)"
NOT_CONTROL = r"mock[\W_]*(?:fmt|transplant|treat|infect|surgery|procedure)|\bsham\b|placebo"
ENV_SOURCE = r"rhizosphere|\bsoil\b|sediment|seawater|freshwater|wastewater|sludge|plant|root"
SAMPLE_TYPES = ["specimen", "negative_control", "mock_community", "positive_control", "synthetic_community"]


def detect_controls(runs: pd.DataFrame) -> pd.DataFrame:
    """runs: sample_accession, study_accession, sample_title, library_name, isolation_source, scientific_name (one row per run).
    Returns one row per flagged sample: sample_accession, study_accession, sample_type, action (mark|exclude), evidence."""
    r = runs.drop_duplicates("sample_accession").copy()
    txt = (r.sample_title.fillna("").astype(str) + " | " + r.library_name.fillna("").astype(str) + " | " + r.isolation_source.fillna("").astype(str)).str.lower()
    sn = r.scientific_name.fillna("").astype(str).str.lower()
    syn = sn.str.contains("synthetic")
    t = pd.Series(pd.NA, index=r.index, dtype="object")
    t[syn] = "synthetic_community"
    t[txt.str.contains(POS)] = "positive_control"
    t[txt.str.contains(MOCK)] = "mock_community"
    t[txt.str.contains(NEG)] = "negative_control"
    t[txt.str.contains(NOT_CONTROL) & ~syn] = pd.NA
    r["sample_type"] = t
    r["evidence"] = txt.str.slice(0, 160) + " | " + sn
    r = r[r.sample_type.notna()].copy()
    env = r.isolation_source.fillna("").astype(str).str.lower().str.contains(ENV_SOURCE)
    r["action"] = "mark"
    r.loc[(r.sample_type == "synthetic_community") & env, "action"] = "exclude"
    return r[["sample_accession", "study_accession", "sample_type", "action", "evidence"]].reset_index(drop=True)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry-runs", required=True)
    ap.add_argument("--studies", required=True, help="parquet with study_accession of the scope")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    acc = set(pd.read_parquet(a.studies, columns=["study_accession"]).study_accession)
    rr = pd.read_parquet(a.registry_runs, columns=["study_accession", "sample_accession", "sample_title", "library_name", "isolation_source", "scientific_name"])
    d = detect_controls(rr[rr.study_accession.isin(acc)])
    d.to_csv(a.out, index=False)
    print(d.groupby(["sample_type", "action"]).size().to_dict(), d.study_accession.nunique(), "projects")
