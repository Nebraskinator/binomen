"""Build the binomen indexes from an NCBI taxdump archive.

Produces two artifacts, deliberately separate:

  stage1.sqlite   ~tens of MB. Verdict table plus per-code Bloom filters.
                  Answers "is there anything to worry about with this name?"
                  This is the one a user has to install.

  binomen.sqlite  the full local backbone: names, nodes, merges, overlay.
                  Needed only when stage 1 escalates. Optional -- stage 1
                  degrades honestly without it.

Neither is committed. The full taxdump is large, and freezing a copy in git
would reintroduce exactly the staleness this project exists to detect.

Usage
-----
    binomen-build-index                      # download, build both
    binomen-build-index --audit              # report what is in the archive, build nothing
    binomen-build-index --full               # keep strain/environmental taxa too
    binomen-build-index --fixture tests/fixtures/taxdump --out /tmp/t.sqlite

NCBI Taxonomy is public domain (US government work). The release version is
recorded in `meta` and echoed in the provenance of every response.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..bloom import BloomFilter
from ..codes import Code, code_anchors

TAXDUMP_URL = "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz"
TAXDUMP_MD5_URL = TAXDUMP_URL + ".md5"

SCHEMA = Path(__file__).with_name("schema.sql")
SCHEMA_STAGE1 = Path(__file__).with_name("schema_stage1.sql")
SCHEMA_FIELD = Path(__file__).with_name("schema_field.sql")
DATA_DIR = Path(__file__).resolve().parents[3] / "data"
DEFAULT_OUT = DATA_DIR / "binomen.sqlite"
DEFAULT_STAGE1 = DATA_DIR / "binomen-stage1.sqlite"
DEFAULT_FIELD = DATA_DIR / "binomen-field.sqlite"

# --- what we keep, and why -------------------------------------------------
# Every class here was checked against what the tools actually query.

# Nomenclaturally load-bearing. These ARE the answer.
CORE_CLASSES = {
    "scientific name", "synonym", "equivalent name", "genbank synonym",
    "misspelling", "misnomer", "includes", "in-part",
    "anamorph", "teleomorph", "genbank anamorph",   # pre-2011 ICNafp dual names
}
# The author citation. Worth keeping: without it we would have to either omit
# authorship or invent it, and an invented authority string is citable.
AUTHORITY_CLASS = "authority"
# Useful for matching what a user typed, but only at genus rank and above --
# "human" and "baker's yeast" are worth resolving, ten thousand strain-level
# vernaculars are not.
VERNACULAR_CLASSES = {"common name", "genbank common name", "blast name",
                      "acronym", "genbank acronym"}
# Dropped entirely. `type material` is strain designations (ATCC 25922, DSM
# 1103, ...). Numerous, and nomenclaturally irrelevant: a strain code is not a
# name that anybody asks "is this still current?" about.
DROP_CLASSES = {"type material"}

# Taxa that are not the subject of nomenclature questions. These dominate the
# taxid count in NCBI Taxonomy and contribute nothing here.
JUNK_NAME_RE = re.compile(
    r"(uncultured|environmental sample|unclassified|metagenome|"
    r"\bunidentified\b|^candidate division|\bincertae sedis\b|"
    r"\bsp\.\s|\bcf\.\s|\baff\.\s|\bbacterium\s+(enrichment|adurb|str)\b)",
    re.I,
)
VERNACULAR_MIN_RANKS = {
    "genus", "subgenus", "family", "subfamily", "order", "class", "phylum",
    "kingdom", "superkingdom", "domain", "realm", "clade", "no rank",
}


# NCBI wraps a genus in square brackets when the species is known to be
# misplaced in it: "[Clostridium] difficile", "[Ruminococcus] gnavus". The
# brackets are an editorial annotation, not part of the name -- and they are
# attached to precisely the taxa this project cares about, because a genus
# flagged as wrong is a genus about to change. Treating them as literal
# characters meant a user typing the plain binomial matched nothing, which is
# the silent-lookup-miss failure mode, committed by the tool that exists to
# detect it. Folded for lookup; the raw string is stored intact and the
# annotation is surfaced (see BRACKETED_NOTE).
_BRACKETS = str.maketrans("", "", "[]()")


def normalize_name(name: str) -> str:
    """Fold a name to a lookup key.

    Conservative on purpose. Case, whitespace, accents, NCBI's misplacement
    brackets, and a few orthographic variants only. We do NOT fold subspecific
    rank markers: "Escherichia coli" and "Escherichia coli subsp. coli" are
    different taxa, and merging their keys would manufacture the exact false
    conflation this package detects.
    """
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("×", "x ").replace("'", "").replace("’", "")
    s = s.translate(_BRACKETS)
    return " ".join(s.lower().split())


def split_designation(name: str, code: str | None = None) -> tuple[str, str] | None:
    """Split "Clostridioides difficile 630" into ("Clostridioides difficile", "630").

    A strain name is a binomial plus a laboratory designation, and only the
    binomial is governed by a nomenclatural code. When the species is
    transferred, every one of its strains inherits the new genus:
    "Clostridium difficile 630" became "Clostridioides difficile 630" without
    anyone publishing anything about strain 630.

    That decomposition is why the field-edition index does not need to store
    strain taxa at all -- there are over a million of them in NCBI and each one
    is derivable. Resolve the binomial, carry the designation through, and the
    coverage is complete at zero cost in file size.

    Returns None when the name is not of this shape, including for viruses,
    whose names are not binomials.
    """
    if code == "ICTV":
        return None
    toks = name.split()
    if len(toks) < 3 or not toks[0][:1].isupper():
        return None
    start = 1
    if toks[0] == "Candidatus" and len(toks) > 2:
        start = 2
    if start >= len(toks) or not toks[start][:1].islower():
        return None
    if toks[start] in {"x", "\u00d7"} and start + 1 < len(toks) and toks[start + 1][:1].islower():
        start += 1

    end = start + 1
    while end + 1 < len(toks) and toks[end] in _RANK_MARKERS and toks[end + 1][:1].islower():
        end += 2
    if end >= len(toks):
        return None

    binomial = " ".join(toks[:end])
    designation = " ".join(toks[end:])
    # An author citation is not a designation. "Homo sapiens Linnaeus, 1758"
    # must not become strain "Linnaeus, 1758".
    if strip_authority(name, code) is not None:
        return None
    return binomial, designation


_ABBREV_RE = re.compile(r"^([A-Za-z])\.\s*(.+)$")


def split_abbreviation(name: str) -> tuple[str, str] | None:
    """Split "C. difficile" into ("c", "difficile") -- genus initial, remainder.

    The abbreviated genus is how these organisms actually appear in prose.
    "E. coli" outnumbers "Escherichia coli" in most clinical writing, and a
    resolver that answers `unknown` to it is answering `unknown` to the common
    case -- while its own input schema advertises that it accepts the form.

    Returned folded, ready to build a lookup key against normalize_name'd text.
    None when the string is not of that shape, which includes a bare genus, a
    full binomial, and a gene symbol.
    """
    m = _ABBREV_RE.match(name.strip())
    if not m:
        return None
    initial, rest = m.group(1).lower(), normalize_name(m.group(2))
    if not rest or not rest[:1].isalpha():
        return None
    return initial, rest


def is_bracketed(name: str) -> bool:
    """Did the source flag this name's generic placement as wrong?"""
    return "[" in name or "]" in name


