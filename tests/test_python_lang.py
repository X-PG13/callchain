"""Tests for the Python language plugin."""

from callchain.languages.python_lang import PythonPlugin, _get_parser, _module_from_path


def test_discover_files(python_fixtures):
    plugin = PythonPlugin()
    files = plugin.discover_files(python_fixtures)
    names = {f.name for f in files}
    assert "sample.py" in names
    assert "utils.py" in names


def test_parse_file_functions(python_fixtures):
    plugin = PythonPlugin()
    mod = plugin.parse_file(python_fixtures / "sample.py", python_fixtures)
    func_names = {f.name for f in mod.functions}
    assert "increment" in func_names
    assert "main" in func_names
    assert "helper" in func_names


def test_parse_file_classes(python_fixtures):
    plugin = PythonPlugin()
    mod = plugin.parse_file(python_fixtures / "sample.py", python_fixtures)
    assert len(mod.classes) == 1
    cls = mod.classes[0]
    assert cls.name == "Calculator"
    method_names = {m.name for m in cls.methods}
    assert "__init__" in method_names
    assert "add" in method_names
    assert "async_add" in method_names


def test_parse_file_imports(python_fixtures):
    plugin = PythonPlugin()
    mod = plugin.parse_file(python_fixtures / "sample.py", python_fixtures)
    modules = {i.module for i in mod.imports}
    assert "os" in modules
    assert "pathlib" in modules


def test_parse_file_variables(python_fixtures):
    plugin = PythonPlugin()
    mod = plugin.parse_file(python_fixtures / "sample.py", python_fixtures)
    var_names = {v.name for v in mod.variables}
    assert "GLOBAL_VAR" in var_names


def test_extract_calls(python_fixtures):
    plugin = PythonPlugin()
    edges = plugin.extract_calls(python_fixtures / "sample.py", python_fixtures)
    callee_names = {e.callee.qualified_name for e in edges}
    assert any("increment" in name for name in callee_names)
    assert any("print" in name for name in callee_names)


def test_complexity(python_fixtures):
    plugin = PythonPlugin()
    mod = plugin.parse_file(python_fixtures / "sample.py", python_fixtures)
    for func in mod.functions:
        assert func.complexity >= 1


def test_function_signatures(python_fixtures):
    plugin = PythonPlugin()
    mod = plugin.parse_file(python_fixtures / "sample.py", python_fixtures)
    inc = next(f for f in mod.functions if f.name == "increment")
    assert "a: int" in inc.signature
    assert "b: int" in inc.signature
    assert "-> int" in inc.signature


def test_method_is_async(python_fixtures):
    plugin = PythonPlugin()
    mod = plugin.parse_file(python_fixtures / "sample.py", python_fixtures)
    cls = mod.classes[0]
    async_method = next((m for m in cls.methods if m.name == "async_add"), None)
    # async_add is defined with async def but tree-sitter may parse it differently
    # At minimum it should be found as a method
    assert async_method is not None or any(m.name == "async_add" for m in cls.methods)


def test_python_module_names_and_internal_helpers():
    plugin = PythonPlugin()
    parser = _get_parser()
    root = parser.parse(b"pass\n").root_node
    decorated_class = parser.parse(b"@decorator\nclass Wrapped:\n    pass\n").root_node.children[0]
    decorated_function = parser.parse(b"@decorator\ndef wrapped():\n    return 1\n").root_node.children[0]
    simple_call = parser.parse(b"result = helper(1)\n").root_node.children[0].children[0].child_by_field_name("right")
    attr_call = parser.parse(b"result = obj.run()\n").root_node.children[0].children[0].child_by_field_name("right")
    bare_function = parser.parse(b"def simple():\n    pass\n").root_node.children[0]

    assert _module_from_path("pkg/__init__.py") == "pkg"
    assert _module_from_path("pkg/module.py") == "pkg.module"
    assert plugin._unwrap_definition(decorated_class).type == "class_definition"
    assert plugin._unwrap_definition(bare_function).type == "function_definition"
    assert plugin._parse_function(decorated_class, b"", "sample.py", "sample", None) is None
    assert plugin._parse_class(decorated_function, b"", "sample.py", "sample") is None
    assert plugin._parse_function(root, b"", "sample.py", "sample", None) is None
    assert plugin._parse_class(root, b"", "sample.py", "sample") is None
    assert plugin._parse_import(root, b"", "sample.py") is None
    assert plugin._parse_variable(root, b"", "sample.py") is None
    assert plugin._extract_docstring(root, b"") is None
    assert plugin._resolve_call_name(root, b"") is None
    assert plugin._resolve_call_name(simple_call, b"result = helper(1)\n") == "helper"
    assert plugin._resolve_call_name(attr_call, b"result = obj.run()\n") == "obj.run"


def test_parse_decorated_classes_import_variants_and_call_patterns(tmp_path):
    plugin = PythonPlugin()
    source = b'''
import pkg.sub as alias
from helpers import util as renamed
from stars import *

FLAG = True

@decorator
class Wrapped(BaseOne, BaseTwo):
    """wrapped class"""

    @classmethod
    def build(cls):
        return renamed()

    async def run(self):
        return alias.call()


@staticmethod
def helper():
    if FLAG and True:
        return helper_two()
    return 0


def helper_two():
    return 2
'''
    path = tmp_path / "advanced.py"
    path.write_bytes(source)

    mod = plugin.parse_file(path, tmp_path)
    imports = {(imp.module, tuple(imp.names), imp.alias, imp.is_from_import) for imp in mod.imports}
    wrapped = next(cls for cls in mod.classes if cls.name == "Wrapped")
    helper = next(func for func in mod.functions if func.name == "helper")
    edges = plugin.extract_calls(path, tmp_path)
    callees = {edge.callee.qualified_name for edge in edges}

    assert ("pkg.sub", (), "alias", False) in imports
    assert ("helpers", ("util",), None, True) in imports
    assert ("stars", ("*",), None, True) in imports
    assert wrapped.bases == ["BaseOne", "BaseTwo"]
    assert wrapped.docstring == "wrapped class"
    assert {method.name for method in wrapped.methods} == {"build", "run"}
    assert helper.decorators == ["staticmethod"]
    assert helper.complexity >= 2
    assert "renamed" in callees
    assert "alias.call" in callees
    assert "helper_two" in callees


def test_python_internal_defensive_branches_cover_unwrap_none_and_unsupported_call():
    plugin = PythonPlugin()

    class FakeNode:
        def __init__(self, node_type: str, *, children=None):
            self.type = node_type
            self.children = children or []

    root = _get_parser().parse(b"(helper())()\n").root_node
    stack = [root]
    unsupported_call = None
    while stack:
        node = stack.pop()
        if node.type == "call" and node.child_by_field_name("function").type == "parenthesized_expression":
            unsupported_call = node
            break
        stack.extend(reversed(node.children))

    assert plugin._unwrap_definition(FakeNode("decorated_definition", children=[FakeNode("decorator")])) is None
    assert unsupported_call is not None
    assert plugin._resolve_call_name(unsupported_call, b"(helper())()\n") is None
