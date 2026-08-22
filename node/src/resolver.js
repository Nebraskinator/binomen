"use strict";
/*
 * Field-edition resolver.
 *
 * The same staged contract as the Python server, reduced to what fits in one
 * shippable file:
 *
 *   check_name     ~2 ms, ~25 tokens, call it on every organism mention
 *   resolve_name   accepted name, authority, prior names, provenance
 *   get_synonyms   every name recorded for the taxon
 *   expand_query   the search terms that span a naming change
 *
 * Not here: lineage, reclassification listings, live authority queries. Those
 * are stage-2 and stage-3 questions and they are what makes the full index
 * 525 MB. The Python server has them.
 *
 * Rules carried over unchanged, because they are the point of the project:
 * nothing is generated, disagreement is never collapsed to one answer, and
 * absence of a record is reported as absence rather than filled in.
 */

let DatabaseSync;
try {
  ({ DatabaseSync } = require("node:sqlite"));
} catch (e) {
  // node:sqlite exists from Node 22.5 but is behind --experimental-sqlite
  // until it was unflagged. Claude Desktop 1.28929.0 bundles Node 24, where it
  // is available. An older host should say so plainly rather than emit a
  // module-loader stack trace that looks like a broken extension.
  throw new Error(
    `binomen needs Node 24 or newer for its built-in SQLite support; this host is ` +
    `running ${process.version}. Update Claude Desktop. (${e.code || e.message})`);
}
const {
  normalizeName, splitDesignation, splitAbbreviation, BloomFilter,
} = require("./names.js");

const ABBREV_COVERAGE =
  "Expansions cover names with recorded nomenclatural history. A binomial that has never "
  + "moved is certified by a filter that cannot be enumerated, so it would not appear here "
  + "-- absence from this list is not evidence of absence from the index.";

/**
 * Restore display form from a folded lookup key.
 *
 * Only the first letter: a binomial is a capitalised genus and a lowercase
 * epithet, so a title-case helper would corrupt every one of them.
 */
function capitaliseGenus(norm) {
  return norm ? norm[0].toUpperCase() + norm.slice(1) : norm;
}

const REASON = {
  superseded: "recorded as a synonym; the accepted name differs",
  has_synonyms: "accepted, but other names exist for this taxon",
  homonym: "this string denotes more than one taxon",
  contested: "authorities disagree about the accepted name",
  unknown: "no record of this name in the indexed release",
};

/* The fetched stage-2 index, as a store.
 *
 * Kept in this file rather than beside AmbiguityStore because it is the shape
 * everything else was written against: the adapter came second, and this is what
 * it had to match.
 */
class FieldStore {
  constructor(indexFile) {
    this.db = new DatabaseSync(indexFile, { readOnly: true });
    this.meta = {};
    for (const r of this.db.prepare("SELECT key, value FROM meta").all()) {
      this.meta[r.key] = r.value;
    }
    this._stmt = {
      lookup: this.db.prepare(
        "SELECT norm, taxid, verdict, code FROM lookup WHERE norm = ?"),
      taxon: this.db.prepare(
        "SELECT taxid, accepted, rank, code, authority, synonyms FROM taxa WHERE taxid = ?"),
      note: this.db.prepare("SELECT payload FROM notes WHERE norm = ?"),
      // `norm` is indexed, so this is a range scan over one initial rather
      // than a table scan -- about 40 ms warm against ~900k rows, which is the
      // only reason it can sit inside the cheap stage-1 call at all.
      expand: this.db.prepare(
        "SELECT DISTINCT norm FROM lookup WHERE norm LIKE ? ESCAPE '\\' "
        + "ORDER BY norm LIMIT 40"),
    };
  }

  get release() { return this.meta.version || "unknown"; }
  get hasEnumeration() { return true; }
  get bundled() { return false; }

