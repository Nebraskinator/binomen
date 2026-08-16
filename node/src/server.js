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

const TEXT = require("./tool_descriptions.js");
const DESCRIPTIONS = TEXT.descriptions();
const store = require("./index_store.js");

const PROTOCOL_VERSION = "2025-06-18";

/*
 * Versions this server will agree to speak.
 *
 * The spec is explicit: if the server supports the requested version it MUST
 * echo it, otherwise it MUST answer with one it does support. The previous code
 * echoed whatever arrived, which claimed support for anything a client asked
 * for -- including versions that do not exist. Harmless against today's client,
 * wrong the moment one negotiates honestly.
 *
 * All three are listed because the surface this server actually uses --
 * initialize, tools/list, tools/call with text content blocks -- is identical
 * across them. Nothing here depends on a feature added after 2024-11-05.
 */
const SUPPORTED_PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"];

/*
 * Which wording this process is running.
 *
 * Both are treatments, not features, and both are selected by environment
 * variable so a comparison can be run without rebuilding:
 *
 *   BINOMEN_DESCRIPTIONS   terse (default) | broad
 *   BINOMEN_INSTRUCTIONS   terse (default) | conditional | unconditional | off
 *
 * `off` exists because the description conditions must run without an
 * instruction present, or they measure wording in the presence of something
 * stronger. An unrecognised value falls back to the shipping default rather
 * than sending nothing: silently dropping a treatment because of a typo would
 * produce a clean negative result for a condition that never ran.
 */
const instructionsText = TEXT.instructions;

const NAME_ARG = {
  type: "object",
  properties: {
    name: {
      type: "string",
      description: "A scientific name: binomial, genus, or a strain designation " +
                   "such as 'Clostridium difficile 630'. Abbreviated genus forms " +
                   "like 'C. difficile' are accepted.",
    },
  },
  required: ["name"],
};

/*
 * Every tool here reads a local SQLite file and returns what it found.
 *
 * The annotations say so in the protocol's own vocabulary. They are hints, and
 * the spec warns clients not to trust them from untrusted servers, so this may
 * or may not change how a client gates these calls -- that is the client's
 * decision to make and ours to state accurately. What is certain is that
 * declaring nothing gives a client no basis to distinguish a name lookup from a
 * tool that deletes files, and it currently has to prompt for both alike.
 *
 * readOnly + non-destructive + idempotent + closed-world is the literal truth
 * here: no writes, no network at query time, same answer for the same input
 * against a pinned index release.
 */
const READ_ONLY = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: false,
};

const TOOLS = [
  { name: "check_name", title: "Check a name", description: DESCRIPTIONS.check_name,
    inputSchema: NAME_ARG, annotations: { title: "Check a name", ...READ_ONLY } },
  { name: "resolve_name", title: "Resolve to the accepted name",
    description: DESCRIPTIONS.resolve_name,
    inputSchema: NAME_ARG,
    annotations: { title: "Resolve to the accepted name", ...READ_ONLY } },
  { name: "get_synonyms", title: "List every recorded name",
    description: DESCRIPTIONS.get_synonyms,
    inputSchema: NAME_ARG,
    annotations: { title: "List every recorded name", ...READ_ONLY } },
  { name: "expand_query", title: "Build literature search terms",
    description: DESCRIPTIONS.expand_query,
    inputSchema: NAME_ARG,
    annotations: { title: "Build literature search terms", ...READ_ONLY } },
];

/*
 * stderr is not a reliable diagnostic channel under every host.
 *
 * Claude Desktop's built-in-Node path forks the server as an Electron
 * UtilityProcess, whose documented stdio options are `pipe`, `ignore` and
 * `inherit` -- and the default, `inherit`, sends the child's stderr to the
 * app's own stream rather than to the per-extension log file. So `[binomen]`
 * lines that appear under system Node are simply absent under built-in Node,
 * whether the server is healthy or dead. Their absence proves nothing, which
 * cost a whole round of diagnosis to establish.
 *
 * `boot()` therefore appends to a file that survives either way. It is
 * best-effort by construction: a logger that can throw is a liability in a
 * process whose job is to answer within 60 seconds.
 */
const log = (m) => { try { process.stderr.write(`[binomen] ${m}\n`); } catch { /* no stderr */ } };

