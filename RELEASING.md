# Releasing CallChain

This document describes the expected release flow for maintainers.

## Pre-release

1. Make sure unreleased changes are documented under `## [Unreleased]` in `CHANGELOG.md`.
2. Check that merged PRs are labeled correctly for `release-drafter` categories and version resolution.
3. Run the local quality gates:

```bash
make ci
make audit
make release-check
python scripts/check_release.py
make corpus-sources
make corpus-sync
make corpus-materialize ARGS='click-src --ref <git-ref>'  # when you need to reconstruct or update a vendored snapshot
make corpus-verify-archive ARGS='click-src'
make corpus-snapshot
make corpus-baseline
```

## Bump the version

Use the bump helper to update `pyproject.toml`, `src/callchain/__init__.py`, and roll the `Unreleased` changelog section into a dated release entry:

```bash
python scripts/bump_version.py 0.2.0a1 --date 2026-04-08
```

After the bump:

- `CHANGELOG.md` should contain a fresh empty `## [Unreleased]` section.
- the new version should appear in `pyproject.toml`, `src/callchain/__init__.py`, `CITATION.cff`, and the changelog.

## Validate again

```bash
make ci
make audit
make release-check
python scripts/check_release.py --expected-tag v0.2.0a1
```

If parser, resolver, or corpus thresholds changed during the release cycle, compare the last known corpus snapshot against a fresh one before publishing:

```bash
python scripts/compare_corpus_reports.py \
  --baseline build/corpus-before.json \
  --candidate build/corpus-snapshot.json

python scripts/compare_corpus_reports.py \
  --baseline build/corpus-before.json \
  --candidate build/corpus-snapshot.json \
  --metric summary \
  --fail-on-summary-drift

python scripts/compare_corpus_reports.py \
  --baseline build/corpus-before.json \
  --candidate build/corpus-snapshot.json \
  --metric summary \
  --markdown
```

If a vendored corpus sample was refreshed, also verify its recorded provenance metadata:

```bash
python scripts/check_corpus_sources.py
python scripts/refresh_corpus_source.py click-src --ref <git-ref>
python scripts/materialize_corpus_source.py click-src --ref <git-ref>
python scripts/verify_corpus_source_archive.py click-src
```

If you want the same vendored-source maintenance flow on a disposable runner, use the manual `Corpus Maintenance` GitHub Actions workflow. It uploads source-registry diffs and before/after corpus snapshots as artifacts for review.

If you want a fresh official baseline artifact bundle without using a local machine, trigger the scheduled/manual `Corpus Baseline` GitHub Actions workflow and keep the uploaded snapshot + benchmark artifacts with the release review notes.

If you want GitHub to consume the latest official baseline artifact automatically, use the `Corpus Baseline Compare` workflow. It downloads the newest successful `Corpus Baseline` artifact from the base branch, captures a fresh branch snapshot, uploads JSON + Markdown compare artifacts, writes a sticky PR comment, cross-checks review hints against the PR's changed files when available, derives CODEOWNERS-backed owner hints when `.github/CODEOWNERS` contains real assignments, adds a priority-sorted owner focus for reviewer routing, and fails on summary drift.

Before publishing, also run the packaged-install smoke path once:

```bash
make install-smoke
```

That builds the distribution, installs it into a fresh virtual environment, analyzes `examples/smoke_repo`, and verifies that the shipped CLI entrypoint and packaged dependencies still work outside the source checkout.

For registry-level rehearsal before a real release, use the manual `TestPyPI Rehearsal` workflow. It publishes the current build to TestPyPI through trusted publishing, then installs the uploaded `callchain==<version>` back from TestPyPI and runs the same smoke example. After a real GitHub Release is published, the `Post-release Smoke` workflow performs the same check against PyPI.

If the drift is intentional and reviewed, either apply the `corpus-drift-approved` label to the PR or set `allow_summary_drift=true` when running the workflow manually. That keeps the workflow green while preserving the compare report and the explicit waiver reason.

After a drift-approved PR merges, the `Corpus Baseline Refresh` workflow automatically captures a post-merge candidate baseline artifact bundle on the merge commit. Review that bundle when deciding whether to promote a new official baseline or leave the previous one in place.

The `Release` workflow also materializes its current view of corpus baseline state into `build/release-corpus-baseline-state.json` and `build/release-corpus-baseline-state.md`, appends the Markdown summary to the job summary, and uploads both files as artifacts before tag validation. The Markdown summary includes direct links to the latest baseline, latest successful compare run for the release target branch, a severity-sorted focus excerpt for the highest-impact drift cases, coarse parser/resolver-style categories, finer discovery/symbol-extraction/call-resolution/chain-enumeration/parse-health/non-structural attribution hints, likely modules to review such as `src/callchain/languages/*.py`, `src/callchain/core/callgraph.py`, and `src/callchain/core/chain_enum.py`, CODEOWNERS-backed owner hints when the compare artifact carries them, a priority-sorted owner focus for likely reviewer routing, reviewer candidates plus a review-request dry-run, changed-file overlap when the compare artifact includes PR file context, a priority-sorted changed-file focus that surfaces core-module hits first, and refresh candidate artifacts, plus branch/SHA/event context and a release recommendation. After distribution artifacts are uploaded, the workflow renders a marker-backed `Release Corpus Audit` section and upserts it into the GitHub Release body, so release reviewers can inspect the same evidence without opening the Actions run. If you have an exported state JSON from CI or a local rehearsal, use the same renderer locally:

```bash
python scripts/render_release_corpus_state.py --state path/to/release-corpus-baseline-state.json --markdown
```

## Publish

1. Commit the release changes.
2. Create and push a tag such as `v0.2.0a1`.
3. Review the drafted GitHub release notes and adjust if necessary.
4. Publish a GitHub Release for that tag.
5. The `release.yml` workflow will build, validate, and publish to PyPI.

## Post-release

- confirm the GitHub Release notes are correct
- verify the package on PyPI
- start recording follow-up changes under `## [Unreleased]`
- keep `.github/labels.yml` and `.github/release-drafter.yml` aligned if label taxonomy changed
