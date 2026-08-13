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
