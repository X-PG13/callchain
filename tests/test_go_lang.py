"""Tests for the Go language plugin."""

from callchain.languages.go_lang import GoPlugin, _get_parser, _is_valid_go_callee, _module_from_path


def test_discover_files(go_fixtures):
    plugin = GoPlugin()
    files = plugin.discover_files(go_fixtures)
    names = {f.name for f in files}
    assert "main.go" in names


def test_parse_functions(go_fixtures):
    plugin = GoPlugin()
    mod = plugin.parse_file(go_fixtures / "main.go", go_fixtures)
    func_names = {f.name for f in mod.functions}
    assert "NewCalculator" in func_names
    assert "Add" in func_names
    assert "Increment" in func_names
    assert "main" in func_names


def test_parse_structs(go_fixtures):
    plugin = GoPlugin()
    mod = plugin.parse_file(go_fixtures / "main.go", go_fixtures)
    class_names = {c.name for c in mod.classes}
    assert "Calculator" in class_names


def test_method_receiver(go_fixtures):
    plugin = GoPlugin()
    mod = plugin.parse_file(go_fixtures / "main.go", go_fixtures)
    add = next(f for f in mod.functions if f.name == "Add")
    assert add.is_method
    assert add.class_name == "Calculator"


def test_extract_calls(go_fixtures):
    plugin = GoPlugin()
    edges = plugin.extract_calls(go_fixtures / "main.go", go_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}
    assert any("Increment" in n for n in callee_names)
    assert any("Println" in n for n in callee_names)


def test_go_internal_helpers_cover_path_validation_and_invalid_nodes():
    plugin = GoPlugin()
    root = _get_parser().parse(b"package main\n").root_node

    assert _module_from_path(r"pkg\main.go") == "pkg.main"
    assert _is_valid_go_callee("fmt.Println")
    assert not _is_valid_go_callee("")
    assert not _is_valid_go_callee("func(value)")
    assert not _is_valid_go_callee("bad{\n")
    assert plugin._parse_function(root, b"", "sample.go", "sample") is None
    assert plugin._parse_method_decl(root, b"", "sample.go", "sample") is None
    assert plugin._parse_type_spec(root, b"", "sample.go", "sample") is None


def test_go_parses_block_imports_non_struct_types_and_func_literals(tmp_path):
    plugin = GoPlugin()
    source = b'''
package main

import (
    alias "fmt"
    "os"
)

type Count int
type Box struct{}

func helper() {}

func (b *Box) Run() {
    alias.Println(os.Args)
    func() {
        helper()
    }()
}
'''
    path = tmp_path / "extra.go"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)
    edges = plugin.extract_calls(path, tmp_path)
    imports = {(item.module, item.alias) for item in mod.imports}
    callee_names = {edge.callee.qualified_name for edge in edges}

    assert imports == {("fmt", "alias"), ("os", None)}
    assert "Box" in {cls.name for cls in mod.classes}
    assert "Count" not in {cls.name for cls in mod.classes}
    assert next(func for func in mod.functions if func.name == "Run").class_name == "Box"
    assert "alias.Println" in callee_names
    assert "helper" in callee_names
    assert not any(name.startswith("func") for name in callee_names)


def test_go_internal_import_literal_and_complexity_branch():
    plugin = GoPlugin()
    source = b'import "fmt"\nfunc main() { if true { fmt.Println("x") } }\n'
    root = _get_parser().parse(source).root_node
    import_spec = _find_node(root, "import_spec")
    func = _find_node(root, "function_declaration")
    imports = []

    plugin._parse_imports(import_spec, source, "main.go", imports)

    assert imports[0].module == "fmt"
    assert plugin._compute_complexity(func) > 1


def _find_node(root, node_type: str):
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == node_type:
            return node
        stack.extend(reversed(node.children))
    raise AssertionError(f"Could not find node type {node_type!r}")
