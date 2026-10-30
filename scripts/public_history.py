"""public_history.py — keep the package's public history to the releases from `public_history_from` (config/releases.yaml) on.
Release notes of earlier releases are removed from the package dir, the CHANGELOG keeps only the entries of those releases'
package versions, and the README's historical infant description is dropped. Earlier versions stay archived with their own
GitHub / Zenodo releases.  usage: python scripts/public_history.py PKG_DIR [--after-docs]
"""
import os, re, sys
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))

# README sections that describe the infant extension tables get headings that say so (no version history in headings)
README_HEADINGS = [
    (r"^## Age scope and roles(?: \(new in v[\d.]+\))?\s*$", "## Infant extension: age scope and roles"),
    (r"^## Known limitations\s*$", "## Infant extension: known limitations"),
    (r"^## Provenance\s*$", "## Provenance and licence"),
    (r"^## Added in v[\d.]+: Sandpiper community profiles and author index\s*$", "## Infant extension: Sandpiper community profiles and author index"),
]
# previous-package table names → their catalog_* names (text references left in carried documentation)
LEGACY_NAMES = [
    (r"`gut_([a-z_]+\.(?:parquet|csv))`", r"`catalog_\1`"),
    (r"`human_samples\.parquet`", "`catalog_sample_metadata_wide.parquet`"),
    (r"`human_(studies|runs|exposure_items)\.parquet`", r"`catalog_\1.parquet`"),
    (r"`GUT_SANDPIPER_REPORT\.md`", "`CATALOG_SANDPIPER_REPORT.md`"),
    (r"`gut_(studies|runs|sample_metadata_wide|sample_determinations|relatedness|relations|metadata_contradictions)`", r"`catalog_\1`"),
]


def neutral_text(s: str) -> str:
    """Version / release annotations out of headings and parentheticals; previous-package table names → catalog_* names."""
    s = re.sub(r"(?m)^(#{2,4} .*?) \((?:R\d{4}\.\d+, package [\d.]+|added v[\d.]+|new in v[\d.]+|R\d{4}\.\d+)\)[ \t]*$", r"\1", s)
    s = re.sub(r"(?m)^(### .*?) — added R\d{4}\.\d+[ \t]*$", r"\1", s)
    for _ in range(2):  # heading parentheticals that carry a version note, e.g. "(v1.2 clarification)", "(v2, v1.2.1)", "(documented v1.2.1)"
        s = re.sub(r"(?m)^(#{2,4} [^\n]*?) \([^()\n]*\bv\d+\.\d+[^()\n]*\)", r"\1", s)
    s = re.sub(r"(?m)^(## Sample unit) \(added after [^)]*\)", r"\1", s)
    s = re.sub(r" \(v\d+\.\d+\.\d+\)", "", s)
    s = re.sub(r" \(R\d{4}\.\d+\)", "", s)
    for a, b in LEGACY_NAMES:
        s = re.sub(a, b, s)
    return s


COUNT_COLS = ["n_studies_included", "n_samples", "n_catalog_scope", "n_determinations_current"]


