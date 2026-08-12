"""The three-stage contract: cost, escalation, and honest degradation."""

import json
from pathlib import Path


def _size(obj) -> int:
    return len(json.dumps(obj, separators=(",", ":"), default=str))


def _a_stable_name(resolver) -> str:
    """A name the loaded index reports as stable, whichever fixture is in use."""
    for row in resolver.db.conn.execute(
            "SELECT name FROM name_norm WHERE name_class='scientific name' LIMIT 400"):
        if resolver.check_name(row["name"])["verdict"] == "stable":
            return row["name"]
    raise AssertionError("no stable name found in the loaded index")


class TestStage1Cost:
    """Stage 1 is only worth having if it is cheap enough to call on every
    organism mention. These are budget tests, and they should fail if a future
    change makes the response fat again."""

    def test_stable_name_is_tiny(self, resolver):
        """Pick a name the loaded index actually considers stable.

        Hardcoding 'Escherichia coli' here was another assumption: against real
        taxdump it has synonyms, so its verdict is `has_synonyms`, not `stable`.
        The budget is the thing under test, not the example."""
        row = resolver.s1.conn.execute("SELECT code FROM bloom LIMIT 1").fetchone()
        assert row, "no stable-name filter in the index"
        name = _a_stable_name(resolver)
        out = resolver.check_name(name)
        assert out["verdict"] == "stable", out
        assert out["escalate"] is False
        assert _size(out) < 200, f"stage 1 stable response grew to {_size(out)} chars"

    def test_every_stage1_response_is_small(self, resolver):
        for q in ["Escherichia coli", "Clostridium difficile", "Candida auris",
                  "Bacillus", "Nonsenseus fakus"]:
            assert _size(resolver.check_name(q)) < 400

    def test_stable_answer_needs_no_accepted_name(self, resolver):
        """For a stable name the accepted name IS the input -- that is what
        makes the response 20 tokens instead of 400."""
        out = resolver.check_name(_a_stable_name(resolver))
        assert "accepted_name" not in out


class TestEscalationContract:
    """The data decides the policy, not the model."""

    def test_superseded_escalates_to_stage_2(self, resolver):
        out = resolver.check_name("Clostridium difficile")
        assert out["verdict"] == "superseded"
        assert out["escalate"] is True
        assert out["next"] == "resolve_name"
        assert out["accepted_name"] == "Clostridioides difficile"

    def test_contested_escalates_past_stage_2_to_the_network(self, resolver):
        """A dispute is not expressible in taxdump, so stage 2 cannot settle
        it. Stage 1 should route around it."""
        out = resolver.check_name("Candida auris")
        assert out["verdict"] == "contested"
        assert out["next"] == "consult_authorities"

    def test_homonym_escalates(self, resolver):
        out = resolver.check_name("Bacillus")
        assert out["verdict"] == "homonym" and out["escalate"] is True

    def test_unknown_name_escalates_and_forbids_substitution(self, resolver):
        out = resolver.check_name("Nonsenseus fakus")
        assert out["verdict"] == "unknown"
        assert "Do not substitute" in out["do_not"]

    def test_every_escalation_names_its_next_tool(self, resolver):
        for q in ["Clostridium difficile", "Candida auris", "Bacillus", "Nonsenseus fakus"]:
            out = resolver.check_name(q)
            assert out["escalate"] is True
            assert out["next"] in {"resolve_name", "consult_authorities"}
            assert out.get("reason")


class TestBloomSafety:
    """The probabilistic structure may only ever err in the harmless direction."""

    def test_names_with_history_are_never_in_a_filter(self, resolver):
        """Every name that actually changed is in the exact verdict table. None
        may be certified 'stable' by a filter -- that is the safety property the
        whole hybrid design rests on. Checked exhaustively, not on examples."""
        verdicts = [r[0] for r in resolver.s1.conn.execute(
            "SELECT norm FROM verdicts LIMIT 3000")]
        assert verdicts
        for norm in verdicts:
            # The property is about the ANSWER, not the filters. A Bloom false
            # positive on a name that also has an exact verdict is harmless,
            # because the verdict table is consulted first -- and at p=0.001
            # over thousands of probes, some are expected.
            assert resolver.check_name(norm)["verdict"] != "stable", \
                f"{norm!r} has recorded history but check_name called it stable"

    def test_absence_from_all_filters_is_certain(self, resolver):
        """Bloom filters have no false negatives, so 'no record' is exact."""
        assert resolver.s1.codes_matching("Zzzzzzz qqqqqqq") == []
        assert resolver.check_name("Zzzzzzz qqqqqqq")["verdict"] == "unknown"


