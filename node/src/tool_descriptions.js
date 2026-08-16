"use strict";
/*
 * Tool descriptions. Kept in their own file because the wording is a measured
 * variable, not decoration -- see README section 7 and docs/DISCOVERY-LOG.md.
 *
 * Measured result worth knowing before editing these: on a prompt framed as a
 * domain task ("summarize the treatment guidelines for C. difficile
 * infection"), neither a plain nor a maximally forceful description caused the
 * tool to be called at all. An instruction in the client's own context did,
 * immediately. Description wording is not the lever it looks like -- so these
 * aim to be accurate and cheap to act on rather than insistent.
 *
 * The one claim that does work is cost. "Call this on every organism mention"
 * is only a reasonable request for a tool that answers in 2 ms and 25 tokens,
 * so check_name leads with that and it is true.
 */

const _BROAD = {
  check_name:
    "STAGE 1 -- CHEAP. Local lookup, about 2 ms, and the reply is usually one short line. " +
    "Call it freely.\n\n" +
    "USE THIS WHENEVER AN ORGANISM NAME OR GENE SYMBOL APPEARS -- in a question, a document " +
    "you are reading, a dataset column, or a sentence you are about to write. Not only for " +
    "questions that are about taxonomy.\n\n" +
    "Answers one question: is there anything about this name that needs a closer look? Most " +
    "names return `verdict: stable, escalate: false`, which means the name you have is the " +
    "accepted one and you are done. Otherwise it returns `escalate: true` with a `next` " +
    "field naming the tool to call and a one-line reason -- superseded, homonym, contested, " +
    "or unknown.\n\n" +
    "You do not have to judge which names are risky. This tells you, from the index.",

  resolve_name:
    "STAGE 2 -- local, no network. Call when check_name says `next: resolve_name`, or when " +
    "you need the accepted name, its authorship, or the prior names for a taxon.\n\n" +
    "Biological names are not stable identifiers. Clostridium difficile is now " +
    "Clostridioides difficile; Enterobacter aerogenes is now Klebsiella aerogenes; " +
    "Lactobacillus was split into about 25 genera. Old and new names are both heavily " +
    "attested in text, so a name being familiar is no evidence that it is current.\n\n" +
    "Returns candidate accepted names (a LIST -- authorities genuinely disagree for some " +
    "taxa), stable identifiers, author citation, and provenance.",

  get_synonyms:
    "Every name recorded for a taxon, bare and searchable. Use when reconciling datasets or " +
    "author lists that may use different names for one organism, and before concluding that " +
    "two records describe different organisms.",

  expand_query:
    "Build the complete set of search terms covering a taxon's naming history, including " +
    "abbreviated genus forms like 'C. difficile'.\n\n" +
    "USE BEFORE ANY LITERATURE SEARCH, DATABASE QUERY OR SYSTEMATIC REVIEW involving an " +
    "organism. Searching the current name alone silently misses everything published under " +
    "prior names -- often decades of primary work -- and returns zero results rather than an " +
    "error, which reads like a finding."
};

/*
 * `terse` -- author's wording, shipped verbatim so its behaviour can be
 * observed rather than argued about.
 *
 * The tools block is sent with EVERY request; `_BROAD` costs ~2000 characters
 * of it. That is the largest recurring cost this extension imposes, and
 * session 2 measured description wording as the lever that did NOT move
 * invocation -- so it was the most expensive channel doing the least work.
 *
 * What is deliberately absent here, and why it may be fine:
 *
 *   - No "authorities disagree, report every candidate" rule. resolve_name's
 *     response already carries `contested: true` and the literal instruction
 *     "AUTHORITIES DISAGREE. Report both names." The data decides the policy;
 *     the description does not need to restate it.
 *   - No description of what check_name returns. The response is
 *     self-describing: `verdict`, `escalate`, `reason`, `next`.
 *
 * Open questions this wording will answer by being run, not by review:
 *
 *   - Does "call it on every name" hold without the cost claim (~2 ms, one
 *     line) that made the ask reasonable? README calls cost the one claim that
 *     works.
 *   - Does expand_query still fire without a trigger condition? It now
 *     describes capability rather than occasion, and the Candida auris failure
 *     was a literature-search prompt.
 *   - Does "if the context requires disambiguating" reintroduce the epistemic
 *     judgement that check_name's wording removes?
 */
const _TERSE = {
  check_name:
    "Call on every genus and/or species name you read or write",
  resolve_name:
    "Call if a genus and/or species name has changed",
  get_synonyms:
    "Call when it is useful to view every name recorded for a taxon",
  expand_query:
    "Call when designing a search query concerning a genus and/or species",
};


const DESCRIPTION_SETS = { broad: _BROAD, terse: _TERSE };
const DESCRIPTION_VARIANTS = Object.keys(DESCRIPTION_SETS);

function descriptions() {
  const v = String(process.env.BINOMEN_DESCRIPTIONS || "terse").toLowerCase();
  return DESCRIPTION_SETS[v] || _TERSE;
}

/*
 * Instruction variants. Sent once, in the initialize result, and rendered by
 * the client into the system prompt.
 *
 *   terse          author's wording. One sentence. All trigger guidance moved
 *                  into the tool descriptions, where `check_name` now carries
 *                  it unconditionally. SHIPPED DEFAULT.
 *   conditional    asks the model to judge whether the answer depends on the
 *                  name being current. Failed live on a domain-framed prompt,
 *                  DISCOVERY-LOG session 3.
 *   unconditional  triggers on the name itself, no judgement asked, and says
 *                  why the judgement cannot be made.
 *   off            send nothing. Required for description-wording comparisons.
 */
const INSTRUCTION_SETS = {
  terse:
    "binomen resolves genus and species names against a dated copy of NCBI Taxonomy.",

  conditional:
    "binomen resolves biological names against a dated copy of NCBI Taxonomy. Every " +
    "value is a lookup, not a generation.\n\n" +
    "Call check_name when an organism name appears and the answer depends on it being " +
    "current -- writing about an organism, reconciling datasets, preparing a search. " +
    "A lookup costs about 2 ms and one line.\n\n" +
    "Call expand_query before a literature search. The current name alone returns few " +
    "results rather than an error, which reads like a finding.\n\n" +
    "Where authorities disagree, report every candidate. Do not pick one.",

  unconditional:
    "binomen resolves biological names against a dated copy of NCBI Taxonomy. Every " +
    "value is a lookup, not a generation.\n\n" +
    "Call check_name on every organism name you read or write. Do not judge which ones " +
    "need it -- you cannot. Nothing you remember carries a date, and superseded names " +
    "are usually the more familiar ones. A lookup costs about 2 ms and one line.\n\n" +
    "Call expand_query before any literature search, including for recent work. The " +
    "current name alone returns few results rather than an error, which reads like a " +
    "finding.\n\n" +
    "Where authorities disagree, report every candidate. Do not pick one.",
};
const INSTRUCTION_VARIANTS = Object.keys(INSTRUCTION_SETS);

function instructions() {
  const v = String(process.env.BINOMEN_INSTRUCTIONS || "terse").toLowerCase();
  if (v === "off") return null;
  return INSTRUCTION_SETS[v] || INSTRUCTION_SETS.terse;
}

module.exports = {
  descriptions, instructions,
  DESCRIPTION_SETS, DESCRIPTION_VARIANTS,
  INSTRUCTION_SETS, INSTRUCTION_VARIANTS,
};
