#!/usr/bin/env node
"use strict";
/*
 * binomen MCP server, field edition.
 *
 * Hand-rolled JSON-RPC rather than the MCP SDK, for one reason: with no
 * dependencies the whole extension is a ~60 KB .mcpb that needs no npm install,
 * no bundling step and no node_modules. Claude Desktop ships Node, so a user
 * double-clicks a file and it works. MCP stdio is newline-delimited JSON-RPC
 * 2.0; the surface used here is small and stable.
 *
 * stdout is the protocol. Everything diagnostic goes to stderr, which the
 * client surfaces in its logs.
 */

const fs = require("node:fs");
const path = require("node:path");

const DESCRIPTIONS = require("./tool_descriptions.js");
const store = require("./index_store.js");

const PROTOCOL_VERSION = "2025-06-18";
const NAME_ARG = { type: "object", properties: { name: { type: "string" } },
                   required: ["name"] };

const TOOLS = [
  { name: "check_name", description: DESCRIPTIONS.check_name, inputSchema: NAME_ARG },
  { name: "resolve_name", description: DESCRIPTIONS.resolve_name, inputSchema: NAME_ARG },
  { name: "get_synonyms", description: DESCRIPTIONS.get_synonyms, inputSchema: NAME_ARG },
  { name: "expand_query", description: DESCRIPTIONS.expand_query, inputSchema: NAME_ARG },
];

const log = (m) => process.stderr.write(`[binomen] ${m}\n`);
const send = (msg) => process.stdout.write(`${JSON.stringify(msg)}\n`);
const reply = (id, result) => {
  send({ jsonrpc: "2.0", id, result });
  if (result && result.serverInfo) log("initialize answered");
};
const replyError = (id, code, message) => send({ jsonrpc: "2.0", id, error: { code, message } });
const asText = (obj) => ({ content: [{ type: "text", text: JSON.stringify(obj) }] });

let resolver = null;
let downloadState = "idle";   // idle | downloading | failed

/**
 * Open the index, installing a staged update first.
 *
 * Promotion happens here, at startup, because a running server holds the
 * database open and Windows will not let an open SQLite file be replaced. The
 * previous session downloaded it; this one installs it.
 */
function openIndex() {
  store.promoteStaged(log);
  const file = store.indexPath();
  if (!fs.existsSync(file)) return null;
  try {
    const { Resolver } = require("./resolver.js");
    const r = new Resolver(file);
    log(`index ${r.release} (${path.basename(file)})`);
    return r;
  } catch (e) {
    log(`could not open ${file}: ${e.message}`);
    return null;
  }
}

/** First run: no index yet. Fetch it in the background; tools say so meanwhile. */
async function firstRunDownload() {
  if (downloadState === "downloading") return;
  downloadState = "downloading";
  try {
    log("no index installed; downloading the field edition (about 46 MB)");
    const manifest = await (await fetch(store.DEFAULT_MANIFEST,
                                        { redirect: "follow" })).json();
    const entry = (manifest.artifacts || {}).field;
    if (!entry) throw new Error("the manifest has no 'field' artifact");
    const sha = await store.stageDownload(store.DEFAULT_MANIFEST, entry, log);
    store.writeState({ staged_sha256: sha, staged_release: manifest.taxdump_release });
    if (store.promoteStaged(log)) {
      resolver = openIndex();
      downloadState = resolver ? "idle" : "failed";
    }
  } catch (e) {
    downloadState = "failed";
    log(`index download failed: ${e.message}`);
  }
}

function notReady() {
  if (downloadState === "downloading") {
    return {
      status: "index_downloading",
      message: "binomen is downloading its name index (about 46 MB). This happens once. " +
               "Try again shortly, or restart Claude Desktop when it finishes.",
      do_not: "Do not answer name questions from memory in the meantime -- say the name " +
              "could not be verified.",
    };
  }
  return {
    status: "index_unavailable",
    message: "binomen has no name index installed and could not download one. Check the " +
             "network connection, then restart Claude Desktop.",
    do_not: "Do not substitute a remembered name for a failed lookup.",
  };
}

function callTool(name, args) {
  if (!resolver) return notReady();
  const arg = String((args && args.name) || "");
  try {
    switch (name) {
      case "check_name": return resolver.checkName(arg);
      case "resolve_name": return resolver.resolveName(arg);
      case "get_synonyms": return resolver.getSynonyms(arg);
      case "expand_query": return resolver.expandQuery(arg);
      default: return null;
    }
  } catch (e) {
    // An agent that sees a crash falls back to its own memory, which is the
    // outcome this tool exists to prevent. Give it something actionable.
    return { error: `${e.name}: ${e.message}`,
             do_not: "Do not substitute a remembered name for a failed lookup." };
  }
}

function handle(msg) {
  const { id, method, params } = msg;
  log(`<- ${method}${id !== undefined ? ` (id ${id})` : ""}`);
  switch (method) {
    case "initialize":
      reply(id, {
        protocolVersion: (params && params.protocolVersion) || PROTOCOL_VERSION,
        capabilities: { tools: {} },
        serverInfo: { name: "binomen", version: "0.2.2" },
      });
      return;
    case "notifications/initialized":
    case "initialized":
      return;
    case "tools/list":
      reply(id, { tools: TOOLS });
      return;
    case "tools/call": {
      const out = callTool(params && params.name, params && params.arguments);
      if (out === null) replyError(id, -32602, `unknown tool: ${params && params.name}`);
      else reply(id, asText(out));
      return;
    }
    case "ping":
      reply(id, {});
      return;
    default:
      if (id !== undefined && id !== null) {
        replyError(id, -32601, `method not found: ${method}`);
      }
  }
}

function main() {
  log(`node ${process.version} on ${process.platform}`);
  log(`data dir ${store.dataDir()}`);
  resolver = openIndex();

  // Read stdin FIRST, before anything that touches the network or a large
  // file. The client sends `initialize` immediately and gives up after 60
  // seconds; a server that is busy fetching a 46 MB index before it starts
  // listening looks, from the outside, exactly like a server that crashed.
  let buffer = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => {
    buffer += chunk;
    let nl;
    while ((nl = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      try { handle(JSON.parse(line)); } catch (e) { log(`bad message: ${e.message}`); }
    }
  });
  process.stdin.on("end", () => process.exit(0));

  // Only now, once the transport is live, start anything slow. setImmediate
  // puts it behind any message already queued.
  setImmediate(() => {
    if (!resolver) {
      firstRunDownload();
    } else {
      // Never inside a tool call either: check_name is advertised as costing
      // 2 ms, and that promise is the only reason an agent calls it freely.
      store.checkForUpdate(resolver.release, log).catch(() => {});
    }
  });
}

if (require.main === module) main();
module.exports = { handle, TOOLS, openIndex };