def fix_releases_csv(pkg, keep):
    """releases.csv: the newest row counts the catalog tables (current rows); rows of releases before public_history_from keep
    their identifiers (tags, DOI) only — their counts and notes files belong to the archived packages."""
    import pandas as pd
    import pyarrow.parquet as pq
    p = os.path.join(pkg, "releases.csv")
    if not os.path.exists(p) or not os.path.exists(os.path.join(pkg, "catalog_studies.parquet")):
        return
    reg = pd.read_csv(p, dtype=str, keep_default_na=False)
    old = ~reg.release_id.isin(keep)
    for c in COUNT_COLS + ["notes_file"]:
        if c in reg.columns:
            reg.loc[old, c] = ""

    def cur(fn, cols):
        have = pq.ParquetFile(os.path.join(pkg, fn)).schema.names
        d = pd.read_parquet(os.path.join(pkg, fn), columns=[c for c in cols if c in have] + (["release_retired"] if "release_retired" in have else []))
        return d[d.release_retired.isna()] if "release_retired" in d.columns else d
    st = cur("catalog_studies.parquet", ["study_accession"])
    sw = cur("catalog_sample_metadata_wide.parquet", ["sample_key", "infant_scope"])
    t = pq.read_table(os.path.join(pkg, "catalog_sample_determinations.parquet"), columns=["release_retired"])
    counts = dict(n_studies_included=len(st), n_samples=len(sw), n_determinations_current=t.column(0).null_count,
                  n_catalog_scope=int(sw["infant_scope"].fillna(False).astype(bool).sum()) if "infant_scope" in sw.columns else "")
    i = reg.index[-1]   # bitemporal writes the release being cut as the newest row
    for c, v in counts.items():
        reg.loc[i, c] = str(v)
    reg.to_csv(p, index=False)


