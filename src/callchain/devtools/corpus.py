"""Corpus regression and benchmark helpers for local sample repositories."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from time import perf_counter
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile

from callchain.core.analyzer import Analyzer
from callchain.core.callgraph import CallGraphBuilder
from callchain.core.chain_enum import ChainEnumerator
from callchain.core.models import Language


DEFAULT_MANIFEST = Path("test_repos/corpus.toml")
DEFAULT_SOURCE_REGISTRY = Path("test_repos/sources.toml")
MANIFEST_VERSION = 1
_TIMING_METRICS = ("build_seconds", "chain_seconds", "analysis_seconds", "total_seconds")
_COMPARE_METRICS = _TIMING_METRICS + ("summary",)
_SOURCE_KINDS = {"local", "vendored"}

_LANGUAGE_ALIASES = {
    "python": Language.PYTHON,
    "py": Language.PYTHON,
    "javascript": Language.JAVASCRIPT,
    "js": Language.JAVASCRIPT,
    "typescript": Language.TYPESCRIPT,
    "ts": Language.TYPESCRIPT,
    "java": Language.JAVA,
    "go": Language.GO,
    "rust": Language.RUST,
    "rs": Language.RUST,
    "c": Language.C,
    "cpp": Language.CPP,
    "c++": Language.CPP,
    "cc": Language.CPP,
}

_COMPARE_REVIEW_HINTS = (
    (
        "discovery",
        "discovery",
        ("src/callchain/languages/base.py", "src/callchain/core/callgraph.py"),
        "Review file discovery, skip-dir rules, path filtering, and language auto-detection.",
    ),
    (
        "symbol_extraction",
        "symbol-extraction",
        ("src/callchain/languages/*.py",),
        "Review language parsers that extract functions, classes, methods, imports, and variables.",
    ),
    (
        "call_resolution",
        "call-resolution",
        ("src/callchain/core/callgraph.py", "src/callchain/languages/*.py"),
        "Review raw call extraction and cross-file edge resolution.",
    ),
    (
        "chain_enumeration",
        "chain-enumeration",
        ("src/callchain/core/chain_enum.py",),
        "Review chain traversal, depth/count limits, and cross-file filtering.",
    ),
    (
        "parse_health",
        "parse-health",
        ("src/callchain/languages/*.py", "src/callchain/core/callgraph.py"),
        "Review parser failures, parse-error collection, and file-level error handling.",
    ),
    (
        "non_structural",
        "non-structural",
        ("src/callchain/devtools/corpus.py", ".github/workflows/corpus-baseline-compare.yml"),
        "Review corpus thresholds, compare rendering, and workflow gating rather than structural analysis.",
    ),
)


@dataclass(frozen=True)
class CorpusEntry:
    name: str
    path: str
    languages: tuple[Language, ...] = ()
    exclude: tuple[str, ...] = ()
    restrict_dir: str | None = None
    max_depth: int = 20
    max_chains: int = 50_000
    only_cross_file: bool = False
    cache: bool = False
    min_files: int = 0
    min_functions: int = 0
    min_classes: int = 0
    min_edges: int = 0
    min_chains: int = 0
    max_parse_errors: int = 0


@dataclass(frozen=True)
class CorpusRun:
    name: str
    path: str
    languages: tuple[str, ...]
    files: int
    functions: int
    classes: int
    edges: int
    chains: int
    parse_errors: int
    build_seconds: float
    chain_seconds: float
    analysis_seconds: float
    total_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "languages": list(self.languages),
            "summary": {
                "files": self.files,
                "functions": self.functions,
                "classes": self.classes,
                "edges": self.edges,
                "chains": self.chains,
                "parse_errors": self.parse_errors,
            },
            "timings": {
                "build_seconds": round(self.build_seconds, 6),
                "chain_seconds": round(self.chain_seconds, 6),
                "analysis_seconds": round(self.analysis_seconds, 6),
                "total_seconds": round(self.total_seconds, 6),
            },
        }


@dataclass(frozen=True)
class CorpusSource:
    name: str
    kind: str
    analyzed_path: str
    root_path: str
    license_spdx: str
    license_file: str
    upstream_url: str | None = None
    version: str | None = None
    source_ref: str | None = None
    archive_url: str | None = None
    archive_sha256: str | None = None
    content_sha256: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "analyzed_path": self.analyzed_path,
            "root_path": self.root_path,
            "license_spdx": self.license_spdx,
            "license_file": self.license_file,
            "upstream_url": self.upstream_url,
            "version": self.version,
            "source_ref": self.source_ref,
            "archive_url": self.archive_url,
            "archive_sha256": self.archive_sha256,
            "content_sha256": self.content_sha256,
            "notes": self.notes,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Corpus regression and benchmark helpers for CallChain.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Run regression checks against the local corpus manifest.")
    check_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the corpus manifest TOML file.")
    check_parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary.")
    check_parser.add_argument("--output", default=None, help="Optional output file for the report.")

    benchmark_parser = subparsers.add_parser("benchmark", help="Benchmark local corpus analysis runs.")
    benchmark_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the corpus manifest TOML file.")
    benchmark_parser.add_argument("--iterations", type=int, default=3, help="Measured iterations per corpus entry.")
    benchmark_parser.add_argument("--warmup", type=int, default=1, help="Warmup iterations to discard before timing.")
    benchmark_parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary.")
    benchmark_parser.add_argument("--output", default=None, help="Optional output file for the report.")

    sources_parser = subparsers.add_parser(
        "sources",
        help="Validate corpus source metadata and vendored sample provenance.",
    )
    sources_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the corpus manifest TOML file.")
    sources_parser.add_argument(
        "--registry",
        default=str(DEFAULT_SOURCE_REGISTRY),
        help="Path to the corpus source registry TOML file.",
    )
    sources_parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary.")
    sources_parser.add_argument("--output", default=None, help="Optional output file for the report.")

    sync_sources_parser = subparsers.add_parser(
        "sync-sources",
        help="Sync corpus source metadata from local sample repositories.",
    )
    sync_sources_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the corpus manifest TOML file.")
    sync_sources_parser.add_argument(
        "--registry",
        default=str(DEFAULT_SOURCE_REGISTRY),
        help="Path to the corpus source registry TOML file.",
    )
    sync_sources_parser.add_argument("--dry-run", action="store_true", help="Compute updates without writing the registry.")
    sync_sources_parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary.")
    sync_sources_parser.add_argument("--output", default=None, help="Optional output file for the report.")

    refresh_source_parser = subparsers.add_parser(
        "refresh-source",
        help="Refresh a vendored corpus source to a specific git ref and sync its registry metadata.",
    )
    refresh_source_parser.add_argument(
        "source_name",
        help="Name of the vendored corpus source entry to refresh.",
    )
    refresh_source_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the corpus manifest TOML file.")
    refresh_source_parser.add_argument(
        "--registry",
        default=str(DEFAULT_SOURCE_REGISTRY),
        help="Path to the corpus source registry TOML file.",
    )
    refresh_source_parser.add_argument("--ref", required=True, help="Git ref (tag, branch, or commit) to check out.")
    refresh_source_parser.add_argument("--remote", default="origin", help="Git remote to fetch before checkout.")
    refresh_source_parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip git fetch and use only refs already present in the local checkout.",
    )
    refresh_source_parser.add_argument(
        "--verify-archive",
        action="store_true",
        help="Download the rendered archive URL for the refreshed ref and update archive_sha256.",
    )
    refresh_source_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Network timeout in seconds for archive verification.",
    )
    refresh_source_parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary.")
    refresh_source_parser.add_argument("--output", default=None, help="Optional output file for the report.")

    materialize_source_parser = subparsers.add_parser(
        "materialize-source",
        help="Materialize a vendored corpus source at a target ref and sync registry metadata.",
    )
    materialize_source_parser.add_argument("source_name", help="Name of the vendored corpus source entry to materialize.")
    materialize_source_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the corpus manifest TOML file.")
    materialize_source_parser.add_argument(
        "--registry",
        default=str(DEFAULT_SOURCE_REGISTRY),
        help="Path to the corpus source registry TOML file.",
    )
    materialize_source_parser.add_argument("--ref", required=True, help="Git ref (tag, branch, or commit) to materialize.")
    materialize_source_parser.add_argument("--remote", default="origin", help="Git remote to fetch before checkout when using a git-backed snapshot.")
    materialize_source_parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip git fetch and use only refs already present in the local checkout.",
    )
    materialize_source_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Network timeout in seconds for archive download and verification.",
    )
    materialize_source_parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary.")
    materialize_source_parser.add_argument("--output", default=None, help="Optional output file for the report.")

    verify_archive_parser = subparsers.add_parser(
        "verify-archive",
        help="Download a vendored corpus source archive and verify its recorded checksum.",
    )
    verify_archive_parser.add_argument("source_name", help="Name of the vendored corpus source entry to verify.")
    verify_archive_parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the corpus manifest TOML file.")
    verify_archive_parser.add_argument(
        "--registry",
        default=str(DEFAULT_SOURCE_REGISTRY),
        help="Path to the corpus source registry TOML file.",
    )
    verify_archive_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Network timeout in seconds for archive verification.",
    )
    verify_archive_parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary.")
    verify_archive_parser.add_argument("--output", default=None, help="Optional output file for the report.")

    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare two corpus reports (benchmark JSON or check JSON snapshots).",
    )
    compare_parser.add_argument("--baseline", required=True, help="Path to the baseline corpus report JSON.")
    compare_parser.add_argument("--candidate", required=True, help="Path to the candidate corpus report JSON.")
    compare_parser.add_argument(
        "--metric",
        choices=_COMPARE_METRICS,
        default="total_seconds",
        help="Metric to compare. Use 'summary' to compare structural counts only.",
    )
    compare_parser.add_argument(
        "--max-regression-pct",
        type=float,
        default=15.0,
        help="Maximum allowed slowdown percentage before compare fails.",
    )
    compare_parser.add_argument(
        "--fail-on-summary-drift",
        action="store_true",
        help="When --metric summary is used, fail if any summary counters differ.",
    )
    compare_parser.add_argument(
        "--changed-files",
        default=None,
        help="Optional JSON or newline-delimited file listing changed repository paths for review-hint overlap.",
    )
    compare_parser.add_argument(
        "--codeowners",
        default=None,
        help="Optional CODEOWNERS file used to annotate review-owner hints for changed repository paths.",
    )
    compare_format_group = compare_parser.add_mutually_exclusive_group()
    compare_format_group.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary.")
    compare_format_group.add_argument(
        "--markdown",
        action="store_true",
        help="Emit a Markdown summary suitable for PR comments or job summaries.",
    )
    compare_parser.add_argument("--output", default=None, help="Optional output file for the report.")

    args = parser.parse_args(argv)

    if args.command == "check":
        manifest_path = Path(args.manifest).resolve()
        runs = check_manifest(manifest_path)
        report = {"manifest": str(manifest_path), "projects": [run.to_dict() for run in runs]}
        rendered = json.dumps(report, indent=2) if args.json else format_check_report(runs, manifest_path)
        _write_output(rendered, args.output)
        return 0

    if args.command == "benchmark":
        manifest_path = Path(args.manifest).resolve()
        report = benchmark_manifest(manifest_path, iterations=args.iterations, warmup=args.warmup)
        rendered = json.dumps(report, indent=2) if args.json else format_benchmark_report(report)
        _write_output(rendered, args.output)
        return 0

    if args.command == "sources":
        manifest_path = Path(args.manifest).resolve()
        registry_path = Path(args.registry).resolve()
        report = source_inventory(manifest_path, registry_path)
        rendered = json.dumps(report, indent=2) if args.json else format_source_inventory(report)
        _write_output(rendered, args.output)
        return 0

    if args.command == "sync-sources":
        manifest_path = Path(args.manifest).resolve()
        registry_path = Path(args.registry).resolve()
        report = sync_source_registry(manifest_path, registry_path, dry_run=args.dry_run)
        rendered = json.dumps(report, indent=2) if args.json else format_sync_report(report)
        _write_output(rendered, args.output)
        return 0

    if args.command == "refresh-source":
        manifest_path = Path(args.manifest).resolve()
        registry_path = Path(args.registry).resolve()
        report = refresh_vendored_source(
            manifest_path,
            registry_path,
            source_name=args.source_name,
            ref=args.ref,
            remote=args.remote,
            fetch=not args.no_fetch,
            verify_archive=args.verify_archive,
            timeout=args.timeout,
        )
        rendered = json.dumps(report, indent=2) if args.json else format_refresh_report(report)
        _write_output(rendered, args.output)
        return 0

    if args.command == "materialize-source":
        manifest_path = Path(args.manifest).resolve()
        registry_path = Path(args.registry).resolve()
        report = materialize_vendored_source(
            manifest_path,
            registry_path,
            source_name=args.source_name,
            ref=args.ref,
            remote=args.remote,
            fetch=not args.no_fetch,
            timeout=args.timeout,
        )
        rendered = json.dumps(report, indent=2) if args.json else format_materialize_report(report)
        _write_output(rendered, args.output)
        return 0

    if args.command == "verify-archive":
        manifest_path = Path(args.manifest).resolve()
        registry_path = Path(args.registry).resolve()
        report = verify_source_archive(
            manifest_path,
            registry_path,
            source_name=args.source_name,
            timeout=args.timeout,
        )
        rendered = json.dumps(report, indent=2) if args.json else format_archive_verification(report)
        _write_output(rendered, args.output)
        return 0

    report = compare_reports(
        Path(args.baseline).resolve(),
        Path(args.candidate).resolve(),
        metric=args.metric,
        max_regression_pct=args.max_regression_pct,
        fail_on_summary_drift=args.fail_on_summary_drift,
        changed_files=_load_changed_files(Path(args.changed_files).resolve()) if args.changed_files else None,
        codeowners_path=Path(args.codeowners).resolve() if args.codeowners else None,
    )
    if args.json:
        rendered = json.dumps(report, indent=2)
    elif args.markdown:
        rendered = format_compare_markdown(report)
    else:
        rendered = format_compare_report(report)
    _write_output(rendered, args.output)
    return 0


def load_manifest(path: Path) -> list[CorpusEntry]:
    toml = _import_toml_module()
    data = toml.loads(path.read_text(encoding="utf-8"))
    version = data.get("version", MANIFEST_VERSION)
    if version != MANIFEST_VERSION:
        raise ValueError(f"Unsupported corpus manifest version {version!r}; expected {MANIFEST_VERSION}.")

    projects = data.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ValueError("Corpus manifest must define at least one [[projects]] entry.")

    entries: list[CorpusEntry] = []
    for project in projects:
        if not isinstance(project, dict):
            raise ValueError("Each corpus project entry must be a TOML table.")
        entries.append(_parse_entry(project))
    return entries


def check_manifest(manifest_path: Path) -> list[CorpusRun]:
    manifest_path = manifest_path.resolve()
    entries = load_manifest(manifest_path)
    manifest_root = manifest_path.parent

    runs: list[CorpusRun] = []
    failures: list[str] = []
    for entry in entries:
        run = run_entry(entry, manifest_root)
        runs.append(run)
        failures.extend(_validate_run(entry, run))

    if failures:
        joined = "\n".join(f"- {failure}" for failure in failures)
        raise ValueError(f"Corpus regression check failed:\n{joined}")

    return runs


def benchmark_manifest(
    manifest_path: Path,
    *,
    iterations: int = 3,
    warmup: int = 1,
    runner: Callable[[CorpusEntry, Path], CorpusRun] | None = None,
) -> dict[str, Any]:
    if iterations < 1:
        raise ValueError("Benchmark iterations must be at least 1.")
    if warmup < 0:
        raise ValueError("Benchmark warmup iterations must be 0 or greater.")

    manifest_path = manifest_path.resolve()
    entries = load_manifest(manifest_path)
    manifest_root = manifest_path.parent
    runner = runner or run_entry

    cases: list[dict[str, Any]] = []
    for entry in entries:
        for _ in range(warmup):
            runner(entry, manifest_root)

        measured = [runner(entry, manifest_root) for _ in range(iterations)]
        latest = measured[-1]
        cases.append(
            {
                "name": entry.name,
                "path": entry.path,
                "languages": list(latest.languages),
                "iterations": iterations,
                "warmup": warmup,
                "summary": latest.to_dict()["summary"],
                "timings": _summarize_timings(measured),
            }
        )

    return {
        "manifest": str(manifest_path),
        "iterations": iterations,
        "warmup": warmup,
        "cases": cases,
    }


def load_source_registry(path: Path) -> list[CorpusSource]:
    toml = _import_toml_module()
    data = toml.loads(path.read_text(encoding="utf-8"))
    version = data.get("version", MANIFEST_VERSION)
    if version != MANIFEST_VERSION:
        raise ValueError(f"Unsupported source registry version {version!r}; expected {MANIFEST_VERSION}.")

    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Corpus source registry must define at least one [[sources]] entry.")

    registry: list[CorpusSource] = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Each corpus source entry must be a TOML table.")
        registry.append(_parse_source_entry(source))
    return registry


def run_entry(entry: CorpusEntry, manifest_root: Path) -> CorpusRun:
    project_path = (manifest_root / entry.path).resolve()
    if not project_path.exists():
        raise ValueError(f"Corpus project path does not exist: {entry.path}")

    start = perf_counter()
    builder = CallGraphBuilder(project_path, use_cache=entry.cache, exclude=list(entry.exclude))
    result = builder.build(
        languages=list(entry.languages) if entry.languages else None,
        restrict_dir=entry.restrict_dir,
    )
    after_build = perf_counter()

    enumerator = ChainEnumerator(
        edges=result.edges,
        max_depth=entry.max_depth,
        max_chains=entry.max_chains,
        only_cross_file=entry.only_cross_file,
        restrict_dir=entry.restrict_dir,
    )
    result.chains = enumerator.enumerate()
    after_chain = perf_counter()

    Analyzer(result).run_all()
    after_analysis = perf_counter()

    return CorpusRun(
        name=entry.name,
        path=entry.path,
        languages=tuple(language.value for language in result.languages_detected),
        files=result.total_files,
        functions=result.total_functions,
        classes=result.total_classes,
        edges=len(result.edges),
        chains=len(result.chains),
        parse_errors=len(result.parse_errors),
        build_seconds=after_build - start,
        chain_seconds=after_chain - after_build,
        analysis_seconds=after_analysis - after_chain,
        total_seconds=after_analysis - start,
    )


def source_inventory(manifest_path: Path, registry_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    registry_path = registry_path.resolve()
    manifest_root = manifest_path.parent

    manifest_entries = load_manifest(manifest_path)
    sources = load_source_registry(registry_path)
    manifest_by_name = {entry.name: entry for entry in manifest_entries}
    source_by_name = _dedupe_sources(sources)
    _validate_registry_alignment(manifest_by_name, source_by_name)

    entries: list[dict[str, Any]] = []
    for name in sorted(manifest_by_name):
        entry = manifest_by_name[name]
        source = source_by_name[name]
        entries.append(_validate_source_entry(source, entry, manifest_root))

    return {
        "manifest": str(manifest_path),
        "registry": str(registry_path),
        "entries": entries,
    }


def sync_source_registry(manifest_path: Path, registry_path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    registry_path = registry_path.resolve()
    manifest_root = manifest_path.parent

    manifest_entries = load_manifest(manifest_path)
    sources = load_source_registry(registry_path)
    manifest_by_name = {entry.name: entry for entry in manifest_entries}
    source_by_name = _dedupe_sources(sources)
    _validate_registry_alignment(manifest_by_name, source_by_name)

    updated_sources: list[CorpusSource] = []
    changes: list[dict[str, Any]] = []
    for source in sources:
        entry = manifest_by_name[source.name]
        updated = _sync_source_entry(source, entry, manifest_root)
        updated_sources.append(updated)
        changed_fields = _diff_source_fields(source, updated)
        if changed_fields:
            changes.append({"name": source.name, "fields": changed_fields})

    if not dry_run:
        _write_source_registry(registry_path, updated_sources)

    return {
        "manifest": str(manifest_path),
        "registry": str(registry_path),
        "dry_run": dry_run,
        "written": not dry_run,
        "changed": bool(changes),
        "changes": changes,
        "entries": [source.to_dict() for source in updated_sources],
    }


def refresh_vendored_source(
    manifest_path: Path,
    registry_path: Path,
    *,
    source_name: str,
    ref: str,
    remote: str = "origin",
    fetch: bool = True,
    verify_archive: bool = False,
    timeout: float = 30.0,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    registry_path = registry_path.resolve()
    manifest_root = manifest_path.parent

    manifest_entries = load_manifest(manifest_path)
    sources = load_source_registry(registry_path)
    manifest_by_name = {entry.name: entry for entry in manifest_entries}
    source_by_name = _dedupe_sources(sources)
    _validate_registry_alignment(manifest_by_name, source_by_name)

    source = source_by_name.get(source_name)
    if source is None:
        raise ValueError(f"Corpus source registry does not contain source {source_name!r}.")
    if source.kind != "vendored":
        raise ValueError(f"Corpus source {source_name!r} is not vendored and cannot be refreshed.")

    entry = manifest_by_name[source_name]
    root_path = (manifest_root / source.root_path).resolve()
    if not root_path.exists():
        raise ValueError(f"Corpus source root path does not exist for {source.name!r}: {source.root_path}")
    if _resolve_git_dir(root_path) is None:
        raise ValueError(f"Vendored corpus source {source.name!r} is not backed by a local git checkout.")

    _ensure_git_clean(root_path, source.name)

    previous_ref = _detect_git_source_ref(root_path)
    if fetch:
        _run_git(root_path, "fetch", "--tags", remote)
    resolved_ref = _resolve_git_ref(root_path, ref)
    _run_git(root_path, "checkout", "--detach", resolved_ref)

    refreshed_source = _sync_source_entry(source, entry, manifest_root)
    archive_verification: dict[str, Any] | None = None
    if verify_archive:
        archive_url = _render_archive_url(refreshed_source)
        assert archive_url is not None
        downloaded_archive_sha256, archive_bytes = _download_archive_sha256(archive_url, timeout=timeout)
        refreshed_source = CorpusSource(
            name=refreshed_source.name,
            kind=refreshed_source.kind,
            analyzed_path=refreshed_source.analyzed_path,
            root_path=refreshed_source.root_path,
            license_spdx=refreshed_source.license_spdx,
            license_file=refreshed_source.license_file,
            upstream_url=refreshed_source.upstream_url,
            version=refreshed_source.version,
            source_ref=refreshed_source.source_ref,
            archive_url=refreshed_source.archive_url,
            archive_sha256=downloaded_archive_sha256,
            content_sha256=refreshed_source.content_sha256,
            notes=refreshed_source.notes,
        )
        archive_verification = {
            "archive_url": archive_url,
            "archive_sha256": downloaded_archive_sha256,
            "archive_bytes": archive_bytes,
            "verified": True,
        }
    updated_sources = [refreshed_source if item.name == source_name else item for item in sources]
    changed_fields = _diff_source_fields(source, refreshed_source)
    _write_source_registry(registry_path, updated_sources)

    return {
        "manifest": str(manifest_path),
        "registry": str(registry_path),
        "name": source_name,
        "root_path": str(root_path),
        "requested_ref": ref,
        "previous_ref": previous_ref,
        "resolved_ref": resolved_ref,
        "remote": remote if fetch else None,
        "fetched": fetch,
        "changed": bool(changed_fields),
        "changes": changed_fields,
        "archive_verification": archive_verification,
        "entry": refreshed_source.to_dict(),
    }


def materialize_vendored_source(
    manifest_path: Path,
    registry_path: Path,
    *,
    source_name: str,
    ref: str,
    remote: str = "origin",
    fetch: bool = True,
    timeout: float = 30.0,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    registry_path = registry_path.resolve()
    manifest_root = manifest_path.parent

    manifest_entries = load_manifest(manifest_path)
    sources = load_source_registry(registry_path)
    manifest_by_name = {entry.name: entry for entry in manifest_entries}
    source_by_name = _dedupe_sources(sources)
    _validate_registry_alignment(manifest_by_name, source_by_name)

    source = source_by_name.get(source_name)
    if source is None:
        raise ValueError(f"Corpus source registry does not contain source {source_name!r}.")
    if source.kind != "vendored":
        raise ValueError(f"Corpus source {source_name!r} is not vendored and cannot be materialized.")

    entry = manifest_by_name[source_name]
    root_path = (manifest_root / source.root_path).resolve()
    if _resolve_git_dir(root_path) is not None:
        report = refresh_vendored_source(
            manifest_path,
            registry_path,
            source_name=source_name,
            ref=ref,
            remote=remote,
            fetch=fetch,
            verify_archive=True,
            timeout=timeout,
        )
        report["mode"] = "git"
        return report

    archive_url = _render_archive_url_for_ref(source, ref)
    if archive_url is None:
        raise ValueError(f"Vendored corpus source {source_name!r} must define an archive_url.")

    archive_bytes = _download_archive_bytes(archive_url, timeout=timeout)
    downloaded_archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    archive_bytes_len = len(archive_bytes)

    with tempfile.TemporaryDirectory(prefix="callchain-corpus-materialize-") as temp_dir:
        extracted_parent = Path(temp_dir)
        extracted_root = _extract_archive_bytes(archive_bytes, extracted_parent)
        _replace_tree(root_path, extracted_root)

    staged_source = CorpusSource(
        name=source.name,
        kind=source.kind,
        analyzed_path=source.analyzed_path,
        root_path=source.root_path,
        license_spdx=source.license_spdx,
        license_file=source.license_file,
        upstream_url=source.upstream_url,
        version=source.version,
        source_ref=ref,
        archive_url=source.archive_url,
        archive_sha256=downloaded_archive_sha256,
        content_sha256=source.content_sha256,
        notes=source.notes,
    )
    materialized_source = _sync_source_entry(staged_source, entry, manifest_root)
    updated_sources = [materialized_source if item.name == source_name else item for item in sources]
    changed_fields = _diff_source_fields(source, materialized_source)
    _write_source_registry(registry_path, updated_sources)

    return {
        "manifest": str(manifest_path),
        "registry": str(registry_path),
        "name": source_name,
        "mode": "archive",
        "root_path": str(root_path),
        "requested_ref": ref,
        "previous_ref": source.source_ref,
        "resolved_ref": ref,
        "fetched": False,
        "changed": bool(changed_fields),
        "changes": changed_fields,
        "archive_verification": {
            "archive_url": archive_url,
            "archive_sha256": downloaded_archive_sha256,
            "archive_bytes": archive_bytes_len,
            "verified": True,
        },
        "entry": materialized_source.to_dict(),
    }


def verify_source_archive(
    manifest_path: Path,
    registry_path: Path,
    *,
    source_name: str,
    timeout: float = 30.0,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    registry_path = registry_path.resolve()
    manifest_root = manifest_path.parent

    manifest_entries = load_manifest(manifest_path)
    sources = load_source_registry(registry_path)
    manifest_by_name = {entry.name: entry for entry in manifest_entries}
    source_by_name = _dedupe_sources(sources)
    _validate_registry_alignment(manifest_by_name, source_by_name)

    source = source_by_name.get(source_name)
    if source is None:
        raise ValueError(f"Corpus source registry does not contain source {source_name!r}.")
    entry = manifest_by_name[source_name]
    payload = _validate_source_entry(source, entry, manifest_root)

    if source.kind != "vendored":
        raise ValueError(f"Corpus source {source_name!r} is not vendored and does not publish an upstream archive.")
    archive_url = payload.get("rendered_archive_url")
    assert isinstance(archive_url, str) and archive_url

    downloaded_archive_sha256, archive_bytes = _download_archive_sha256(archive_url, timeout=timeout)
    if downloaded_archive_sha256 != source.archive_sha256:
        raise ValueError(
            f"Vendored corpus source {source_name!r} archive_sha256 {source.archive_sha256!r} does not match "
            f"downloaded archive checksum {downloaded_archive_sha256!r}."
        )

    return {
        "manifest": str(manifest_path),
        "registry": str(registry_path),
        "name": source_name,
        "archive_url": archive_url,
        "source_ref": source.source_ref,
        "expected_archive_sha256": source.archive_sha256,
        "downloaded_archive_sha256": downloaded_archive_sha256,
        "archive_bytes": archive_bytes,
        "verified": True,
    }


def format_check_report(runs: list[CorpusRun], manifest_path: Path) -> str:
    lines = [f"Corpus regression check passed: {manifest_path}"]
    for run in runs:
        lines.append(
            "  "
            f"{run.name}: files={run.files}, functions={run.functions}, classes={run.classes}, "
            f"edges={run.edges}, chains={run.chains}, parse_errors={run.parse_errors}, "
            f"total={run.total_seconds:.3f}s"
        )
    return "\n".join(lines)


def format_benchmark_report(report: dict[str, Any]) -> str:
    lines = [
        f"Corpus benchmark report: {report['manifest']}",
        f"Iterations={report['iterations']}, Warmup={report['warmup']}",
    ]
    for case in report["cases"]:
        total = case["timings"]["total_seconds"]
        lines.append(
            "  "
            f"{case['name']}: total median={total['median']:.3f}s "
            f"(min={total['min']:.3f}s, max={total['max']:.3f}s)"
        )
    return "\n".join(lines)


def format_source_inventory(report: dict[str, Any]) -> str:
    lines = [
        f"Corpus source inventory passed: {report['registry']}",
        f"Manifest: {report['manifest']}",
    ]
    for entry in report["entries"]:
        suffix = ""
        if entry["kind"] == "vendored":
            suffix += f", upstream={entry['upstream_url']}, version={entry['version']}"
            if entry.get("source_ref"):
                suffix += f", ref={entry['source_ref']}"
            if entry.get("archive_sha256"):
                suffix += f", archive_sha256={entry['archive_sha256']}"
        if entry.get("content_sha256"):
            suffix += f", sha256={entry['content_sha256']}"
        lines.append(
            "  "
            f"{entry['name']}: kind={entry['kind']}, path={entry['analyzed_path']}, "
            f"license={entry['license_spdx']}{suffix}"
        )
    return "\n".join(lines)


def format_sync_report(report: dict[str, Any]) -> str:
    action = "previewed" if report["dry_run"] else "synced"
    lines = [
        f"Corpus source registry {action}: {report['registry']}",
        f"Manifest: {report['manifest']}",
    ]
    if report["changes"]:
        for change in report["changes"]:
            lines.append(f"  {change['name']}: updated {', '.join(change['fields'])}")
    else:
        lines.append("  No source metadata changes detected.")
    return "\n".join(lines)


def format_refresh_report(report: dict[str, Any]) -> str:
    lines = [
        f"Vendored corpus source refreshed: {report['registry']}",
        f"Manifest: {report['manifest']}",
    ]
    if report["fetched"] and report["remote"]:
        lines.append(f"  fetched remote {report['remote']} before checkout")
    lines.append(
        "  "
        f"{report['name']}: {report['previous_ref'] or 'unknown'} -> {report['resolved_ref']} "
        f"(requested {report['requested_ref']})"
    )
    if report["changes"]:
        lines.append(f"  updated {', '.join(report['changes'])}")
    else:
        lines.append("  No source metadata changes detected.")
    if report.get("archive_verification"):
        archive_verification = report["archive_verification"]
        lines.append(
            "  "
            f"verified archive {archive_verification['archive_url']} "
            f"({archive_verification['archive_bytes']} bytes)"
        )
    return "\n".join(lines)


def format_materialize_report(report: dict[str, Any]) -> str:
    lines = [
        f"Vendored corpus source materialized: {report['registry']}",
        f"Manifest: {report['manifest']}",
        f"  mode={report['mode']}",
    ]
    lines.append(
        "  "
        f"{report['name']}: {report['previous_ref'] or 'unknown'} -> {report['resolved_ref']} "
        f"(requested {report['requested_ref']})"
    )
    if report["changes"]:
        lines.append(f"  updated {', '.join(report['changes'])}")
    else:
        lines.append("  No source metadata changes detected.")
    archive_verification = report.get("archive_verification")
    if archive_verification:
        lines.append(
            "  "
            f"verified archive {archive_verification['archive_url']} "
            f"({archive_verification['archive_bytes']} bytes)"
        )
    return "\n".join(lines)


def format_archive_verification(report: dict[str, Any]) -> str:
    lines = [
        f"Vendored corpus archive verified: {report['registry']}",
        f"Manifest: {report['manifest']}",
        "  "
        f"{report['name']}: ref={report['source_ref']}, sha256={report['downloaded_archive_sha256']}, "
        f"bytes={report['archive_bytes']}",
        f"  archive={report['archive_url']}",
    ]
    return "\n".join(lines)


def compare_reports(
    baseline_path: Path,
    candidate_path: Path,
    *,
    metric: str = "total_seconds",
    max_regression_pct: float = 15.0,
    fail_on_summary_drift: bool = False,
    changed_files: list[str] | None = None,
    codeowners_path: Path | None = None,
) -> dict[str, Any]:
    if metric not in _COMPARE_METRICS:
        raise ValueError(f"Unsupported compare metric {metric!r}.")
    if max_regression_pct < 0:
        raise ValueError("Maximum regression percentage must be 0 or greater.")

    baseline_report = _load_report_json(baseline_path)
    candidate_report = _load_report_json(candidate_path)
    baseline_cases = _normalize_report_cases(baseline_report, label="baseline")
    candidate_cases = _normalize_report_cases(candidate_report, label="candidate")

    missing_in_candidate = sorted(set(baseline_cases) - set(candidate_cases))
    missing_in_baseline = sorted(set(candidate_cases) - set(baseline_cases))
    if missing_in_candidate or missing_in_baseline:
        problems: list[str] = []
        if missing_in_candidate:
            problems.append(f"missing in candidate: {', '.join(missing_in_candidate)}")
        if missing_in_baseline:
            problems.append(f"missing in baseline: {', '.join(missing_in_baseline)}")
        raise ValueError("Corpus report case mismatch: " + "; ".join(problems))

    normalized_changed_files = _normalize_changed_files(changed_files)
    codeowners_rules = _load_codeowners_rules(codeowners_path) if codeowners_path is not None else []
    comparisons: list[dict[str, Any]] = []
    regressions: list[str] = []
    for case_name in sorted(baseline_cases):
        baseline_case = baseline_cases[case_name]
        candidate_case = candidate_cases[case_name]
        summary_delta = _compute_summary_delta(baseline_case["summary"], candidate_case["summary"])

        if metric == "summary":
            baseline_value = None
            candidate_value = None
            delta = None
            delta_pct = None
            status = "changed" if _summary_has_drift(summary_delta) else "unchanged"
        else:
            baseline_value = _extract_metric_value(baseline_case, metric, label="baseline", case_name=case_name)
            candidate_value = _extract_metric_value(candidate_case, metric, label="candidate", case_name=case_name)
            delta = candidate_value - baseline_value
            delta_pct = _percent_change(baseline_value, candidate_value)

            if candidate_value > baseline_value:
                status = "regression" if delta_pct > max_regression_pct else "within_threshold"
            elif candidate_value < baseline_value:
                status = "improvement"
            else:
                status = "unchanged"

        comparisons.append(
            {
                "name": case_name,
                "path": candidate_case["path"],
                "metric": metric,
                "baseline": baseline_value,
                "candidate": candidate_value,
                "delta": delta,
                "delta_pct": delta_pct,
                "status": status,
                "summary_delta": summary_delta,
                "summary_bits": _format_summary_delta(summary_delta),
            }
        )

        if status == "regression":
            regressions.append(
                f"{case_name}: {metric} regressed by {delta_pct:.1f}% "
                f"({baseline_value:.3f} -> {candidate_value:.3f})"
            )
        if metric == "summary" and fail_on_summary_drift and status == "changed":
            regressions.append(
                f"{case_name}: summary drift detected ({', '.join(_format_summary_delta(summary_delta))})"
            )

    report = {
        "baseline": str(baseline_path),
        "candidate": str(candidate_path),
        "metric": metric,
        "max_regression_pct": max_regression_pct,
        "fail_on_summary_drift": fail_on_summary_drift,
        "has_changed_files_context": changed_files is not None,
        "changed_files": normalized_changed_files,
        "comparisons": comparisons,
    }
    summary_drift_cases = [comparison["name"] for comparison in comparisons if _summary_has_drift(comparison["summary_delta"])]
    report["summary_drift_cases"] = summary_drift_cases
    report["has_summary_drift"] = bool(summary_drift_cases)
    review_hints = _build_compare_review_hints(comparisons, normalized_changed_files)
    report["review_hints"] = review_hints
    report["owner_hints"] = _build_compare_owner_hints(review_hints, codeowners_rules)
    owner_focus = _build_compare_owner_focus(review_hints, codeowners_rules)
    report["owner_focus"] = owner_focus
    reviewer_candidates = _build_compare_reviewer_candidates(owner_focus)
    report["reviewer_candidates"] = reviewer_candidates
    report["review_request_plan"] = _build_compare_review_request_plan(reviewer_candidates)
    if regressions:
        joined = "\n".join(f"- {item}" for item in regressions)
        if metric == "summary":
            raise ValueError(f"Corpus report summary comparison failed:\n{joined}")
        raise ValueError(f"Corpus report comparison failed:\n{joined}")
    return report


def format_compare_report(report: dict[str, Any]) -> str:
    lines = [
        f"Corpus report comparison passed: {report['baseline']} -> {report['candidate']}",
        f"Metric={report['metric']}, Max regression={report['max_regression_pct']:.1f}%",
    ]
    if report.get("has_changed_files_context"):
        lines.append(f"Changed files context={len(report.get('changed_files', []))} file(s)")
    review_hints = report.get("review_hints", [])
    if review_hints:
        lines.append("Review hints:")
        for hint in review_hints:
            touched = ""
            if report.get("has_changed_files_context"):
                overlap = ", ".join(hint["matched_changed_files"]) if hint["matched_changed_files"] else "none"
                touched = f", touched: {overlap}"
            lines.append(
                "  "
                f"{hint['label']}: review {', '.join(hint['paths'])}, cases: {', '.join(hint['cases'])}{touched}"
            )
    owner_hints = report.get("owner_hints", [])
    owner_focus = report.get("owner_focus", [])
    review_request_plan = report.get("review_request_plan")
    if review_request_plan and (
        review_request_plan["users"] or review_request_plan["teams"] or review_request_plan["unsupported"]
    ):
        lines.append("Review-request dry-run: " + _format_compare_review_request_plan_line(review_request_plan))
    if owner_focus:
        lines.append("Owner focus:")
        for item in owner_focus:
            lines.append(
                "  "
                f"{item['priority']} {item['owner']}: review {', '.join(item['labels'])}, "
                f"cases: {', '.join(item['cases'])}, touched: {', '.join(item['matched_changed_files'])}"
            )
    if owner_hints:
        lines.append("Owner hints:")
        for hint in owner_hints:
            line = (
                "  "
                f"{hint['label']}: owners {', '.join(hint['owners'])}, cases: {', '.join(hint['cases'])}, "
                f"touched: {', '.join(hint['matched_changed_files'])}"
            )
            if hint["ownerless_changed_files"]:
                line += f", ownerless: {', '.join(hint['ownerless_changed_files'])}"
            lines.append(line)
    for comparison in report["comparisons"]:
        summary_bits = comparison["summary_bits"]
        summary_suffix = f", summary: {', '.join(summary_bits)}" if summary_bits else ""
        if report["metric"] == "summary":
            lines.append(
                "  "
                f"{comparison['name']}: summary {comparison['status']}{summary_suffix}"
            )
            continue
        lines.append(
            "  "
            f"{comparison['name']}: {comparison['baseline']:.3f} -> {comparison['candidate']:.3f} "
            f"({comparison['delta_pct']:+.1f}%, {comparison['status']}){summary_suffix}"
        )
    return "\n".join(lines)


def format_compare_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Corpus Baseline Compare",
        "",
        f"- Baseline: `{report['baseline']}`",
        f"- Candidate: `{report['candidate']}`",
        f"- Metric: `{report['metric']}`",
    ]
    if report.get("has_changed_files_context"):
        lines.append(f"- Changed files context: `{len(report.get('changed_files', []))} file(s)`")
    review_hints = report.get("review_hints", [])
    if review_hints:
        lines.append("- Review hints:")
        for hint in review_hints:
            paths = ", ".join(f"`{path}`" for path in hint["paths"])
            cases = ", ".join(f"`{case}`" for case in hint["cases"])
            line = f"  - `{hint['label']}`: review {paths}; cases: {cases}"
            if report.get("has_changed_files_context"):
                touched = (
                    ", ".join(f"`{path}`" for path in hint["matched_changed_files"])
                    if hint["matched_changed_files"]
                    else "`none`"
                )
                line += f"; touched changed files: {touched}"
            lines.append(line)
    owner_hints = report.get("owner_hints", [])
    owner_focus = report.get("owner_focus", [])
    review_request_plan = report.get("review_request_plan")
    if review_request_plan and (
        review_request_plan["users"] or review_request_plan["teams"] or review_request_plan["unsupported"]
    ):
        lines.append(f"- Review-request dry-run: {_format_compare_review_request_plan_markdown(review_request_plan)}")
    if owner_focus:
        lines.append("- Owner focus:")
        for item in owner_focus:
            labels = ", ".join(f"`{label}`" for label in item["labels"])
            cases = ", ".join(f"`{case}`" for case in item["cases"])
            touched = ", ".join(f"`{path}`" for path in item["matched_changed_files"])
            lines.append(
                "  "
                f"- `{item['priority']}` `{item['owner']}`: review {labels}; "
                f"cases: {cases}; touched changed files: {touched}"
            )
    if owner_hints:
        lines.append("- Owner hints:")
        for hint in owner_hints:
            owners = ", ".join(f"`{owner}`" for owner in hint["owners"])
            cases = ", ".join(f"`{case}`" for case in hint["cases"])
            touched = ", ".join(f"`{path}`" for path in hint["matched_changed_files"])
            line = f"  - `{hint['label']}`: owners {owners}; cases: {cases}; touched changed files: {touched}"
            if hint["ownerless_changed_files"]:
                ownerless = ", ".join(f"`{path}`" for path in hint["ownerless_changed_files"])
                line += f"; ownerless changed files: {ownerless}"
            lines.append(line)
    if report["metric"] == "summary":
        drift_cases = report.get("summary_drift_cases", [])
        drift_label = f"{len(drift_cases)} case(s)" if drift_cases else "none"
        lines.append(f"- Summary drift: `{drift_label}`")
        lines.extend(
            [
                "",
                "| Case | Status | Summary Delta |",
                "| --- | --- | --- |",
            ]
        )
        for comparison in report["comparisons"]:
            delta_text = ", ".join(comparison["summary_bits"]) or "No summary drift"
            lines.append(f"| `{comparison['name']}` | `{comparison['status']}` | {delta_text} |")
        return "\n".join(lines)

    lines.append(f"- Max regression: `{report['max_regression_pct']:.1f}%`")
    lines.extend(
        [
            "",
            "| Case | Baseline | Candidate | Delta | Status | Summary Delta |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for comparison in report["comparisons"]:
        summary_text = ", ".join(comparison["summary_bits"]) or "No summary drift"
        lines.append(
            "| "
            f"`{comparison['name']}` | "
            f"`{comparison['baseline']:.3f}` | "
            f"`{comparison['candidate']:.3f}` | "
            f"`{comparison['delta_pct']:+.1f}%` | "
            f"`{comparison['status']}` | "
            f"{summary_text} |"
        )
    return "\n".join(lines)


def _load_changed_files(path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"Changed-files input {path} does not exist.")
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _normalize_changed_files(text.splitlines())
    if isinstance(payload, list):
        return _normalize_changed_files(payload)
    if isinstance(payload, dict):
        files = payload.get("files")
        if isinstance(files, list):
            return _normalize_changed_files(files)
    raise ValueError("Changed-files input must be a JSON list, {'files': [...]}, or newline-delimited text.")


def _normalize_changed_files(changed_files: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in changed_files or []:
        if not isinstance(item, str):
            raise ValueError("Changed-files entries must be strings.")
        cleaned = item.strip()
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        if not cleaned or cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
    return normalized


def _build_compare_review_hints(
    comparisons: list[dict[str, Any]],
    changed_files: list[str],
) -> list[dict[str, Any]]:
    categorized_cases: dict[str, list[str]] = {key: [] for key, _label, _paths, _reason in _COMPARE_REVIEW_HINTS}
    for comparison in comparisons:
        matched = False
        summary_delta = comparison["summary_delta"]
        if int(summary_delta.get("files", 0)) != 0:
            categorized_cases["discovery"].append(comparison["name"])
            matched = True
        if any(int(summary_delta.get(field, 0)) != 0 for field in ("functions", "classes")):
            categorized_cases["symbol_extraction"].append(comparison["name"])
            matched = True
        if int(summary_delta.get("edges", 0)) != 0:
            categorized_cases["call_resolution"].append(comparison["name"])
            matched = True
        if int(summary_delta.get("chains", 0)) != 0:
            categorized_cases["chain_enumeration"].append(comparison["name"])
            matched = True
        if int(summary_delta.get("parse_errors", 0)) != 0:
            categorized_cases["parse_health"].append(comparison["name"])
            matched = True
        if _comparison_is_highlight(comparison) and not matched:
            categorized_cases["non_structural"].append(comparison["name"])
    hints: list[dict[str, Any]] = []
    for key, label, paths, reason in _COMPARE_REVIEW_HINTS:
        cases = categorized_cases[key]
        if not cases:
            continue
        matched_changed_files = [path for path in changed_files if _matches_review_hint(path, paths)]
        hints.append(
            {
                "key": key,
                "label": label,
                "cases": cases,
                "paths": list(paths),
                "reason": reason,
                "matched_changed_files": matched_changed_files,
            }
        )
    return hints


def _build_compare_owner_hints(
    review_hints: list[dict[str, Any]],
    codeowners_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not review_hints or not codeowners_rules:
        return []
    hints: list[dict[str, Any]] = []
    for hint in review_hints:
        owners: list[str] = []
        ownerless_changed_files: list[str] = []
        seen_owners: set[str] = set()
        for path in hint["matched_changed_files"]:
            matched_owners = _match_codeowners(path, codeowners_rules)
            if not matched_owners:
                ownerless_changed_files.append(path)
                continue
            for owner in matched_owners:
                if owner in seen_owners:
                    continue
                owners.append(owner)
                seen_owners.add(owner)
        if not owners:
            continue
        hints.append(
            {
                "key": hint["key"],
                "label": hint["label"],
                "cases": list(hint["cases"]),
                "paths": list(hint["paths"]),
                "owners": owners,
                "matched_changed_files": list(hint["matched_changed_files"]),
                "ownerless_changed_files": ownerless_changed_files,
            }
        )
    return hints


def _build_compare_owner_focus(
    review_hints: list[dict[str, Any]],
    codeowners_rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not review_hints or not codeowners_rules:
        return []
    aggregated: dict[str, dict[str, Any]] = {}
    for hint in review_hints:
        for path in hint["matched_changed_files"]:
            for owner in _match_codeowners(path, codeowners_rules):
                current = aggregated.setdefault(
                    owner,
                    {
                        "owner": owner,
                        "labels": [],
                        "cases": [],
                        "matched_changed_files": [],
                    },
                )
                _append_unique(current["labels"], hint["label"])
                for case in hint["cases"]:
                    _append_unique(current["cases"], case)
                _append_unique(current["matched_changed_files"], path)
    ordered = sorted(aggregated.values(), key=_compare_owner_focus_sort_key)
    return [
        {
            "owner": item["owner"],
            "labels": list(item["labels"]),
            "cases": list(item["cases"]),
            "matched_changed_files": list(item["matched_changed_files"]),
            "priority": _compare_owner_focus_priority(item["matched_changed_files"]),
            "score": _compare_owner_focus_score(
                item["matched_changed_files"],
                item["labels"],
                item["cases"],
            ),
        }
        for item in ordered
    ]


def _build_compare_reviewer_candidates(owner_focus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "owner": item["owner"],
            "kind": _classify_review_owner(item["owner"]),
            "priority": item["priority"],
            "score": item["score"],
            "labels": list(item["labels"]),
            "cases": list(item["cases"]),
            "matched_changed_files": list(item["matched_changed_files"]),
        }
        for item in owner_focus
    ]


def _build_compare_review_request_plan(reviewer_candidates: list[dict[str, Any]]) -> dict[str, list[str]]:
    plan: dict[str, list[str]] = {"users": [], "teams": [], "unsupported": []}
    for candidate in reviewer_candidates:
        bucket = "unsupported"
        if candidate["kind"] == "user":
            bucket = "users"
        elif candidate["kind"] == "team":
            bucket = "teams"
        _append_unique(plan[bucket], candidate["owner"])
    return plan


def _comparison_is_highlight(comparison: dict[str, Any]) -> bool:
    if comparison["summary_bits"]:
        return True
    status = comparison.get("status")
    return isinstance(status, str) and status != "unchanged"


def _matches_review_hint(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _load_codeowners_rules(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ValueError(f"CODEOWNERS file {path} does not exist.")
    rules: list[dict[str, Any]] = []
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"CODEOWNERS line {lineno} must include a pattern and at least one owner.")
        pattern = parts[0].strip()
        owners = [owner.strip() for owner in parts[1:] if owner.strip()]
        rules.append({"pattern": pattern, "owners": owners, "line": lineno})
    return rules


def _match_codeowners(path: str, rules: list[dict[str, Any]]) -> list[str]:
    matched: list[str] = []
    for rule in rules:
        if _codeowners_pattern_matches(path, rule["pattern"]):
            matched = list(rule["owners"])
    return matched


def _codeowners_pattern_matches(path: str, pattern: str) -> bool:
    normalized_path = path.strip()
    while normalized_path.startswith("./"):
        normalized_path = normalized_path[2:]
    normalized_pattern = pattern.strip()
    anchored = normalized_pattern.startswith("/")
    normalized_pattern = normalized_pattern.lstrip("/")
    if not normalized_pattern:
        return False
    if normalized_pattern.endswith("/"):
        prefix = normalized_pattern.rstrip("/")
        return normalized_path == prefix or normalized_path.startswith(prefix + "/")
    if "/" not in normalized_pattern:
        basename = Path(normalized_path).name
        if fnmatch.fnmatch(basename, normalized_pattern):
            return True
        if any(segment == normalized_pattern for segment in normalized_path.split("/")):
            return True
    if fnmatch.fnmatch(normalized_path, normalized_pattern):
        return True
    if not anchored and "/" in normalized_pattern:
        return normalized_path == normalized_pattern or normalized_path.endswith("/" + normalized_pattern)
    return False


def _classify_review_owner(owner: str) -> str:
    if owner.startswith("@"):
        return "team" if "/" in owner[1:] else "user"
    return "unsupported"


def _compare_owner_focus_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    files = item["matched_changed_files"]
    labels = item["labels"]
    cases = item["cases"]
    score = _compare_owner_focus_score(files, labels, cases)
    max_weight = max((_changed_file_weight(path) for path in files), default=0)
    return (-max_weight, -score, -len(labels), -len(files), item["owner"])


def _compare_owner_focus_score(matched_changed_files: list[str], labels: list[str], cases: list[str]) -> int:
    path_score = sum(_changed_file_weight(path) for path in matched_changed_files)
    return path_score + len(matched_changed_files) + (len(labels) * 2) + (len(cases) * 2)


def _compare_owner_focus_priority(matched_changed_files: list[str]) -> str:
    max_weight = max((_changed_file_weight(path) for path in matched_changed_files), default=0)
    if max_weight >= 8:
        return "critical"
    if max_weight >= 6:
        return "high"
    if max_weight >= 4:
        return "medium"
    return "low"


def _changed_file_weight(path: str) -> int:
    if path == "src/callchain/core/callgraph.py":
        return 9
    if path == "src/callchain/core/chain_enum.py":
        return 8
    if path.startswith("src/callchain/core/"):
        return 7
    if path == "src/callchain/languages/base.py":
        return 6
    if path.startswith("src/callchain/languages/"):
        return 5
    if path.startswith("src/callchain/devtools/"):
        return 3
    if path.startswith(".github/"):
        return 2
    return 1


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _format_compare_review_request_plan_line(plan: dict[str, list[str]]) -> str:
    parts: list[str] = []
    if plan["users"]:
        parts.append("users " + ", ".join(plan["users"]))
    if plan["teams"]:
        parts.append("teams " + ", ".join(plan["teams"]))
    if plan["unsupported"]:
        parts.append("unsupported " + ", ".join(plan["unsupported"]))
    return "; ".join(parts) if parts else "none"


def _format_compare_review_request_plan_markdown(plan: dict[str, list[str]]) -> str:
    parts: list[str] = []
    if plan["users"]:
        parts.append("users " + ", ".join(f"`{owner}`" for owner in plan["users"]))
    if plan["teams"]:
        parts.append("teams " + ", ".join(f"`{owner}`" for owner in plan["teams"]))
    if plan["unsupported"]:
        parts.append("unsupported " + ", ".join(f"`{owner}`" for owner in plan["unsupported"]))
    return "; ".join(parts) if parts else "`none`"


def _parse_entry(data: dict[str, Any]) -> CorpusEntry:
    path = data.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("Corpus project entries must include a non-empty string 'path'.")

    name = data.get("name", path)
    if not isinstance(name, str) or not name:
        raise ValueError("Corpus project entries must use a non-empty string 'name'.")

    languages = _parse_languages(data.get("languages"))
    exclude = _parse_string_list(data.get("exclude"), field_name="exclude")

    return CorpusEntry(
        name=name,
        path=path,
        languages=languages,
        exclude=exclude,
        restrict_dir=_parse_optional_string(data.get("restrict_dir"), field_name="restrict_dir"),
        max_depth=int(data.get("max_depth", 20)),
        max_chains=int(data.get("max_chains", 50_000)),
        only_cross_file=bool(data.get("only_cross_file", False)),
        cache=bool(data.get("cache", False)),
        min_files=int(data.get("min_files", 0)),
        min_functions=int(data.get("min_functions", 0)),
        min_classes=int(data.get("min_classes", 0)),
        min_edges=int(data.get("min_edges", 0)),
        min_chains=int(data.get("min_chains", 0)),
        max_parse_errors=int(data.get("max_parse_errors", 0)),
    )


def _parse_source_entry(data: dict[str, Any]) -> CorpusSource:
    name = _require_non_empty_string(data.get("name"), field_name="name", prefix="Corpus source entries")
    kind = _require_non_empty_string(data.get("kind"), field_name="kind", prefix="Corpus source entries")
    if kind not in _SOURCE_KINDS:
        raise ValueError(f"Corpus source entries must use a supported kind; got {kind!r}.")

    analyzed_path = _require_non_empty_string(
        data.get("analyzed_path"),
        field_name="analyzed_path",
        prefix="Corpus source entries",
    )
    root_path = _require_non_empty_string(
        data.get("root_path", analyzed_path),
        field_name="root_path",
        prefix="Corpus source entries",
    )
    license_spdx = _require_non_empty_string(
        data.get("license_spdx"),
        field_name="license_spdx",
        prefix="Corpus source entries",
    )
    license_file = _require_non_empty_string(
        data.get("license_file"),
        field_name="license_file",
        prefix="Corpus source entries",
    )
    upstream_url = _parse_optional_string(data.get("upstream_url"), field_name="upstream_url")
    version = _parse_optional_string(data.get("version"), field_name="version")
    source_ref = _parse_optional_string(data.get("source_ref"), field_name="source_ref")
    archive_url = _parse_optional_string(data.get("archive_url"), field_name="archive_url")
    archive_sha256 = _parse_optional_string(data.get("archive_sha256"), field_name="archive_sha256")
    content_sha256 = _parse_optional_string(data.get("content_sha256"), field_name="content_sha256")
    notes = data.get("notes", "")
    if not isinstance(notes, str):
        raise ValueError("Corpus source entries 'notes' must be a string.")

    return CorpusSource(
        name=name,
        kind=kind,
        analyzed_path=analyzed_path,
        root_path=root_path,
        license_spdx=license_spdx,
        license_file=license_file,
        upstream_url=upstream_url,
        version=version,
        source_ref=source_ref,
        archive_url=archive_url,
        archive_sha256=archive_sha256,
        content_sha256=content_sha256,
        notes=notes,
    )


def _parse_languages(value: Any) -> tuple[Language, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("Corpus project 'languages' must be a list of strings.")

    languages: list[Language] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Corpus project 'languages' entries must be strings.")
        language = _LANGUAGE_ALIASES.get(item.lower())
        if language is None:
            raise ValueError(f"Unsupported corpus language {item!r}.")
        if language not in languages:
            languages.append(language)
    return tuple(languages)


def _parse_string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Corpus project '{field_name}' must be a list of strings.")
    return tuple(value)


def _parse_optional_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"Corpus project '{field_name}' must be a non-empty string when provided.")
    return value


def _require_non_empty_string(value: Any, *, field_name: str, prefix: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{prefix} must include a non-empty string '{field_name}'.")
    return value


def _validate_run(entry: CorpusEntry, run: CorpusRun) -> list[str]:
    failures: list[str] = []
    checks = [
        ("files", run.files, entry.min_files, ">="),
        ("functions", run.functions, entry.min_functions, ">="),
        ("classes", run.classes, entry.min_classes, ">="),
        ("edges", run.edges, entry.min_edges, ">="),
        ("chains", run.chains, entry.min_chains, ">="),
        ("parse_errors", run.parse_errors, entry.max_parse_errors, "<="),
    ]
    for label, actual, expected, operator in checks:
        failed = actual < expected if operator == ">=" else actual > expected
        if failed:
            failures.append(
                f"{entry.name}: expected {label} {operator} {expected}, got {actual} "
                f"for {entry.path}"
            )
    return failures


def _load_report_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Corpus report file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corpus report file is not valid JSON: {path}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Corpus report root must be a JSON object: {path}")
    return data


def _dedupe_sources(sources: list[CorpusSource]) -> dict[str, CorpusSource]:
    source_by_name: dict[str, CorpusSource] = {}
    for source in sources:
        if source.name in source_by_name:
            raise ValueError(f"Corpus source registry contains duplicate source name {source.name!r}.")
        source_by_name[source.name] = source
    return source_by_name


def _validate_registry_alignment(
    manifest_by_name: dict[str, CorpusEntry],
    source_by_name: dict[str, CorpusSource],
) -> None:
    missing_sources = sorted(set(manifest_by_name) - set(source_by_name))
    unreferenced_sources = sorted(set(source_by_name) - set(manifest_by_name))
    if missing_sources or unreferenced_sources:
        problems: list[str] = []
        if missing_sources:
            problems.append(f"missing source metadata for: {', '.join(missing_sources)}")
        if unreferenced_sources:
            problems.append(f"unreferenced source metadata for: {', '.join(unreferenced_sources)}")
        raise ValueError("Corpus source registry mismatch: " + "; ".join(problems))


def _validate_source_entry(source: CorpusSource, entry: CorpusEntry, manifest_root: Path) -> dict[str, Any]:
    if source.analyzed_path != entry.path:
        raise ValueError(
            f"Corpus source {source.name!r} analyzed_path {source.analyzed_path!r} does not match "
            f"manifest path {entry.path!r}."
        )

    analyzed_path = (manifest_root / source.analyzed_path).resolve()
    root_path = (manifest_root / source.root_path).resolve()
    license_path = (manifest_root / source.license_file).resolve()

    if not analyzed_path.exists():
        raise ValueError(f"Corpus source analyzed path does not exist for {source.name!r}: {source.analyzed_path}")
    if not root_path.exists():
        raise ValueError(f"Corpus source root path does not exist for {source.name!r}: {source.root_path}")
    if not license_path.exists():
        raise ValueError(f"Corpus source license file does not exist for {source.name!r}: {source.license_file}")
    if analyzed_path != root_path and not analyzed_path.is_relative_to(root_path):
        raise ValueError(f"Corpus source analyzed path for {source.name!r} must live within its root_path.")

    pyproject_version, pyproject_license = _read_source_pyproject(root_path)
    computed_sha256 = _compute_tree_sha256(root_path)
    detected_source_ref = _detect_git_source_ref(root_path)
    rendered_archive_url = _render_archive_url(source)
    if not source.content_sha256:
        raise ValueError(f"Corpus source {source.name!r} must define a content_sha256.")
    if source.content_sha256 != computed_sha256:
        raise ValueError(
            f"Corpus source {source.name!r} content_sha256 {source.content_sha256!r} does not match "
            f"computed checksum {computed_sha256!r}."
        )
    if source.kind == "vendored":
        if not source.upstream_url or not source.upstream_url.startswith(("https://", "http://")):
            raise ValueError(f"Vendored corpus source {source.name!r} must define an upstream_url.")
        if not source.version:
            raise ValueError(f"Vendored corpus source {source.name!r} must define a version.")
        if not source.source_ref:
            raise ValueError(f"Vendored corpus source {source.name!r} must define a source_ref.")
        if not source.archive_url:
            raise ValueError(f"Vendored corpus source {source.name!r} must define an archive_url.")
        if not source.archive_sha256:
            raise ValueError(f"Vendored corpus source {source.name!r} must define an archive_sha256.")
        if pyproject_version and pyproject_version != source.version:
            raise ValueError(
                f"Vendored corpus source {source.name!r} version {source.version!r} does not match "
                f"local pyproject version {pyproject_version!r}."
            )
        if pyproject_license and pyproject_license != source.license_spdx:
            raise ValueError(
                f"Vendored corpus source {source.name!r} license {source.license_spdx!r} does not match "
                f"local pyproject license {pyproject_license!r}."
            )
    if source.source_ref and detected_source_ref and source.source_ref != detected_source_ref:
        raise ValueError(
            f"Corpus source {source.name!r} source_ref {source.source_ref!r} does not match "
            f"detected git ref {detected_source_ref!r}."
        )

    payload = source.to_dict()
    payload["pyproject_version"] = pyproject_version
    payload["pyproject_license"] = pyproject_license
    payload["computed_sha256"] = computed_sha256
    payload["detected_source_ref"] = detected_source_ref
    payload["rendered_archive_url"] = rendered_archive_url
    return payload


def _sync_source_entry(source: CorpusSource, entry: CorpusEntry, manifest_root: Path) -> CorpusSource:
    if source.analyzed_path != entry.path:
        raise ValueError(
            f"Corpus source {source.name!r} analyzed_path {source.analyzed_path!r} does not match "
            f"manifest path {entry.path!r}."
        )

    analyzed_path = (manifest_root / source.analyzed_path).resolve()
    root_path = (manifest_root / source.root_path).resolve()
    license_path = (manifest_root / source.license_file).resolve()

    if not analyzed_path.exists():
        raise ValueError(f"Corpus source analyzed path does not exist for {source.name!r}: {source.analyzed_path}")
    if not root_path.exists():
        raise ValueError(f"Corpus source root path does not exist for {source.name!r}: {source.root_path}")
    if not license_path.exists():
        raise ValueError(f"Corpus source license file does not exist for {source.name!r}: {source.license_file}")
    if analyzed_path != root_path and not analyzed_path.is_relative_to(root_path):
        raise ValueError(f"Corpus source analyzed path for {source.name!r} must live within its root_path.")

    pyproject_version, pyproject_license = _read_source_pyproject(root_path)
    detected_source_ref = _detect_git_source_ref(root_path)
    computed_sha256 = _compute_tree_sha256(root_path)

    updated_version = pyproject_version or source.version
    updated_license = pyproject_license or source.license_spdx
    updated_source_ref = detected_source_ref or source.source_ref

    updated = CorpusSource(
        name=source.name,
        kind=source.kind,
        analyzed_path=source.analyzed_path,
        root_path=source.root_path,
        license_spdx=updated_license,
        license_file=source.license_file,
        upstream_url=source.upstream_url,
        version=updated_version,
        source_ref=updated_source_ref,
        archive_url=source.archive_url,
        archive_sha256=source.archive_sha256,
        content_sha256=computed_sha256,
        notes=source.notes,
    )
    if updated.kind == "vendored":
        if not updated.upstream_url or not updated.upstream_url.startswith(("https://", "http://")):
            raise ValueError(f"Vendored corpus source {updated.name!r} must define an upstream_url.")
        if not updated.version:
            raise ValueError(f"Vendored corpus source {updated.name!r} must define a version.")
        if not updated.source_ref:
            raise ValueError(
                f"Vendored corpus source {updated.name!r} must define a source_ref or expose local git metadata."
            )
        if not updated.archive_url:
            raise ValueError(f"Vendored corpus source {updated.name!r} must define an archive_url.")
    return updated


def _read_source_pyproject(root_path: Path) -> tuple[str | None, str | None]:
    pyproject_path = root_path / "pyproject.toml"
    if not pyproject_path.exists():
        return None, None

    toml = _import_toml_module()
    data = toml.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict):
        return None, None
    version = project.get("version")
    raw_license = project.get("license")
    license_value = raw_license if isinstance(raw_license, str) else None
    return version if isinstance(version, str) else None, license_value


def _compute_tree_sha256(root_path: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(_iter_source_files(root_path)):
        rel = path.relative_to(root_path).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _compute_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _download_archive_bytes(location: str, *, timeout: float = 30.0) -> bytes:
    try:
        with _open_archive_handle(location, timeout=timeout) as handle:
            payload = handle.read()
    except (OSError, URLError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Could not open archive location {location!r}: {exc}") from exc
    if not isinstance(payload, bytes):
        raise ValueError(f"Archive location {location!r} did not produce binary content.")
    return payload


def _download_archive_sha256(location: str, *, timeout: float = 30.0) -> tuple[str, int]:
    archive_bytes = _download_archive_bytes(location, timeout=timeout)
    return hashlib.sha256(archive_bytes).hexdigest(), len(archive_bytes)


def _open_archive_handle(location: str, *, timeout: float = 30.0):
    parsed = urlparse(location)
    if parsed.scheme in {"http", "https", "file"}:
        return urlopen(location, timeout=timeout)
    if not parsed.scheme:
        return Path(location).expanduser().open("rb")
    raise ValueError(f"Unsupported archive URL scheme {parsed.scheme!r}.")


def _extract_archive_bytes(archive_bytes: bytes, destination_root: Path) -> Path:
    destination_root.mkdir(parents=True, exist_ok=True)

    buffer = io.BytesIO(archive_bytes)
    try:
        with tarfile.open(fileobj=buffer, mode="r:*") as archive:
            _safe_extract_tar(archive, destination_root)
        return _normalize_extracted_root(destination_root)
    except tarfile.ReadError:
        buffer = io.BytesIO(archive_bytes)
        try:
            with zipfile.ZipFile(buffer) as archive:
                _safe_extract_zip(archive, destination_root)
            return _normalize_extracted_root(destination_root)
        except zipfile.BadZipFile as exc:
            raise ValueError("Unsupported archive format; expected tar or zip archive.") from exc


def _safe_extract_tar(archive: tarfile.TarFile, destination_root: Path) -> None:
    root_resolved = destination_root.resolve()
    for member in archive.getmembers():
        _ensure_archive_member_within_root(root_resolved, member.name)
    extract_kwargs: dict[str, Any] = {}
    if sys.version_info >= (3, 12):
        extract_kwargs["filter"] = "data"
    archive.extractall(destination_root, **extract_kwargs)


def _safe_extract_zip(archive: zipfile.ZipFile, destination_root: Path) -> None:
    root_resolved = destination_root.resolve()
    for member_name in archive.namelist():
        _ensure_archive_member_within_root(root_resolved, member_name)
    archive.extractall(destination_root)


def _ensure_archive_member_within_root(root_resolved: Path, member_name: str) -> None:
    target = (root_resolved / member_name).resolve()
    if not target.is_relative_to(root_resolved):
        raise ValueError(f"Archive member {member_name!r} would extract outside {root_resolved}.")


def _normalize_extracted_root(destination_root: Path) -> Path:
    entries = [path for path in destination_root.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return destination_root


def _replace_tree(destination_root: Path, source_root: Path) -> None:
    destination_root.mkdir(parents=True, exist_ok=True)
    for existing in destination_root.iterdir():
        if existing.is_dir() and not existing.is_symlink():
            shutil.rmtree(existing)
        else:
            existing.unlink()

    for source_path in source_root.iterdir():
        target_path = destination_root / source_path.name
        if source_path.is_dir():
            shutil.copytree(source_path, target_path, symlinks=True)
        else:
            shutil.copy2(source_path, target_path, follow_symlinks=False)


def _iter_source_files(root_path: Path) -> list[Path]:
    return [
        path
        for path in root_path.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ]


def _detect_git_source_ref(root_path: Path) -> str | None:
    git_dir = _resolve_git_dir(root_path)
    if git_dir is None:
        return None

    head_path = git_dir / "HEAD"
    if not head_path.exists():
        return None
    head = head_path.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref_name = head[5:]
        ref_path = git_dir / ref_name
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
        packed_refs = git_dir / "packed-refs"
        if packed_refs.exists():
            for line in packed_refs.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#") or line.startswith("^"):
                    continue
                value, _, name = line.partition(" ")
                if name == ref_name:
                    return value
        return None
    return head or None


def _ensure_git_clean(root_path: Path, source_name: str) -> None:
    status = _run_git(root_path, "status", "--porcelain")
    if status:
        raise ValueError(f"Vendored corpus source {source_name!r} has local changes; commit or stash them before refresh.")


def _resolve_git_ref(root_path: Path, ref: str) -> str:
    return _run_git(root_path, "rev-parse", "--verify", f"{ref}^{{commit}}")


def _run_git(root_path: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root_path), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("git is required for vendored corpus source maintenance.") from exc

    if proc.returncode != 0:
        details = proc.stderr.strip() or proc.stdout.strip() or f"git exited with status {proc.returncode}"
        raise ValueError(f"git {' '.join(args)} failed for {root_path}: {details}")
    return proc.stdout.strip()


def _resolve_git_dir(root_path: Path) -> Path | None:
    git_marker = root_path / ".git"
    if git_marker.is_dir():
        return git_marker
    if git_marker.is_file():
        content = git_marker.read_text(encoding="utf-8").strip()
        if content.startswith("gitdir: "):
            return (root_path / content[8:]).resolve()
    return None


def _diff_source_fields(before: CorpusSource, after: CorpusSource) -> list[str]:
    changed: list[str] = []
    for field_name in (
        "license_spdx",
        "version",
        "source_ref",
        "archive_url",
        "archive_sha256",
        "content_sha256",
        "upstream_url",
        "license_file",
        "root_path",
        "analyzed_path",
        "notes",
    ):
        if getattr(before, field_name) != getattr(after, field_name):
            changed.append(field_name)
    return changed


def _write_source_registry(path: Path, sources: list[CorpusSource]) -> None:
    lines = [f"version = {MANIFEST_VERSION}", ""]
    for source in sources:
        lines.append("[[sources]]")
        lines.append(f'name = {_toml_quote(source.name)}')
        lines.append(f'kind = {_toml_quote(source.kind)}')
        lines.append(f'analyzed_path = {_toml_quote(source.analyzed_path)}')
        lines.append(f'root_path = {_toml_quote(source.root_path)}')
        lines.append(f'license_spdx = {_toml_quote(source.license_spdx)}')
        lines.append(f'license_file = {_toml_quote(source.license_file)}')
        if source.upstream_url is not None:
            lines.append(f'upstream_url = {_toml_quote(source.upstream_url)}')
        if source.version is not None:
            lines.append(f'version = {_toml_quote(source.version)}')
        if source.source_ref is not None:
            lines.append(f'source_ref = {_toml_quote(source.source_ref)}')
        if source.archive_url is not None:
            lines.append(f'archive_url = {_toml_quote(source.archive_url)}')
        if source.archive_sha256 is not None:
            lines.append(f'archive_sha256 = {_toml_quote(source.archive_sha256)}')
        if source.content_sha256 is not None:
            lines.append(f'content_sha256 = {_toml_quote(source.content_sha256)}')
        if source.notes:
            lines.append(f'notes = {_toml_quote(source.notes)}')
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _toml_quote(value: str) -> str:
    return json.dumps(value)


def _render_archive_url(source: CorpusSource) -> str | None:
    if source.archive_url is None:
        return None
    if "{ref}" not in source.archive_url:
        return source.archive_url
    if not source.source_ref:
        raise ValueError(f"Vendored corpus source {source.name!r} must define a source_ref to render archive_url.")
    return source.archive_url.replace("{ref}", source.source_ref)


def _render_archive_url_for_ref(source: CorpusSource, ref: str) -> str | None:
    if source.archive_url is None:
        return None
    if "{ref}" not in source.archive_url:
        return source.archive_url
    return source.archive_url.replace("{ref}", ref)


def _normalize_report_cases(report: dict[str, Any], *, label: str) -> dict[str, dict[str, Any]]:
    raw_cases = report.get("cases")
    if raw_cases is None:
        raw_cases = report.get("projects")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError(f"{label} report must contain a non-empty 'cases' or 'projects' list.")

    normalized: dict[str, dict[str, Any]] = {}
    for item in raw_cases:
        if not isinstance(item, dict):
            raise ValueError(f"{label} report entries must be objects.")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{label} report entries must contain a non-empty 'name'.")
        if name in normalized:
            raise ValueError(f"{label} report contains duplicate case name {name!r}.")
        summary = item.get("summary")
        timings = item.get("timings")
        path = item.get("path")
        if not isinstance(summary, dict):
            raise ValueError(f"{label} report case {name!r} is missing a summary object.")
        if not isinstance(timings, dict):
            raise ValueError(f"{label} report case {name!r} is missing a timings object.")
        if not isinstance(path, str) or not path:
            raise ValueError(f"{label} report case {name!r} is missing a non-empty path.")
        normalized[name] = item
    return normalized


def _extract_metric_value(case: dict[str, Any], metric: str, *, label: str, case_name: str) -> float:
    timings = case["timings"]
    raw_value = timings.get(metric)
    if isinstance(raw_value, dict):
        median_value = raw_value.get("median")
        if not isinstance(median_value, (int, float)):
            raise ValueError(f"{label} report case {case_name!r} metric {metric!r} is missing median timing.")
        return float(median_value)
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    raise ValueError(f"{label} report case {case_name!r} is missing numeric metric {metric!r}.")


def _compute_summary_delta(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, int]:
    fields = ("files", "functions", "classes", "edges", "chains", "parse_errors")
    delta: dict[str, int] = {}
    for field_name in fields:
        baseline_value = baseline.get(field_name, 0)
        candidate_value = candidate.get(field_name, 0)
        if not isinstance(baseline_value, int) or not isinstance(candidate_value, int):
            raise ValueError(f"Corpus report summary field {field_name!r} must be an integer.")
        delta[field_name] = candidate_value - baseline_value
    return delta


def _summary_has_drift(summary_delta: dict[str, int]) -> bool:
    return any(delta != 0 for delta in summary_delta.values())


def _format_summary_delta(summary_delta: dict[str, int]) -> list[str]:
    return [
        f"{name} {delta:+d}"
        for name, delta in summary_delta.items()
        if delta != 0
    ]


def _percent_change(baseline: float, candidate: float) -> float:
    if baseline == 0:
        if candidate == 0:
            return 0.0
        return float("inf")
    return ((candidate - baseline) / baseline) * 100.0


def _summarize_timings(runs: list[CorpusRun]) -> dict[str, dict[str, float]]:
    return {
        field_name: _summarize_numbers([getattr(run, field_name) for run in runs])
        for field_name in ("build_seconds", "chain_seconds", "analysis_seconds", "total_seconds")
    }


def _summarize_numbers(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "median": median(values),
        "mean": mean(values),
        "max": max(values),
    }


def _write_output(rendered: str, output: str | None) -> None:
    if output is None:
        print(rendered)
        return
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered + ("\n" if not rendered.endswith("\n") else ""), encoding="utf-8")


def _import_toml_module() -> Any:
    module_name = "tomllib" if sys.version_info >= (3, 11) else "tomli"
    return importlib.import_module(module_name)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
