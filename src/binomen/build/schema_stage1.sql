-- binomen stage-1 index: the small, always-installed artifact.
--
-- Answers one question in ~2 ms and ~20 tokens: "is there anything about this
-- name that needs a closer look?" Most of the time the answer is no, and that
-- is the whole design -- a tool cheap enough to call on every organism mention
-- is a tool that actually gets called.
--
-- Two structures, chosen so the probabilistic one can only ever err in the
-- harmless direction:
--
--   verdicts  EXACT. Every name with a recorded synonymy, merge, homonym or
--             dispute. If a name has real nomenclatural history it is in here
--             and gets an exact answer. Never probabilistic.
--
--   bloom     One filter per governing code, over names with NO recorded
--             history. Bloom filters have no false negatives, so absence is
--             certain: a name in none of them is a name we have no record of.
--             A false positive means saying "no change recorded" about a
--             string that is not a name -- a missed 'unknown', never an
--             invented rename.

-- Deliberately NOT WAL. These are read-mostly artifacts written once and
-- shipped; WAL buys concurrent writers we do not have, and costs a class of
-- failure where a stale -wal/-shm sidecar silently supplies an older schema
-- to a reader. A single self-contained file is also copyable.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verdicts (
    norm     TEXT NOT NULL,
    verdict  TEXT NOT NULL,   -- superseded | has_synonyms | homonym | contested
    code     TEXT NOT NULL,
    taxid    INTEGER,         -- handoff to stage 2; null for overlay-only entries
    accepted TEXT             -- accepted name, only when it differs from the query
);
CREATE INDEX IF NOT EXISTS idx_verdicts_norm ON verdicts(norm);

CREATE TABLE IF NOT EXISTS bloom (
    code  TEXT PRIMARY KEY,
    n     INTEGER NOT NULL,
    blob  BLOB NOT NULL
);
