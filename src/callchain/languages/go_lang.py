"""Go language plugin using tree-sitter."""

from __future__ import annotations

from pathlib import Path

import tree_sitter
import tree_sitter_go as tsgo

from callchain.core.models import (
    CallEdge,
    ClassInfo,
    FunctionInfo,
    ImportInfo,
    Language,
    ModuleInfo,
)
from callchain.languages.base import LanguagePlugin

_parser: tree_sitter.Parser | None = None


def _get_parser() -> tree_sitter.Parser:
    global _parser
    if _parser is None:
        _parser = tree_sitter.Parser(tree_sitter.Language(tsgo.language()))
    return _parser


def _module_from_path(rel_path: str) -> str:
    s = rel_path.replace("/", ".").replace("\\", ".")
    if s.endswith(".go"):
        s = s[:-3]
    return s


def _is_valid_go_callee(name: str) -> bool:
    """Filter out malformed callee names (anonymous funcs, type conversions with braces)."""
    if not name:
        return False
    # Anonymous func literal or multiline expression
    if "func(" in name or "\n" in name or "{" in name:
        return False
    # Simple identifiers and dotted identifiers only
    return all(c.isalnum() or c in (".", "_") for c in name)


class GoPlugin(LanguagePlugin):
    language = Language.GO
    extensions = (".go",)

    def discover_files(self, project_path: str | Path) -> list[Path]:
        files = super().discover_files(project_path)
        return [f for f in files if not f.name.endswith("_test.go")]

    def parse_file(self, file_path: Path, project_root: Path) -> ModuleInfo:
        source = self._read_file(file_path)
        tree = _get_parser().parse(source)
        rel = self._rel_path(file_path, project_root)
        module = _module_from_path(rel)

        functions: list[FunctionInfo] = []
        classes: list[ClassInfo] = []
        imports: list[ImportInfo] = []

        self._walk(tree.root_node, source, rel, module, functions, classes, imports)

        return ModuleInfo(
            file_path=rel,
            language=Language.GO,
            functions=functions,
            classes=classes,
            imports=imports,
        )

    def extract_calls(self, file_path: Path, project_root: Path) -> list[CallEdge]:
        source = self._read_file(file_path)
        tree = _get_parser().parse(source)
        rel = self._rel_path(file_path, project_root)
        module = _module_from_path(rel)

        edges: list[CallEdge] = []
        self._extract_calls_recursive(tree.root_node, source, rel, module, None, edges)
        return edges

    # ── Parsing ──────────────────────────────────────────────────

    def _walk(
        self,
        root: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        functions: list[FunctionInfo],
        classes: list[ClassInfo],
        imports: list[ImportInfo],
    ) -> None:
        for child in root.children:
            if child.type == "function_declaration":
                f = self._parse_function(child, source, rel, module)
                if f:
                    functions.append(f)

            elif child.type == "method_declaration":
                f = self._parse_method_decl(child, source, rel, module)
                if f:
                    functions.append(f)

            elif child.type == "type_declaration":
                for spec in child.children:
                    if spec.type == "type_spec":
                        c = self._parse_type_spec(spec, source, rel, module)
                        if c:
                            classes.append(c)

            elif child.type == "import_declaration":
                self._parse_imports(child, source, rel, imports)

    def _parse_function(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
    ) -> FunctionInfo | None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = self._node_text(name_node, source)
        params_node = node.child_by_field_name("parameters")
        params = self._node_text(params_node, source) if params_node else "()"
        result_node = node.child_by_field_name("result")
        ret = f" {self._node_text(result_node, source)}" if result_node else ""

        return FunctionInfo(
            name=name,
            qualified_name=f"{module}.{name}",
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=f"func {name}{params}{ret}",
            language=Language.GO,
            complexity=self._compute_complexity(node),
        )

    def _parse_method_decl(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
    ) -> FunctionInfo | None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = self._node_text(name_node, source)

        receiver = node.child_by_field_name("receiver")
        receiver_type = ""
        if receiver:
            for child in receiver.children:
                if child.type == "parameter_declaration":
                    type_node = child.child_by_field_name("type")
                    if type_node:
                        receiver_type = self._node_text(type_node, source).lstrip("*")

        params_node = node.child_by_field_name("parameters")
        params = self._node_text(params_node, source) if params_node else "()"

        qualified = f"{module}.{receiver_type}.{name}" if receiver_type else f"{module}.{name}"

        return FunctionInfo(
            name=name,
            qualified_name=qualified,
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=f"func ({receiver_type}) {name}{params}",
            is_method=bool(receiver_type),
            class_name=receiver_type or None,
            language=Language.GO,
            complexity=self._compute_complexity(node),
        )

    def _parse_type_spec(
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
        type_node = node.child_by_field_name("type")
        if not type_node or type_node.type != "struct_type":
            return None

        return ClassInfo(
            name=name,
            qualified_name=f"{module}.{name}",
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            language=Language.GO,
        )

    def _parse_imports(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        imports: list[ImportInfo],
    ) -> None:
        for child in node.children:
            if child.type == "import_spec":
                path_node = child.child_by_field_name("path")
                if path_node:
                    module_str = self._node_text(path_node, source).strip('"')
                    alias_node = child.child_by_field_name("name")
                    alias = self._node_text(alias_node, source) if alias_node else None
                    imports.append(ImportInfo(
                        module=module_str, alias=alias,
                        file_path=rel, line=child.start_point.row + 1,
                    ))
            elif child.type == "import_spec_list":
                self._parse_imports(child, source, rel, imports)
            elif child.type == "interpreted_string_literal":
                module_str = self._node_text(child, source).strip('"')
                imports.append(ImportInfo(
                    module=module_str, file_path=rel, line=child.start_point.row + 1,
                ))

    def _compute_complexity(self, node: tree_sitter.Node) -> int:
        complexity = 1
        branch_types = {"if_statement", "for_statement", "expression_switch_statement",
                        "type_switch_statement", "select_statement", "expression_case",
                        "default_case", "communication_case"}
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in branch_types:
                complexity += 1
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
        if node.type in ("function_declaration", "method_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node:
                fn = self._node_text(name_node, source)
                # detect receiver for methods
                receiver_type = ""
                if node.type == "method_declaration":
                    receiver = node.child_by_field_name("receiver")
                    if receiver:
                        for child in receiver.children:
                            if child.type == "parameter_declaration":
                                tn = child.child_by_field_name("type")
                                if tn:
                                    receiver_type = self._node_text(tn, source).lstrip("*")
                qn = f"{module}.{receiver_type}.{fn}" if receiver_type else f"{module}.{fn}"
                enclosing = FunctionInfo(
                    name=fn, qualified_name=qn, file_path=rel,
                    line=node.start_point.row + 1, language=Language.GO,
                    is_method=bool(receiver_type), class_name=receiver_type or None,
                )

        if node.type == "call_expression" and enclosing:
            func_node = node.child_by_field_name("function")
            if func_node:
                # Skip anonymous function literals (func(...){...}() / go func(){...}())
                if func_node.type == "func_literal":
                    pass  # will still recurse into children below to extract inner calls
                else:
                    callee_name = self._node_text(func_node, source)
                    # Skip if callee looks like an inline expression rather than an identifier
                    if _is_valid_go_callee(callee_name):
                        callee = FunctionInfo(
                            name=callee_name.split(".")[-1],
                            qualified_name=callee_name,
                            file_path="", line=0, language=Language.GO,
                        )
                        edges.append(CallEdge(
                            caller=enclosing, callee=callee,
                            call_site_line=node.start_point.row + 1,
                            call_site_file=rel,
                        ))

        for child in node.children:
            self._extract_calls_recursive(child, source, rel, module, enclosing, edges)
