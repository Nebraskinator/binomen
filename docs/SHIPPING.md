# From taxdump to a biologist's laptop

The whole pipeline, and the decisions inside it. Written down because it spans
three artifacts, two languages and a release process, and none of that fits in
anyone's head.

```
  NCBI taxdump                    you, quarterly
        |  binomen-build-index
        v
  binomen.sqlite        525 MB    full backbone      (developers, eval harness)
  binomen-stage1.sqlite 107 MB    verdicts + blooms  (Python server)
  binomen-field.sqlite  123 MB    the shippable one  (Node extension)
        |  binomen-fetch-index --publish dist/
        v
  dist/manifest.json + *.sqlite.gz
        |  upload as a GitHub release, tagged index-YYYY-MM-DD
        v
  binomen.mcpb          ~60 KB    Node server, no index inside
        |  user double-clicks
        v
  Claude Desktop installs it
        |  first run: fetch binomen-field.sqlite.gz  (46 MB)
        v
  working, and checking for a newer index every few weeks
```

## Why three indexes

They have different consumers and the middle one is not a compromise between
the other two.

| artifact | who reads it | why it exists |
|---|---|---|
| `binomen.sqlite` | Python resolver, eval harness | Full backbone: lineage, ranks, reclassification listings. Needed for stage-2 tools and for the research questions |
| `binomen-stage1.sqlite` | Python `check_name` | Verdicts + Bloom filters. The cheap always-callable path |
| `binomen-field.sqlite` | Node extension | Verdicts **plus** per-taxon synonym lists and authorities. Answers the four questions a bench biologist actually asks, without the 3M-name backbone |

The field edition is *larger* than stage 1 (123 vs 107 MB) because per-taxon
synonym lists cost more than the backbone rows they replace. That was a surprise
and it is worth stating rather than quietly rounding off: the saving is in what
it lets you leave behind, not in the file itself.

## What is deliberately not in the field edition

**Strain-level taxa.** NCBI has well over a million, and every one of them
inherits its species' transfer: `Clostridium difficile 630` became
`Clostridioides difficile 630` without anyone publishing anything about strain
630. A strain name is a binomial plus a laboratory designation, only the
binomial is governed by a code, and the designation never changes. So the strain
form is *derivable* -- `split_designation()` peels the suffix, resolves the
species, and reattaches it. Complete coverage, zero bytes.

**Lineage, rank hierarchy, reclassification listings.** Stage-2 questions. They
are most of what makes the full index 525 MB, and a bench biologist downloading
half a gigabyte to check a species list is a tool that does not get installed.

## Releasing an index

Quarterly is enough; `--check-age` starts warning at 120 days.

```bash
binomen-build-index --taxdump taxdump.tar.gz   # canaries must pass or it stops
binomen-doctor                                  # eyeball it
binomen-fetch-index --publish dist/             # gzip + manifest with checksums
```

Upload `dist/` as a GitHub release tagged `index-YYYY-MM-DD`. Point
`DEFAULT_MANIFEST` (or `BINOMEN_INDEX_MANIFEST`) at it.

Integrity is checked twice: the transfer against `sha256`, and the decompressed
database against `uncompressed_sha256` before it is allowed to replace a working
index. A valid gzip containing the wrong database passes the first check and
fails the second, and there is a test that plants exactly that.

## Releasing the extension

The `.mcpb` carries the Node server and no data — around 60 KB. It is decoupled
from the index on purpose: a new index does not require a new extension, and a
fixed bug does not require anyone to re-download 46 MB.

```bash
make mcpb        # builds dist/binomen.mcpb
```

Install path for the user: double-click, or Settings → Extensions → Advanced
settings → Install Extension…

> Requires `manifest_version` 0.3 and `server.type: "node"`. The newer `uv`
> type (Python, cross-platform, no bundling) is rejected by Claude Desktop
> 1.28929.0 with "the preview failed" — verified. Revisit when it is supported;
> it would let the Python implementation ship directly.

### Pre-release checklist

Bump the version in **three** places or the installed build will lie about
itself, which cost a full round of diagnosis once already:

- `node/manifest.json` → `version`
- `node/package.json` → `version`
- `node/src/server.js` → `serverInfo.version`

