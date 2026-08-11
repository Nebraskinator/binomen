
from binomen.codes import Code, Status


def test_synonym_resolves_to_accepted_name(resolver):
    r = resolver.resolve_name("Clostridium difficile")
    assert [c.accepted_name for c in r.candidates] == ["Clostridioides difficile"]
    assert r.governing_code.code is Code.ICNP


def test_input_status_and_candidate_status_are_distinct(resolver):
    """'Clostridioides difficile is a synonym' is the reading we must not produce."""
    r = resolver.resolve_name("Clostridium difficile")
    assert r.input_status.normalized is Status.SYNONYM
    assert r.candidates[0].status.normalized is Status.ACCEPTED


def test_every_candidate_carries_provenance(resolver):
    for q in ["Escherichia coli", "Clostridium difficile", "Candida auris"]:
        r = resolver.resolve_name(q)
        for c in r.candidates:
            assert c.provenance is not None
            assert c.provenance.source and c.provenance.version and c.provenance.retrieved


def test_no_toplevel_current_name_field(resolver):
    """A single-string return type would hide contested cases. It must not exist."""
    d = resolver.resolve_name("Candida auris").to_dict()
    assert "current_name" not in d
    assert isinstance(d["candidates"], list)


def test_contested_case_returns_multiple_candidates(resolver):
    r = resolver.resolve_name("Candida auris")
    assert r.contested is True
    names = {c.accepted_name for c in r.candidates}
    assert {"Candida auris", "Candidozyma auris"} <= names
    assert any("DISAGREE" in w for w in r.warnings)


def test_homonym_returns_both_and_disambiguates(resolver):
    r = resolver.resolve_name("Bacillus")
    assert len(r.candidates) == 2
    disamb = {c.disambiguation for c in r.candidates}
    assert any(d and "bacteria" in d for d in disamb)
    assert any(d and "insect" in d for d in disamb)
    assert any("homonym" in w for w in r.warnings)


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


def test_merged_taxid_is_followed(resolver):
    """merged.dmp is NCBI recording that two taxa were unified."""
    live, moved = resolver.db.resolve_taxid(1428020)
    assert live == 1496 and moved is True


def test_check_currency_flags_superseded_name(resolver):
    c = resolver.check_currency("Enterobacter aerogenes")
    assert c["is_current_accepted_name"] is False
    assert "Klebsiella aerogenes" in c["accepted_names_now"]
    assert any("NOT the currently accepted" in w for w in c["warnings"])


def test_check_currency_admits_when_as_of_is_unanswerable(resolver):
    c = resolver.check_currency("Lactobacillus casei", as_of="2015-01-01")
    if not c["as_of_answerable"]:
        assert any("cannot be answered from data" in w for w in c["warnings"])


def test_get_lineage_names_its_source_and_its_limits(resolver):
    lg = resolver.get_lineage("Escherichia coli")
    assert "not a phylogenetic hypothesis" in lg["classification_source"]


def test_list_authorities_reports_unconfigured_sources(resolver):
    a = resolver.list_authorities("Fungi")
    assert a["governing_code"]["code"] == "ICNafp"
    assert "mycobank" in a["not_configured"]
    assert a["status_vocabulary"]


def test_reclassifications_reports_its_own_incompleteness(resolver):
    r = resolver.list_reclassifications("Lactobacillaceae", limit=20)
    assert "lower bound" in r["completeness_warning"]
