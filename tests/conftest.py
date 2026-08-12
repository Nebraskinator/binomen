import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FIXTURE = ROOT / "tests" / "fixtures" / "taxdump"


def pytest_report_header(config):
    """Say which fixture the suite is about to run against.

    Not decoration. Four normalization bugs shipped because the fixture was
    hand-written from assumptions about NCBI, and every one of them passed a
    green suite. Then a real extraction got silently regenerated over, and the
    suite went green again against fiction. Which data you are testing on is
    the single most load-bearing fact about a run of these tests, so it goes at
    the top of the output.
    """
    marker = FIXTURE / "PROVENANCE.txt"
    if marker.exists():
        text = marker.read_text()
        release = next((ln.split(":", 1)[1].strip() for ln in text.splitlines()
                        if ln.startswith("release:")), "unknown")
        # Trust nothing: check the marker against the files beside it.
        claimed = next((int(ln.split("taxa:")[1].split()[0]) for ln in text.splitlines()
                        if ln.startswith("taxa:")), 0)
        nodes = FIXTURE / "nodes.dmp"
        actual = sum(1 for _ in nodes.open()) if nodes.exists() else 0
        if claimed and actual < claimed * 0.9:
            return [
                "binomen fixture: MARKER DISAGREES WITH CONTENTS",
                f"  PROVENANCE.txt claims {claimed} taxa; nodes.dmp holds {actual}.",
                "  Something overwrote the extracted rows and left the label. Re-extract:",
                "  binomen-build-index --taxdump taxdump.tar.gz "
                "--extract-fixture tests/fixtures/taxdump",
            ]
        return [f"binomen fixture: REAL archive rows ({release}, {actual} taxa)"]
    return [
        "binomen fixture: SYNTHETIC (hand-written)",
        "  -> tests are checking the parser against its author's assumptions, which is",
        "     how four normalization bugs shipped green. Prefer real rows:",
        "     binomen-build-index --taxdump taxdump.tar.gz --extract-fixture tests/fixtures/taxdump",
    ]


@pytest.fixture(scope="session")
def indexes(tmp_path_factory):
    """Build both artifacts once per session from the synthetic fixture."""
    fixture = ROOT / "tests" / "fixtures" / "taxdump"
    # A real extracted fixture wins. make_fixture.py also refuses to clobber it,
    # but regenerating here would have silently reverted it before every run --
    # which is how the extraction got thrown away the first time it was used.
    real = (fixture / "PROVENANCE.txt").exists()
    if not real:
        subprocess.run([sys.executable, str(ROOT / "tests" / "fixtures" / "make_fixture.py")],
                       check=True, capture_output=True)
    d = tmp_path_factory.mktemp("db")
    s2, s1, fld = d / "binomen.sqlite", d / "stage1.sqlite", d / "field.sqlite"
    # Every output goes to the temp dir. A test run must never write into the
    # repo's data/ -- it would clobber a real index, and on a read-only checkout
    # it simply fails.
    subprocess.run([sys.executable, "-m", "binomen.build.build_index",
                    "--fixture", str(fixture), "--out", str(s2),
                    "--stage1-out", str(s1), "--field-out", str(fld),
                    "--version", "fixture-v2", "--quiet"],
                   check=True, cwd=ROOT,
                   env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    return {"stage1": s1, "stage2": s2, "field": fld, "real": real}


@pytest.fixture()
def real_fixture(indexes):
    """True when the suite is running against real archive rows."""
    return indexes["real"]


@pytest.fixture()
def resolver(indexes, monkeypatch):
    monkeypatch.setenv("BINOMEN_STAGE1_DB", str(indexes["stage1"]))
    monkeypatch.setenv("BINOMEN_DB", str(indexes["stage2"]))
    monkeypatch.setenv("BINOMEN_OFFLINE", "1")
    from binomen.resolver import Resolver
    return Resolver(use_live=False)


@pytest.fixture()
def stage1_only(indexes, monkeypatch, tmp_path):
    """A stage-1-only install: the full index is deliberately absent."""
    monkeypatch.setenv("BINOMEN_STAGE1_DB", str(indexes["stage1"]))
    monkeypatch.setenv("BINOMEN_DB", str(tmp_path / "does-not-exist.sqlite"))
    monkeypatch.setenv("BINOMEN_OFFLINE", "1")
    from binomen.resolver import Resolver
    return Resolver(use_live=False)
