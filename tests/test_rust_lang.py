"""Tests for the Rust language plugin."""

from callchain.languages.rust_lang import RustPlugin, _get_parser, _is_valid_rust_callee, _module_from_path


def test_discover_files(rust_fixtures):
    plugin = RustPlugin()
    files = plugin.discover_files(rust_fixtures)
    names = {f.name for f in files}
    assert "main.rs" in names


def test_parse_functions(rust_fixtures):
    plugin = RustPlugin()
    mod = plugin.parse_file(rust_fixtures / "main.rs", rust_fixtures)
    func_names = {f.name for f in mod.functions}
    assert "increment" in func_names
    assert "main" in func_names
    assert "new" in func_names
    assert "add" in func_names


def test_parse_structs(rust_fixtures):
    plugin = RustPlugin()
    mod = plugin.parse_file(rust_fixtures / "main.rs", rust_fixtures)
    class_names = {c.name for c in mod.classes}
    assert "Calculator" in class_names


def test_impl_methods(rust_fixtures):
    plugin = RustPlugin()
    mod = plugin.parse_file(rust_fixtures / "main.rs", rust_fixtures)
    add_fn = next(f for f in mod.functions if f.name == "add")
    assert add_fn.is_method
    assert add_fn.class_name == "Calculator"
    new_fn = next(f for f in mod.functions if f.name == "new")
    assert new_fn.class_name == "Calculator"
    assert new_fn.is_static  # no self param


def test_extract_calls(rust_fixtures):
    plugin = RustPlugin()
    edges = plugin.extract_calls(rust_fixtures / "main.rs", rust_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}
    assert any("increment" in n for n in callee_names)


def test_rust_module_names_and_internal_helpers():
    plugin = RustPlugin()
    parser = _get_parser()
    root = parser.parse(b"mod inner;\n").root_node
    impl_without_body = parser.parse(b"impl Foo").root_node.children[0]

    assert _module_from_path("pkg/mod.rs") == "pkg"
    assert _module_from_path("pkg/main.rs") == "pkg.main"
    assert _is_valid_rust_callee("std::mem::take")
    assert not _is_valid_rust_callee("")
    assert not _is_valid_rust_callee("closure|x|")
    assert not _is_valid_rust_callee("bad{call}")
    assert plugin._parse_function(root, b"", "main.rs", "main", None) is None
    assert plugin._parse_struct_or_enum(root, b"", "main.rs", "main") is None
    assert plugin._parse_trait(root, b"", "main.rs", "main") is None
    assert plugin._parse_impl(impl_without_body, b"impl Foo", "main.rs", "main", []) is None


def test_parse_rust_traits_imports_macros_and_complexity(tmp_path):
    plugin = RustPlugin()
    source = b'''
use std::fmt::Debug;

trait Render {
    fn render(&self);
}

enum Mode {
    Fast,
    Slow,
}

impl Render for Mode {
    fn render(&self) {
        println!("{:?}", helper());
    }
}

fn helper() -> i32 {
    1
}

fn choose(flag: bool) -> i32 {
    if flag {
        helper()
    } else {
        helper()
    }
}
'''
    path = tmp_path / "advanced.rs"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)
    classes = {cls.name: cls for cls in mod.classes}
    functions = {func.name: func for func in mod.functions}
    imports = {imp.module for imp in mod.imports}
    edges = plugin.extract_calls(path, tmp_path)
    callees = {edge.callee.qualified_name for edge in edges}

    assert "std::fmt::Debug" in imports
    assert "Render" in classes
    assert "Mode" in classes
    assert "render" in {method.name for method in classes["Render"].methods}
    assert functions["choose"].complexity >= 3
    assert "println!" in callees
    assert "helper" in callees
