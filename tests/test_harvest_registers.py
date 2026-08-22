"""The register harvester's filter, which is where every judgement call lives.

The download routes are not tested here -- they are network shape, and
`test_stages.py` covers what the shipped database must look like. What is tested
is the part that decides whether a name is worth shipping, because
`docs/FINDINGS.md` §8 is a list of eight results this project got wrong from
instruments nobody checked, and four of them were filters.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from binomen.build.harvest_registers import (
    ALL_SPECS,
    REGISTERS,
    Row,
    _status,
    ambiguous_only,
    bound_to_backbone,
    enforce_budget,
    enforce_bundle_budget,
    overlay_lpsn_medical_use,
    write,
)


def row(name, status="accepted", *, ident=None, rank="species",
        accepted_id=None, accepted_name=None, native=None):
    extras = {"_accepted_name": accepted_name} if accepted_name else {}
    return Row(ident=ident or name, name=name, rank=rank, status=status,
               native_status=native or status, accepted_id=accepted_id,
               link=None, extras=extras)


class TestStatus:
    def test_bare_names_have_no_standing(self):
        """Under every code a bare name was never validly published.

        It is the largest single category in the fungal register (162,756 of
        491,586) and calling it a weaker kind of acceptance would flood the
        database with names that say nothing.
        """
        assert _status("bare name") == "no_standing"

    def test_nom_status_can_override(self):
        assert _status("accepted", "not established") == "no_standing"

    def test_native_terms_are_not_flattened(self):
        """codes.py rule 1: the source's own word survives alongside ours."""
        assert _status("ambiguous synonym") == "synonym"
        assert _status("misapplied") == "synonym"


class TestAmbiguityFilter:
    def test_synonym_is_kept(self):
        kept, stats = ambiguous_only([row("Borreliella burgdorferi", "synonym")])
        assert [r.name for r in kept] == ["Borreliella burgdorferi"]
        assert stats["synonym"] == 1

    def test_accepted_target_comes_along(self):
        """A row saying 'this is wrong' is useless without what is right."""
        rows = [row("Old name", "synonym", ident="a", accepted_id="b"),
                row("New name", "accepted", ident="b")]
        kept, stats = ambiguous_only(rows)
        assert {r.name for r in kept} == {"Old name", "New name"}
        assert stats["accepted_target"] == 1

    def test_unambiguous_accepted_is_dropped(self):
        """The register has no news about it, so silence is the answer."""
        kept, stats = ambiguous_only([row("Quiet name", "accepted")])
        assert kept == []
        assert stats["unambiguous_dropped"] == 1

    def test_accepted_is_kept_when_the_backbone_retired_it(self):
        """The cross-source half of the rule.

        Without it a register like ICTV's -- every name accepted, no internal
        synonymy -- contributes nothing at all, which is exactly what the first
        run of this harvester produced.
        """
        kept, stats = ambiguous_only([row("Borrelia burgdorferi", "accepted")],
                                     superseded={"borrelia burgdorferi"})
        assert [r.name for r in kept] == ["Borrelia burgdorferi"]
        assert stats["backbone_disagrees"] == 1

    def test_ranks_above_genus_are_dropped(self):
        kept, stats = ambiguous_only([row("Borreliaceae", "synonym", rank="family")])
        assert kept == []
        assert stats["rank_dropped"] == 1

    def test_no_standing_is_kept(self):
        kept, stats = ambiguous_only([row("Nomen nudum", "no_standing")])
        assert len(kept) == 1
        assert stats["no_standing"] == 1


