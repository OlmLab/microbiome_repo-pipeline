#!/usr/bin/env python
"""Rebuild the Taxon map payload (taxa.json, matrix_<rank>.bin, countries.json, studies.json, country_hdi.csv) from the
profile tables of the release being built.

R2026.43: until this release the payload under data/inputs/gut/atlas was the one computed in September 2026 (package 1.12.0,
Sandpiper 2.0.0) and the page went on showing it under every later release. `make taxon-map` runs this script on the built
package and the current profile tables; run it in every release, before `make site` (docs/MONTHLY_RUN.md).

    python scripts/build_taxon_map.py --package build/package --sandpiper-dir data/inputs/catalog/sandpiper --out data/inputs/gut/atlas

Memory: about 3 GB (the two long tables are read dictionary-encoded).  No model call.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'site_generator' / 'gen'))
from pages import atlas  # noqa: E402


def _long(path: Path, name_col: str) -> pd.DataFrame:
    cols = ['sample_key', name_col, 'lineage', 'relabund']
    t = pq.read_table(path, columns=cols, read_dictionary=['sample_key', name_col, 'lineage'])
    df = t.to_pandas()
    for c in ('sample_key', name_col, 'lineage'):
        df[c] = df[c].astype(object)   # objects are shared with the dictionary: 8 bytes per cell
    return df


def _one(path: Path, col: str) -> str:
    vals = pq.read_table(path, columns=[col], read_dictionary=[col])[col].unique().to_pylist()
    vals = sorted({str(v) for v in vals if v is not None})
    if len(vals) != 1:
        raise SystemExit(f'{path.name}: expected one value of {col}, found {vals}')
    return vals[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--package', required=True, help='built data package directory (catalog_sample_metadata_wide.parquet, VERSION.json)')
    ap.add_argument('--sandpiper-dir', required=True, help='directory with gut_sandpiper_sample_{genus,species,summary}.parquet')
    ap.add_argument('--out', required=True, help='payload directory (ATLAS_DATA)')
    ap.add_argument('--n-species', type=int, default=500)
    a = ap.parse_args()
    pkg, sp, out = Path(a.package), Path(a.sandpiper_dir), Path(a.out)
    version = json.loads((pkg / 'VERSION.json').read_text())
    wide = pd.read_parquet(pkg / 'catalog_sample_metadata_wide.parquet', columns=['sample_key', 'country', 'release_retired'])
    wide = wide[wide.release_retired.isna()][['sample_key', 'country']]
    summary = pd.read_parquet(sp / 'gut_sandpiper_sample_summary.parquet')
    summary = summary[summary.sample_key.isin(set(wide.sample_key))]
    g_path, s_path = sp / 'gut_sandpiper_sample_genus.parquet', sp / 'gut_sandpiper_sample_species.parquet'
    tool = dict(sandpiper_version=_one(g_path, 'sandpiper_version'), zenodo_record=_one(g_path, 'zenodo_record'),
                taxonomy_db=_one(g_path, 'taxonomy_db'), taxonomy_version=_one(g_path, 'taxonomy_version'))
    for k, v in tool.items():
        if _one(s_path, k) != v:
            raise SystemExit(f'genus and species tables disagree on {k}')
    taxonomy = f"{tool['taxonomy_db']} {tool['taxonomy_version']} (Sandpiper {tool['sandpiper_version']}, Zenodo {tool['zenodo_record']})"
    genus_long, species_long = _long(g_path, 'genus'), _long(s_path, 'species')
    meta = atlas.precompute(genus_long, species_long, summary, wide, out, n_species=a.n_species, taxonomy=taxonomy,
                            generated_from=dict(package_version=version.get('package_version') or version.get('version'),
                                                release_id=version.get('release_id'), **tool))
    print(json.dumps({k: meta[k] for k in ('n_samples', 'n_studies', 'n_countries', 'n_samples_with_country', 'n_taxa', 'ranks_n', 'taxonomy', 'payload_bytes')}))


if __name__ == '__main__':
    main()
