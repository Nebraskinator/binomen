# Cutting a release

A biologist should not need a Python toolchain, a 400 MB NCBI download, or a
first-run wait to answer "is this name current". Since v0.3.0 the extension
carries its own data: one file to download, ~24 MB, and nothing to fetch
afterwards.

That data is still a **release artifact rather than a committed database**, and
the distinction is the premise of the project: a frozen copy of a taxonomy goes
stale, and stale does not announce itself. Every shipped database records the
taxdump release and each register's own version, those appear in the provenance
of every tool response, and the databases are gitignored so no snapshot is ever
mistaken for source.

## Steps

```bash
# 1. Build the backbone from a current taxdump
binomen-build-index                       # canaries must pass, or stop

# 2. Reduce it to the shipped ambiguity database (~48 MB)
binomen-build-ambiguity

# 3. Harvest the registers (~19 MB). No credentials: ChecklistBank exports.
#    LPSN's medical-use flags are overlaid from data/lpsn.sqlite when present --
#    see docs/DATA.md for why that file needs credentials and this step does not.
binomen-harvest-registers --bound-to data/binomen.sqlite

# 4. Sanity-check
binomen-doctor
python -m binomen.server --check

# 5. Package. Runs the Node tests, refuses to build if they fail, and refuses
#    to build if the data is over budget (25 MB compressed, 100 MB on disk).
python scripts/build_mcpb.py              # -> dist/binomen.mcpb

# 6. Upload dist/binomen.mcpb as the GitHub release asset, tagged vX.Y.Z
```

Bump the version in all three files that carry it before packaging --
`node/manifest.json`, `node/package.json`, `node/src/server.js`. They have three
different audiences, which is exactly why they drift; a server reporting a
version the installer did not ship sends every bug report to the wrong code.

## The optional full index

`binomen-fetch-index --publish dist/` still publishes the stage-1 and full
backbone databases for anyone who wants ranks, lineages, author citations or the
reclassification listings. The shipped server prefers a fetched index when one is
present and falls back to the bundled data otherwise, so publishing these is
additive and never required for a working install.

Tag the release with the taxdump date it was built from — `index-2026-08-11` —
so it is obvious what a given download contains without opening it.

## Cadence

NCBI Taxonomy changes continuously. `STALE_AFTER_DAYS` in `fetch_index.py` is
120 days, which is the point at which `--check-age` starts warning; cutting a
release quarterly keeps users inside that window. Slower is not a disaster — the
release string is on every response — but the warning exists because nobody
reads provenance fields.

## Verification is not optional

`fetch_index` checks every download against the manifest SHA-256 and **refuses
to install on mismatch**, leaving nothing on disk. An index is a set of
assertions about what organisms are called; quietly accepting a corrupted or
substituted one would be a worse failure than any this package detects.
`tests/test_fetch_index.py` covers the mismatch path, partial-download cleanup,
and stale `-wal` sidecar removal.

## Pointing at your own host

`DEFAULT_MANIFEST` in `fetch_index.py` carries a placeholder GitHub URL. Set
`BINOMEN_INDEX_MANIFEST`, or pass `--manifest`, to use a fork, a mirror, or an
internal host. `scripts/bootstrap.py --manifest URL` forwards it.

## Licensing

NCBI Taxonomy is public domain (work of the US Government), so the derived index
is freely redistributable. This does **not** extend to Tier 3 authorities — LPSN
and MycoBank data are query-and-cite only and must never be baked into a
published artifact. The build does not include them; keep it that way.
