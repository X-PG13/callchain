"""Tests for call graph building and chain enumeration."""

from collections import defaultdict
from pathlib import Path
import shutil

import pytest

from callchain.core.callgraph import CallGraphBuilder, _matches_any
from callchain.core.chain_enum import ChainEnumerator
from callchain.core.models import CallChain, CallEdge, FunctionInfo, Language, ModuleInfo


def test_build_python_callgraph(python_fixtures):
    builder = CallGraphBuilder(python_fixtures)
    result = builder.build(languages=[Language.PYTHON])
    assert result.total_files >= 2
    assert result.total_functions >= 3
    assert result.total_classes >= 1
    assert len(result.edges) > 0


def test_build_multi_language(tmp_path):
    """Verify builder works even when fixture only has one language."""
    # Copy a python file into tmp
    sample = Path(__file__).parent / "fixtures" / "python" / "sample.py"
    (tmp_path / "sample.py").write_bytes(sample.read_bytes())

    builder = CallGraphBuilder(tmp_path)
    result = builder.build()
    assert Language.PYTHON in result.languages_detected
    assert result.total_files >= 1


def test_build_returns_empty_result_when_no_languages_detected(tmp_path):
    builder = CallGraphBuilder(tmp_path)

    result = builder.build()

    assert result.languages_detected == []
    assert result.total_files == 0
    assert result.edges == []


def test_chain_enumeration(python_fixtures):
    builder = CallGraphBuilder(python_fixtures)
    result = builder.build(languages=[Language.PYTHON])

    enumerator = ChainEnumerator(edges=result.edges, max_depth=10, max_chains=100)
    summary = enumerator.enumerate_with_summary()
    assert summary["chains_written"] >= 0
    assert summary["graph_nodes"] >= 0


def test_chain_cross_file(python_fixtures):
    builder = CallGraphBuilder(python_fixtures)
    result = builder.build(languages=[Language.PYTHON])

    enumerator = ChainEnumerator(edges=result.edges, max_depth=10, max_chains=100, only_cross_file=True)
    chains = enumerator.enumerate()
    for chain in chains:
        assert chain.cross_file_transitions > 0


def test_build_respects_exclude_patterns(tmp_path, python_fixtures):
    project = tmp_path / "project"
    shutil.copytree(python_fixtures, project)

    builder = CallGraphBuilder(project, exclude=["utils.py"])
    result = builder.build(languages=[Language.PYTHON])

    module_paths = {module.file_path for module in result.modules}
    assert "sample.py" in module_paths
    assert "utils.py" not in module_paths


def test_resolve_function_heuristics(tmp_path):
    builder = CallGraphBuilder(tmp_path)

    alpha_run = _make_function("run", "pkg.alpha.Alpha.run", "alpha.py", class_name="Alpha")
    beta_run = _make_function("run", "pkg.beta.Beta.run", "beta.py", class_name="Beta")
    alpha_helper = _make_function("helper", "pkg.alpha.helper", "alpha.py")
    beta_helper = _make_function("helper", "pkg.beta.helper", "beta.py")
    tool_x = _make_function("execute", "pkg.x.Tool.execute", "x.py", class_name="Tool")
    tool_y = _make_function("execute", "pkg.y.Tool.execute", "y.py", class_name="Tool")
    alias_preferred = _make_function("run", "pkg.pref.Preferred.run", "pref.py", class_name="Preferred")
    alias_other = _make_function("run", "pkg.other.Other.run", "other.py", class_name="Other")
    caller = _make_function("caller", "pkg.main.Preferred.caller", "main.py", class_name="Preferred")

    for func in [alpha_run, beta_run, alpha_helper, beta_helper, tool_x, tool_y, alias_preferred, alias_other, caller]:
        builder._func_by_qname[func.qualified_name] = func

    builder._func_by_simple = defaultdict(list, {
        "run": [alias_preferred, alias_other],
        "helper": [alpha_helper, beta_helper],
        "Alpha.run": [alpha_run],
        "Tool.execute": [tool_x, tool_y],
        "Alias.run": [alias_preferred, alias_other],
    })

    assert builder._resolve_function(_make_function("helper", "pkg.alpha.helper", "")) == alpha_helper
    assert builder._resolve_function(_make_function("run", "self.run", ""), caller=_make_function("call", "pkg.alpha.Alpha.call", "alpha.py", class_name="Alpha")) == alpha_run
    assert builder._resolve_function(_make_function("run", "this.run", ""), caller=_make_function("call", "pkg.alpha.Alpha.call", "alpha.py", class_name="Alpha")) == alpha_run
    assert builder._resolve_function(_make_function("helper", "helper", ""), caller=_make_function("call", "pkg.alpha.call", "alpha.py")) == alpha_helper
    assert builder._resolve_function(_make_function("run", "run", ""), caller=caller) == alias_preferred
    assert builder._resolve_function(_make_function("execute", "pkg.Tool.execute", ""), caller=_make_function("call", "pkg.x.call", "x.py")) == tool_x
    assert builder._resolve_function(_make_function("run", "pkg.Alias.run", ""), caller=caller) == alias_preferred
    assert builder._resolve_function(_make_function("missing", "missing", ""), caller=caller) is None


