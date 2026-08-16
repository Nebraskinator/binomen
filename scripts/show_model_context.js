#!/usr/bin/env node
"use strict";
/*
 * Print everything the model actually receives from this extension.
 *
 * Written because three different texts are easy to confuse, and only two of
 * them ever reach a model:
 *
 *   manifest.json tools[].description   -> the human, in the install dialog
 *   tool_descriptions.js                -> the model, EVERY request
 *   instructions                        -> the model, ONCE, in the system prompt
 *
 * Reviewing the wording by opening the source files means reading string
 * concatenations and comments. This assembles the finished text the way the
 * model sees it, so a judgement about the wording is made against the artifact
 * rather than against the code that builds it.
 *
 *   node scripts/show_model_context.js                 # conditional (shipped)
 *   node scripts/show_model_context.js unconditional
 *   node scripts/show_model_context.js off
 *   node scripts/show_model_context.js --md > context.md
 *
 * The --md output is committed as docs/MODEL-CONTEXT-<variant>.md. Those files
 * are generated; `make model-context` rewrites all three.
 */

const path = require("node:path");
const crypto = require("node:crypto");
const T = require(path.join(__dirname, "..", "node", "src", "tool_descriptions.js"));

/*
 * Python's `json.dumps(obj, sort_keys=True)`, reproduced.
 *
 * Not decoration. The fingerprint printed below has to be byte-identical to
 * the one `eval/invocation.py:text_fingerprint` stamps onto every run row, or
 * it is worse than absent: a wrong one looks like grounds for pooling runs
 * that must not be pooled (TESTING.md rule 7). Two things JSON.stringify does
 * differently, and either one changes the hash: Python puts a space after `:`
 * and `,` (live -- get this wrong and every hash differs), and Python escapes
 * every character above U+007F as \uXXXX (latent, because the instruction and
 * description strings happen to be pure ASCII today; one micro sign in a
 * description ends that).
 *
 * tests/test_instructions_parity.py asserts this agrees with Python for every
 * variant, so a divergence fails the suite rather than quietly mislabelling a
 * document.
 */
function pyJsonString(s) {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    const n = s.charCodeAt(i);
    if (c === "\\") out += "\\\\";
    else if (c === '"') out += '\\"';
    else if (c === "\b") out += "\\b";
    else if (c === "\f") out += "\\f";
    else if (c === "\n") out += "\\n";
    else if (c === "\r") out += "\\r";
    else if (c === "\t") out += "\\t";
    else if (n < 0x20 || n >= 0x7f) out += "\\u" + n.toString(16).padStart(4, "0");
    else out += c;
  }
  return out + '"';
}

function pyJsonDumps(v) {
  if (typeof v === "string") return pyJsonString(v);
  if (Array.isArray(v)) return "[" + v.map(pyJsonDumps).join(", ") + "]";
  if (v && typeof v === "object") {
    return "{" + Object.keys(v).sort()
      .map((k) => `${pyJsonString(k)}: ${pyJsonDumps(v[k])}`).join(", ") + "}";
  }
  return JSON.stringify(v);
}

function fingerprint(descSet, instrVariant) {
  const blob = pyJsonDumps({
    d: T.DESCRIPTION_SETS[descSet],
    i: instrVariant === "off" ? "" : T.INSTRUCTION_SETS[instrVariant],
  });
  return crypto.createHash("sha256").update(blob, "utf8").digest("hex").slice(0, 12);
}

const args = process.argv.slice(2);
const md = args.includes("--md");
const variant = args.find((a) => !a.startsWith("--")) || "terse";
const descSet = process.env.BINOMEN_DESCRIPTIONS || "terse";
const D = T.DESCRIPTION_SETS[descSet] || T.DESCRIPTION_SETS.terse;

const TOOLS = ["check_name", "resolve_name", "get_synonyms", "expand_query"];

// Rough, and labelled rough. ~4 chars/token is a serviceable estimate for
// English prose; anything precise needs the real tokenizer.
const tok = (s) => Math.round(s.length / 4);

const rule = (c = "=") => c.repeat(74);
const out = [];
const say = (s = "") => out.push(s);

const instructions = variant === "off" ? null : T.INSTRUCTION_SETS[variant];
if (variant !== "off" && !instructions) {
  console.error(`unknown variant: ${variant} (try ${T.INSTRUCTION_VARIANTS.join(", ")}, off)`);
  process.exit(1);
}

const fp = fingerprint(descSet, variant);

// Hash only, for tests/test_instructions_parity.py to compare against Python.
if (args.includes("--fingerprint")) {
  console.log(fp);
  process.exit(0);
}

say(md ? "# What the model actually sees" : rule());
if (!md) say("WHAT THE MODEL ACTUALLY SEES");
if (!md) say(rule());
say();
if (md) {
  // A generated file that does not say so gets hand-edited, and the edit is
  // then invisible: the wording review passes against a document the model
  // never saw. The fingerprint is the same one eval/invocation.py stamps on
  // every run row, so a reader can tell at a glance whether a given run was
  // measuring this text or something since rewritten.
  say("<!-- GENERATED FILE - do not edit by hand.");
  say("     Source: node/src/tool_descriptions.js");
  say("     Rebuild all three variants with `make model-context`. -->");
  say();
}
say(`Instructions variant: ${variant}`);
say(`Descriptions set    : ${descSet}`);
say(`Text fingerprint    : ${fp}`);
say();

// ---------------------------------------------------------------------------
say(md ? "## 1. System prompt — sent ONCE per conversation" : rule("-"));
if (!md) say("1. SYSTEM PROMPT  --  sent ONCE per conversation");
if (!md) say(rule("-"));
say();
if (instructions) {
  say(md ? "The client renders this under a heading of its own:" : "The client renders this under a heading of its own:");
  say();
  say(md ? "```" : "");
  say("# MCP Server Instructions");
  say();
  say("## binomen — biological name checker");
  say();
  say(instructions);
  say(md ? "```" : "");
  say();
  say(`  ${instructions.length} chars, ~${tok(instructions)} tokens, paid once`);
} else {
  say("(nothing — BINOMEN_INSTRUCTIONS=off)");
}
say();

// ---------------------------------------------------------------------------
say(md ? "## 2. Tools block — sent with EVERY request" : rule("-"));
if (!md) say("2. TOOLS BLOCK  --  sent with EVERY request");
if (!md) say(rule("-"));
say();
say("Each tool arrives as a name, a description, and an input schema.");
say("The description is the only prose the model has when choosing a tool.");
say();

let descTotal = 0;
for (const t of TOOLS) {
  const d = D[t];
  descTotal += d.length;
  say(md ? `### \`${t}\`` : `--- ${t} ${"-".repeat(Math.max(0, 66 - t.length))}`);
  say();
  say(md ? "```" : "");
  say(d);
  say(md ? "```" : "");
  say();
  say(`  ${d.length} chars, ~${tok(d)} tokens`);
  say();
}

// ---------------------------------------------------------------------------
say(md ? "## Budget" : rule("-"));
if (!md) say("BUDGET");
if (!md) say(rule("-"));
say();
const once = instructions ? instructions.length : 0;
say(`  instructions        ${String(once).padStart(5)} chars  ~${String(tok(instructions || "")).padStart(4)} tok   once per conversation`);
say(`  tool descriptions   ${String(descTotal).padStart(5)} chars  ~${String(tok("x".repeat(descTotal))).padStart(4)} tok   EVERY request`);
say();
say("  For comparison, a check_name reply on a stable name is ~98 chars, ~25 tok.");
say("  Token counts are estimates at ~4 chars/token, not measured.");
say();

console.log(out.join("\n"));
