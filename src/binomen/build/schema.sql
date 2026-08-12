-- binomen stage-2 index (full local backbone, built from NCBI taxdump)
--
-- What is deliberately NOT here: a materialized lineage cache. An earlier
-- version stored each taxon's root-first lineage as JSON so that code
-- detection could read it directly. Measured on the fixture it was 62% of the
-- index, and at real NCBI lineage depth it worked out to roughly 840 bytes per
-- taxon -- about two gigabytes -- to answer a question whose answer is one of
-- six values. It is now a single `code` column on `nodes`, assigned at build
-- time, and `get_lineage` walks parent pointers on demand.

-- Deliberately NOT WAL. These are read-mostly artifacts written once and
-- shipped; WAL buys concurrent writers we do not have, and costs a class of
-- failure where a stale -wal/-shm sidecar silently supplies an older schema
-- to a reader. A single self-contained file is also copyable.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    taxid         INTEGER PRIMARY KEY,
    parent_taxid  INTEGER NOT NULL,
    rank          TEXT NOT NULL,
    code          TEXT NOT NULL       -- governing code, precomputed. One byte of
                                      -- information, stored as one short string.
);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_taxid);

CREATE TABLE IF NOT EXISTS names (
    taxid       INTEGER NOT NULL,
    name        TEXT NOT NULL,
    unique_name TEXT,
    name_class  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_names_taxid ON names(taxid);

CREATE TABLE IF NOT EXISTS name_norm (
    norm       TEXT NOT NULL,
    taxid      INTEGER NOT NULL,
    name       TEXT NOT NULL,
    name_class TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_norm ON name_norm(norm);

-- merged.dmp: NCBI stating that two taxids were unified. The single
-- highest-value file in the archive for this project's purpose.
CREATE TABLE IF NOT EXISTS merged (
    old_taxid INTEGER PRIMARY KEY,
    new_taxid INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_merged_new ON merged(new_taxid);

CREATE TABLE IF NOT EXISTS deleted (
    taxid INTEGER PRIMARY KEY
);

-- From the curated overlay, not taxdump. Nothing in taxdump can express
-- "two committees disagree".
CREATE TABLE IF NOT EXISTS overlay_notes (
    name    TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_overlay_name ON overlay_notes(name);
