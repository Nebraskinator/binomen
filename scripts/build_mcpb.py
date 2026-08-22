#!/usr/bin/env python3
"""Package the Node server and its data as dist/binomen.mcpb.

The bundle carries the server AND the two shipped databases, so installing it is
the whole installation: no download step, no first-run wait, nothing for a bench
biologist to do but double-click. That reverses the earlier decoupling, and the
reason is in docs/adr/0001-ambiguity-only-local-database.md — an index holding
only ambiguous names is small enough to ship, where the 123 MB stage-2 index
never was.

    python scripts/build_mcpb.py

Two databases rather than one, because LPSN is CC BY-SA and NCBI Taxonomy is
public domain, and merging them would put a share-alike claim on the public
domain half. See docs/adr/0002-two-files-for-licence-containment.md.

Refuses to build if the Node tests do not pass, or if the bundle is over budget.
A setup artifact that has not been run is a draft, and this one cannot be tested
by the person installing it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NODE = REPO / "node"
OUT = REPO / "dist" / "binomen.mcpb"


def main() -> int:
    manifest = json.loads((NODE / "manifest.json").read_text())

    if shutil.which("node") is None:
        print("node is required to verify the bundle before building it", file=sys.stderr)
        return 1
    print("running the Node test suite")
    # Pass our own interpreter through: the Node tests shell out to Python to
    # build a fixture index, and "python3" is not a command on Windows.
    env = {**os.environ, "BINOMEN_PYTHON": sys.executable}
    r = subprocess.run(["node", "--test", "--no-warnings"], cwd=NODE,
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(r.stdout[-4000:], file=sys.stderr)
        print("\nNode tests failed; refusing to package.", file=sys.stderr)
        return 1
    passed = next((ln for ln in r.stdout.splitlines() if ln.startswith("# pass")), "")
    print(f"  {passed.strip()}")

    # Conformance must reflect the current Python implementation, or the two
    # halves can drift apart between a change and the next regeneration.
    print("checking the cross-language conformance fixture")
    live = NODE / "test" / "conformance.json"
    before = live.read_bytes() if live.exists() else b""
    # The generator writes the file itself, in UTF-8. Capturing its stdout and
    # decoding with the locale encoding corrupted non-ASCII characters on
    # Windows -- see emit_conformance.py.
    subprocess.run([sys.executable, str(REPO / "scripts" / "emit_conformance.py"),
                    "--out", str(live)], check=True, capture_output=True)
    if live.read_bytes() != before:
        print("  fixture was stale and has been refreshed -- re-run to package",
              file=sys.stderr)
        return 1
    print("  fixture is current")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()

    # Layout the manifest promises: server/index.js plus its requires.
    files = {
        "manifest.json": NODE / "manifest.json",
        "package.json": NODE / "package.json",
        "server/index.js": NODE / "src" / "server.js",
        "server/resolver.js": NODE / "src" / "resolver.js",
        "server/names.js": NODE / "src" / "names.js",
        "server/index_store.js": NODE / "src" / "index_store.js",
        "server/ambiguity.js": NODE / "src" / "ambiguity.js",
        "server/registers.js": NODE / "src" / "registers.js",
        "server/tool_descriptions.js": NODE / "src" / "tool_descriptions.js",
    }
    missing = [k for k, v in files.items() if not v.exists()]
    if missing:
        print(f"missing sources: {missing}", file=sys.stderr)
        return 1

    # The data. `ambiguity.js` and `registers.js` look for these at
    # ../data relative to their own directory, which is where these land.
    data = {
        "data/ambiguity.sqlite": REPO / "data" / "ambiguity.sqlite",
        "data/registers.sqlite": REPO / "data" / "registers.sqlite",
    }
    absent = [k for k, v in data.items() if not v.exists()]
    if absent:
        print(f"missing data: {absent}", file=sys.stderr)
        print("build them with binomen-build-ambiguity and binomen-harvest-registers",
              file=sys.stderr)
        return 1

    # Budget first, so a build that cannot ship fails before it writes anything.
    # The ceiling that matters is the compressed one: an .mcpb is a zip, and that
    # is what a biologist waits for on a download.
    from binomen.build.harvest_registers import enforce_bundle_budget
    sizes = enforce_bundle_budget(list(data.values()), max_zip_mb=25.0, max_disk_mb=100.0)
    print(f"data {sizes['disk_mb']:.1f} MB on disk, {sizes['zipped_mb']:.1f} MB compressed")

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for arc, src in files.items():
            z.write(src, arc)
        for arc, src in data.items():
            z.write(src, arc)

    size = OUT.stat().st_size
    print(f"\nwrote {OUT} ({size/1e6:.1f} MB)")
    print(f"  {manifest['display_name']}  v{manifest['version']}")
    print(f"  tools: {', '.join(t['name'] for t in manifest['tools'])}")
    for arc, src in data.items():
        print(f"  {arc}  {src.stat().st_size/1e6:.1f} MB")
    print("\nInstall: double-click it, or Claude Desktop >"
          " Settings > Extensions > Advanced settings > Install Extension...")
    print("Nothing downloads on first use: the data is in the bundle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
