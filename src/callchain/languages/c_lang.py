"""C language plugin using tree-sitter."""

from __future__ import annotations

from pathlib import Path

import tree_sitter
import tree_sitter_c as tsc

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

_c_parser: tree_sitter.Parser | None = None


def _get_c_parser() -> tree_sitter.Parser:
    global _c_parser
    if _c_parser is None:
        _c_parser = tree_sitter.Parser(tree_sitter.Language(tsc.language()))
    return _c_parser


def _module_from_path(rel_path: str) -> str:
    s = rel_path.replace("/", ".").replace("\\", ".")
    for ext in (".c", ".h"):
        if s.endswith(ext):
            s = s[: -len(ext)]
            break
    return s


class CPlugin(LanguagePlugin):
    language = Language.C
    extensions = (".c", ".h")

    def parse_file(self, file_path: Path, project_root: Path) -> ModuleInfo:
        source = self._read_file(file_path)
        parser = _get_c_parser()
        tree = parser.parse(source)
        rel = self._rel_path(file_path, project_root)
        module = _module_from_path(rel)

        functions: list[FunctionInfo] = []
        classes: list[ClassInfo] = []
        imports: list[ImportInfo] = []
        variables: list[VariableInfo] = []

        self._walk(tree.root_node, source, rel, module, functions, classes, imports, variables)

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
        parser = _get_c_parser()
        tree = parser.parse(source)
        rel = self._rel_path(file_path, project_root)
        module = _module_from_path(rel)

        edges: list[CallEdge] = []
        self._extract_calls_recursive(tree.root_node, source, rel, module, None, edges)
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
    ) -> None:
        for child in root.children:
            if child.type == "function_definition":
                f = self._parse_function(child, source, rel, module)
                if f:
                    functions.append(f)

            elif child.type == "declaration":
                v = self._parse_variable(child, source, rel)
                if v:
                    variables.append(v)

            elif child.type == "preproc_include":
                imp = self._parse_include(child, source, rel)
                if imp:
                    imports.append(imp)

            elif child.type == "type_definition":
                c = self._parse_struct_typedef(child, source, rel, module)
                if c:
                    classes.append(c)

            elif child.type == "struct_specifier":
                c = self._parse_struct(child, source, rel, module)
                if c:
                    classes.append(c)

    def _parse_function(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
    ) -> FunctionInfo | None:
        declarator = node.child_by_field_name("declarator")
        if not declarator:
            return None

        # The function_declarator contains the name and parameters
        func_decl = declarator
        if func_decl.type == "pointer_declarator":
            for c in func_decl.children:
                if c.type == "function_declarator":
                    func_decl = c
                    break

        if func_decl.type != "function_declarator":
            return None

        name_node = func_decl.child_by_field_name("declarator")
        if not name_node:
            return None

        name = self._node_text(name_node, source)
        params_node = func_decl.child_by_field_name("parameters")
        params = self._node_text(params_node, source) if params_node else "()"

        # Get return type
        type_node = node.child_by_field_name("type")
        ret_type = self._node_text(type_node, source) if type_node else ""

        is_static = any(
            c.type == "storage_class_specifier" and self._node_text(c, source) == "static"
            for c in node.children
        )

        return FunctionInfo(
            name=name,
            qualified_name=f"{module}.{name}",
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=f"{ret_type} {name}{params}",
            is_static=is_static,
            language=self.language,
            complexity=self._compute_complexity(node),
        )

    def _parse_struct(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
    ) -> ClassInfo | None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = self._node_text(name_node, source)
        return ClassInfo(
            name=name,
            qualified_name=f"{module}.{name}",
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            language=self.language,
        )

    def _parse_struct_typedef(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
    ) -> ClassInfo | None:
        """Parse typedef struct { ... } Name;"""
        type_id = None
        for child in node.children:
            if child.type == "type_identifier":
                type_id = child
        if not type_id:
            return None
        name = self._node_text(type_id, source)
        if not name:
            return None
        return ClassInfo(
            name=name,
            qualified_name=f"{module}.{name}",
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            language=self.language,
        )

    def _parse_include(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
    ) -> ImportInfo | None:
        for child in node.children:
            if child.type == "system_lib_string":
                mod = self._node_text(child, source).strip("<>")
                return ImportInfo(module=mod, file_path=rel, line=node.start_point.row + 1)
            elif child.type == "string_literal":
                mod = self._node_text(child, source).strip('"')
                return ImportInfo(module=mod, file_path=rel, line=node.start_point.row + 1)
        return None

    def _parse_variable(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
    ) -> VariableInfo | None:
        """Parse top-level variable declarations."""
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
        while current.type in ("pointer_declarator", "array_declarator", "parenthesized_declarator"):
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
            "if_statement", "else_clause", "for_statement", "while_statement",
            "do_statement", "case_statement", "conditional_expression",
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
        edges: list[CallEdge],
    ) -> None:
        if node.type == "function_definition":
            f = self._parse_function(node, source, rel, module)
            if f:
                enclosing = f

        if node.type == "call_expression" and enclosing:
            func_node = node.child_by_field_name("function")
            if func_node:
                callee_name = self._node_text(func_node, source)
                if _is_valid_c_callee(callee_name):
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
            self._extract_calls_recursive(child, source, rel, module, enclosing, edges)


def _is_valid_c_callee(name: str) -> bool:
    if not name or "\n" in name or "{" in name:
        return False
    # Reject casts and complex expressions
    if "(" in name or ")" in name or "*" in name:
        return False
    return True
