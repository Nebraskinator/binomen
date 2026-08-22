"""Build `ambiguity.sqlite`: the NCBI-derived half of the shipped bundle.

Derived from stage 1 rather than from taxdump, on purpose. `build_index.py`
already decides what counts as a recorded nomenclatural history -- superseded,
has synonyms, homonym, contested -- and re-deriving that here would give the
project two places to disagree with itself about its own central judgement.
This module does something narrower: it decides what is worth *shipping*, and
packs it small enough to bundle.

What changes on the way through
-------------------------------
1. **Shapes.** Environmental and *Candidatus* placeholders, strain
   designations and unparseable strings are dropped -- roughly a third of stage
   1, and none of it is a name a person writes in prose.
2. **Codes and verdicts become integers.** Four verdict values and five codes
   were being stored as strings on every one of a million rows.
3. **Clusters.** Every name that is an alternative of another, under one code,
   carries the same cluster id, so one lookup can return the whole disagreement
   rather than a row the caller has to chase. NCBI's taxid is the cluster id
   where there is one, because that is exactly what a taxid means.
4. **`WITHOUT ROWID`, keyed on (norm, code).** The key *is* the lookup, so the
   separate index stage 1 needed disappears. Two codes claiming one spelling --
   *Bacillus* the bacterium and *Bacillus* the stick insect -- are two rows and
   never merge, which is how the homonym case survives the model rather than
   needing a rule.

Measured: 1,007,862 stage-1 rows and 107 MB in, 663,228 rows and 29 MB out.

The Bloom filters come across untouched. They are 1.9 MB and they are what makes
absence readable: a name in none of them is a name we have no record of, so a
misspelling reads as *unknown* rather than as an all-clear. See
`docs/adr/0001-ambiguity-only-local-database.md`.
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .budget import enforce_budget
from .build_index import strip_authority

REPO = Path(__file__).resolve().parents[3]
DEFAULT_IN = REPO / "data" / "binomen-stage1.sqlite"
DEFAULT_OUT = REPO / "data" / "ambiguity.sqlite"

# Stored as integers; the strings live once, in `vocab`.
VERDICTS = ["superseded", "has_synonyms", "homonym", "contested"]
CODES = ["ICNP", "ICNafp", "ICZN", "ICTV", "HGNC", "undetermined"]

# Substrings that mark a string as something other than a name someone writes.
PLACEHOLDER_MARKERS = (
    "unclassified", "uncultured", "environmental", " sp.", " bacterium",
    " symbiont", "endosymbiont", " group", " complex sp",
)
INFRASPECIFIC_MARKERS = ("subsp.", "ssp.", "var.", "pv.", "serovar", "biovar")

SCHEMA = """
CREATE TABLE IF NOT EXISTS amb (
    norm     TEXT NOT NULL,
    code     INTEGER NOT NULL,
    verdict  INTEGER NOT NULL,
    cluster  INTEGER NOT NULL,   -- taxid where NCBI has one; negative when synthesised
    accepted TEXT,               -- only when it differs from the name itself
    PRIMARY KEY (norm, code)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS bloom (code TEXT PRIMARY KEY, n INTEGER NOT NULL, blob BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS vocab (kind TEXT NOT NULL, id INTEGER NOT NULL, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# Every name of a cluster, packed into one row so the bundle can answer "what
# else is this called" offline.
#
# Measured against the alternatives, because the obvious ones are worse: a plain
# index on `amb(cluster)` costs 20.5 MB, since a WITHOUT ROWID key is copied into
# every index entry, and a rowid table with two indexes costs 30 MB. Packing the
# names once per cluster costs 14.6 MB and answers the question in a single
# lookup rather than a scan.
CLUSTER_TABLE = """
CREATE TABLE IF NOT EXISTS cluster (
    id    INTEGER PRIMARY KEY,   -- the taxid, matching amb.cluster
    names TEXT NOT NULL          -- US-separated; display forms, not normalised
);
"""
NAME_SEP = "\x1f"


def shape_of(name: str) -> str:
    """Classify a name so the filter's decisions stay inspectable.

    Kept deliberately close to `scripts/harvest_lpsn.py:shape_of`, which exists
    because a first measurement reported 92.8% of NCBI's superseded ICNP names
    as having no standing against 47% in a hand-checked sample. The whole gap
    was denominator: strain designations and placeholders that no register
    indexes and that are evidence about nothing.
    """
    t = name.split()
    if not t:
        return "other"
    if t[0] == "candidatus":
        return "candidatus"
    if any(w in name for w in PLACEHOLDER_MARKERS):
        return "placeholder"
    if any(m in t or m in name for m in INFRASPECIFIC_MARKERS):
        return "infraspecific"
    if len(t) == 1:
        return "genus" if t[0].isalpha() else "other"
    if len(t) == 2 and all(x.isalpha() for x in t):
        return "binomial"
    return "other"


KEEP_SHAPES = {"binomial", "genus", "infraspecific"}


def pack_clusters(d: sqlite3.Connection, index: Path, clusters: set[int]) -> int:
    """Write one row per cluster holding every name NCBI records for it.

    Display forms, taken from the full index rather than from stage 1: stage 1
    stores the normalised key and only carries a display name for superseded
    entries, so packing it would hand callers lowercased names for exactly the
    half of the cases they are most likely to quote.

    Synthetic (negative) cluster ids are skipped -- they exist only to keep
    overlay-only rows apart and have no taxon behind them to enumerate.
    """
    real = {c for c in clusters if c > 0}
    if not index.exists() or not real:
        return 0
    s = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    try:
        s.execute("CREATE TEMP TABLE want(taxid INTEGER PRIMARY KEY)")
        s.executemany("INSERT OR IGNORE INTO want VALUES (?)", ((c,) for c in real))
        packed, current, names = [], None, []
        # `names` carries the display forms and is indexed by taxid; ordering by
        # it lets this stream rather than hold every name in memory.
        #
        # Only classes that are names someone might write. `authority` is an
        # author citation and not a name at all -- there are 1,057,536 of them
        # and they are long, which alone pushed the compressed bundle from 21 MB
        # to 32 MB. `includes` is unidentified material filed under a taxon,
        # which expand_query already refuses for the same reason: putting it in
        # a search returns nothing.
        def flush(taxid, names):
            # Deduplicate while keeping order, because order is load-bearing:
            # the scientific name comes first so a reader can recover which of a
            # cluster's names is the accepted one without a second column. The
            # rest follow alphabetically.
            #
            # Authorities are stripped first. NCBI files the authority-bearing
            # form under `synonym`, not under `authority` -- taxid 821 carries
            # "Bacteroides vulgatus Eggerth and Gagnon 1933 (Approved Lists
            # 1980)" as a synonym and no bare form at all -- so filtering by name
            # class cannot remove them. Without this the bundle would hand a
            # caller citations where get_synonyms promises bare, searchable
            # names, and pasting one into PubMed returns nothing.
            seen, ordered = set(), []
            for raw in names:
                n = strip_authority(raw) or raw
                if n not in seen:
                    seen.add(n)
                    ordered.append(n)
            if len(ordered) > 1:
                packed.append((taxid, NAME_SEP.join(ordered)))

        for taxid, name in s.execute(
                "SELECT n.taxid, n.name FROM names n JOIN want w ON w.taxid = n.taxid "
                "WHERE n.name_class IN ('scientific name','synonym','equivalent name') "
                "ORDER BY n.taxid, "
                "CASE n.name_class WHEN 'scientific name' THEN 0 ELSE 1 END, n.name"):
            if taxid != current:
                if current is not None:
                    flush(current, names)
                current, names = taxid, []
            names.append(name)
        if current is not None:
            flush(current, names)
        d.executescript(CLUSTER_TABLE)
        d.executemany("INSERT OR REPLACE INTO cluster VALUES (?,?)", packed)
        return len(packed)
    finally:
        s.close()


def build(src: Path, out: Path, *, names_from: Path | None = None) -> dict:
    if not src.exists():
        raise SystemExit(f"no stage-1 index at {src}; run binomen-build-index first")
    if out.exists():
        out.unlink()

    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(out)
    d.execute("PRAGMA page_size=4096")
    d.executescript(SCHEMA)

    vi = {v: i for i, v in enumerate(VERDICTS)}
    ci = {c: i for i, c in enumerate(CODES)}
    d.executemany("INSERT INTO vocab(kind,id,value) VALUES ('verdict',?,?)", list(enumerate(VERDICTS)))
    d.executemany("INSERT INTO vocab(kind,id,value) VALUES ('code',?,?)", list(enumerate(CODES)))

    stats = dict.fromkeys(("in", "kept", "noop", "shape_dropped", "synthetic_cluster"), 0)
    synthetic = 0
    batch = []
    clusters: set[int] = set()
    for norm, verdict, code, taxid, accepted in s.execute(
            "SELECT norm, verdict, code, taxid, accepted FROM verdicts"):
        stats["in"] += 1
        if shape_of(norm) not in KEEP_SHAPES:
            stats["shape_dropped"] += 1
            continue
        # NCBI's table carries rows whose replacement is the same name in a
        # different case or spelling. Shipping those would report a rename that
        # did not happen -- 1,772 of them at last count.
        if accepted and accepted.strip().lower() == norm.strip().lower():
            accepted = None
            if verdict == "superseded":
                stats["noop"] += 1
                continue
        if taxid is None:
            synthetic += 1
            cluster = -synthetic
            stats["synthetic_cluster"] += 1
        else:
            cluster = taxid
        batch.append((norm, ci.get(code, len(CODES) - 1), vi.get(verdict, 0), cluster, accepted))
        clusters.add(cluster)
        stats["kept"] += 1
        if len(batch) >= 50_000:
            d.executemany("INSERT OR IGNORE INTO amb VALUES (?,?,?,?,?)", batch)
            batch = []
    d.executemany("INSERT OR IGNORE INTO amb VALUES (?,?,?,?,?)", batch)

    # Absence has to stay readable: without these, "not in the database" cannot
    # be told apart from "not a name", and a typo would read as an all-clear.
    blooms = list(s.execute("SELECT code, n, blob FROM bloom"))
    d.executemany("INSERT OR REPLACE INTO bloom VALUES (?,?,?)", blooms)
    stats["blooms"] = len(blooms)

    src_meta = dict(s.execute("SELECT key, value FROM meta"))
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for k, v in {
        "source": "NCBI Taxonomy",
        "licence": "public domain (work of the US Government)",
        "built_at": stamp,
        "rows": str(stats["kept"]),
        "from_stage1": src.name,
        **{f"ncbi.{k}": v for k, v in src_meta.items()},
    }.items():
        d.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))

    stats["clusters_packed"] = (
        pack_clusters(d, names_from, clusters) if names_from else 0)
    if not stats["clusters_packed"]:
        # Enumeration is a promise the bundle makes; if it could not be built,
        # say so here rather than letting get_synonyms quietly return less.
        d.execute("INSERT OR REPLACE INTO meta VALUES ('enumeration','absent')")
    d.commit()
    d.execute("VACUUM")
    d.close()
    s.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, default=DEFAULT_IN)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--names-from", type=Path, default=REPO / "data" / "binomen.sqlite",
                    metavar="INDEX",
                    help="full index supplying display names for offline enumeration; "
                         "without it the bundle cannot list a taxon's other names")
    ap.add_argument("--no-enumeration", action="store_true",
                    help="skip the packed cluster table (saves ~15 MB, and get_synonyms "
                         "then needs the fetched stage-2 index)")
    ap.add_argument("--max-mb", type=float, default=80.0,
                    help="fail if the result exceeds this on disk")
    a = ap.parse_args()

    t0 = time.time()
    st = build(a.src, a.out, names_from=None if a.no_enumeration else a.names_from)
    size = a.out.stat().st_size / 1e6
    print(f"stage-1 rows      {st['in']:,}")
    print(f"  shapes dropped  {st['shape_dropped']:,}")
    print(f"  case no-ops     {st['noop']:,}")
    print(f"  kept            {st['kept']:,}")
    print(f"  clusters synth  {st['synthetic_cluster']:,}")
    print(f"  blooms carried  {st['blooms']}")
    print(f"  clusters packed {st['clusters_packed']:,}"
          f"{'' if st['clusters_packed'] else '   (no offline enumeration)'}")
    print(f"\n{a.out}  {size:.1f} MB  ({time.time() - t0:.1f}s)")
    enforce_budget(a.out, a.max_mb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
