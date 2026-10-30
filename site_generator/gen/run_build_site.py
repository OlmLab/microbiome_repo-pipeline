"""Wrapper that puts src/ on sys.path and runs build_site.main() — for environments where PYTHONPATH cannot be set
as a command prefix. Arguments are passed straight through to build_site.py.

usage: python site_generator/gen/run_build_site.py --package ... --out ... [--human-dir ...]
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "site_generator", "gen"))
runpy.run_path(os.path.join(ROOT, "site_generator", "gen", "build_site.py"), run_name="__main__")