Then, in order:

```bash
python scripts/build_mcpb.py     # runs the Node suite and the conformance check
```

**Install the artifact and confirm it attaches.** Not the working copy — the
`.mcpb`. This is not optional and it is not paranoia:

> The v0.2.2 blocker was `if (require.main === module) main();` on the last
> line of `server.js`. Claude Desktop's built-in-Node path forks extensions with
> Electron's `utilityProcess.fork()`, which does not load the entry module as a
> Node CLI entry point, so `require.main === module` was false and the server
> never started. Every direct test passed — `try_node_server.js`, the Node
> suite, manual runs — because every one of them invoked the file as an entry
> point. The packaged install was the only path that did not, and it was the
> only path never exercised outside Claude Desktop.

So the gate is: install it, restart with **"Use Built-in Node.js for MCP" ON**,
and call one tool. `scripts/collect_boot_log.ps1` reports the version actually
on disk and dumps `boot.log`, which records `require.main === module` for
exactly this reason.

Keep `probe2/` around. Installing it beside a failing build and comparing the
two logs within one restart is what found that bug, and it will find the next
one faster than reading code will.

### Publishing to GitHub Releases

The extension and the index ride the same release mechanism but move on
different clocks — the index tracks NCBI, the extension tracks bug fixes.

```
Tag           v0.2.5                     (extension versions carry a v prefix)
Assets        binomen.mcpb               ← what a biologist downloads
              manifest.json              ← what the auto-updater reads
              binomen-field.sqlite.gz    ← the 46 MB index
              binomen-stage1.sqlite      ← optional, for the Python server
```

`docs/RELEASING.md` covers cutting an index release. The extension's updater
reads `releases/latest/download/manifest.json`, so **`latest` must always point
at a release carrying a valid index manifest** — publishing an extension-only
release without one silently breaks first-run downloads for every new user.
Either attach the current index manifest to every release, or mark
extension-only releases as pre-releases so they do not become `latest`.

Release notes should lead with what a non-developer needs: whether they have to
do anything, and whether the index changed. `docs/INSTALL.md` is the link to
give people; it assumes no terminal and covers the permission prompt, the
verdict vocabulary, and troubleshooting.

## The index lives outside the extension

Not in the extension directory. Extension updates replace that directory, and a
46 MB re-download on every bug-fix release is unacceptable.

```
Windows  %LOCALAPPDATA%\binomen\
macOS    ~/Library/Application Support/binomen/
Linux    ~/.local/share/binomen/
```

Overridable through `user_config` for anyone who wants it on another drive.

## Updates

Three rules, and the first two exist because of a real failure during
development.

**1. Never block a tool call on the network.** The update check runs at server
startup, not inside `check_name`. A name lookup must stay at ~2 ms or the
premise of the whole staged design collapses.

**2. Never require the user to quit anything.** The running server holds the
index open, and on Windows an open SQLite file cannot be replaced — trying it
produced `WinError 32` during development. So a new index downloads to a staging
file and is *swapped on next start*, when nothing holds a handle. The user
restarts Claude Desktop eventually anyway.

**3. Say so, once, in the tool response.** Not a notification the user has to
dismiss. When an index is past 120 days or a newer release exists, `check_name`
adds one short line. It is the only channel that reaches the person who is
actually relying on the answer.

```
startup   -> if last check > 14 days: fetch manifest (a few KB), compare release
          -> if newer: download .gz to <index>.staged, verify both checksums
          -> if <index>.staged exists and verifies: swap it in, then serve
tool call -> never touches the network
```

A failed check is silent — no network is a normal state on a laptop, and an
extension that complains about it is worse than one that quietly serves a
slightly older index. A *stale* index is not silent, because answering as of a
forgotten date is exactly this project's subject.

## Open questions

- **Should stage 1 drop strains too?** They are derivable there as well. Measured
  at 17% of taxa, so perhaps 15 MB — real but not urgent, and it would collapse
  two code paths into one.
- **Does the extension need the instruction snippet?** Measured (§7): tool
  descriptions did not cause invocation on a prompt framed as a domain task; an
  instruction in the client context did. For a *product*, shipping the
  instruction is probably right. For the *harness* it would delete the research
  question. These can differ, and should.
