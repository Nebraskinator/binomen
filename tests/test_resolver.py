
from binomen.codes import Code, Status


def test_synonym_resolves_to_accepted_name(resolver):
    r = resolver.resolve_name("Clostridium difficile")
    assert [c.accepted_name for c in r.candidates] == ["Clostridioides difficile"]
    assert r.governing_code.code is Code.ICNP
    assert not r.contested


def test_input_status_and_candidate_status_are_distinct(resolver):
    """'Clostridioides difficile is a synonym' is the reading we must not produce."""
    r = resolver.resolve_name("Clostridium difficile")
    assert r.input_status.normalized is Status.SYNONYM
    assert r.candidates[0].status.normalized is Status.ACCEPTED


def test_every_response_carries_provenance(resolver):
    """Provenance moved from per-candidate duplication to one block per source,
    with candidates attributing by source name. It must still be complete."""
    for q in ["Escherichia coli", "Clostridium difficile", "Candida auris"]:
        d = resolver.resolve_name(q).to_dict()
        assert d["provenance"], q
        for p in d["provenance"]:
            assert p["source"] and p["version"] and p["retrieved"]
        for c in d["candidates"]:
            assert c["sources"]


def test_no_toplevel_current_name_field(resolver):
    """A single-string return type would hide contested cases. It must not exist."""
    d = resolver.resolve_name("Candida auris").to_dict()
    assert "current_name" not in d
    assert isinstance(d["candidates"], list)
    assert "current_name" not in resolver.check_name("Candida auris")


def test_contested_case_returns_multiple_candidates(resolver):
    r = resolver.resolve_name("Candida auris")
    assert r.contested is True
    names = {c.accepted_name for c in r.candidates}
    assert {"Candida auris", "Candidozyma auris"} <= names
    assert any("DISAGREE" in w for w in r.warnings)


def test_homonym_returns_both_and_disambiguates(resolver):
    """Bacillus is a bacterium and a stick insect, under different codes.

    Asserts the disambiguation is *usable* rather than matching a particular
    string -- NCBI's unique_name wording is its business, not ours."""
    r = resolver.resolve_name("Bacillus")
    assert len(r.candidates) >= 2
    assert any("homonym" in w for w in r.warnings)
    codes = {tuple(c.lineage_summary)[:3] for c in r.candidates}
    assert len(codes) > 1, "candidates are indistinguishable to a reader"
    disamb = [c.disambiguation for c in r.candidates if c.disambiguation]
    assert len(set(disamb)) == len(disamb), "disambiguations are not distinct"


def test_unfound_name_does_not_invent_one(resolver):
    r = resolver.resolve_name("Xyzzyus imaginarius")
    assert r.candidates == []
    assert any("not found" in w for w in r.warnings)
    assert any("Do not substitute" in w or "do not substitute" in w.lower() for w in r.warnings)


def test_compare_names_same_taxon(resolver):
    c = resolver.compare_names("Clostridium difficile", "Clostridioides difficile")
    assert c["same_taxon"] is True
    assert "txid" in c["shared_identifier"]


def test_compare_names_distractor_pair(resolver):
    c = resolver.compare_names("Pneumocystis carinii", "Pneumocystis jirovecii")
    assert c["same_taxon"] is False
    assert c["nearest_shared_taxon"]["name"] == "Pneumocystis"


def test_unresolvable_comparison_is_unknown_not_different(resolver):
    """same_taxon=False would be read as 'different organisms'. It must be None."""
    c = resolver.compare_names("Escherichia coli", "Xyzzyus imaginarius")
    assert c["same_taxon"] is None
    assert c["confidence"] == "none"


def test_expand_query_requires_resolution(resolver):
    bad = resolver.expand_query("not-a-real-handle")
    assert "error" in bad and "search_terms" not in bad


