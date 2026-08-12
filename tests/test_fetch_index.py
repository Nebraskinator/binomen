"""Tests for prebuilt-index publishing and fetching.

An index is a set of assertions about what organisms are called. Installing an
unverified one would be a worse failure than any this package is designed to
detect, so the integrity path gets tested rather than assumed.
"""

import http.server
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from binomen.fetch_index import build_manifest, check_age, fetch, sha256


@pytest.fixture()
def served(tmp_path):
    """A real HTTP server over a directory. Exercises the download path rather
    than mocking it away."""
    root = tmp_path / "release"
    root.mkdir()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(root), **kw)

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield root, f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def _fake_index(path: Path, version: str = "taxdump-2026-08-11") -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE verdicts(norm TEXT, verdict TEXT, code TEXT, taxid INTEGER, accepted TEXT);"
        "CREATE TABLE bloom(code TEXT PRIMARY KEY, n INTEGER, blob BLOB);")
    conn.execute("INSERT INTO meta VALUES ('version', ?)", (version,))
    conn.commit()
    conn.close()
    return path


def _publish(root: Path, **manifest_overrides) -> dict:
    idx = _fake_index(root / "binomen-stage1.sqlite")
    m = build_manifest({"stage1": idx}, "taxdump-2026-08-11")
    m.update(manifest_overrides)
    (root / "manifest.json").write_text(json.dumps(m))
    return m


def test_manifest_records_size_and_checksum(tmp_path):
    idx = _fake_index(tmp_path / "binomen-stage1.sqlite")
    m = build_manifest({"stage1": idx}, "taxdump-2026-08-11")
    assert m["artifacts"]["stage1"]["sha256"] == sha256(idx)
    assert m["artifacts"]["stage1"]["bytes"] == idx.stat().st_size
    assert m["taxdump_release"] == "taxdump-2026-08-11"


def test_fetch_downloads_and_verifies(served, tmp_path):
    root, base = served
    _publish(root)
    out = tmp_path / "data"
    assert fetch(f"{base}/manifest.json", out_dir=out, quiet=True) == 0
    got = out / "binomen-stage1.sqlite"
    assert got.exists()
    assert sha256(got) == json.loads((root / "manifest.json").read_text())[
        "artifacts"]["stage1"]["sha256"]


def test_checksum_mismatch_is_fatal_and_installs_nothing(served, tmp_path):
    """A corrupted or substituted index must never land on disk."""
    root, base = served
    m = _publish(root)
    m["artifacts"]["stage1"]["sha256"] = "0" * 64
    (root / "manifest.json").write_text(json.dumps(m))
    out = tmp_path / "data"
    assert fetch(f"{base}/manifest.json", out_dir=out, quiet=True) == 1
    assert not (out / "binomen-stage1.sqlite").exists()
    assert not list(out.glob("*.part")), "a partial download was left behind"


def test_existing_matching_index_is_left_alone(served, tmp_path):
    root, base = served
    _publish(root)
    out = tmp_path / "data"
    fetch(f"{base}/manifest.json", out_dir=out, quiet=True)
    target = out / "binomen-stage1.sqlite"
    before = target.stat().st_mtime_ns
    assert fetch(f"{base}/manifest.json", out_dir=out, quiet=True) == 0
    assert target.stat().st_mtime_ns == before, "re-fetched an index that already matched"


def test_mismatched_existing_index_is_replaced(served, tmp_path):
    root, base = served
    _publish(root)
    out = tmp_path / "data"
    out.mkdir()
    (out / "binomen-stage1.sqlite").write_bytes(b"stale garbage")
    assert fetch(f"{base}/manifest.json", out_dir=out, quiet=True) == 0
    assert sha256(out / "binomen-stage1.sqlite") != sha256(Path(__file__))