class TestStage1OnlyInstall:
    """30 MB should be a working install. It must degrade honestly, not crash."""

    def test_check_name_works_without_the_full_index(self, stage1_only):
        assert stage1_only.has_stage2 is False
        out = stage1_only.check_name("Escherichia coli")
        assert out["verdict"] in {"stable", "has_synonyms"}, out
        assert stage1_only.check_name("Clostridium difficile")["escalate"] is True

    def test_stage2_tools_say_so_rather_than_failing(self, stage1_only):
        r = stage1_only.resolve_name("Clostridium difficile")
        assert r.candidates == []
        assert any("not installed" in w for w in r.warnings)
        assert any("Do not substitute" in w for w in r.warnings)

    def test_compare_names_is_unknown_not_false_without_the_index(self, stage1_only):
        c = stage1_only.compare_names("Escherichia coli", "Bacillus subtilis")
        assert c["same_taxon"] is None


class TestStage2IsLocalOnly:
    def test_resolve_name_makes_no_network_calls(self, resolver, monkeypatch):
        """Stage 2 must have a predictable cost. Mixing a local lookup with
        four HTTP requests made latency depend on the name."""
        import binomen.authorities._http as http

        def boom(*a, **k):
            raise AssertionError("resolve_name attempted a network call")

        monkeypatch.setattr(http, "get_json", boom)
        r = resolver.resolve_name("Clostridium difficile")
        assert r.candidates


class TestResponseBudget:
    def test_no_code_description_on_every_response(self, resolver):
        """A ~300-char paragraph about ICNP on every call was most of a
        'nothing has changed' answer."""
        d = resolver.resolve_name("Clostridium difficile").to_dict()
        assert "description" not in d["governing_code"]

    def test_full_descriptions_are_still_reachable(self, resolver):
        a = resolver.list_authorities("Fungi")
        assert any(len(v) > 200 for v in a["codes"].values())

    def test_null_fields_are_omitted(self, resolver):
        d = resolver.resolve_name("Clostridium difficile").to_dict()
        for event in d["change_chain"]:
            assert "year" not in event or event["year"] is not None


class TestBuildIntegrity:
    """A silently wrong index is the worst possible outcome for this project.
    These make it a loud one."""

    def test_artifacts_are_single_self_contained_files(self, indexes):
        """No WAL. A stale -wal beside a deleted .sqlite can be recovered into
        a fresh build, resurrecting an old schema into what looks new."""
        import sqlite3
        for key in ("stage1", "stage2", "field"):
            p = indexes[key]
            conn = sqlite3.connect(p)
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
            conn.close()
            for suffix in ("-wal", "-shm", "-journal"):
                assert not (p.parent / (p.name + suffix)).exists()

    def test_stale_schema_raises_an_actionable_error(self, tmp_path):
        """Old and current `nodes` both have four columns, so a bad insert
        succeeds and only the read fails -- far from the cause."""
        import sqlite3

        from binomen.db import Backbone, IndexStale
        p = tmp_path / "old.sqlite"
        c = sqlite3.connect(p)
        c.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE nodes(taxid INTEGER PRIMARY KEY, parent_taxid INTEGER,"
            " rank TEXT, division TEXT);")
        c.commit()
        c.close()
        try:
            Backbone(p)
        except IndexStale as e:
            assert "code" in str(e) and "binomen-build-index" in str(e)
        else:
            raise AssertionError("a stale index was accepted")

    def test_taxa_with_history_survive_the_junk_filter(self, resolver):
        """The filter is a name-shape heuristic and drops about half of NCBI.
        It must never override evidence: anything carrying a synonym or a merge
        is kept regardless of what its name looks like."""
        from binomen.build.build_index import JUNK_NAME_RE
        assert JUNK_NAME_RE.search("Clostridium sp. nov.")
        for real in ["Clostridioides difficile", "Escherichia coli", "Candida auris",
                     "Homo sapiens", "Klebsiella aerogenes", "Pneumocystis jirovecii"]:
            assert not JUNK_NAME_RE.search(real), f"junk filter would eat {real}"

    def test_canary_list_covers_each_code(self):
        from binomen.build.build_index import CANARIES
        names = {n for n, _ in CANARIES}
        assert {"Escherichia coli", "Clostridium difficile", "Homo sapiens"} <= names


