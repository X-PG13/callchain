"""Python language plugin using tree-sitter."""

from __future__ import annotations

from pathlib import Path

import tree_sitter
import tree_sitter_python as tspython

from callchain.core.models import (
    CallEdge,
    ClassInfo,
    FunctionInfo,
    ImportInfo,
    Language,
    ModuleInfo,
    VariableInfo,
)
from callchain.languages.base import LanguagePlugin

_parser: tree_sitter.Parser | None = None


def _get_parser() -> tree_sitter.Parser:
    global _parser
    if _parser is None:
        _parser = tree_sitter.Parser(tree_sitter.Language(tspython.language()))
    return _parser


def _module_from_path(rel_path: str) -> str:
    """Convert relative file path to Python module name."""
    s = rel_path.replace("/", ".").replace("\\", ".")
    if s.endswith(".py"):
        s = s[:-3]
    if s.endswith(".__init__"):
        s = s[:-9]
    return s


class PythonPlugin(LanguagePlugin):
    language = Language.PYTHON
    extensions = (".py",)

    def parse_file(self, file_path: Path, project_root: Path) -> ModuleInfo:
        source = self._read_file(file_path)
        tree = _get_parser().parse(source)
        rel = self._rel_path(file_path, project_root)
        module_name = _module_from_path(rel)

        functions: list[FunctionInfo] = []
        classes: list[ClassInfo] = []
        imports: list[ImportInfo] = []
        variables: list[VariableInfo] = []

        self._walk_module(tree.root_node, source, rel, module_name, functions, classes, imports, variables)

        return ModuleInfo(
            file_path=rel,
            language=Language.PYTHON,
            functions=functions,
            classes=classes,
            imports=imports,
            variables=variables,
        )

    def extract_calls(self, file_path: Path, project_root: Path) -> list[CallEdge]:
        source = self._read_file(file_path)
        tree = _get_parser().parse(source)
        rel = self._rel_path(file_path, project_root)
        module_name = _module_from_path(rel)

        edges: list[CallEdge] = []
        self._extract_calls_from_node(tree.root_node, source, rel, module_name, None, edges)
        return edges

    # ── Internal parsing ─────────────────────────────────────────

    def _walk_module(
        self,
        root: tree_sitter.Node,
        source: bytes,
        rel_path: str,
        module_name: str,
        functions: list[FunctionInfo],
        classes: list[ClassInfo],
        imports: list[ImportInfo],
        variables: list[VariableInfo],
    ) -> None:
        for child in root.children:
            actual = self._unwrap_definition(child)

            if actual and actual.type in ("function_definition", "async_function_definition"):
                func = self._parse_function(child, source, rel_path, module_name, class_name=None)
                if func:
                    functions.append(func)

            elif actual and actual.type == "class_definition":
                cls = self._parse_class(child, source, rel_path, module_name)
                if cls:
                    classes.append(cls)

            elif child.type in ("import_statement", "import_from_statement"):
                imp = self._parse_import(child, source, rel_path)
                if imp:
                    imports.append(imp)

            elif child.type in ("expression_statement",):
                # Module-level assignments
                assign = child.children[0] if child.children else None
                if assign and assign.type == "assignment":
                    var = self._parse_variable(assign, source, rel_path)
                    if var:
                        variables.append(var)

    def _unwrap_definition(self, node: tree_sitter.Node) -> tree_sitter.Node | None:
        if node.type != "decorated_definition":
            return node
        for child in node.children:
            if child.type in ("function_definition", "async_function_definition", "class_definition"):
                return child
        return None

    def _parse_function(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel_path: str,
        module_name: str,
        class_name: str | None,
    ) -> FunctionInfo | None:
        decorators: list[str] = []
        func_node = node

        if node.type == "decorated_definition":
            for child in node.children:
                if child.type == "decorator":
                    decorators.append(self._node_text(child, source).lstrip("@").strip())
                elif child.type in ("function_definition", "async_function_definition"):
                    func_node = child
                    break
            else:
                return None

        is_async = func_node.type == "async_function_definition"
        name_node = func_node.child_by_field_name("name")
        if not name_node:
            return None
        name = self._node_text(name_node, source)

        params_node = func_node.child_by_field_name("parameters")
        params_text = self._node_text(params_node, source) if params_node else "()"

        return_node = func_node.child_by_field_name("return_type")
        return_text = f" -> {self._node_text(return_node, source)}" if return_node else ""

        signature = f"{name}{params_text}{return_text}"
        qualified = f"{module_name}.{class_name}.{name}" if class_name else f"{module_name}.{name}"

        docstring = self._extract_docstring(func_node, source)
        complexity = self._compute_complexity(func_node)

        return FunctionInfo(
            name=name,
            qualified_name=qualified,
            file_path=rel_path,
            line=func_node.start_point.row + 1,
            end_line=func_node.end_point.row + 1,
            signature=signature,
            docstring=docstring,
            is_method=class_name is not None,
            is_async=is_async,
            class_name=class_name,
            decorators=decorators,
            language=Language.PYTHON,
            complexity=complexity,
        )

    def _parse_class(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel_path: str,
        module_name: str,
    ) -> ClassInfo | None:
        actual = node
        decorators: list[str] = []

        if node.type == "decorated_definition":
            for child in node.children:
                if child.type == "decorator":
                    decorators.append(self._node_text(child, source).lstrip("@").strip())
                elif child.type == "class_definition":
                    actual = child
                    break
            else:
                return None

        name_node = actual.child_by_field_name("name")
        if not name_node:
            return None
        name = self._node_text(name_node, source)

        bases: list[str] = []
        superclasses = actual.child_by_field_name("superclasses")
        if superclasses:
            for child in superclasses.children:
                if child.type not in ("(", ")", ","):
                    bases.append(self._node_text(child, source))

        body = actual.child_by_field_name("body")
        methods: list[FunctionInfo] = []
        if body:
            for child in body.children:
                if child.type in ("function_definition", "decorated_definition", "async_function_definition"):
                    func = self._parse_function(child, source, rel_path, module_name, class_name=name)
                    if func:
                        methods.append(func)

        docstring = self._extract_docstring(actual, source)

        return ClassInfo(
            name=name,
            qualified_name=f"{module_name}.{name}",
            file_path=rel_path,
            line=actual.start_point.row + 1,
            end_line=actual.end_point.row + 1,
            bases=bases,
            methods=methods,
            docstring=docstring,
            decorators=decorators,
            language=Language.PYTHON,
        )

    def _parse_import(self, node: tree_sitter.Node, source: bytes, rel_path: str) -> ImportInfo | None:
        text = self._node_text(node, source)
        if node.type == "import_statement":
            # import foo, import foo as bar
            names: list[str] = []
            alias = None
            for child in node.children:
                if child.type == "dotted_name":
                    names.append(self._node_text(child, source))
                elif child.type == "aliased_import":
                    name_n = child.child_by_field_name("name")
                    alias_n = child.child_by_field_name("alias")
                    if name_n:
                        names.append(self._node_text(name_n, source))
                    if alias_n:
                        alias = self._node_text(alias_n, source)
            module = names[0] if names else text
            return ImportInfo(
                module=module, names=[], alias=alias, is_from_import=False,
                file_path=rel_path, line=node.start_point.row + 1,
            )

        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            module = self._node_text(module_node, source) if module_node else ""
            imported: list[str] = []
            for child in node.children:
                if child.type == "dotted_name" and child != module_node:
                    imported.append(self._node_text(child, source))
                elif child.type == "aliased_import":
                    name_n = child.child_by_field_name("name")
                    if name_n:
                        imported.append(self._node_text(name_n, source))
                elif child.type == "wildcard_import":
                    imported.append("*")
            return ImportInfo(
                module=module, names=imported, is_from_import=True,
                file_path=rel_path, line=node.start_point.row + 1,
            )
        return None

    def _parse_variable(self, node: tree_sitter.Node, source: bytes, rel_path: str) -> VariableInfo | None:
        left = node.child_by_field_name("left")
        if not left or left.type != "identifier":
            return None
        name = self._node_text(left, source)
        type_node = node.child_by_field_name("type")
        type_ann = self._node_text(type_node, source) if type_node else None
        return VariableInfo(
            name=name, file_path=rel_path,
            line=node.start_point.row + 1, type_annotation=type_ann,
        )

    def _extract_docstring(self, node: tree_sitter.Node, source: bytes) -> str | None:
        body = node.child_by_field_name("body")
        if not body or not body.children:
            return None
        first = body.children[0]
        if first.type == "expression_statement" and first.children:
            expr = first.children[0]
            if expr.type == "string":
                raw = self._node_text(expr, source)
                return raw.strip("'\"").strip()
        return None

    def _compute_complexity(self, node: tree_sitter.Node) -> int:
        """Compute cyclomatic complexity for a function node."""
        complexity = 1
        branch_types = {"if_statement", "elif_clause", "for_statement", "while_statement",
                        "except_clause", "with_statement", "assert_statement",
                        "boolean_operator", "conditional_expression"}
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in branch_types:
                complexity += 1
            stack.extend(n.children)
        return complexity

    # ── Call extraction ──────────────────────────────────────────

    def _extract_calls_from_node(
        self,
        root: tree_sitter.Node,
        source: bytes,
        rel_path: str,
        module_name: str,
        enclosing: FunctionInfo | None,
        edges: list[CallEdge],
    ) -> None:
        for child in root.children:
            actual = self._unwrap_definition(child)

            if actual and actual.type in ("function_definition", "async_function_definition"):
                func_info = self._parse_function(child, source, rel_path, module_name, class_name=None)
                if func_info:
                    self._extract_calls_from_node(actual, source, rel_path, module_name, func_info, edges)
                continue

            elif actual and actual.type == "class_definition":
                name_node = actual.child_by_field_name("name")
                cls_name = self._node_text(name_node, source) if name_node else ""
                body = actual.child_by_field_name("body")
                if body:
                    for member in body.children:
                        actual_member = self._unwrap_definition(member)
                        if actual_member and actual_member.type in ("function_definition", "async_function_definition"):
                            method_info = self._parse_function(member, source, rel_path, module_name, class_name=cls_name)
                            if method_info:
                                self._extract_calls_from_node(actual_member, source, rel_path, module_name, method_info, edges)
                continue

            if child.type == "call" and enclosing:
                callee_name = self._resolve_call_name(child, source)
                if callee_name:
                    callee = FunctionInfo(
                        name=callee_name.split(".")[-1],
                        qualified_name=callee_name,
                        file_path="",  # resolved later
                        line=0,
                        language=Language.PYTHON,
                    )
                    edges.append(CallEdge(
                        caller=enclosing,
                        callee=callee,
                        call_site_line=child.start_point.row + 1,
                        call_site_file=rel_path,
                    ))

            self._extract_calls_from_node(child, source, rel_path, module_name, enclosing, edges)

    def _resolve_call_name(self, call_node: tree_sitter.Node, source: bytes) -> str | None:
        func = call_node.child_by_field_name("function")
        if not func:
            return None
        if func.type == "identifier":
            return self._node_text(func, source)
        elif func.type == "attribute":
            return self._node_text(func, source)
        return None
