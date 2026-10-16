"""A2: two site builds from the same package must be byte-identical.

Fixed by generator v2 (2026-09-26): every *.csv.gz is written with gzip mtime=0, the build date comes from VERSION.json,
iteration is over sorted keys and JSON is dumped with sort_keys. Needs an unpacked package under data/inputs/data_package
(or CATALOG_PACKAGE_DIR); skipped otherwise.
"""
import hashlib
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(REPO, "site_generator", "gen", "build_site.py")
# R2026.1: the generator needs a package with release columns (>= 1.3.0); prefer the freshly built build/package,
# then the unpacked input package (skipped below if it predates 1.3.0 — no releases.csv)
_CANDIDATES = [os.environ.get("CATALOG_PACKAGE_DIR"), os.path.join(REPO, "build", "package"), os.path.join(REPO, "data", "inputs", "data_package")]
PKG = next((c for c in _CANDIDATES if c and os.path.exists(os.path.join(c, "releases.csv"))), _CANDIDATES[-1])


def _tree_hashes(root):
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            out[os.path.relpath(p, root)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def _build(out):
    cmd = [sys.executable, GEN, "--package", PKG, "--out", out, "--base-url", "https://microbiomerepo.com/"]
    subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=REPO)


def test_two_builds_are_byte_identical(tmp_path):
    if not os.path.isdir(PKG) or not os.path.exists(os.path.join(PKG, "sample_metadata_wide.parquet")):
        pytest.skip(f"no unpacked package at {PKG}")
    if not os.path.exists(os.path.join(PKG, "releases.csv")):
        pytest.skip(f"package at {PKG} predates 1.3.0 (no releases.csv); run make release first")
    a, b = tmp_path / "a", tmp_path / "b"
    _build(str(a))
    _build(str(b))
    ha, hb = _tree_hashes(a), _tree_hashes(b)
    assert set(ha) == set(hb), "file sets differ"
    diff = sorted(k for k in ha if ha[k] != hb[k])
    assert not diff, f"{len(diff)} files differ between builds, e.g. {diff[:5]}"
