# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.0a2] - 2026-04-08
### Changed
- Vendored the `click-src` corpus snapshot into `test_repos/vendored/click` so corpus baseline, release validation, and GitHub-hosted workflows no longer depend on a local `downloaded_repos/` checkout.

## [0.2.0a1] - 2026-04-08
### Added
- Dependency review, release drafter, and PR autolabeler workflows for tighter repository automation
- Repository label taxonomy with sync workflow and stale issue/PR triage automation
- CodeQL scanning, scheduled dependency auditing, and CycloneDX SBOM export workflow automation
- Citation metadata, support guidance, and typed-package markers for downstream consumers
- Local corpus regression and benchmark tooling for smoke repos and a vendored real-world sample repository
- Corpus report comparison tooling for reviewing structural deltas and timing regressions between stored snapshots
- Corpus sample provenance registry and validation tooling for vendored source metadata
- Corpus source sync tooling to refresh vendored sample refs and checksums from local contents
- Vendored corpus source refresh tooling for git-backed sample upgrades with registry sync
- Vendored corpus archive verification tooling with recorded upstream archive URLs and checksums
- Vendored corpus materialization tooling for archive-backed snapshot reconstruction and one-step maintainer updates
- Manual GitHub Actions corpus-maintenance workflow for vendored source verify/materialize snapshot review
- Scheduled/manual GitHub Actions corpus-baseline workflow plus local baseline capture target
- GitHub Actions corpus-baseline-compare workflow for consuming the latest official baseline artifact on branch checks
- GitHub Actions corpus-baseline-refresh workflow for post-merge baseline candidate capture after drift-approved PRs
- Install-smoke tooling for validating built artifacts against a packaged example repository
- TestPyPI rehearsal and post-release smoke workflows for registry-level package verification
- Real-world example projects for Python service, TypeScript dashboard, and C++ library walkthroughs

### Changed
- New bug reports and feature requests now start with a `needs-triage` label for consistent maintainer intake
- Developer tooling now includes local vulnerability audit and SBOM generation targets, plus stricter pre-commit hygiene hooks
- Release tooling now keeps `CITATION.cff` aligned with package version and release date metadata
- CI now runs a dedicated corpus regression job, while timing benchmarks stay as local advisory tooling
- CI now runs a fresh-venv install-smoke matrix against the built distribution and packaged example repo
- Install-smoke tooling now supports package-index mode for TestPyPI/PyPI verification
- README and examples documentation now follow task-oriented first-user workflows instead of a single smoke-only path
- Corpus maintenance guidance now includes snapshot capture and explicit before/after report comparison
- Corpus maintenance now records upstream, version, and license metadata for each sample repository
- Corpus maintenance now records and can refresh vendored sample commit refs and content checksums
- Corpus report comparison now supports summary-only drift gates for stable parser/resolver snapshot review
- Corpus baseline compare automation now emits Markdown review notes and sticky PR comments alongside JSON artifacts
- Corpus baseline compare automation now cross-checks review hints against the PR changed-file list
- Corpus baseline compare automation now derives CODEOWNERS-backed owner hints for touched drift surfaces when repository ownership rules are configured
- Corpus baseline compare automation now derives a priority-sorted owner focus for reviewer routing from CODEOWNERS-backed drift matches
- Corpus baseline compare automation now derives reviewer candidates and a review-request dry-run from CODEOWNERS-backed routing data without issuing actual reviewer requests
- Release corpus audit rendering now trims compare evidence into a severity-sorted focus view for the most important drift cases
- Release corpus audit summaries now classify compare drift into parser, resolver, parse-health, and non-structural buckets
- Release corpus audit summaries now include finer discovery, symbol-extraction, call-resolution, and chain-enumeration attribution hints
- Release corpus audit summaries now derive likely implementation modules to review from drift attribution signals
- Release corpus audit summaries now retain compare-time owner hints so release reviewers can route drift evidence to likely maintainers
- Release corpus audit summaries now retain compare-time owner focus so release reviewers can see the highest-priority likely maintainers first
- Release corpus audit summaries now retain compare-time reviewer candidates and review-request dry-run output for release review routing
- Release corpus audit summaries now retain compare changed-file context and show overlap between branch edits and likely review modules
- Release corpus audit summaries now priority-sort changed-file overlap so core module hits surface first
- Corpus baseline compare automation now supports explicit maintainer drift waivers via PR label or manual workflow input
- Release automation now publishes a rendered corpus baseline state summary and raw state artifact alongside release validation, with direct run/artifact links, branch compare evidence, compare-report excerpts, review context, and an auditable GitHub Release body section

## [0.1.0] - 2026-04-06

### Added
- Multi-language support: Python, JavaScript, TypeScript, Java, Go, Rust, C, C++
- tree-sitter based parsing for all supported languages
- Unified CLI (`callchain analyze`) with rich terminal output
- Watch mode (`callchain watch`) for incremental local feedback
- Call graph construction with cross-file symbol resolution
- DFS-based call chain enumeration with depth/count limits
- Cyclomatic complexity calculation per function
- Module coupling analysis (fan-in, fan-out, instability)
- Circular dependency detection with deduplication
- Dead code detection (functions never called)
- Hotspot function analysis (most frequently called)
- Unused import detection
- Class hierarchy / inheritance analysis
- Incremental file cache (`--cache`)
- Exclude globs (`--exclude`)
- `.callchain.toml` project configuration support
- Output formats: JSON, JSONL, Graphviz DOT, Mermaid, interactive HTML
- Directory scoping (`--restrict-dir`)
- Cross-file-only chain filtering (`--only-cross-file`)
- Parse error reporting with file-level warnings
- Plugin architecture for easy language extension
- GitHub Actions CI (Python 3.10-3.13, Ubuntu + macOS)
- 102 automated tests covering language plugins, CLI integration, cache/config helpers, metadata checks, and report writers
- release tooling for version bumps and release metadata validation
- maintainer release guide (`RELEASING.md`)

### Changed
- CI now uses pip caching, enforces a 90% coverage gate, and validates package builds
- Added pre-commit, Makefile developer shortcuts, editorconfig, and GitHub issue/PR templates
- Added release workflow, security/code-of-conduct docs, and version/changelog consistency checks
- Release workflow now uploads distribution artifacts and generates build provenance attestations
- Dependabot now tracks Python and GitHub Actions dependencies
