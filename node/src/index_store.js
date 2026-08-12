"use strict";
/*
 * The field index: locating it, opening it, and keeping it current.
 *
 * Three rules govern updates here, and the first two were learned the hard way
 * during development rather than reasoned out in advance.
 *
 *  1. Never block a tool call on the network. check_name is advertised as
 *     costing about 2 ms, and a description that promises cheapness is the only
 *     reason an agent calls it on every organism mention. One network round
 *     trip inside a lookup breaks the premise of the entire staged design.
 *
 *  2. Never require the user to quit anything. A running server holds the index
 *     open, and Windows will not let you replace an open SQLite file -- an
 *     actual `WinError 32` during development. So a new index downloads to a
 *     staging file and is swapped in on the NEXT start, when nothing holds a
 *     handle. The user restarts Claude Desktop eventually anyway.
 *
 *  3. A stale index must not be silent. Every response already carries the
 *     release string and nobody reads those. Answering as of a date the user
 *     has forgotten is the subject of this entire project, so staleness gets
 *     one short line in the tool response -- the only channel that reaches the
 *     person actually relying on the answer.
 */

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const zlib = require("node:zlib");
const { pipeline } = require("node:stream/promises");

const STALE_AFTER_DAYS = 120;
const CHECK_EVERY_DAYS = 14;
const DEFAULT_MANIFEST =
  process.env.BINOMEN_INDEX_MANIFEST ||
  "https://github.com/Nebraskinator/binomen/releases/latest/download/manifest.json";

/**
 * Where the index lives.
 *
 * Deliberately NOT inside the extension directory: installing an extension
 * update replaces that directory, and re-downloading 46 MB for a bug fix is
 * not acceptable. A user data directory persists across extension updates.
 */
function dataDir() {
  if (process.env.BINOMEN_DATA_DIR) return process.env.BINOMEN_DATA_DIR;
  if (process.platform === "win32") {
    return path.join(process.env.LOCALAPPDATA || os.homedir(), "binomen");
  }
  if (process.platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", "binomen");
  }
  return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"),
                   "binomen");
}

const INDEX_NAME = "binomen-field.sqlite";
const indexPath = () => path.join(dataDir(), INDEX_NAME);
const stagedPath = () => `${indexPath()}.staged`;
const statePath = () => path.join(dataDir(), "update-state.json");

function readState() {
  try {
    return JSON.parse(fs.readFileSync(statePath(), "utf8"));
  } catch {
    return {};
  }
}

function writeState(patch) {
  try {
    fs.mkdirSync(dataDir(), { recursive: true });
    fs.writeFileSync(statePath(), JSON.stringify({ ...readState(), ...patch }, null, 2));
  } catch { /* state is an optimisation; losing it costs one extra check */ }
}

function sha256File(file) {
  const h = crypto.createHash("sha256");
  h.update(fs.readFileSync(file));
  return h.digest("hex");
}

/**
 * Promote a verified staged download, if one is waiting.
 *
 * Runs at startup, before anything opens the database. This is the half of the
 * update that the previous session could not perform because it held the file
 * open.
 */
function promoteStaged(log = () => {}) {
  const staged = stagedPath();
  if (!fs.existsSync(staged)) return false;
  const state = readState();
  const want = state.staged_sha256;
  try {
    if (want && sha256File(staged) !== want) {
      log("staged index failed its checksum; discarding it");
      fs.unlinkSync(staged);
      writeState({ staged_sha256: null, staged_release: null });
      return false;
    }
    // Sidecars from the outgoing database must go too: a stale -wal can be
    // recovered into the replacement and resurrect an older schema.
    for (const suffix of ["", "-wal", "-shm", "-journal"]) {
      const p = indexPath() + suffix;
      if (fs.existsSync(p)) fs.unlinkSync(p);
    }
    fs.renameSync(staged, indexPath());
    log(`installed index ${state.staged_release || "(unknown release)"}`);
    writeState({ staged_sha256: null, staged_release: null,
                 installed_release: state.staged_release || null });
    return true;
  } catch (e) {
    log(`could not install the staged index: ${e.message}`);
    return false;
  }
}

