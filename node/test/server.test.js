"use strict";
/* End-to-end tests for the field-edition server, against a real index built by
 * the Python builder from the real-rows fixture. Nothing is mocked: the point
 * is that the two implementations agree about a file one wrote and the other
 * reads. */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const ROOT = path.join(__dirname, "..", "..");
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "binomen-node-"));
const INDEX = path.join(TMP, "field.sqlite");

/* Point the shipped databases at nothing, before anything requires them.
 *
 * Without this, a checkout that has run a harvest serves the maintainer's real
 * 19 MB register to these tests, and assertions about what check_name returns
 * start depending on whose machine they run on. Tests that need a register build
 * a small one and pass its path explicitly. */
process.env.BINOMEN_REGISTER_DB = path.join(TMP, "no-registers.sqlite");
process.env.BINOMEN_AMBIGUITY_DB = path.join(TMP, "no-ambiguity.sqlite");

/* Locate a Python that can import binomen.
 *
 * build_mcpb.py passes BINOMEN_PYTHON so the exact interpreter that has
 * binomen installed is used. Standalone `node --test` has to guess: "python3"
 * on Unix, "python" on Windows -- where "python3" is not a command at all and
 * Windows redirects the missing name to the Microsoft Store, producing an
 * error that mentions neither Python nor this test.
 */
function findPython() {
  const candidates = process.env.BINOMEN_PYTHON
    ? [process.env.BINOMEN_PYTHON]
    : (process.platform === "win32" ? ["python", "py", "python3"] : ["python3", "python"]);
  // Probe with the same environment the real call uses: PYTHONPATH pointing
  // at src/. That way a plain checkout works with nothing installed, which is
  // what a contributor running `node --test` for the first time actually has.
  const env = { ...process.env, PYTHONPATH: path.join(ROOT, "src") };
  for (const exe of candidates) {
    try {
      execFileSync(exe, ["-c", "import binomen"], { stdio: "ignore", env });
      return exe;
    } catch { /* try the next one */ }
  }
  throw new Error(
    `no usable Python found (tried: ${candidates.join(", ")}).\n` +
    "binomen needs Python 3.10+ on PATH to build the test index.\n" +
    "Set BINOMEN_PYTHON to the interpreter you want used.");
}

const PYTHON = findPython();

// Build the index with the Python builder, exactly as a release would.
execFileSync(PYTHON, [
  "-m", "binomen.build.build_index",
  "--fixture", path.join(ROOT, "tests", "fixtures", "taxdump"),
  "--out", path.join(TMP, "full.sqlite"),
  "--stage1-out", path.join(TMP, "s1.sqlite"),
  "--field-out", INDEX,
  "--version", "taxdump-2026-08-11", "--quiet",
], { cwd: ROOT, env: { ...process.env, PYTHONPATH: path.join(ROOT, "src") } });

const { Resolver } = require("../src/resolver.js");
const r = new Resolver(INDEX);

test("a superseded name reports its replacement", () => {
  const out = r.checkName("Clostridium difficile");
  assert.strictEqual(out.verdict, "superseded");
  assert.strictEqual(out.accepted_name, "Clostridioides difficile");
  assert.strictEqual(out.escalate, true);
});

test("check_name stays cheap", () => {
  // The description promises ~2 ms and one short line, and that promise is the
  // only reason an agent calls it on every organism mention.
  for (const q of ["Clostridium difficile", "Candida auris", "Nonsenseus fakus"]) {
    assert.ok(JSON.stringify(r.checkName(q)).length < 400, q);
  }
});

test("a contested name is never reduced to one answer", () => {
  const out = r.resolveName("Candida auris");
  assert.strictEqual(out.contested, true);
  assert.ok(out.candidates.length >= 2, JSON.stringify(out.candidates));
  assert.ok(out.warnings.some((w) => w.includes("AUTHORITIES DISAGREE")));
  assert.ok(!("current_name" in out), "a top-level current_name would hide the disagreement");
});

test("an unknown name is reported as unknown, not guessed", () => {
  const out = r.checkName("Nonsenseus fakus");
  assert.strictEqual(out.verdict, "unknown");
  assert.match(out.do_not, /Do not substitute/);
});

test("a strain resolves through its binomial and keeps its designation", () => {
  const out = r.checkName("Clostridium difficile 630");
  assert.strictEqual(out.verdict, "superseded");
  assert.deepStrictEqual(out.resolved_via,
    { binomial: "Clostridium difficile", designation: "630" });
  assert.strictEqual(out.accepted_name, "Clostridioides difficile 630");
  assert.match(out.note, /derived from the species record/);
});

test("expand_query spans the genus change", () => {
  const out = r.expandQuery("Clostridium difficile");
  assert.ok(out.search_terms.includes("Clostridium difficile"));
  assert.ok(out.search_terms.includes("Clostridioides difficile"));
  assert.ok(out.abbreviated_forms.includes("C. difficile"));
});

