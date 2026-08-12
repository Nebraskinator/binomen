#!/usr/bin/env python3
"""Package the Node server as dist/binomen.mcpb — the double-click install.

The bundle carries the server and no data. That decoupling is deliberate: a new
index does not require a new extension, and a fixed bug does not make anyone
re-download 46 MB. The index arrives on first run and updates itself.

    python scripts/build_mcpb.py

Refuses to build if the Node tests do not pass. A setup artifact that has not
been run is a draft, and this one cannot be tested by the person installing it.
"""

from __future__ import annotations

import json
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
    r = subprocess.run(["node", "--test", "--no-warnings"], cwd=NODE,
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-4000:], file=sys.stderr)
        print("\nNode tests failed; refusing to package.", file=sys.stderr)
        return 1
    passed = next((ln for ln in r.stdout.splitlines() if ln.startswith("# pass")), "")
    print(f"  {passed.strip()}")

    # Conformance must reflect the current Python implementation, or the two
    # halves can drift apart between a change and the next regeneration.
    print("regenerating the cross-language conformance fixture")
    fixture = subprocess.run([sys.executable, str(REPO / "scripts" / "emit_conformance.py")],
                             capture_output=True, text=True, check=True).stdout
    live = NODE / "test" / "conformance.json"
    if live.read_text() != fixture:
        live.write_text(fixture)
        print("  fixture was stale and has been refreshed -- re-run to package", file=sys.stderr)
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
        "server/tool_descriptions.js": NODE / "src" / "tool_descriptions.js",
    }
    missing = [k for k, v in files.items() if not v.exists()]
    if missing:
        print(f"missing sources: {missing}", file=sys.stderr)
        return 1

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, src in files.items():
            z.write(src, arc)

    size = OUT.stat().st_size
    print(f"\nwrote {OUT} ({size/1024:.0f} KB)")
    print(f"  {manifest['display_name']}  v{manifest['version']}")
    print(f"  tools: {', '.join(t['name'] for t in manifest['tools'])}")
    print("\nInstall: double-click it, or Claude Desktop >"
          " Settings > Extensions > Advanced settings > Install Extension...")
    print("The index (about 46 MB) downloads on first use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