  lookupRows(norm) { return this._stmt.lookup.all(norm); }
  taxonRow(taxid) { return this._stmt.taxon.get(taxid) || null; }
  noteRow(norm) { return this._stmt.note.get(norm) || null; }
  expandRows(pattern) { return this._stmt.expand.all(pattern); }
  bloomRows() { return this.db.prepare("SELECT code, blob FROM bloom").all(); }
  close() { this.db.close(); }
}

class Resolver {
  /* Takes a path to the fetched index, or any store that answers the same four
   * questions. Two backends exist -- the fetched stage-2 index and the bundled
   * ambiguity database -- and the tool logic below is written once for both,
   * because two copies of it is the drift ADR-0003 exists to prevent. */
  constructor(index) {
    this.store = typeof index === "string" ? new FieldStore(index) : index;
    this.meta = this.store.meta || {};
    this._blooms = null;
  }

  get release() { return this.store.release || this.meta.version || "unknown"; }

  get blooms() {
    if (!this._blooms) {
      this._blooms = new Map();
      for (const r of this.store.bloomRows()) {
        this._blooms.set(r.code, BloomFilter.load(Buffer.from(r.blob)));
      }
    }
    return this._blooms;
  }

  /* One place where a name becomes rows.
   *
   * The bundled store carries the accepted display name on the row rather than
   * in a taxa table, so it is stashed here for taxonRow to fall back on: a
   * cluster with a single name is not packed at all, and without this the
   * accepted form would come back lowercased.
   */
  _lookup(norm) {
    const rows = this.store.lookupRows(norm);
    this._acceptedHint = rows.length ? rows[0].accepted || null : null;
    return rows;
  }

  codesMatching(name) {
    const n = normalizeName(name);
    const out = [];
    for (const [code, bf] of this.blooms) if (bf.has(n)) out.push(code);
    return out;
  }

  taxon(taxid) {
    const t = this.store.taxonRow(taxid, this._acceptedHint);
    if (!t) return null;
    t.synonyms = t.synonyms ? JSON.parse(t.synonyms) : [];
    return t;
  }

  note(name) {
    const r = this.store.noteRow(normalizeName(name));
    return r ? JSON.parse(r.payload) : null;
  }

  provenance() {
    return {
      source: this.meta.source || "NCBI Taxonomy",
      version: this.release,
      retrieved: this.meta.retrieved,
      license: this.meta.source_license,
      edition: "field",
    };
  }

  get registers() {
    if (this._registers === undefined) {
      const { Registers } = require("./registers.js");
      this._registers = new Registers();
    }
    return this._registers;
  }

  /* Every answer that names a taxon carries what the registers say about it.
   *
   * It used to be attached here at one call site, so a model that went straight
   * to resolve_name was told NCBI's answer and never learned that the ICNP
   * register recommends a different name for medical use. Consulting the
   * registers is part of what a resolved name IS, not a decoration one entry
   * point remembers to add.
   *
   * A failure degrades to the backbone's answer rather than to no answer: a
   * broken register file must never take a lookup down with it. */
  _withRegisters(out, name, detail = null) {
    try {
      return detail
        ? this.registers.annotateDetailed(out, name, detail)
        : this.registers.annotateTerse(out, name);
    } catch {
      return out;
    }
  }

  // ------------------------------------------------------------- stage 1
  /* Stage 1, then the registers' second opinion. Wrapping rather than editing
   * the body keeps the rule enforceable: annotation only ADDS keys. */
  checkName(name) {
    return this._withRegisters(this._checkNameNcbi(name), String(name || "").trim());
  }

