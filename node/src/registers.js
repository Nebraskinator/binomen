"use strict";
/*
 * Nomenclatural registers, read from the database shipped beside the backbone.
 *
 * Mirrors src/binomen/build/harvest_registers.py, which builds the file. This
 * module only reads it, and it is the only place in the server that knows what a
 * register row looks like.
 *
 * Two files, not one. NCBI's half is public domain; the registers are CC BY-SA
 * (LPSN) and CC BY (Species Fungorum, ICTV), and share-alike is viral, so
 * merging them would put a licence claim on the NCBI half it does not otherwise
 * carry. They are joined here, at query time, on the normalised name -- verified
 * to work under Claude Desktop's built-in Node. See
 * docs/adr/0002-two-files-for-licence-containment.md.
 *
 * Two runtime facts from that verification shape this file:
 *
 *   - cwd is C:\WINDOWS\system32 when Claude Desktop launches an extension, so
 *     every path is resolved from __dirname and never relatively.
 *   - two open handles work as well as ATTACH, so we use two handles: the same
 *     answer with a narrower dependency on what the runtime provides.
 *
 * What a register can and cannot say
 * ----------------------------------
 * A register speaks only for its own code. LPSN has nothing to say about Homo
 * sapiens, and its silence there is not evidence -- reporting "no standing under
 * the ICNP" for a mammal would be true and useless. Jurisdiction is checked
 * before anything is reported.
 *
 * The rule from the Python side holds here too: annotation only ever ADDS keys.
 * The backbone's verdict is never overwritten. Choosing a winner is the caller's
 * job, and silently swapping in the register's answer would be the same
 * substitution this project exists to detect.
 */
const fs = require("node:fs");
const path = require("node:path");
const { DatabaseSync } = require("node:sqlite");
const { normalizeName } = require("./names.js");

/* Bundled first, repo checkout second. The bundled layout is what a user gets;
 * the repo layout is what the tests and the maintainer get. */
function candidatePaths(file) {
  // An explicit override is authoritative, with no fallback. Searching on past
  // it would mean a caller who pointed at one file could silently get another:
  // that is how the test suite started reading the maintainer's real 19 MB
  // register and asserting against whatever happened to be on disk.
  if (process.env.BINOMEN_REGISTER_DB) return [process.env.BINOMEN_REGISTER_DB];
  return [
    path.join(__dirname, "..", "data", file),      // bundled inside the extension
    path.join(__dirname, "..", "..", "data", file), // repo checkout
  ];
}

/* A bare DOI is a citation; a resolvable URL is a link someone can follow, and
 * following it is how a reader checks the claim. */
function doiUrl(doi) {
  if (!doi) return null;
  return /^https?:\/\//.test(doi) ? doi : `https://doi.org/${doi}`;
}

class Registers {
  constructor(file) {
    this.db = null;
    this.meta = {};
    this.path = null;
    const candidates = file ? [file] : candidatePaths("registers.sqlite");
    const found = candidates.find((p) => { try { return fs.existsSync(p); } catch { return false; } });
    if (!found) return;
    try {
      this.db = new DatabaseSync(found, { readOnly: true });
      this.path = found;
      for (const r of this.db.prepare("SELECT key, value FROM meta").all()) {
        this.meta[r.key] = r.value;
      }
      this._byName = this.db.prepare(
        "SELECT norm, name, code, source, rank, status, native_status, " +
        "accepted_name, accepted_norm, link, extras FROM register WHERE norm = ?");
    } catch {
      // A corrupt or half-written register must never take the server down. The
      // install then behaves exactly as one without a register: silent.
      this.db = null;
    }
  }

  get available() { return this.db !== null; }

  /* Which codes this file speaks for, from the meta rows the harvest wrote. */
  get codes() {
    const out = {};
    for (const [k, v] of Object.entries(this.meta)) {
      if (k.endsWith(".code")) out[k.slice(0, -5)] = v;
    }
    return out;
  }

  sourceLabel(key) {
    return this.meta[`${key}.source`] || key;
  }

  snapshot(key) {
    return this.meta[`${key}.version`] || this.meta[`${key}.issued`] ||
      this.meta[`${key}.harvested_at`] || null;
  }

  /* Every register row for a name, as plain objects. No judgement applied. */
  opinions(name) {
    if (!this.db) return [];
    const rows = this._byName.all(normalizeName(name)) || [];
    return rows.map((r) => {
      let extras = {};
      try { extras = r.extras ? JSON.parse(r.extras) : {}; } catch { extras = {}; }
      return {
        source: r.source,
        sourceLabel: this.sourceLabel(r.source),
        snapshot: this.snapshot(r.source),
        code: r.code,
        name: r.name,
        rank: r.rank || null,
        status: r.status,
        nativeStatus: r.native_status || null,
        acceptedName: r.accepted_name || null,
        acceptedNorm: r.accepted_norm || null,
        // Per-record links come from the paged API; the archive route has none,
        // so fungal rows fall back to the dataset's own DOI. CC BY requires
        // attribution and a row that cannot say where it came from cannot
        // provide it -- an approximate citation is the obligation met, a null is
        // the obligation dropped.
        link: r.link || doiUrl(this.meta[`${r.source}.doi`]),
        medicalUse: extras.medical_use || null,
        // LPSN attaches its recommendation to a name, so the superseded name
        // reads "not recommended" and the name the register prefers reads
        // "recommended". Reporting only the former beside the latter's name
        // would say the opposite of what the register says.
        preferredMedicalUse: extras.accepted_medical_use || null,
        remarks: extras.remarks || null,
      };
    });
  }

