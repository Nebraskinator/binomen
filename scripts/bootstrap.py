#!/usr/bin/env python3
"""One command from nothing to a working binomen install.

    python bootstrap.py

Creates a virtualenv, installs the package, fetches a prebuilt index (or builds
one), registers the server with Claude Code or Claude Desktop, and verifies the
whole chain end to end.

Written for someone who wants to resolve organism names, not to debug a Python
environment. Every step reports what it did, every failure says what to do next,
and re-running is safe.

Options
-------
    --build-index        build from NCBI taxdump instead of fetching (~400 MB)
    --full-index         also fetch/build the larger stage-2 index
    --client code|desktop|none
    --descriptions narrow|broad|imperative
    --no-venv            install into the current environment
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STEP = 0


def step(msg: str) -> None:
    global STEP
    STEP += 1
    print(f"\n[{STEP}] {msg}", flush=True)


def fail(msg: str, remedy: str = "") -> int:
    print(f"\n  FAILED: {msg}", file=sys.stderr)
    if remedy:
        print(f"  {remedy}", file=sys.stderr)
    return 1


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, **kw)


def venv_python(vdir: Path) -> Path:
    return vdir / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-index", action="store_true",
                    help="build from taxdump instead of fetching a prebuilt index")
    ap.add_argument("--full-index", action="store_true",
                    help="also install the larger stage-2 index (full local resolution)")
    ap.add_argument("--client", choices=("code", "desktop", "none"), default="code")
    ap.add_argument("--descriptions", choices=("narrow", "broad", "imperative"),
                    default="broad")
    ap.add_argument("--no-venv", action="store_true",
                    help="install into the current environment instead of ./.venv")
    ap.add_argument("--manifest", help="override the prebuilt index manifest URL")
    a = ap.parse_args(argv)

    print("binomen bootstrap")
    print(f"  repo     {REPO}")
    print(f"  python   {sys.version.split()[0]}  ({platform.system()} {platform.machine()})")

    if sys.version_info < (3, 10):  # noqa: UP036 -- runs on the user's interpreter,
        # which is precisely the one that might be too old. Not dead code here.
        return fail(f"Python 3.10+ required, found {sys.version.split()[0]}",
                    "Install a newer Python and re-run.")

    # ---------------------------------------------------------------- venv
    if a.no_venv:
        py = Path(sys.executable)
        step(f"using the current environment ({py})")
    else:
        vdir = REPO / ".venv"
        py = venv_python(vdir)
        if py.exists():
            step(f"virtualenv already present ({vdir})")
        else:
            step(f"creating virtualenv at {vdir}")
            venv.EnvBuilder(with_pip=True, clear=False).create(vdir)
            if not py.exists():
                return fail("virtualenv was created but has no interpreter")
            print(f"  {py}")

    # ------------------------------------------------------------- install
    step("installing binomen and its dependencies")
    r = run([str(py), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
            capture_output=True)
    r = run([str(py), "-m", "pip", "install", "--quiet", "-e", f"{REPO}[eval,dev]"],
            capture_output=True)
    if r.returncode != 0:
        return fail("pip install failed:\n" + (r.stderr or r.stdout),
                    "If this is a network or proxy problem, fix that and re-run.")
    print("  installed")

    # --------------------------------------------------------------- index
    stage1 = REPO / "data" / "binomen-stage1.sqlite"
    if stage1.exists() and not a.build_index:
        step("index already present")
        run([str(py), "-m", "binomen.fetch_index", "--check-age"])
    elif a.build_index:
        step("building the index from NCBI taxdump (this takes a few minutes)")
        r = run([str(py), "-m", "binomen.build.build_index"])
        if r.returncode != 0:
            return fail("index build failed",
                        "Re-run, or fetch a prebuilt index by omitting --build-index.")
    else:
        step("fetching a prebuilt index")
        cmd = [str(py), "-m", "binomen.fetch_index",
               "--which", "both" if a.full_index else "stage1"]
        if a.manifest:
            cmd += ["--manifest", a.manifest]
        if run(cmd).returncode != 0:
            print("\n  Could not fetch a prebuilt index. Building from source instead;"
                  "\n  this downloads ~400 MB from NCBI and takes a few minutes.", flush=True)
            if run([str(py), "-m", "binomen.build.build_index"]).returncode != 0:
                return fail("both fetching and building the index failed",
                            "Check network access to github.com and ftp.ncbi.nlm.nih.gov.")

    # --------------------------------------------------------------- verify
    step("verifying the server")
    r = run([str(py), "-m", "binomen.server", "--check"], capture_output=True)
    print("  " + (r.stdout or r.stderr).strip().replace("\n", "\n  "))
    if r.returncode != 0:
        return fail("the server self-check did not pass",
                    "Fix the problems above before registering it with a client.")

    # --------------------------------------------------------------- client
    if a.client != "none":
        step(f"registering with Claude {'Code' if a.client == 'code' else 'Desktop'}")
        cmd = [str(py), str(REPO / "scripts" / "install_claude_desktop.py"),
               "--descriptions", a.descriptions]
        if a.client == "code":
            cmd.append("--claude-code")
        rc = run(cmd).returncode
        if rc != 0:
            print("  registration did not complete; the server itself is fine.")

    # ----------------------------------------------------------------- done
    print("\n" + "=" * 68)
    print("binomen is installed.")
    print("=" * 68)
    print("\nTry it:")
    if a.client == "code":
        claude = shutil.which("claude") or "claude"
        print(f"  cd to a directory that is NOT this repo, then:  {claude}")
        print('  then ask something like: "Is Clostridium difficile still the accepted name?"')
    elif a.client == "desktop":
        print("  Quit Claude Desktop completely (system tray), reopen, and start a new chat.")
    print("\nUseful commands:")
    print(f"  {py} -m binomen.doctor                      what is installed, and probes")
    print(f"  {py} -m binomen.fetch_index --check-age     how stale is the index")
    print(f"  {py} -m binomen.server --check              is the server healthy")
    if not a.full_index:
        print("\nOnly the small stage-1 index is installed, which answers 'does this name need")
        print("a closer look'. For full local resolution (synonyms, change history, lineage):")
        print(f"  {py} -m binomen.fetch_index --which stage2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