  _checkNameNcbi(name) {
    const q = String(name || "").trim();
    if (!q) return { name: q, verdict: "unknown", escalate: false, reason: "empty query" };

    const rows = this._lookup(normalizeName(q));
    if (rows.length) return this._verdict(q, rows);

    const codes = this.codesMatching(q);
    if (codes.length === 1) {
      return { name: q, verdict: "stable", code: codes[0], escalate: false,
               as_of: this.release };
    }
    if (codes.length > 1) {
      return { name: q, verdict: "ambiguous", codes, escalate: true, next: "resolve_name",
               reason: "matched under more than one nomenclatural code" };
    }

    // Abbreviation before strain. "C. difficile 630" is both shapes at once,
    // and the abbreviated genus is the part that cannot be looked up as
    // written, so it has to be settled first.
    const abbrev = this._checkAbbreviation(q);
    if (abbrev) return abbrev;

    // Might be a strain: binomial plus a laboratory designation. The
    // designation is not governed by any code and never changes; the binomial
    // is what moves, and every strain inherits its species' transfer. So the
    // strain form is derived rather than stored -- which is why over a million
    // strain taxa are absent from this file without any loss of coverage.
    const split = splitDesignation(q);
    if (split) {
      const [binomial, designation] = split;
      const brows = this._lookup(normalizeName(binomial));
      if (brows.length) {
        const v = this._verdict(binomial, brows);
        v.name = q;
        v.resolved_via = { binomial, designation };
        if (v.accepted_name) {
          v.accepted_name = `${v.accepted_name} ${designation}`;
          v.note = `the species was transferred; the designation '${designation}' is ` +
                   "unchanged. This strain name is derived from the species record, not " +
                   "read from one -- searches should cover both forms.";
        }
        return v;
      }
      const bcodes = this.codesMatching(binomial);
      if (bcodes.length === 1) {
        return { name: q, verdict: "stable", code: bcodes[0], escalate: false,
                 resolved_via: { binomial, designation }, as_of: this.release };
      }
    }

    return {
      name: q, verdict: "unknown", escalate: true, next: "consult_a_full_source",
      reason: REASON.unknown,
      do_not: "Do not substitute a name you remember. The string may be a misspelling, " +
              `an infraspecific or strain designation, or newer than ${this.release}.`,
    };
  }

  /**
   * Resolve an abbreviated genus by enumerating what it could stand for.
   *
   * The input schema has always promised this form and the lookup has never
   * supported it: checkName("C. difficile") returned `unknown` alongside "Do
   * not substitute a name you remember", which is the worst available answer --
   * indistinguishable from "no such organism" and offering no way forward.
   *
   * Enumerating rather than guessing is the point. "S. aureus" matches twelve
   * binomials in NCBI including a plant (Senecio), a fish (Stegastes) and a
   * pothos (Scindapsus). Everyone reads it as Staphylococcus. That is the same
   * conflation this package exists to catch, running the other way: one string,
   * several organisms.
   *
   * Mirrors _check_abbreviation in src/binomen/resolver.py.
   */
  _checkAbbreviation(q) {
    const split = splitAbbreviation(q);
    if (!split) return null;
    const [initial, rest] = split;
    const esc = rest.replace(/\\/g, "\\\\").replace(/%/g, "\\%").replace(/_/g, "\\_");
    // LIKE matches the whole string, so a trinomial ending in the same epithet
    // comes back too: "spermophilus elegans aureus" answers 's% aureus'. It
    // abbreviates to "S. e. aureus", not "S. aureus", so it goes on token count.
    const want = rest.split(/\s+/).length + 1;
    const names = this.store.expandRows(`${initial}% ${esc}`)
      .map((r) => r.norm)
      .filter((n) => n[0] === initial && n.split(/\s+/).length === want);
    if (!names.length) return null;

    if (names.length > 1) {
      return {
        name: q, verdict: "ambiguous_abbreviation", escalate: true, next: "resolve_name",
        reason: `abbreviated genus; ${names.length} names in the index match it`,
        expansions: names.map(capitaliseGenus),
        coverage_warning: ABBREV_COVERAGE,
        do_not: "Do not assume the familiar expansion. Abbreviations collide across "
              + "kingdoms, and the reader's organism may not be yours.",
      };
    }

    const expanded = names[0];
    const via = { abbreviation: q, expanded: capitaliseGenus(expanded) };
    const rows = this._lookup(expanded);
    if (!rows.length) {
      const codes = this.codesMatching(expanded);
      if (codes.length !== 1) return null;
      return { name: q, verdict: "stable", code: codes[0], escalate: false,
               resolved_via: via, coverage_warning: ABBREV_COVERAGE, as_of: this.release };
    }
    const out = this._verdict(q, rows);
    out.resolved_via = via;
    out.coverage_warning = ABBREV_COVERAGE;
    return out;
  }

