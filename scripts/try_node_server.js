#!/usr/bin/env node
"use strict";
/*
 * Run the Node MCP server the way a client does, and report what happens.
 *
 * Written because Claude Desktop's extension log shows only its own messages:
 * it records "Message from client: initialize" and then a 60-second timeout,
 * which is consistent with the server never receiving it, never answering, or
 * answering something the client rejected. Those need different fixes and the
 * log cannot tell them apart.
 *
 *   node scripts/try_node_server.js                 # the working copy
 *   node scripts/try_node_server.js <path/to/index.js>   # an installed extension
 */

const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

const target = process.argv[2] ||
  path.join(__dirname, "..", "node", "src", "server.js");

if (!fs.existsSync(target)) {
  console.error(`not found: ${target}`);
  process.exit(1);
}

console.log(`server   ${target}`);
console.log(`node     ${process.version} (${process.platform})`);
console.log("");

const t0 = Date.now();
const child = spawn(process.execPath, ["--no-warnings", target], {
  stdio: ["pipe", "pipe", "pipe"],
});

let sawInitialize = false;
let buffer = "";

child.stdout.on("data", (d) => {
  buffer += d;
  let nl;
  while ((nl = buffer.indexOf("\n")) !== -1) {
    const line = buffer.slice(0, nl).trim();
    buffer = buffer.slice(nl + 1);
    if (!line) continue;
    const ms = Date.now() - t0;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      console.log(`  [${ms}ms] STDOUT IS NOT JSON -- this would break the protocol:`);
      console.log(`          ${line.slice(0, 200)}`);
      continue;
    }
    if (msg.result && msg.result.serverInfo) {
      sawInitialize = true;
      console.log(`  [${ms}ms] initialize answered: ${JSON.stringify(msg.result.serverInfo)}`);
    } else if (msg.result && msg.result.tools) {
      console.log(`  [${ms}ms] tools/list answered: ` +
                  msg.result.tools.map((t) => t.name).join(", "));
    } else if (msg.result && msg.result.content) {
      const text = msg.result.content[0].text;
      console.log(`  [${ms}ms] tools/call answered: ${text.slice(0, 160)}`);
    } else if (msg.error) {
      console.log(`  [${ms}ms] error: ${JSON.stringify(msg.error)}`);
    } else {
      console.log(`  [${ms}ms] ${line.slice(0, 160)}`);
    }
  }
});

child.stderr.on("data", (d) => {
  for (const line of String(d).split("\n")) {
    if (line.trim()) console.log(`  [log] ${line}`);
  }
});

child.on("exit", (code, signal) => {
  console.log(`\n  server exited: code=${code} signal=${signal}`);
});

const send = (obj) => child.stdin.write(`${JSON.stringify(obj)}\n`);

send({ jsonrpc: "2.0", id: 0, method: "initialize",
       params: { protocolVersion: "2025-06-18", capabilities: {},
                 clientInfo: { name: "try_node_server", version: "1" } } });

setTimeout(() => send({ jsonrpc: "2.0", method: "notifications/initialized" }), 300);
setTimeout(() => send({ jsonrpc: "2.0", id: 1, method: "tools/list" }), 600);
setTimeout(() => send({ jsonrpc: "2.0", id: 2, method: "tools/call",
                        params: { name: "check_name",
                                  arguments: { name: "Clostridium difficile" } } }), 900);

setTimeout(() => {
  console.log("");
  if (!sawInitialize) {
    console.log("  VERDICT: the server never answered initialize.");
    console.log("  Anything printed above under [log] shows how far it got.");
  } else {
    console.log("  VERDICT: the server works when run directly.");
    console.log("  If Claude Desktop still cannot attach, the problem is between");
    console.log("  the two, not in the server.");
  }
  child.kill();
  process.exit(0);
}, 8000);
