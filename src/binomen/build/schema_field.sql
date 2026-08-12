-- binomen "field edition" index.
--
-- One file, self-contained, sized for a laptop. Carries exactly what a working
-- biologist asks of this tool:
--
--   is my name current?          -> lookup.verdict
--   what is it now?              -> taxa.accepted
--   what else has it been called? -> taxa.synonyms
--   what do I search to find     -> taxa.synonyms, bare and searchable
--     the older literature?
--   do authorities disagree?     -> notes
--
-- And deliberately not: the three-million-name backbone, lineage, rank
-- hierarchy, reclassification listings. Those are stage-2 questions and they
-- are what makes the full index 525 MB. A bench biologist downloading half a
-- gigabyte to check a species list is a tool that does not get installed.
--
-- Not WAL: written once, shipped, read-only. A stale -wal sidecar next to a
-- replaced database can resurrect an old schema, which cost an afternoon once
-- already.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Every name worth an exact answer: anything superseded, homonymous, contested,
-- or carrying synonyms. Names with no recorded history are NOT here -- they are
-- certified absent by the bloom filters, which is both smaller and safe (bloom
-- filters have no false negatives, so "not in the filter" is certain).
CREATE TABLE IF NOT EXISTS lookup (
    norm    TEXT NOT NULL,
    taxid   INTEGER,
    verdict TEXT NOT NULL,   -- superseded | has_synonyms | homonym | contested
    code    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lookup_norm ON lookup(norm);

-- One row per taxon that any lookup points at.
CREATE TABLE IF NOT EXISTS taxa (
    taxid     INTEGER PRIMARY KEY,
    accepted  TEXT NOT NULL,
    rank      TEXT,
    code      TEXT NOT NULL,
    authority TEXT,          -- author citation, when the source carries one
    synonyms  TEXT           -- JSON array of bare, searchable prior names
);

-- Per-code filters over names with NO recorded history. Membership in exactly
-- one filter means "no change recorded, governed by this code". Absence from
-- all of them is certain, and means we have no record of the name at all.
CREATE TABLE IF NOT EXISTS bloom (
    code TEXT PRIMARY KEY,
    n    INTEGER NOT NULL,
    blob BLOB NOT NULL
);

-- Curated overlay: the things taxdump structurally cannot say. That a synonymy
-- is disputed, who disputes it, the year and reference of the act.
CREATE TABLE IF NOT EXISTS notes (
    norm    TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_norm ON notes(norm);
