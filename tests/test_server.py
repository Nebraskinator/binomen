
import pytest


@pytest.mark.asyncio
async def test_all_ten_tools_are_registered(monkeypatch, indexes):
    monkeypatch.setenv("BINOMEN_DB", str(indexes["stage2"]))
    monkeypatch.setenv("BINOMEN_STAGE1_DB", str(indexes["stage1"]))
    monkeypatch.setenv("BINOMEN_OFFLINE", "1")
    from binomen.server import mcp
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {
        "check_name", "resolve_name", "consult_authorities", "check_currency",
        "get_synonyms", "expand_query", "compare_names", "get_lineage",
        "list_reclassifications", "list_authorities"}


@pytest.mark.asyncio
async def test_descriptions_name_the_trigger_condition(monkeypatch):
    """A description that only names the domain does not get the tool called."""
    monkeypatch.setenv("BINOMEN_DESCRIPTIONS", "broad")
    from binomen.tool_descriptions import descriptions
    d = descriptions()
    assert "WHENEVER" in d["check_name"].upper()
    assert "CHEAP" in d["check_name"].upper()
    assert len(d["check_name"]) > len(d["get_lineage"])


@pytest.mark.asyncio
async def test_stage_1_description_leads_with_cost(monkeypatch):
    """'Call this whenever a name appears' is only a reasonable ask for a tool
    that is cheap. The description has to say so, early."""
    monkeypatch.setenv("BINOMEN_DESCRIPTIONS", "broad")
    from binomen.tool_descriptions import descriptions
    head = descriptions()["check_name"][:200].lower()
    assert "cheap" in head or "2 ms" in head


@pytest.mark.asyncio
async def test_description_variants_differ_only_in_wording(monkeypatch):
    from binomen.tool_descriptions import descriptions
    monkeypatch.setenv("BINOMEN_DESCRIPTIONS", "narrow")
    narrow = descriptions()
    monkeypatch.setenv("BINOMEN_DESCRIPTIONS", "imperative")
    imperative = descriptions()
    assert set(narrow) == set(imperative)
    assert all(len(imperative[k]) > len(narrow[k])
               for k in ("check_name", "resolve_name", "expand_query"))
