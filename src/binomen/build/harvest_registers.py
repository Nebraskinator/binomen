"""Harvest nomenclatural registers into the shipped register database.

One harvester, several registers. A register is added by naming its
ChecklistBank dataset and the code it speaks for -- see `REGISTERS` below --
not by writing another client. That is the whole point of routing through
ChecklistBank: LPSN, Species Fungorum Plus and the ICTV Master Species List
publish incompatible interfaces, and ChecklistBank republishes all three in one
shape, versioned, under CC BY.

Two routes, because ChecklistBank does not build the same artefacts for every
dataset:

  archive  A prebuilt Darwin Core Archive: one download, one flat TSV. Species
           Fungorum Plus has one (7 MB for 491,586 names).
  api      Paged `nameusage/search`. LPSN and ICTV have no prebuilt archive, and
           the API carries something the archive drops -- a per-record `link`,
           which for LPSN is the DOI its CC BY-SA terms require us to ship.

`route="auto"` tries the archive and falls back. Neither route needs
credentials.

What gets kept
--------------
The shipped database holds ambiguity, not taxonomy. A register row survives only
if it participates in an ambiguity: it is a synonym of something, something is a
synonym of it, or it lacks standing under its own code. Species Fungorum Plus
illustrates the ratio -- 491,586 names in, of which 170,408 are accepted with
nothing disagreeing and 162,756 are bare names that were never validly
published. Neither group tells a reader anything, and shipping them would spend
the entire size budget on silence.

Ranks above genus are dropped for the same reason: nobody writes a family name
into a manuscript and wonders whether it was renamed.

LPSN's medical-use recommendations
----------------------------------
ChecklistBank's LPSN mirror does not carry `lorn_status`, the field that says a
name is recommended for medical use -- and `docs/FINDINGS.md` §6 calls those rows
the product. They are added by `overlay_lpsn_medical_use`, a local join against a
credentialled harvest (`data/lpsn.sqlite`, produced by `scripts/harvest_lpsn.py`).
The overlay itself needs no network and no credentials; only refreshing that file
does, and `BINOMEN_LPSN_USER` / `BINOMEN_LPSN_PASSWORD` stay on the maintainer's
machine, never in CI and never shipped -- see
`docs/adr/0003-node-is-the-product-python-is-the-builder.md`. Without the file the
harvest still succeeds and simply carries no medical-use flags.

Note that `lorn_status` describes a *name*, not a pair: the superseded name reads
"not recommended" and the name the register prefers reads "recommended". Both
travel with the row, because either one alone can be read to mean the opposite of
what LPSN says.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .budget import enforce_budget, enforce_bundle_budget, zipped_mb  # noqa: F401
from .build_index import normalize_name

CLB = "https://api.checklistbank.org"
REPO = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO / "data" / "registers.sqlite"

# Ranks a reader might actually write. Everything above genus is dropped.
KEEP_RANKS = {
    "genus", "subgenus", "species", "species aggregate", "subspecies",
    "variety", "subvariety", "form", "subform", "forma specialis",
}

# ChecklistBank name-usage statuses. `bare name` is a name that was never
# validly published; under every code that is an absence of standing, not a
# weaker kind of acceptance, and it is the largest single category in the
# fungal register.
ACCEPTED = {"accepted", "provisionally accepted"}
SYNONYM = {"synonym", "ambiguous synonym", "misapplied"}
NO_STANDING = {"bare name"}

# Extras dropped before writing. Author citations are the whole change history
# under the ICNafp -- Candidozyma auris (Satoh & Makimura) Liu et al. names its
# own basionym -- and they also cost ~7 MB on the fungal register, which is most
# of a budget meant to hold three registers. `--keep-authorship` buys them back.
DROP_EXTRAS: set[str] = set()


@dataclass(frozen=True)
class RegisterSpec:
    """Everything that distinguishes one register from another."""

    key: str                 # our short name, and the `source` column value
    code: str                # the nomenclatural code it speaks for
    dataset: int             # ChecklistBank dataset key
    source: str              # human-readable attribution string
    licence: str
    route: str = "auto"      # auto | archive | api
    doi: str | None = None


REGISTERS: dict[str, RegisterSpec] = {
    "lpsn": RegisterSpec(
        key="lpsn", code="ICNP", dataset=2015,
        source="LPSN (DSMZ), via ChecklistBank",
        licence="CC BY-SA 4.0", route="api"),
    # Kew's Index Fungorum crawl, two years fresher than Species Fungorum Plus
    # (2073, Apr 2024) and carrying renames 2073 predates -- Candida auris ->
    # Candidozyma auris among them. Catalogue of Life 2026 was checked as an
    # alternative and is no fresher.
    #
    # ChecklistBank leaves 1028's `license` field empty, which held this back for
    # a round. Kew's own terms resolve it: general website content is
    # all-rights-reserved and bars commercial use, but "pages containing science
    # data and digital resources" are carved out and published under CC BY, with
    # attribution required. Nomenclatural data is science data. Two further
    # supports: Kew licenses the same nomenclator as 2073 under `cc by` here, and
    # GBIF hosts a Kew Index Fungorum crawl under CC BY 4.0. See docs/DATA.md.
    "indexfungorum": RegisterSpec(
        key="indexfungorum", code="ICNafp", dataset=1028,
        source="Index Fungorum (Royal Botanic Gardens, Kew), via ChecklistBank",
        licence="CC BY 4.0 (Kew science data terms)", doi="10.48580/d38h",
        route="auto"),
    "ictv": RegisterSpec(
        key="ictv", code="ICTV", dataset=1014,
        source="ICTV Master Species List, via ChecklistBank",
        licence="CC BY 4.0", route="api"),
}

# Known-good alternatives, not harvested by default. Two registers for one code
# would put two opinions in the file under different `source` values, which is a
# question nobody asked; the fresher one wins and this stays as the fallback.
ALTERNATES: dict[str, RegisterSpec] = {
    # The Apr 2024 release of the same nomenclator, declared `cc by` on
    # ChecklistBank. Superseded here by dataset 1028, which is two years fresher.
    # Harvest it explicitly (`binomen-harvest-registers sfp`) if the crawl is
    # ever unavailable.
    "sfp": RegisterSpec(
        key="sfp", code="ICNafp", dataset=2073,
        source="Species Fungorum Plus (Royal Botanic Gardens, Kew), via ChecklistBank",
        licence="CC BY 4.0", doi="10.15468/ts7wsb", route="auto"),
}

ALL_SPECS: dict[str, RegisterSpec] = {**REGISTERS, **ALTERNATES}

SCHEMA = """
-- One row per (name, code, register). Keyed by the normalised name so a lookup
-- joins straight onto the ambiguity index in the sibling database; the two ship
-- as separate files to keep NCBI's public-domain claim and LPSN's share-alike
-- claim from contaminating one another. See
-- docs/adr/0002-two-files-for-licence-containment.md.
CREATE TABLE IF NOT EXISTS register (
    norm          TEXT NOT NULL,
    name          TEXT NOT NULL,
    code          TEXT NOT NULL,
    source        TEXT NOT NULL,   -- REGISTERS key: lpsn | sfp | ictv
    rank          TEXT,
    status        TEXT NOT NULL,   -- accepted | synonym | no_standing
    native_status TEXT,            -- the register's own term, never flattened away
    accepted_name TEXT,            -- NULL when this name is itself the accepted one
    accepted_norm TEXT,
    link          TEXT,            -- DOI or page; the attribution obligation
    extras        TEXT,            -- JSON: authorship, remarks, medical_use
    PRIMARY KEY (norm, code, source)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_register_accepted ON register(accepted_norm);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Row:
    """One name as the register published it, before the ambiguity filter."""

    ident: str
    name: str
    rank: str
    status: str
    native_status: str
    accepted_id: str | None
    link: str | None
    extras: dict = field(default_factory=dict)


# ---------------------------------------------------------------- routes


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=120.0, follow_redirects=True,
        headers={"User-Agent": "binomen/0.3 (register harvest; +https://github.com/Nebraskinator/binomen)"})


