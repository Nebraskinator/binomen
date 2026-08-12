"""binomen — code-aware biological name resolution for agents.

Two artifacts share this package: an MCP server exposing deterministic,
provenance-carrying name-resolution tools, and an evaluation harness that
measures whether an agent knows to check a name at all.

The package had no `__init__.py` until it was noticed by a test asserting that
every directory under `src/binomen` is an importable package. It had been
working as an implicit namespace package, which is fragile and conceals exactly
the sort of packaging error that shipped alongside it: an unanchored `build/`
line in .gitignore silently excluded the entire index-building subpackage from
the repository.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