async function fetchJson(url) {
  const r = await fetch(url, { redirect: "follow" });
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
  return r.json();
}

/**
 * Download an artifact to the staging file and verify it twice.
 *
 * The transfer is checked against `sha256`, and the decompressed database
 * against `uncompressed_sha256`, because a valid gzip can contain the wrong
 * database and pass the first check. An index is a set of assertions about
 * what organisms are called; one checksum is not enough.
 */
async function stageDownload(manifestUrl, entry, log) {
  const base = manifestUrl.slice(0, manifestUrl.lastIndexOf("/"));
  const url = `${base}/${entry.file}`;
  fs.mkdirSync(dataDir(), { recursive: true });
  const tmp = `${stagedPath()}.part`;

  log(`downloading ${entry.file} (${(entry.bytes / 1e6).toFixed(0)} MB)`);
  const r = await fetch(url, { redirect: "follow" });
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
  await pipeline(r.body, fs.createWriteStream(tmp));

  if (sha256File(tmp) !== entry.sha256) {
    fs.unlinkSync(tmp);
    throw new Error("download failed its checksum");
  }
  if (entry.compression === "gzip") {
    const raw = `${tmp}.raw`;
    await pipeline(fs.createReadStream(tmp), zlib.createGunzip(),
                   fs.createWriteStream(raw));
    fs.unlinkSync(tmp);
    if (entry.uncompressed_sha256 && sha256File(raw) !== entry.uncompressed_sha256) {
      fs.unlinkSync(raw);
      throw new Error("decompressed index failed its checksum");
    }
    fs.renameSync(raw, stagedPath());
  } else {
    fs.renameSync(tmp, stagedPath());
  }
  return sha256File(stagedPath());
}

/**
 * Check for a newer index, in the background, failing silently.
 *
 * No network is a normal state on a laptop, and an extension that complains
 * about it is worse than one that quietly serves a slightly older index.
 * Staleness is reported separately, because that is a fact about the answers.
 */
async function checkForUpdate(currentRelease, log = () => {}, { force = false } = {}) {
  const state = readState();
  const last = state.last_check ? Date.parse(state.last_check) : 0;
  const ageDays = (Date.now() - last) / 86400000;
  if (!force && ageDays < CHECK_EVERY_DAYS) return null;
  writeState({ last_check: new Date().toISOString() });

  try {
    const manifestUrl = DEFAULT_MANIFEST;
    const manifest = await fetchJson(manifestUrl);
    const release = manifest.taxdump_release;
    if (!release || release === currentRelease) return null;
    if (state.staged_release === release) return release;   // already waiting
    const entry = (manifest.artifacts || {}).field;
    if (!entry) return null;

    log(`a newer index is available (${release}); downloading in the background`);
    const sha = await stageDownload(manifestUrl, entry, log);
    writeState({ staged_sha256: sha, staged_release: release });
    log(`index ${release} staged; it will be used next time Claude Desktop starts`);
    return release;
  } catch (e) {
    log(`update check skipped: ${e.message}`);
    return null;
  }
}

/** Days since a `taxdump-YYYY-MM-DD` release string, or null. */
function releaseAgeDays(release) {
  const m = /^taxdump-(\d{4}-\d{2}-\d{2})$/.exec(release || "");
  if (!m) return null;
  return Math.floor((Date.now() - Date.parse(m[1])) / 86400000);
}

module.exports = {
  dataDir, indexPath, stagedPath, statePath, readState, writeState,
  promoteStaged, checkForUpdate, releaseAgeDays, stageDownload,
  STALE_AFTER_DAYS, CHECK_EVERY_DAYS, DEFAULT_MANIFEST, INDEX_NAME,
};