  _verdict(q, rows) {
    const r = rows[0];
    const out = { name: q, verdict: r.verdict, code: r.code, escalate: true,
                  reason: REASON[r.verdict] };
    if (r.verdict === "homonym") {
      out.next = "resolve_name";
      return out;
    }
    out.next = r.verdict === "contested" ? "resolve_name" : "resolve_name";
    const t = r.taxid != null ? this.taxon(r.taxid) : null;
    // One extra field, only where it saves a whole round trip: a plainly
    // superseded name whose replacement we know.
    if (r.verdict === "superseded" && t) out.accepted_name = t.accepted;
    return out;
  }

  // ------------------------------------------------------------- stage 2
  resolveName(name) {
    const q = String(name || "").trim();
    const warnings = [];
    let rows = this._lookup(normalizeName(q));
    let designation = null;

    if (!rows.length) {
      const split = splitDesignation(q);
      if (split && this._lookup(normalizeName(split[0])).length) {
        [, designation] = split;
        rows = this._lookup(normalizeName(split[0]));
        warnings.push(
          `'${q}' was resolved through its species name '${split[0]}'; the designation ` +
          `'${split[1]}' is a laboratory identifier, not governed by any nomenclatural ` +
          "code. Names below are the species records; append the designation for the " +
          "strain-level form.");
      }
    }

    const note = this.note(q);
    const contested = Boolean(note && note.contested) ||
      rows.some((r) => r.verdict === "contested");

    if (!rows.length) {
      return {
        query: q, candidates: [], contested: false,
        warnings: [
          `'${q}' not found in ${this.release}. May be a misspelling, an infraspecific ` +
          "name, or newer than this index. Do not substitute a remembered name.",
        ],
        provenance: [this.provenance()],
      };
    }

    if (rows.length > 1 || rows[0].verdict === "homonym") {
      warnings.push(
        `homonym: '${q}' denotes ${rows.length} distinct taxa. All are returned; the ` +
        "correct one depends on which is meant.");
    }

    const candidates = [];
    for (const r of rows) {
      const t = r.taxid != null ? this.taxon(r.taxid) : null;
      if (!t) continue;
      const c = {
        accepted_name: t.accepted,
        status: { normalized: contested ? "contested" : "accepted" },
        sources: ["NCBI Taxonomy"],
        identifier: `NCBI:txid${t.taxid}`,
      };
      if (t.rank) c.rank = t.rank;
      if (t.authority) c.authority = t.authority;
      candidates.push(c);
    }

    // Disagreement is never collapsed. There is deliberately no top-level
    // current_name: if one existed every caller would read it and contested
    // cases would silently degrade to whichever answer sorted first.
    if (note && note.contested) {
      warnings.unshift(`AUTHORITIES DISAGREE. ${note.guidance || ""}`.trim());
      for (const cand of note.candidates || []) {
        if (!candidates.some((c) => c.accepted_name === cand.accepted_name)) {
          candidates.push({
            accepted_name: cand.accepted_name,
            status: { normalized: "contested" },
            sources: cand.supporting_sources || [],
            argument: cand.argument,
          });
        }
      }
    } else if (note && note.guidance) {
      warnings.push(note.guidance);
    }

    const out = {
      query: q, candidates,
      governing_code: { code: rows[0].code },
      provenance: [this.provenance()],
    };
    if (contested) out.contested = true;
    if (designation) out.designation = designation;
    if (rows[0].verdict === "superseded") {
      out.input_status = { normalized: "synonym" };
    }
    if (warnings.length) out.warnings = warnings;
    const stale = this._stalenessWarning();
    if (stale) (out.warnings ||= []).push(stale);
    // Stage 2 is where detail belongs: standing in the register's own words,
    // the rank each source holds the name at, and the link the licence requires.
    return this._withRegisters(out, q, {
      code: out.governing_code && out.governing_code.code,
      currentName: candidates.length ? candidates[0].accepted_name : null,
      rank: candidates.length ? candidates[0].rank || null : null,
    });
  }

