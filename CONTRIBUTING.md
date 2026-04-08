# Contributing to CallChain

Thank you for your interest in contributing! This guide will help you get started.

## Development Setup

```bash
git clone https://github.com/callchain/callchain.git
cd callchain
pip install -e ".[dev]"
pytest  # verify everything works
# Optional helpers
pre-commit install
```

## Project Structure

```
src/callchain/
├── cli.py              # CLI entry point
├── core/
│   ├── models.py       # Data models
│   ├── callgraph.py    # Call graph builder + symbol resolver
│   ├── chain_enum.py   # Chain enumeration
│   └── analyzer.py     # Advanced analysis
├── languages/
│   ├── base.py         # Plugin base class
│   ├── python_lang.py  # Language plugins...
│   └── ...
└── output/             # Output formatters
```

## Adding a New Language

1. Create `src/callchain/languages/yourlang_lang.py`
2. Install the corresponding `tree-sitter-yourlang` package
3. Subclass `LanguagePlugin`:

```python
class YourLangPlugin(LanguagePlugin):
    language = Language.YOUR_LANG
    extensions = (".ext",)

    def parse_file(self, file_path, project_root):
        # Parse with tree-sitter, return ModuleInfo
        ...

    def extract_calls(self, file_path, project_root):
        # Extract CallEdge list
        ...
```

4. Add `Language.YOUR_LANG` to the `Language` enum in `models.py`
5. Add the extension mapping in `_EXT_MAP`
6. Import your plugin in `cli.py`
7. Add test fixtures in `tests/fixtures/yourlang/`
8. Add tests in `tests/test_yourlang_lang.py`

Registration is automatic — subclassing `LanguagePlugin` with a `language` attribute registers it.

## Running Tests

```bash
pytest                          # all tests
pytest tests/test_python_lang.py  # single file
pytest -v --cov=callchain       # with coverage
make ci                         # lint + mypy + coverage gate
make audit                      # dependency vulnerability scan
make sbom                       # export CycloneDX SBOM
make release-check              # build + twine validation
make install-smoke              # fresh-venv install + example analysis smoke check
make published-smoke PACKAGE_SPEC=callchain==<version> ARGS='--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/'
make corpus-check               # run local sample-repo regression checks
make corpus-sources             # validate sample source metadata and provenance
make corpus-sync                # refresh source metadata from local sample repos
make corpus-refresh ARGS='click-src --ref <git-ref>'
make corpus-materialize ARGS='click-src --ref <git-ref>'
make corpus-verify-archive ARGS='click-src'
make corpus-snapshot            # write a JSON corpus snapshot
make corpus-baseline            # write the official baseline snapshot + benchmark pair
make benchmark-corpus           # write a JSON corpus benchmark report
make compare-corpus BASELINE=build/base.json CANDIDATE=build/head.json
make compare-corpus BASELINE=build/base.json CANDIDATE=build/head.json ARGS='--metric summary --fail-on-summary-drift'
make compare-corpus BASELINE=build/base.json CANDIDATE=build/head.json ARGS='--metric summary --markdown'
make bump-version VERSION=0.2.0a1 # update release version files
make release-corpus-state ARGS='--state build/release-corpus-baseline-state.json --markdown'
```

## Code Style

- Format with `black`
- Lint with `ruff`
- Type check with `mypy`

```bash
black src/ tests/
ruff check src/ tests/
mypy src/
pre-commit run --all-files
```

## Pull Request Guidelines

1. Create a feature branch from `main`
2. Write tests for new functionality
3. Ensure all tests pass: `pytest`
4. Ensure lint passes: `ruff check src/ tests/`
5. Ensure type checking passes: `mypy src/`
6. Keep PRs focused — one feature or fix per PR
7. Update docs and CHANGELOG.md when behavior changes
8. Prefer branch names like `feat/...`, `fix/...`, or `deps/...` so repository automation can label and categorize the PR cleanly

## Maintainer Notes

