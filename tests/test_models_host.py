"""R1-01: model resolution must work when `host` is a plain namespace global (Claude Science kernel),
not builtins.host and not __main__.host; `python -m catalog.models` must exit 1 when a role is unresolved."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from catalog import models  # noqa: E402


class FakeHost:
    def list_models(self):
        return ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-1", "claude-opus-5", "claude-sonnet-5"]

    def reasoning_model(self):
        return "claude-sonnet-5"

    def current_model(self):
        return "claude-opus-4-1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("CATALOG_MODEL_"):
            monkeypatch.delenv(k)
    models._HOST = None
    yield
    models._HOST = None


def test_set_host_explicit():
    models.set_host(FakeHost())
    assert models.resolve_model("rubric") == "claude-sonnet-5"
    assert models.resolve_model("screen") == "claude-haiku-4-5"
    assert models.resolve_model("adjudicate") == "claude-opus-5"   # config/models.yaml pins adjudicate to Opus 5 (owner, 2026-10-28)


def test_header_under_kernel_like_namespace():
    """exec the real header pattern in a fresh dict that holds `host` — no builtins, no __main__."""
    ns = {"host": FakeHost(), "__name__": "leaf_worker"}
    header = (
        "from catalog.models import resolve_model, set_host\n"
        "set_host(globals().get('host'))\n"
        "MODEL = globals().get('MODEL') or resolve_model('rubric')\n"
    )
    exec(compile(header, "<header>", "exec"), ns)
    assert ns["MODEL"] == "claude-sonnet-5"


def test_preset_model_short_circuits_resolution():
    """`globals().get('MODEL') or resolve_model(...)` must NOT evaluate resolve_model when MODEL is preset."""
    ns = {"MODEL": "preset-id", "__name__": "leaf_worker"}  # no host anywhere -> resolve_model would raise
    exec("from catalog.models import resolve_model, set_host\nset_host(globals().get('host'))\n"
         "MODEL = globals().get('MODEL') or resolve_model('rubric')\n", ns)
    assert ns["MODEL"] == "preset-id"


def test_frame_globals_fallback_without_set_host():
    """Even without set_host, a caller frame whose globals hold `host` is found."""
    ns = {"host": FakeHost()}
    exec("from catalog.models import resolve_model\nMODEL = resolve_model('screen')\n", ns)
    assert ns["MODEL"] == "claude-haiku-4-5"


def test_no_host_raises():
    with pytest.raises(models.ModelResolutionError):
        models.resolve_model("rubric")


def test_main_exits_nonzero_when_unresolved():
    env = {k: v for k, v in os.environ.items() if not k.startswith("CATALOG_MODEL_")}
    env["PYTHONPATH"] = SRC
    p = subprocess.run([sys.executable, "-m", "catalog.models"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "UNRESOLVED" in p.stdout


def test_main_exits_zero_with_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("CATALOG_MODEL_")}
    env["PYTHONPATH"] = SRC
    for role in ("screen", "rubric", "adjudicate", "audit"):
        env[f"CATALOG_MODEL_{role.upper()}"] = "x-" + role
    p = subprocess.run([sys.executable, "-m", "catalog.models"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0, p.stdout + p.stderr