def main(pkg, after_docs=False):
    cfg = yaml.safe_load(open(os.path.join(HERE, "..", "config", "releases.yaml"), encoding="utf-8"))
    first = cfg.get("public_history_from")
    if not first:
        print("public_history_from not set: nothing removed"); return
    rel = [r["release_id"] for r in cfg["releases"]]
    keep = set(rel[rel.index(first):])
    keep_versions = {str(r["package_version"]) for r in cfg["releases"] if r["release_id"] in keep}
    n_rm = 0
    for f in sorted(os.listdir(pkg)):
        m = re.match(r"RELEASE_NOTES_(R\d{4}\.\d+)\.md$", f)
        if m and m.group(1) not in keep:
            os.remove(os.path.join(pkg, f)); n_rm += 1
    fix_releases_csv(pkg, keep)
    cp = os.path.join(pkg, "CHANGELOG.md")
    if os.path.exists(cp):
        s = open(cp, encoding="utf-8").read()
        parts = re.split(r"(?m)^(?=## )", s)
        kept = [p for p in parts if p.startswith("## ") and any(re.match(rf"## v?{re.escape(v)}\b", p) for v in keep_versions)]
        head = "# Changelog\n\nEarlier versions are archived with their own releases (GitHub / Zenodo).\n\n"
        open(cp, "w", encoding="utf-8").write(head + "".join(kept))
    # tables renamed / folded in 1.26.0: their old sections and README rows go, package_docs appends the catalog_* ones
    legacy = r"(?:gut_(?:studies|sample_metadata_wide|sample_determinations(?:_conflicts)?|runs|relatedness|relations|metadata_contradictions|pass_coverage|sandpiper_[a-z_]+)\.(?:parquet|csv)|human_(?:studies|samples|runs|exposure_items)\.parquet|GUT_SANDPIPER_REPORT\.md)"
    dp = os.path.join(pkg, "DATA_DICTIONARY.md")
    if os.path.exists(dp):
        d = open(dp, encoding="utf-8").read()
        d = re.sub(r"(?ms)^### " + legacy + r".*?(?=^### |^## |\Z)", "", d)
        d = d.replace("The curated infant catalog is the scope\n`infant_gut` inside this registry (`in_infant_catalog` mirrors", "`in_infant_catalog` mirrors")
        open(dp, "w", encoding="utf-8").write(d)
    rp0 = os.path.join(pkg, "README.md")
    if os.path.exists(rp0):
        r0 = open(rp0, encoding="utf-8").read()
        r0 = "\n".join(l for l in r0.split("\n") if not re.match(r"^\| `" + legacy + "`", l))
        open(rp0, "w", encoding="utf-8").write(r0)
    if after_docs:
        rp = os.path.join(pkg, "README.md")
        if os.path.exists(rp):
            s = open(rp, encoding="utf-8").read()
            s2 = re.sub(r"(?ms)^#{2,3} Infant extension \(historical description\).*?(?=^## |\Z)", "", s)
            # Files table: the catalog tables first; the infant tables are the extension, not the starting point
            m = re.search(r"(?ms)^## Files\n(\| File .*?\n\|---.*?\n)(.*?)(?=\n\n|\n## |\Z)", s2)
            if m:
                rows = m.group(2).split("\n")
                cat = [r for r in rows if r.startswith("| `catalog_") or r.startswith("| `CATALOG_")]
                ORDER = ["catalog_studies.parquet", "catalog_sample_metadata_wide.parquet", "catalog_sample_determinations.parquet", "catalog_runs.parquet",
                         "catalog_study_papers.parquet", "catalog_exposure_items.parquet", "catalog_sample_determinations_conflicts.parquet"]
                cat.sort(key=lambda r: next((i for i, f in enumerate(ORDER) if r.startswith(f"| `{f}`")), len(ORDER)))
                other = [r.replace("**Start here.** ", "Infant extension: ", 1) for r in rows if r not in cat]
                s2 = s2[:m.start(2)] + "\n".join(cat + other) + s2[m.end(2):]
            s2 = neutral_text(s2)
            # Files-table rows: no release / version tags at the end of the description
            s2 = re.sub(r"(?m)^(\| .*?) — (?:R\d{4}\.\d+|v\d+\.\d+(?:\.\d+)?) \|[ \t]*$", r"\1 |", s2)
            for a, b in README_HEADINGS:
                s2 = re.sub(a, b, s2, flags=re.M)
            s2 = s2.replace("This is the first **numbered catalog release** (`R<YYYY>.<n>`; `VERSION.json.release_id`).",
                            "Releases are numbered `R<YYYY>.<n>` (`VERSION.json.release_id`).")
            s2 = re.sub(r"Built \d{4}-\d{2}-\d{2} → \d{4}-\d{2}-\d{2} with ", "Built with ", s2)
            s2 = s2.replace("The full run × taxon profiles (`sandpiper_profiles.parquet`, ~100 MB) are a Release asset, not in this package.",
                            "Genus- and species-level long tables for every profiled catalog sample are Release assets "
                            "(`catalog_sandpiper_sample_genus_v<version>_part*.parquet`, `catalog_sandpiper_sample_species_v<version>_part*.parquet`).")
            open(rp, "w", encoding="utf-8").write(s2)
        sp_rep = os.path.join(pkg, "CATALOG_SANDPIPER_REPORT.md")
        if os.path.exists(sp_rep):
            t = open(sp_rep, encoding="utf-8").read().replace("(listed in `sp/gut_runs_profiled` join;", "(listed in the run-to-sample join;")
            open(sp_rep, "w", encoding="utf-8").write(neutral_text(t))
        if os.path.exists(dp):
            d = neutral_text(open(dp, encoding="utf-8").read())
            d = d.replace("Historical\nrows carry only the counts the CHANGELOGs state (empty otherwise — never invented).",
                          f"Counts are over the current rows of\nthe catalog tables: projects (`n_studies_included`), samples (`n_samples`), samples with `infant_scope` (`n_catalog_scope`) and\n"
                          f"sample × field values (`n_determinations_current`). Rows of releases before {first} carry identifiers only (tags, DOI); those\npackages are archived with their own releases.")
            if "\n## Catalog tier" not in d and "\n### catalog_" in d:
                i = d.index("\n### catalog_")
                d = d[:i] + "\n## Catalog tier\nOne row per project / sample / sample × field / run of the catalog (every body site, all ages, DNA and RNA data sets).\n" + d[i:]
            open(dp, "w", encoding="utf-8").write(d)
    print(f"public history from {first}: {n_rm} earlier release-notes files removed; changelog entries kept for {sorted(keep_versions)}")


if __name__ == "__main__":
    main(sys.argv[1], "--after-docs" in sys.argv)
