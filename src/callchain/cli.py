"""Unified CLI entry point for CallChain."""

from __future__ import annotations

from collections.abc import Callable
import sys
from typing import Any
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from callchain import __version__
from callchain.core.analyzer import Analyzer
from callchain.core.callgraph import CallGraphBuilder
from callchain.core.chain_enum import ChainEnumerator
from callchain.core.config import load_config, load_config_file
from callchain.core.models import AnalysisResult, Language
from callchain.output.json_output import write_chains_jsonl, write_json
from callchain.output.dot_output import write_dot
from callchain.output.mermaid_output import write_mermaid_callgraph
from callchain.output.html_output import write_html

# Ensure all language plugins are registered on import
import callchain.languages.python_lang  # noqa: F401
import callchain.languages.javascript_lang  # noqa: F401
import callchain.languages.java_lang  # noqa: F401
import callchain.languages.go_lang  # noqa: F401
import callchain.languages.rust_lang  # noqa: F401
import callchain.languages.c_lang  # noqa: F401
import callchain.languages.cpp_lang  # noqa: F401

console = Console()


LANG_MAP = {
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


@click.group()
@click.version_option(version=__version__, prog_name="callchain")
def main() -> None:
    """CallChain — Multi-language call chain analysis tool."""


def _load_user_config(project: Path, config_path: str | None) -> dict[str, Any]:
    if config_path is None:
        return load_config(project)

    config_file = Path(config_path)
    if not config_file.exists():
        console.print(f"[red]Error: config file '{config_path}' not found[/red]")
        sys.exit(1)

    try:
        return load_config_file(config_file, strict=True)
    except Exception as exc:
        console.print(f"[red]Error loading config '{config_path}': {exc}[/red]")
        sys.exit(1)


def _parse_languages(lang_values: tuple[str, ...]) -> list[Language] | None:
    if not lang_values:
        return None

    languages: list[Language] = []
    for lang_value in lang_values:
        mapped = LANG_MAP.get(lang_value.lower())
        if not mapped:
            console.print(f"[red]Unknown language: {lang_value}[/red]")
            console.print(f"Supported: {', '.join(sorted(LANG_MAP.keys()))}")
            sys.exit(1)
        if mapped not in languages:
            languages.append(mapped)
    return languages


def _watch_extensions(languages: list[Language] | None) -> set[str]:
    if languages:
        watched_exts: set[str] = set()
        for language in languages:
            for ext, ext_language in _EXT_MAP_REVERSE.items():
                if ext_language == language:
                    watched_exts.add(ext)
        return watched_exts
    return set(_EXT_MAP_REVERSE.keys())


def _run_watch_analysis(
    project: Path,
    languages: list[Language] | None,
    restrict_dir: str | None,
    exclude: tuple[str, ...],
) -> AnalysisResult:
    console.clear()
    console.print(f"[bold]Re-analyzing[/bold] {project}")
    builder = CallGraphBuilder(project, use_cache=True, exclude=list(exclude))
    result = builder.build(languages=languages, restrict_dir=restrict_dir)
    console.print(f"  Files: {result.total_files}, Functions: {result.total_functions}, Edges: {len(result.edges)}")

    enumerator = ChainEnumerator(edges=result.edges, restrict_dir=restrict_dir)
    result.chains = enumerator.enumerate()
    Analyzer(result).run_all()
    _print_summary(result)
    console.print(f"\n[dim]Watching {project} for changes... (Ctrl+C to stop)[/dim]")
    return result


def _import_watchdog_components() -> tuple[Any, Any]:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    return FileSystemEventHandler, Observer


def _make_timer(delay: float, callback: Callable[[], None]) -> Any:
    import threading

    return threading.Timer(delay, callback)


def _wait_forever(sleep_fn: Callable[[float], None] | None = None) -> None:
    import time

    sleeper = sleep_fn or time.sleep
    while True:
        sleeper(1)


def _watch_project(
    project: Path,
    languages: list[Language] | None,
    restrict_dir: str | None,
    exclude: tuple[str, ...],
    debounce: float,
    event_handler_base: type[Any],
    observer_factory: Callable[[], Any],
    *,
    timer_factory: Callable[[float, Callable[[], None]], Any] | None = None,
    wait_forever: Callable[[], None] | None = None,
) -> None:
    watched_exts = _watch_extensions(languages)
    timer_factory = timer_factory or _make_timer
    wait_forever = wait_forever or _wait_forever

    def run_analysis() -> None:
        _run_watch_analysis(project, languages, restrict_dir, exclude)

    timer: list[Any | None] = [None]

    def schedule_rerun() -> None:
        if timer[0] is not None:
            timer[0].cancel()
        next_timer = timer_factory(debounce, run_analysis)
        timer[0] = next_timer
        next_timer.start()

    class Handler(event_handler_base):
        def on_any_event(self, event: Any) -> None:
            if event.is_directory:
                return
            src = str(event.src_path)
            if any(src.endswith(ext) for ext in watched_exts):
                schedule_rerun()

    run_analysis()

    observer = observer_factory()
    observer.schedule(Handler(), str(project), recursive=True)
    observer.start()
    try:
        wait_forever()
    except KeyboardInterrupt:
        observer.stop()
        console.print("\n[yellow]Stopped watching.[/yellow]")
    observer.join()


@main.command()
@click.argument("project_path", type=click.Path(exists=True))
@click.option("--lang", "-l", multiple=True, help="Languages to analyze (auto-detect if omitted). E.g. python, js, java, go, rust")
@click.option("--restrict-dir", "-d", default=None, help="Restrict analysis to a subdirectory")
@click.option("--exclude", "-e", multiple=True, help="Exclude files/directories matching pattern (can be used multiple times). Supports globs.")
@click.option("--max-depth", default=20, show_default=True, help="Max depth for call chain enumeration")
@click.option("--max-chains", default=50000, show_default=True, help="Max number of chains to enumerate")
@click.option("--only-cross-file", is_flag=True, help="Only emit chains with cross-file transitions")
@click.option("--output", "-o", default=None, help="Output file path (format inferred from extension: .json, .jsonl, .dot, .md, .html)")
@click.option("--format", "fmt", type=click.Choice(["json", "jsonl", "dot", "mermaid", "html", "summary"]), default="summary", show_default=True, help="Output format")
@click.option("--cache/--no-cache", default=False, help="Enable incremental analysis cache")
@click.option("--config", "config_path", default=None, help="Path to config file (default: .callchain.toml in project root)")
@click.pass_context
def analyze(
    ctx: click.Context,
    project_path: str,
    lang: tuple[str, ...],
    restrict_dir: str | None,
    exclude: tuple[str, ...],
    max_depth: int,
    max_chains: int,
    only_cross_file: bool,
    output: str | None,
    fmt: str,
    cache: bool,
    config_path: str | None,
) -> None:
    """Analyze a project: build call graph, enumerate chains, run analysis."""
    project = Path(project_path).resolve()

    # Load config file and fill in defaults for CLI args that weren't set
    config = _load_user_config(project, config_path)

    if config:
        # Apply config values only when CLI args weren't explicitly set
        def _get_src(name: str) -> click.core.ParameterSource | None:
            return ctx.get_parameter_source(name)

        if _get_src("lang") == click.core.ParameterSource.DEFAULT and "lang" in config:
            lang = tuple(config["lang"])
        if _get_src("restrict_dir") == click.core.ParameterSource.DEFAULT and "restrict_dir" in config:
            restrict_dir = config["restrict_dir"]
        if _get_src("exclude") == click.core.ParameterSource.DEFAULT and "exclude" in config:
            exclude = tuple(config["exclude"])
        if _get_src("max_depth") == click.core.ParameterSource.DEFAULT and "max_depth" in config:
            max_depth = int(config["max_depth"])
        if _get_src("max_chains") == click.core.ParameterSource.DEFAULT and "max_chains" in config:
            max_chains = int(config["max_chains"])
        if _get_src("only_cross_file") == click.core.ParameterSource.DEFAULT and "only_cross_file" in config:
            only_cross_file = bool(config["only_cross_file"])
        if _get_src("fmt") == click.core.ParameterSource.DEFAULT and "format" in config:
            fmt = config["format"]
        if _get_src("cache") == click.core.ParameterSource.DEFAULT and "cache" in config:
            cache = bool(config["cache"])
        if _get_src("output") == click.core.ParameterSource.DEFAULT and "output" in config:
            output = config["output"]

    # Parse languages
    languages = _parse_languages(lang)

    # Validate restrict_dir
    if restrict_dir:
        restrict_path = project / restrict_dir
        if not restrict_path.exists():
            console.print(f"[red]Error: --restrict-dir '{restrict_dir}' does not exist inside {project}[/red]")
            sys.exit(1)

    # Phase 1+2+3: Build call graph
    console.print(f"[bold]Analyzing[/bold] {project}")
    builder = CallGraphBuilder(project, use_cache=cache, exclude=list(exclude))
    result = builder.build(languages=languages, restrict_dir=restrict_dir)
    if cache:
        console.print(f"  [dim]Cache: {builder._cache_hits} hits, {builder._cache_misses} misses[/dim]")

    if not result.modules and not result.parse_errors:
        console.print("[yellow]No source files found.[/yellow]")
        if restrict_dir:
            console.print(f"[yellow]  Hint: check that --restrict-dir '{restrict_dir}' exists inside the project.[/yellow]")
        if languages:
            console.print("[yellow]  Hint: check that --lang matches files in the project.[/yellow]")
        sys.exit(0)

    console.print(f"  Detected languages: {', '.join(language.value for language in result.languages_detected)}")
    console.print(f"  Files: {result.total_files}, Functions: {result.total_functions}, Classes: {result.total_classes}")
    console.print(f"  Call edges: {len(result.edges)}")

    if result.parse_errors:
        n = len(result.parse_errors)
        console.print(f"  [yellow]Parse warnings: {n} file(s) failed to parse[/yellow]")
        for err in result.parse_errors[:5]:
            console.print(f"    [dim]{err['file']} ({err['phase']}): {err['error'][:120]}[/dim]")
        if n > 5:
            console.print(f"    [dim]... and {n - 5} more[/dim]")

    # Phase 4: Enumerate chains
    enumerator = ChainEnumerator(
        edges=result.edges,
        max_depth=max_depth,
        max_chains=max_chains,
        only_cross_file=only_cross_file,
        restrict_dir=restrict_dir,
    )
    summary = enumerator.enumerate_with_summary()
    result.chains = summary["chains"]
    console.print(f"  Call chains: {len(result.chains)}")

    # Phase 5: Advanced analysis
    analyzer = Analyzer(result)
    analyzer.run_all()

    # Output
    if fmt == "summary" and output is None:
        _print_summary(result)
        return

    if output is None:
        ext_map = {"json": ".json", "jsonl": ".jsonl", "dot": ".dot", "mermaid": ".md", "html": ".html", "summary": ".json"}
        output = f"callchain_report{ext_map.get(fmt, '.json')}"

    out_path = Path(output)
    if fmt == "json" or fmt == "summary":
        write_json(result, out_path)
    elif fmt == "jsonl":
        write_chains_jsonl(result, out_path)
    elif fmt == "dot":
        write_dot(result, out_path)
    elif fmt == "mermaid":
        write_mermaid_callgraph(result, out_path)
    elif fmt == "html":
        write_html(result, out_path)

    console.print(f"[green]Output written to {out_path}[/green]")


@main.command()
@click.argument("project_path", type=click.Path(exists=True))
@click.option("--lang", "-l", multiple=True, help="Languages to analyze")
@click.option("--restrict-dir", "-d", default=None, help="Restrict analysis to a subdirectory")
@click.option("--exclude", "-e", multiple=True, help="Exclude files/directories matching pattern")
@click.option("--debounce", default=1.0, show_default=True, help="Debounce interval in seconds between re-analyses")
@click.option("--config", "config_path", default=None, help="Path to config file (default: .callchain.toml in project root)")
def watch(
    project_path: str,
    lang: tuple[str, ...],
    restrict_dir: str | None,
    exclude: tuple[str, ...],
    debounce: float,
    config_path: str | None,
) -> None:
    """Re-run analysis automatically when files change."""
    try:
        event_handler_base, observer_factory = _import_watchdog_components()
    except ImportError:
        console.print("[red]Error: watchdog is not installed. Install with: pip install watchdog[/red]")
        sys.exit(1)

    project = Path(project_path).resolve()

    config = _load_user_config(project, config_path)
    if not lang and "lang" in config:
        lang = tuple(config["lang"])
    if restrict_dir is None and "restrict_dir" in config:
        restrict_dir = config["restrict_dir"]
    if not exclude and "exclude" in config:
        exclude = tuple(config["exclude"])

    # Parse languages once
    languages = _parse_languages(lang)
    _watch_project(
        project,
        languages,
        restrict_dir,
        exclude,
        debounce,
        event_handler_base,
        observer_factory,
    )


# Reverse map for watch-mode: extension -> Language
_EXT_MAP_REVERSE = {
    ".py": Language.PYTHON,
    ".js": Language.JAVASCRIPT, ".jsx": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT, ".tsx": Language.TYPESCRIPT,
    ".java": Language.JAVA,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".c": Language.C, ".h": Language.C,
    ".cpp": Language.CPP, ".cc": Language.CPP, ".cxx": Language.CPP,
    ".hpp": Language.CPP, ".hxx": Language.CPP,
}


def _print_summary(result: AnalysisResult) -> None:
    """Print a rich summary to the terminal."""
    console.print()

    # Summary table
    table = Table(title="Project Summary", show_header=False, border_style="dim")
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Languages", ", ".join(language.value for language in result.languages_detected))
    table.add_row("Files", str(result.total_files))
    table.add_row("Functions", str(result.total_functions))
    table.add_row("Classes", str(result.total_classes))
    table.add_row("Call Edges", str(len(result.edges)))
    table.add_row("Call Chains", str(len(result.chains)))
    console.print(table)

    # Complexity
    if result.complexity_distribution:
        ct = Table(title="Complexity Distribution", border_style="dim")
        ct.add_column("Range")
        ct.add_column("Count", justify="right")
        for bucket, count in result.complexity_distribution.items():
            ct.add_row(bucket, str(count))
        console.print(ct)

    # Hotspots
    if result.hotspot_functions:
        ht = Table(title="Hotspot Functions (Most Called)", border_style="dim")
        ht.add_column("Function")
        ht.add_column("File")
        ht.add_column("Calls", justify="right")
        for func, count in result.hotspot_functions[:10]:
            ht.add_row(func.display_name, func.file_path, str(count))
        console.print(ht)

    # Coupling (top by instability)
    if result.module_coupling:
        mt = Table(title="Module Coupling (Top 10 Unstable)", border_style="dim")
        mt.add_column("Module")
        mt.add_column("Fan-In", justify="right")
        mt.add_column("Fan-Out", justify="right")
        mt.add_column("Instability", justify="right")
        sorted_coupling = sorted(result.module_coupling.items(), key=lambda x: x[1].instability, reverse=True)
        for mod, metrics in sorted_coupling[:10]:
            mt.add_row(mod, str(metrics.fan_in), str(metrics.fan_out), f"{metrics.instability:.3f}")
        console.print(mt)

    # Circular dependencies
    if result.circular_dependencies:
        console.print(f"\n[red]Circular Dependencies ({len(result.circular_dependencies)}):[/red]")
        for cycle in result.circular_dependencies[:5]:
            console.print(f"  [dim]{'  ->  '.join(cycle)}[/dim]")
    else:
        console.print("\n[green]No circular dependencies detected.[/green]")

    # Unused imports
    if result.unused_imports:
        console.print(f"\n[yellow]Unused imports: {len(result.unused_imports)}[/yellow]")
        for imp in result.unused_imports[:10]:
            names = ", ".join(imp.names) if imp.names else imp.module
            console.print(f"  [dim]{imp.file_path}:{imp.line} — {imp.module} ({names})[/dim]")
        if len(result.unused_imports) > 10:
            console.print(f"  [dim]... and {len(result.unused_imports) - 10} more[/dim]")

    # Class hierarchy
    if result.class_hierarchy:
        roots = [k for k, children in result.class_hierarchy.items() if children]
        if roots:
            ht2 = Table(title="Class Hierarchy (Inheritance)", border_style="dim")
            ht2.add_column("Base Class")
            ht2.add_column("Subclasses")
            for base in sorted(roots)[:15]:
                children = result.class_hierarchy[base]
                ht2.add_row(base.split(".")[-1], ", ".join(c.split(".")[-1] for c in children))
            console.print(ht2)

    # Dead functions count
    if result.dead_functions:
        console.print(f"\n[yellow]Dead functions (never called): {len(result.dead_functions)}[/yellow]")
        for func in result.dead_functions[:5]:
            console.print(f"  [dim]{func.qualified_name} ({func.file_path}:{func.line})[/dim]")
        if len(result.dead_functions) > 5:
            console.print(f"  [dim]... and {len(result.dead_functions) - 5} more[/dim]")

    console.print()


if __name__ == "__main__":
    main()
