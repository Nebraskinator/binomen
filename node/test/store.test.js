"use strict";
/* Update mechanics. Two of these encode failures hit during development. */

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");

function withDataDir(fn) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "binomen-store-"));
  const prev = process.env.BINOMEN_DATA_DIR;
  process.env.BINOMEN_DATA_DIR = dir;
  delete require.cache[require.resolve("../src/index_store.js")];
  const store = require("../src/index_store.js");
  try { return fn(store, dir); } finally {
    if (prev === undefined) delete process.env.BINOMEN_DATA_DIR;
    else process.env.BINOMEN_DATA_DIR = prev;
  }
}

const sha = (f) => crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex");

test("the index lives outside the extension directory", () => {
  withDataDir((store, dir) => {
    // Extension updates replace the extension directory; a 46 MB re-download
    // on every bug-fix release would be unacceptable.
    assert.strictEqual(store.dataDir(), dir);
    assert.ok(store.indexPath().endsWith("binomen-field.sqlite"));
  });
});

test("a verified staged index is promoted at startup", () => {
  withDataDir((store, dir) => {
    fs.writeFileSync(store.indexPath(), "old index");
    fs.writeFileSync(store.stagedPath(), "new index");
    store.writeState({ staged_sha256: sha(store.stagedPath()),
                       staged_release: "taxdump-2026-08-11" });
    assert.strictEqual(store.promoteStaged(), true);
    assert.strictEqual(fs.readFileSync(store.indexPath(), "utf8"), "new index");
    assert.ok(!fs.existsSync(store.stagedPath()));
    assert.strictEqual(store.readState().installed_release, "taxdump-2026-08-11");
  });
});

test("a corrupt staged index is discarded, not installed", () => {
  withDataDir((store) => {
    fs.writeFileSync(store.indexPath(), "good index");
    fs.writeFileSync(store.stagedPath(), "corrupted");
    store.writeState({ staged_sha256: "0".repeat(64), staged_release: "x" });
    assert.strictEqual(store.promoteStaged(), false);
    assert.strictEqual(fs.readFileSync(store.indexPath(), "utf8"), "good index");
    assert.ok(!fs.existsSync(store.stagedPath()));
  });
});

test("promotion clears sidecars from the outgoing database", () => {
  // A stale -wal can be recovered into the replacement and resurrect an older
  // schema. Cost an afternoon of misdiagnosis once already.
  withDataDir((store) => {
    fs.writeFileSync(store.indexPath(), "old");
    fs.writeFileSync(`${store.indexPath()}-wal`, "orphaned journal");
    fs.writeFileSync(store.stagedPath(), "new");
    store.writeState({ staged_sha256: sha(store.stagedPath()), staged_release: "r" });
    store.promoteStaged();
    assert.ok(!fs.existsSync(`${store.indexPath()}-wal`));
  });
});

test("nothing staged is a no-op", () => {
  withDataDir((store) => assert.strictEqual(store.promoteStaged(), false));
});

test("the update check is rate limited", async () => {
  await withDataDir(async (store) => {
    store.writeState({ last_check: new Date().toISOString() });
    // Recent check: must not touch the network, so an unreachable manifest is
    // irrelevant rather than an error.
    assert.strictEqual(await store.checkForUpdate("taxdump-2026-08-11"), null);
  });
});

test("a failed update check is silent", async () => {
  await withDataDir(async (store) => {
    process.env.BINOMEN_INDEX_MANIFEST = "http://127.0.0.1:9/manifest.json";
    delete require.cache[require.resolve("../src/index_store.js")];
    const s2 = require("../src/index_store.js");
    // No network is a normal state on a laptop. Complaining is worse than
    // quietly serving a slightly older index.
    assert.strictEqual(await s2.checkForUpdate("x", () => {}, { force: true }), null);
    delete process.env.BINOMEN_INDEX_MANIFEST;
  });
});

test("release age is computed from the release string", () => {
  withDataDir((store) => {
    assert.strictEqual(store.releaseAgeDays("fixture-v1"), null);
    assert.ok(store.releaseAgeDays("taxdump-2019-01-01") > 2000);
  });
});
