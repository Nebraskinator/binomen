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

module.exports = {
  /*
   * Sent as `instructions` in the initialize result. The spec describes this
   * field as "Instructions describing how to use the server and its features
   * ... this information MAY be added to the system prompt."
   *
   * This is here rather than in server.js because it is the same kind of thing
   * as the descriptions below: a measured variable, not decoration. The one
   * result the discovery log has on invocation is that a description did not
   * cause the tool to be called on a domain-framed prompt and an instruction in
   * the client's own context did. `instructions` is the protocol's own channel
   * for exactly that, and it ships inside the extension -- which matters,
   * because the target user will never write a CLAUDE.md.
   *
   * TREAT THIS AS UNMEASURED. Whether Claude Desktop surfaces the field at all
   * is unknown, and whether it changes invocation on a domain-framed prompt is
   * the open research question. It must be run through the harness as its own
   * condition before any claim is made about it. Do not report it as working
   * because the tool got called once.
   */
  instructions:
    "binomen resolves biological names against a versioned copy of NCBI Taxonomy. " +
    "Every value it returns is a lookup, not a generation.\n\n" +
    "Organism names are not stable identifiers, and a name being familiar is no evidence " +
    "that it is current. Clostridium difficile is now Clostridioides difficile; " +
    "Enterobacter aerogenes is now Klebsiella aerogenes. Both old and new names are heavily " +
    "attested in the literature, so recognising a name tells you nothing about its status.\n\n" +
    "Because of that, a name cannot be checked from memory -- including yours. Call " +
    "check_name when an organism name appears and the answer depends on the name being " +
    "current: writing about an organism, reconciling datasets, or preparing a literature " +
    "search. It costs about 2 ms and one short line, so the cost of calling it is lower " +
    "than the cost of being wrong.\n\n" +
    "Call expand_query before any literature search involving an organism. Searching the " +
    "current name alone returns few results rather than an error, which reads like a " +
    "finding and is not one.\n\n" +
    "Where authorities disagree, binomen returns every candidate rather than choosing. Do " +
    "not collapse that list to one name.",

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
    "error, which reads like a finding.",
};