class TestBackboneBound:
    def _index(self, tmp_path, names):
        p = tmp_path / "backbone.sqlite"
        db = sqlite3.connect(p)
        db.execute("CREATE TABLE name_norm (norm TEXT NOT NULL, taxid INTEGER, "
                   "name TEXT, name_class TEXT)")
        db.executemany("INSERT INTO name_norm VALUES (?,1,?,'scientific name')",
                       [(n, n) for n in names])
        db.commit()
        db.close()
        return p

    def test_names_the_backbone_never_recorded_are_dropped(self, tmp_path):
        idx = self._index(tmp_path, ["known fungus"])
        rows = [row("Known fungus", "synonym"), row("Obscure fungus", "synonym")]
        kept, dropped = bound_to_backbone(rows, idx)
        assert [r.name for r in kept] == ["Known fungus"]
        assert dropped == 1

    def test_the_partner_survives_even_if_the_backbone_lacks_it(self, tmp_path):
        """Knowing the old name is what lets us answer someone who wrote it."""
        idx = self._index(tmp_path, ["known fungus"])
        rows = [row("Known fungus", "synonym", accepted_name="Renamed fungus"),
                row("Renamed fungus", "accepted")]
        kept, _ = bound_to_backbone(rows, idx)
        assert {r.name for r in kept} == {"Known fungus", "Renamed fungus"}

    def test_a_missing_index_bounds_nothing(self, tmp_path):
        """Absent input must not silently shrink the register."""
        rows = [row("Anything", "synonym")]
        kept, dropped = bound_to_backbone(rows, tmp_path / "nope.sqlite")
        assert kept == rows and dropped == 0


class TestWrite:
    def test_an_accepted_name_equal_to_its_own_name_is_refused(self, tmp_path):
        """The echo fault from FINDINGS §8, refused at write time.

        A resolver that returns its input when it does not know the answer was
        written independently in three places in this codebase, and downstream a
        comparison read those echoes as a 98% disagreement rate that did not
        exist.
        """
        out = tmp_path / "registers.sqlite"
        write(REGISTERS["lpsn"],
              [row("Same name", "synonym", accepted_name="Same name")], out, {})
        db = sqlite3.connect(out)
        assert db.execute("SELECT accepted_name FROM register").fetchone()[0] is None

    def test_provenance_travels_with_the_rows(self, tmp_path):
        """LPSN's terms require a link; a column cannot be dropped the way a
        footnote can."""
        out = tmp_path / "registers.sqlite"
        r = row("Borreliella burgdorferi", "synonym", accepted_name="Borrelia burgdorferi")
        r.link = "https://doi.org/10.83108/rn.792726"
        write(REGISTERS["lpsn"], [r], out, {})
        db = sqlite3.connect(out)
        got = db.execute("SELECT norm, accepted_name, link, code FROM register").fetchone()
        assert got == ("borreliella burgdorferi", "Borrelia burgdorferi",
                       "https://doi.org/10.83108/rn.792726", "ICNP")
        meta = dict(db.execute("SELECT key, value FROM meta"))
        assert meta["lpsn.licence"] == "CC BY-SA 4.0"

    def test_rewriting_one_register_leaves_the_others_alone(self, tmp_path):
        out = tmp_path / "registers.sqlite"
        write(ALL_SPECS["sfp"], [row("Candida auris", "synonym",
                                     accepted_name="Candidozyma auris")], out, {})
        write(REGISTERS["lpsn"], [row("Borreliella burgdorferi", "synonym",
                                      accepted_name="Borrelia burgdorferi")], out, {})
        db = sqlite3.connect(out)
        assert dict(db.execute("SELECT source, count(*) FROM register GROUP BY 1")) == {
            "sfp": 1, "lpsn": 1}


