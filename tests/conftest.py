import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="session")
def index(tmp_path_factory):
    """Build the fixture index once per session."""
    fixture = ROOT / "tests" / "fixtures" / "taxdump"
    subprocess.run([sys.executable, str(ROOT / "tests" / "fixtures" / "make_fixture.py")],
                   check=True, capture_output=True)
    out = tmp_path_factory.mktemp("db") / "binomen.sqlite"
    subprocess.run([sys.executable, "-m", "binomen.build.build_index",
                    "--fixture", str(fixture), "--out", str(out),
                    "--version", "fixture-v1", "--quiet"],
                   check=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"),
                                              "PATH": "/usr/bin:/bin"})
    return out


@pytest.fixture()
def resolver(index, monkeypatch):
    monkeypatch.setenv("BINOMEN_DB", str(index))
    monkeypatch.setenv("BINOMEN_OFFLINE", "1")
    from binomen.db import Backbone
    from binomen.resolver import Resolver
    return Resolver(Backbone(index), use_live=False)
