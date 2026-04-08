"""Tests for the C++ language plugin."""

from callchain.languages.cpp_lang import CppPlugin, _get_cpp_parser, _is_valid_cpp_callee


def test_discover_files(cpp_fixtures):
    plugin = CppPlugin()
    files = plugin.discover_files(cpp_fixtures)
    names = {f.name for f in files}
    assert "calculator.cpp" in names
    assert "advanced.cpp" in names


def test_parse_classes(cpp_fixtures):
    plugin = CppPlugin()
    mod = plugin.parse_file(cpp_fixtures / "calculator.cpp", cpp_fixtures)
    class_names = {c.name for c in mod.classes}
    assert "Calculator" in class_names
    assert "AdvancedCalc" in class_names


def test_class_methods(cpp_fixtures):
    plugin = CppPlugin()
    mod = plugin.parse_file(cpp_fixtures / "calculator.cpp", cpp_fixtures)
    calc = next(c for c in mod.classes if c.name == "Calculator")
    method_names = {m.name for m in calc.methods}
    assert "add" in method_names
    assert "subtract" in method_names
    assert "multiply" in method_names


def test_inheritance(cpp_fixtures):
    plugin = CppPlugin()
    mod = plugin.parse_file(cpp_fixtures / "calculator.cpp", cpp_fixtures)
    adv = next(c for c in mod.classes if c.name == "AdvancedCalc")
    assert any("Calculator" in b for b in adv.bases)


def test_parse_includes(cpp_fixtures):
    plugin = CppPlugin()
    mod = plugin.parse_file(cpp_fixtures / "calculator.cpp", cpp_fixtures)
    modules = {i.module for i in mod.imports}
    assert "iostream" in modules
    assert "vector" in modules


def test_extract_calls(cpp_fixtures):
    plugin = CppPlugin()
    edges = plugin.extract_calls(cpp_fixtures / "calculator.cpp", cpp_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}
    assert any("add" in n for n in callee_names)
    assert any("power" in n for n in callee_names)
    assert any("greet" in n for n in callee_names)


def test_free_function(cpp_fixtures):
    plugin = CppPlugin()
    mod = plugin.parse_file(cpp_fixtures / "calculator.cpp", cpp_fixtures)
    func_names = {f.name for f in mod.functions}
    assert "greet" in func_names
    assert "main" in func_names


def test_parse_additional_cpp_constructs(cpp_fixtures):
    plugin = CppPlugin()
    mod = plugin.parse_file(cpp_fixtures / "advanced.cpp", cpp_fixtures)

    func_names = {f.qualified_name for f in mod.functions}
    class_names = {c.name for c in mod.classes}
    var_names = {v.name for v in mod.variables}

    assert "advanced.square" in func_names
    assert "advanced.Widget.declared" in func_names
    assert "advanced.Widget.invoke" in func_names
    assert "Widget" in class_names
    assert "Data" in class_names
    assert "GLOBAL_COUNTER" in var_names

    widget = next(c for c in mod.classes if c.name == "Widget")
    widget_methods = {m.name for m in widget.methods}
    assert "declared" in widget_methods
    assert "invoke" in widget_methods


def test_extract_calls_from_additional_cpp_fixture(cpp_fixtures):
    plugin = CppPlugin()
    edges = plugin.extract_calls(cpp_fixtures / "advanced.cpp", cpp_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}

    assert "helper.declared" in callee_names
    assert "Widget.declared" in callee_names
    assert "square" in callee_names


def test_callee_validation_rejects_invalid_cpp_expressions():
    assert _is_valid_cpp_callee("Widget.declared")
    assert not _is_valid_cpp_callee("")
    assert not _is_valid_cpp_callee("fn(arg)")
    assert not _is_valid_cpp_callee("bad{\n")


def test_parse_template_class_and_inline_template_method(tmp_path):
    plugin = CppPlugin()
    source = b'''
#include "local.hpp"

template <typename T>
class Box : public Widget {
public:
    template <typename U>
    U convert(U value) {
        return value;
    }
};
'''
    path = tmp_path / "templated.cpp"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)

    box = next(c for c in mod.classes if c.name == "Box")
    assert "Widget" in box.bases
    assert "local.hpp" in {imp.module for imp in mod.imports}
    assert "convert" in {method.name for method in box.methods}


def test_cpp_internal_helpers_handle_non_matching_nodes():
    plugin = CppPlugin()
    source = b"int value = 1;"
    root = _get_cpp_parser().parse(source).root_node

    assert plugin._parse_function(root, source, "tmp.cpp", "tmp", None) is None
    assert plugin._parse_class(root, source, "tmp.cpp", "tmp", []) is None
    assert plugin._parse_method_declaration(root, source, "tmp.cpp", "tmp", "Widget") is None
    assert plugin._parse_include(root, source, "tmp.cpp") is None
    assert plugin._parse_variable(root, source, "tmp.cpp") is None
    assert plugin._resolve_call(root, source) is None