let bootLogPath = null;
function boot(m) {
  try {
    if (bootLogPath === null) {
      bootLogPath = path.join(store.dataDir(), "boot.log");
      fs.mkdirSync(store.dataDir(), { recursive: true });
      // One file, not a growing one: the last start is the interesting one.
      fs.writeFileSync(bootLogPath,
        `--- ${new Date().toISOString()} pid ${process.pid} ---\n` +
        `node ${process.version} ${process.platform} ${process.arch}\n` +
        `execPath ${process.execPath}\n` +
        `parentPort ${typeof process.parentPort}\n` +
        // Recorded because a false value here is what kept the server from
        // ever starting, and a future host could do the same thing again.
        `require.main===module ${require.main === module}\n` +
        `require.main ${require.main ? require.main.filename : "(undefined)"}\n` +
        `stdin isTTY=${process.stdin.isTTY} readable=${process.stdin.readable}\n` +
        `cwd ${process.cwd()}\n` +
        `descriptions ${process.env.BINOMEN_DESCRIPTIONS || 'terse (default)'}\n` +
        `instructions ${process.env.BINOMEN_INSTRUCTIONS || 'terse (default)'}\n`);
    }
    fs.appendFileSync(bootLogPath, `${Date.now() % 100000} ${m}\n`);
  } catch { /* diagnostics must never be the thing that breaks the server */ }
}
const send = (msg) => process.stdout.write(`${JSON.stringify(msg)}\n`);
const reply = (id, result) => {
  send({ jsonrpc: "2.0", id, result });
  if (result && result.serverInfo) log("initialize answered");
};
const replyError = (id, code, message) => send({ jsonrpc: "2.0", id, error: { code, message } });
const asText = (obj) => ({ content: [{ type: "text", text: JSON.stringify(obj) }] });

let resolver = null;
let downloadState = "idle";   // idle | downloading | failed
// Opening the index now happens after the transport is live, so there is a
// real window -- short, but real -- where the server is answering and the
// index is not yet open. That is a third state, and reporting it as
// "unavailable" would tell an agent to give up on a lookup that is about to
// start working.
let indexState = "opening";   // opening | ready | absent

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
      indexState = resolver ? "ready" : "absent";
      downloadState = resolver ? "idle" : "failed";
    }
  } catch (e) {
    downloadState = "failed";
    log(`index download failed: ${e.message}`);
  }
}

