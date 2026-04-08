# Test Repositories

This directory holds local sample repositories used for higher-level regression checks.

- `smoke_repo/` is a tiny deterministic project for fast end-to-end validation.
- `corpus.toml` defines the manifest used by `scripts/check_corpus.py` and `scripts/benchmark_corpus.py`.
- `sources.toml` defines provenance metadata for the corpus entries, including vendored sample version and license details.
  It also records `source_ref`, `archive_url`, `archive_sha256`, and `content_sha256` for vendored or pinned samples.

The wider corpus also references vendored samples outside this directory, including `examples/smoke_repo` and `downloaded_repos/pallets/click/src`.

Suggested maintenance flow when parser or resolver behavior changes:

1. Run `make corpus-check` to see whether existing thresholds still hold.
2. Run `make corpus-sources` to verify source metadata and vendored sample provenance.
3. If you are intentionally updating a vendored git-backed sample, run `make corpus-refresh ARGS='click-src --ref <git-ref>'`.
4. If the vendored snapshot is missing or no longer git-backed, run `make corpus-materialize ARGS='click-src --ref <git-ref>'` to reconstruct it from the configured archive.
5. Run `make corpus-verify-archive ARGS='click-src'` to verify the rendered upstream archive checksum matches the recorded metadata.
6. If sample contents changed locally outside that workflow, run `make corpus-sync` to refresh `source_ref` and `content_sha256`.
7. Capture a before or after snapshot with `make corpus-snapshot`.
8. If you collected two reports, compare them with `make compare-corpus BASELINE=... CANDIDATE=...`.
   For structure-only review, add `ARGS='--metric summary --fail-on-summary-drift'`.
9. Only then decide whether `corpus.toml` thresholds or `sources.toml` metadata should move.

If you prefer not to mutate a local checkout while validating a vendored-source update, the manual `Corpus Maintenance` GitHub Actions workflow runs the same archive verification and materialization flow on an ephemeral runner and uploads the resulting artifacts.

If you want a fresh canonical baseline for future comparisons, use `make corpus-baseline` locally or the scheduled/manual `Corpus Baseline` workflow in GitHub Actions.

For pull-request enforcement against the latest official baseline artifact, use the `Corpus Baseline Compare` workflow. It downloads the newest successful baseline bundle from the base branch and fails when summary counts drift.