def test_expand_query_covers_prior_names(resolver):
    r = resolver.resolve_name("Clostridioides difficile")
    e = resolver.expand_query(r.resolution_id)
    assert "Clostridium difficile" in e["search_terms"]
    assert "Peptoclostridium difficile" in e["search_terms"]
    assert any(t.startswith("C. ") for t in e["abbreviated_forms"])


def test_expand_query_emits_searchable_strings_not_citations(resolver):
    """NCBI stores synonyms as full nomenclatural citations. Pasting one into
    PubMed returns nothing -- a query-expansion tool producing a silently empty
    search is the failure it exists to prevent."""
    r = resolver.resolve_name("Clostridioides difficile")
    e = resolver.expand_query(r.resolution_id)
    import re
    for t in e["search_terms"]:
        assert "(" not in t and "[" not in t, f"unsearchable term: {t!r}"
        assert not re.search(r"\b(1[6-9]\d{2}|20[0-2]\d)\b", t), f"citation year in: {t!r}"
        assert " str. " not in t and " strain " not in t, f"strain designation in: {t!r}"


def test_the_verbatim_citation_is_still_available(resolver):
    """Stripped for searching, kept for citing."""
    groups = resolver.get_synonyms("Clostridioides difficile")["by_synonymy_type"]
    verbatim = [e["name"] for g in groups.values() for e in g]
    assert any("Prevot 1938" in n for n in verbatim), verbatim


def test_merged_taxid_is_followed(resolver):
    """merged.dmp is NCBI recording that two taxa were unified.

    Data-driven: takes whatever merge the loaded index actually contains, so it
    works against the synthetic fixture and a real extraction alike."""
    row = resolver.db.conn.execute("SELECT old_taxid, new_taxid FROM merged LIMIT 1").fetchone()
    assert row, "no merges in the loaded index"
    live, moved = resolver.db.resolve_taxid(row["old_taxid"])
    assert live == row["new_taxid"] and moved is True


def test_check_currency_flags_superseded_name(resolver):
    c = resolver.check_currency("Enterobacter aerogenes")
    assert c["is_current_accepted_name"] is False
    assert "Klebsiella aerogenes" in c["accepted_names_now"]
    assert any("is NOT the accepted name" in w for w in c["warnings"])


def test_check_currency_admits_when_as_of_is_unanswerable(resolver):
    c = resolver.check_currency("Lactobacillus casei", as_of="2015-01-01")
    if not c["as_of_answerable"]:
        assert any("cannot be answered from data" in w for w in c["warnings"])


def test_get_lineage_names_its_source_and_its_limits(resolver):
    lg = resolver.get_lineage("Escherichia coli")
    assert "not a phylogenetic hypothesis" in lg["classification_source"]


def test_lineage_is_walked_not_cached(resolver):
    """The materialized lineage cache was 62% of the index, ~840 bytes/taxon at
    real depth, to answer a six-way question. It is now a code column."""
    tables = {r[0] for r in resolver.db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "lineage_cache" not in tables
    assert resolver.db.lineage_names(1496)[:3] == ["root", "cellular organisms", "Bacteria"]


def test_code_is_precomputed_per_taxon(resolver):
    """These four taxids are real NCBI identifiers, so the assertion holds
    against the synthetic fixture and a real extraction alike."""
    for taxid, expected in ((1496, "ICNP"), (9606, "ICZN"),
                            (2697049, "ICTV"), (498019, "ICNafp")):
        got = resolver.db.code_for(taxid)
        if got is None:
            continue          # not present in this fixture's slice
        assert got == expected, f"txid{taxid}: expected {expected}, got {got}"


def test_list_authorities_reports_unconfigured_sources(resolver):
    a = resolver.list_authorities("Fungi")
    assert a["governing_code"] == "ICNafp"
    assert "mycobank" in a["not_configured"]
    assert a["status_vocabulary"]


def test_reclassifications_reports_its_own_incompleteness(resolver):
    r = resolver.list_reclassifications("Lactobacillaceae", limit=20)
    assert "lower bound" in r["completeness_warning"]
