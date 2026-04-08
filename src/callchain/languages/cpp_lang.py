"""C++ language plugin using tree-sitter."""

from __future__ import annotations

from pathlib import Path

import tree_sitter
import tree_sitter_cpp as tscpp

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

_cpp_parser: tree_sitter.Parser | None = None


def _get_cpp_parser() -> tree_sitter.Parser:
    global _cpp_parser
    if _cpp_parser is None:
        _cpp_parser = tree_sitter.Parser(tree_sitter.Language(tscpp.language()))
    return _cpp_parser


def _module_from_path(rel_path: str) -> str:
    s = rel_path.replace("/", ".").replace("\\", ".")
    for ext in (".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".h"):
        if s.endswith(ext):
            s = s[: -len(ext)]
            break
    return s


class CppPlugin(LanguagePlugin):
    language = Language.CPP
    extensions = (".cpp", ".cc", ".cxx", ".hpp", ".hxx")

    def parse_file(self, file_path: Path, project_root: Path) -> ModuleInfo:
        source = self._read_file(file_path)
        parser = _get_cpp_parser()
        tree = parser.parse(source)
        rel = self._rel_path(file_path, project_root)
        module = _module_from_path(rel)

        functions: list[FunctionInfo] = []
        classes: list[ClassInfo] = []
        imports: list[ImportInfo] = []
        variables: list[VariableInfo] = []

        self._walk(tree.root_node, source, rel, module, functions, classes, imports, variables, None)

        return ModuleInfo(
            file_path=rel,
            language=self.language,
            functions=functions,
            classes=classes,
            imports=imports,
            variables=variables,
        )

    def extract_calls(self, file_path: Path, project_root: Path) -> list[CallEdge]:
        source = self._read_file(file_path)
        parser = _get_cpp_parser()
        tree = parser.parse(source)
        rel = self._rel_path(file_path, project_root)
        module = _module_from_path(rel)

        edges: list[CallEdge] = []
        self._extract_calls_recursive(tree.root_node, source, rel, module, None, None, edges)
        return edges

    # ── Parsing helpers ──────────────────────────────────────────

    def _walk(
        self,
        root: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        functions: list[FunctionInfo],
        classes: list[ClassInfo],
        imports: list[ImportInfo],
        variables: list[VariableInfo],
        current_class: str | None,
    ) -> None:
        for child in root.children:
            if child.type == "function_definition":
                f = self._parse_function(child, source, rel, module, current_class)
                if f:
                    functions.append(f)

            elif child.type == "class_specifier":
                c = self._parse_class(child, source, rel, module, functions)
                if c:
                    classes.append(c)

            elif child.type == "struct_specifier":
                c = self._parse_class(child, source, rel, module, functions)
                if c:
                    classes.append(c)

            elif child.type == "namespace_definition":
                body = child.child_by_field_name("body")
                if body:
                    ns_name = child.child_by_field_name("name")
                    ns_module = f"{module}.{self._node_text(ns_name, source)}" if ns_name else module
                    self._walk(body, source, rel, ns_module, functions, classes, imports, variables, current_class)

            elif child.type == "template_declaration":
                # Walk into template declarations to find the function/class inside
                for sub in child.children:
                    if sub.type == "function_definition":
                        f = self._parse_function(sub, source, rel, module, current_class)
                        if f:
                            functions.append(f)
                    elif sub.type in ("class_specifier", "struct_specifier"):
                        c = self._parse_class(sub, source, rel, module, functions)
                        if c:
                            classes.append(c)

            elif child.type == "preproc_include":
                imp = self._parse_include(child, source, rel)
                if imp:
                    imports.append(imp)

            elif child.type == "declaration":
                v = self._parse_variable(child, source, rel)
                if v:
                    variables.append(v)

    def _parse_function(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        class_name: str | None,
    ) -> FunctionInfo | None:
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return None

        func_decl = declarator
        # Unwrap reference/pointer declarators
        while func_decl.type in ("reference_declarator", "pointer_declarator"):
            for c in func_decl.children:
                if c.type == "function_declarator":
                    func_decl = c
                    break
            else:
                break

        if func_decl.type != "function_declarator":
            return None

        name_node = func_decl.child_by_field_name("declarator")
        if not name_node:
            return None

        # Handle qualified names like ClassName::method
        name_text = self._node_text(name_node, source)
        if "::" in name_text:
            parts = name_text.rsplit("::", 1)
            class_name = parts[0]
            name = parts[1]
        else:
            name = name_text

        params_node = func_decl.child_by_field_name("parameters")
        params = self._node_text(params_node, source) if params_node else "()"

        type_node = node.child_by_field_name("type")
        ret_type = self._node_text(type_node, source) if type_node else ""

        is_static = any(
            c.type == "storage_class_specifier" and self._node_text(c, source) == "static"
            for c in node.children
        )

        qualified = f"{module}.{class_name}.{name}" if class_name else f"{module}.{name}"

        return FunctionInfo(
            name=name,
            qualified_name=qualified,
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=f"{ret_type} {name}{params}",
            is_method=class_name is not None,
            is_static=is_static,
            class_name=class_name,
            language=self.language,
            complexity=self._compute_complexity(node),
        )

    def _parse_class(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        all_functions: list[FunctionInfo],
    ) -> ClassInfo | None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = self._node_text(name_node, source)

        bases: list[str] = []
        for child in node.children:
            if child.type == "base_class_clause":
                for sub in child.children:
                    if sub.type == "type_identifier":
                        bases.append(self._node_text(sub, source))
                    elif sub.type == "base_class_specifier":
                        type_node = sub.child_by_field_name("type") or sub.child_by_field_name("name")
                        if type_node:
                            bases.append(self._node_text(type_node, source))

        body = node.child_by_field_name("body")
        methods: list[FunctionInfo] = []
        if body:
            for child in body.children:
                if child.type == "function_definition":
                    f = self._parse_function(child, source, rel, module, class_name=name)
                    if f:
                        methods.append(f)
                elif child.type in ("declaration", "field_declaration"):
                    # Inline method declarations like: int add(int a, int b);
                    # We detect function declarators inside declarations
                    f = self._parse_method_declaration(child, source, rel, module, name)
                    if f:
                        methods.append(f)
                elif child.type == "template_declaration":
                    for sub in child.children:
                        if sub.type == "function_definition":
                            f = self._parse_function(sub, source, rel, module, class_name=name)
                            if f:
                                methods.append(f)
                elif child.type in ("access_specifier", "comment", "{", "}", ";"):
                    continue

        return ClassInfo(
            name=name,
            qualified_name=f"{module}.{name}",
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            bases=bases,
            methods=methods,
            language=self.language,
        )

    def _parse_method_declaration(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        class_name: str,
    ) -> FunctionInfo | None:
        """Parse method declarations (not definitions) inside class body."""
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return None
        func_decl = declarator
        if func_decl.type != "function_declarator":
            return None
        name_node = func_decl.child_by_field_name("declarator")
        if not name_node:
            return None
        name = self._node_text(name_node, source)
        params_node = func_decl.child_by_field_name("parameters")
        params = self._node_text(params_node, source) if params_node else "()"

        type_node = node.child_by_field_name("type")
        ret_type = self._node_text(type_node, source) if type_node else ""

        is_static = any(
            c.type == "storage_class_specifier" and self._node_text(c, source) == "static"
            for c in node.children
        )

        return FunctionInfo(
            name=name,
            qualified_name=f"{module}.{class_name}.{name}",
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=f"{ret_type} {name}{params}",
            is_method=True,
            is_static=is_static,
            class_name=class_name,
            language=self.language,
            complexity=1,
        )

    def _parse_include(self, node: tree_sitter.Node, source: bytes, rel: str) -> ImportInfo | None:
        for child in node.children:
            if child.type == "system_lib_string":
                mod = self._node_text(child, source).strip("<>")
                return ImportInfo(module=mod, file_path=rel, line=node.start_point.row + 1)
            elif child.type == "string_literal":
                mod = self._node_text(child, source).strip('"')
                return ImportInfo(module=mod, file_path=rel, line=node.start_point.row + 1)
        return None

    def _parse_variable(self, node: tree_sitter.Node, source: bytes, rel: str) -> VariableInfo | None:
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return None

        if declarator.type == "init_declarator":
            declarator = declarator.child_by_field_name("declarator") or declarator

        name = self._extract_declarator_name(declarator, source)
        if not name:
            return None

        return VariableInfo(
            name=name,
            file_path=rel,
            line=node.start_point.row + 1,
        )

    def _extract_declarator_name(self, node: tree_sitter.Node, source: bytes) -> str | None:
        current = node
        while current.type in (
            "pointer_declarator",
            "reference_declarator",
            "array_declarator",
            "parenthesized_declarator",
        ):
            next_node = current.child_by_field_name("declarator")
            if not next_node:
                return None
            current = next_node

        if current.type == "identifier":
            return self._node_text(current, source)
        return None

    def _compute_complexity(self, node: tree_sitter.Node) -> int:
        complexity = 1
        branch_types = {
            "if_statement", "else_clause", "for_statement", "for_range_loop",
            "while_statement", "do_statement", "case_statement", "catch_clause",
            "conditional_expression",
        }
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in branch_types:
                complexity += 1
            elif n.type == "binary_expression":
                for child in n.children:
                    if child.type in ("&&", "||"):
                        complexity += 1
                        break
            stack.extend(n.children)
        return complexity

    # ── Call extraction ──────────────────────────────────────────

    def _extract_calls_recursive(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        enclosing: FunctionInfo | None,
        class_name: str | None,
        edges: list[CallEdge],
    ) -> None:
        if node.type == "function_definition":
            f = self._parse_function(node, source, rel, module, class_name)
            if f:
                enclosing = f

        if node.type in ("class_specifier", "struct_specifier"):
            name_node = node.child_by_field_name("name")
            if name_node:
                class_name = self._node_text(name_node, source)

        if node.type == "namespace_definition":
            body = node.child_by_field_name("body")
            ns_name = node.child_by_field_name("name")
            ns_module = f"{module}.{self._node_text(ns_name, source)}" if ns_name else module
            if body:
                for child in body.children:
                    self._extract_calls_recursive(child, source, rel, ns_module, enclosing, class_name, edges)
            return

        if node.type == "call_expression" and enclosing:
            callee_name = self._resolve_call(node, source)
            if callee_name and _is_valid_cpp_callee(callee_name):
                callee = FunctionInfo(
                    name=callee_name.split(".")[-1],
                    qualified_name=callee_name,
                    file_path="",
                    line=0,
                    language=self.language,
                )
                edges.append(CallEdge(
                    caller=enclosing,
                    callee=callee,
                    call_site_line=node.start_point.row + 1,
                    call_site_file=rel,
                ))

        for child in node.children:
            self._extract_calls_recursive(child, source, rel, module, enclosing, class_name, edges)

    def _resolve_call(self, node: tree_sitter.Node, source: bytes) -> str | None:
        func = node.child_by_field_name("function")
        if not func:
            return None
        if func.type == "identifier":
            return self._node_text(func, source)
        elif func.type == "field_expression":
            return self._node_text(func, source).replace("->", ".").replace("::", ".")
        elif func.type == "qualified_identifier":
            return self._node_text(func, source).replace("::", ".")
        elif func.type == "template_function":
            name_node = func.child_by_field_name("name")
            if name_node:
                return self._node_text(name_node, source)
        return None


def _is_valid_cpp_callee(name: str) -> bool:
    if not name or "\n" in name or "{" in name:
        return False
    if "(" in name or ")" in name:
        return False
    return True
