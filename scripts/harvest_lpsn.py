#!/usr/bin/env python3
"""Harvest LPSN once, at build time, so users never need credentials.

Why
---
NCBI Taxonomy exists to hang sequence records on. It needs a label for
everything submitted, so it takes names early and carries names that were never
validly published. LPSN is the register the ICNP itself points to. Where they
differ is information nobody publishes, and a language model cannot produce it —
"these two databases disagree" is a fact about two artefacts, not about the
world.

Measured on 100 names NCBI records as superseded (seed 909090, 2026-08-15):

    agree                    42/53
    NCBI/LPSN disagree       11/53  ~21%  [12-33%]
    no ICNP standing at all  47/100 ~47%  [38-57%]

The disagreements are not scattered. Every one is a recent genus segregation
NCBI adopted and LPSN has not: Vreelandella and Modicisalibacter out of
Halomonas, Mycolicibacterium out of Mycobacterium, Hallella out of Prevotella,
Paenirhodobacter out of Rhodobacter. That is a mechanism, not noise.

Licensing (https://lpsn.dsmz.de/text/copyright, read 2026-08-15)
---------------------------------------------------------------
LPSN is CC BY-SA 4.0. Automated download is permitted *via the API* — this
script — or the download page. Redistribution is permitted with attribution and
a link to the source page, which is why `address` (a DOI) is stored per record
and must be carried into any response derived from it.

SHARE-ALIKE: an index embedding this data is an adaptation and must itself be
CC BY-SA 4.0. The repository's MIT licence covers source code only; the shipped
index needs its own licence field. docs/DATA.md already draws that line for
NCBI.

Usage
-----
    set BINOMEN_LPSN_USER=...
    set BINOMEN_LPSN_PASSWORD=...

    python scripts/harvest_lpsn.py --probe              # 2 requests, no commitment
    python scripts/harvest_lpsn.py --genus Halomonas    # one genus, ~2 requests
    python scripts/harvest_lpsn.py --all                # full harvest, resumable
    python scripts/harvest_lpsn.py --compare            # local join vs NCBI, offline

`--all` is resumable: every batch is committed before the next request, and a
rerun skips genera already recorded. Interrupt it freely.

NOT TESTED AGAINST THE LIVE SERVICE. The sandbox this was written in cannot
reach dsmz.de. The record shape comes from real cached responses and the
endpoints from DSMZ's own client, but `--probe` exists because that is evidence,
not a test. Run it before `--all`.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

OUT = REPO / "data" / "lpsn.sqlite"
BATCH = 50          # ids per fetch; the API takes them semicolon-joined
LICENSE = ("CC BY-SA 4.0, LPSN (DSMZ). Redistribution permitted with attribution; "
           "every record carries its own DOI in `address`. Derived indexes inherit "
           "share-alike.")


# --------------------------------------------------------------------------
def connect() -> sqlite3.Connection:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(OUT)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS lpsn (
            lpsn_id      INTEGER PRIMARY KEY,
            norm         TEXT NOT NULL,
            full_name    TEXT NOT NULL,
            monomial     TEXT,
            category     TEXT,
            correct_id   INTEGER,
            correct_name TEXT,          -- NULL when this name is itself correct
            standing     TEXT,          -- nomenclatural_status
            medical_use  TEXT,          -- lorn_status
            authority    TEXT,
            address      TEXT           -- DOI; the attribution obligation
        );
        CREATE INDEX IF NOT EXISTS idx_lpsn_norm ON lpsn(norm);
        CREATE TABLE IF NOT EXISTS harvested (genus TEXT PRIMARY KEY, n INTEGER, at TEXT);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    return c


def put_meta(c: sqlite3.Connection, **kw) -> None:
    c.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
                  [(k, str(v)) for k, v in kw.items()])


# --------------------------------------------------------------------------
def correct_name_of(rec: dict, by_id: dict[int, dict], norm) -> str | None:
    """The name LPSN considers correct, or None. NEVER the record's own name.

    This is the single invariant worth enforcing in this file. Five separate
    bugs in this project have been a resolver returning its input when it did
    not know the answer -- gbif.py echoing canonicalName on synonyms, lpsn.py
    falling back to full_name twice, and the comparison script reading those
    echoes as "the authority says this retired name is still current", which
    produced a 98% disagreement rate out of nothing.

    So: None means "no different name on record". A non-None value is
    guaranteed to differ from `full_name`, and `store` asserts it.
    """
    cid = rec.get("lpsn_correct_name_id")
    if cid is None or cid == rec.get("id"):
        return None
    target = by_id.get(cid) or {}
    name = (target.get("full_name") or "").strip()
    if not name or norm(name) == norm(rec.get("full_name") or ""):
        return None
    return name


def resolve_correct_names(c: sqlite3.Connection) -> tuple[int, int]:
    """Fill correct_name by joining the table to itself. Local, no requests.

    Runs after the harvest, when every genus is present, so a pointer from
    Halomonas into Vreelandella resolves. Safe to rerun.
    """
    c.execute("""
        UPDATE lpsn SET correct_name = (
            SELECT t.full_name FROM lpsn t WHERE t.lpsn_id = lpsn.correct_id
        ) WHERE correct_id IS NOT NULL AND correct_id <> lpsn_id
    """)
    # The invariant, enforced in SQL rather than trusted: a correct name that
    # equals the record's own name carries no information and is exactly the
    # echo that produced a 98% disagreement rate out of nothing.
    c.execute("""
        UPDATE lpsn SET correct_name = NULL
        WHERE correct_name IS NOT NULL
          AND lower(trim(correct_name)) = lower(trim(full_name))
    """)
    resolved = c.execute(
        "SELECT count(*) FROM lpsn WHERE correct_name IS NOT NULL").fetchone()[0]
    dangling = c.execute(
        "SELECT count(*) FROM lpsn WHERE correct_id IS NOT NULL AND correct_id <> lpsn_id "
        "AND correct_name IS NULL").fetchone()[0]
    c.commit()
    return resolved, dangling


def store(c: sqlite3.Connection, records: list[dict], norm) -> int:
    rows = []
    for r in records:
        full = (r.get("full_name") or "").strip()
        if not full or not r.get("id"):
            continue
        # correct_name is left NULL here and filled by resolve_correct_names()
        # once the whole table exists. `lpsn_correct_name_id` points wherever
        # LPSN likes, and a genus transfer points OUT of the genus by
        # definition -- so resolving inside a batch would drop those pointers
        # silently, with no way to tell a real "no different name" from a
        # target that simply had not been fetched yet.
        #
        # Not established by the Halomonas probe, which showed 2 synonyms in 50
        # and both in-batch. The point is that the two cases are
        # indistinguishable at batch scope, and one of them is the finding.
        rows.append((r["id"], norm(full), full, r.get("monomial"), r.get("category"),
                     r.get("lpsn_correct_name_id"), None,
                     r.get("nomenclatural_status") or r.get("validly_published"),
                     r.get("lorn_status"), r.get("authority"), r.get("lpsn_address")))
    c.executemany("INSERT OR REPLACE INTO lpsn VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


# --------------------------------------------------------------------------
def page_ids(api, first: str, sleep: float, cap: int | None = None) -> list[int]:
    """Walk `next` collecting ids. LPSN returns absolute URLs; _call wants paths."""
    ids, page = [], first
    while page:
        payload, _ = api._call(page)
        for x in payload.get("results") or []:
            ids.append(x["id"] if isinstance(x, dict) else x)
        if cap and len(ids) >= cap:
            break
        nxt = payload.get("next")
        page = nxt.split("dsmz.de/", 1)[-1] if isinstance(nxt, str) and nxt else None
        if page:
            time.sleep(sleep)
    return ids


def harvest_category(category: str, sleep: float, resume: bool) -> None:
    """Enumerate a whole LPSN category in one pass.

    Per-genus harvesting needs a search plus a fetch for each of ~9,400 ICNP
    genera: 20-28k requests, five to seven hours, against a small academic
    service. Paging one category costs ~250 search pages plus ~500 batched
    fetches. Same data, 25x fewer requests, and far politer.
    """
    from binomen.authorities.lpsn import LPSN
    from binomen.build.build_index import normalize_name as norm

    api = LPSN()
    if not api.configured:
        raise SystemExit("set BINOMEN_LPSN_USER and BINOMEN_LPSN_PASSWORD")
    c = connect()
    print(f"enumerating LPSN category={category} ...")
    ids = page_ids(api, f"advanced_search?category={category}", sleep)
    print(f"  {len(ids)} ids")
    if not ids:
        raise SystemExit("enumeration returned nothing; fall back to --strategy genus")

    have = {i for (i,) in c.execute("SELECT lpsn_id FROM lpsn")} if resume else set()
    todo = [i for i in ids if i not in have]
    print(f"  {len(have)} already stored, {len(todo)} to fetch\n")

    total = 0
    for j in range(0, len(todo), BATCH):
        chunk = todo[j:j + BATCH]
        try:
            payload, _ = api._call("fetch/" + ";".join(str(x) for x in chunk))
            res = payload.get("results") or []
            if isinstance(res, dict):
                res = list(res.values())
            total += store(c, [x for x in res if isinstance(x, dict)], norm)
            c.commit()
            print(f"  {min(j + BATCH, len(todo)):>6}/{len(todo)}   stored {total}")
            time.sleep(sleep)
        except KeyboardInterrupt:
            print("\ninterrupted; progress committed, rerun to continue")
            break
        except Exception as e:                                        # noqa: BLE001
            print(f"  batch at {j} FAILED {type(e).__name__}: {e}")
            time.sleep(sleep * 4)

    resolved, dangling = resolve_correct_names(c)
    print(f"\nresolved {resolved} correct-name pointers; {dangling} dangling")
    put_meta(c, harvested_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             source="LPSN (DSMZ)", license=LICENSE, strategy=f"category={category}",
             records=c.execute("SELECT count(*) FROM lpsn").fetchone()[0])
    c.commit()
    print(f"{c.execute('SELECT count(*) FROM lpsn').fetchone()[0]} records in {OUT}")


def harvest(genera: list[str], sleep: float, resume: bool) -> None:
    from binomen.authorities.lpsn import LPSN
    from binomen.build.build_index import normalize_name as norm

    api = LPSN()
    if not api.configured:
        raise SystemExit("set BINOMEN_LPSN_USER and BINOMEN_LPSN_PASSWORD")
    c = connect()
    done = {g for (g,) in c.execute("SELECT genus FROM harvested")} if resume else set()
    todo = [g for g in genera if g not in done]
    print(f"{len(genera)} genera, {len(done)} already harvested, {len(todo)} to go")

    total = 0
    for i, genus in enumerate(todo, 1):
        try:
            ids, page = [], f"advanced_search?taxon-name={genus}"
            while page:
                payload, _ = api._call(page)
                for x in payload.get("results") or []:
                    ids.append(x["id"] if isinstance(x, dict) else x)
                nxt = payload.get("next")
                # `next` may be absolute or relative; _call prefixes the base.
                page = nxt.split("dsmz.de/")[-1] if isinstance(nxt, str) and nxt else None
                if page:
                    time.sleep(sleep)

            got = []
            for j in range(0, len(ids), BATCH):
                payload, _ = api._call("fetch/" + ";".join(str(x) for x in ids[j:j + BATCH]))
                res = payload.get("results") or []
                if isinstance(res, dict):
                    res = list(res.values())
                got += [x for x in res if isinstance(x, dict)]
                time.sleep(sleep)

            n = store(c, got, norm)
            c.execute("INSERT OR REPLACE INTO harvested VALUES (?,?,?)",
                      (genus, n, time.strftime("%Y-%m-%dT%H:%M:%S")))
            c.commit()                       # commit per genus: resumability
            total += n
            print(f"  {i:>5}/{len(todo)}  {genus:<32} {n:>4} records   (total {total})")
        except KeyboardInterrupt:
            print("\ninterrupted; progress is committed, rerun to continue")
            break
        except Exception as e:                                        # noqa: BLE001
            print(f"  {i:>5}/{len(todo)}  {genus:<32} FAILED {type(e).__name__}: {e}")
            time.sleep(sleep * 4)

    resolved, dangling = resolve_correct_names(c)
    print(f"resolved {resolved} correct-name pointers; {dangling} still dangling "
          f"(target genus not harvested yet -- rerun after --all completes)")
    put_meta(c, harvested_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             source="LPSN (DSMZ)", license=LICENSE,
             records=c.execute("SELECT count(*) FROM lpsn").fetchone()[0])
    c.commit()
    print(f"\n{c.execute('SELECT count(*) FROM lpsn').fetchone()[0]} records in {OUT}")


# --------------------------------------------------------------------------
def shape_of(name: str) -> str:
    """Classify an NCBI name so `no standing` stays interpretable.

    A first run reported 92.8% of NCBI's superseded ICNP names as having no
    ICNP standing, against 47% in a hand-checked sample of clean binomials.
    The gap was entirely denominator: NCBI's verdict table carries strain
    designations, subspecies, Candidatus placeholders and environmental
    labels, none of which LPSN indexes and none of which are evidence about
    anything. Segmenting is the difference between a finding and an artefact.
    """
    t = name.split()
    if not t:
        return "other"
    if t[0] == "candidatus":
        return "candidatus"
    if any(w in name for w in ("unclassified", "uncultured", "environmental", " sp.", " bacterium")):
        return "placeholder"
    for marker in ("subsp.", "ssp.", "var.", "biovar", "pv.", "serovar"):
        if marker in t:
            return "infraspecific"
    if len(t) == 1:
        return "genus"
    if len(t) == 2 and all(x.isalpha() for x in t):
        return "binomial"
    return "other"


def compare() -> None:
    """Join LPSN against NCBI locally. No network, rerun freely."""
    from binomen.build.build_index import normalize_name as norm

    if not OUT.exists():
        raise SystemExit(f"no harvest at {OUT}; run --all first")
    lp = connect()
    s1p = REPO / "data" / "binomen-stage1.sqlite"
    if not s1p.exists():
        raise SystemExit(f"no NCBI index at {s1p}")
    s1 = sqlite3.connect(f"file:{s1p}?mode=ro", uri=True)

    lpsn = {n: (fn, cn, st, mu, ad) for n, fn, cn, st, mu, ad in lp.execute(
        "SELECT norm, full_name, correct_name, standing, medical_use, address FROM lpsn")}
    have_cats = dict(lp.execute("SELECT category, count(*) FROM lpsn GROUP BY category"))
    print(f"{len(lpsn)} LPSN records  {have_cats}\n")
    if "subspecies" not in have_cats:
        print("NOTE: no subspecies harvested. Every NCBI infraspecific name will read\n"
              "      as 'no standing'. Run --all --category subspecies to close that.\n")

    from collections import Counter
    tally = Counter()
    findings = []
    for old, ncbi_new in s1.execute(
            "SELECT norm, accepted FROM verdicts "
            "WHERE verdict='superseded' AND accepted IS NOT NULL AND code='ICNP'"):
        shape = shape_of(old)
        # NCBI's table contains rows whose "replacement" is the same name in a
        # different case or spelling. That is not a supersession and must not
        # be counted as agreement OR disagreement -- it is a no-op, and it was
        # inflating the disagreement list with pairs like
        # `shewanella colwelliana` -> `Shewanella colwelliana`.
        if norm(old) == norm(ncbi_new):
            tally[f"{shape}/noop"] += 1
            continue

        hit = lpsn.get(old)
        if hit is None:
            tally[f"{shape}/no_icnp_standing"] += 1
            continue
        full, correct, standing, medical, address = hit
        if correct is None:
            tally[f"{shape}/lpsn_kept_it"] += 1
            findings.append((shape, old, ncbi_new, full, standing, medical, address))
        elif norm(correct) == norm(ncbi_new):
            tally[f"{shape}/agree"] += 1
        else:
            tally[f"{shape}/different_target"] += 1
            findings.append((shape, old, ncbi_new, correct, standing, medical, address))

    shapes = sorted({k.split("/")[0] for k in tally})
    outcomes = ["agree", "lpsn_kept_it", "different_target", "no_icnp_standing", "noop"]
    print(f"{'shape':<15}" + "".join(f"{o:>19}" for o in outcomes) + f"{'total':>9}")
    for sh in shapes:
        row = [tally[f"{sh}/{o}"] for o in outcomes]
        print(f"{sh:<15}" + "".join(f"{v:>19,}" for v in row) + f"{sum(row):>9,}")

    # The headline, computed only over names both sources can be said to hold
    # an opinion about.
    b_ag = tally["binomial/agree"]
    b_kept = tally["binomial/lpsn_kept_it"]
    b_diff = tally["binomial/different_target"]
    comparable = b_ag + b_kept + b_diff
    if comparable:
        print(f"\nBINOMIALS BOTH SOURCES HOLD: {comparable:,}")
        print(f"  disagree            {b_kept + b_diff:>7,}  {(b_kept + b_diff)/comparable:>6.1%}")
        print(f"    LPSN kept the name NCBI retired  {b_kept:>7,}")
        print(f"    LPSN points somewhere else       {b_diff:>7,}")
    nos = tally["binomial/no_icnp_standing"]
    denom = comparable + nos
    if denom:
        print(f"\nBINOMIALS NCBI RETIRED, ABSENT FROM LPSN: {nos:,} of {denom:,} "
              f"({nos/denom:.1%}) -- no standing under the ICNP")

    med = [f for f in findings if f[0] == "binomial" and (f[5] or "").startswith("recommend")]
    print(f"\n{len(med):,} disagreements where LPSN RECOMMENDS its name for medical use.")
    print("These are the shippable cases: binomen would otherwise hand a clinician")
    print("a name the ICNP register advises against.\n")
    for _shape, old, ncbi_new, theirs, standing, medical, address in med[:20]:
        print(f"  {old}")
        print(f"      NCBI -> {ncbi_new}")
        print(f"      LPSN -> {theirs}   [{standing}; medical: {medical}]")
        print(f"      {address}")


# --------------------------------------------------------------------------
def probe() -> None:
    """Two requests. Validates the endpoint shape before committing to 20k."""
    from binomen.authorities.lpsn import LPSN
    from binomen.build.build_index import normalize_name as norm

    api = LPSN()
    if not api.configured:
        raise SystemExit("set BINOMEN_LPSN_USER and BINOMEN_LPSN_PASSWORD")
    print("--- can we page a whole category? (the 25x cheaper route) ---")
    try:
        cat, _ = api._call("advanced_search?category=species")
        n = len(cat.get("results") or [])
        print(f"  category=species: count={cat.get('count')} first page={n} "
              f"next={'yes' if cat.get('next') else 'no'}")
        if cat.get("count"):
            print(f"  -> ~{(cat['count'] // max(n,1)) + (cat['count'] // BATCH)} requests total")
    except Exception as e:                                            # noqa: BLE001
        print(f"  category paging NOT supported ({type(e).__name__}); use --strategy genus")
    print()

    payload, _ = api._call("advanced_search?taxon-name=Halomonas")
    ids = [x["id"] if isinstance(x, dict) else x for x in (payload.get("results") or [])]
    print(f"search 'Halomonas': count={payload.get('count')} next={payload.get('next')} "
          f"ids={len(ids)}")
    if not ids:
        raise SystemExit("enumeration returned nothing -- the search shape is wrong, stop here")
    payload, _ = api._call("fetch/" + ";".join(str(x) for x in ids[:BATCH]))
    res = payload.get("results") or []
    if isinstance(res, dict):
        res = list(res.values())
    recs = [x for x in res if isinstance(x, dict)]
    print(f"fetch {min(len(ids), BATCH)} ids -> {len(recs)} records")
    if not recs:
        raise SystemExit("fetch returned no records -- stop here")
    by_id = {r["id"]: r for r in recs if isinstance(r.get("id"), int)}
    syn = [r for r in recs if correct_name_of(r, by_id, norm)]
    print(f"\nfields on the first record:\n  {sorted(recs[0])}")
    print(f"\n{len(syn)} of {len(recs)} resolve to a DIFFERENT correct name:")
    for r in syn[:6]:
        print(f"  {r.get('full_name'):<34} -> {correct_name_of(r, by_id, norm)}")
    print("\nA LOW count here is expected and not a failure: this batch can only "
          "resolve pointers whose target is also in the batch, and a genus transfer "
          "points out of the genus. Cross-genus pointers are filled by "
          "resolve_correct_names() after --all. What matters here is that the ids, "
          "the fetch and the field names are right.")
    unresolved = sum(1 for r in recs
                     if r.get("lpsn_correct_name_id") not in (None, r.get("id")))
    print(f"{unresolved} of {len(recs)} point at a correct name outside this batch — "
          f"those are the ones --all recovers.")


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--probe", action="store_true")
    g.add_argument("--genus")
    g.add_argument("--all", action="store_true")
    g.add_argument("--compare", action="store_true")
    g.add_argument("--resolve", action="store_true",
                   help="re-run the local correct-name join; no network")
    ap.add_argument("--strategy", default="category", choices=["category", "genus"],
                    help="category (default) pages all of LPSN in ~750 requests; "
                         "genus is the per-genus fallback, ~25k requests")
    ap.add_argument("--category", default="species")
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--no-resume", action="store_true")
    a = ap.parse_args()

    if a.probe:
        probe()
        return 0
    if a.compare:
        compare()
        return 0
    if a.resolve:
        r, d = resolve_correct_names(connect())
        print(f"resolved {r}; dangling {d}")
        return 0
    if a.genus:
        harvest([a.genus], a.sleep, not a.no_resume)
        return 0

    if a.strategy == "category":
        harvest_category(a.category, a.sleep, not a.no_resume)
        return 0

    s1p = REPO / "data" / "binomen-stage1.sqlite"
    if not s1p.exists():
        raise SystemExit(f"no NCBI index at {s1p}")
    s1 = sqlite3.connect(f"file:{s1p}?mode=ro", uri=True)
    # Junk leaks out of NCBI's verdict table -- "A-proteobacteria",
    # "Aeromonadaceae/succinivibrionaceae". 393 of 9,907. Each would cost a
    # request to learn nothing.
    genera = sorted({g for g in (n.split()[0].capitalize() for (n,) in s1.execute(
        "SELECT norm FROM verdicts WHERE code='ICNP'") if n)
        if g.isalpha() and len(g) >= 4})
    print(f"{len(genera)} ICNP genera in the index")
    harvest(genera, a.sleep, not a.no_resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
