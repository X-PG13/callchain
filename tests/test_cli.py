"""Integration tests for the CLI."""

from __future__ import annotations

import io
import os
import runpy
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

from click.testing import CliRunner
from rich.console import Console

import callchain.cli as cli
from callchain.cli import main
from callchain.core.models import AnalysisResult, CouplingMetrics, FunctionInfo, ImportInfo, Language


def test_analyze_summary_command_outputs_report(python_fixtures):
    runner = CliRunner()
    result = runner.invoke(main, ["analyze", str(python_fixtures), "--lang", "python"])

    assert result.exit_code == 0
    assert "Analyzing" in result.output
    assert "Hotspot Functions" in result.output
    assert "Unused imports:" in result.output


def test_analyze_uses_explicit_config_file(tmp_path, python_fixtures):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    shutil.copytree(python_fixtures, src_dir, dirs_exist_ok=True)

    config_path = tmp_path / "callchain.toml"
    config_path.write_text(
        "[analyze]\nlang = [\"python\"]\nrestrict_dir = \"src\"\nexclude = [\"src/utils.py\"]\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["analyze", str(project), "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Detected languages: python" in result.output
    assert "Files: 1, Functions:" in result.output


def test_analyze_writes_json_output(tmp_path, python_fixtures):
    runner = CliRunner()
    output_path = tmp_path / "report.json"

    result = runner.invoke(
        main,
        ["analyze", str(python_fixtures), "--lang", "python", "--format", "json", "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert output_path.exists()
    assert "Output written to" in result.output


def test_analyze_writes_default_output_file_in_current_directory(python_fixtures):
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            main,
            ["analyze", str(python_fixtures), "--lang", "python", "--format", "html"],
        )
        assert result.exit_code == 0
        assert Path("callchain_report.html").exists()


def test_analyze_reports_empty_projects(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["analyze", str(tmp_path), "--lang", "python"])

    assert result.exit_code == 0
    assert "No source files found." in result.output


def test_analyze_reports_empty_projects_with_restrict_dir_hints(tmp_path):
    (tmp_path / "src").mkdir()
    runner = CliRunner()

    result = runner.invoke(main, ["analyze", str(tmp_path), "--lang", "python", "--restrict-dir", "src"])

    assert result.exit_code == 0
    assert "No source files found." in result.output
    assert "Hint: check that --restrict-dir 'src' exists inside the project." in result.output
    assert "Hint: check that --lang matches files in the project." in result.output


def test_module_execution_entrypoint_runs(python_fixtures):
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    src_path = str(repo_root / "src")
    env["PYTHONPATH"] = src_path if not existing_pythonpath else f"{src_path}{os.pathsep}{existing_pythonpath}"

    proc = subprocess.run(
        [sys.executable, "-m", "callchain.cli", "analyze", str(python_fixtures), "--lang", "python"],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    assert "Analyzing" in proc.stdout


def test_cli_runpy_entrypoint_hits___main__(capsys):
    argv = sys.argv[:]
    sys.argv = ["callchain.cli", "--version"]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                runpy.run_module("callchain.cli", run_name="__main__")
            except SystemExit as exc:
                assert exc.code == 0
    finally:
        sys.argv = argv

    assert "version" in capsys.readouterr().out.lower()


def test_analyze_rejects_unknown_language(python_fixtures):
    runner = CliRunner()
    result = runner.invoke(main, ["analyze", str(python_fixtures), "--lang", "wat"])

    assert result.exit_code == 1
    assert "Unknown language: wat" in result.output
    assert "Supported:" in result.output


def test_analyze_rejects_missing_config_file(python_fixtures):
    runner = CliRunner()
    result = runner.invoke(main, ["analyze", str(python_fixtures), "--config", "missing.toml"])

    assert result.exit_code == 1
    assert "config file 'missing.toml' not found" in result.output


def test_analyze_rejects_invalid_config_file(tmp_path, python_fixtures):
    config = tmp_path / "bad.toml"
    config.write_text("[analyze\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(main, ["analyze", str(python_fixtures), "--config", str(config)])

    assert result.exit_code == 1
    assert "Error loading config" in result.output


def test_analyze_rejects_missing_restrict_dir(python_fixtures):
    runner = CliRunner()
    result = runner.invoke(main, ["analyze", str(python_fixtures), "--lang", "python", "--restrict-dir", "missing"])

    assert result.exit_code == 1
    assert "--restrict-dir 'missing' does not exist" in result.output


def test_watch_command_runs_controlled_cycle(monkeypatch, python_fixtures):
    runner = CliRunner()
    observer_instances: list[FakeObserver] = []

    class FakeEvent:
        def __init__(self, src_path: str, is_directory: bool = False):
            self.src_path = src_path
            self.is_directory = is_directory

    class FakeHandlerBase:
        pass

    class ImmediateTimer:
        def __init__(self, delay: float, callback):
            self.delay = delay
            self.callback = callback
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

        def start(self) -> None:
            if not self.cancelled:
                self.callback()

    class FakeObserver:
        def __init__(self):
            self.handler = None
            self.path = None
            self.recursive = False
            self.stopped = False
            self.joined = False

        def schedule(self, handler, path: str, recursive: bool) -> None:
            self.handler = handler
            self.path = path
            self.recursive = recursive

        def start(self) -> None:
            assert self.handler is not None
            assert self.path is not None
            self.handler.on_any_event(FakeEvent(str(Path(self.path) / "sample.py")))
            self.handler.on_any_event(FakeEvent(str(Path(self.path) / "README.md")))

        def stop(self) -> None:
            self.stopped = True

        def join(self) -> None:
            self.joined = True

    def fake_observer_factory() -> FakeObserver:
        observer = FakeObserver()
        observer_instances.append(observer)
        return observer

    def fake_wait_forever() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_import_watchdog_components", lambda: (FakeHandlerBase, fake_observer_factory))
    monkeypatch.setattr(cli, "_make_timer", lambda delay, callback: ImmediateTimer(delay, callback))
    monkeypatch.setattr(cli, "_wait_forever", fake_wait_forever)

    result = runner.invoke(main, ["watch", str(python_fixtures), "--lang", "python", "--debounce", "0"])

    assert result.exit_code == 0
    assert result.output.count("Re-analyzing") == 2
    assert "Stopped watching." in result.output
    assert observer_instances[0].recursive is True
    assert observer_instances[0].stopped is True
    assert observer_instances[0].joined is True


def test_watch_project_reschedules_timer_and_ignores_directory_events(monkeypatch, tmp_path):
    analysis_calls = {"count": 0}
    timers: list[FakeTimer] = []
    observer_instances: list[FakeObserver] = []

    class FakeEvent:
        def __init__(self, src_path: str, is_directory: bool = False):
            self.src_path = src_path
            self.is_directory = is_directory

    class FakeHandlerBase:
        pass

    class FakeTimer:
        def __init__(self, delay: float, callback):
            self.delay = delay
            self.callback = callback
            self.cancelled = False
            self.started = False

        def cancel(self) -> None:
            self.cancelled = True

        def start(self) -> None:
            self.started = True

    class FakeObserver:
        def __init__(self):
            self.handler = None
            self.stopped = False
            self.joined = False

        def schedule(self, handler, path: str, recursive: bool) -> None:
            self.handler = handler

        def start(self) -> None:
            assert self.handler is not None
            self.handler.on_any_event(FakeEvent(str(tmp_path / "pkg"), is_directory=True))
            self.handler.on_any_event(FakeEvent(str(tmp_path / "one.py")))
            first_timer = timers[0]
            self.handler.on_any_event(FakeEvent(str(tmp_path / "two.py")))
            assert first_timer.cancelled is True

        def stop(self) -> None:
            self.stopped = True

        def join(self) -> None:
            self.joined = True

    def fake_run_watch_analysis(*args, **kwargs) -> None:
        analysis_calls["count"] += 1

    def fake_timer_factory(delay: float, callback):
        timer = FakeTimer(delay, callback)
        timers.append(timer)
        return timer

    def fake_observer_factory() -> FakeObserver:
        observer = FakeObserver()
        observer_instances.append(observer)
        return observer

    monkeypatch.setattr(cli, "_run_watch_analysis", fake_run_watch_analysis)

    def fake_wait_forever() -> None:
        raise KeyboardInterrupt

    cli._watch_project(
        tmp_path,
        [Language.PYTHON],
        None,
        (),
        0.2,
        FakeHandlerBase,
        fake_observer_factory,
        timer_factory=fake_timer_factory,
        wait_forever=fake_wait_forever,
    )

    assert analysis_calls["count"] == 1
    assert len(timers) == 2
    assert all(timer.started for timer in timers)
    assert observer_instances[0].stopped is True
    assert observer_instances[0].joined is True


def test_watch_command_uses_config_defaults(monkeypatch, tmp_path, python_fixtures):
    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)
    shutil.copytree(python_fixtures, src_dir, dirs_exist_ok=True)
    (project / ".callchain.toml").write_text(
        "[analyze]\nlang = [\"python\"]\nrestrict_dir = \"src\"\nexclude = [\"src/utils.py\"]\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    runner = CliRunner()

    def fake_watch_project(project, languages, restrict_dir, exclude, debounce, event_handler_base, observer_factory):
        captured["project"] = project
        captured["languages"] = languages
        captured["restrict_dir"] = restrict_dir
        captured["exclude"] = exclude
        captured["debounce"] = debounce

    monkeypatch.setattr(cli, "_import_watchdog_components", lambda: (object, object))
    monkeypatch.setattr(cli, "_watch_project", fake_watch_project)

    result = runner.invoke(main, ["watch", str(project)])

    assert result.exit_code == 0
    assert captured["project"] == project.resolve()
    assert captured["languages"] == [cli.Language.PYTHON]
    assert captured["restrict_dir"] == "src"
    assert captured["exclude"] == ("src/utils.py",)
    assert captured["debounce"] == 1.0


def test_watch_command_reports_missing_watchdog(monkeypatch, python_fixtures):
    runner = CliRunner()

    def raise_import_error():
        raise ImportError("missing watchdog")

    monkeypatch.setattr(cli, "_import_watchdog_components", raise_import_error)
    result = runner.invoke(main, ["watch", str(python_fixtures)])

    assert result.exit_code == 1
    assert "watchdog is not installed" in result.output


def test_watch_command_rejects_unknown_language(python_fixtures):
    runner = CliRunner()
    result = runner.invoke(main, ["watch", str(python_fixtures), "--lang", "wat"])

    assert result.exit_code == 1
    assert "Unknown language: wat" in result.output


def test_cli_helper_functions_and_summary_render(monkeypatch):
    assert cli._parse_languages(()) is None
    assert cli._watch_extensions(None) >= {".py", ".cpp", ".c"}
    assert cli._watch_extensions([Language.PYTHON]) == {".py"}
    event_handler_base, observer_factory = cli._import_watchdog_components()
    assert event_handler_base is not None
    assert observer_factory is not None
    timer = cli._make_timer(5, lambda: None)
    timer.cancel()
    assert hasattr(timer, "start")

    sleep_calls = {"count": 0}

    def fake_sleep(_: float) -> None:
        sleep_calls["count"] += 1
        raise KeyboardInterrupt

    try:
        cli._wait_forever(fake_sleep)
    except KeyboardInterrupt:
        pass

    assert sleep_calls["count"] == 1

    output = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, force_terminal=False, width=120))
    funcs = [_make_function(f"dead_{idx}", f"pkg.dead_{idx}", "dead.py") for idx in range(6)]
    hot = _make_function("hot", "pkg.hot", "hot.py")
    result = AnalysisResult(
        project_path="demo",
        languages_detected=[Language.PYTHON],
        total_files=3,
        total_functions=8,
        total_classes=2,
        complexity_distribution={"low (1-5)": 1, "medium (6-10)": 2, "high (11-20)": 0, "very_high (21+)": 0},
        hotspot_functions=[(hot, 5)],
        circular_dependencies=[["a.py", "b.py", "a.py"]],
        module_coupling={"a.py": CouplingMetrics(fan_in=1, fan_out=2, instability=0.667)},
        unused_imports=[ImportInfo(module=f"mod_{idx}", file_path="dead.py", line=idx, names=["name"]) for idx in range(11)],
        class_hierarchy={"pkg.Base": [f"pkg.Child{idx}" for idx in range(2)], "pkg.Child0": [], "pkg.Child1": []},
        dead_functions=funcs,
        chains=[],
        edges=[],
        modules=[],
    )

    cli._print_summary(result)
    text = output.getvalue()

    assert "Circular Dependencies (1):" in text
    assert "Module Coupling (Top 10 Unstable)" in text
    assert "Class Hierarchy (Inheritance)" in text
    assert "Unused imports: 11" in text
    assert "Dead functions (never called): 6" in text
    assert "... and 1 more" in text


def test_analyze_applies_config_defaults_and_reports_parse_warnings(monkeypatch, tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    output_path = tmp_path / "report.jsonl"
    captured: dict[str, object] = {}
    parse_errors = [{"file": f"broken_{idx}.py", "phase": "parse", "error": f"error {idx}"} for idx in range(6)]

    class FakeBuilder:
        def __init__(self, project_path, use_cache=False, exclude=None):
            captured["builder_project"] = Path(project_path)
            captured["use_cache"] = use_cache
            captured["exclude"] = exclude
            self._cache_hits = 2
            self._cache_misses = 1

        def build(self, languages=None, restrict_dir=None):
            captured["languages"] = languages
            captured["restrict_dir"] = restrict_dir
            return AnalysisResult(
                project_path=str(project),
                languages_detected=[Language.PYTHON],
                total_files=1,
                total_functions=1,
                total_classes=0,
                edges=[],
                chains=[],
                modules=[],
                parse_errors=parse_errors,
            )

    class FakeEnumerator:
        def __init__(self, *, edges, max_depth, max_chains, only_cross_file, restrict_dir):
            captured["max_depth"] = max_depth
            captured["max_chains"] = max_chains
            captured["only_cross_file"] = only_cross_file
            captured["enum_restrict_dir"] = restrict_dir

        def enumerate_with_summary(self):
            return {"chains": []}

    class FakeAnalyzer:
        def __init__(self, result):
            captured["analyzer_result"] = result

        def run_all(self):
            captured["analyzer_ran"] = True

    monkeypatch.setattr(
        cli,
        "_load_user_config",
        lambda project_root, config_path=None: {
            "lang": ["python"],
            "restrict_dir": "src",
            "exclude": ["src/generated.py"],
            "max_depth": 7,
            "max_chains": 11,
            "only_cross_file": True,
            "format": "jsonl",
            "cache": True,
            "output": str(output_path),
        },
    )
    monkeypatch.setattr(cli, "CallGraphBuilder", FakeBuilder)
    monkeypatch.setattr(cli, "ChainEnumerator", FakeEnumerator)
    monkeypatch.setattr(cli, "Analyzer", FakeAnalyzer)
    monkeypatch.setattr(cli, "write_chains_jsonl", lambda result, out_path: captured.setdefault("writer_path", out_path))

    result = runner.invoke(main, ["analyze", str(project)])

    assert result.exit_code == 0
    assert "Cache: 2 hits, 1 misses" in result.output
    assert "Parse warnings: 6 file(s) failed to parse" in result.output
    assert "... and 1 more" in result.output
    assert captured["use_cache"] is True
    assert captured["exclude"] == ["src/generated.py"]
    assert captured["languages"] == [Language.PYTHON]
    assert captured["restrict_dir"] == "src"
    assert captured["max_depth"] == 7
    assert captured["max_chains"] == 11
    assert captured["only_cross_file"] is True
    assert captured["enum_restrict_dir"] == "src"
    assert captured["writer_path"] == output_path
    assert captured["analyzer_ran"] is True


def test_analyze_writes_dot_and_mermaid_outputs(monkeypatch, tmp_path):
    runner = CliRunner()
    project = tmp_path / "project"
    project.mkdir()
    fake_result = AnalysisResult(
        project_path=str(project),
        languages_detected=[Language.PYTHON],
        total_files=1,
        total_functions=1,
        total_classes=0,
        edges=[],
        chains=[],
        modules=[object()],
    )
    calls: list[tuple[str, Path]] = []

    class FakeBuilder:
        def __init__(self, project_path, use_cache=False, exclude=None):
            self._cache_hits = 0
            self._cache_misses = 0

        def build(self, languages=None, restrict_dir=None):
            return fake_result

    class FakeEnumerator:
        def __init__(self, **kwargs):
            pass

        def enumerate_with_summary(self):
            return {"chains": []}

    class FakeAnalyzer:
        def __init__(self, result):
            pass

        def run_all(self):
            pass

    monkeypatch.setattr(cli, "CallGraphBuilder", FakeBuilder)
    monkeypatch.setattr(cli, "ChainEnumerator", FakeEnumerator)
    monkeypatch.setattr(cli, "Analyzer", FakeAnalyzer)
    monkeypatch.setattr(cli, "write_dot", lambda result, out_path: calls.append(("dot", out_path)))
    monkeypatch.setattr(cli, "write_mermaid_callgraph", lambda result, out_path: calls.append(("mermaid", out_path)))

    dot_result = runner.invoke(main, ["analyze", str(project), "--lang", "python", "--format", "dot", "--output", str(tmp_path / "graph.dot")])
    mermaid_result = runner.invoke(
        main,
        ["analyze", str(project), "--lang", "python", "--format", "mermaid", "--output", str(tmp_path / "graph.md")],
    )

    assert dot_result.exit_code == 0
    assert mermaid_result.exit_code == 0
    assert ("dot", tmp_path / "graph.dot") in calls
    assert ("mermaid", tmp_path / "graph.md") in calls


def _make_function(name: str, qualified_name: str, file_path: str) -> FunctionInfo:
    return FunctionInfo(
        name=name,
        qualified_name=qualified_name,
        file_path=file_path,
        line=1,
        language=Language.PYTHON,
    )
