#!/usr/bin/env python3
"""Point binomen at your GitHub repo.

The prebuilt-index manifest URL appears in two places -- the Python fetcher and
the Node extension -- which is exactly the sort of duplication that drifts. This
sets both, and refuses to leave them inconsistent.

    python scripts/set_repo.py yourname/binomen
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGETS = [
    REPO / "src" / "binomen" / "fetch_index.py",
    REPO / "node" / "src" / "index_store.js",
]
PATTERN = re.compile(r"https://github\.com/[^/]+/[^/]+/releases/latest/download/manifest\.json")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1 or "/" not in argv[0]:
        print(__doc__)
        print("current setting:")
        for t in TARGETS:
            m = PATTERN.search(t.read_text())
            print(f"  {t.relative_to(REPO)}: {m.group(0) if m else '(not found)'}")
        return 1

    slug = argv[0].strip().removesuffix(".git")
    url = f"https://github.com/{slug}/releases/latest/download/manifest.json"

    changed = []
    for t in TARGETS:
        s = t.read_text()
        new, n = PATTERN.subn(url, s)
        if n == 0:
            print(f"could not find the manifest URL in {t}", file=sys.stderr)
            return 1
        if new != s:
            t.write_text(new)
            changed.append(t.relative_to(REPO))
    print(f"manifest URL -> {url}")
    for c in changed:
        print(f"  updated {c}")
    if not changed:
        print("  already set")
    print("\nRebuild the extension so it carries the new URL:  make mcpb")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