def archive_available(dataset: int) -> bool:
    """Does ChecklistBank hold a prebuilt DwC-A for this dataset?

    Only two of the three registers do, which is why `route="auto"` exists. A
    404 here is a routing fact, not a failure.
    """
    with _client() as c:
        r = c.head(f"{CLB}/dataset/{dataset}/export.zip", params={"format": "DWCA"})
        return r.status_code == 200


def from_archive(spec: RegisterSpec, cache_dir: Path) -> tuple[list[Row], dict]:
    """Stream the prebuilt Darwin Core Archive.

    ChecklistBank's DwC-A flavour is a single flat TSV plus a metadata.yaml, not
    the multi-file archive the name suggests, so there is no meta.xml to parse.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    local = cache_dir / f"clb-{spec.dataset}.zip"
    if not local.exists():
        with _client() as c, c.stream(
                "GET", f"{CLB}/dataset/{spec.dataset}/export.zip",
                params={"format": "DWCA"}) as r:
            r.raise_for_status()
            with open(local, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)

    rows: list[Row] = []
    with zipfile.ZipFile(local) as z:
        name = next(n for n in z.namelist() if n.endswith(".tsv"))
        meta = {}
        if "metadata.yaml" in z.namelist():
            meta = _thin_yaml(z.read("metadata.yaml").decode("utf8"))
        with z.open(name) as raw:
            fh = io.TextIOWrapper(raw, encoding="utf8", newline="")
            header = fh.readline().rstrip("\r\n").split("\t")
            idx = {h: i for i, h in enumerate(header)}

            def col(parts: list[str], key: str) -> str:
                i = idx.get(key, -1)
                return parts[i].strip() if 0 <= i < len(parts) else ""

            for line in fh:
                p = line.rstrip("\r\n").split("\t")
                native = col(p, "dwc:taxonomicStatus").lower()
                rows.append(Row(
                    ident=col(p, "dwc:taxonID"),
                    name=col(p, "dwc:scientificName"),
                    rank=col(p, "dwc:taxonRank").lower(),
                    status=_status(native),
                    native_status=native,
                    accepted_id=col(p, "dwc:acceptedNameUsageID") or None,
                    link=None,
                    extras={k: v for k, v in
                            (("authorship", col(p, "dwc:scientificNameAuthorship")),)
                            if v},
                ))
    return rows, meta


def dataset_version(dataset: int) -> dict:
    """The register's own version string, not the date we happened to fetch it.

    "LPSN as of 2026-07-26" is a checkable statement about the register; "we
    downloaded this on Tuesday" is a statement about us. The archive route reads
    this from metadata.yaml; the API route has to ask.
    """
    try:
        with _client() as c:
            r = c.get(f"{CLB}/dataset/{dataset}")
            r.raise_for_status()
            d = r.json()
        return {k: str(d[k]) for k in ("version", "issued") if d.get(k)}
    except Exception:
        # Provenance is worth a request, never worth failing a harvest over.
        return {}


def from_api(spec: RegisterSpec, page: int = 1000, sleep: float = 0.0) -> tuple[list[Row], dict]:
    """Page `nameusage/search`, which every dataset supports.

    Slower than an archive -- 45 requests for LPSN, 23 for ICTV -- and worth it:
    the API carries the per-record `link` that the archive omits, and LPSN's
    terms require shipping a link back to the record a value came from.
    """
    rows: list[Row] = []
    total = None
    offset = 0
    with _client() as c:
        while True:
            r = c.get(f"{CLB}/dataset/{spec.dataset}/nameusage/search",
                      params={"limit": page, "offset": offset})
            r.raise_for_status()
            payload = r.json()
            total = payload.get("total", 0)
            batch = payload.get("result", [])
            if not batch:
                break
            for item in batch:
                usage = item.get("usage") or {}
                nm = usage.get("name") or {}
                native = str(usage.get("status") or "").lower()
                acc = usage.get("accepted") or {}
                acc_name = (acc.get("name") or {}).get("scientificName") or acc.get("label")
                extras = {}
                if nm.get("authorship"):
                    extras["authorship"] = nm["authorship"]
                if nm.get("nomStatus"):
                    extras["nom_status"] = nm["nomStatus"]
                if usage.get("remarks"):
                    extras["remarks"] = usage["remarks"][:400]
                rows.append(Row(
                    ident=str(usage.get("id") or item.get("id") or ""),
                    name=nm.get("scientificName") or "",
                    rank=str(nm.get("rank") or "").lower(),
                    status=_status(native, nm.get("nomStatus")),
                    native_status=native or (nm.get("nomStatus") or ""),
                    # The API gives the accepted name directly, so there is no
                    # id to resolve in a second pass the way the archive needs.
                    accepted_id=None,
                    link=usage.get("link") or nm.get("link"),
                    extras=extras,
                ))
                if acc_name:
                    rows[-1].extras["_accepted_name"] = acc_name
            offset += len(batch)
            if payload.get("last") or (total and offset >= total):
                break
            if sleep:
                time.sleep(sleep)
    return rows, {"total_reported": total, **dataset_version(spec.dataset)}


def _status(native: str, nom_status: str | None = None) -> str:
    """Normalise a status while the caller keeps the register's own word.

    `codes.py` rule 1: never silently flatten "not validly published",
    "illegitimate" and "unaccepted" into one label. The normalised value is for
    joining; the native one is for reporting.
    """
    n = (native or "").lower()
    if n in NO_STANDING or (nom_status or "").lower() in {"not established", "nomen nudum"}:
        return "no_standing"
    if n in SYNONYM:
        return "synonym"
    if n in ACCEPTED:
        return "accepted"
    return "accepted" if not n else n.replace(" ", "_")


def _thin_yaml(text: str) -> dict:
    """Pull the few scalars we cite from metadata.yaml without a YAML dependency.

    Deliberately shallow: title, doi, version, issued, licence. Anything harder
    than `key: value` at the top level is not something we quote.
    """
    out = {}
    for line in text.splitlines():
        if line[:1].isspace() or ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip().strip('"')
        if v and k.strip() in {"key", "doi", "title", "alias", "version", "issued", "license"}:
            out[k.strip()] = v
    return out


# ---------------------------------------------------------------- filter


def backbone_superseded(code: str, path: Path) -> set[str]:
    """Normalised names the backbone records as superseded, for one code.

    The cross-source half of the inclusion rule needs this. A register saying
    "*Borrelia burgdorferi* is accepted" is only news when NCBI says that name
    was replaced -- and that is a fact about NCBI, so the register harvest
    cannot decide it alone.

    An absent index is not an error: the harvest then keeps only intra-register
    ambiguity and says so in the stats, rather than silently shipping a register
    that looks complete and is not.
    """
    if not path.exists():
        return set()
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {n for (n,) in db.execute(
            "SELECT norm FROM verdicts WHERE verdict='superseded' AND code=?", (code,))}
    finally:
        db.close()


def backbone_names(path: Path, wanted: set[str]) -> set[str]:
    """Of `wanted`, the normalised names the backbone holds under any name class.

    The bound for registers that are nomenclators rather than checklists.
    Species Fungorum Plus lists every described fungal name; NCBI Taxonomy holds
    mostly what has been sequenced, so 72% of the fungal register is names no
    NCBI record uses. Shipping them triples the register database to serve names
    a reader is unlikely to write and the backbone could not corroborate anyway.

    Uses the full index (`binomen.sqlite`), not stage 1: stage 1 holds only names
    that already carry an ambiguity, which would bound the register to what we
    already knew and defeat the point.
    """
    if not path.exists() or not wanted:
        return set()
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        db.execute("CREATE TEMP TABLE want(norm TEXT PRIMARY KEY)")
        db.executemany("INSERT OR IGNORE INTO want VALUES (?)", ((n,) for n in wanted))
        return {n for (n,) in db.execute(
            "SELECT want.norm FROM want JOIN name_norm ON name_norm.norm = want.norm")}
    finally:
        db.close()


def accepted_name_of(r: Row, by_id: dict[str, Row]) -> str | None:
    """The name this row points at, from whichever route supplied it.

    The API gives the accepted name as text; the archive gives an id to resolve
    against its own rows. Callers should not have to know which route ran.
    """
    direct = r.extras.get("_accepted_name")
    if direct:
        return direct
    target = by_id.get(r.accepted_id or "")
    return target.name if target else None


def bound_to_backbone(rows: list[Row], index: Path) -> tuple[list[Row], int]:
    """Drop register names the backbone has never recorded, keeping partners.

    A synonym survives if the backbone knows either side of the pair: knowing the
    old name is what lets us warn a reader who wrote it, and knowing the new one
    is what lets us corroborate the register's answer.
    """
    if not index.exists() or not rows:
        return rows, 0
    by_id = {r.ident: r for r in rows if r.ident}
    known = backbone_names(index, {normalize_name(r.name) for r in rows})
    if not known:
        return rows, 0
    partners = set()
    for r in rows:
        if normalize_name(r.name) in known:
            acc = accepted_name_of(r, by_id)
            if acc:
                partners.add(normalize_name(acc))
    keep = [r for r in rows
            if normalize_name(r.name) in known or normalize_name(r.name) in partners]
    return keep, len(rows) - len(keep)


def ambiguous_only(rows: list[Row],
                   superseded: set[str] | None = None) -> tuple[list[Row], dict[str, int]]:
    """Keep the rows that carry an ambiguity, drop the rest.

    Four ways in. Three are visible inside the register: the name is a synonym of
    something; something is a synonym of it; or it has no standing under its own
    code. The fourth needs the backbone: the register holds the name as accepted
    while NCBI records it as superseded, which is the disagreement the whole
    project exists to report -- *Borrelia* against *Borreliella*.

    Without the fourth, a register like ICTV's -- 22,671 names, every one
    accepted, no internal synonymy -- contributes nothing at all.

    An accepted name that nothing disagrees about is exactly the case where the
    register has no news, and
    `docs/adr/0001-ambiguity-only-local-database.md` says silence is the answer.
    """
    superseded = superseded or set()
    by_id = {r.ident: r for r in rows if r.ident}
    keep: dict[str, Row] = {}
    stats = {"in": len(rows), "rank_dropped": 0, "synonym": 0, "accepted_target": 0,
             "no_standing": 0, "backbone_disagrees": 0, "unambiguous_dropped": 0}

    ranked = []
    for r in rows:
        if not r.name:
            continue
        if r.rank and r.rank not in KEEP_RANKS:
            stats["rank_dropped"] += 1
            continue
        ranked.append(r)

    for r in ranked:
        if r.status == "synonym":
            keep[r.ident or r.name] = r
            stats["synonym"] += 1
            # The name it points at is half the disagreement; without it the
            # row says "this is wrong" and cannot say what is right.
            target = by_id.get(r.accepted_id or "")
            if target is not None and (target.ident or target.name) not in keep:
                keep[target.ident or target.name] = target
                stats["accepted_target"] += 1
        elif r.status == "no_standing":
            keep[r.ident or r.name] = r
            stats["no_standing"] += 1
        elif r.status == "accepted" and normalize_name(r.name) in superseded:
            # The register keeps a name NCBI retired. This is the 1,003-row
            # `lpsn_kept_it` case measured in FINDINGS §6, and for ICTV it is
            # the only case there is.
            keep[r.ident or r.name] = r
            stats["backbone_disagrees"] += 1

    stats["unambiguous_dropped"] = len(ranked) - len(keep)
    stats["out"] = len(keep)
    return list(keep.values()), stats


# ---------------------------------------------------------------- write


def write(spec: RegisterSpec, rows: list[Row], out: Path, meta: dict) -> int:
    """Replace this register's rows. Other registers in the file are untouched."""
    out.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(out)
    db.executescript(SCHEMA)
    db.execute("DELETE FROM register WHERE source = ?", (spec.key,))

    by_id = {r.ident: r for r in rows if r.ident}
    payload = []
    for r in rows:
        acc_name = accepted_name_of(r, by_id)
        extras = {k: v for k, v in r.extras.items()
                  if not k.startswith("_") and k not in DROP_EXTRAS}
        if acc_name and normalize_name(acc_name) == normalize_name(r.name):
            # An accepted name equal to the record's own name is the echo fault
            # from FINDINGS §8: a resolver returning its input when it does not
            # know. Refuse it at write time rather than reading it back later as
            # a disagreement that never existed.
            acc_name = None
        payload.append((
            normalize_name(r.name), r.name, spec.code, spec.key, r.rank or None,
            r.status, r.native_status or None, acc_name,
            normalize_name(acc_name) if acc_name else None,
            r.link, json.dumps(extras, ensure_ascii=False) if extras else None,
        ))

    db.executemany(
        "INSERT OR REPLACE INTO register "
        "(norm,name,code,source,rank,status,native_status,accepted_name,accepted_norm,link,extras) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", payload)

    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for k, v in {
        f"{spec.key}.code": spec.code,
        f"{spec.key}.source": spec.source,
        f"{spec.key}.licence": spec.licence,
        f"{spec.key}.dataset": str(spec.dataset),
        f"{spec.key}.harvested_at": stamp,
        f"{spec.key}.rows": str(len(payload)),
        **({f"{spec.key}.doi": spec.doi} if spec.doi else {}),
        **({f"{spec.key}.version": meta["version"]} if meta.get("version") else {}),
        **({f"{spec.key}.issued": meta["issued"]} if meta.get("issued") else {}),
    }.items():
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (k, v))
    db.commit()
    db.execute("VACUUM")
    db.close()
    return len(payload)