class TestNormalization:
    """The bug the canary caught, kept caught."""

    def test_ncbi_misplacement_brackets_are_folded_for_lookup(self):
        from binomen.build.build_index import normalize_name
        assert normalize_name("[Clostridium] difficile") == normalize_name("Clostridium difficile")
        assert normalize_name("[Ruminococcus] gnavus") == normalize_name("Ruminococcus gnavus")

    def test_a_bracket_only_synonym_is_findable_by_its_plain_form(self, resolver):
        """NCBI carries some synonyms ONLY in bracketed form. A user types the
        plain binomial. If the normalizer does not fold, the lookup misses
        silently -- the exact failure this project exists to detect."""
        out = resolver.check_name("Ruminococcus gnavus")
        assert out["verdict"] != "unknown", out
        assert out["escalate"] is True
        r = resolver.resolve_name("Ruminococcus gnavus")
        assert any("gnavus" in c.accepted_name for c in r.candidates), r.candidates

    def test_brackets_are_reported_not_silently_dropped(self, resolver):
        """The annotation means 'this genus placement is known to be wrong',
        which is advance warning of a rename. Worth saying."""
        bracketed = resolver.db.conn.execute(
            "SELECT name FROM name_norm WHERE name LIKE '[%' LIMIT 1").fetchone()
        if bracketed is None:
            import pytest
            pytest.skip("no bracketed names in this fixture slice")
        r = resolver.resolve_name(bracketed["name"])
        assert any("misplaced" in w for w in r.warnings), r.warnings

    def test_subspecific_markers_are_still_not_folded(self, resolver):
        """Folding brackets must not become an excuse to fold everything."""
        from binomen.build.build_index import normalize_name
        assert normalize_name("Escherichia coli") != normalize_name("Escherichia coli subsp. coli")

    def test_canary_failure_explains_by_looking(self, tmp_path):
        """The first version of this message asserted 'not present in the source
        archive at all' without ever checking the archive -- a confident
        unverified claim, from the tool whose subject is confident unverified
        claims. It must now report what it actually found."""
        import sqlite3

        from binomen.build.build_index import check_canaries
        db = tmp_path / "probe.sqlite"
        c = sqlite3.connect(db)
        c.executescript(
            "CREATE TABLE names(taxid INTEGER, name TEXT, unique_name TEXT, name_class TEXT);"
            "CREATE TABLE name_norm(norm TEXT, taxid INTEGER, name TEXT, name_class TEXT);")
        # Present in the data, but under a string whose key does not match --
        # exactly the normalization gap that shipped.
        c.execute("INSERT INTO names VALUES (1496,'[Clostridium] difficile','','synonym')")
        c.commit()
        failures = check_canaries(c, quiet=True)
        msg = next(f for f in failures if f.startswith("Clostridium difficile"))
        assert "[Clostridium] difficile" in msg
        assert "normalization gap" in msg
        c.close()


