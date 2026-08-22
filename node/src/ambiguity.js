"use strict";
/*
 * The bundled backbone: `ambiguity.sqlite`, shipped inside the extension.
 *
 * Built by src/binomen/build/build_ambiguity.py. This is the half derived from
 * NCBI Taxonomy (public domain); the registers live in their own file for
 * licence reasons -- see docs/adr/0002-two-files-for-licence-containment.md.
 *
 * Why it exists at all: the fetched stage-2 index is 123 MB and arrives after
 * install, which is a step a bench biologist has to complete before the tool
 * works. This file is small enough to ride inside the .mcpb, so the common
 * install has no download step.
 *
 * It holds only names that carry an ambiguity, so presence IS the finding -- see
 * docs/adr/0001-ambiguity-only-local-database.md. That makes one rule
 * load-bearing: absence must stay readable. The Bloom filters come across from
 * stage 1 for exactly that, so a name in none of them is reported as unknown
 * rather than as clean. A misspelling read as an all-clear is worse than an
 * unfamiliar wrong name.
 *
 * This module is an ADAPTER, not a second resolver. It answers the same four
 * questions the fetched index answers -- lookup, taxon, note, expand -- so
 * Resolver runs unchanged over either. Two implementations of the tool logic is
 * exactly the drift ADR-0003 exists to stop.
 */
const fs = require("node:fs");
const path = require("node:path");
const { DatabaseSync } = require("node:sqlite");

const SEP = "\x1f";

/* Bundled layout first, repo checkout second. cwd is C:\WINDOWS\system32 when
 * Claude Desktop launches an extension, so nothing here may be relative. */
function candidatePaths(file) {
  // An explicit override is authoritative, with no fallback -- see the same
  // note in registers.js. Falling through would let a caller who named one file
  // silently be served another.
  if (process.env.BINOMEN_AMBIGUITY_DB) return [process.env.BINOMEN_AMBIGUITY_DB];
  return [
    path.join(__dirname, "..", "data", file),       // bundled inside the extension
    path.join(__dirname, "..", "..", "data", file), // repo checkout
  ];
}

function locate(file = "ambiguity.sqlite") {
  return candidatePaths(file).find((p) => {
    try { return fs.existsSync(p); } catch { return false; }
  }) || null;
}

class AmbiguityStore {
  constructor(file) {
    const found = file || locate();
    if (!found) throw new Error("no bundled ambiguity database");
    this.db = new DatabaseSync(found, { readOnly: true });
    this.path = found;

    this.meta = {};
    for (const r of this.db.prepare("SELECT key, value FROM meta").all()) {
      this.meta[r.key] = r.value;
    }
    // Codes and verdicts are stored as integers -- four verdict values and six
    // codes were being written as strings on every one of a million rows. The
    // strings live once, here.
    this.vocab = { code: [], verdict: [] };
    for (const r of this.db.prepare("SELECT kind, id, value FROM vocab").all()) {
      (this.vocab[r.kind] ||= [])[r.id] = r.value;
    }

    this._stmt = {
      amb: this.db.prepare(
        "SELECT norm, code, verdict, cluster, accepted FROM amb WHERE norm = ?"),
      cluster: this.db.prepare("SELECT names FROM cluster WHERE id = ?"),
      expand: this.db.prepare(
        "SELECT DISTINCT norm FROM amb WHERE norm LIKE ? ESCAPE '\\' "
        + "ORDER BY norm LIMIT 40"),
    };
    this._clusterCode = new Map();
  }

  get release() {
    return this.meta["ncbi.version"] || this.meta["ncbi.taxdump"] ||
      this.meta.built_at || "unknown";
  }

  /* Does the bundle promise offline enumeration? The build records when it
   * could not pack clusters, so get_synonyms can say "fetch the full index"
   * rather than quietly returning a shorter list. */
  get hasEnumeration() { return this.meta.enumeration !== "absent"; }

  /* Shipped inside the extension rather than fetched. The server uses this
   * to skip the update check: staging a 46 MB download over a bundled
   * install would undo the one property this design exists for. */
  get bundled() { return true; }

  // -- the four questions the fetched index also answers -------------------

  lookupRows(norm) {
    return this._stmt.amb.all(norm).map((r) => {
      const code = this.vocab.code[r.code] || "undetermined";
      this._clusterCode.set(r.cluster, code);
      return {
        norm: r.norm,
        taxid: r.cluster,
        verdict: this.vocab.verdict[r.verdict] || "has_synonyms",
        code,
        accepted: r.accepted || null,
      };
    });
  }

  /* A cluster, shaped like the taxa row the fetched index returns.
   *
   * The packed name list is ordered scientific-name-first by the build, which is
   * how the accepted name is recovered without a second column. A cluster of one
   * is not packed at all -- there are no alternatives to enumerate -- so the
   * accepted name then comes from the row that pointed here.
   */
  taxonRow(cluster, fallbackAccepted = null) {
    const row = this._stmt.cluster.get(cluster);
    const code = this._clusterCode.get(cluster) || null;
    if (!row) {
      return fallbackAccepted
        ? { taxid: cluster, accepted: fallbackAccepted, rank: null, code,
            authority: null, synonyms: "[]" }
        : null;
    }
    const names = String(row.names).split(SEP).filter(Boolean);
    return {
      taxid: cluster,
      accepted: names[0],
      rank: null,          // ranks are not shipped; stage 2 carries them
      code,
      authority: null,     // author citations are not names, and cost 11 MB
      synonyms: JSON.stringify(names.slice(1)),
    };
  }

  /* The contested overlay is not in the bundle. Returning null is honest: it
   * means "no dispute recorded here", which is what the fetched index says for
   * all but a few dozen names anyway. */
  noteRow() { return null; }

  expandRows(pattern) { return this._stmt.expand.all(pattern); }

  bloomRows() {
    return this.db.prepare("SELECT code, blob FROM bloom").all();
  }

  close() {
    try { this.db.close(); } catch { /* already closed */ }
  }
}

module.exports = { AmbiguityStore, locate, SEP };