- `scripts/bump_version.py` also updates `CITATION.cff`; do not hand-edit release version metadata in one file only.
- Dependabot is configured for Python and GitHub Actions updates.
- Release builds upload artifacts, validate distributions, and generate build provenance attestations.
- CodeQL scans Python and GitHub Actions workflow code; scheduled dependency audits export a CycloneDX SBOM artifact.
- Labels are synchronized from `.github/labels.yml`; keep that file aligned with `.github/release-drafter.yml`.
- New bug reports and feature requests start with `needs-triage`; remove or replace it during first review.
- Release notes are drafted automatically from PR labels, and inactive issues/PRs are handled by the stale workflow.
- Apply `corpus-drift-approved` only after reviewing the baseline compare report and deciding the structural drift is intentional.
- `test_repos/corpus.toml` is the source of truth for local corpus regression thresholds; update it deliberately when parser or resolver behavior changes.
- `test_repos/sources.toml` is the source of truth for corpus sample provenance; keep vendored sample version, upstream URL, archive URL template, archive checksum, and license metadata in sync with the local snapshot.
- Use `make corpus-refresh ARGS='click-src --ref <git-ref>'` when you intentionally move a vendored git-backed sample to a new upstream commit or tag.
- Use `make corpus-materialize ARGS='click-src --ref <git-ref>'` when you want one command that can either update an existing git-backed sample or reconstruct a non-git vendored snapshot from its configured archive.
- Use `make corpus-verify-archive ARGS='click-src'` after vendored refreshes when you want an explicit upstream archive checksum verification pass.
- Use `make corpus-sync` when sample contents changed locally outside the refresh workflow and you only need to resync `source_ref` or `content_sha256`.
- If you want a remote maintainer dry-run instead of touching your local checkout, use the manual `Corpus Maintenance` GitHub Actions workflow with `source_name`, `ref`, and `materialize` inputs.
- The scheduled/manual `Corpus Baseline` GitHub Actions workflow is the canonical place to capture official baseline snapshot and benchmark artifacts for future comparisons.
- Capture `make corpus-snapshot` before and after large parser or resolver changes when you expect real-world counts to move.
- Use `make compare-corpus BASELINE=... CANDIDATE=...` to review timing regressions and structural count deltas before you loosen corpus thresholds.
- `make compare-corpus ... ARGS='--metric summary --markdown'` renders a Markdown review note that can be pasted into PRs or release notes.
- The `Corpus Baseline Compare` workflow consumes the latest successful official baseline artifact on the base branch, uploads JSON + Markdown compare artifacts, writes the Markdown summary to the job summary, annotates review hints with the PR's changed files when available, derives CODEOWNERS-backed owner hints when `.github/CODEOWNERS` has real rules, surfaces a priority-sorted owner focus plus reviewer candidates and a review-request dry-run for reviewer routing, and gates pull requests on `--metric summary --fail-on-summary-drift`.
- If the structural drift is intentional, use the `corpus-drift-approved` PR label or the manual workflow's `allow_summary_drift` input so the workflow stays green while preserving the compare artifacts and audit trail.
- Merged PRs that still carry `corpus-drift-approved` trigger the `Corpus Baseline Refresh` workflow, which captures a post-merge snapshot + benchmark candidate bundle and comments back on the PR with the artifact reference.
- The main CI workflow also runs an `install-smoke` matrix job that builds the package, installs it into a fresh virtual environment, analyzes `examples/smoke_repo`, and uploads a smoke-summary artifact. Use `make install-smoke` locally after packaging, entrypoint, or example changes.
- The manual `TestPyPI Rehearsal` workflow builds the current revision, publishes it to TestPyPI via trusted publishing, then installs the uploaded version back from TestPyPI and runs the same smoke example. Use `make published-smoke PACKAGE_SPEC=...` locally when you want the same index-install validation without GitHub Actions.
- The `Post-release Smoke` workflow runs on published GitHub Releases and verifies that the released version can be installed from PyPI and used against `examples/smoke_repo`.
- The `Release` workflow captures the latest baseline/refresh state as JSON, renders a Markdown summary with `scripts/render_release_corpus_state.py`, writes it to the job summary, and uploads both files as artifacts before tag validation runs. The Markdown summary includes direct run/artifact links plus branch, commit, and event context for review, includes the latest successful branch compare run plus a severity-sorted focus excerpt for the most important drift cases, classifies those cases into coarse parser/resolver buckets and finer discovery/symbol-extraction/call-resolution/chain-enumeration/parse-health/non-structural attribution buckets, derives likely modules to review such as `src/callchain/languages/*.py`, `src/callchain/core/callgraph.py`, and `src/callchain/core/chain_enum.py`, carries forward compare-time CODEOWNERS owner hints, owner focus, reviewer candidates, and review-request dry-run output when available, shows changed-file overlap when the compare artifact came from a PR-aware compare run, priority-sorts that overlap so core-module hits surface first, and upserts a marker-backed corpus audit section into the published GitHub Release body.
- The `examples/` directory now includes a first-run smoke repo plus richer Python, TypeScript, and C++ scenarios. Keep those examples usable from the README; they are part of the product surface, not filler demo code.
- `make benchmark-corpus` writes `build/corpus-benchmark.json`; keep timing benchmarks local or advisory rather than making raw CI timings a required gate.

## Reporting Issues

- Include the callchain version (`callchain --version`)
- Include the language(s) being analyzed
- Include a minimal reproduction case if possible
- For parse errors, include the file that failed (or a simplified version)
- For usage questions, prefer the support flow documented in [SUPPORT.md](SUPPORT.md)
