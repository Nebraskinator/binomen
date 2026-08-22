"""How big the shipped bundle is allowed to be, and the check that enforces it.

Its own module, with nothing but the standard library behind it, because the
packaging script has to run it from a plain checkout. `scripts/build_mcpb.py`
imported this from `harvest_registers` for one release and broke immediately:
that module pulls in `httpx`, so packaging suddenly required the package to be
installed with its dependencies. A build step that can fail for a reason
unrelated to the build is a bad build step.

The ceiling that matters is the **compressed** one. An `.mcpb` is a zip, so that
is what a biologist waits for on a download; the uncompressed footprint is the
looser constraint. SQLite compresses well -- 67 MB of databases become 24 MB --
so judging by disk size would refuse builds that ship perfectly well.

Both limits are enforced as build failures rather than warnings, for the reason
`docs/FINDINGS.md` §8 gives about every fix that stuck in this project: a size
preference erodes one register at a time, and the erosion is invisible until
someone waits on an install.
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

# The agreed ceilings. Raise them deliberately, in a commit that says why.
MAX_ZIP_MB = 25.0
MAX_DISK_MB = 100.0


def zipped_mb(paths: list[Path]) -> float:
    """Compressed size of these files together, in MB."""
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        scratch = Path(tmp.name)
    try:
        with zipfile.ZipFile(scratch, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for p in paths:
                z.write(p, p.name)
        return scratch.stat().st_size / 1e6
    finally:
        scratch.unlink(missing_ok=True)


def enforce_bundle_budget(paths: list[Path], *, max_zip_mb: float = MAX_ZIP_MB,
                          max_disk_mb: float = MAX_DISK_MB) -> dict[str, float]:
    """Fail the build when the bundle outgrows what was agreed to ship."""
    disk = sum(p.stat().st_size for p in paths) / 1e6
    zipped = zipped_mb(paths)
    if zipped > max_zip_mb or disk > max_disk_mb:
        raise SystemExit(
            f"bundle is {zipped:.1f} MB compressed / {disk:.1f} MB on disk, over the "
            f"{max_zip_mb:.0f} / {max_disk_mb:.0f} MB budget.\n"
            f"Shrink a register or raise the budget deliberately -- not in passing.")
    return {"zipped_mb": zipped, "disk_mb": disk}


def enforce_budget(path: Path, max_mb: float) -> None:
    """The single-file check, used by the individual build steps."""
    mb = path.stat().st_size / 1e6
    if mb > max_mb:
        raise SystemExit(
            f"{path.name} is {mb:.1f} MB, over the {max_mb:.0f} MB budget.\n"
            f"Shrink it or raise the budget deliberately -- do not raise it in passing.")