  /* Registers with jurisdiction over this code, whether or not they hold the
   * name. Used to tell "no standing" apart from "not our code". */
  hasJurisdiction(code) {
    return Object.values(this.codes).includes(code);
  }

  /* --------------------------------------------------------------- stage 1
   * check_name promises ~25 tokens, and that promise is the only reason an agent
   * calls it on every organism mention. Agreement is not news: it costs a caller
   * nothing to learn that two sources concur. Bytes are spent only on
   * disagreement and on absence, the two things a caller cannot find out any
   * other way. Detail belongs in resolve_name.
   */
  annotateTerse(out, name) {
    if (!this.available) return out;
    const q = String(name || "").trim();
    const backboneName = out.accepted_name || out.name || q;
    const hits = this.opinions(q).filter((o) => !out.code || o.code === out.code);

    // Absence is silence, and this is not the same rule the single-register
    // reader used. That one shipped every LPSN name, so a missing row meant "not
    // validly published". This file ships only names that carry an ambiguity
    // (docs/adr/0001-ambiguity-only-local-database.md), so a missing row means
    // the registers have nothing to say -- which is the common case, and true of
    // Escherichia coli. Reporting absence as "no standing under the ICNP" here
    // would assert something false about the most ordinary names there are.
    //
    // Lack of standing is a claim, so it must come from a row that makes it:
    // the harvest records those with status `no_standing`.
    if (!hits.length) return out;

    const unstanding = hits.filter((o) => o.status === "no_standing");
    if (unstanding.length) {
      out.registers = unstanding.map((o) => ({
        source: o.sourceLabel,
        has_standing: false,
        native_status: o.nativeStatus,
        url: o.link,
      }));
      out.escalate = true;
      out.next = "resolve_name";
      out.do_not = `${unstanding[0].sourceLabel} records no standing for this name ` +
                   "under its code. Do not present it as an accepted name.";
      return out;
    }

    const disagreeing = hits.filter((o) => {
      const theirs = o.acceptedName || o.name;
      return theirs && backboneName &&
        normalizeName(theirs) !== normalizeName(backboneName);
    });
    if (!disagreeing.length) return out;

    out.sources_disagree = true;
    out.escalate = true;
    out.next = "resolve_name";
    out.registers = disagreeing.map((o) => ({
      source: o.sourceLabel,
      accepted_name: o.acceptedName || o.name,
      url: o.link,
      ...(o.preferredMedicalUse ? { medical_use: o.preferredMedicalUse } : {}),
    }));
    const clinical = disagreeing.find(
      (o) => String(o.preferredMedicalUse || "").startsWith("recommend"));
    const first = disagreeing[0];
    let note = `${first.sourceLabel} gives '${first.acceptedName || first.name}'. Report both.`;
    if (clinical) {
      note += ` It recommends '${clinical.acceptedName || clinical.name}' for medical use; ` +
              "giving the backbone's name alone in a clinical context is an error.";
    }
    out.do_not = note;
    return out;
  }

  /* --------------------------------------------------------------- stage 2
   * Where detail belongs: standing in the register's own words, the rank each
   * source holds the name at, the snapshot the answer came from, and the link
   * the licence requires us to carry.
   */
  annotateDetailed(out, name, { code = null, currentName = null, rank = null } = {}) {
    if (!this.available) return out;
    const q = String(name || "").trim();
    const hits = this.opinions(q).filter((o) => !code || o.code === code);
    if (!hits.length) return out;

    out.registers = hits.map((o) => {
      const theirs = o.acceptedName || o.name;
      const entry = {
        source: o.sourceLabel,
        code: o.code,
        accepted_name: theirs,
        standing: { normalized: o.status, native: o.nativeStatus },
        snapshot: o.snapshot,
        url: o.link,
      };
      if (o.rank) entry.rank = o.rank;
      if (o.medicalUse) entry.medical_use = { name: o.name, status: o.medicalUse };
      if (o.preferredMedicalUse) {
        entry.medical_use_preferred = { name: theirs, status: o.preferredMedicalUse };
      }
      if (o.remarks) entry.remarks = o.remarks;
      if (currentName && theirs &&
          normalizeName(theirs) !== normalizeName(currentName)) {
        entry.disagrees_with_backbone = true;
      }
      // Same name, different rank. A real finding, and not the same claim as a
      // name disagreement: NCBI holds Treponema pertenue at subspecies where
      // LPSN holds it at species, and the two agree about the name completely.
      if (rank && o.rank && rank.toLowerCase() !== o.rank.toLowerCase() &&
          currentName && theirs &&
          normalizeName(theirs) === normalizeName(currentName)) {
        entry.rank_disagreement = { backbone: rank, register: o.rank };
      }
      return entry;
    });
    if (out.registers.some((e) => e.disagrees_with_backbone)) out.sources_disagree = true;
    if (out.registers.some((e) => e.rank_disagreement)) out.ranks_disagree = true;
    return out;
  }

  close() {
    if (this.db) { try { this.db.close(); } catch { /* already closed */ } }
    this.db = null;
  }
}

module.exports = { Registers };
