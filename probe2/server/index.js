#!/usr/bin/env node
"use strict";
/*
 * binomen capability probe.
 *
 * Built after three rounds of diagnosis produced ambiguous evidence. The
 * pattern in each round was the same: the diagnostic channel failed silently,
 * so absence of output could not be distinguished from absence of a problem.
 *
 *   - stderr under the built-in-Node path goes to `inherit`, so the server's
 *     own log lines never reach the per-extension log file. Their absence
 *     proved nothing.
 *   - a boot.log written to LOCALAPPDATA never appeared, but the writer
 *     swallows its own errors, so that proved nothing either.
 *
 * This probe inverts the approach. It is built to ATTACH FIRST and report
 * afterwards, through the one channel already proven to work on this host: an
 * MCP tool response. The original throwaway probe attached fine under built-in
 * Node, so the shape it used -- register the stdin listener before doing
 * anything else, and do nothing risky at startup -- is known-good on this
 * platform. Everything binomen does that the probe did not is tested here, one
 * item at a time, inside try/catch, after the handshake is complete.
 *
 * Rules this file follows, and the reason for each:
 *
 *   1. No require() at module scope except the entry itself. A throw at load
 *      time happens before any listener exists and is indistinguishable from a
 *      crashed process.
 *   2. The stdin listener is the first statement executed.
 *   3. Every probe runs inside try/catch and records its own failure as data.
 *   4. Nothing is logged anywhere that could be swallowed. The report is the
 *      tool response.
 *
 * Install it, restart Claude Desktop with built-in Node ON, and ask Claude to
 * call `probe_capabilities`.
 */

const PROTOCOL_VERSION = "2025-06-18";

const TOOLS = [
  {
    name: "probe_capabilities",
    description:
      "Diagnostic. Reports which runtime capabilities are available to a Claude Desktop " +
      "extension on this host: module loading, node:sqlite, filesystem write access, and " +
      "whether binomen's real index can be opened. Call it when asked to run the binomen " +
      "capability probe.",
    inputSchema: { type: "object", properties: {}, required: [] },
  },
];

function send(msg) {
  try { process.stdout.write(JSON.stringify(msg) + "\n"); } catch { /* nothing to do */ }
}
const reply = (id, result) => send({ jsonrpc: "2.0", id, result });
const replyError = (id, code, message) =>
  send({ jsonrpc: "2.0", id, error: { code, message } });

/* --------------------------------------------------------------------------
 * The probes. Each returns a string; each catches its own failure.
 * `attempt` exists so that a thrown error becomes a recorded result rather
 * than an event that ends the process.
 * ----------------------------------------------------------------------- */

function attempt(fn) {
  try {
    const v = fn();
    return v === undefined ? "ok" : String(v);
  } catch (e) {
    return `FAIL ${(e && e.code) || ""} ${(e && e.message) || e}`.trim();
  }
}