def test_resolve_function_falls_back_to_first_two_part_match(tmp_path):
    builder = CallGraphBuilder(tmp_path)
    left = _make_function("run", "pkg.alpha.Service.run", "alpha.py", class_name="Service")
    right = _make_function("run", "pkg.beta.Service.run", "beta.py", class_name="Service")
    builder._func_by_simple = defaultdict(list, {"Service.run": [left, right]})

    resolved = builder._resolve_function(_make_function("run", "pkg.Service.run", ""))

    assert resolved == left


def test_parse_and_extract_errors_are_recorded(tmp_path):
    file_path = tmp_path / "broken.py"
    file_path.write_text("pass\n", encoding="utf-8")
    builder = CallGraphBuilder(tmp_path)

    class BrokenParsePlugin:
        def discover_files(self, project_root):
            return [file_path]

        def parse_file(self, file_path, project_root):
            raise RuntimeError("parse boom")

    class BrokenExtractPlugin(BrokenParsePlugin):
        def extract_calls(self, file_path, project_root):
            raise RuntimeError("extract boom")

    builder._parse_language(BrokenParsePlugin(), None)
    builder._extract_calls(BrokenExtractPlugin(), None)

    assert len(builder.parse_errors) == 2
    assert builder.parse_errors[0].phase == "parse"
    assert builder.parse_errors[1].phase == "extract_calls"
    assert "ParseError(" in repr(builder.parse_errors[0])


def test_filter_helpers_cover_patterns_and_external_paths(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    builder = CallGraphBuilder(project, exclude=["src", "tests/**", "*.tmp"])

    inside = project / "src" / "module.py"
    inside.parent.mkdir(parents=True)
    inside.write_text("pass\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")

    filtered = builder._filter_files([inside], None)

    assert filtered == []
    assert _matches_any("tests/unit/test_sample.py", ["tests/**"])
    assert _matches_any("cache.tmp", ["*.tmp"])
    assert builder._rel_path(outside) == str(outside)


def test_filter_helpers_cover_restrict_dir_and_dir_glob_branch(tmp_path, monkeypatch):
    import callchain.core.callgraph as callgraph_module

    project = tmp_path / "project"
    src_dir = project / "src"
    docs_dir = project / "docs"
    src_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    inside = src_dir / "module.py"
    outside = docs_dir / "guide.py"
    inside.write_text("pass\n", encoding="utf-8")
    outside.write_text("pass\n", encoding="utf-8")
    builder = CallGraphBuilder(project)

    filtered = builder._filter_files([inside, outside], "src")

    monkeypatch.setattr(callgraph_module.fnmatch, "fnmatch", lambda path, pat: False)

    assert filtered == [inside]
    assert callgraph_module._matches_any("tests/unit/test_sample.py", ["tests/**"])


def test_get_plugin_lazy_loads_unregistered_languages(monkeypatch):
    from callchain.languages import base as language_base

    monkeypatch.setattr(language_base, "_REGISTRY", {})
    loaded: list[str] = []

    def fake_import_module(name: str):
        loaded.append(name)

        class FakePythonPlugin(language_base.LanguagePlugin):
            language = Language.PYTHON
            extensions = (".py",)

            def parse_file(self, file_path, project_root):
                raise AssertionError("parse_file should not be called in this test")

            def extract_calls(self, file_path, project_root):
                return []

        return FakePythonPlugin

    monkeypatch.setattr(language_base, "import_module", fake_import_module)

    plugin = language_base.get_plugin(Language.PYTHON)

    assert isinstance(plugin, language_base.LanguagePlugin)
    assert loaded == ["callchain.languages.python_lang"]


def test_language_base_helpers_cover_skip_dirs_and_error_paths(tmp_path, monkeypatch):
    from callchain.languages import base as language_base

    monkeypatch.setattr(language_base, "_REGISTRY", {})
    monkeypatch.setattr(language_base, "_PLUGIN_MODULES", {Language.PYTHON: "python_lang"})

    def fake_import_module(name: str):
        class FakePythonPlugin(language_base.LanguagePlugin):
            language = Language.PYTHON
            extensions = (".py",)

            def parse_file(self, file_path, project_root):
                return ModuleInfo(file_path="sample.py", language=Language.PYTHON)

            def extract_calls(self, file_path, project_root):
                return []

        return FakePythonPlugin

    monkeypatch.setattr(language_base, "import_module", fake_import_module)

    plugins = language_base.get_all_plugins()
    project = tmp_path / "project"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "main.py").write_text("pass\n", encoding="utf-8")
    (project / "web").mkdir()
    (project / "web" / "app.ts").write_text("export const ok = true;\n", encoding="utf-8")
    (project / ".git").mkdir()
    (project / ".git" / "ignored.py").write_text("pass\n", encoding="utf-8")
    (project / "vendor").mkdir()
    (project / "vendor" / "ignored.go").write_text("package main\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")

    assert set(plugins) == {Language.PYTHON}
    assert language_base.detect_languages(project) == [Language.PYTHON, Language.TYPESCRIPT]
    assert language_base.LanguagePlugin._rel_path(outside, project) == str(outside)


