#!/usr/bin/env python3
"""map_carter_names.py - Carter et al. 2023 species names to GTDB R232 species.

For every species name printed in Carter's VANISH/BloSSUM lists, take the genomes that carry that
species name in the GTDB release Carter used and report the R232 species of those genomes: the
majority R232 name, its share, and a flag when the Carter species is split (several R232 species) or
merged (several Carter species land on one R232 species).

usage:
  python map_carter_names.py --carter-lists FILE --gtdb-dir DIR --out FILE
                             [--carter-release r207] [--background FILE]

--gtdb-dir holds bac120_taxonomy_r<rel>.tsv.gz (genome accession <tab> lineage).
With --carter-release auto (default) the release whose species names cover Carter's list best is
chosen and reported; --background reads the release from a background.json written by the
literature sub-agent ("gtdb_release" key) if present.
"""
import argparse, gzip, json, os, re
import pandas as pd
from collections import Counter

RELEASES = ["r95", "r202", "r207", "r214", "r220", "r226", "r232"]


def read_tax(gtdb_dir, rel):
    f = os.path.join(gtdb_dir, "bac120_taxonomy_%s.tsv.gz" % rel)
    acc, sp = [], []
    with gzip.open(f, "rt") as fh:
        for line in fh:
            a, lin = line.rstrip("\n").split("\t")
            acc.append(a)
            sp.append(lin.rsplit(";s__", 1)[-1].strip())
    return pd.DataFrame({"accession": acc, "species": sp})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--carter-lists", required=True)
    ap.add_argument("--gtdb-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--carter-release", default="auto")
    ap.add_argument("--background", default=None)
    a = ap.parse_args()

    cl = pd.read_csv(a.carter_lists)
    cl["name"] = cl.species_name_as_printed.astype(str).str.replace("^s__", "", regex=True).str.strip()
    names = sorted(set(cl.name))

    rel = a.carter_release
    if a.background and os.path.exists(a.background):
        try:
            bg = json.load(open(a.background))
            r = str(bg.get("gtdb_release", "")).lower()
            m = re.search(r"r?(\d{3})", r)
            if m:
                rel = "r" + m.group(1)
        except Exception:
            pass
    cover = {}
    if rel == "auto":
        for r in RELEASES:
            t = read_tax(a.gtdb_dir, r)
            cover[r] = int(len(set(t.species) & set(names)))
        rel = max(cover, key=lambda k: (cover[k], -RELEASES.index(k)))

    src = read_tax(a.gtdb_dir, rel)
    tgt = read_tax(a.gtdb_dir, "r232").set_index("accession").species
    src = src[src.species.isin(names)]
    rows = []
    for nm, g in src.groupby("species"):
        mapped = tgt.reindex(g.accession).dropna()
        c = Counter(mapped)
        if not c:
            rows.append(dict(species_name_as_printed=nm, n_genomes_source=len(g), n_genomes_in_r232=0,
                             r232_species=None, majority_share=None, n_r232_species=0, flag="absent_in_r232"))
            continue
        top, k = c.most_common(1)[0]
        rows.append(dict(species_name_as_printed=nm, n_genomes_source=len(g), n_genomes_in_r232=int(len(mapped)),
                         r232_species="s__" + top if not str(top).startswith("s__") else str(top),
                         majority_share=round(k / len(mapped), 4), n_r232_species=len(c),
                         flag="split" if len(c) > 1 else "one_to_one"))
    mp = pd.DataFrame(rows)
    miss = sorted(set(names) - set(mp.species_name_as_printed))
    if miss:
        # fallback: a name absent from Carter's release but still a current R232 species maps to itself
        r232names = set(read_tax(a.gtdb_dir, "r232").species)
        mrows = []
        for nm in miss:
            direct = nm in r232names
            mrows.append(dict(species_name_as_printed=nm, n_genomes_source=0, n_genomes_in_r232=0,
                              r232_species=("s__" + nm) if direct else None,
                              majority_share=1.0 if direct else None,
                              n_r232_species=1 if direct else 0,
                              flag="name_found_directly_in_r232" if direct else "name_absent_in_" + rel))
        mp = pd.concat([mp, pd.DataFrame(mrows)], ignore_index=True)
    dup = mp.r232_species.dropna().duplicated(keep=False)
    mp.loc[dup[dup].index, "flag"] = mp.loc[dup[dup].index, "flag"] + "+merged"
    mp = cl[["list", "name"]].drop_duplicates().rename(columns={"name": "species_name_as_printed"}).merge(
        mp, on="species_name_as_printed", how="left")
    mp["carter_release"] = rel
    mp.to_csv(a.out, index=False)
    summ = {"carter_release_used": rel, "release_name_coverage": cover, "n_carter_species": len(names),
            "n_mapped": int(mp.r232_species.notna().sum()),
            "n_one_to_one": int(mp.flag.fillna("").str.startswith("one_to_one").sum()),
            "n_split": int(mp.flag.fillna("").str.contains("split").sum()),
            "n_merged": int(mp.flag.fillna("").str.contains("merged").sum()),
            "n_unmapped": int(mp.r232_species.isna().sum()),
            "n_direct_r232": int(mp.flag.fillna("").str.contains("directly_in_r232").sum()),
            "n_placeholder_names": int(mp.species_name_as_printed.str.endswith(" sp.").sum()),
            "rows_by_list": {k: int(v) for k, v in cl.groupby("list").size().items()},
            "rows_without_species_name_by_list": {
                k: int(v) for k, v in
                cl.assign(ph=cl.name.str.endswith(" sp.")).groupby("list").ph.sum().items()},
            "by_list": {k: int(v) for k, v in mp.groupby("list").size().items()}}
    json.dump(summ, open(os.path.splitext(a.out)[0] + "_summary.json", "w"), indent=1)
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
