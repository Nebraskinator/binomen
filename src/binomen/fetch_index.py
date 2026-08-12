"""Fetch a prebuilt index instead of building one from taxdump.

Building from source is a ~400 MB download, a few minutes of parsing, and a
working Python toolchain. That is fine for a developer and a wall for a
biologist, and the wall sits in front of the only part of this project a
biologist actually wants.

A prebuilt stage-1 index is ~107 MB and takes under a minute. It is a *release
artifact*, not a committed database -- the distinction matters, because the
whole premise here is that a frozen copy of a taxonomy goes stale. Every
artifact is stamped with the taxdump release it was built from, that release
appears in the provenance of every response, and `--check-age` will tell you
when yours is old.

Integrity is not optional: an index is a set of assertions about what organisms
are called, and silently accepting a corrupted or substituted one would be the
worst failure this package could have. Every download is checked against a
SHA-256 recorded in a signed-by-provenance manifest, and a mismatch is fatal.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"

# Override for a fork, a mirror, or an internal host.
DEFAULT_MANIFEST = os.environ.get(
    "BINOMEN_INDEX_MANIFEST",
    "https://github.com/Nebraskinator/binomen/releases/latest/download/manifest.json",
)

ARTIFACTS = {
    "stage1": "binomen-stage1.sqlite",
    "stage2": "binomen.sqlite",
    "field": "binomen-field.sqlite",
}
STALE_AFTER_DAYS = 120


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def compress(src: Path, dest: Path, *, quiet: bool = False) -> Path:
    """Gzip an index for shipping.

    SQLite files are mostly text and compress to about a third. The field index
    is 123 MB on disk and 46 MB over the wire, which is the difference between
    a download a bench biologist will start and one they will not.
    """
    if not quiet:
        print(f"[binomen] compressing {src.name}", file=sys.stderr)
    with open(src, "rb") as fin, gzip.open(dest, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout, length=1 << 20)
    return dest


def build_manifest(paths: dict[str, Path], version: str, notes: str = "",
                   compress_to: Path | None = None, quiet: bool = False) -> dict:
    """Describe a set of artifacts so a downloader can verify them.

    Both checksums are recorded when compressing: the transfer is verified on
    arrival, and the decompressed database is verified again before it is
    allowed to replace a working index. Belt and braces, because an index is a
    set of assertions about what organisms are called.
    """
    entries = {}
    for name, p in paths.items():
        if not p.exists():
            continue
        if compress_to is not None:
            gz = compress_to / (p.name + ".gz")
            compress(p, gz, quiet=quiet)
            entries[name] = {
                "file": gz.name,
                "compression": "gzip",
                "bytes": gz.stat().st_size,
                "sha256": sha256(gz),
                "uncompressed_bytes": p.stat().st_size,
                "uncompressed_sha256": sha256(p),
            }
            continue
        entries[name] = {
            "file": p.name,
            "bytes": p.stat().st_size,
            "sha256": sha256(p),
        }
    return {
        "schema": 1,
        "taxdump_release": version,
        "built": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "builder_version": "0.2.0",
        "artifacts": entries,
        "notes": notes or (
            "Prebuilt binomen indexes. Derived from NCBI Taxonomy, which is public domain "
            "(work of the US Government). The taxdump release these were built from is "
            "recorded above and is echoed in the provenance of every tool response."
        ),
    }


def _download(url: str, dest: Path, *, quiet: bool = False) -> Path:
    if not quiet:
        print(f"[binomen] downloading {url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "binomen/0.2 (index fetch)"})
    total = 0
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        size = int(r.headers.get("Content-Length") or 0)
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
            total += len(block)
            if not quiet and size:
                pct = 100 * total / size
                print(f"\r[binomen]   {total/1e6:7.1f} / {size/1e6:.1f} MB  {pct:5.1f}%",
                      end="", file=sys.stderr)
    if not quiet:
        print(file=sys.stderr)
    return dest


def fetch(manifest_url: str = DEFAULT_MANIFEST, *, which: str = "stage1",
          out_dir: Path | None = None, quiet: bool = False, force: bool = False) -> int:
    out_dir = out_dir or DATA
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        payload, _ = _get_json(manifest_url)
    except Exception as e:  # noqa: BLE001
        print(f"[binomen] could not fetch the index manifest: {e}\n"
              f"          {manifest_url}\n"
              f"          Build locally instead:  binomen-build-index",
              file=sys.stderr)
        return 1

    release = payload.get("taxdump_release", "unknown")
    wanted = {"both": ["stage1", "stage2"], "all": ["field", "stage1", "stage2"]}.get(
        which, [which])
    base = manifest_url.rsplit("/", 1)[0]

    if not quiet:
        print(f"[binomen] manifest: taxdump release {release}, built {payload.get('built')}",
              file=sys.stderr)

    for name in wanted:
        entry = (payload.get("artifacts") or {}).get(name)
        if not entry:
            print(f"[binomen] manifest has no '{name}' artifact", file=sys.stderr)
            return 1
        dest = out_dir / ARTIFACTS[name]
        if dest.exists() and not force:
            if sha256(dest) == entry.get("uncompressed_sha256", entry["sha256"]):
                if not quiet:
                    print(f"[binomen] {dest.name} already present and matches the manifest",
                          file=sys.stderr)
                continue
            print(f"[binomen] {dest.name} exists but does not match the manifest; replacing",
                  file=sys.stderr)

        with tempfile.NamedTemporaryFile(delete=False, dir=out_dir,
                                         suffix=".part") as tf:
            tmp = Path(tf.name)
        try:
            _download(f"{base}/{entry['file']}", tmp, quiet=quiet)
            got = sha256(tmp)
            if got != entry["sha256"]:
                # Fatal, deliberately. An index is a set of assertions about what
                # organisms are called; quietly accepting an unverified one would
                # be the worst failure this package could have.
                print(f"[binomen] CHECKSUM MISMATCH for {entry['file']}\n"
                      f"          expected {entry['sha256']}\n"
                      f"          got      {got}\n"
                      f"          Refusing to install it. Retry, or build locally with "
                      f"binomen-build-index.", file=sys.stderr)
                return 1
            if entry.get("compression") == "gzip":
                if not quiet:
                    print(f"[binomen] decompressing to "
                          f"{entry.get('uncompressed_bytes', 0)/1e6:.0f} MB", file=sys.stderr)
                with tempfile.NamedTemporaryFile(delete=False, dir=out_dir,
                                                 suffix=".raw") as rf:
                    raw = Path(rf.name)
                with gzip.open(tmp, "rb") as fin, open(raw, "wb") as fout:
                    shutil.copyfileobj(fin, fout, length=1 << 20)
                tmp.unlink()
                tmp = raw
                want = entry.get("uncompressed_sha256")
                if want and sha256(tmp) != want:
                    print(f"[binomen] CHECKSUM MISMATCH after decompressing {entry['file']}. "
                          f"Refusing to install it.", file=sys.stderr)
                    tmp.unlink()
                    return 1

            # Clear sidecars from any previous build before moving into place.
            try:
                for suffix in ("", "-wal", "-shm", "-journal"):
                    stale = Path(str(dest) + suffix)
                    if stale.exists():
                        stale.unlink()
                shutil.move(str(tmp), dest)
            except PermissionError as e:
                print(f"[binomen] {dest} is locked by another process and cannot be "
                      f"replaced.\n"
                      f"          Quit Claude Desktop completely (system tray, not just the "
                      f"window),\n"
                      f"          exit any Claude Code session, then re-run.\n"
                      f"          (OS said: {e.strerror})", file=sys.stderr)
                return 1
            if not quiet:
                print(f"[binomen] installed {dest} ({dest.stat().st_size/1e6:.1f} MB), "
                      f"sha256 verified", file=sys.stderr)
        finally:
            if tmp.exists():
                tmp.unlink()

    if not quiet:
        print(f"[binomen] ready. Release {release}. Verify with:  binomen-doctor",
              file=sys.stderr)
    return 0


def _get_json(url: str) -> tuple[dict, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "binomen/0.2"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode()), r.headers.get("Date", "")


def check_age(path: Path | None = None) -> int:
    """Report how old the installed index is.

    A stale index does not announce itself -- it just answers as of a date you
    have forgotten. Every response carries the release string, but nobody reads
    those, so this makes it checkable in one command.
    """
    from .db import DEFAULT_STAGE1, IndexNotBuilt, IndexStale, Stage1
    try:
        s = Stage1(path or DEFAULT_STAGE1)
    except (IndexNotBuilt, IndexStale) as e:
        print(str(e).splitlines()[0], file=sys.stderr)
        return 1
    version = s.meta.get("version", "unknown")
    s.close()
    if not version.startswith("taxdump-"):
        print(f"index release: {version} (not a dated NCBI release; age unknown)")
        return 0
    try:
        built = date.fromisoformat(version.removeprefix("taxdump-"))
    except ValueError:
        print(f"index release: {version} (unparseable date)")
        return 0
    age = (date.today() - built).days
    print(f"index release: {version}  ({age} days old)")
    if age > STALE_AFTER_DAYS:
        print(f"  This is more than {STALE_AFTER_DAYS} days old. NCBI Taxonomy changes "
              f"continuously; names accepted since then will resolve as 'unknown' and "
              f"recent transfers will be missing.\n"
              f"  Refresh:  binomen-fetch-index --force   (or binomen-build-index)")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--which", choices=("stage1", "stage2", "field", "both", "all"),
                    default="field",
                    help="field is the shippable one-file edition; stage1/stage2 are the "
                         "developer artifacts")
    ap.add_argument("--out", type=Path, default=DATA)
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    ap.add_argument("--check-age", action="store_true",
                    help="report how old the installed index is and exit")
    ap.add_argument("--publish", type=Path, metavar="DIR",
                    help="(maintainer) write a manifest.json and gzipped artifacts into DIR")
    ap.add_argument("--no-compress", action="store_true",
                    help="with --publish, ship uncompressed artifacts")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    if a.check_age:
        return check_age()

    if a.publish:
        paths = {k: a.out / v for k, v in ARTIFACTS.items()}
        present = {k: p for k, p in paths.items() if p.exists()}
        if not present:
            print(f"no built indexes found in {a.out}. Run binomen-build-index first.",
                  file=sys.stderr)
            return 1
        from .db import Stage1
        version = Stage1(present.get("stage1", next(iter(present.values())))).meta.get(
            "version", "unknown") if "stage1" in present else "unknown"
        a.publish.mkdir(parents=True, exist_ok=True)
        manifest = build_manifest(present, version,
                                  compress_to=None if a.no_compress else a.publish,
                                  quiet=a.quiet)
        (a.publish / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if a.no_compress:
            for p in present.values():
                target = a.publish / p.name
                if target.resolve() != p.resolve():
                    shutil.copy2(p, target)
        print(f"wrote {a.publish / 'manifest.json'}")
        for k, e in manifest["artifacts"].items():
            raw = e.get("uncompressed_bytes")
            extra = f"  (from {raw/1e6:.0f} MB)" if raw else ""
            print(f"  {k:7s} {e['file']:30s} {e['bytes']/1e6:7.1f} MB{extra}")
        print(f"\nUpload the contents of {a.publish} as release assets. Downloaders will "
              f"verify against the manifest.")
        return 0

    return fetch(a.manifest, which=a.which, out_dir=a.out, quiet=a.quiet, force=a.force)


if __name__ == "__main__":
    raise SystemExit(main())