class TestAuthorityStripping:
    """NCBI stores many synonyms as full nomenclatural citations. Users type
    bare binomials. Both must resolve; only the key is bare, the stored string
    keeps the citation."""

    def test_bare_binomial_finds_a_name_stored_with_its_citation(self, resolver):
        out = resolver.check_name("Clostridium difficile")
        assert out["verdict"] == "superseded", out
        assert out["accepted_name"] == "Clostridioides difficile"

    def test_strain_designations_are_not_mistaken_for_authorities(self, resolver):
        """A strain must never inherit its species' lookup key. Doing so made
        every species a hundreds-way homonym -- false conflation, produced by
        the indexer, which is the error this package grades as very major."""
        rows = resolver.db.conn.execute(
            "SELECT norm, COUNT(DISTINCT taxid) n FROM name_norm "
            "WHERE norm IN ('escherichia coli','clostridioides difficile','bacillus subtilis') "
            "GROUP BY norm").fetchall()
        for r in rows:
            assert r["n"] == 1, f"{r['norm']!r} resolves to {r['n']} taxa -- strains leaked in"

    def test_the_citation_is_preserved_not_discarded(self, resolver):
        """The authority is real information -- it just should not be required
        as input, and must not leak into search terms. Verbatim strings live in
        by_synonymy_type; all_names is the bare, searchable form."""
        syn = resolver.get_synonyms("Clostridium difficile")
        verbatim = [e["name"] for g in syn["by_synonymy_type"].values() for e in g]
        assert any("Prevot 1938" in n for n in verbatim), verbatim
        assert all("Prevot" not in n for n in syn["all_names"]), syn["all_names"]

    def test_full_citation_string_also_resolves(self, resolver):
        full = "Clostridium difficile (Hall and O'Toole 1935) Prevot 1938 (Approved Lists 1980)"
        assert resolver.check_name(full)["verdict"] == "superseded"

    def test_strain_shapes_are_rejected_at_every_rank(self):
        from binomen.build.build_index import strip_authority as sa
        for n in ["Clostridioides difficile QCD-32g58", "Escherichia coli O157:H7 str. Sakai",
                  "Bacillus subtilis ATCC 6051",
                  "Salmonella enterica subsp. enterica serovar Typhi"]:
            for rank in (None, "no rank", "strain", "species", "subspecies"):
                assert sa(n, None, rank) is None, f"{n!r} stripped at rank={rank!r}"

    def test_virus_names_are_never_stripped(self):
        """ICTV names are not binomials and are full of capitalised words and
        digits that resemble authorities."""
        from binomen.build.build_index import strip_authority
        for n in ["Severe acute respiratory syndrome coronavirus 2",
                  "Influenza A virus (A/Puerto Rico/8/1934(H1N1))",
                  "Human alphaherpesvirus 1"]:
            assert strip_authority(n, "ICTV") is None
            assert strip_authority(n, None) is None, f"ungated strip mangled {n}"

    def test_shapes_that_must_be_left_alone(self):
        from binomen.build.build_index import strip_authority
        assert strip_authority("Escherichia coli") is None          # already bare
        assert strip_authority("Bacteria") is None                  # uninomial
        assert strip_authority("Escherichia coli subsp. coli") is None

    def test_shapes_that_must_be_stripped(self):
        from binomen.build.build_index import strip_authority as sa
        assert sa("Homo sapiens Linnaeus, 1758") == "Homo sapiens"
        assert sa("Bacillus subtilis subsp. spizizenii Nakamura et al. 1999") == \
            "Bacillus subtilis subsp. spizizenii"
        assert sa("Candidatus Liberibacter asiaticus Jagoueix et al. 1997") == \
            "Candidatus Liberibacter asiaticus"
        assert sa("Rosa x damascena Mill.") == "Rosa x damascena"


class TestFixtureProvenance:
    def test_a_real_extraction_path_exists(self):
        """Hand-written fixtures encode the author's assumptions. There has to
        be a way to build one from the archive instead."""
        from binomen.build.build_index import extract_fixture
        seeds = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "seeds.txt"
        assert seeds.exists()
        names = [x for x in seeds.read_text().splitlines()
                 if x.strip() and not x.startswith("#")]
        assert len(names) >= 20
        assert callable(extract_fixture)


class TestSearchTermHygiene:
    """expand_query feeds a literature search. Anything unsearchable in it is
    not cosmetic -- it is the tool degrading the query it exists to improve."""

    def test_includes_entries_never_reach_search_terms(self, resolver):
        """NCBI's `includes` class is unidentified material filed under a taxon
        ("Candida sp. JHS-2008"), not a name for it."""
        for query in ("Clostridium difficile", "Candida auris"):
            r = resolver.resolve_name(query)
            if not r.resolution_id:
                continue
            for t in resolver.expand_query(r.resolution_id)["search_terms"]:
                assert " sp. " not in t and not t.endswith(" sp."), f"{t!r} in search terms"

    def test_no_strain_or_open_nomenclature_qualifiers(self, resolver):
        import re
        r = resolver.resolve_name("Clostridium difficile")
        for t in resolver.expand_query(r.resolution_id)["search_terms"]:
            assert not re.search(r"\b(sp|cf|aff|str)\.|\b(strain|isolate|clone)\b", t, re.I), t

    def test_includes_is_not_reported_as_synonymy(self, resolver):
        from binomen.codes import Status, normalize_status
        assert normalize_status("ncbi", "includes").normalized is Status.INCLUDES
        assert normalize_status("ncbi", "includes").normalized is not Status.SYNONYM


