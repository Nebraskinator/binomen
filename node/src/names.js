"use strict";
/*
 * Name normalization and Bloom membership.
 *
 * This file has a twin: src/binomen/build/build_index.py and
 * src/binomen/bloom.py. They must agree exactly, because Python writes the
 * index and this reads it. A one-character difference in normalization means a
 * name resolves in one implementation and not the other, and the failure
 * presents as "no record of this name" -- which reads as a legitimate negative
 * result and is the precise failure mode this whole project exists to detect.
 *
 * So neither side is written from the other's description. Python emits
 * test/conformance.json and this is tested against it. If you change anything
 * here, regenerate that file and run `node --test`.
 */

const crypto = require("node:crypto");

/**
 * Fold a name to a lookup key.
 *
 * Mirrors normalize_name() in build_index.py, step for step:
 *   NFKD, drop combining marks, fold the hybrid sign, drop apostrophes,
 *   drop NCBI's misplacement brackets, lowercase, collapse whitespace.
 *
 * Conservative on purpose -- subspecific rank markers are NOT folded, because
 * "Escherichia coli" and "Escherichia coli subsp. coli" are different taxa and
 * merging their keys would manufacture exactly the false conflation this
 * package is built to detect.
 */
function normalizeName(name) {
  if (typeof name !== "string") return "";
  let s = name.normalize("NFKD");
  // Python drops characters with a nonzero canonical combining class. \p{M}
  // is very slightly broader (it also covers marks with class 0), which is
  // immaterial for biological names; the conformance fixture is the arbiter.
  s = s.replace(/\p{M}/gu, "");
  s = s.replace(/×/g, "x ").replace(/['’]/g, "");
  s = s.replace(/[[\]()]/g, "");
  return s.toLowerCase().trim().split(/\s+/).join(" ");
}

/** Did the source flag this name's generic placement as wrong? */
function isBracketed(name) {
  return name.includes("[") || name.includes("]");
}

const RANK_MARKERS = new Set([
  "subsp.", "ssp.", "var.", "subvar.", "f.", "forma", "subf.",
  "cv.", "x", "×", "nothosubsp.",
]);
const STRAIN_MARKERS = new Set([
  "str.", "strain", "serovar", "serotype", "serogroup", "biovar", "biotype",
  "pathovar", "pv.", "isolate", "clone", "genomovar", "genomosp.", "substr.",
  "type", "group", "subgroup", "morphovar", "phagovar", "chemoform",
]);
const YEAR = /^\(?(1[6-9]\d{2}|20[0-2]\d)\)?[,.);]?$/;
const AUTHORITY_RANKS = new Set([
  "species", "subspecies", "genus", "subgenus", "family", "subfamily", "tribe",
  "subtribe", "order", "suborder", "infraorder", "superfamily", "class",
  "subclass", "infraclass", "superorder", "phylum", "subphylum", "kingdom",
  "subkingdom", "superkingdom", "domain", "varietas", "forma", "section",
  "subsection", "series", "species group", "species subgroup", "superclass",
]);

function binomialEnd(toks) {
  // Index just past the binomial (plus infraspecific parts), or -1.
  if (toks.length < 3 || !/^\p{Lu}/u.test(toks[0])) return -1;
  let start = 1;
  if (toks[0] === "Candidatus" && toks.length > 2) start = 2;
  if (start >= toks.length || !/^\p{Ll}/u.test(toks[start])) return -1;
  if ((toks[start] === "x" || toks[start] === "×") &&
      start + 1 < toks.length && /^\p{Ll}/u.test(toks[start + 1])) start += 1;
  let end = start + 1;
  while (end + 1 < toks.length && RANK_MARKERS.has(toks[end]) &&
         /^\p{Ll}/u.test(toks[end + 1])) end += 2;
  return end >= toks.length ? -1 : end;
}

/** Bare binomial when a name row carries its author citation, else null. */
function stripAuthority(name, code = null, rank = null) {
  if (code === "ICTV") return null;
  if (rank !== null && !AUTHORITY_RANKS.has(rank)) return null;
  const toks = name.split(/\s+/).filter(Boolean);
  const end = binomialEnd(toks);
  if (end < 0) return null;
  const tail = toks.slice(end);
  if (tail.some((t) => STRAIN_MARKERS.has(t.toLowerCase()))) return null;
  const hasParen = tail.some((t) => t.startsWith("("));
  const hasYear = tail.some((t) => YEAR.test(t));
  const abbreviated = tail.length === 1 && /^\p{Lu}/u.test(tail[0]) &&
    tail[0].endsWith(".") && tail[0].length <= 12;
  if (!hasParen && !hasYear && !abbreviated) return null;
  return toks.slice(0, end).join(" ");
}

/**
 * Split "Clostridioides difficile 630" into ["Clostridioides difficile", "630"].
 *
 * A strain name is a binomial plus a laboratory designation. Only the binomial
 * is governed by a code; the designation never changes. So a strain inherits
 * its species' transfer, and that is derivable rather than storable -- which is
 * why over a million strain taxa are absent from the shipped index without any
 * loss of coverage.
 */
function splitDesignation(name, code = null) {
  if (code === "ICTV") return null;
  const toks = name.split(/\s+/).filter(Boolean);
  const end = binomialEnd(toks);
  if (end < 0) return null;
  // An author citation is not a designation: "Homo sapiens Linnaeus, 1758"
  // must not yield strain "Linnaeus, 1758".
  if (stripAuthority(name, code) !== null) return null;
  return [toks.slice(0, end).join(" "), toks.slice(end).join(" ")];
}

/**
 * Bloom filter, read-only, matching src/binomen/bloom.py.
 *
 * No false negatives: absence from the filter is certain. That is the property
 * the whole hybrid index design rests on -- names with real nomenclatural
 * history live in an exact table, and the filters only ever certify the
 * *absence* of history.
 */
class BloomFilter {
  constructor(m, k, bits, n) {
    this.m = m; this.k = k; this.bits = bits; this.n = n;
  }

  static load(blob) {
    const magic = blob.subarray(0, 4).toString("latin1");
    if (magic === "BLM1") {
      throw new Error(
        "index was built with the old BLAKE2b filter (BLM1); rebuild it with a current " +
        "binomen-build-index");
    }
    if (magic !== "BLM2") throw new Error("not a binomen bloom filter");
    return new BloomFilter(
      blob.readUInt32LE(4), blob.readUInt32LE(8),
      blob.subarray(16), blob.readUInt32LE(12));
  }

  *positions(s) {
    // SHA-256 truncated to 16 bytes, read as two little-endian 64-bit halves.
    // Kirsch-Mitzenmacher double hashing: k indices from one digest.
    const d = crypto.createHash("sha256").update(s, "utf8").digest();
    const h1 = d.readBigUInt64LE(0);
    const h2 = d.readBigUInt64LE(8) | 1n;       // keep the stride odd
    const m = BigInt(this.m);
    for (let i = 0; i < this.k; i++) {
      // Deliberately NOT masked to 64 bits. Python's integers are arbitrary
      // precision, so `h1 + i*h2` there never wraps; masking here made every
      // index past the first differ, and membership failed for names that were
      // definitely present. A Bloom filter must not produce false negatives --
      // the entire hybrid index design rests on absence being certain.
      yield Number((h1 + BigInt(i) * h2) % m);
    }
  }

  has(s) {
    for (const pos of this.positions(s)) {
      if ((this.bits[pos >> 3] & (1 << (pos & 7))) === 0) return false;
    }
    return true;
  }
}

module.exports = {
  normalizeName, isBracketed, stripAuthority, splitDesignation, BloomFilter,
};
