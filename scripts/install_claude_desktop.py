#!/usr/bin/env python3
"""Register the binomen MCP server with Claude Desktop.

Writes a "binomen" entry into claude_desktop_config.json, creating the file if
absent and MERGING into it if present. Any existing config is backed up first --
it may hold other servers you care about.

Written in Python rather than PowerShell for an unglamorous reason: Python is
testable on the machine this was developed on, and the PowerShell version
shipped with a null-reference bug on its very first line of real work. Same
lesson as the rest of this project -- code that has not been run is a draft.

Usage
-----
    python scripts/install_claude_desktop.py
    python scripts/install_claude_desktop.py --descriptions imperative
    python scripts/install_claude_desktop.py --remove
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def candidate_config_paths() -> list[Path]:
    """Every place Claude Desktop might keep its config, most specific first.

    Windows has two, and the difference cost an evening. An MSIX/Store-packaged
    install gets a *virtualized* AppData: when the app writes to %APPDATA%\\Claude,
    Windows silently redirects it under

        %LOCALAPPDATA%\\Packages\\Claude_<hash>\\LocalCache\\Roaming\\Claude\\

    A config written to the unpackaged %APPDATA% path is simply a different file
    that the app never reads -- and nothing anywhere reports an error, which is
    precisely the silent-wrong-target failure this project is about, arriving
    from an unexpected direction.
    """
    out: list[Path] = []
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        for pkg in sorted((local / "Packages").glob("Claude_*")):
            out.append(pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
        roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        out.append(roaming / "Claude" / "claude_desktop_config.json")
    elif sys.platform == "darwin":
        out.append(Path.home() / "Library" / "Application Support" / "Claude" /
                   "claude_desktop_config.json")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        out.append(base / "Claude" / "claude_desktop_config.json")
    return out


def config_path() -> Path:
    """Pick the config Claude Desktop actually reads.

    Prefer a packaged location whose directory already exists -- if the app has
    created it, that is the one in use. Otherwise fall back to the first
    candidate.
    """
    cands = candidate_config_paths()
    for c in cands:
        if c.exists():
            return c
    for c in cands:
        if c.parent.exists():
            return c
    return cands[-1]


def python_exe() -> tuple[Path | str, str | None]:
    """The interpreter Claude Desktop should launch. Returns (path, warning)."""
    for rel in (Path(".venv") / "Scripts" / "python.exe",      # Windows
                Path(".venv") / "bin" / "python"):             # macOS / Linux
        cand = REPO / rel
        if cand.exists():
            return cand, None
    return sys.executable, (
        f"No virtualenv found at {REPO / '.venv'}. Falling back to the interpreter running "
        f"this script ({sys.executable}). That works only if binomen is importable from it."
    )


def entry(descriptions: str) -> dict:
    py, _ = python_exe()
    return {
        "command": str(py),
        "args": ["-m", "binomen.server"],
        "env": {
            "PYTHONPATH": str(REPO / "src"),
            "BINOMEN_STAGE1_DB": str(REPO / "data" / "binomen-stage1.sqlite"),
            "BINOMEN_DB": str(REPO / "data" / "binomen.sqlite"),
            "BINOMEN_DESCRIPTIONS": descriptions,
        },
    }


NEXT_STEPS = """
NEXT -- and step 1 is the one people miss:

  1. QUIT Claude Desktop completely. Closing the window is not enough; it keeps
     running in the system tray. Right-click the Claude icon in the tray
     (bottom-right on Windows, possibly hidden under the '^' arrow; menu bar on
     macOS) and choose Quit. Or File > Exit. Then reopen it.
     The config is only read at startup.

  2. Confirm it connected: click the '+' button at the bottom of the chat box,
     then 'Connectors'. You should see binomen with ten tools.

  3. If it is not there: Settings > Developer shows connection status and server
     logs. binomen prints the indexes it loaded to stderr on startup, so the log
     should begin with a line like
         [binomen] stage 1: taxdump-YYYY-MM-DD (N verdicts, M stable names)

  4. Then try this, and watch whether a tool is called at all:
         "Summarize the current treatment guidelines for C. difficile infection."