test("search terms carry no citations or strain designations", () => {
  for (const q of ["Clostridium difficile", "Candida auris", "Lactobacillus casei"]) {
    for (const t of r.expandQuery(q).search_terms) {
      assert.ok(!/[()[\]]/.test(t), `unsearchable term: ${t}`);
      assert.ok(!/\b(1[6-9]\d{2}|20[0-2]\d)\b/.test(t), `citation year in: ${t}`);
    }
  }
});

test("a strain query expands at strain level", () => {
  const out = r.expandQuery("Clostridium difficile 630");
  assert.ok(out.search_terms.every((t) => t.endsWith("630")), JSON.stringify(out.search_terms));
  assert.ok(out.search_terms.some((t) => t.startsWith("Clostridium difficile")));
  assert.ok(out.search_terms.some((t) => t.startsWith("Clostridioides difficile")));
});

test("every response carries provenance naming the release", () => {
  for (const out of [r.resolveName("Clostridium difficile"),
                     r.getSynonyms("Clostridium difficile"),
                     r.expandQuery("Clostridium difficile")]) {
    assert.ok(out.provenance?.[0]?.version, JSON.stringify(out).slice(0, 120));
  }
});

test("the index knows its own release", () => {
  assert.strictEqual(r.release, "taxdump-2026-08-11");
});

test("staleness is surfaced, because nobody reads provenance fields", () => {
  const old = path.join(TMP, "old.sqlite");
  fs.copyFileSync(INDEX, old);
  const { DatabaseSync } = require("node:sqlite");
  const db = new DatabaseSync(old);
  db.prepare("UPDATE meta SET value = 'taxdump-2019-01-01' WHERE key = 'version'").run();
  db.close();
  const stale = new Resolver(old);
  const out = stale.resolveName("Clostridium difficile");
  assert.ok(out.warnings.some((w) => /days old/.test(w)), JSON.stringify(out.warnings));
  stale.close();
});

// --- register annotation -------------------------------------------------
// Built here rather than taken from data/registers.sqlite: the rest of this file
// is deterministic against a fixture, and a test that only passes when someone
// has run a harvest is not a test.
function tempRegisters() {
  const { DatabaseSync } = require("node:sqlite");
  const f = path.join(os.tmpdir(), `binomen-reg-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}.sqlite`);
  const db = new DatabaseSync(f);
  db.exec(`CREATE TABLE register (norm TEXT NOT NULL, name TEXT NOT NULL, code TEXT NOT NULL,
             source TEXT NOT NULL, rank TEXT, status TEXT NOT NULL, native_status TEXT,
             accepted_name TEXT, accepted_norm TEXT, link TEXT, extras TEXT,
             PRIMARY KEY (norm, code, source)) WITHOUT ROWID;
           CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
           INSERT INTO meta VALUES ('lpsn.code','ICNP'),('lpsn.source','LPSN (DSMZ)'),
             ('lpsn.version','2026-07-26'),('sfp.code','ICNafp'),
             ('sfp.source','Species Fungorum Plus (Kew)');
           INSERT INTO register VALUES
             ('borreliella burgdorferi','Borreliella burgdorferi','ICNP','lpsn','species',
              'synonym','synonym','Borrelia burgdorferi','borrelia burgdorferi',
              'https://doi.org/10.83108/rn.792726',
              '{"medical_use":"not recommended","accepted_medical_use":"recommended"}'),
             ('treponema pertenue','Treponema pertenue','ICNP','lpsn','species',
              'accepted','accepted',NULL,NULL,'https://doi.org/10.83108/rn.1','{}');`);
  db.close();
  const { Registers } = require("../src/registers.js");
  return new Registers(f);
}

test("a register disagreement is reported, and is allowed to cost more", () => {
  // The cheapness promise above is about the COMMON case. NCBI says
  // Borreliella burgdorferi; LPSN -- the register the ICNP points to -- says
  // Borrelia burgdorferi and recommends it for medical use. That is the one
  // thing a caller cannot learn any other way, so it is worth the bytes.
  const reg = tempRegisters();
  const out = reg.annotateTerse(
    { name: "Borreliella burgdorferi", verdict: "has_synonyms", code: "ICNP" },
    "Borreliella burgdorferi");
  assert.strictEqual(out.sources_disagree, true);
  assert.strictEqual(out.registers[0].accepted_name, "Borrelia burgdorferi");
  assert.ok(out.registers[0].url, "attribution DOI is a licence obligation, not decoration");
  assert.match(out.do_not, /medical/i);
  reg.close();
});

