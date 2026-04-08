# CallChain

**Multi-language static call chain analysis tool.** Extract call graphs, enumerate call chains, and analyze code structure across Python, JavaScript, TypeScript, Java, Go, Rust, C, and C++.

---

## Features

- **Multi-language support** — Python, JavaScript/TypeScript, Java, Go, Rust, C, C++ (powered by [tree-sitter](https://tree-sitter.github.io/))
- **Call graph construction** — Build inter-procedural call graphs with cross-file resolution
- **Call chain enumeration** — DFS-based chain discovery with depth/count limits and cross-file filtering
- **Advanced analysis**
  - Cyclomatic complexity per function
  - Module coupling (fan-in / fan-out / instability)
  - Circular dependency detection
  - Dead code detection (functions never called)
  - Hotspot functions (most frequently called)
  - Unused import detection
  - Class hierarchy / inheritance mapping
  - Complexity distribution bucketing
- **Multiple output formats** — Terminal summary, JSON, JSONL, Graphviz DOT, Mermaid, interactive HTML report
- **Incremental analysis** — Optional file-level cache for faster repeat runs
- **Project configuration** — `.callchain.toml` support for repeatable analysis settings
- **Directory scoping** — Restrict analysis to specific subdirectories and exclude files with glob patterns
- **Watch mode** — Re-run analysis automatically on file changes
- **Plugin architecture** — Easy to add new language support

## Installation

```bash
pip install -e ".[dev]"
```

Or install from the repository:

```bash
git clone https://github.com/callchain/callchain.git
cd callchain
pip install -e ".[dev]"
```

## Quick Start

### First run: analyze a project

```bash
callchain analyze /path/to/project
```

### Validate the packaged-install path

```bash
python -m build
python scripts/install_smoke.py --dist-dir dist --example examples/smoke_repo
```

This creates a fresh virtual environment, installs the built artifact, runs `callchain --version`, analyzes `examples/smoke_repo`, and verifies JSON + HTML outputs. The example project is documented in [examples/README.md](examples/README.md).

### Smoke a published package from TestPyPI or PyPI

```bash
make published-smoke PACKAGE_SPEC=callchain==<version> \
  ARGS='--index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/'
```

Use this after a TestPyPI rehearsal or a real release to validate the package as an external user would install it.

### Task-oriented quickstart

#### I want to inspect a layered Python service

```bash
callchain analyze examples/python_service
callchain analyze examples/python_service --restrict-dir service
callchain analyze examples/python_service --only-cross-file
```

#### I want to inspect a TypeScript frontend-style flow

```bash
callchain analyze examples/ts_dashboard --lang typescript
callchain analyze examples/ts_dashboard --format mermaid --output build/ts-dashboard.md
```

#### I want to inspect a tiny C++ library

```bash
callchain analyze examples/cpp_library --lang cpp
callchain analyze examples/cpp_library --format html --output build/cpp-library.html
```

For the full example catalog, see [examples/README.md](examples/README.md).

### Common analysis options

#### Specify language explicitly

```bash
callchain analyze /path/to/project --lang python
callchain analyze /path/to/project --lang js --lang ts
```

#### Restrict to a subdirectory

```bash
callchain analyze /path/to/project --restrict-dir src/core
```

#### Exclude generated or vendored code

```bash
callchain analyze /path/to/project --exclude "tests/**" --exclude "vendor"
```

#### Enable incremental caching

```bash
callchain analyze /path/to/project --cache
```

#### Watch for changes

```bash
callchain watch /path/to/project --lang python
```

#### Only show cross-file call chains

```bash
callchain analyze /path/to/project --only-cross-file
```

#### Output formats

```bash
# Terminal summary (default)
callchain analyze ./my_project

# JSON full report
callchain analyze ./my_project --format json -o report.json

# JSONL chains (one chain per line)
callchain analyze ./my_project --format jsonl -o chains.jsonl

# Graphviz DOT
callchain analyze ./my_project --format dot -o callgraph.dot

# Mermaid diagram
callchain analyze ./my_project --format mermaid -o callgraph.md

# Interactive HTML report
callchain analyze ./my_project --format html -o report.html
```

### Config file

Create `.callchain.toml` in the project root:

```toml
[analyze]
lang = ["python", "typescript"]
restrict_dir = "src"
exclude = ["tests/**", "build"]
cache = true
max_depth = 30
format = "summary"
```

## Example Output

```
$ callchain analyze tests/fixtures/python

Analyzing /path/to/tests/fixtures/python
  Detected languages: python
  Files: 2, Functions: 8, Classes: 1
  Call edges: 6
  Call chains: 4

    Project Summary
┌─────────────┬────────┐
│ Languages   │ python │
│ Files       │ 2      │
│ Functions   │ 8      │
│ Classes     │ 1      │
│ Call Edges  │ 6      │
│ Call Chains │ 4      │
└─────────────┴────────┘
  Complexity Distribution
┏━━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Range           ┃ Count ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ low (1-5)       │     8 │
│ medium (6-10)   │     0 │
│ high (11-20)    │     0 │
│ very_high (21+) │     0 │
└─────────────────┴───────┘
   Hotspot Functions (Most Called)
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┓
┃ Function       ┃ File      ┃ Calls ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━┩
│ increment      │ sample.py │     4 │
│ Calculator.add │ sample.py │     1 │
│ double         │ utils.py  │     1 │
└────────────────┴───────────┴───────┘

No circular dependencies detected.

Dead functions (never called): 3
  sample.helper (sample.py:35)
  sample.Calculator.async_add (sample.py:20)
  utils.triple (utils.py:10)
```

## Supported Languages

| Language   | Extensions                       | Structure Extraction | Call Graph | Complexity |
|------------|----------------------------------|----------------------|------------|------------|
| Python     | `.py`                            | Yes                  | Yes        | Yes        |
| JavaScript | `.js`, `.jsx`                    | Yes                  | Yes        | Yes        |
| TypeScript | `.ts`, `.tsx`                    | Yes                  | Yes        | Yes        |
| Java       | `.java`                          | Yes                  | Yes        | Yes        |
| Go         | `.go`                            | Yes                  | Yes        | Yes        |
| Rust       | `.rs`                            | Yes                  | Yes        | Yes        |
| C          | `.c`, `.h`                       | Yes                  | Yes        | Yes        |
| C++        | `.cpp`, `.cc`, `.cxx`, `.hpp`... | Yes                  | Yes        | Yes        |

## Architecture

```
src/callchain/
├── cli.py                  # Unified CLI (click-based)
├── core/
│   ├── models.py           # Data models (FunctionInfo, CallEdge, CallChain, ...)
│   ├── callgraph.py        # Language-agnostic call graph builder + symbol resolver
│   ├── chain_enum.py       # DFS call chain enumerator
│   ├── analyzer.py         # Advanced analysis (complexity, coupling, dead code, ...)
│   ├── cache.py            # Incremental file-level cache
│   └── config.py           # .callchain.toml loading helpers
├── languages/
│   ├── base.py             # Plugin base class + registry
│   ├── python_lang.py      # Python plugin (tree-sitter)
│   ├── javascript_lang.py  # JS/TS plugin (tree-sitter)
│   ├── java_lang.py        # Java plugin (tree-sitter)
│   ├── go_lang.py          # Go plugin (tree-sitter)
│   ├── rust_lang.py        # Rust plugin (tree-sitter)
│   ├── c_lang.py           # C plugin (tree-sitter)
│   └── cpp_lang.py         # C++ plugin (tree-sitter)
└── output/
    ├── json_output.py      # JSON / JSONL
    ├── mermaid_output.py   # Mermaid diagrams
    ├── dot_output.py       # Graphviz DOT
    └── html_output.py      # Interactive HTML report
```

### Adding a new language

1. Create `src/callchain/languages/yourlang_lang.py`
2. Subclass `LanguagePlugin`, set `language` and `extensions`
3. Implement `parse_file()` and `extract_calls()` using tree-sitter
4. Import it in `cli.py` — registration is automatic

## Development

```bash
# Install in dev mode
pip install -e ".[dev]"

# Or use the bundled helper target
make install-dev

# Run tests
pytest

# Run tests with coverage
make coverage

# Format
make format

# Lint
make lint

# Type-check
make typecheck

# Audit dependencies for known vulnerabilities
make audit

# Export a CycloneDX SBOM
make sbom

# Build wheel + sdist
make build

# Validate built distributions
make release-check

# Validate release metadata only
make release-validate

# Render a Markdown or JSON summary for release-time corpus state
make release-corpus-state ARGS='--state build/release-corpus-baseline-state.json --markdown'

# Run corpus regression checks against local sample repos
make corpus-check

# Validate corpus source metadata and vendored sample provenance
make corpus-sources

# Sync source metadata from local sample repos
make corpus-sync

# Refresh a vendored sample checkout to a specific git ref and sync metadata
make corpus-refresh ARGS='click-src --ref <git-ref>'

# Materialize or update a vendored sample from its configured upstream archive
make corpus-materialize ARGS='click-src --ref <git-ref>'

# Verify a vendored sample's upstream archive checksum
make corpus-verify-archive ARGS='click-src'

# Capture a machine-readable corpus snapshot
make corpus-snapshot

# Capture the "official baseline" snapshot + benchmark artifact pair locally
make corpus-baseline

# Write a JSON benchmark report to build/corpus-benchmark.json
make benchmark-corpus

# Compare two stored corpus reports
make compare-corpus BASELINE=build/base.json CANDIDATE=build/head.json

# Compare structure only and fail if summary counts drift
make compare-corpus BASELINE=build/base.json CANDIDATE=build/head.json ARGS='--metric summary --fail-on-summary-drift'

# Render a Markdown review note for PR comments or job summaries
make compare-corpus BASELINE=build/base.json CANDIDATE=build/head.json ARGS='--metric summary --markdown'

# Maintainers can also run the "Corpus Maintenance" and "Corpus Baseline"
# workflows manually in GitHub Actions for remote maintenance and artifact capture.
# Pull requests also run "Corpus Baseline Compare", which downloads the latest
# official baseline artifact and checks the current branch for summary drift.
# Maintainers can waive intentional drift via the "corpus-drift-approved" label
# or the workflow_dispatch allow_summary_drift input.
# After a drift-approved PR is merged, "Corpus Baseline Refresh" automatically
# captures a post-merge baseline candidate artifact bundle for follow-up review.

# Bump version files and roll changelog notes
make bump-version VERSION=0.2.0a1

# Run the full local CI bundle
make ci
```

### Pre-commit

```bash
pre-commit install
pre-commit run --all-files
```

## Corpus Regression And Benchmarks

The repository includes a local corpus manifest at `test_repos/corpus.toml`. It tracks two tiny smoke repos plus a vendored snapshot of `click` so parser, resolver, and chain-enumeration behavior are exercised against code that is closer to a real project than unit fixtures alone.

```bash
# Validate corpus thresholds
python scripts/check_corpus.py

# Or use the Makefile shortcut
make corpus-check

# Validate source metadata for vendored or in-tree sample repos
python scripts/check_corpus_sources.py

# Refresh source_ref / version / content_sha256 from local sample repos
python scripts/sync_corpus_sources.py

# Refresh a vendored sample checkout to a specific git ref and sync metadata
python scripts/refresh_corpus_source.py click-src --ref <git-ref>

# One-step materialization workflow for vendored sources
python scripts/materialize_corpus_source.py click-src --ref <git-ref>

# Verify the rendered upstream archive for a vendored sample
python scripts/verify_corpus_source_archive.py click-src

# Capture a JSON snapshot suitable for later comparison
python scripts/check_corpus.py --json --output build/corpus-snapshot.json

# Capture the official local baseline snapshot + benchmark pair
make corpus-baseline

# Produce a JSON benchmark artifact
python scripts/benchmark_corpus.py --iterations 5 --warmup 1 --json --output build/corpus-benchmark.json

# Compare two stored corpus reports or snapshots
python scripts/compare_corpus_reports.py --baseline build/corpus-before.json --candidate build/corpus-after.json

# Compare structure only and fail if summary counts drift
python scripts/compare_corpus_reports.py \
  --baseline build/corpus-before.json \
  --candidate build/corpus-after.json \
  --metric summary \
  --fail-on-summary-drift

# Render Markdown for PR comments or GitHub job summaries
python scripts/compare_corpus_reports.py \
  --baseline build/corpus-before.json \
  --candidate build/corpus-after.json \
  --metric summary \
  --markdown
```

`scripts/check_corpus.py` is intended to be stable and CI-safe. `scripts/check_corpus_sources.py` validates where each corpus sample comes from and which license file covers it. `scripts/sync_corpus_sources.py` refreshes vendored source metadata such as `version`, `source_ref`, and `content_sha256` from the local checkout. `scripts/refresh_corpus_source.py` updates vendored git checkouts in place. `scripts/materialize_corpus_source.py` is the one-step maintainer workflow: it will use a git checkout when present, otherwise download and unpack the configured archive into the vendored snapshot path, then sync `sources.toml`. `scripts/verify_corpus_source_archive.py` is the explicit networked provenance check: it renders the configured `archive_url` (including `{ref}` placeholders), downloads the upstream archive, and verifies its recorded `archive_sha256`. `scripts/benchmark_corpus.py` is local-first by design, since shared CI runners produce timing noise that is not reliable enough for a required gate. `scripts/compare_corpus_reports.py` accepts either benchmark JSON or `check --json` snapshots, and `--metric summary --fail-on-summary-drift` turns it into a structure-only gate for parser/resolver drift review.

For remote dry-runs, maintainers can use the manual **Corpus Maintenance** GitHub Actions workflow. It validates current source metadata, optionally materializes a vendored source to a target ref on the runner, verifies the rendered upstream archive checksum, captures before/after corpus snapshots, compares them, and uploads the resulting artifacts for review.

For recurring official baseline capture, the scheduled/manual **Corpus Baseline** workflow uploads a canonical snapshot JSON, benchmark JSON, and `sources.toml` artifact bundle. Use that artifact pair as the review baseline before loosening corpus thresholds or accepting parser/resolver count shifts.

For branch-level enforcement, the **Corpus Baseline Compare** workflow downloads the latest successful official baseline artifact from the default branch, captures a fresh snapshot for the current branch, uploads JSON + Markdown compare artifacts, writes the Markdown report into the job summary, and upserts a sticky PR comment before failing on summary drift. On pull requests it also pulls the actual changed file list from the PR, annotates compare review hints with touched paths, and when `.github/CODEOWNERS` contains real rules it derives likely owners for the touched drift surface, a priority-sorted owner focus, reviewer candidates, and a review-request dry-run plan so reviewers can see both which modules moved and which maintainers would be the first request targets without issuing actual reviewer requests.

If the drift is intentional and reviewed, maintainers can waive the final failure by applying the `corpus-drift-approved` label to the PR or by setting `allow_summary_drift=true` when running the workflow manually. The workflow still publishes the compare report and records the waiver reason in the summary/comment.

When a drift-approved PR merges, the **Corpus Baseline Refresh** workflow runs on the merge commit, captures a fresh snapshot + benchmark pair, uploads them as a candidate baseline bundle, and posts a follow-up note back to the PR. That turns a waiver into an auditable post-merge artifact instead of a dead-end exception.

The **Release** workflow also renders the latest baseline/refresh state into a Markdown summary and uploads the raw JSON + rendered Markdown as release artifacts before it validates the tag. That summary now carries clickable run links, artifact links, branch/SHA/event context, and a release recommendation so reviewers can jump straight into the relevant baseline or refresh evidence. It also includes the latest successful **Corpus Baseline Compare** run for the release target branch plus a severity-sorted focus view of the most important drift cases, with both coarse category summaries and finer attribution hints that distinguish discovery, symbol extraction, call resolution, chain enumeration, parse-health, and non-structural drift. On top of that, the release renderer now derives likely modules to review, for example `src/callchain/languages/*.py`, `src/callchain/core/callgraph.py`, and `src/callchain/core/chain_enum.py`, and when the compare artifact carries PR changed-file context it also shows which of those likely modules were actually touched in the branch, with a priority-sorted changed-file focus that lifts core-module hits like `callgraph.py` ahead of lower-signal overlaps. If the compare artifact also carries CODEOWNERS-backed routing data, the release audit keeps owner hints, priority-sorted owner focus, reviewer candidates, and a review-request dry-run in the same summary and release body so release reviewers can jump from drift evidence straight to likely maintainers without triggering actual reviewer assignment. That keeps the branch-level drift review visible from the same audit record without dumping the entire compare table into release notes. After build artifacts are uploaded, the workflow writes a marker-backed **Release Corpus Audit** section into the GitHub Release body itself so the evidence survives outside Actions logs. Maintain the same view locally with `make release-corpus-state ARGS='--state build/release-corpus-baseline-state.json --markdown'`.

For packaging verification, the main CI workflow also runs an `install-smoke` matrix job. It builds the distribution artifact, installs it into a fresh virtual environment, analyzes `examples/smoke_repo`, and uploads a JSON smoke summary artifact for each tested Python version.

For registry-level verification, the repository also includes a manual **TestPyPI Rehearsal** workflow and a **Post-release Smoke** workflow. The first publishes the current build to TestPyPI via trusted publishing and then installs `callchain==<version>` back from TestPyPI for a fresh smoke run; the second runs after GitHub Releases are published and verifies that the package can be installed from PyPI and used against `examples/smoke_repo`.

## CLI Reference

```
callchain analyze <PROJECT_PATH> [OPTIONS]
callchain watch <PROJECT_PATH> [OPTIONS]

Options:
  -l, --lang TEXT         Languages to analyze (auto-detect if omitted)
  -d, --restrict-dir TEXT Restrict analysis to a subdirectory
  -e, --exclude TEXT      Exclude files or directories (repeatable, glob-aware)
  --max-depth INT         Max depth for chain enumeration [default: 20]
  --max-chains INT        Max chains to enumerate [default: 50000]
  --only-cross-file       Only emit chains with cross-file transitions
  -o, --output TEXT       Output file path
  --format [json|jsonl|dot|mermaid|html|summary]
                          Output format [default: summary]
  --cache / --no-cache    Enable or disable incremental cache
  --config TEXT           Path to config file
  --version               Show version
  --help                  Show help
```

## License

[Apache License 2.0](LICENSE)

## Project Policy

- Citation metadata for research and benchmarking: [CITATION.cff](CITATION.cff)
- Contributor behavior expectations: [Code of Conduct](CODE_OF_CONDUCT.md)
- Community support channels: [SUPPORT.md](SUPPORT.md)
- Security reporting guidance: [Security Policy](SECURITY.md)
- Release procedure: [Releasing Guide](RELEASING.md)
- Dependency update automation: [Dependabot config](.github/dependabot.yml)
- Dependency risk review on pull requests: [Dependency Review workflow](.github/workflows/dependency-review.yml)
- Draft release notes and PR labeling: [Release Drafter](.github/workflows/release-drafter.yml), [PR Autolabeler](.github/workflows/pr-autolabeler.yml)
- Repository label taxonomy: [Labels config](.github/labels.yml)
- Inactive issue and PR management: [Stale triage workflow](.github/workflows/stale.yml)
- Static security scanning: [CodeQL workflow](.github/workflows/codeql.yml)
- Dependency vulnerability audit and SBOM export: [Security audit workflow](.github/workflows/security-audit.yml)
