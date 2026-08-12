"""MCP server. A thin wrapper over Resolver -- all logic lives there.

stdio transport, which is what Claude Desktop expects. Every tool returns JSON
text; no tool returns prose, because prose is where provenance goes to die.
"""

from __future__ import annotations

import functools
import json
import os
import sys

# The Python MCP SDK renamed FastMCP to MCPServer in 2.0. Support both so the
# server runs on whatever the reader already has installed.
try:
    from mcp.server import MCPServer as _Server  # SDK >= 2.0
except ImportError:                                # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server  # SDK 1.x

from .db import Backbone, IndexNotBuilt, IndexStale, Stage1
from .resolver import Resolver
from .tool_descriptions import descriptions, variant

mcp = _Server("binomen")
_resolver: Resolver | None = None
DESC = descriptions()


def resolver() -> Resolver:
    global _resolver
    if _resolver is None:
        _resolver = Resolver(
            use_live=os.environ.get("BINOMEN_LIVE", "1").lower() not in {"0", "false", "no"},
        )
    return _resolver


def _json(obj) -> str:
    """Compact, not pretty-printed.

    indent=2 added roughly a third to every response for whitespace a model
    does not need. On a 20-token check_name reply that is not a rounding error.
    """
    return json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"))


def _guard(fn):
    """Turn an unbuilt index into instructions rather than a traceback.

    A model that receives a stack trace concludes the tool is broken and
    answers from memory, which is the outcome we are trying to avoid. It has to
    get an actionable message and an explicit instruction not to guess.
    """
    @functools.wraps(fn)   # preserves the signature the SDK introspects for the schema
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except (IndexNotBuilt, IndexStale) as e:
            return _json({"error": str(e), "action_required": "build the index",
                          "do_not": "Do not answer this question from memory. Report that name "
                                    "resolution is unavailable."})
        except Exception as e:  # noqa: BLE001
            return _json({"error": f"{type(e).__name__}: {e}",
                          "do_not": "Do not substitute a remembered name for a failed lookup."})
    return wrapper


@mcp.tool(description=DESC["check_name"])
@_guard
def check_name(name: str) -> str:
    return _json(resolver().check_name(name))


@mcp.tool(description=DESC["resolve_name"])
@_guard
def resolve_name(name: str, group_hint: str | None = None) -> str:
    return _json(resolver().resolve_name(name, group_hint).to_dict())


@mcp.tool(description=DESC["consult_authorities"])
@_guard
def consult_authorities(name: str, question: str = "current_name",
                        as_of: str | None = None) -> str:
    return _json(resolver().consult_authorities(name, question, as_of))


@mcp.tool(description=DESC["check_currency"])
@_guard
def check_currency(name: str, as_of: str | None = None) -> str:
    return _json(resolver().check_currency(name, as_of))


@mcp.tool(description=DESC["get_synonyms"])
@_guard
def get_synonyms(name: str) -> str:
    return _json(resolver().get_synonyms(name))


@mcp.tool(description=DESC["expand_query"])
@_guard
def expand_query(resolution_id: str, include_vernacular: bool = False) -> str:
    return _json(resolver().expand_query(resolution_id, include_vernacular))


@mcp.tool(description=DESC["compare_names"])
@_guard
def compare_names(name_a: str, name_b: str) -> str:
    return _json(resolver().compare_names(name_a, name_b))


@mcp.tool(description=DESC["get_lineage"])
@_guard
def get_lineage(name: str) -> str:
    return _json(resolver().get_lineage(name))


@mcp.tool(description=DESC["list_reclassifications"])
@_guard
def list_reclassifications(group: str, since_year: int | None = None, limit: int = 100) -> str:
    return _json(resolver().list_reclassifications(group, since_year, limit))


@mcp.tool(description=DESC["list_authorities"])
@_guard
def list_authorities(group: str | None = None) -> str:
    return _json(resolver().list_authorities(group))


def selfcheck() -> int:
    """Start everything except the transport and report. `--check`.

    Exists to separate two failure modes that look identical from inside Claude
    Desktop: the server is broken, versus the client never launched it. If this
    prints OK, the problem is configuration or the client, not binomen.
    """
    import asyncio
    import json as _json

    ok = True
    print("binomen server self-check")
    print(f"  python   {sys.executable}")
    print(f"  package  {__file__}")
    try:
        import mcp as _sdk  # NOT `mcp` -- that name is the server instance
        print(f"  mcp sdk  {getattr(_sdk, '__version__', 'unknown')} ({_Server.__name__})")
    except Exception as e:  # noqa: BLE001
        print(f"  mcp sdk  FAILED: {e}")
        return 1

    for label, cls, env in (("stage 1", Stage1, "BINOMEN_STAGE1_DB"),
                            ("stage 2", Backbone, "BINOMEN_DB")):
        try:
            db = cls()
            print(f"  {label}  OK  {db.meta.get('version')}  {db.path}")
            db.close()
        except (IndexNotBuilt, IndexStale) as e:
            first = str(e).splitlines()[0]
            print(f"  {label}  {'MISSING' if isinstance(e, IndexNotBuilt) else 'STALE'}: {first}")
            if label == "stage 1":
                ok = False
        if os.environ.get(env):
            print(f"           {env}={os.environ[env]}")

    try:
        tools = asyncio.run(mcp.list_tools())
        print(f"  tools    {len(tools)}: {', '.join(t.name for t in tools)}")
        if len(tools) != 10:
            ok = False
    except Exception as e:  # noqa: BLE001
        print(f"  tools    FAILED: {type(e).__name__}: {e}")
        ok = False

    del _json
    try:
        r = resolver().check_name("Clostridium difficile")
        print(f"  probe    check_name('Clostridium difficile') -> {r.get('verdict')}")
    except Exception as e:  # noqa: BLE001
        print(f"  probe    FAILED: {type(e).__name__}: {e}")
        ok = False

    print()
    if ok:
        print("OK -- the server itself is fine. If Claude Desktop does not show it:")
        print("  * Settings > Developer: is Developer Mode toggled ON?")
        print("  * Did you QUIT from the system tray, not just close the window?")
        print("  * Settings > Developer > logs: any error from the binomen server?")
    else:
        print("NOT OK -- fix the above before touching Claude Desktop.")
    return 0 if ok else 1


def main() -> None:
    """Report which indexes are present. Stage 1 alone is a working install."""
    if "--check" in sys.argv:
        raise SystemExit(selfcheck())
    stage1 = stage2 = "NOT INSTALLED"
    try:
        s = Stage1()
        stage1 = f"{s.meta.get('version')} ({s.meta.get('n_verdicts')} verdicts, " \
                 f"{s.meta.get('n_stable')} stable names)"
        s.close()
    except (IndexNotBuilt, IndexStale) as e:
        stage1 = f"ERROR: {e}"
    try:
        b = Backbone()
        stage2 = f"{b.meta.get('version')} ({b.meta.get('n_names')} names, " \
                 f"{b.meta.get('build_profile')} profile)"
        b.close()
    except IndexNotBuilt:
        pass
    print(f"[binomen] stage 1: {stage1}", file=sys.stderr)
    print(f"[binomen] stage 2: {stage2}", file=sys.stderr)
    print(f"[binomen] descriptions={variant()}", file=sys.stderr)
    if stage1 == "NOT INSTALLED":
        print("[binomen] WARNING: no stage-1 index. Run: binomen-build-index", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