  getSynonyms(name) {
    const q = String(name || "").trim();
    let rows = this._lookup(normalizeName(q));
    let designation = null;
    if (!rows.length) {
      const split = splitDesignation(q);
      if (split) {
        const brows = this._lookup(normalizeName(split[0]));
        if (brows.length) { rows = brows; [, designation] = split; }
      }
    }
    if (!rows.length) {
      return { query: q, all_names: [],
               warnings: [`'${q}' not found in ${this.release}.`],
               provenance: [this.provenance()] };
    }
    const names = new Set();
    let accepted = null;
    for (const r of rows) {
      const t = r.taxid != null ? this.taxon(r.taxid) : null;
      if (!t) continue;
      accepted ||= t.accepted;
      names.add(t.accepted);
      for (const s of t.synonyms) names.add(s);
    }
    const note = this.note(q);
    const out = {
      query: q, accepted_name: accepted,
      all_names: [...names].sort(),
      note: "Names are stored bare and searchable; author citations are stripped. " +
            "This index does not distinguish homotypic from heterotypic synonyms.",
      provenance: [this.provenance()],
    };
    if (designation) out.designation = designation;
    if (note && note.contested) {
      out.contested = true;
      out.warnings = [`AUTHORITIES DISAGREE. ${note.guidance || ""}`.trim()];
    }
    // An enumeration of names is exactly where a register's alternative belongs:
    // a caller asking for every name recorded for a taxon must not be handed a
    // list the ICNP register would add to.
    return this._withRegisters(out, q, {
      code: rows[0] && rows[0].code,
      currentName: accepted,
    });
  }

  expandQuery(name, { includeAbbreviated = true } = {}) {
    const syn = this.getSynonyms(name);
    if (!syn.all_names.length) {
      return { query: name, search_terms: [],
               warnings: syn.warnings, provenance: [this.provenance()] };
    }
    let terms = new Set(syn.all_names);
    // Reattach a strain designation, if the query carried one: someone
    // searching for "Clostridioides difficile 630" needs "Clostridium
    // difficile 630" too, and neither bare species name will find it.
    if (syn.designation) {
      terms = new Set([...terms].map((t) => `${t} ${syn.designation}`));
    }
    const ordered = [...terms].sort();
    const abbreviated = [];
    if (includeAbbreviated) {
      for (const t of ordered) {
        const parts = t.split(" ");
        if (parts.length >= 2 && /^\p{Lu}/u.test(parts[0])) {
          abbreviated.push(`${parts[0][0]}. ${parts.slice(1).join(" ")}`);
        }
      }
    }
    const out = {
      query: name,
      search_terms: ordered,
      abbreviated_forms: [...new Set(abbreviated)].sort(),
      boolean_query: ordered.map((t) => `"${t}"`).join(" OR "),
      pubmed_query: ordered.map((t) => `"${t}"[tiab]`).join(" OR "),
      coverage_warning:
        `Searching the current name alone would have missed ${ordered.length - 1} ` +
        "variant(s). Covers names recorded in this index only -- not informal usage, " +
        `unindexed misspellings, or changes after ${this.release}.`,
      provenance: [this.provenance()],
    };
    if (syn.contested) out.contested = true;
    return out;
  }

  _stalenessWarning() {
    const { releaseAgeDays, STALE_AFTER_DAYS } = require("./index_store.js");
    const age = releaseAgeDays(this.release);
    if (age === null || age <= STALE_AFTER_DAYS) return null;
    return `This index is ${age} days old (${this.release}). NCBI Taxonomy changes ` +
           "continuously; names accepted since then resolve as unknown and recent " +
           "transfers are missing.";
  }

  close() { this.store.close(); }
}

module.exports = { Resolver, FieldStore, REASON };
