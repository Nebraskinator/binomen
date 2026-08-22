"""The shipped backbone half: what survives the trip out of stage 1.

Every assertion here is about a decision that changes what a user is told --
which names are worth shipping, which are placeholders, and whether absence
stays readable.
"""

from __future__ import annotations

import sqlite3

import pytest

from binomen.build.build_ambiguity import (
    CODES,
    NAME_SEP,
    VERDICTS,
    build,
    shape_of,
)


class TestShape:
    @pytest.mark.parametrize("name, expected", [
        ("escherichia coli", "binomial"),
        ("bacteroides", "genus"),
        ("treponema pallidum subsp. pertenue", "infraspecific"),
        ("uncultured bacteroides sp.", "placeholder"),
        ("candidatus pelagibacter ubique", "candidatus"),
        ("escherichia coli o157:h7 str. sakai", "other"),
    ])
    def test_classification(self, name, expected):
        assert shape_of(name) == expected

    def test_placeholders_are_not_names_anyone_writes(self):
        """A third of stage 1 is these, and shipping them buys nothing."""
        assert shape_of("unclassified Bacteroides") == "placeholder"
        assert shape_of("environmental samples") == "placeholder"


@pytest.fixture
def stage1(tmp_path):
    """A stage-1 index small enough to reason about, in the real schema."""
    p = tmp_path / "stage1.sqlite"
    db = sqlite3.connect(p)
    db.executescript("""
        CREATE TABLE verdicts (norm TEXT NOT NULL, verdict TEXT NOT NULL,
            code TEXT NOT NULL, taxid INTEGER, accepted TEXT);
        CREATE TABLE bloom (code TEXT PRIMARY KEY, n INTEGER NOT NULL, blob BLOB NOT NULL);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    db.executemany("INSERT INTO verdicts VALUES (?,?,?,?,?)", [
        ("bacteroides vulgatus", "superseded", "ICNP", 821, "Phocaeicola vulgatus"),
        ("phocaeicola vulgatus", "has_synonyms", "ICNP", 821, None),
        # Same spelling, two codes: the homonym case.
        ("bacillus", "has_synonyms", "ICNP", 1386, None),
        ("bacillus", "homonym", "ICZN", 55087, None),
        # NCBI's own table carries these: a "replacement" that is the same name.
        ("shewanella colwelliana", "superseded", "ICNP", 23, "Shewanella colwelliana"),
        # Shapes that must not ship.
        ("uncultured bacteroides sp.", "superseded", "ICNP", 9, "Bacteroides"),
        ("candidatus pelagibacter ubique", "has_synonyms", "ICNP", 10, None),
    ])
    db.execute("INSERT INTO bloom VALUES ('ICNP', 3, ?)", (b"\x01\x02\x03",))
    db.execute("INSERT INTO meta VALUES ('taxdump','taxdump-2026-08-13')")
    db.commit()
    db.close()
    return p


def read(out):
    db = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


class TestBuild:
    def test_only_real_name_shapes_ship(self, stage1, tmp_path):
        out = tmp_path / "ambiguity.sqlite"
        stats = build(stage1, out)
        names = {r["norm"] for r in read(out).execute("SELECT norm FROM amb")}
        # shewanella colwelliana is absent for a different reason -- see the
        # no-op test below -- so it is not in this set either.
        assert names == {"bacteroides vulgatus", "phocaeicola vulgatus", "bacillus"}
        assert stats["shape_dropped"] == 2

    def test_a_replacement_that_is_the_same_name_is_not_a_rename(self, stage1, tmp_path):
        """1,772 rows in the real index. Reporting them would invent a change."""
        out = tmp_path / "ambiguity.sqlite"
        stats = build(stage1, out)
        got = read(out).execute(
            "SELECT * FROM amb WHERE norm = 'shewanella colwelliana'").fetchall()
        assert got == []
        assert stats["noop"] == 1

    def test_one_spelling_under_two_codes_stays_two_rows(self, stage1, tmp_path):
        """Bacillus the bacterium and Bacillus the stick insect never merge.

        The key is (norm, code) precisely so the homonym case falls out of the
        data model instead of needing a rule that could be forgotten.
        """
        out = tmp_path / "ambiguity.sqlite"
        build(stage1, out)
        rows = read(out).execute(
            "SELECT code, cluster FROM amb WHERE norm = 'bacillus' ORDER BY code").fetchall()
        assert len(rows) == 2
        assert {r["cluster"] for r in rows} == {1386, 55087}
        assert {CODES[r["code"]] for r in rows} == {"ICNP", "ICZN"}

    def test_the_alternative_name_survives(self, stage1, tmp_path):
        out = tmp_path / "ambiguity.sqlite"
        build(stage1, out)
        row = read(out).execute(
            "SELECT verdict, accepted, cluster FROM amb "
            "WHERE norm = 'bacteroides vulgatus'").fetchone()
        assert row["accepted"] == "Phocaeicola vulgatus"
        assert VERDICTS[row["verdict"]] == "superseded"

    def test_names_of_one_taxon_share_a_cluster(self, stage1, tmp_path):
        """The old name and the current one are one disagreement, not two rows
        a caller has to correlate."""
        out = tmp_path / "ambiguity.sqlite"
        build(stage1, out)
        rows = read(out).execute(
            "SELECT cluster FROM amb WHERE norm IN "
            "('bacteroides vulgatus','phocaeicola vulgatus')").fetchall()
        assert {r["cluster"] for r in rows} == {821}

    def test_blooms_come_across_untouched(self, stage1, tmp_path):
        """Without them, a misspelling reads as an all-clear rather than as
        unknown -- the failure FINDINGS §5 calls worse than a wrong name."""
        out = tmp_path / "ambiguity.sqlite"
        build(stage1, out)
        row = read(out).execute("SELECT code, n, blob FROM bloom").fetchone()
        assert (row["code"], row["n"], bytes(row["blob"])) == ("ICNP", 3, b"\x01\x02\x03")

    def test_provenance_of_the_backbone_is_carried(self, stage1, tmp_path):
        out = tmp_path / "ambiguity.sqlite"
        build(stage1, out)
        meta = dict(read(out).execute("SELECT key, value FROM meta"))
        assert meta["licence"].startswith("public domain")
        assert meta["ncbi.taxdump"] == "taxdump-2026-08-13"

    def test_missing_stage1_is_a_refusal_not_an_empty_database(self, tmp_path):
        with pytest.raises(SystemExit):
            build(tmp_path / "absent.sqlite", tmp_path / "out.sqlite")


@pytest.fixture
def full_index(tmp_path):
    """A stand-in for binomen.sqlite, supplying display names for enumeration."""
    p = tmp_path / "binomen.sqlite"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE names (taxid INTEGER NOT NULL, name TEXT NOT NULL, "
               "unique_name TEXT, name_class TEXT NOT NULL)")
    db.executemany("INSERT INTO names VALUES (?,?,NULL,?)", [
        (821, "Phocaeicola vulgatus", "scientific name"),
        (821, "Bacteroides vulgatus", "synonym"),
        # NCBI files the authority-bearing form under `synonym`, not under
        # `authority`, so a name-class filter cannot remove it.
        (821, "Bacteroides vulgatus Eggerth and Gagnon 1933 (Approved Lists 1980)",
         "synonym"),
        (821, "(Eggerth and Gagnon 1933) Garcia 2019", "authority"),
        (821, "unidentified Bacteroides", "includes"),
        (1386, "Bacillus", "scientific name"),
    ])
    db.commit()
    db.close()
    return p


class TestEnumeration:
    def test_a_cluster_carries_every_name_of_its_taxon(self, stage1, full_index, tmp_path):
        """The point of packing: the bundle can answer "what else is this
        called" without the fetched stage-2 index."""
        out = tmp_path / "ambiguity.sqlite"
        stats = build(stage1, out, names_from=full_index)
        row = read(out).execute("SELECT names FROM cluster WHERE id = 821").fetchone()
        assert set(row["names"].split(NAME_SEP)) == {
            "Phocaeicola vulgatus", "Bacteroides vulgatus"}
        assert stats["clusters_packed"] == 1

    def test_author_citations_are_not_names(self, stage1, full_index, tmp_path):
        """1,057,536 `authority` rows, and they are long: including them took the
        compressed bundle from 21 MB to 32 MB, over its ceiling. An author
        citation is not something anyone searches for as a name."""
        out = tmp_path / "ambiguity.sqlite"
        build(stage1, out, names_from=full_index)
        names = read(out).execute(
            "SELECT names FROM cluster WHERE id = 821").fetchone()["names"]
        assert "authority" not in names
        assert "Garcia 2019" not in names
        assert "unidentified" not in names

    def test_a_taxon_with_one_name_is_not_packed(self, stage1, full_index, tmp_path):
        """A cluster of one has no alternatives to enumerate."""
        out = tmp_path / "ambiguity.sqlite"
        build(stage1, out, names_from=full_index)
        assert read(out).execute(
            "SELECT names FROM cluster WHERE id = 1386").fetchone() is None

    def test_a_build_without_enumeration_says_so(self, stage1, tmp_path):
        """get_synonyms must not quietly return less than the bundle promises."""
        out = tmp_path / "ambiguity.sqlite"
        stats = build(stage1, out, names_from=None)
        assert stats["clusters_packed"] == 0
        meta = dict(read(out).execute("SELECT key, value FROM meta"))
        assert meta["enumeration"] == "absent"

    def test_author_citations_are_stripped_from_packed_names(self, stage1, full_index,
                                                             tmp_path):
        """get_synonyms promises bare, searchable names.

        Taxid 821 carries "Bacteroides vulgatus Eggerth and Gagnon 1933 (Approved
        Lists 1980)" as a *synonym* row, so filtering by name class leaves it in.
        Pasting that string into PubMed returns nothing, which is the failure
        FINDINGS §4 measured in a different guise.
        """
        out = tmp_path / "ambiguity.sqlite"
        build(stage1, out, names_from=full_index)
        names = read(out).execute(
            "SELECT names FROM cluster WHERE id = 821").fetchone()["names"].split(NAME_SEP)
        assert names == ["Phocaeicola vulgatus", "Bacteroides vulgatus"]

    def test_the_scientific_name_comes_first(self, stage1, full_index, tmp_path):
        """Order is load-bearing: it is how the accepted name is recovered
        without spending a column on it."""
        out = tmp_path / "ambiguity.sqlite"
        build(stage1, out, names_from=full_index)
        names = read(out).execute(
            "SELECT names FROM cluster WHERE id = 821").fetchone()["names"].split(NAME_SEP)
        assert names[0] == "Phocaeicola vulgatus"
