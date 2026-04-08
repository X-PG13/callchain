"""Tests for the Java language plugin."""

from callchain.languages.java_lang import JavaPlugin, _get_parser, _module_from_path


def test_discover_files(java_fixtures):
    plugin = JavaPlugin()
    files = plugin.discover_files(java_fixtures)
    names = {f.name for f in files}
    assert "Calculator.java" in names


def test_parse_classes(java_fixtures):
    plugin = JavaPlugin()
    mod = plugin.parse_file(java_fixtures / "Calculator.java", java_fixtures)
    assert len(mod.classes) == 1
    cls = mod.classes[0]
    assert cls.name == "Calculator"
    method_names = {m.name for m in cls.methods}
    assert "add" in method_names
    assert "subtract" in method_names
    assert "main" in method_names


def test_parse_imports(java_fixtures):
    plugin = JavaPlugin()
    mod = plugin.parse_file(java_fixtures / "Calculator.java", java_fixtures)
    assert any("java.util.List" in i.module for i in mod.imports)


def test_extract_calls(java_fixtures):
    plugin = JavaPlugin()
    edges = plugin.extract_calls(java_fixtures / "Calculator.java", java_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}
    assert any("increment" in n for n in callee_names)
    assert any("println" in n for n in callee_names)


def test_method_reference(java_fixtures):
    """Java method references (::) should produce call edges."""
    plugin = JavaPlugin()
    edges = plugin.extract_calls(java_fixtures / "MathUtils.java", java_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}
    # MathUtils::increment should be captured
    assert any("increment" in n for n in callee_names)


def test_lambda_calls(java_fixtures):
    """Calls inside Java lambda bodies should be captured."""
    plugin = JavaPlugin()
    edges = plugin.extract_calls(java_fixtures / "MathUtils.java", java_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}
    assert any("println" in n for n in callee_names)
    assert any("forEach" in n for n in callee_names)


def test_java_module_names_and_internal_helpers():
    plugin = JavaPlugin()
    root = _get_parser().parse(b"class Example {}").root_node

    assert _module_from_path("pkg/Main.java") == "pkg.Main"
    assert plugin._parse_class(root, b"class Example {}", "Main.java", "Main") is None
    assert plugin._parse_method(root, b"class Example {}", "Main.java", "Main", None) is None
    assert plugin._parse_import(root, b"class Example {}", "Main.java").module == "class Example {}"
    assert plugin._collect_type_names(root, b"class Example {}") == []


def test_parse_java_interfaces_annotations_bases_and_complexity(tmp_path):
    plugin = JavaPlugin()
    source = b'''
import static java.lang.Math.max;

@Service
interface Worker extends Runnable, AutoCloseable {
    void run();

    @Deprecated
    default int work(boolean a, boolean b) {
        if (a && b || a) {
            return max(1, 2);
        }
        return helper();
    }
}

class Impl implements Worker, java.io.Closeable {
    public void run() {}
}

class Main {
    Main() {
        helper();
    }

    static int helper() {
        return 1;
    }
}
'''
    path = tmp_path / "Main.java"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)
    classes = {cls.name: cls for cls in mod.classes}
    imports = {imp.module for imp in mod.imports}
    edges = plugin.extract_calls(path, tmp_path)
    callees = {edge.callee.qualified_name for edge in edges}

    assert "java.lang.Math.max" in imports
    assert classes["Worker"].decorators == ["@Service"]
    assert classes["Worker"].bases == ["Runnable", "AutoCloseable"]
    assert "run" in {method.name for method in classes["Worker"].methods}
    work = next(method for method in classes["Worker"].methods if method.name == "work")
    assert work.decorators == ["@Deprecated"]
    assert work.complexity >= 4
    assert classes["Impl"].bases == ["Worker", "java.io.Closeable"]
    constructor = next(method for method in classes["Main"].methods if method.name == "Main")
    assert constructor.signature == "Main()"
    assert "max" in callees
    assert "helper" in callees


def test_java_walk_parses_top_level_methods_and_class_extends(tmp_path):
    plugin = JavaPlugin()
    source = b"""
class Child extends Base {}
void helper() {}
"""
    path = tmp_path / "TopLevel.java"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)

    assert next(cls for cls in mod.classes if cls.name == "Child").bases == ["Base"]
    assert "helper" in {func.name for func in mod.functions}


def test_java_parse_class_fallbacks_cover_direct_annotations_and_superclass_children():
    plugin = JavaPlugin()
    source = b"@Loose Child Base {}"

    class FakePoint:
        row = 0

    class FakeNode:
        def __init__(self, node_type: str, *, start_byte: int = 0, end_byte: int = 0, children=None, fields=None):
            self.type = node_type
            self.children = children or []
            self._fields = fields or {}
            self.start_point = FakePoint()
            self.end_point = FakePoint()
            self.start_byte = start_byte
            self.end_byte = end_byte

        def child_by_field_name(self, name: str):
            return self._fields.get(name)

    annotation = FakeNode("annotation", start_byte=0, end_byte=6)
    name_node = FakeNode("identifier", start_byte=7, end_byte=12)
    type_node = FakeNode("type_identifier", start_byte=13, end_byte=17)
    superclass = FakeNode("superclass", children=[type_node])
    body = FakeNode("class_body", start_byte=18, end_byte=20)
    fake_class = FakeNode(
        "class_declaration",
        children=[annotation, name_node, superclass, body],
        fields={"name": name_node, "body": body},
    )

    parsed = plugin._parse_class(fake_class, source, "Child.java", "pkg")

    assert parsed is not None
    assert parsed.bases == ["Base"]
    assert parsed.decorators == ["@Loose"]
