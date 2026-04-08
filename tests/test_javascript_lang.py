"""Tests for the JavaScript language plugin."""

from pathlib import Path

import pytest

from callchain.languages.javascript_lang import (
    JavaScriptPlugin,
    TypeScriptPlugin,
    _JSPluginBase,
    _get_js_parser,
    _module_from_path,
)


def test_discover_files(javascript_fixtures):
    plugin = JavaScriptPlugin()
    files = plugin.discover_files(javascript_fixtures)
    names = {f.name for f in files}
    assert "sample.js" in names


def test_parse_functions(javascript_fixtures):
    plugin = JavaScriptPlugin()
    mod = plugin.parse_file(javascript_fixtures / "sample.js", javascript_fixtures)
    func_names = {f.name for f in mod.functions}
    assert "increment" in func_names
    assert "main" in func_names
    assert "double" in func_names
    assert "fetchData" in func_names


def test_parse_classes(javascript_fixtures):
    plugin = JavaScriptPlugin()
    mod = plugin.parse_file(javascript_fixtures / "sample.js", javascript_fixtures)
    assert len(mod.classes) == 1
    cls = mod.classes[0]
    assert cls.name == "Calculator"
    method_names = {m.name for m in cls.methods}
    assert "constructor" in method_names
    assert "add" in method_names
    assert "subtract" in method_names


def test_extract_calls(javascript_fixtures):
    plugin = JavaScriptPlugin()
    edges = plugin.extract_calls(javascript_fixtures / "sample.js", javascript_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}
    assert any("increment" in n for n in callee_names)


def test_async_function(javascript_fixtures):
    plugin = JavaScriptPlugin()
    mod = plugin.parse_file(javascript_fixtures / "sample.js", javascript_fixtures)
    fetch_fn = next((f for f in mod.functions if f.name == "fetchData"), None)
    assert fetch_fn is not None
    assert fetch_fn.is_async


def test_arrow_in_object(javascript_fixtures):
    plugin = JavaScriptPlugin()
    mod = plugin.parse_file(javascript_fixtures / "sample.js", javascript_fixtures)
    func_names = {f.name for f in mod.functions}
    assert "onAdd" in func_names
    assert "onReset" in func_names


def test_arrow_in_object_calls(javascript_fixtures):
    """Arrow functions inside objects should still produce call edges."""
    plugin = JavaScriptPlugin()
    edges = plugin.extract_calls(javascript_fixtures / "sample.js", javascript_fixtures)
    # onAdd calls increment
    on_add_edges = [e for e in edges if e.caller.name == "onAdd"]
    assert len(on_add_edges) >= 1
    assert any("increment" in e.callee.qualified_name for e in on_add_edges)


def test_javascript_module_names_and_internal_helpers():
    plugin = JavaScriptPlugin()
    root = _get_js_parser().parse(b"const value = 1;").root_node
    call_node = _get_js_parser().parse(b"console.log(value);").root_node.children[0].children[0]

    assert _module_from_path("pkg/component.jsx") == "pkg.component"
    assert plugin._parse_class(root, b"const value = 1;", "sample.js", "sample") is None
    assert plugin._parse_function(root, b"const value = 1;", "sample.js", "sample", None) is None
    assert plugin._parse_import(root, b"const value = 1;", "sample.js") is None
    assert plugin._resolve_call(root, b"const value = 1;") is None
    assert plugin._resolve_call(call_node, b"console.log(value);") == "console.log"


def test_parse_javascript_export_class_imports_and_nested_arrows(tmp_path):
    plugin = JavaScriptPlugin()
    source = b'''
import thing, { alpha as beta } from "pkg";
import * as ns from "mod";

export class Fancy extends Base {
    static make() {
        return helper(1);
    }
}

export const exported = value => helper(value);

const api = {
    run: x => helper(x),
    nested: {
        inner: async (value) => ns.call(value),
    },
};

function helper(v) {
    return v;
}

function main() {
    console.log(api.run(1));
}
'''
    path = tmp_path / "advanced.js"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)
    classes = {cls.name: cls for cls in mod.classes}
    imports = {(imp.module, tuple(imp.names)) for imp in mod.imports}
    variables = {var.name for var in mod.variables}
    functions = {func.name: func for func in mod.functions}
    edges = plugin.extract_calls(path, tmp_path)
    callees = {edge.callee.qualified_name for edge in edges}

    assert classes["Fancy"].bases == ["Base"]
    assert {method.name for method in classes["Fancy"].methods} == {"make"}
    assert ("pkg", ("thing", "alpha")) in imports
    assert ("mod", ("*",)) in imports
    assert "api" in variables
    assert functions["run"].signature == "run(x)"
    assert functions["inner"].is_async is True
    assert "helper" in callees
    assert "ns.call" in callees
    assert "api.run" in callees
    assert "console.log" in callees


def test_typescript_plugin_uses_tsx_parser(tmp_path):
    plugin = TypeScriptPlugin()
    path = tmp_path / "App.tsx"
    path.write_text(
        """
export const App = (value: number) => <div>{helper(value)}</div>;

function helper(value: number): number {
    return value;
}
""",
        encoding="utf-8",
    )

    mod = plugin.parse_file(path, tmp_path)
    edges = plugin.extract_calls(path, tmp_path)

    assert {"App", "helper"} <= {func.name for func in mod.functions}
    assert any(edge.callee.qualified_name == "helper" for edge in edges)


def test_javascript_base_parser_guard_and_export_function_branch(tmp_path):
    plugin = JavaScriptPlugin()
    source = b"""
export function top(value) {
    if (value) {
        return helper(value);
    }
    return value;
}

function helper(value) {
    return value;
}
"""
    path = tmp_path / "exported.js"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)
    edges = plugin.extract_calls(path, tmp_path)
    top = next(func for func in mod.functions if func.name == "top")

    assert top.complexity >= 2
    assert any(edge.caller.name == "top" and edge.callee.qualified_name == "helper" for edge in edges)
    with pytest.raises(NotImplementedError):
        _JSPluginBase()._get_parser(Path("sample.js"))


def test_javascript_handles_missing_variable_names_and_unsupported_call_targets():
    plugin = JavaScriptPlugin()

    class FakePoint:
        row = 0

    class FakeNode:
        def __init__(self, node_type: str, *, children=None, fields=None):
            self.type = node_type
            self.children = children or []
            self._fields = fields or {}
            self.start_point = FakePoint()
            self.end_point = FakePoint()

        def child_by_field_name(self, name: str):
            return self._fields.get(name)

    decl = FakeNode("lexical_declaration", children=[FakeNode("variable_declarator")])
    functions: list = []
    variables: list = []

    plugin._extract_arrow_funcs(decl, b"", "sample.js", "sample", functions, variables)

    unsupported_root = _get_js_parser().parse(b"(fn())();").root_node
    unsupported_call = unsupported_root.children[0].children[0]

    assert functions == []
    assert variables == []
    assert plugin._resolve_call(unsupported_call, b"(fn())();") is None