class TestMedicalUseOverlay:
    """LPSN's lorn_status, which is the one field the mirror does not carry."""

    def _lpsn(self, tmp_path):
        p = tmp_path / "lpsn.sqlite"
        db = sqlite3.connect(p)
        db.execute("CREATE TABLE lpsn (norm TEXT, full_name TEXT, correct_name TEXT, "
                   "medical_use TEXT)")
        db.executemany("INSERT INTO lpsn VALUES (?,?,?,?)", [
            ("borreliella burgdorferi", "Borreliella burgdorferi",
             "Borrelia burgdorferi", "not recommended"),
            ("borrelia burgdorferi", "Borrelia burgdorferi", None, "recommended"),
        ])
        db.commit()
        db.close()
        return p

    def _registers(self, tmp_path):
        out = tmp_path / "registers.sqlite"
        r = row("Borreliella burgdorferi", "synonym", accepted_name="Borrelia burgdorferi")
        write(REGISTERS["lpsn"], [r], out, {})
        return out

    def test_the_preferred_name_s_flag_travels_with_the_row(self, tmp_path):
        """The whole point, and the easiest thing to get backwards.

        LPSN attaches the recommendation to a name, so the superseded name reads
        "not recommended". A caller seeing only that beside `accepted_name:
        Borrelia burgdorferi` would conclude the opposite of what LPSN says.
        """
        out = self._registers(tmp_path)
        stats = overlay_lpsn_medical_use(out, self._lpsn(tmp_path))
        extras = json.loads(sqlite3.connect(out).execute(
            "SELECT extras FROM register WHERE norm='borreliella burgdorferi'").fetchone()[0])
        assert extras["medical_use"] == "not recommended"
        assert extras["accepted_medical_use"] == "recommended"
        assert stats["recommending"] == 1

    def test_an_absent_harvest_is_not_an_error(self, tmp_path):
        """Credentials are the maintainer's, so a build without them must still
        produce a register -- just one with no medical-use flags."""
        out = self._registers(tmp_path)
        stats = overlay_lpsn_medical_use(out, tmp_path / "nothing.sqlite")
        assert stats == {"lpsn_rows": 0, "matched": 0, "recommending": 0}
        assert sqlite3.connect(out).execute(
            "SELECT count(*) FROM register").fetchone()[0] == 1

    def test_the_overlay_only_adds(self, tmp_path):
        """The register's own name, status and link come from ChecklistBank and
        must stay traceable to it."""
        out = self._registers(tmp_path)
        before = sqlite3.connect(out).execute(
            "SELECT name, status, accepted_name, link FROM register").fetchone()
        overlay_lpsn_medical_use(out, self._lpsn(tmp_path))
        after = sqlite3.connect(out).execute(
            "SELECT name, status, accepted_name, link FROM register").fetchone()
        assert before == after


class TestBudget:
    def test_over_budget_fails_the_build(self, tmp_path):
        """A size preference erodes one register at a time; an invariant does not."""
        p = tmp_path / "big.sqlite"
        p.write_bytes(b"x" * 2_000_000)
        with pytest.raises(SystemExit):
            enforce_budget(p, 1.0)

    def test_within_budget_is_silent(self, tmp_path):
        p = tmp_path / "small.sqlite"
        p.write_bytes(b"x" * 1000)
        enforce_budget(p, 1.0)


def test_every_register_declares_its_licence_and_code():
    """A register without a licence string cannot be shipped lawfully, and one
    without a code cannot have jurisdiction decided."""
    for key, spec in REGISTERS.items():
        assert spec.licence and spec.code and spec.dataset
        assert spec.key == key


class TestBundleBudget:
    """The ceiling that matters is the compressed one: an .mcpb is a zip, and
    that is what a biologist waits for."""

    def _dbs(self, tmp_path, payload):
        out = []
        for i in (1, 2):
            p = tmp_path / f"part{i}.sqlite"
            p.write_bytes(payload)
            out.append(p)
        return out

    def test_compressible_data_is_judged_compressed(self, tmp_path):
        # 8 MB of highly compressible bytes: far over a 1 MB disk ceiling, far
        # under a 1 MB zip ceiling. Judging it uncompressed would refuse a
        # bundle that ships fine.
        paths = self._dbs(tmp_path, b"a" * 4_000_000)
        got = enforce_bundle_budget(paths, max_zip_mb=1.0, max_disk_mb=100.0)
        assert got["disk_mb"] > 7
        assert got["zipped_mb"] < 1

    def test_over_the_compressed_ceiling_fails(self, tmp_path):
        import os
        paths = self._dbs(tmp_path, os.urandom(2_000_000))
        with pytest.raises(SystemExit):
            enforce_bundle_budget(paths, max_zip_mb=1.0, max_disk_mb=100.0)

    def test_over_the_disk_ceiling_fails(self, tmp_path):
        paths = self._dbs(tmp_path, b"a" * 4_000_000)
        with pytest.raises(SystemExit):
            enforce_bundle_budget(paths, max_zip_mb=25.0, max_disk_mb=1.0)