function notReady() {
  if (indexState === "opening") {
    return {
      status: "index_opening",
      message: "binomen is still opening its name index. This takes a moment at startup. " +
               "Call again.",
      do_not: "Do not answer name questions from memory in the meantime -- say the name " +
              "could not be verified yet.",
    };
  }
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

/* Sentinels, so callTool can distinguish "no such tool" from "bad argument"
 * without either of them looking like a result. */
const UNKNOWN_TOOL = Symbol("unknown tool");
const BAD_ARGUMENT = Symbol("bad argument");

const HANDLERS = {
  check_name: (r, a) => r.checkName(a),
  resolve_name: (r, a) => r.resolveName(a),
  get_synonyms: (r, a) => r.getSynonyms(a),
  expand_query: (r, a) => r.expandQuery(a),
};

function callTool(name, args) {
  // Order matters, and getting it wrong is how a caller learns the wrong thing.
  //
  // A first draft checked the resolver before the tool name, so calling a tool
  // that does not exist reported `index_unavailable` whenever the index had not
  // finished opening -- telling the caller to retry something that will never
  // work. Identity of the request first, then its arguments, then whether we
  // are in a state to serve it.
  if (!Object.prototype.hasOwnProperty.call(HANDLERS, name)) return UNKNOWN_TOOL;

  // "Servers MUST validate all tool inputs." The previous line was
  // `String((args && args.name) || "")`, which coerces rather than validates: a
  // missing name became the empty string and was looked up as if it were a
  // question. An empty lookup returning "unknown" is a wrong answer to a
  // question nobody asked, and in this project a wrong "unknown" is exactly the
  // failure that sends an agent back to its own memory.
  if (!args || typeof args.name !== "string" || args.name.trim() === "") {
    return BAD_ARGUMENT;
  }

  if (!resolver) return notReady();
  const arg = args.name.trim();
  try {
    return HANDLERS[name](resolver, arg);
  } catch (e) {
    // An agent that sees a crash falls back to its own memory, which is the
    // outcome this tool exists to prevent. Give it something actionable.
    //
    // Reported with isError so the model can see the failure and self-correct,
    // which is what the spec asks for and what the `do_not` line depends on:
    // a failure dressed as a success is the one an agent quietly papers over.
    return { __isError: true,
             error: `${e.name}: ${e.message}`,
             do_not: "Do not substitute a remembered name for a failed lookup." };
  }
}

function handle(msg) {
  const { id, method, params } = msg;
  log(`<- ${method}${id !== undefined ? ` (id ${id})` : ""}`);
  switch (method) {
    case "initialize": {
      // Echo the requested version if it is one we speak; otherwise answer with
      // the newest we do, and let the client decide whether to continue.
      const asked = params && params.protocolVersion;
      const agreed = SUPPORTED_PROTOCOL_VERSIONS.includes(asked) ? asked : PROTOCOL_VERSION;
      reply(id, {
        protocolVersion: agreed,
        capabilities: { tools: {} },
        serverInfo: { name: "binomen", title: "binomen — biological name checker",
                      version: "0.2.7" },
        ...(() => { const t = instructionsText(); return t ? { instructions: t } : {}; })(),
      });
      return;
    }
    case "notifications/initialized":
    case "initialized":
      return;
    case "tools/list":
      reply(id, { tools: TOOLS });
      return;
    case "tools/call": {
      const out = callTool(params && params.name, params && params.arguments);
      // Protocol errors for "cannot find the tool" and "the request was
      // malformed"; in-result errors for anything the tool itself hit. The spec
      // draws that line, and it is the right one: the model can see and correct
      // the second kind, and cannot do anything useful about the first.
      if (out === UNKNOWN_TOOL) {
        replyError(id, -32602, `unknown tool: ${params && params.name}`);
      } else if (out === BAD_ARGUMENT) {
        replyError(id, -32602,
          "the 'name' argument is required and must be a non-empty string, " +
          "for example { \"name\": \"Clostridium difficile\" }");
      } else if (out && out.__isError) {
        const { __isError, ...body } = out;
        reply(id, { ...asText(body), isError: true });
      } else {
        reply(id, asText(out));
      }
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
  // ------------------------------------------------------------------
  // Transport first. Nothing above this line may throw, block, or log.
  //
  // The comment below used to sit here while `openIndex()` ran above it --
  // the rule was written down and then not followed. openIndex() loads a
  // native module (node:sqlite), may checksum a 123 MB file, and opens a
  // database. If any of that throws or blocks before a listener exists, the
  // client's `initialize` lands on a process that will never answer, and the
  // only symptom is a 60-second timeout.
  //
  // Found by diffing this file against the throwaway probe extension, which
  // attached fine on the same host: the probe registers its listener first
  // and does nothing else at startup.
  //
  // Read stdin FIRST, before anything that touches the network or a large
  // file. The client sends `initialize` immediately and gives up after 60
  // seconds; a server that is busy fetching a 46 MB index before it starts
  // listening looks, from the outside, exactly like a server that crashed.
  // ------------------------------------------------------------------
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

  boot("transport live");

  // Only now, once the transport is live, do anything that can fail. Wrapped
  // because a server that cannot open its index must still answer initialize
  // and say so -- an unhandled throw here is the failure this whole ordering
  // exists to prevent.
  setImmediate(() => {
    try {
      boot(`node ${process.version} on ${process.platform}`);
      log(`node ${process.version} on ${process.platform}`);
      log(`data dir ${store.dataDir()}`);
      boot(`data dir ${store.dataDir()}`);
      resolver = openIndex();
      boot(resolver ? `index open: ${resolver.release}` : "no index opened");
      indexState = resolver ? "ready" : "absent";
    } catch (e) {
      indexState = "absent";
      boot(`startup failed: ${e && e.stack ? e.stack : e}`);
      log(`startup failed: ${e && e.message ? e.message : e}`);
    }

    if (!resolver) {
      firstRunDownload();
    } else {
      // Never inside a tool call either: check_name is advertised as costing
      // 2 ms, and that promise is the only reason an agent calls it freely.
      store.checkForUpdate(resolver.release, log).catch(() => {});
    }
  });
}

// A crash after the transport is live should be recorded somewhere readable,
// not just vanish into a stderr stream the host may have set to `inherit`.
process.on("uncaughtException", (e) => {
  boot(`uncaughtException: ${e && e.stack ? e.stack : e}`);
  log(`uncaughtException: ${e && e.message ? e.message : e}`);
});
process.on("unhandledRejection", (e) => {
  boot(`unhandledRejection: ${e && e.stack ? e.stack : e}`);
});

/*
 * Start unconditionally. This line was `if (require.main === module) main();`
 * and that guard was the blocker.
 *
 * Claude Desktop's built-in-Node path forks extensions with Electron's
 * `utilityProcess.fork()`, which loads the entry module through the host's own
 * bootstrap rather than as a Node CLI entry point. `require.main === module` is
 * therefore not reliably true for the file that IS the entry point, so `main()`
 * was never called: no stdin listener, no boot log, no stderr, no reply, and a
 * 60-second client timeout that looked like a crashed or hung server. Under
 * system Node the same file works, because `node server/index.js` makes it
 * `require.main` in the ordinary way -- which is why every direct test passed
 * and only the packaged install failed.
 *
 * The guard was also protecting nothing: no test and no script requires this
 * file as a module. It was reflex, carried over from a shape that suited a
 * library. An MCP stdio server is an entry point, not a library, and it should
 * start when it is loaded.
 *
 * Found by installing a capability probe alongside the real extension and
 * comparing them in the same restart. The probe attached in 15 ms; this file
 * did not answer at all. The probe registers its listener as unconditional
 * top-level code, and that was the last structural difference left between
 * them.
 */
main();

// Exported for anything that wants to drive the protocol in-process. Nothing
// does today; the server no longer depends on that staying true.
module.exports = { handle, TOOLS, openIndex, main };