def test_cpp_resolves_call_variants_and_boolean_complexity(tmp_path):
    plugin = CppPlugin()
    source = b'''
template <typename T>
T square(T value);

int target();

class Widget {
public:
    static int declared(int value);
    int member();
};

namespace ns {
int call();
}

int choose(bool a, bool b, bool c) {
    Widget helper;
    if (a && b || c) {
        helper.member();
        Widget::declared(1);
        target();
    }
    return ns::call() + square<int>(3);
}
'''
    path = tmp_path / "calls.cpp"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)
    choose = next(func for func in mod.functions if func.name == "choose")
    edges = plugin.extract_calls(path, tmp_path)
    callee_names = {edge.callee.qualified_name for edge in edges}

    assert choose.complexity >= 4
    assert "helper.member" in callee_names
    assert "Widget.declared" in callee_names
    assert "target" in callee_names
    assert "ns.call" in callee_names
    assert "square" in callee_names


def test_cpp_parses_uninitialized_variables_and_pointer_reference_functions(tmp_path):
    plugin = CppPlugin()
    source = b'''
int counter;
int *buffer;
int values[4];

int &pick() {
    static int value = counter;
    return value;
}

int *make() {
    return buffer;
}

class Base {};
class Child : public Base {
public:
    int ok();
};
'''
    path = tmp_path / "symbols.cpp"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)
    var_names = {var.name for var in mod.variables}
    func_names = {func.qualified_name for func in mod.functions}
    child = next(cls for cls in mod.classes if cls.name == "Child")

    assert {"counter", "buffer", "values"} <= var_names
    assert "symbols.pick" in func_names
    assert "symbols.make" in func_names
    assert child.bases == ["Base"]
    assert "ok" in {method.name for method in child.methods}


def test_cpp_resolve_call_returns_none_for_unsupported_call_targets(tmp_path):
    plugin = CppPlugin()
    source = b"int maker(); int f(){ return (maker())(); }"
    root = _get_cpp_parser().parse(source).root_node
    outer_call = _find_node(root, "call_expression")

    assert plugin._resolve_call(outer_call, source) is None


def test_cpp_internal_defensive_branches_cover_missing_fields_and_base_specifiers():
    plugin = CppPlugin()
    source = b"Base Child"

    reference_without_function = _fake_node(
        "function_definition",
        fields={"declarator": _fake_node("reference_declarator")},
    )
    nameless_function = _fake_node(
        "function_definition",
        fields={"declarator": _fake_node("function_declarator")},
    )
    base_spec = _fake_node(
        "base_class_specifier",
        fields={"type": _fake_node("type_identifier", start_byte=0, end_byte=4)},
    )
    fake_class = _fake_node(
        "class_specifier",
        children=[
            _fake_node("identifier", start_byte=5, end_byte=10),
            _fake_node("base_class_clause", children=[base_spec]),
            _fake_node("class_body"),
        ],
        fields={
            "name": _fake_node("identifier", start_byte=5, end_byte=10),
            "body": _fake_node("class_body"),
        },
    )
    nameless_method_decl = _fake_node(
        "declaration",
        fields={"declarator": _fake_node("function_declarator")},
    )

    assert plugin._parse_function(reference_without_function, b"", "tmp.cpp", "tmp", None) is None
    assert plugin._parse_function(nameless_function, b"", "tmp.cpp", "tmp", None) is None
    assert plugin._parse_class(fake_class, source, "tmp.cpp", "tmp", []).bases == ["Base"]
    assert plugin._parse_method_declaration(nameless_method_decl, b"", "tmp.cpp", "tmp", "Widget") is None
    assert plugin._extract_declarator_name(_fake_node("pointer_declarator"), b"") is None


def _find_node(root, node_type: str):
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == node_type:
            return node
        stack.extend(reversed(node.children))
    raise AssertionError(f"Could not find node type {node_type!r}")


def _fake_node(node_type: str, *, children=None, fields=None, start_byte: int = 0, end_byte: int = 0):
    class FakePoint:
        row = 0

    class FakeNode:
        def __init__(self):
            self.type = node_type
            self.children = children or []
            self._fields = fields or {}
            self.start_point = FakePoint()
            self.end_point = FakePoint()
            self.start_byte = start_byte
            self.end_byte = end_byte

        def child_by_field_name(self, name: str):
            return self._fields.get(name)

    return FakeNode()
