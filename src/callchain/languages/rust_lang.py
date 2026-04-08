"""Rust language plugin using tree-sitter."""

from __future__ import annotations

from pathlib import Path

import tree_sitter
import tree_sitter_rust as tsrust

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
        _parser = tree_sitter.Parser(tree_sitter.Language(tsrust.language()))
    return _parser


def _module_from_path(rel_path: str) -> str:
    s = rel_path.replace("/", ".").replace("\\", ".")
    if s.endswith(".rs"):
        s = s[:-3]
    s = s.replace(".mod", "")
    return s


def _is_valid_rust_callee(name: str) -> bool:
    """Filter out malformed callee names (closures, complex expressions)."""
    if not name:
        return False
    # Closures, multiline, or expressions with braces/pipes
    if any(c in name for c in ("{", "}", "|", "\n", "[", "]")):
        return False
    # Only allow identifiers, ::, and .
    return all(c.isalnum() or c in (":",".", "_", "<", ">", "&", "*") for c in name)


class RustPlugin(LanguagePlugin):
    language = Language.RUST
    extensions = (".rs",)

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
            language=Language.RUST,
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
        self._extract_calls_recursive(tree.root_node, source, rel, module, None, None, edges)
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
            if child.type == "function_item":
                f = self._parse_function(child, source, rel, module, None)
                if f:
                    functions.append(f)

            elif child.type == "impl_item":
                self._parse_impl(child, source, rel, module, functions)

            elif child.type in ("struct_item", "enum_item"):
                c = self._parse_struct_or_enum(child, source, rel, module)
                if c:
                    classes.append(c)

            elif child.type == "trait_item":
                c = self._parse_trait(child, source, rel, module)
                if c:
                    classes.append(c)

            elif child.type == "use_declaration":
                imp = self._parse_use(child, source, rel)
                if imp:
                    imports.append(imp)

    def _parse_function(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        impl_type: str | None,
    ) -> FunctionInfo | None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = self._node_text(name_node, source)

        params_node = node.child_by_field_name("parameters")
        params = self._node_text(params_node, source) if params_node else "()"

        return_node = node.child_by_field_name("return_type")
        ret = f" -> {self._node_text(return_node, source)}" if return_node else ""

        is_async = any(c.type == "async" for c in node.children)
        is_method = impl_type is not None
        # check if first param is &self / self / &mut self
        has_self = "self" in params.split(",")[0] if params else False
        if impl_type and not has_self:
            is_method = False  # associated function, not method

        qualified = f"{module}.{impl_type}.{name}" if impl_type else f"{module}.{name}"

        return FunctionInfo(
            name=name,
            qualified_name=qualified,
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=f"fn {name}{params}{ret}",
            is_method=is_method,
            is_async=is_async,
            is_static=impl_type is not None and not has_self,
            class_name=impl_type,
            language=Language.RUST,
            complexity=self._compute_complexity(node),
        )

    def _parse_impl(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        functions: list[FunctionInfo],
    ) -> None:
        type_node = node.child_by_field_name("type")
        impl_type = self._node_text(type_node, source) if type_node else None

        body = node.child_by_field_name("body")
        if not body:
            return
        for child in body.children:
            if child.type == "function_item":
                f = self._parse_function(child, source, rel, module, impl_type)
                if f:
                    functions.append(f)

    def _parse_struct_or_enum(
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
            language=Language.RUST,
        )

    def _parse_trait(
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
        # Extract method signatures from trait body
        body = node.child_by_field_name("body")
        methods: list[FunctionInfo] = []
        if body:
            for child in body.children:
                if child.type in ("function_item", "function_signature_item"):
                    f = self._parse_function(child, source, rel, module, name)
                    if f:
                        methods.append(f)
        return ClassInfo(
            name=name,
            qualified_name=f"{module}.{name}",
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            methods=methods,
            language=Language.RUST,
        )

    def _parse_use(self, node: tree_sitter.Node, source: bytes, rel: str) -> ImportInfo | None:
        text = self._node_text(node, source)
        # use foo::bar::Baz;
        path = text.replace("use ", "").replace("pub ", "").rstrip(";").strip()
        return ImportInfo(
            module=path, file_path=rel, line=node.start_point.row + 1,
        )

    def _compute_complexity(self, node: tree_sitter.Node) -> int:
        complexity = 1
        branch_types = {"if_expression", "else_clause", "for_expression", "while_expression",
                        "loop_expression", "match_arm", "if_let_expression"}
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
        impl_type: str | None,
        edges: list[CallEdge],
    ) -> None:
        if node.type == "impl_item":
            type_node = node.child_by_field_name("type")
            impl_type = self._node_text(type_node, source) if type_node else impl_type

        if node.type == "function_item":
            name_node = node.child_by_field_name("name")
            if name_node:
                fn = self._node_text(name_node, source)
                qn = f"{module}.{impl_type}.{fn}" if impl_type else f"{module}.{fn}"
                enclosing = FunctionInfo(
                    name=fn, qualified_name=qn, file_path=rel,
                    line=node.start_point.row + 1, language=Language.RUST,
                    class_name=impl_type,
                )

        if node.type == "call_expression" and enclosing:
            func_node = node.child_by_field_name("function")
            if func_node:
                callee_name = self._node_text(func_node, source)
                # Skip if callee is a complex expression (closure, chain) rather than a name
                if _is_valid_rust_callee(callee_name):
                    callee = FunctionInfo(
                        name=callee_name.split("::")[-1].split(".")[-1],
                        qualified_name=callee_name.replace("::", "."),
                        file_path="", line=0, language=Language.RUST,
                    )
                    edges.append(CallEdge(
                        caller=enclosing, callee=callee,
                        call_site_line=node.start_point.row + 1,
                        call_site_file=rel,
                    ))

        # Macro invocations: e.g. println!(...), vec![...]
        if node.type == "macro_invocation" and enclosing:
            macro_node = node.child_by_field_name("macro")
            if macro_node:
                macro_name = self._node_text(macro_node, source)
                callee = FunctionInfo(
                    name=f"{macro_name}!",
                    qualified_name=f"{macro_name}!",
                    file_path="", line=0, language=Language.RUST,
                )
                edges.append(CallEdge(
                    caller=enclosing, callee=callee,
                    call_site_line=node.start_point.row + 1,
                    call_site_file=rel,
                ))

        for child in node.children:
            self._extract_calls_recursive(child, source, rel, module, enclosing, impl_type, edges)
