"""R2026.40: the explorers can load DuckDB-WASM from the site itself; the switch stays off (and .wasm stays out of releases)
until the owner confirms that the CU server serves .wasm files."""
import json, re
from pathlib import Path
import yaml

REPO = Path(__file__).resolve().parents[1]
GEN = REPO / 'site_generator' / 'gen'


def test_switch_is_off_and_wasm_is_not_an_allowed_site_file_type():
    hosting = yaml.safe_load((REPO / 'config' / 'site.yaml').read_text())['hosting']
    assert hosting['duckdb_self_hosted'] is False
    assert set(hosting['duckdb_self_hosted_builds']) <= {'eh', 'mvp'} and 'eh' in hosting['duckdb_self_hosted_builds']
    allowed = re.search(r"ALLOWED = \{([^}]*)\}", (GEN / 'check_file_types.py').read_text()).group(1)
    assert "'wasm'" not in allowed and "'mjs'" not in allowed
    assert not list((GEN / 'static').rglob('*.wasm'))


def test_both_explorers_use_the_shared_boot_module():
    boot = (GEN / 'static' / 'duckdb_boot.js').read_text()
    assert 'custom_extension_repository' in boot and 'getJsDelivrBundles' in boot and "DUCKDB_VERSION = '1.29.0'" in boot
    for fn in ['explorer.js', 'registry_explorer.js']:
        s = (GEN / 'static' / fn).read_text()
        assert "import { bootDuckDB, DUCKDB_SOURCE } from './duckdb_boot.js';" in s and 'await bootDuckDB()' in s
        assert 'jsdelivr' not in s
    for tn in ['explorer.html', 'registry.html']:
        t = (GEN / 'templates' / tn).read_text()
        assert 'site.duckdb_self_hosted' in t and "window.SITE_DUCKDB={base:'../static/vendor/duckdb/'" in t


def test_vendor_inputs_are_pinned_to_the_version_the_explorers_name():
    pkg = json.loads((REPO / 'site_generator' / 'vendor_src' / 'duckdb' / 'package.json').read_text())
    assert pkg['dependencies'] == {'@duckdb/duckdb-wasm': '1.29.0'}
    assert re.fullmatch(r'\d+\.\d+\.\d+', pkg['devDependencies']['esbuild'])
    lock = json.loads((REPO / 'site_generator' / 'vendor_src' / 'duckdb' / 'package-lock.json').read_text())
    assert lock['packages']['node_modules/@duckdb/duckdb-wasm']['version'] == '1.29.0'
    assert 'integrity' in lock['packages']['node_modules/@duckdb/duckdb-wasm']