"""


def print_claude_code(descriptions: str, run: bool = True, remove: bool = False) -> int:
    """Register binomen with Claude Code, or print the command to do it.

    Claude Code is a second MCP client, and on a Pro or Max subscription it
    needs no API key -- which makes it the practical home for the conversational
    discovery loop when Claude Desktop will not cooperate. Same server, same
    tools, same descriptions.
    """
    py, _ = python_exe()
    e = entry(descriptions)["env"]
    envs = " ".join(f'-e {k}="{v}"' for k, v in e.items())

    # The native Windows installer drops claude.exe in ~/.local/bin and tells
    # you to add it to PATH yourself, so `claude` frequently is not resolvable
    # in the shell you are standing in. Emit whatever actually works from here.
    claude = shutil.which("claude")
    if claude is None:
        for cand in (Path.home() / ".local" / "bin" / "claude.exe",
                     Path.home() / ".local" / "bin" / "claude"):
            if cand.exists():
                claude = f'"{cand}"'
                break
    if claude is None:
        print("\nClaude Code does not appear to be installed. On Windows, in PowerShell:")
        print("    irm https://claude.ai/install.ps1 | iex")
        print("On macOS or Linux:")
        print("    curl -fsSL https://claude.ai/install.sh | bash")
        print("Then re-run this command.\n")
        return 1
    if not shutil.which("claude"):
        print(f"\nnote: `claude` is not on PATH; using {claude} directly.")
        print("      To fix permanently (PowerShell), then reopen the terminal:")
        print('        $bin = "$env:USERPROFILE\\.local\\bin"')
        print('        [Environment]::SetEnvironmentVariable("Path",'
              ' [Environment]::GetEnvironmentVariable("Path","User") + ";$bin", "User")')

    exe = claude.strip('"')

    if not run:
        print("\nRegister binomen with Claude Code (no API key needed on Pro/Max):\n")
        print(f"  {claude} mcp add binomen -s user {envs} -- \"{py}\" -m binomen.server")
        print(f"\nVerify:  {claude} mcp get binomen")
        print(f"Remove:  {claude} mcp remove binomen -s user\n")
        return 0

    # Actually run it. Printing a command for the user to paste failed twice in
    # practice -- once silently, leaving no server registered and producing an
    # experimental result that looked like a finding and was an artifact of the
    # tool being absent. A setup step that can be half-completed will be.
    import subprocess

    # Remove first, so re-running with a different --descriptions takes effect
    # instead of colliding with the existing entry.
    subprocess.run([exe, "mcp", "remove", "binomen", "-s", "user"],
                   capture_output=True, text=True)
    if remove:
        print("\nremoved binomen from Claude Code\n")
        return 0

    cmd = [exe, "mcp", "add", "binomen", "-s", "user"]
    for k, v in e.items():
        cmd += ["-e", f"{k}={v}"]
    cmd += ["--", str(py), "-m", "binomen.server"]

    print(f"\nbinomen -> Claude Code  (descriptions: {descriptions})")
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if out:
        print("  " + out.replace("\n", "\n  "))
    if r.returncode != 0:
        print(f"\n  registration FAILED (exit {r.returncode}). By hand:\n")
        print(f"  {claude} mcp add binomen -s user {envs} -- \"{py}\" -m binomen.server\n")
        return r.returncode

    # Verify rather than assume, and show which variant is actually live --
    # a run whose condition was not what you thought is worse than no run.
    g = subprocess.run([exe, "mcp", "get", "binomen"], capture_output=True, text=True)
    got = (g.stdout + g.stderr).strip()
    print("\n  verification:")
    for line in got.splitlines():
        if line.strip():
            print("    " + line)
    if descriptions not in got:
        print(f"\n  WARNING: BINOMEN_DESCRIPTIONS={descriptions} is not visible in the "
              f"registered entry. Do not treat the next run as a comparison until it is.")
    print("\nStart the session from a directory that does NOT contain the binomen repo,")
    print("or the model will read the source instead of using the tool:")
    print(f"    {claude}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--descriptions", choices=("narrow", "broad", "imperative"), default="broad",
                    help="tool-description variant to expose (README section 7)")
    ap.add_argument("--remove", action="store_true", help="remove the binomen entry")
    ap.add_argument("--config", type=Path, help="override the config path (for testing)")
    ap.add_argument("--force", action="store_true",
                    help="register even if the stage-1 index is missing")
    ap.add_argument("--claude-code", action="store_true",
                    help="register with Claude Code instead of writing a Claude Desktop config")
    ap.add_argument("--print-only", action="store_true",
                    help="with --claude-code, print the command instead of running it")
    a = ap.parse_args(argv)

    if a.claude_code:
        return print_claude_code(a.descriptions, run=not a.print_only, remove=a.remove)

    path = a.config or config_path()
    print("\nbinomen -> Claude Desktop")
    others = [c for c in candidate_config_paths() if c != path and (c.exists() or c.parent.exists())]
    if others and not a.config:
        print("  note    more than one Claude Desktop config location exists on this machine.")
        print(f"          using   {path}")
        for o in others:
            print(f"          also    {o}   ({'exists' if o.exists() else 'dir only'})")
        print("          If the server does not appear, the app may be reading a different one;"
              "\n          re-run with --config <path> to target it explicitly.")
    print(f"  repo    {REPO}")

    py, warning = python_exe()
    if warning:
        print(f"  WARNING {warning}")

    stage1 = REPO / "data" / "binomen-stage1.sqlite"
    stage2 = REPO / "data" / "binomen.sqlite"
    if not a.remove:
        if not stage1.exists() and not a.force:
            # Registering a server whose index is missing produces an opaque
            # failure inside Claude Desktop, which is a bad place to debug.
            print(f"\n  Stage-1 index not found: {stage1}")
            print("  Build it first:  binomen-build-index")
            print("  (or pass --force if you know what you are doing)")
            return 1
        if not stage2.exists():
            print("  note    stage-2 index absent; check_name and consult_authorities will work "
                  "and the\n          stage-2 tools will report themselves unavailable rather "
                  "than failing.")

    # --- load, backing up anything already there --------------------------
    config: dict = {}
    if path.exists():
        backup = path.with_name(path.name + f".binomen-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"  backup  {backup}")
        try:
            config = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as e:
            print(f"\n  {path} is not valid JSON ({e}). Not touching it.")
            print("  Fix or delete that file, then re-run.")
            return 1
        if not isinstance(config, dict):
            print(f"\n  {path} does not contain a JSON object. Not touching it.")
            return 1
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"  create  {path}")

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    config["mcpServers"] = servers

    # --- apply ------------------------------------------------------------
    if a.remove:
        if servers.pop("binomen", None) is None:
            print("  no binomen entry to remove")
        else:
            print("  removed the binomen entry")
    else:
        verb = "updated" if "binomen" in servers else "added"
        servers["binomen"] = entry(a.descriptions)
        print(f"  {verb}   binomen  (descriptions: {a.descriptions})")
        others = sorted(k for k in servers if k != "binomen")
        if others:
            print(f"  kept    {', '.join(others)}")

    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote   {path}")
    if not a.remove:
        print(NEXT_STEPS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