def overlay_lpsn_medical_use(registers: Path, lpsn: Path) -> dict[str, int]:
    """Add LPSN's medical-use recommendations to the harvested ICNP rows.

    ChecklistBank's mirror carries LPSN's names, statuses and DOIs but not its
    `lorn_status` -- the field that says "this register recommends its own name
    for medical use". Those rows are what `docs/FINDINGS.md` §6 identifies as the
    product: without them binomen hands a clinician writing about Lyme disease
    the genus name the ICNP register advises against.

    The source is a harvest made with LPSN credentials, which are build-time only
    and stay on the maintainer's machine. This pass is a local join against that
    file, so it needs no network and no credentials -- and an absent file is not
    an error, it just means the register ships without the flags.

    Only ever ADDS a key to `extras`. The register's own name, status and link
    come from ChecklistBank and are not touched, so provenance stays traceable to
    the artefact it was harvested from.
    """
    stats = {"lpsn_rows": 0, "matched": 0, "recommending": 0}
    if not lpsn.exists() or not registers.exists():
        return stats

    src = sqlite3.connect(f"file:{lpsn}?mode=ro", uri=True)
    flags = dict(src.execute(
        "SELECT norm, medical_use FROM lpsn WHERE medical_use IS NOT NULL AND medical_use <> ''"))
    src.close()
    stats["lpsn_rows"] = len(flags)

    db = sqlite3.connect(registers)
    updates = []
    for norm, accepted_norm, extras in db.execute(
            "SELECT norm, accepted_norm, extras FROM register WHERE source = 'lpsn'"):
        mine = flags.get(norm)
        # LPSN attaches lorn_status to a *name*, so a superseded name carries
        # "not recommended" and the name it points at carries "recommended".
        # Storing only the row's own flag would let a caller read
        # `medical_use: not recommended` beside `accepted_name: Borrelia
        # burgdorferi` and conclude the exact opposite of what LPSN says. The
        # preferred name's flag therefore travels with the row that names it.
        theirs = flags.get(accepted_norm) if accepted_norm else None
        if not mine and not theirs:
            continue
        payload = json.loads(extras) if extras else {}
        if mine:
            payload["medical_use"] = mine
        if theirs:
            payload["accepted_medical_use"] = theirs
        updates.append((json.dumps(payload, ensure_ascii=False), norm))
        stats["matched"] += 1
        if str(theirs or mine).startswith("recommend"):
            stats["recommending"] += 1
    db.executemany(
        "UPDATE register SET extras = ? WHERE source = 'lpsn' AND norm = ?", updates)
    db.execute("INSERT OR REPLACE INTO meta VALUES ('lpsn.medical_use_rows', ?)",
               (str(stats["matched"]),))
    db.commit()
    db.close()
    return stats


