#!/usr/bin/env python3
"""How often does NCBI disagree with the nomenclatural authority for a code?

The question
------------
NCBI Taxonomy exists to hang sequence records on. It needs a label for
everything submitted, including names that were never validly published, so it
takes names early and rarely refuses one. That is the right call for a sequence
archive and it is not the same job as a nomenclatural register.

Where the two differ is information nobody currently publishes, and it is the
one thing a language model cannot produce -- not because the model is ignorant
but because "these two databases disagree" is not a fact about the world, it is
a fact about two artefacts.

Before building a second backbone into the index, measure whether the
disagreement is common enough to be worth shipping.

  under 1%   a curiosity
  5-10%      a product
  per code, because the rate is not uniform: a GBIF comparison found 4/18 for
  ICNafp and 0/16 for ICNP, and the ICNP zero was uninformative because GBIF's
  bacterial content derives from LPSN in the first place.

Usage
-----
    set BINOMEN_LPSN_USER=you@example.org
    set BINOMEN_LPSN_PASSWORD=...
    python scripts/compare_authority.py --code ICNP -n 100
    python scripts/compare_authority.py --code ICNP -n 100 --out lpsn-100.json

Names are drawn from the index with a fixed seed, so a rerun with the same -n
and --seed queries the same names and the comparison is reproducible.

Every response is cached by `binomen.authorities._http`, so a rerun costs no
requests. Be considerate: LPSN is a small academic service and its terms
restrict bulk use. Sample, do not sweep -- and do not point this at the whole
index.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def normalise(s: str | None) -> str:
    """Fold for comparison only -- never for display.

    Transliteration variants are the trap here. `Acantholimon korolkovii` and
    `korolkowii` are the same name spelled two ways, and a raw string diff
    reports them as a taxonomic disagreement. One of four "disagreements" in
    the GBIF run was exactly this. v/w, i/j and doubled vowels are the common
    cases; this handles the first two and flags the rest as `orthographic?`
    rather than pretending to be complete.
    """
    if not s:
        return ""
    s = " ".join(s.lower().split())
    return s.replace("w", "v").replace("j", "i")


# Latin adjectival endings that change with the gender of the genus. An epithet
# must agree with its genus, so a transfer can alter the ending without any
# taxonomic disagreement at all: Desulfofundulus salinus / salinum is one name,
# spelled for two genders, and a raw string diff calls it a dispute.
_ENDINGS = ("us", "um", "is", "ae", "ii", "i", "a", "e", "os", "on")


def same_stem(a: str, b: str) -> bool:
    """True when two binomials differ only in the epithet's gender ending."""
    pa, pb = normalise(a).split(), normalise(b).split()
    if len(pa) != 2 or len(pb) != 2 or pa[0] != pb[0]:
        return False
    if pa[1] == pb[1]:
        return True

    def stem(w: str) -> str:
        for e in sorted(_ENDINGS, key=len, reverse=True):
            if w.endswith(e) and len(w) - len(e) >= 4:
                return w[: -len(e)]
        return w

    return stem(pa[1]) == stem(pb[1])