# Infraspecific connecting terms. Part of the name, not the authority.
_RANK_MARKERS = {"subsp.", "ssp.", "var.", "subvar.", "f.", "forma", "subf.",
                 "cv.", "x", "×", "nothosubsp."}
_YEAR = re.compile(r"^\(?(1[6-9]\d{2}|20[0-2]\d)\)?[,.);]?$")

# Ranks at which NCBI attaches an author citation to the name. Everything below
# species -- strains, isolates, serovars -- carries a *strain designation*
# instead, which looks superficially similar and means something completely
# different.
_AUTHORITY_RANKS = {
    "species", "subspecies", "genus", "subgenus", "family", "subfamily", "tribe",
    "subtribe", "order", "suborder", "infraorder", "superfamily", "class",
    "subclass", "infraclass", "superorder", "phylum", "subphylum", "kingdom",
    "subkingdom", "superkingdom", "domain", "varietas", "forma", "section",
    "subsection", "series", "species group", "species subgroup", "superclass",
}

# Ranks the field edition carries. Everything below is a strain or isolate
# designation, which is a laboratory identifier rather than a name under any
# nomenclatural code.
FIELD_RANKS = {
    "species", "subspecies", "genus", "subgenus", "family", "subfamily", "tribe",
    "subtribe", "order", "suborder", "infraorder", "superfamily", "class", "subclass",
    "infraclass", "superorder", "phylum", "subphylum", "kingdom", "subkingdom",
    "superkingdom", "domain", "realm", "varietas", "forma", "section", "subsection",
    "series", "species group", "species subgroup", "superclass", "clade",
}

# Tokens that mark what follows as a strain/isolate designation, not an author.
_STRAIN_MARKERS = {
    "str.", "strain", "serovar", "serotype", "serogroup", "biovar", "biotype",
    "pathovar", "pv.", "isolate", "clone", "genomovar", "genomosp.", "substr.",
    "type", "group", "subgroup", "morphovar", "phagovar", "chemoform",
}


def strip_authority(name: str, code: str | None = None, rank: str | None = None) -> str | None:
    """Return the bare binomial when a name row carries its author citation.

    NCBI stores many synonyms as complete nomenclatural citations rather than
    bare names:

        Clostridium difficile (Hall and O'Toole 1935) Prevot 1938 (Approved Lists 1980)

    Nobody types that. The user types "Clostridium difficile", which matched
    nothing. So the bare form is indexed as an ADDITIONAL lookup key while the
    raw string is stored intact -- the citation is real information and should
    be shown, it just should not be required as input.

    Returns None when the name is not of this shape, which is the common case
    and must stay cheap and safe. Five gates, and every one of them was earned:

      * **rank**. This is the important one. NCBI attaches author citations at
        species rank and above; below that a taxon carries a *strain
        designation*, which looks similar and means something else entirely.
        "Clostridioides difficile QCD-32g58" is a strain, and an earlier version
        of this function happily reduced it to "Clostridioides difficile" --
        giving every one of a species' strains the species' own lookup key and
        turning Escherichia coli into a 277-way homonym. That is false
        conflation, the error this package grades as *very major*, manufactured
        by the indexer. Hence the whitelist.
      * **strain markers**. "str.", "serovar", "isolate" and friends mark what
        follows as a designation, not an author.
      * **viruses**. ICTV names are not binomials and are full of capitalised
        words and digits that resemble authorities ("Severe acute respiratory
        syndrome coronavirus 2", "Influenza A virus (A/Puerto Rico/8/1934)").
      * **binomial shape**: capitalised genus, lowercase epithet.
      * **a real year or parenthetical**. A plausible publication year (1600 to
        the present) or a parenthesised basionym author. Catalogue numbers like
        "ATCC 6051" and "str. 6407" are four digits too, which is why the year
        pattern is range-bounded and why the strain-marker gate exists.
    """
    if code == "ICTV":
        return None
    if rank is not None and rank not in _AUTHORITY_RANKS:
        return None
    toks = name.split()
    if len(toks) < 3 or not toks[0][:1].isupper():
        return None
    start = 1
    if toks[0] == "Candidatus" and len(toks) > 2:
        start = 2
    if start >= len(toks) or not toks[start][:1].islower():
        return None
    # Nothospecies: "Rosa x damascena Mill." -- the hybrid marker sits between
    # genus and epithet and is part of the name.
    if toks[start] in {"x", "×"} and start + 1 < len(toks) and toks[start + 1][:1].islower():
        start += 1

    out = toks[: start + 1]
    i = start + 1
    while i + 1 < len(toks) and toks[i] in _RANK_MARKERS and toks[i + 1][:1].islower():
        out.extend([toks[i], toks[i + 1]])
        i += 2
    if i >= len(toks):
        return None

    tail = toks[i:]
    if any(t.lower() in _STRAIN_MARKERS for t in tail):
        return None
    # Must look like a citation, not a catalogue entry: a parenthesised author
    # group, or a plausible publication year somewhere in the tail.
    has_paren = any(t.startswith("(") for t in tail)
    has_year = any(_YEAR.match(t) for t in tail)
    abbreviated_author = (len(tail) == 1 and tail[0][:1].isupper()
                          and tail[0].endswith(".") and len(tail[0]) <= 12)
    if not (has_paren or has_year or abbreviated_author):
        return None
    return " ".join(out)


# --- archive handling -------------------------------------------------------
def _download(url: str, dest: Path, quiet: bool = False) -> Path:
    if not quiet:
        print(f"[binomen] downloading {url}", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "binomen/0.2 (index builder)"})
    with urllib.request.urlopen(req, timeout=900) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    return dest