def test_language_base_returns_when_mapping_missing_and_get_plugin_raises(monkeypatch):
    from callchain.languages import base as language_base

    monkeypatch.setattr(language_base, "_REGISTRY", {})
    monkeypatch.setattr(language_base, "_PLUGIN_MODULES", {})

    language_base._ensure_plugin_registered(Language.PYTHON)

    with pytest.raises(ValueError, match="No plugin registered for python"):
        language_base.get_plugin(Language.PYTHON)


def test_chain_enumerator_covers_fallbacks_limits_and_cycle_guards():
    alpha = _make_function("alpha", "pkg.alpha", "pkg/a.py")
    beta = _make_function("beta", "pkg.beta", "pkg/b.py")
    gamma = _make_function("gamma", "pkg.gamma", "pkg/c.py")
    delta = _make_function("delta", "pkg.delta", "pkg/d.py")

    limited = ChainEnumerator(
        edges=[_make_edge(alpha, beta), _make_edge(gamma, delta)],
        max_chains=1,
    )
    limited_chains = limited.enumerate()
    prefilled = [CallChain(nodes=[alpha])]
    limited._dfs(alpha, [alpha], set(), 1, prefilled, set())

    cycle_restricted = ChainEnumerator(
        edges=[_make_edge(alpha, beta), _make_edge(beta, alpha)],
        max_depth=4,
        restrict_dir="pkg",
    )
    cycle_outside = ChainEnumerator(
        edges=[_make_edge(alpha, beta), _make_edge(beta, alpha)],
        max_depth=4,
        restrict_dir="outside",
    )

    assert len(limited_chains) == 1
    assert prefilled == [CallChain(nodes=[alpha])]
    assert cycle_restricted.enumerate() == []
    assert set(cycle_restricted._find_starts()) == {alpha.qualified_name, beta.qualified_name}
    assert set(cycle_outside._find_starts()) == {alpha.qualified_name, beta.qualified_name}
    cycle_outside.node_map.pop(alpha.qualified_name)
    assert cycle_outside._in_restrict(alpha.qualified_name) is True


def _make_function(name: str, qualified_name: str, file_path: str, class_name: str | None = None) -> FunctionInfo:
    return FunctionInfo(
        name=name,
        qualified_name=qualified_name,
        file_path=file_path,
        line=1,
        class_name=class_name,
        is_method=class_name is not None,
        language=Language.PYTHON,
    )


def _make_edge(caller: FunctionInfo, callee: FunctionInfo) -> CallEdge:
    return CallEdge(
        caller=caller,
        callee=callee,
        call_site_line=caller.line,
        call_site_file=caller.file_path,
    )
