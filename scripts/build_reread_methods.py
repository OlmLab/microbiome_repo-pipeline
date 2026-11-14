#!/usr/bin/env python3
"""Per-project methods facts from the paper re-read (scripts/reread_passes.py), with controlled names (R2026.40).

Inputs
  --facts   reread_facts.parquet (study_accession, fact, value_raw, quote, source, …): extraction_kit, library_kit, platform,
            sequencing_facility as written in the project's own paper, each with the sentence it was read from
  config/methods_names/<fact>_map.csv   value_raw -> controlled name (made by Opus 5 readers over the distinct strings;
            rows with a mapping_note are provisional). A string that is not in a map keeps its wording and gets no controlled name.
  --runs    catalog_runs.parquet of the previous build (study_accession, instrument_model): the archive's instrument models
Output (one row per project) --out reread_methods.parquet:
  extraction_kit, library_kit, platform           as written in the paper
  extraction_kit_norm, library_kit_norm, platform_norm   controlled names (' | ' between several)
  platform_vs_archive    agree | paper_less_specific | differ | not_compared
  sequencing_facility, sequencing_facility_norm, sequencing_facility_country, sequencing_facility_type
  methods_evidence       JSON {fact: {"quote": …, "source": …}}
Prints counts only.
"""
import argparse, json, os, re, sys
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACTS = ["extraction_kit", "library_kit", "platform", "sequencing_facility"]
NORM_COL = {"extraction_kit": "extraction_kit_norm", "library_kit": "library_kit_norm", "platform": "platform_norm", "sequencing_facility": "facility_norm"}


def model_key(m):
    """one key for names of the same instrument (archive spelling, maker prefix, Dx variants, MGI renamings)."""
    k = re.sub(r"\s+", " ", str(m).lower().replace("illumina ", "").strip())
    k = re.sub(r"\s?dx$", "", k)
    k = {"hiseq x ten": "hiseq x", "hiseq x five": "hiseq x", "novaseq x plus": "novaseq x", "mgiseq-2000": "dnbseq-g400", "mgiseq-2000rs": "dnbseq-g400",
         "mgiseq-200": "dnbseq-g50", "mgiseq-t7": "dnbseq-t7", "nextseq cn500": "nextseq 500", "nextseq cn550": "nextseq 550"}.get(k, k)
    return k


FAMILY = [(r"novaseq", r"novaseq"), (r"hiseq", r"hiseq"), (r"nextseq", r"nextseq"), (r"miseq", r"miseq"),
          (r"mgi|bgi|dnbseq", r"dnbseq|bgiseq|mgiseq"), (r"nanopore", r"minion|gridion|promethion|nanopore"), (r"pacbio", r"sequel|revio|pacbio|rs ii"),
          (r"ion torrent", r"ion |proton|pgm|genestudio"), (r"454", r"454"), (r"illumina", r"illumina|hiseq|novaseq|nextseq|miseq|miniseq|genome analyzer|iseq")]


def compare(norm, archive_models):
    """paper's instruments (controlled names) against the archive's instrument models of the project's runs."""
    if not isinstance(norm, str) or not norm or norm == "unclear":
        return "not_compared"
    arch = {model_key(m) for m in archive_models if isinstance(m, str) and m.lower() not in ("unspecified", "not specified", "", "none")}
    if not arch:
        return "not_compared"
    named = [n.strip() for n in norm.split("|") if n.strip()]
    specific = {model_key(n) for n in named if "not specified" not in n}
    if specific & arch:
        return "agree"
    for n in named:
        if "not specified" in n:
            low = n.lower()
            for fam_pat, model_pat in FAMILY:
                if re.search(fam_pat, low):
                    if any(re.search(model_pat, a) for a in arch):
                        return "paper_less_specific"
                    break
    return "differ"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", required=True); ap.add_argument("--runs", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--maps", default=os.path.join(ROOT, "config", "methods_names"))
    a = ap.parse_args(argv)
    fa = pd.read_parquet(a.facts)
    fa = fa[fa.fact.isin(FACTS) & fa.value_raw.notna()]
    out, stats = {}, {}
    ev = {}
    for f in FACTS:
        g = fa[fa.fact == f].copy()
        mp = pd.read_csv(os.path.join(a.maps, f"{f}_map.csv")).drop_duplicates("value_raw").set_index("value_raw")
        nc = NORM_COL[f]
        g["norm"] = g.value_raw.map(mp[nc]).where(lambda s: s.notna() & (s != "unclear"))
        stats[f] = dict(projects=int(g.study_accession.nunique()), with_controlled_name=int(g[g.norm.notna()].study_accession.nunique()),
                        strings_not_in_map=int((~g.value_raw.isin(mp.index)).sum()), provisional=int(g.value_raw.map(mp.get("mapping_note", pd.Series(dtype=str))).notna().sum()))
        join = lambda s: " | ".join(dict.fromkeys(x for v in s.dropna() for x in str(v).split(" | ") if x)) or None
        col = f
        agg = g.groupby("study_accession").agg(**{col: ("value_raw", lambda s: "; ".join(dict.fromkeys(map(str, s)))), f"{col}_norm": ("norm", join)})
        if f == "sequencing_facility":
            g["country"] = g.value_raw.map(mp["facility_country"]); g["ftype"] = g.value_raw.map(mp["facility_type"])
            agg["sequencing_facility_country"] = g.groupby("study_accession").country.agg(lambda s: ";".join(dict.fromkeys(str(x) for x in s.dropna())) or None)
            agg["sequencing_facility_type"] = g.groupby("study_accession").ftype.agg(lambda s: ";".join(dict.fromkeys(str(x) for x in s.dropna())) or None)
        out[f] = agg
        for r in g.drop_duplicates("study_accession").itertuples(index=False):
            ev.setdefault(r.study_accession, {})[f] = {"quote": r.quote, "source": r.source}
    df = pd.concat(out.values(), axis=1)
    runs = pd.read_parquet(a.runs, columns=["study_accession", "instrument_model"], filters=[("study_accession", "in", list(df.index))])
    am = runs.groupby("study_accession").instrument_model.agg(lambda s: sorted(set(map(str, s.dropna()))))
    df["platform_vs_archive"] = [compare(n, am.get(acc, [])) if isinstance(n, str) else None for acc, n in zip(df.index, df.platform_norm)]
    df.loc[df.platform.isna(), "platform_vs_archive"] = None
    df["methods_evidence"] = [json.dumps(ev.get(acc, {}), ensure_ascii=False, sort_keys=True) for acc in df.index]
    df = df.reset_index().rename(columns={"index": "study_accession"})
    df.to_parquet(a.out, index=False)
    stats["projects"] = len(df)
    stats["platform_vs_archive"] = df.platform_vs_archive.value_counts(dropna=False).to_dict()
    print(json.dumps(stats, default=str))


if __name__ == "__main__":
    main()