class TestContestedCoherence:
    def test_guidance_is_not_duplicated(self, resolver):
        """One overlay entry reachable by several keys emitted its guidance
        twice, which reads like two sources agreeing."""
        w = resolver.get_synonyms("Candida auris")["warnings"]
        assert len(w) == len(set(w)), w

    def test_contested_flag_agrees_with_the_name_list(self, resolver):
        """`contested: true` beside a one-element list invites a reader to take
        the single answer and ignore the flag."""
        out = resolver.consult_authorities("Candida auris")
        if out["contested"]:
            assert len(out["distinct_accepted_names"]) > 1, out["distinct_accepted_names"]


class TestStrainDecomposition:
    """A strain name is a binomial plus a laboratory designation. Only the
    binomial is governed by a code; the designation never changes. So a strain
    inherits its species' transfer, and that is derivable rather than storable
    -- which is why over a million strain taxa can be left out of the field
    index without losing coverage."""

    def test_splits_designation_from_binomial(self):
        from binomen.build.build_index import split_designation as sd
        assert sd("Clostridioides difficile 630") == ("Clostridioides difficile", "630")
        assert sd("Escherichia coli O157:H7 str. Sakai") == (
            "Escherichia coli", "O157:H7 str. Sakai")
        assert sd("Bacillus subtilis subsp. spizizenii ATCC 6633") == (
            "Bacillus subtilis subsp. spizizenii", "ATCC 6633")
        assert sd("Candidatus Liberibacter asiaticus psy62") == (
            "Candidatus Liberibacter asiaticus", "psy62")

    def test_author_citations_are_not_designations(self):
        """"Homo sapiens Linnaeus, 1758" must not yield strain "Linnaeus, 1758"."""
        from binomen.build.build_index import split_designation as sd
        assert sd("Homo sapiens Linnaeus, 1758") is None
        assert sd("Clostridium difficile (Hall and O'Toole 1935) Prevot 1938") is None

    def test_bare_binomials_and_viruses_are_left_alone(self):
        from binomen.build.build_index import split_designation as sd
        assert sd("Escherichia coli") is None
        assert sd("Bacteria") is None
        assert sd("Severe acute respiratory syndrome coronavirus 2", "ICTV") is None

    def test_strain_search_terms_span_the_genus_change(self, resolver):
        """The point of the whole exercise: someone searching for a strain must
        find the literature published under its old genus."""
        r = resolver.resolve_name("Clostridium difficile 630")
        if not r.resolution_id:
            import pytest
            pytest.skip("strain not present in this fixture slice")
        terms = resolver.expand_query(r.resolution_id)["search_terms"]
        assert any(t.startswith("Clostridioides difficile 630") for t in terms), terms
        assert any(t.startswith("Clostridium difficile 630") for t in terms), terms

    def test_designation_is_not_doubled(self, resolver):
        """Appending unconditionally produced 'Clostridioides difficile 630 630'."""
        r = resolver.resolve_name("Clostridium difficile 630")
        if not r.resolution_id:
            import pytest
            pytest.skip("strain not present in this fixture slice")
        for t in resolver.expand_query(r.resolution_id)["search_terms"]:
            assert not t.endswith("630 630"), t

    def test_field_index_omits_strains_entirely(self, indexes):
        """They are derivable, so storing them is waste -- and it is most of
        what would make this file too big to ship."""
        import sqlite3
        c = sqlite3.connect(indexes["field"])
        strains = c.execute(
            "SELECT count(*) FROM taxa WHERE accepted LIKE '% 630' "
            "OR accepted LIKE '%QCD-%' OR accepted LIKE '% str. %'").fetchone()[0]
        assert strains == 0
        c.close()
