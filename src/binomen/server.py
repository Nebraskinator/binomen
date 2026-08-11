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

from .db import Backbone, IndexNotBuilt
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
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


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
        except IndexNotBuilt as e:
            return _json({"error": str(e), "action_required": "build the index",
                          "do_not": "Do not answer this question from memory. Report that name "
                                    "resolution is unavailable."})
        except Exception as e:  # noqa: BLE001
            return _json({"error": f"{type(e).__name__}: {e}",
                          "do_not": "Do not substitute a remembered name for a failed lookup."})
    return wrapper


@mcp.tool(description=DESC["resolve_name"])
@_guard
def resolve_name(name: str, group_hint: str | None = None) -> str:
    return _json(resolver().resolve_name(name, group_hint).to_dict())


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


def main() -> None:
    try:
        b = Backbone()
        print(f"[binomen] index {b.meta.get('version')} "
              f"({b.meta.get('n_names')} names), descriptions={variant()}", file=sys.stderr)
        b.close()
    except IndexNotBuilt as e:
        print(f"[binomen] WARNING: {e}", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