function gather() {
  const out = {};

  // --- what runtime is this, really -------------------------------------
  out.node = process.version;
  out.platform = `${process.platform} ${process.arch}`;
  out.execPath = process.execPath;
  out.pid = String(process.pid);
  out.argv = JSON.stringify(process.argv);

  // The decisive question about the transport. Claude Desktop's main.log says
  // it forks extensions as an Electron UtilityProcess when built-in Node is
  // enabled. A UtilityProcess child has process.parentPort and, per Electron's
  // docs, cannot be given a readable stdin -- yet the earlier probe read stdin
  // successfully, so the host must bridge them. This records which is true.
  out.parentPort = typeof process.parentPort;
  out.stdin_isTTY = String(process.stdin.isTTY);
  out.stdin_readable = String(process.stdin.readable);
  out.electron_version = (process.versions && process.versions.electron) || "(none)";
  out.is_electron_run_as_node = String(process.env.ELECTRON_RUN_AS_NODE || "(unset)");

  // --- module loading ----------------------------------------------------
  // binomen's bundle is five files; the probe that worked was one.
  out.require_sibling = attempt(() => {
    const s = require("./sibling.js");
    return s && s.ok ? "ok" : "loaded but wrong shape";
  });
  out.require_node_fs = attempt(() => { require("node:fs"); });
  out.require_node_crypto = attempt(() => { require("node:crypto"); });
  out.require_node_zlib = attempt(() => { require("node:zlib"); });
  out.require_stream_promises = attempt(() => { require("node:stream/promises"); });
  out.fetch_available = attempt(() => typeof fetch);

  // --- node:sqlite -------------------------------------------------------
  // The stated hypothesis in HANDOFF.md, never actually tested on this host.
  // Requiring the module and constructing a database are different questions:
  // Electron ships its own Node build, and a module can be present while its
  // native backing is not.
  out.require_node_sqlite = attempt(() => {
    const m = require("node:sqlite");
    return m && m.DatabaseSync ? "ok (DatabaseSync present)" : "loaded, no DatabaseSync";
  });
  out.sqlite_open_memory = attempt(() => {
    const { DatabaseSync } = require("node:sqlite");
    const db = new DatabaseSync(":memory:");
    db.exec("CREATE TABLE t (a INTEGER)");
    db.exec("INSERT INTO t VALUES (1)");
    const n = db.prepare("SELECT count(*) AS n FROM t").get().n;
    db.close();
    return `ok (round-tripped ${n} row)`;
  });

  // --- filesystem: where can this process actually see and write ---------
  // If LOCALAPPDATA is redirected inside the MSIX container, the extension has
  // been looking for its index somewhere the installer never wrote to.
  const path = require("node:path");
  const fs = require("node:fs");
  const os = require("node:os");

  out.env_LOCALAPPDATA = process.env.LOCALAPPDATA || "(unset)";
  out.os_homedir = attempt(() => os.homedir());
  out.os_tmpdir = attempt(() => os.tmpdir());
  out.cwd = attempt(() => process.cwd());
  out.dirname = __dirname;

  const dataDir = path.join(process.env.LOCALAPPDATA || os.homedir(), "binomen");
  out.dataDir = dataDir;
  out.dataDir_exists = attempt(() => String(fs.existsSync(dataDir)));
  out.dataDir_writable = attempt(() => {
    fs.mkdirSync(dataDir, { recursive: true });
    const f = path.join(dataDir, "probe-write-test.txt");
    fs.writeFileSync(f, `written by probe pid ${process.pid} at ${new Date().toISOString()}\n`);
    const back = fs.readFileSync(f, "utf8");
    fs.unlinkSync(f);
    return back.length > 0 ? "ok (wrote, read back, removed)" : "wrote but read back empty";
  });
  out.tmpdir_writable = attempt(() => {
    const f = path.join(os.tmpdir(), `binomen-probe-${process.pid}.txt`);
    fs.writeFileSync(f, "x");
    fs.unlinkSync(f);
    return "ok";
  });

  // --- the real index ----------------------------------------------------
  // The operation binomen actually performs at startup, on the actual file.
  const indexFile = path.join(dataDir, "binomen-field.sqlite");
  out.index_path = indexFile;
  out.index_exists = attempt(() => String(fs.existsSync(indexFile)));
  out.index_size = attempt(() =>
    fs.existsSync(indexFile) ? `${(fs.statSync(indexFile).size / 1e6).toFixed(1)} MB` : "n/a");
  out.index_open_readonly = attempt(() => {
    if (!fs.existsSync(indexFile)) return "skipped (no index file)";
    const { DatabaseSync } = require("node:sqlite");
    const db = new DatabaseSync(indexFile, { readOnly: true });
    const rows = db.prepare("SELECT key, value FROM meta").all();
    db.close();
    return `ok (${rows.length} meta rows)`;
  });

  // --- network -----------------------------------------------------------
  // Not awaited: this report must not block on a network the container may
  // not grant. Recorded as "started" and never as a result.
  out.network_note =
    "not tested here -- a probe that awaits the network can hang, which is the " +
    "failure mode being investigated";

  return out;
}

function report() {
  let d;
  try {
    d = gather();
  } catch (e) {
    return `probe itself threw: ${e && e.stack ? e.stack : e}`;
  }
  const width = Math.max(...Object.keys(d).map((k) => k.length));
  const lines = ["binomen capability probe", ""];
  for (const [k, v] of Object.entries(d)) {
    lines.push(`  ${k.padEnd(width)}  ${v}`);
  }
  lines.push("");
  lines.push("Any line beginning FAIL is a capability this host does not give an");
  lines.push("extension. Those are the candidate causes of the attach failure.");
  return lines.join("\n");
}

/* --------------------------------------------------------------------------
 * Protocol. Deliberately identical in shape to the probe that already attached
 * on this host, so that the transport is not a new variable.
 * ----------------------------------------------------------------------- */

function handle(msg) {
  const { id, method, params } = msg;
  switch (method) {
    case "initialize":
      reply(id, {
        protocolVersion: (params && params.protocolVersion) || PROTOCOL_VERSION,
        capabilities: { tools: {} },
        serverInfo: { name: "binomen-probe2", version: "0.0.1" },
      });
      return;
    case "notifications/initialized":
    case "initialized":
      return;
    case "tools/list":
      reply(id, { tools: TOOLS });
      return;
    case "tools/call":
      if (params && params.name === "probe_capabilities") {
        reply(id, { content: [{ type: "text", text: report() }] });
      } else {
        replyError(id, -32602, `unknown tool: ${params && params.name}`);
      }
      return;
    case "ping":
      reply(id, {});
      return;
    default:
      if (id !== undefined && id !== null) {
        replyError(id, -32601, `method not found: ${method}`);
      }
  }
}

// The listener is the first thing that runs. Nothing above this line can throw.
let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let nl;
  while ((nl = buffer.indexOf("\n")) !== -1) {
    const line = buffer.slice(0, nl).trim();
    buffer = buffer.slice(nl + 1);
    if (!line) continue;
    try { handle(JSON.parse(line)); } catch { /* malformed frame; keep reading */ }
  }
});
process.stdin.on("end", () => process.exit(0));

// A crash must not be silent, but it also must not be trusted to any one
// channel. Report it on stdout as a protocol-legal notification too.
process.on("uncaughtException", (e) => {
  send({ jsonrpc: "2.0", method: "notifications/message",
         params: { level: "error", data: `probe uncaughtException: ${e && e.stack}` } });
});