def sample_names(code: str, n: int, seed: int, population: str) -> list[tuple[str, str]]:
    """Draw (query_name, ncbi_answer) pairs.

    The choice of population is the whole experiment, and getting it wrong
    produces a confident zero.

    `accepted` samples uniformly from names NCBI calls current. The first run
    of this script used it and returned 89/90 agreement -- because the vast
    majority of names have never moved, and two sources trivially agree about a
    name nobody has ever reclassified. The measurement was real and the
    denominator was meaningless.

    `superseded` samples names NCBI records as having been replaced, and asks
    the authority about the OLD name. That is where the sources can actually
    differ, and it is the population the product serves: someone typing a name
    they read in a paper. Three outcomes matter, and the third is the one worth
    shipping:

        authority agrees it moved, same target      -> agree
        authority agrees it moved, different target -> DISAGREE
        authority says it is still the correct name -> STILL_CURRENT
    """
    if population == "accepted":
        db = REPO / "data" / "binomen-field.sqlite"
        sql = "SELECT accepted, accepted FROM taxa WHERE rank='species' AND code=?"
    else:
        db = REPO / "data" / "binomen-stage1.sqlite"
        sql = ("SELECT norm, accepted FROM verdicts "
               "WHERE verdict='superseded' AND accepted IS NOT NULL AND code=?")
    if not db.exists():
        raise SystemExit(f"no index at {db}; run binomen-build-index or binomen-fetch-index")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    pool = []
    for query, answer in conn.execute(sql, (code,)):
        q, a = query.split(), (answer or "").split()
        if len(q) != 2 or len(a) != 2:
            continue
        if not all(x.isalpha() for x in q + a):
            continue
        # `norm` is folded to lower case; restore a display form to query with.
        pool.append((query[0].upper() + query[1:], answer))
    if len(pool) < n:
        raise SystemExit(f"only {len(pool)} usable {code} {population} names in the index")
    return random.Random(seed).sample(pool, n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="ICNP", choices=["ICNP", "ICNafp", "ICZN", "ICTV"])
    ap.add_argument("-n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=909090)
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="pause between requests; be polite to a small service")
    ap.add_argument("--population", default="superseded", choices=["superseded", "accepted"],
                    help="superseded (default) asks the authority about names NCBI says "
                         "have moved -- the only population where the sources can differ")
    ap.add_argument("--authority", help="pin one authority by name (e.g. lpsn) instead of "
                                        "taking the highest-tier one that answers")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    from binomen.authorities import authorities_for
    from binomen.codes import Code

    # `authorities_for` sorts tier ASCENDING, which is right for the runtime
    # cascade -- try the cheap source first, escalate only if it cannot answer.
    # It is exactly wrong for adjudication. The first run of this script broke
    # on the first authority that answered, so GBIF (tier 2) shadowed LPSN
    # (tier 3) on 93 of 100 names and the whole comparison silently measured
    # GBIF again. Highest tier first here: the code-specific register is the
    # point, and an aggregator is the fallback.
    auths = [x for x in authorities_for(Code(a.code)) if getattr(x, "tier", 0) >= 2]
    auths.sort(key=lambda x: -getattr(x, "tier", 0))
    if a.authority:
        auths = [x for x in auths if x.name == a.authority] or auths[:0]
        if not auths:
            raise SystemExit(f"no authority named {a.authority} claims {a.code}")
    auths = [x for x in auths if not hasattr(x, "configured") or x.configured] or auths
    if not auths:
        raise SystemExit(f"no authority registered for {a.code}")
    print(f"authorities for {a.code}: {[x.name for x in auths]}")
    for x in auths:
        if hasattr(x, "configured") and not x.configured:
            print(f"  !! {x.name} is NOT configured -- it will report 'not consulted'")

    names = sample_names(a.code, a.n, a.seed, a.population)
    print(f"{len(names)} {a.population} names, seed {a.seed}\n")

    rows, tally = [], Counter()
    for i, (query, ncbi) in enumerate(names, 1):
        best = None
        for auth in auths:
            try:
                r = auth.lookup(query, fuzzy=False)
            except Exception as e:                                  # noqa: BLE001
                r = None
                err = f"{type(e).__name__}: {e}"
            else:
                err = r.error
            if r is not None and r.found:
                best = (auth.name, r)
                break
        if best is None:
            # "not consulted" and "not found" are different facts. Keep them apart.
            verdict = "not_consulted" if (err and "not configured" in str(err)) else "absent"
            rows.append({"query": query, "ncbi": ncbi, "verdict": verdict, "detail": err})
        else:
            src, r = best
            theirs = r.accepted_name
            if not theirs:
                verdict = "no_accepted_name"
            # Agreement is tested FIRST. When NCBI's answer and the authority's
            # answer are the same name, they agree -- even if that name also
            # stem-matches the query, which happens whenever the "transfer" NCBI
            # recorded was only a gender correction (gwangjuense -> gwangjuensis).
            # Testing STILL_CURRENT first labelled four such agreements as
            # disagreements and pushed the headline from 21% to 28%.
            elif normalise(theirs) == normalise(ncbi) or same_stem(theirs, ncbi):
                verdict = "agree"
            elif a.population == "superseded" and same_stem(theirs, query):
                # The authority says the name NCBI retired is still correct.
                # This is the finding, not an error: LPSN returned exactly this
                # for Mycobacterium arupense, Pseudomonas guguanensis and
                # Streptomyces paucisporeus, in each case adding "explicitly
                # recommended for medical use".
                verdict = "STILL_CURRENT"
            elif normalise(theirs) == normalise(ncbi):
                verdict = "agree"
            elif same_stem(theirs, ncbi):
                verdict = "orthographic?"
            else:
                verdict = "DISAGREE"
            rows.append({"query": query, "ncbi": ncbi, "authority": src,
                         "theirs": theirs,
                         # .native, not the dataclass: the enum is binomen's
                         # vocabulary, the native string is the source's own
                         # words, and the latter is what a reader needs when
                         # adjudicating. Also, TaxonStatus is not JSON.
                         "status": getattr(r.status, "native", None),
                         "status_note": getattr(r.status, "note", None),
                         "citation": r.author_citation, "verdict": verdict})
        tally[rows[-1]["verdict"]] += 1
        print(f"  {i:>4}/{len(names)}  {rows[-1]['verdict']:<14} {ncbi}"
              f"{'  ->  ' + str(rows[-1].get('theirs')) if rows[-1].get('theirs') else ''}")
        if a.sleep:
            time.sleep(a.sleep)

    print("\n" + "=" * 60)
    for k, v in tally.most_common():
        print(f"  {k:<16}{v:>5}")
    comparable = (tally["agree"] + tally["DISAGREE"] + tally["orthographic?"]
                  + tally["STILL_CURRENT"])
    if tally["STILL_CURRENT"]:
        print(f"\n  STILL_CURRENT {tally['STILL_CURRENT']}/{comparable}: the authority says a "
              f"name NCBI retired is the correct one. Check whether any carry LPSN's "
              f"'recommended for medical use' note -- those are the shippable cases.")
    if comparable:
        rate = (tally["DISAGREE"] + tally["STILL_CURRENT"]) / comparable
        print(f"\n  disagreement, where both sources have the name: "
              f"{tally['DISAGREE'] + tally['STILL_CURRENT']}/{comparable} = {rate:.1%}")
        print("  (orthographic? excluded from the numerator; check them by eye "
              "before trusting the rate either way)")
    if tally["absent"]:
        print(f"\n  {tally['absent']} names the authority does not have at all. For recent "
              f"taxa this is coverage, not disagreement, and a second backbone would be "
              f"blank on them.")

    if a.out:
        a.out.write_text(json.dumps(
            {"code": a.code, "n": a.n, "seed": a.seed,
             "tally": dict(tally), "rows": rows}, indent=2), encoding="utf-8")
        print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
