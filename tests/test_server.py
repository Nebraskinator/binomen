
import pytest


@pytest.mark.asyncio
async def test_all_eight_tools_are_registered(monkeypatch, index):
    monkeypatch.setenv("BINOMEN_DB", str(index))
    monkeypatch.setenv("BINOMEN_OFFLINE", "1")
    from binomen.server import mcp
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {
        "resolve_name", "check_currency", "get_synonyms", "expand_query",
        "compare_names", "get_lineage", "list_reclassifications", "list_authorities"}


@pytest.mark.asyncio
async def test_descriptions_name_the_trigger_condition(monkeypatch):
    """A description that only names the domain does not get the tool called."""
    monkeypatch.setenv("BINOMEN_DESCRIPTIONS", "broad")
    from binomen.tool_descriptions import descriptions
    d = descriptions()
    assert "WHENEVER" in d["resolve_name"].upper()
    assert len(d["resolve_name"]) > len(d["get_lineage"])


@pytest.mark.asyncio
async def test_description_variants_differ_only_in_wording(monkeypatch):
    from binomen.tool_descriptions import descriptions
    monkeypatch.setenv("BINOMEN_DESCRIPTIONS", "narrow")
    narrow = descriptions()
    monkeypatch.setenv("BINOMEN_DESCRIPTIONS", "imperative")
    imperative = descriptions()
    assert set(narrow) == set(imperative)
    assert all(len(imperative[k]) > len(narrow[k]) for k in ("resolve_name", "expand_query"))
