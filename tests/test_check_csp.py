"""check_csp.py (R2026.37): passes a page that loads only what the CU server's policy allows, fails one that does not."""
import json, subprocess, sys
from pathlib import Path

GEN = Path(__file__).resolve().parents[1] / 'site_generator' / 'gen'


def _run(site):
    r = subprocess.run([sys.executable, str(GEN / 'check_csp.py'), str(site)], capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout)


def _site(tmp_path, page, js=''):
    (tmp_path / 'static').mkdir()
    (tmp_path / 'index.html').write_text(page)
    (tmp_path / 'static' / 'app.js').write_text(js)
    return tmp_path


def test_allowed_page_passes(tmp_path):
    page = ('<script src="static/app.js"></script><script src="https://cdn.jsdelivr.net/npm/x@1/x.js"></script>'
            '<script data-goatcounter="https://microbiome-repo.goatcounter.com/count" src="https://gc.zgo.at/count.js"></script>'
            '<link rel="stylesheet" href="static/site.css"><img src="https://zenodo.org/badge/1.svg"><img src="data:image/png;base64,AA==">'
            '<a href="https://example.org/">links are not loads</a>')
    code, rep = _run(_site(tmp_path, page, "const u = 'https://www.ebi.ac.uk/ena/browser/view/' + acc;"))
    assert code == 0 and rep['violations'] == 0, rep


def test_outside_loads_fail(tmp_path):
    page = ('<script src="https://unpkg.com/lib.js"></script><link rel="stylesheet" href="https://fonts.googleapis.com/css2">'
            '<img src="https://img.shields.io/badge.svg"><iframe src="https://www.youtube.com/embed/x"></iframe>'
            "<script>fetch('https://api.example.org/data.json')</script>")
    code, rep = _run(_site(tmp_path, page, "import('https://esm.sh/thing');"))
    assert code == 1
    urls = {v['url'] for v in rep['examples']}
    assert {'https://unpkg.com/lib.js', 'https://fonts.googleapis.com/css2', 'https://img.shields.io/badge.svg',
            'https://www.youtube.com/embed/x', 'https://api.example.org', 'https://esm.sh'} <= urls, urls


def test_ci_policy_matches_site_config():
    """verify.yml serves the smoke-test site with the CU policy enforced; it must be the same string as config/site.yaml."""
    import yaml
    root = Path(__file__).resolve().parents[1]
    want = yaml.safe_load((root / 'config' / 'site.yaml').read_text())['hosting']['csp']
    wf = yaml.safe_load((root / '.github' / 'workflows' / 'verify.yml').read_text())
    assert wf['jobs']['explorer-smoke']['env']['CU_CSP'] == want
