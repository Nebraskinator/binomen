"""Tests for the Claude Desktop installer.

These exist because the first version of this installer was written in
PowerShell, could not be run where it was written, and failed on its first line
of real work with a null-reference error against an empty config object. The
whole session has been one lesson about untested assumptions meeting real data;
a setup script is not exempt.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_claude_desktop.py"


def run(cfg: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INSTALLER), "--config", str(cfg), "--force", *args],
        capture_output=True, text=True)


def read(cfg: Path) -> dict:
    return json.loads(cfg.read_text())


def test_creates_a_config_that_does_not_exist(tmp_path):
    """The exact case the PowerShell version crashed on."""
    cfg = tmp_path / "nested" / "claude_desktop_config.json"
    r = run(cfg)
    assert r.returncode == 0, r.stderr
    assert read(cfg)["mcpServers"]["binomen"]["args"] == ["-m", "binomen.server"]


def test_merges_into_an_existing_config_without_eating_other_servers(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({
        "mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "server-filesystem"]}},
        "someOtherSetting": True,
    }))
    assert run(cfg).returncode == 0
    got = read(cfg)
    assert set(got["mcpServers"]) == {"filesystem", "binomen"}
    assert got["mcpServers"]["filesystem"]["command"] == "npx"
    assert got["someOtherSetting"] is True


def test_backs_up_before_writing(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text('{"mcpServers": {}}')
    run(cfg)
    assert list(tmp_path.glob("*.binomen-backup-*")), "no backup written"


def test_is_idempotent(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    run(cfg)
    first = read(cfg)
    run(cfg)
    assert read(cfg) == first


def test_remove_leaves_other_servers_alone(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    run(cfg)
    run(cfg, "--remove")
    got = read(cfg)
    assert "binomen" not in got["mcpServers"]
    assert "other" in got["mcpServers"]


def test_remove_when_nothing_is_installed(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    r = run(cfg, "--remove")
    assert r.returncode == 0
    assert "no binomen entry" in r.stdout


def test_refuses_to_clobber_invalid_json(tmp_path):
    """Someone's hand-edited config with a trailing comma should not be lost."""
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text('{"mcpServers": {,}}')
    r = run(cfg)
    assert r.returncode == 1
    assert "not valid JSON" in r.stdout
    assert cfg.read_text() == '{"mcpServers": {,}}', "the broken file was modified"


def test_survives_an_empty_file(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text("")
    assert run(cfg).returncode == 0
    assert "binomen" in read(cfg)["mcpServers"]


def test_survives_mcpservers_being_the_wrong_type(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text('{"mcpServers": null}')
    assert run(cfg).returncode == 0
    assert "binomen" in read(cfg)["mcpServers"]


@pytest.mark.parametrize("variant", ["narrow", "broad", "imperative"])
def test_description_variant_is_recorded(tmp_path, variant):
    cfg = tmp_path / "claude_desktop_config.json"
    run(cfg, "--descriptions", variant)
    assert read(cfg)["mcpServers"]["binomen"]["env"]["BINOMEN_DESCRIPTIONS"] == variant


def test_refuses_without_an_index_unless_forced(tmp_path):
    """A config pointing at a missing database fails opaquely inside Claude
    Desktop, which is a bad place to debug."""
    cfg = tmp_path / "claude_desktop_config.json"
    r = subprocess.run([sys.executable, str(INSTALLER), "--config", str(cfg)],
                       capture_output=True, text=True)
    if (ROOT / "data" / "binomen-stage1.sqlite").exists():
        pytest.skip("a real index is present, so the refusal path cannot be exercised here")
    assert r.returncode == 1
    assert "binomen-build-index" in r.stdout
    assert not cfg.exists()


def test_written_config_is_valid_json_claude_can_read(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    run(cfg)
    entry = read(cfg)["mcpServers"]["binomen"]
    assert Path(entry["env"]["PYTHONPATH"]).name == "src"
    assert entry["env"]["BINOMEN_STAGE1_DB"].endswith("binomen-stage1.sqlite")
    assert entry["command"]


class TestWindowsPackagedInstall:
    """An MSIX/Store-packaged Claude Desktop virtualizes AppData, so a config
    written to the unpackaged %APPDATA% path is a different file that the app
    never reads -- silently. Cost an evening of misdiagnosis."""

    def test_packaged_location_is_preferred_when_present(self, tmp_path, monkeypatch):
        import importlib.util
        spec = importlib.util.spec_from_file_location("inst", INSTALLER)
        inst = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inst)
        monkeypatch.setattr(inst.sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

        pkg = tmp_path / "Local" / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / \
            "Roaming" / "Claude"
        pkg.mkdir(parents=True)
        (tmp_path / "Roaming" / "Claude").mkdir(parents=True)

        chosen = inst.config_path()
        assert chosen.parent == pkg, f"picked the unpackaged path: {chosen}"

    def test_falls_back_to_roaming_when_not_packaged(self, tmp_path, monkeypatch):
        import importlib.util
        spec = importlib.util.spec_from_file_location("inst", INSTALLER)
        inst = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inst)
        monkeypatch.setattr(inst.sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        (tmp_path / "Roaming" / "Claude").mkdir(parents=True)
        assert inst.config_path().parent == tmp_path / "Roaming" / "Claude"

    def test_all_candidates_are_reported(self, tmp_path, monkeypatch):
        import importlib.util
        spec = importlib.util.spec_from_file_location("inst", INSTALLER)
        inst = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inst)
        monkeypatch.setattr(inst.sys, "platform", "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        (tmp_path / "Local" / "Packages" / "Claude_abc" / "LocalCache" / "Roaming" /
         "Claude").mkdir(parents=True)
        cands = inst.candidate_config_paths()
        assert len(cands) == 2
        assert "Packages" in str(cands[0])