test("the recommendation reported is the preferred name's, not the query's", () => {
  // LPSN attaches lorn_status to a NAME. The superseded name reads "not
  // recommended"; the name LPSN prefers reads "recommended". Reporting the
  // query's own flag beside the preferred name would tell a clinician the
  // opposite of what the register says.
  const reg = tempRegisters();
  const out = reg.annotateTerse(
    { name: "Borreliella burgdorferi", verdict: "has_synonyms", code: "ICNP" },
    "Borreliella burgdorferi");
  assert.strictEqual(out.registers[0].medical_use, "recommended");
  assert.match(out.do_not, /recommends 'Borrelia burgdorferi'/);
  reg.close();
});

test("the register never overwrites the backbone's answer", () => {
  // Choosing a winner is the caller's job. Silently swapping accepted_name for
  // the register's is the substitution this package exists to detect.
  const reg = tempRegisters();
  const out = reg.annotateTerse(
    { name: "Borreliella burgdorferi", verdict: "superseded", code: "ICNP",
      accepted_name: "Borreliella burgdorferi" }, "Borreliella burgdorferi");
  assert.strictEqual(out.accepted_name, "Borreliella burgdorferi");
  reg.close();
});

test("the register stays silent outside its own code", () => {
  // LPSN speaks for the ICNP. "No standing under the ICNP" is true of Homo
  // sapiens and useless: it is a mammal. Bacillus the stick insect must not be
  // answered with Bacillus the bacterium either.
  const reg = tempRegisters();
  const out = reg.annotateTerse({ name: "Homo sapiens", verdict: "stable", code: "ICZN" },
                                "Homo sapiens");
  assert.strictEqual(out.register, undefined);
  assert.strictEqual(out.registers, undefined);
  reg.close();
});

test("agreement costs nothing", () => {
  const reg = tempRegisters();
  const out = reg.annotateTerse(
    { name: "Borrelia burgdorferi", verdict: "superseded", code: "ICNP",
      accepted_name: "Borrelia burgdorferi" }, "Borrelia burgdorferi");
  assert.strictEqual(out.registers, undefined);
  assert.strictEqual(out.sources_disagree, undefined);
  reg.close();
});

test("a rank disagreement is not reported as a name disagreement", () => {
  // NCBI holds Treponema pertenue at subspecies where LPSN holds it at species.
  // The two agree about the name completely, and saying "sources disagree"
  // would manufacture a naming dispute out of a ranking one.
  const reg = tempRegisters();
  const out = reg.annotateDetailed({}, "Treponema pertenue",
    { code: "ICNP", currentName: "Treponema pertenue", rank: "subspecies" });
  assert.strictEqual(out.ranks_disagree, true);
  assert.strictEqual(out.sources_disagree, undefined);
  assert.deepStrictEqual(out.registers[0].rank_disagreement,
                         { backbone: "subspecies", register: "species" });
  reg.close();
});

test("stage 2 carries standing, snapshot and the licence link", () => {
  const reg = tempRegisters();
  const out = reg.annotateDetailed({}, "Borreliella burgdorferi",
    { code: "ICNP", currentName: "Borreliella burgdorferi" });
  const e = out.registers[0];
  assert.strictEqual(e.disagrees_with_backbone, true);
  assert.strictEqual(e.standing.native, "synonym");
  assert.strictEqual(e.snapshot, "2026-07-26");
  assert.ok(e.url);
  assert.strictEqual(e.medical_use_preferred.name, "Borrelia burgdorferi");
  reg.close();
});

test("a name the registers do not hold gets silence, not a standing claim", () => {
  // The register database ships only names that carry an ambiguity, so a
  // missing row means "nothing to report" -- NOT "never validly published".
  // Escherichia coli is in LPSN and is unremarkable, so it has no row; saying
  // it has no ICNP standing would be false about one of the best established
  // names in bacteriology. Lack of standing is a claim and must come from a row.
  const reg = tempRegisters();
  const out = reg.annotateTerse(
    { name: "Escherichia coli", verdict: "has_synonyms", code: "ICNP" },
    "Escherichia coli");
  assert.strictEqual(out.registers, undefined);
  assert.strictEqual(out.register, undefined);
  assert.strictEqual(out.do_not, undefined);
  reg.close();
});

test("a recorded lack of standing IS reported", () => {
  const { DatabaseSync } = require("node:sqlite");
  const f = path.join(os.tmpdir(), `binomen-reg-ns-${process.pid}-${Date.now()}.sqlite`);
  const db = new DatabaseSync(f);
  db.exec(`CREATE TABLE register (norm TEXT NOT NULL, name TEXT NOT NULL, code TEXT NOT NULL,
             source TEXT NOT NULL, rank TEXT, status TEXT NOT NULL, native_status TEXT,
             accepted_name TEXT, accepted_norm TEXT, link TEXT, extras TEXT,
             PRIMARY KEY (norm, code, source)) WITHOUT ROWID;
           CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
           INSERT INTO meta VALUES ('lpsn.code','ICNP'),('lpsn.source','LPSN (DSMZ)');
           INSERT INTO register VALUES ('nomen nudum','Nomen nudum','ICNP','lpsn','species',
             'no_standing','bare name',NULL,NULL,'https://doi.org/10.83108/rn.2',NULL);`);
  db.close();
  const { Registers } = require("../src/registers.js");
  const reg = new Registers(f);
  const out = reg.annotateTerse({ name: "Nomen nudum", verdict: "unknown", code: "ICNP" },
                                "Nomen nudum");
  assert.strictEqual(out.registers[0].has_standing, false);
  assert.strictEqual(out.registers[0].native_status, "bare name");
  assert.match(out.do_not, /no standing/i);
  reg.close();
});

