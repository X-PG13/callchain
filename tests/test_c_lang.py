"""Tests for the C language plugin."""

from callchain.languages.c_lang import CPlugin, _get_c_parser, _is_valid_c_callee


def test_discover_files(c_fixtures):
    plugin = CPlugin()
    files = plugin.discover_files(c_fixtures)
    names = {f.name for f in files}
    assert "math.c" in names
    assert "advanced.c" in names


def test_parse_functions(c_fixtures):
    plugin = CPlugin()
    mod = plugin.parse_file(c_fixtures / "math.c", c_fixtures)
    func_names = {f.name for f in mod.functions}
    assert "add" in func_names
    assert "multiply" in func_names
    assert "main" in func_names
    assert "print_result" in func_names
    assert "helper" in func_names


def test_parse_struct(c_fixtures):
    plugin = CPlugin()
    mod = plugin.parse_file(c_fixtures / "math.c", c_fixtures)
    class_names = {c.name for c in mod.classes}
    assert "Point" in class_names


def test_parse_includes(c_fixtures):
    plugin = CPlugin()
    mod = plugin.parse_file(c_fixtures / "math.c", c_fixtures)
    modules = {i.module for i in mod.imports}
    assert "stdio.h" in modules
    assert "math.h" in modules


def test_extract_calls(c_fixtures):
    plugin = CPlugin()
    edges = plugin.extract_calls(c_fixtures / "math.c", c_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}
    assert any("add" in n for n in callee_names)
    assert any("multiply" in n for n in callee_names)
    assert any("print_result" in n for n in callee_names)
    assert any("printf" in n for n in callee_names)


def test_static_function(c_fixtures):
    plugin = CPlugin()
    mod = plugin.parse_file(c_fixtures / "math.c", c_fixtures)
    helper = next(f for f in mod.functions if f.name == "helper")
    assert helper.is_static


def test_complexity(c_fixtures):
    plugin = CPlugin()
    mod = plugin.parse_file(c_fixtures / "math.c", c_fixtures)
    multiply = next(f for f in mod.functions if f.name == "multiply")
    assert multiply.complexity > 1  # has a for loop


def test_parse_additional_c_constructs(c_fixtures):
    plugin = CPlugin()
    mod = plugin.parse_file(c_fixtures / "advanced.c", c_fixtures)

    func_names = {f.name for f in mod.functions}
    class_names = {c.name for c in mod.classes}
    var_names = {v.name for v in mod.variables}

    assert "identity_ptr" in func_names
    assert "check_flags" in func_names
    assert "drive" in func_names
    assert "Node" in class_names
    assert "GLOBAL_LIMIT" in var_names

    check_flags = next(f for f in mod.functions if f.name == "check_flags")
    assert check_flags.complexity > 2


def test_extract_calls_from_additional_c_fixture(c_fixtures):
    plugin = CPlugin()
    edges = plugin.extract_calls(c_fixtures / "advanced.c", c_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}

    assert "identity_ptr" in callee_names
    assert "check_flags" in callee_names


def test_callee_validation_rejects_complex_c_expressions():
    assert _is_valid_c_callee("printf")
    assert not _is_valid_c_callee("")
    assert not _is_valid_c_callee("fn(arg)")
    assert not _is_valid_c_callee("fn*ptr")


def test_c_parses_uninitialized_pointer_and_array_variables(tmp_path):
    plugin = CPlugin()
    source = b"""
int VALUE;
int *PTR;
int items[4];
"""
    path = tmp_path / "vars.c"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)

    assert {var.name for var in mod.variables} == {"VALUE", "PTR", "items"}


def test_c_internal_helpers_handle_invalid_nodes_and_missing_names(tmp_path):
    plugin = CPlugin()
    root = _get_c_parser().parse(b"int value = 1;").root_node
    anon_struct_root = _get_c_parser().parse(b"struct { int value; };").root_node
    typedef_root = _get_c_parser().parse(b"typedef struct { int value; };").root_node
    typedef_node = _find_node(typedef_root, "type_definition")
    anon_struct = _find_node(anon_struct_root, "struct_specifier")

    assert plugin._parse_function(root, b"", "tmp.c", "tmp") is None
    assert plugin._parse_include(root, b"", "tmp.c") is None
    assert plugin._parse_variable(root, b"", "tmp.c") is None
    assert plugin._parse_struct(anon_struct, b"struct { int value; };", "tmp.c", "tmp") is None
    assert plugin._parse_struct_typedef(typedef_node, b"typedef struct { int value; };", "tmp.c", "tmp") is None
    assert plugin._extract_declarator_name(root, b"") is None


def test_c_internal_defensive_branches_cover_missing_fields():
    plugin = CPlugin()

    name_source = b"name"
    missing_alias_typedef = _fake_node("type_definition")
    bad_function_decl = _fake_node(
        "function_definition",
        fields={"declarator": _fake_node("array_declarator")},
    )
    nameless_function = _fake_node(
        "function_definition",
        fields={"declarator": _fake_node("function_declarator")},
    )
    bad_variable = _fake_node(
        "declaration",
        fields={"declarator": _fake_node("function_declarator")},
    )
    broken_pointer = _fake_node("pointer_declarator")

    assert plugin._parse_struct_typedef(missing_alias_typedef, b"", "tmp.c", "tmp") is None
    assert plugin._parse_function(bad_function_decl, b"", "tmp.c", "tmp") is None
    assert plugin._parse_function(nameless_function, b"", "tmp.c", "tmp") is None
    assert plugin._parse_variable(bad_variable, b"", "tmp.c") is None
    assert plugin._extract_declarator_name(broken_pointer, name_source) is None


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
