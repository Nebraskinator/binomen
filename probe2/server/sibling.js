"use strict";
/*
 * Exists only to answer one question: can the entry point require a sibling
 * file from inside the installed extension directory?
 *
 * binomen's bundle is five .js files; the probe that attached successfully was
 * one. That is a difference between the working case and the failing case, and
 * an unhandled throw at module-load time -- before any listener exists -- would
 * look exactly like the observed symptom: the host reports the process spawned,
 * sends initialize, and never hears back.
 */
module.exports = { ok: true, loadedAt: Date.now() };