def _verify_md5(archive: Path, quiet: bool = False) -> str | None:
    """A truncated download yields an index missing taxa, which presents
    downstream as 'this organism does not exist' -- a wrong answer wearing the
    costume of a legitimate negative result. Worth one extra request."""
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            md5file = Path(tf.name)
        _download(TAXDUMP_MD5_URL, md5file, quiet)
        expected = md5file.read_text().split()[0]
        h = hashlib.md5()
        with open(archive, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        actual = h.hexdigest()
        if actual != expected:
            raise RuntimeError(f"taxdump md5 mismatch: expected {expected}, got {actual}. "
                               "Delete the archive and re-run rather than building from it.")
        if not quiet:
            print(f"[binomen] md5 verified: {actual}", file=sys.stderr)
        return actual
    except RuntimeError:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"[binomen] warning: could not verify md5 ({e}); continuing", file=sys.stderr)
        return None


def _iter_dmp(path: Path):
    """Yield field lists from an NCBI .dmp file.

    Bar-tab separated with a trailing '\\t|'. A naive split('|') leaves
    whitespace on every field, which is a real and quiet source of lookup
    misses.
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.endswith("\t|"):
                line = line[:-2]
            yield [p.strip() for p in line.split("\t|")]


# --- audit ------------------------------------------------------------------
def audit(src: Path) -> dict:
    """Report what is actually in the archive, so size claims are measured.

    Prints rows and bytes per name class, and how much the slim build drops.
    Build nothing.
    """
    per_class: dict[str, list[int]] = {}
    junk_names = 0
    for f in _iter_dmp(src / "names.dmp"):
        if len(f) < 4:
            continue
        klass, name = f[3], f[1]
        e = per_class.setdefault(klass, [0, 0])
        e[0] += 1
        e[1] += len(name) + len(f[2]) + len(klass) + 8
        if JUNK_NAME_RE.search(name):
            junk_names += 1
    n_nodes = sum(1 for _ in _iter_dmp(src / "nodes.dmp"))
    n_merged = sum(1 for _ in _iter_dmp(src / "merged.dmp")) if (src / "merged.dmp").exists() else 0

    total_rows = sum(v[0] for v in per_class.values())
    total_bytes = sum(v[1] for v in per_class.values())
    kept_rows = sum(v[0] for k, v in per_class.items()
                    if k in CORE_CLASSES or k == AUTHORITY_CLASS)
    kept_bytes = sum(v[1] for k, v in per_class.items()
                     if k in CORE_CLASSES or k == AUTHORITY_CLASS)

    print(f"\n{'name class':28s} {'rows':>10s} {'MB':>8s}  kept?")
    for k, (rows, b) in sorted(per_class.items(), key=lambda kv: -kv[1][1]):
        keep = ("core" if k in CORE_CLASSES else
                "authority" if k == AUTHORITY_CLASS else
                "DROP" if k in DROP_CLASSES else
                "genus+ only" if k in VERNACULAR_CLASSES else "drop")
        print(f"{k:28s} {rows:10d} {b/1e6:8.1f}  {keep}")
    print(f"\n{'TOTAL names':28s} {total_rows:10d} {total_bytes/1e6:8.1f}")
    print(f"{'kept by slim build':28s} {kept_rows:10d} {kept_bytes/1e6:8.1f}"
          f"   ({100*kept_bytes/max(1,total_bytes):.0f}% of name bytes)")
    print(f"\nnodes: {n_nodes}   merged: {n_merged}")
    print(f"names matching the junk filter (uncultured / environmental / sp. / "
          f"unclassified): {junk_names} ({100*junk_names/max(1,total_rows):.0f}%)")
    print("\nNote: the old build also materialized a root-first lineage per taxon. "
          f"At ~840 bytes/taxon that would have been ~{n_nodes*840/1e9:.1f} GB on its own. "
          "It is now one `code` column.")
    return {"per_class": per_class, "n_nodes": n_nodes, "n_merged": n_merged}


# --- code assignment --------------------------------------------------------
def assign_codes(parent: dict[int, int], sci: dict[int, str], quiet: bool = False) -> dict[int, str]:
    """One iterative pass from the root, carrying the current code downward.

    Deepest anchor wins, which is the behavior we want: NCBI nests Microsporidia
    inside Fungi, and the dual-claimed anchor deeper in the tree correctly
    overrides the ICNafp it would otherwise inherit.
    """
    anchors = {k.lower(): v.value for k, v in code_anchors().items()}
    children: dict[int, list[int]] = {}
    roots = []
    for t, p in parent.items():
        if p == t or p not in parent:
            roots.append(t)
        else:
            children.setdefault(p, []).append(t)

    codes: dict[int, str] = {}
    stack = [(r, Code.UNDETERMINED.value) for r in roots]
    while stack:
        t, code = stack.pop()
        name = (sci.get(t) or "").lower()
        if name in anchors:
            code = anchors[name]
        elif name.endswith(("viridae", "virales", "viricetes", "viricota")):
            code = Code.ICTV.value
        codes[t] = code
        for c in children.get(t, ()):
            stack.append((c, code))
    if not quiet:
        from collections import Counter
        print(f"[binomen]   codes: {dict(Counter(codes.values()))}", file=sys.stderr)
    return codes


# --- build ------------------------------------------------------------------
def build(src: Path, out: Path, stage1_out: Path | None, field_out: Path | None = None, *,
          version: str | None = None, md5: str | None = None, overlay: Path | None = None,
          full: bool = False, fp_rate: float = 0.001, quiet: bool = False) -> dict:
    def log(m: str) -> None:
        if not quiet:
            print(f"[binomen] {m}", file=sys.stderr)

    out.parent.mkdir(parents=True, exist_ok=True)
    _clear(out)
    conn = sqlite3.connect(out)
    conn.executescript(SCHEMA.read_text())
    cur = conn.cursor()
    cur.execute("PRAGMA synchronous = OFF")

    # nodes
    log("parsing nodes.dmp")
    parent: dict[int, int] = {}
    rank: dict[int, str] = {}
    for f in _iter_dmp(src / "nodes.dmp"):
        if len(f) < 3:
            continue
        t = int(f[0])
        parent[t] = int(f[1])
        rank[t] = f[2]
    log(f"  {len(parent)} nodes")

    # scientific names first -- needed for code assignment and junk filtering
    log("parsing names.dmp")
    sci: dict[int, str] = {}
    rows_all = []
    for f in _iter_dmp(src / "names.dmp"):
        if len(f) < 4:
            continue
        t, name, uniq, klass = int(f[0]), f[1], f[2] or None, f[3]
        rows_all.append((t, name, uniq, klass))
        if klass == "scientific name":
            sci[t] = name
    log(f"  {len(rows_all)} name rows")

    codes = assign_codes(parent, sci, quiet)

    # Which taxa survive the slim build.
    #
    # The filter drops environmental and strain-level noise, which is roughly
    # half of NCBI by taxid count and none of it the subject of a nomenclature
    # question. But it is a name-shape heuristic, and a name-shape heuristic
    # that silently eats a real taxon is exactly the failure this project is
    # about. So it is overridden by evidence: **any taxon carrying
    # nomenclatural history is kept regardless of what its name looks like.**
    # If a taxon has a synonym, an equivalent name, or absorbed another taxid,
    # it has history, and history is the entire subject matter here.
    if full:
        keep_taxid = set(parent)
        n_junk = n_rescued = 0
    else:
        with_history = {t for t, _n, _u, k in rows_all
                        if k in CORE_CLASSES and k != "scientific name"}
        with_history |= set(merged_preview(src))
        junk = {t for t in parent if JUNK_NAME_RE.search(sci.get(t, ""))}
        n_rescued = len(junk & with_history)
        keep_taxid = set(parent) - (junk - with_history)
        n_junk = len(parent) - len(keep_taxid)
    if full:
        log("  --full: keeping all taxa and all name classes")
    else:
        log(f"  dropping {n_junk} environmental/unclassified/strain-level taxa "
            f"({100*n_junk/max(1,len(parent)):.0f}%); "
            f"kept {n_rescued} that matched the filter but carry nomenclatural history")

    nbatch = [(t, parent[t], rank[t], codes.get(t, Code.UNDETERMINED.value)) for t in keep_taxid]
    cur.executemany("INSERT OR REPLACE INTO nodes VALUES (?,?,?,?)", nbatch)

    # names, filtered
    kept_names, norm_batch, dropped, n_bare = [], [], 0, 0
    for t, name, uniq, klass in rows_all:
        if t not in keep_taxid or klass in DROP_CLASSES:
            dropped += 1
            continue
        if klass in VERNACULAR_CLASSES and rank.get(t) not in VERNACULAR_MIN_RANKS:
            dropped += 1
            continue
        if klass not in CORE_CLASSES and klass != AUTHORITY_CLASS \
                and klass not in VERNACULAR_CLASSES:
            dropped += 1
            continue
        kept_names.append((t, name, uniq, klass))
        if klass != AUTHORITY_CLASS:
            norm_batch.append((normalize_name(name), t, name, klass))
            # unique_name disambiguates homonyms: "Bacillus <stick insect>".
            if uniq and uniq != name:
                norm_batch.append((normalize_name(uniq), t, uniq, klass))
            # Additional key for names that embed their author citation. The
            # stored `name` stays the full citation; only the key is bare.
            bare = strip_authority(name, codes.get(t), rank.get(t))
            if bare:
                bare_norm = normalize_name(bare)
                if bare_norm != normalize_name(name):
                    norm_batch.append((bare_norm, t, name, klass))
                    n_bare += 1
    cur.executemany("INSERT INTO names VALUES (?,?,?,?)", kept_names)
    cur.executemany("INSERT INTO name_norm VALUES (?,?,?,?)", norm_batch)
    log(f"  kept {len(kept_names)} names, dropped {dropped} "
        f"({100*dropped/max(1,len(rows_all)):.0f}%)")
    log(f"  added {n_bare} bare-binomial keys for names carrying author citations")

    # merged / deleted
    merged: dict[int, int] = {}
    if (src / "merged.dmp").exists():
        for f in _iter_dmp(src / "merged.dmp"):
            if len(f) >= 2:
                merged[int(f[0])] = int(f[1])
        cur.executemany("INSERT OR REPLACE INTO merged VALUES (?,?)", list(merged.items()))
    n_del = 0
    if (src / "delnodes.dmp").exists():
        dbatch = [(int(f[0]),) for f in _iter_dmp(src / "delnodes.dmp") if f and f[0]]
        cur.executemany("INSERT OR REPLACE INTO deleted VALUES (?)", dbatch)
        n_del = len(dbatch)
    log(f"  {len(merged)} merges, {n_del} deleted taxids")

    # curated overlay
    overlay = overlay or (Path(__file__).resolve().parents[1] / "data" / "contested.json")
    overlay_names: dict[str, dict] = {}
    n_overlay = 0
    if overlay.exists():
        payload = json.loads(overlay.read_text())
        for entry in payload.get("entries", []):
            blob = json.dumps(entry, separators=(",", ":"))
            # Normalize first, THEN dedupe: "Candida auris" and "[Candida]
            # auris" collapse to the same key now that brackets are folded.
            for k in {normalize_name(key)
                      for key in {entry["name"], *entry.get("also_matches", [])}}:
                cur.execute("INSERT INTO overlay_notes VALUES (?,?)", (k, blob))
                overlay_names[k] = entry
            n_overlay += 1
    log(f"  {n_overlay} overlay entries")

    version = version or _release_version(src)
    meta = {
        "source": "NCBI Taxonomy", "source_url": TAXDUMP_URL,
        "source_license": "public domain (work of the US Government)",
        "version": version,
        "retrieved": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "taxdump_md5": md5 or "", "builder_version": "0.2.0", "schema_version": "5",
        "build_profile": "full" if full else "slim",
        "n_nodes": str(len(keep_taxid)), "n_names": str(len(kept_names)),
        "n_merged": str(len(merged)), "n_deleted": str(n_del), "n_overlay": str(n_overlay),
        "overlay_version": "0.1.0",
    }
    cur.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", list(meta.items()))
    conn.commit()
    cur.execute("ANALYZE")
    conn.commit()
    _finalize(conn)
    _verify_schema(conn, "nodes", {"taxid", "parent_taxid", "rank", "code"}, out)

    stats = {"stage2_bytes": out.stat().st_size}
    log(f"built {out} ({out.stat().st_size/1e6:.1f} MB), release {version}")

    # The synthetic fixture is not expected to contain every canary.
    if version != "fixture" and not version.startswith("fixture"):
        failures = check_canaries(conn, quiet)
        stats["canary_failures"] = failures
        if failures:
            raise RuntimeError(
                f"{len(failures)} canary name(s) missing from the built index -- see above. "
                "The index is silently wrong; refusing to present it as good. Re-run with "
                "--full to disable taxon filtering, or report this with the list above.")

    if stage1_out is not None:
        stats.update(build_stage1(conn, stage1_out, meta, overlay_names, fp_rate, quiet))
    if field_out is not None:
        stats.update(build_field(conn, field_out, meta, overlay_names, fp_rate, quiet))
    conn.close()
    return stats


def build_stage1(conn: sqlite3.Connection, path: Path, meta: dict,
                 overlay_names: dict[str, dict], fp_rate: float, quiet: bool) -> dict:
    """Derive the small always-installed artifact from the full index."""
    def log(m: str) -> None:
        if not quiet:
            print(f"[binomen] {m}", file=sys.stderr)

    log("building stage-1 index")
    path.parent.mkdir(parents=True, exist_ok=True)
    _clear(path)
    s1 = sqlite3.connect(path)
    s1.executescript(SCHEMA_STAGE1.read_text())

    cur = conn.cursor()
    sci = dict(cur.execute(
        "SELECT taxid, name FROM names WHERE name_class = 'scientific name'").fetchall())
    code_of = dict(cur.execute("SELECT taxid, code FROM nodes").fetchall())

    # Which taxa have any recorded alternative name at all.
    has_alt = {r[0] for r in cur.execute(
        "SELECT DISTINCT taxid FROM names WHERE name_class != 'scientific name' "
        "AND name_class != 'authority'")}
    merged_targets = {r[0] for r in cur.execute("SELECT DISTINCT new_taxid FROM merged")}

    # Group normalized names by taxid set to find homonyms in one pass.
    by_norm: dict[str, set[int]] = {}
    klass_of: dict[tuple[str, int], str] = {}
    for norm, taxid, _name, klass in cur.execute(
            "SELECT norm, taxid, name, name_class FROM name_norm"):
        by_norm.setdefault(norm, set()).add(taxid)
        klass_of[(norm, taxid)] = klass

    verdicts: list[tuple] = []
    stable_by_code: dict[str, list[str]] = {}
    for norm, taxids in by_norm.items():
        code = code_of.get(next(iter(taxids)), Code.UNDETERMINED.value)
        if norm in overlay_names and overlay_names[norm].get("contested"):
            verdicts.append((norm, "contested", code, min(taxids), None))
            continue
        if len(taxids) > 1:
            verdicts.append((norm, "homonym", code, None, None))
            continue
        t = next(iter(taxids))
        klass = klass_of.get((norm, t), "scientific name")
        accepted = sci.get(t)
        if klass != "scientific name":
            verdicts.append((norm, "superseded", code, t, accepted))
        elif t in has_alt or t in merged_targets:
            verdicts.append((norm, "has_synonyms", code, t, None))
        else:
            stable_by_code.setdefault(code, []).append(norm)

    s1.executemany("INSERT INTO verdicts VALUES (?,?,?,?,?)", verdicts)

    total_stable = sum(len(v) for v in stable_by_code.values())
    blooms = {}
    for code, names in stable_by_code.items():
        bf = BloomFilter.sized(len(names), fp_rate)
        for n in names:
            bf.add(n)
        blob = bf.dumps()
        blooms[code] = (len(names), bf)
        s1.execute("INSERT INTO bloom VALUES (?,?,?)", (code, len(names), blob))

    s1meta = dict(meta)
    s1meta.update({
        "artifact": "stage1", "n_verdicts": str(len(verdicts)),
        "n_stable": str(total_stable), "bloom_fp_rate": str(fp_rate),
    })
    s1.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", list(s1meta.items()))
    s1.commit()
    s1.execute("ANALYZE")
    s1.commit()
    _finalize(s1)
    _verify_schema(s1, "verdicts", {"norm", "verdict", "code", "taxid", "accepted"}, path)
    s1.close()

    size = path.stat().st_size
    log(f"  {len(verdicts)} exact verdicts, {total_stable} stable names in "
        f"{len(blooms)} bloom filters")
    for code, (n, bf) in sorted(blooms.items()):
        log(f"    {code:13s} {n:8d} names  {bf.nbytes/1e6:6.2f} MB  "
            f"fp={bf.false_positive_rate():.4f}")
    log(f"built {path} ({size/1e6:.1f} MB)")
    return {"stage1_bytes": size, "n_verdicts": len(verdicts), "n_stable": total_stable}


def extract_fixture(src: Path, out_dir: Path, seeds: list[str], quiet: bool = False) -> None:
    """Write a small taxdump containing REAL rows for the named taxa.

    Why this exists. Two normalization bugs shipped in a row -- NCBI's
    misplacement brackets, then embedded author citations -- and both survived
    the whole test suite, because the fixture was hand-written by the same
    person who wrote the parser. A fixture written that way tests the parser
    against its author's beliefs about the data, which is precisely the thing
    that was wrong. Real rows, even a few hundred of them, test it against the
    data.

    Pulls each seed name, its ancestors, its immediate children, and every
    name row and merge record attached to them.
    """
    wanted_names = {normalize_name(s) for s in seeds}
    parent, rank, sci = {}, {}, {}
    for f in _iter_dmp(src / "nodes.dmp"):
        if len(f) >= 3:
            parent[int(f[0])] = int(f[1])
            rank[int(f[0])] = f[2]
    rows = []
    hits: set[int] = set()
    for f in _iter_dmp(src / "names.dmp"):
        if len(f) < 4:
            continue
        t, name, uniq, klass = int(f[0]), f[1], f[2], f[3]
        rows.append((t, name, uniq, klass))
        if klass == "scientific name":
            sci[t] = name
        n = normalize_name(name)
        bare = strip_authority(name, None, rank.get(t))
        if n in wanted_names or (bare and normalize_name(bare) in wanted_names):
            hits.add(t)

    keep = set(hits)
    for t in list(hits):                       # ancestors
        cur, guard = t, 0
        while cur in parent and guard < 60:
            keep.add(cur)
            if parent[cur] == cur:
                break
            cur = parent[cur]
            guard += 1
    children = {}
    for t, p in parent.items():
        children.setdefault(p, []).append(t)
    for t in list(hits):                       # a few children, for descent tests
        keep.update(children.get(t, [])[:3])

    merges = [(int(f[0]), int(f[1])) for f in _iter_dmp(src / "merged.dmp")
              if len(f) >= 2] if (src / "merged.dmp").exists() else []
    merges = [(o, n) for o, n in merges if n in keep]

    out_dir.mkdir(parents=True, exist_ok=True)
    line = lambda parts: "\t|\t".join(str(x) for x in parts) + "\t|\n"  # noqa: E731
    # newline="" everywhere below: real NCBI dumps are LF, and text mode on
    # Windows would otherwise silently write CRLF into the test fixture.
    with open(out_dir / "nodes.dmp", "w", encoding="utf-8", newline="") as f:
        for t in sorted(keep):
            f.write(line([t, parent.get(t, t), rank.get(t, "no rank"), "", "0"]))
    with open(out_dir / "names.dmp", "w", encoding="utf-8", newline="") as f:
        for t, name, uniq, klass in rows:
            if t in keep:
                f.write(line([t, name, uniq, klass]))
    with open(out_dir / "merged.dmp", "w", encoding="utf-8", newline="") as f:
        for o, n in merges:
            f.write(line([o, n]))
    (out_dir / "delnodes.dmp").write_text("")
    # Marker: tells make_fixture.py not to overwrite this with synthetic data,
    # and tells anyone reading a test failure which release they are testing on.
    (out_dir / "PROVENANCE.txt").write_text(
        "Extracted from a real NCBI taxdump by `binomen-build-index --extract-fixture`.\n"
        f"release: {_release_version(src)}\n"
        f"taxa: {len(keep)}   direct hits: {len(hits)}   merges: {len(merges)}\n"
        f"seeds: {len(seeds)} names from tests/fixtures/seeds.txt\n"
        "\nThese are real archive rows, not hand-written. Do not replace them with\n"
        "synthetic data -- three normalization bugs shipped because the fixture was\n"
        "written from assumptions about NCBI rather than from NCBI.\n"
        "Regenerate with the same command against a current taxdump.\n")
    if not quiet:
        print(f"[binomen] extracted {len(keep)} taxa ({len(hits)} direct hits), "
              f"{len(merges)} merges to {out_dir}", file=sys.stderr)
        # Provenance is not enough. A fixture of real rows that happens to
        # contain none of the awkward shapes lets the tests that care about
        # them skip, and a suite that skips is a suite that proves nothing --
        # which is how we got here. Report coverage of each shape the tests
        # reason about, and say so when one is missing.
        kept_rows = [(t, n, u, k) for t, n, u, k in rows if t in keep]
        by_norm: dict[str, set[int]] = {}
        for t, n, _u, k in kept_rows:
            if k != "authority":
                by_norm.setdefault(normalize_name(n), set()).add(t)
        shapes = {
            "bracketed (misplaced genus)": sum(1 for _t, n, _u, _k in kept_rows if is_bracketed(n)),
            "embedded author citation": sum(
                1 for t, n, _u, _k in kept_rows if strip_authority(n, None, rank.get(t))),
            "homonyms (one string, many taxa)": sum(1 for v in by_norm.values() if len(v) > 1),
            "merge records": len(merges),
            "non-scientific name classes": len({k for _t, _n, _u, k in kept_rows}) - 1,
        }
        print("[binomen] fixture coverage:", file=sys.stderr)
        for label, n in shapes.items():
            flag = "  " if n else "  MISSING -> "
            print(f"[binomen]   {flag}{label}: {n}", file=sys.stderr)
        empty = [k for k, v in shapes.items() if not v]
        if empty:
            print(f"[binomen] WARNING: no examples of {empty} in this extraction. Tests that "
                  f"depend on them will SKIP, which looks like passing. Add a seed name that "
                  f"exhibits the shape to tests/fixtures/seeds.txt.", file=sys.stderr)
        missing = [s for s in seeds if normalize_name(s) not in
                   {normalize_name(n) for t, n, _u, _k in rows if t in hits}
                   and not any(strip_authority(n) and normalize_name(strip_authority(n)) ==
                               normalize_name(s) for t, n, _u, _k in rows if t in hits)]
        if missing:
            print(f"[binomen] WARNING: no rows found for {missing}", file=sys.stderr)


def merged_preview(src: Path) -> set[int]:
    """Taxids on either side of a merge, read before the main pass.

    Needed early because a merge is nomenclatural history and therefore
    protects a taxon from the junk filter.
    """
    out: set[int] = set()
    p = src / "merged.dmp"
    if p.exists():
        for f in _iter_dmp(p):
            if len(f) >= 2:
                out.add(int(f[0]))
                out.add(int(f[1]))
    return out


def _clear(path: Path) -> None:
    """Delete the database AND its journal sidecars.

    Deleting a .sqlite while leaving a stale -wal beside it is a real hazard:
    SQLite will happily recover the orphaned journal into the new file, which
    can resurrect an older schema into what looks like a fresh build. Cheap to
    prevent, extremely confusing to diagnose.
    """
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(str(path) + suffix)
        if not p.exists():
            continue
        try:
            p.unlink()
        except PermissionError as e:
            # Windows locks open files. The usual culprit is the MCP server
            # itself: a running Claude Desktop or Claude Code holds the index
            # open, and rebuilding underneath it would be a bad idea anyway.
            raise SystemExit(
                f"\n{p} is locked by another process, so it cannot be replaced.\n"
                f"\nAlmost always this is a running client holding the index open:\n"
                f"  * Claude Desktop -- quit it completely from the system tray, not just\n"
                f"    the window. Config and databases are held for the life of the process.\n"
                f"  * Claude Code -- exit any running session.\n"
                f"  * A python -m binomen.server left running in another terminal.\n"
                f"\nThen re-run. (OS said: {e.strerror})\n"
            ) from e


def _finalize(conn: sqlite3.Connection) -> None:
    """Checkpoint and leave the artifact as one self-contained file."""
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.commit()


def _verify_schema(conn: sqlite3.Connection, table: str, expected: set[str], path: Path) -> None:
    """Fail the build rather than shipping an index that reads wrong later.

    Both `nodes` schemas past and present have four columns, so an insert
    against the wrong one succeeds silently and the mismatch only surfaces at
    query time, far from the cause. Check it here, where the message can say
    what happened.
    """
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if not cols:
        raise RuntimeError(f"{path}: table '{table}' was not created. The build did not work.")
    missing = expected - cols
    if missing:
        raise RuntimeError(
            f"{path}: table '{table}' is missing column(s) {sorted(missing)}; found {sorted(cols)}. "
            f"This usually means an older database or journal file was recovered into the new "
            f"build. Delete {path} and its -wal/-shm sidecars and rebuild.")


# Names that must resolve in any correct build of the real taxdump. If one of
# these comes back missing the index is silently wrong, and silently wrong is
# the failure mode this whole project exists to catch -- so it fails the build.
# Names that legitimately denote more than one taxon.
GENUINE_HOMONYMS = {"Bacillus", "Prunella", "Oenanthe", "Morus", "Ficus", "Aotus"}

CANARIES = [
    ("Escherichia coli", "scientific name"),
    ("Homo sapiens", "scientific name"),
    ("Clostridioides difficile", "scientific name"),
    ("Clostridium difficile", "synonym"),
    ("Klebsiella aerogenes", "scientific name"),
    ("Enterobacter aerogenes", "synonym"),
    ("Candida auris", "any"),
    ("Saccharomyces cerevisiae", "scientific name"),
    ("Bacillus subtilis", "scientific name"),
]


def check_canaries(conn: sqlite3.Connection, quiet: bool = False) -> list[str]:
    """Probe known names: present, AND resolving to one taxon.

    The presence-only version of this check reported 9/9 on an index in which
    every species had been turned into a hundreds-way homonym by its own
    strains. Being findable is not the same as being right, which is more or
    less the thesis of this project, so the canary now checks both.
    """
    failures = []
    for name, expected_class in CANARIES:
        norm = normalize_name(name)
        rows = conn.execute(
            "SELECT taxid, name_class FROM name_norm WHERE norm = ?", (norm,)).fetchall()
        if rows:
            if expected_class != "any" and not any(r[1] == expected_class for r in rows):
                failures.append(f"{name}: present but as {sorted({r[1] for r in rows})}, "
                                f"expected '{expected_class}'")
            taxids = {r[0] for r in rows}
            if len(taxids) > 1 and name not in GENUINE_HOMONYMS:
                sample = conn.execute(
                    "SELECT name FROM names WHERE taxid IN "
                    f"({','.join('?' * min(3, len(taxids)))}) "
                    "AND name_class='scientific name' LIMIT 3",
                    tuple(sorted(taxids)[:3])).fetchall()
                failures.append(
                    f"{name}: FALSE HOMONYM -- resolves to {len(taxids)} distinct taxa, e.g. "
                    + ", ".join(repr(r[0]) for r in sample)
                    + ". A lookup key is being shared by taxa that are not the same organism.")
            continue
        # Explain the absence by looking, not by guessing. The first version of
        # this said "not present in the source archive at all" without ever
        # checking the archive -- a confident unverified assertion, in the tool
        # whose entire subject is confident unverified assertions.
        epithet = name.split()[-1]
        near = conn.execute(
            "SELECT name, name_class FROM names WHERE name LIKE ? LIMIT 5",
            (f"%{epithet}%",)).fetchall()
        if near:
            why = ("absent under this exact string, but the index contains: "
                   + ", ".join(f"{n!r} ({k})" for n, k in near)
                   + " -- this is a normalization gap, not missing data")
        elif JUNK_NAME_RE.search(name):
            why = f"dropped by the junk filter (matched {JUNK_NAME_RE.search(name).group(0)!r})"
        else:
            why = "no name containing this epithet is present in the built index"
        failures.append(f"{name}: MISSING -- {why}")
    if failures and not quiet:
        print("[binomen] CANARY FAILURES:", file=sys.stderr)
        for f in failures:
            print(f"[binomen]   {f}", file=sys.stderr)
    elif not quiet:
        print(f"[binomen]   canaries: {len(CANARIES)}/{len(CANARIES)} present", file=sys.stderr)
    return failures


def build_field(conn: sqlite3.Connection, path: Path, meta: dict,
                overlay_names: dict[str, dict], fp_rate: float, quiet: bool) -> dict:
    """Derive the field edition: one shippable file with the answers, not the backbone.

    Everything here comes from the already-built stage-2 index, so this adds no
    parsing and cannot disagree with the full build.
    """
    def log(m: str) -> None:
        if not quiet:
            print(f"[binomen] {m}", file=sys.stderr)

    log("building field-edition index")
    path.parent.mkdir(parents=True, exist_ok=True)
    _clear(path)
    fx = sqlite3.connect(path)
    fx.executescript(SCHEMA_FIELD.read_text())
    cur = conn.cursor()

    sci = dict(cur.execute(
        "SELECT taxid, name FROM names WHERE name_class = 'scientific name'").fetchall())
    node = {t: (r, c) for t, r, c in cur.execute("SELECT taxid, rank, code FROM nodes")}

    # Formal ranks only. The full index deliberately keeps strain-level taxa
    # when they carry synonyms -- a name-shape heuristic must not override
    # evidence of nomenclatural history. For the field edition that rule is
    # wrong: NCBI holds well over a million strains, every one of them inherits
    # its species' rename ("Clostridium difficile 630" -> "Clostridioides
    # difficile 630"), and no bench biologist has ever asked whether a strain
    # designation is still current. Dropping them is most of what makes this
    # file shippable.
    in_scope = {t for t, (rank, _c) in node.items() if rank in FIELD_RANKS}
    log(f"  {len(in_scope)} taxa at formal ranks, of {len(node)} "
        f"({100*(len(node)-len(in_scope))/max(1,len(node)):.0f}% dropped as strain-level)")
    authority = dict(cur.execute(
        "SELECT taxid, name FROM names WHERE name_class = 'authority'").fetchall())
    merged_targets = {r[0] for r in cur.execute("SELECT DISTINCT new_taxid FROM merged")}

    # Alternative names per taxon, bare and searchable. The stored strings can
    # be full nomenclatural citations ("Clostridium difficile (Hall and O'Toole
    # 1935) Prevot 1938 (Approved Lists 1980)"); a citation pasted into a
    # literature search matches nothing, so the searchable form is what ships.
    alt: dict[int, set[str]] = {}
    for taxid, name, klass in cur.execute(
            "SELECT taxid, name, name_class FROM names "
            "WHERE name_class NOT IN ('authority','scientific name')"):
        if taxid not in in_scope:
            continue
        if klass == "includes" or klass == "in-part":
            continue          # unidentified material filed under a taxon, not a name for it
        rank = node.get(taxid, (None, None))[0]
        bare = strip_authority(name, node.get(taxid, (None, None))[1], rank) or name
        bare = bare.replace("[", "").replace("]", "").strip()
        if bare and bare != sci.get(taxid):
            alt.setdefault(taxid, set()).add(bare)

    by_norm: dict[str, set[int]] = {}
    klass_of: dict[tuple[str, int], str] = {}
    for norm, taxid, _name, klass in cur.execute(
            "SELECT norm, taxid, name, name_class FROM name_norm"):
        if taxid not in in_scope:
            continue
        by_norm.setdefault(norm, set()).add(taxid)
        klass_of[(norm, taxid)] = klass

    lookup_rows, needed, stable_by_code = [], set(), {}
    for norm, taxids in by_norm.items():
        first = next(iter(taxids))
        code = node.get(first, (None, Code.UNDETERMINED.value))[1]
        if norm in overlay_names and overlay_names[norm].get("contested"):
            lookup_rows.append((norm, min(taxids), "contested", code))
            needed |= taxids
            continue
        if len(taxids) > 1:
            for t in taxids:
                lookup_rows.append((norm, t, "homonym", node.get(t, (None, code))[1]))
            needed |= taxids
            continue
        klass = klass_of.get((norm, first), "scientific name")
        if klass != "scientific name":
            lookup_rows.append((norm, first, "superseded", code))
            needed.add(first)
        elif first in alt or first in merged_targets:
            lookup_rows.append((norm, first, "has_synonyms", code))
            needed.add(first)
        else:
            stable_by_code.setdefault(code, []).append(norm)

    fx.executemany("INSERT INTO lookup VALUES (?,?,?,?)", lookup_rows)
    fx.executemany("INSERT INTO taxa VALUES (?,?,?,?,?,?)", [
        (t, sci.get(t, ""), node.get(t, (None, None))[0],
         node.get(t, (None, Code.UNDETERMINED.value))[1],
         authority.get(t),
         json.dumps(sorted(alt.get(t, ())), separators=(",", ":")) if alt.get(t) else None)
        for t in sorted(needed) if sci.get(t)])

    total_stable = 0
    for code, names in stable_by_code.items():
        bf = BloomFilter.sized(len(names), fp_rate)
        for n in names:
            bf.add(n)
        fx.execute("INSERT INTO bloom VALUES (?,?,?)", (code, len(names), bf.dumps()))
        total_stable += len(names)

    n_notes = 0
    for norm, entry in overlay_names.items():
        fx.execute("INSERT INTO notes VALUES (?,?)",
                   (norm, json.dumps(entry, separators=(",", ":"))))
        n_notes += 1

    fmeta = dict(meta)
    fmeta.update({"artifact": "field",
                  "scope": "formal ranks only (species and above, plus infraspecific); "
                           "strain- and isolate-level taxa are not included",
                  "n_lookup": str(len(lookup_rows)),
                  "n_taxa": str(len(needed)), "n_stable": str(total_stable),
                  "n_notes": str(n_notes), "bloom_fp_rate": str(fp_rate)})
    fx.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", list(fmeta.items()))
    fx.commit()
    fx.execute("ANALYZE")
    fx.commit()
    _finalize(fx)
    _verify_schema(fx, "lookup", {"norm", "taxid", "verdict", "code"}, path)
    _verify_schema(fx, "taxa", {"taxid", "accepted", "rank", "code", "authority", "synonyms"}, path)
    fx.close()

    size = path.stat().st_size
    log(f"  {len(lookup_rows)} lookup rows, {len(needed)} taxa, {total_stable} stable names, "
        f"{n_notes} curated notes")
    log(f"built {path} ({size/1e6:.1f} MB)")
    return {"field_bytes": size, "n_lookup": len(lookup_rows), "n_taxa": len(needed)}


def _release_version(src: Path) -> str:
    """taxdump carries no version string; nodes.dmp mtime is set by the dump."""
    nodes = src / "nodes.dmp"
    ts = nodes.stat().st_mtime if nodes.exists() else time.time()
    return "taxdump-" + datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the binomen indexes.")
    ap.add_argument("--taxdump", type=Path, help="local taxdump.tar.gz instead of downloading")
    ap.add_argument("--fixture", type=Path, help="directory of extracted .dmp files")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="full stage-2 index")
    ap.add_argument("--stage1-out", type=Path, default=DEFAULT_STAGE1)
    ap.add_argument("--no-stage1", action="store_true")
    ap.add_argument("--field-out", type=Path, default=DEFAULT_FIELD,
                    help="field-edition index: one shippable file for the .mcpb extension")
    ap.add_argument("--no-field", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="keep strain/environmental taxa and all name classes")
    ap.add_argument("--audit", action="store_true",
                    help="report what is in the archive and exit without building")
    ap.add_argument("--extract-fixture", type=Path, metavar="DIR",
                    help="write a small taxdump of REAL rows for the seed taxa and exit")
    ap.add_argument("--seeds", type=Path,
                    help="newline-delimited names for --extract-fixture "
                         "(default: tests/fixtures/seeds.txt)")
    ap.add_argument("--fp-rate", type=float, default=0.001)
    ap.add_argument("--version")
    ap.add_argument("--overlay", type=Path)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    if a.fixture:
        if a.audit:
            audit(a.fixture)
            return 0
        build(a.fixture, a.out, None if a.no_stage1 else a.stage1_out,
              None if a.no_field else a.field_out,
              version=a.version or "fixture", overlay=a.overlay, full=a.full,
              fp_rate=a.fp_rate, quiet=a.quiet)
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="binomen-taxdump-"))
    try:
        archive, md5 = a.taxdump, None
        if archive is None:
            archive = _download(TAXDUMP_URL, tmp / "taxdump.tar.gz", a.quiet)
            md5 = _verify_md5(archive, a.quiet)
        if not a.quiet:
            print(f"[binomen] extracting {archive}", file=sys.stderr)
        with tarfile.open(archive, "r:gz") as tf:
            members = [m for m in tf.getmembers() if m.name.endswith(".dmp")]
            try:
                tf.extractall(tmp, members=members, filter="data")  # type: ignore[call-arg]
            except TypeError:
                tf.extractall(tmp, members=members)  # noqa: S202
        src = tmp
        if not (src / "nodes.dmp").exists():
            found = next(src.rglob("nodes.dmp"), None)
            if found is None:
                raise SystemExit("nodes.dmp not found in archive")
            src = found.parent
        if a.audit:
            audit(src)
            return 0
        if a.extract_fixture:
            seeds_path = a.seeds or (Path.cwd() / "tests" / "fixtures" / "seeds.txt")
            seeds = [ln.strip() for ln in seeds_path.read_text().splitlines()
                     if ln.strip() and not ln.startswith("#")]
            extract_fixture(src, a.extract_fixture, seeds, a.quiet)
            return 0
        build(src, a.out, None if a.no_stage1 else a.stage1_out,
              None if a.no_field else a.field_out, version=a.version, md5=md5,
              overlay=a.overlay, full=a.full, fp_rate=a.fp_rate, quiet=a.quiet)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
