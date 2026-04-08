"""Tests for the TypeScript language plugin."""

from callchain.languages.javascript_lang import TypeScriptPlugin


def test_discover_files(typescript_fixtures):
    plugin = TypeScriptPlugin()
    files = plugin.discover_files(typescript_fixtures)
    names = {f.name for f in files}
    assert "sample.ts" in names


def test_parse_functions(typescript_fixtures):
    plugin = TypeScriptPlugin()
    mod = plugin.parse_file(typescript_fixtures / "sample.ts", typescript_fixtures)
    func_names = {f.name for f in mod.functions}
    assert "increment" in func_names
    assert "double" in func_names


def test_parse_classes(typescript_fixtures):
    plugin = TypeScriptPlugin()
    mod = plugin.parse_file(typescript_fixtures / "sample.ts", typescript_fixtures)
    assert len(mod.classes) >= 1
    cls = next(c for c in mod.classes if c.name == "Calculator")
    method_names = {m.name for m in cls.methods}
    assert "add" in method_names
    assert "log" in method_names
    assert "constructor" in method_names


def test_async_method(typescript_fixtures):
    plugin = TypeScriptPlugin()
    mod = plugin.parse_file(typescript_fixtures / "sample.ts", typescript_fixtures)
    cls = next(c for c in mod.classes if c.name == "Calculator")
    fetch_method = next((m for m in cls.methods if m.name == "fetchValue"), None)
    assert fetch_method is not None
    assert fetch_method.is_async


def test_arrow_in_object(typescript_fixtures):
    plugin = TypeScriptPlugin()
    mod = plugin.parse_file(typescript_fixtures / "sample.ts", typescript_fixtures)
    func_names = {f.name for f in mod.functions}
    assert "onAdd" in func_names
    assert "onReset" in func_names


def test_extract_calls(typescript_fixtures):
    plugin = TypeScriptPlugin()
    edges = plugin.extract_calls(typescript_fixtures / "sample.ts", typescript_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}
    assert any("increment" in n for n in callee_names)


def test_imports(typescript_fixtures):
    plugin = TypeScriptPlugin()
    mod = plugin.parse_file(typescript_fixtures / "sample.ts", typescript_fixtures)
    assert any("events" in i.module for i in mod.imports)
