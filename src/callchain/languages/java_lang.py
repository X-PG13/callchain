"""Java language plugin using tree-sitter."""

from __future__ import annotations

from pathlib import Path

import tree_sitter
import tree_sitter_java as tsjava

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
        _parser = tree_sitter.Parser(tree_sitter.Language(tsjava.language()))
    return _parser


def _module_from_path(rel_path: str) -> str:
    s = rel_path.replace("/", ".").replace("\\", ".")
    if s.endswith(".java"):
        s = s[:-5]
    return s


class JavaPlugin(LanguagePlugin):
    language = Language.JAVA
    extensions = (".java",)

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
            language=Language.JAVA,
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
            if child.type == "class_declaration":
                c = self._parse_class(child, source, rel, module)
                if c:
                    classes.append(c)
            elif child.type == "interface_declaration":
                c = self._parse_class(child, source, rel, module)
                if c:
                    classes.append(c)
            elif child.type == "import_declaration":
                imp = self._parse_import(child, source, rel)
                if imp:
                    imports.append(imp)
            elif child.type == "method_declaration":
                f = self._parse_method(child, source, rel, module, None)
                if f:
                    functions.append(f)

    def _parse_class(
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

        bases: list[str] = []
        superclass = node.child_by_field_name("superclass")
        if superclass:
            bases.extend(self._collect_type_names(superclass, source))
        else:
            for child in node.children:
                if child.type == "superclass":
                    bases.extend(self._collect_type_names(child, source))
        interfaces = node.child_by_field_name("interfaces") or node.child_by_field_name("extends_interfaces")
        if interfaces:
            bases.extend(self._collect_type_names(interfaces, source))
        else:
            for child in node.children:
                if child.type in ("super_interfaces", "extends_interfaces"):
                    bases.extend(self._collect_type_names(child, source))

        body = node.child_by_field_name("body")
        methods: list[FunctionInfo] = []
        if body:
            for child in body.children:
                if child.type in ("method_declaration", "constructor_declaration"):
                    f = self._parse_method(child, source, rel, module, name)
                    if f:
                        methods.append(f)

        decorators: list[str] = []
        for child in node.children:
            if child.type in ("marker_annotation", "annotation"):
                decorators.append(self._node_text(child, source))
            elif child.type == "modifiers":
                for sub in child.children:
                    if sub.type in ("marker_annotation", "annotation"):
                        decorators.append(self._node_text(sub, source))

        return ClassInfo(
            name=name,
            qualified_name=f"{module}.{name}",
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            bases=bases,
            methods=methods,
            decorators=decorators,
            language=Language.JAVA,
        )

    def _parse_method(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        class_name: str | None,
    ) -> FunctionInfo | None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = self._node_text(name_node, source)

        params_node = node.child_by_field_name("parameters")
        params = self._node_text(params_node, source) if params_node else "()"

        # modifiers
        is_static = False
        decorators: list[str] = []
        for child in node.children:
            if child.type == "modifiers":
                mod_text = self._node_text(child, source)
                is_static = "static" in mod_text
                for sub in child.children:
                    if sub.type in ("marker_annotation", "annotation"):
                        decorators.append(self._node_text(sub, source))

        type_node = node.child_by_field_name("type")
        return_type = self._node_text(type_node, source) if type_node else ""
        sig = f"{return_type} {name}{params}".strip()

        qualified = f"{module}.{class_name}.{name}" if class_name else f"{module}.{name}"

        return FunctionInfo(
            name=name,
            qualified_name=qualified,
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=sig,
            is_method=class_name is not None,
            is_static=is_static,
            class_name=class_name,
            decorators=decorators,
            language=Language.JAVA,
            complexity=self._compute_complexity(node),
        )

    def _parse_import(self, node: tree_sitter.Node, source: bytes, rel: str) -> ImportInfo | None:
        text = self._node_text(node, source)
        # import foo.bar.Baz;
        module = text.replace("import ", "").replace("static ", "").rstrip(";").strip()
        return ImportInfo(
            module=module, names=[], is_from_import=False,
            file_path=rel, line=node.start_point.row + 1,
        )

    def _compute_complexity(self, node: tree_sitter.Node) -> int:
        complexity = 1
        branch_types = {"if_statement", "else_clause", "for_statement", "enhanced_for_statement",
                        "while_statement", "do_statement", "switch_expression", "catch_clause",
                        "ternary_expression"}
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in branch_types:
                complexity += 1
            if n.type == "binary_expression":
                for child in n.children:
                    if child.type in ("&&", "||"):
                        complexity += 1
                        break
            stack.extend(n.children)
        return complexity

    def _collect_type_names(self, node: tree_sitter.Node, source: bytes) -> list[str]:
        names: list[str] = []
        if node.type in ("type_identifier", "scoped_type_identifier"):
            names.append(self._node_text(node, source))
            return names
        for child in node.children:
            names.extend(self._collect_type_names(child, source))
        return names

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
        if node.type == "class_declaration":
            nn = node.child_by_field_name("name")
            class_name = self._node_text(nn, source) if nn else class_name

        if node.type in ("method_declaration", "constructor_declaration"):
            nn = node.child_by_field_name("name")
            if nn:
                fn = self._node_text(nn, source)
                qn = f"{module}.{class_name}.{fn}" if class_name else f"{module}.{fn}"
                enclosing = FunctionInfo(
                    name=fn, qualified_name=qn, file_path=rel,
                    line=node.start_point.row + 1, language=Language.JAVA,
                    is_method=class_name is not None, class_name=class_name,
                )

        if node.type == "method_invocation" and enclosing:
            name_node = node.child_by_field_name("name")
            obj_node = node.child_by_field_name("object")
            if name_node:
                callee_name = self._node_text(name_node, source)
                if obj_node:
                    callee_name = f"{self._node_text(obj_node, source)}.{callee_name}"
                callee = FunctionInfo(
                    name=callee_name.split(".")[-1],
                    qualified_name=callee_name,
                    file_path="", line=0, language=Language.JAVA,
                )
                edges.append(CallEdge(
                    caller=enclosing, callee=callee,
                    call_site_line=node.start_point.row + 1,
                    call_site_file=rel,
                ))

        # Lambda expressions: extract calls inside lambda bodies
        # The lambda itself doesn't change the enclosing — calls inside belong to the enclosing method
        # But we need to handle method_reference (e.g., this::process)
        if node.type == "method_reference" and enclosing:
            # e.g. this::process, String::valueOf
            text = self._node_text(node, source)
            parts = text.split("::")
            if len(parts) == 2:
                callee_name = parts[1]
                if parts[0] not in ("", "this", "super"):
                    callee_name = f"{parts[0]}.{parts[1]}"
                callee = FunctionInfo(
                    name=callee_name.split(".")[-1],
                    qualified_name=callee_name,
                    file_path="", line=0, language=Language.JAVA,
                )
                edges.append(CallEdge(
                    caller=enclosing, callee=callee,
                    call_site_line=node.start_point.row + 1,
                    call_site_file=rel,
                ))

        for child in node.children:
            self._extract_calls_recursive(child, source, rel, module, enclosing, class_name, edges)