def test_stale_sidecars_are_cleared_on_install(served, tmp_path):
    """A leftover -wal can be recovered into a fresh file and resurrect an old
    schema -- the bug that cost an afternoon."""
    root, base = served
    _publish(root)
    out = tmp_path / "data"
    out.mkdir()
    (out / "binomen-stage1.sqlite").write_bytes(b"old")
    (out / "binomen-stage1.sqlite-wal").write_bytes(b"orphaned journal")
    fetch(f"{base}/manifest.json", out_dir=out, quiet=True)
    assert not (out / "binomen-stage1.sqlite-wal").exists()


def test_unreachable_manifest_fails_cleanly(tmp_path):
    assert fetch("http://127.0.0.1:9/manifest.json", out_dir=tmp_path, quiet=True) == 1


def test_check_age_flags_a_stale_index(tmp_path, capsys):
    old = _fake_index(tmp_path / "old.sqlite", version="taxdump-2019-01-01")
    assert check_age(old) == 2
    out = capsys.readouterr().out
    assert "days old" in out and "Refresh" in out


def test_check_age_accepts_a_fresh_index(tmp_path, capsys):
    from datetime import date
    fresh = _fake_index(tmp_path / "new.sqlite", version=f"taxdump-{date.today().isoformat()}")
    assert check_age(fresh) == 0
    assert "0 days old" in capsys.readouterr().out


class TestCompressedArtifacts:
    """Indexes ship gzipped: 123 MB on disk is 46 MB over the wire, which is the
    difference between a download a biologist starts and one they don't."""

    def test_publish_compresses_and_records_both_checksums(self, tmp_path):
        from binomen.fetch_index import build_manifest
        src = tmp_path / "src"
        src.mkdir()
        idx = _fake_index(src / "binomen-field.sqlite")
        out = tmp_path / "dist"
        out.mkdir()
        m = build_manifest({"field": idx}, "taxdump-2026-08-11", compress_to=out)
        e = m["artifacts"]["field"]
        assert e["compression"] == "gzip"
        assert e["file"].endswith(".gz")
        assert (out / e["file"]).exists()
        # Both are recorded: the transfer is verified on arrival, and the
        # decompressed database is verified again before replacing a good one.
        assert e["sha256"] == sha256(out / e["file"])
        assert e["uncompressed_sha256"] == sha256(idx)

    def test_fetch_decompresses_and_verifies(self, served, tmp_path):
        from binomen.fetch_index import build_manifest
        root, base = served
        src = tmp_path / "src"
        src.mkdir()
        idx = _fake_index(src / "binomen-field.sqlite")
        m = build_manifest({"field": idx}, "taxdump-2026-08-11", compress_to=root)
        (root / "manifest.json").write_text(json.dumps(m))

        out = tmp_path / "data"
        assert fetch(f"{base}/manifest.json", which="field", out_dir=out, quiet=True) == 0
        got = out / "binomen-field.sqlite"
        assert got.exists()
        assert sha256(got) == sha256(idx), "decompressed file differs from the original"
        assert not list(out.glob("*.part")) and not list(out.glob("*.raw"))

    def test_corrupt_payload_inside_a_valid_gzip_is_caught(self, served, tmp_path):
        """The transfer checksum can pass while the database inside is wrong.
        That is why the uncompressed hash is recorded too."""
        import gzip as gz

        from binomen.fetch_index import build_manifest
        root, base = served
        src = tmp_path / "src"
        src.mkdir()
        idx = _fake_index(src / "binomen-field.sqlite")
        m = build_manifest({"field": idx}, "taxdump-2026-08-11", compress_to=root)
        # Repack different content, then re-record the outer hash so only the
        # inner check can catch it.
        target = root / m["artifacts"]["field"]["file"]
        with gz.open(target, "wb") as f:
            f.write(b"not the database you asked for")
        m["artifacts"]["field"]["sha256"] = sha256(target)
        (root / "manifest.json").write_text(json.dumps(m))

        out = tmp_path / "data"
        assert fetch(f"{base}/manifest.json", which="field", out_dir=out, quiet=True) == 1
        assert not (out / "binomen-field.sqlite").exists()