# ---------------------------------------------------------------- driver


def harvest(spec: RegisterSpec, out: Path, cache_dir: Path, backbone: Path,
            index: Path | None = None, page: int = 1000, sleep: float = 0.0) -> dict:
    route = spec.route
    if route == "auto":
        route = "archive" if archive_available(spec.dataset) else "api"
    rows, meta = (from_archive(spec, cache_dir) if route == "archive"
                  else from_api(spec, page=page, sleep=sleep))
    superseded = backbone_superseded(spec.code, backbone)
    kept, stats = ambiguous_only(rows, superseded)
    stats["backbone_names"] = len(superseded)
    stats["unknown_dropped"] = 0
    if index is not None:
        kept, dropped = bound_to_backbone(kept, index)
        stats["unknown_dropped"] = dropped
    n = write(spec, kept, out, meta)
    stats["route"] = route
    stats["written"] = n
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("registers", nargs="*", default=list(REGISTERS),
                    help=f"which to harvest (default: {', '.join(REGISTERS)}; "
                         f"also available: {', '.join(ALTERNATES)})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--cache", type=Path, default=REPO / "data" / "cache" / "clb")
    ap.add_argument("--backbone", type=Path, default=REPO / "data" / "binomen-stage1.sqlite",
                    help="stage-1 index, used to spot names the register keeps and "
                         "NCBI retired; without it only intra-register ambiguity is kept")
    ap.add_argument("--bound-to", type=Path, default=None, metavar="INDEX",
                    help="full index (data/binomen.sqlite). When given, register names "
                         "the backbone has never recorded are dropped. Meant for "
                         "nomenclators like Species Fungorum, which list every described "
                         "name rather than every used one")
    ap.add_argument("--keep-authorship", action="store_true",
                    help="keep author citations in extras (adds ~7 MB on the fungal register)")
    ap.add_argument("--lpsn-medical-use", type=Path, default=REPO / "data" / "lpsn.sqlite",
                    metavar="DB", help="credentialled LPSN harvest to take medical-use "
                                       "recommendations from; skipped when absent")
    ap.add_argument("--page", type=int, default=1000)
    ap.add_argument("--sleep", type=float, default=0.0, help="seconds between API pages")
    ap.add_argument("--max-mb", type=float, default=10.0,
                    help="fail if the register database exceeds this (default 10, "
                         "the registers' share of the 40 MB bundle)")
    a = ap.parse_args()

    unknown = [r for r in a.registers if r not in ALL_SPECS]
    if unknown:
        print(f"unknown register(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    for key in a.registers:
        spec = ALL_SPECS[key]
        print(f"\n{key}  ({spec.code}, ChecklistBank {spec.dataset})")
        t0 = time.time()
        if not a.keep_authorship:
            DROP_EXTRAS.add("authorship")
        st = harvest(spec, a.out, a.cache, a.backbone, index=a.bound_to,
                     page=a.page, sleep=a.sleep)
        print(f"  route            {st['route']}")
        print(f"  names seen       {st['in']:,}")
        if not st["backbone_names"]:
            print(f"  backbone         MISSING ({a.backbone}) -- cross-source "
                  f"disagreements not detected")
        print(f"  above genus      {st['rank_dropped']:,} dropped")
        print(f"  unambiguous      {st['unambiguous_dropped']:,} dropped")
        if st["unknown_dropped"]:
            print(f"  unknown to NCBI  {st['unknown_dropped']:,} dropped")
        print(f"  kept             {st['written']:,}"
              f"  (synonym {st['synonym']:,}, targets {st['accepted_target']:,},"
              f" no standing {st['no_standing']:,},"
              f" backbone disagrees {st['backbone_disagrees']:,})")
        print(f"  {time.time() - t0:.1f}s")

    if "lpsn" in a.registers:
        ov = overlay_lpsn_medical_use(a.out, a.lpsn_medical_use)
        if ov["matched"]:
            print(f"\nmedical-use overlay from {a.lpsn_medical_use.name}")
            print(f"  flags available  {ov['lpsn_rows']:,}")
            print(f"  applied          {ov['matched']:,}"
                  f"  ({ov['recommending']:,} recommend the register's own name)")
        else:
            print(f"\nmedical-use overlay: nothing applied from {a.lpsn_medical_use} "
                  f"-- register ships without medical-use flags")

    size = a.out.stat().st_size / 1e6
    print(f"\n{a.out}  {size:.1f} MB")
    enforce_budget(a.out, a.max_mb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
