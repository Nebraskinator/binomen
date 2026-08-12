# Cutting an index release

The point is that a biologist should not need a Python toolchain and a 400 MB
NCBI download to answer "is this name current". A prebuilt stage-1 index is
~107 MB and installs in under a minute.

This is a **release artifact**, not a committed database. The distinction is the
whole premise of the project: a frozen copy of a taxonomy goes stale, and stale
does not announce itself. Every artifact is stamped with the taxdump release it
came from, that release appears in the provenance of every tool response, and
`binomen-fetch-index --check-age` tells a user when theirs is old.

## Steps

```bash
# 1. Build from a current taxdump
binomen-build-index                       # canaries must pass, or stop

# 2. Sanity-check before publishing anything
binomen-doctor
python -m binomen.server --check

# 3. Write a manifest with sizes and SHA-256s
binomen-fetch-index --publish dist/

# 4. Upload dist/ as GitHub release assets:
#      manifest.json
#      binomen-stage1.sqlite     (~107 MB)
#      binomen.sqlite            (~525 MB, optional)
```

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