test("an absent register file is not an error", () => {
  // An install without the register must behave exactly as one that never had
  // it: silent, not broken.
  const { Registers } = require("../src/registers.js");
  const reg = new Registers(path.join(os.tmpdir(), "binomen-no-such-register.sqlite"));
  assert.strictEqual(reg.available, false);
  assert.deepStrictEqual(reg.annotateTerse({ name: "x", code: "ICNP" }, "x"),
                         { name: "x", code: "ICNP" });
});

// --- the bundled backbone ------------------------------------------------
// The shipped install reads ambiguity.sqlite, not the fetched stage-2 index.
// Both go through the same Resolver: one implementation of the tool logic over
// two stores, because two copies is the drift ADR-0003 exists to prevent.
function tempAmbiguity({ enumeration = true } = {}) {
  const { DatabaseSync } = require("node:sqlite");
  const f = path.join(os.tmpdir(), `binomen-amb-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}.sqlite`);
  const db = new DatabaseSync(f);
  db.exec(`CREATE TABLE amb (norm TEXT NOT NULL, code INTEGER NOT NULL,
             verdict INTEGER NOT NULL, cluster INTEGER NOT NULL, accepted TEXT,
             PRIMARY KEY (norm, code)) WITHOUT ROWID;
           CREATE TABLE cluster (id INTEGER PRIMARY KEY, names TEXT NOT NULL);
           CREATE TABLE bloom (code TEXT PRIMARY KEY, n INTEGER NOT NULL, blob BLOB NOT NULL);
           CREATE TABLE vocab (kind TEXT NOT NULL, id INTEGER NOT NULL, value TEXT NOT NULL);
           CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
           INSERT INTO vocab VALUES ('code',0,'ICNP'),('code',2,'ICZN'),
             ('verdict',0,'superseded'),('verdict',1,'has_synonyms');
           INSERT INTO meta VALUES ('source','NCBI Taxonomy'),('ncbi.version','index-2026-08-13')
             ${enumeration ? "" : ",('enumeration','absent')"};
           INSERT INTO amb VALUES ('bacteroides vulgatus',0,0,821,'Phocaeicola vulgatus');
           INSERT INTO amb VALUES ('phocaeicola vulgatus',0,1,821,NULL);
           ${enumeration ? "INSERT INTO cluster VALUES (821,'Phocaeicola vulgatus\x1fBacteroides vulgatus');" : ""}`);
  db.close();
  const { AmbiguityStore } = require("../src/ambiguity.js");
  return new AmbiguityStore(f);
}

test("the bundled store answers check_name without any fetched index", () => {
  const { Resolver } = require("../src/resolver.js");
  const r = new Resolver(tempAmbiguity());
  const out = r.checkName("Bacteroides vulgatus");
  assert.strictEqual(out.verdict, "superseded");
  assert.strictEqual(out.accepted_name, "Phocaeicola vulgatus");
  r.close();
});

test("a cluster supplies the alternatives offline", () => {
  const { Resolver } = require("../src/resolver.js");
  const r = new Resolver(tempAmbiguity());
  const syn = r.getSynonyms("Bacteroides vulgatus");
  assert.strictEqual(syn.accepted_name, "Phocaeicola vulgatus");
  assert.deepStrictEqual(syn.all_names.sort(),
                         ["Bacteroides vulgatus", "Phocaeicola vulgatus"]);
  r.close();
});

test("the accepted name keeps its display form when a cluster was not packed", () => {
  // A cluster of one is not packed -- there is nothing to enumerate -- so the
  // accepted name has to come from the row that pointed at it. Without that it
  // would come back lowercased, since the key is folded.
  const { Resolver } = require("../src/resolver.js");
  const r = new Resolver(tempAmbiguity({ enumeration: false }));
  const out = r.checkName("Bacteroides vulgatus");
  assert.strictEqual(out.accepted_name, "Phocaeicola vulgatus");
  r.close();
});

test("a build without enumeration is recorded, not hidden", () => {
  const store = tempAmbiguity({ enumeration: false });
  assert.strictEqual(store.hasEnumeration, false);
  store.close();
});
