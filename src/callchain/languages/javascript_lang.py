"""JavaScript / TypeScript language plugin using tree-sitter."""

from __future__ import annotations

from pathlib import Path

import tree_sitter
import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts

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

_js_parser: tree_sitter.Parser | None = None
_ts_parser: tree_sitter.Parser | None = None
_tsx_parser: tree_sitter.Parser | None = None


def _get_js_parser() -> tree_sitter.Parser:
    global _js_parser
    if _js_parser is None:
        _js_parser = tree_sitter.Parser(tree_sitter.Language(tsjs.language()))
    return _js_parser


def _get_ts_parser() -> tree_sitter.Parser:
    global _ts_parser
    if _ts_parser is None:
        _ts_parser = tree_sitter.Parser(tree_sitter.Language(tsts.language_typescript()))
    return _ts_parser


def _get_tsx_parser() -> tree_sitter.Parser:
    global _tsx_parser
    if _tsx_parser is None:
        _tsx_parser = tree_sitter.Parser(tree_sitter.Language(tsts.language_tsx()))
    return _tsx_parser


def _module_from_path(rel_path: str) -> str:
    s = rel_path.replace("/", ".").replace("\\", ".")
    for ext in (".tsx", ".ts", ".jsx", ".js"):
        if s.endswith(ext.replace(".", ".")):
            s = s[: -len(ext)]
            break
    return s


# Shared implementation for JS and TS — only extensions & parser differ.

class _JSPluginBase(LanguagePlugin):
    """Shared logic for JavaScript and TypeScript."""

    _abstract = True  # prevent registration

    def _get_parser(self, file_path: Path) -> tree_sitter.Parser:
        raise NotImplementedError

    def parse_file(self, file_path: Path, project_root: Path) -> ModuleInfo:
        source = self._read_file(file_path)
        parser = self._get_parser(file_path)
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
        parser = self._get_parser(file_path)
        tree = parser.parse(source)
        rel = self._rel_path(file_path, project_root)
        module = _module_from_path(rel)

        edges: list[CallEdge] = []
        self._extract_calls_recursive(tree.root_node, source, rel, module, None, None, edges)
        return edges

    # ── Parsing helpers ──────────────────────────────────────────

    _FUNC_TYPES = frozenset({
        "function_declaration", "generator_function_declaration",
        "method_definition",
    })

    _ARROW_TYPES = frozenset({"arrow_function"})

    _CLASS_TYPES = frozenset({"class_declaration"})

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
            # function declarations
            if child.type in self._FUNC_TYPES:
                f = self._parse_function(child, source, rel, module, None)
                if f:
                    functions.append(f)

            # export default function / export function
            elif child.type == "export_statement":
                for sub in child.children:
                    if sub.type in self._FUNC_TYPES:
                        f = self._parse_function(sub, source, rel, module, None)
                        if f:
                            functions.append(f)
                    elif sub.type in self._CLASS_TYPES:
                        c = self._parse_class(sub, source, rel, module)
                        if c:
                            classes.append(c)
                    elif sub.type == "lexical_declaration":
                        self._extract_arrow_funcs(sub, source, rel, module, functions, variables)

            # class declarations
            elif child.type in self._CLASS_TYPES:
                c = self._parse_class(child, source, rel, module)
                if c:
                    classes.append(c)

            # const / let / var with arrow functions or plain vars
            elif child.type in ("lexical_declaration", "variable_declaration"):
                self._extract_arrow_funcs(child, source, rel, module, functions, variables)

            # expression statements — catch object property arrow funcs like module.exports = { ... }
            elif child.type == "expression_statement":
                self._extract_deep_arrow_funcs(child, source, rel, module, functions)

            # imports
            elif child.type in ("import_statement", "import_declaration"):
                imp = self._parse_import(child, source, rel)
                if imp:
                    imports.append(imp)

    def _parse_function(
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
        is_async = any(c.type == "async" for c in node.children)
        is_static = any(c.type == "static" for c in node.children) if node.type == "method_definition" else False
        qualified = f"{module}.{class_name}.{name}" if class_name else f"{module}.{name}"

        return FunctionInfo(
            name=name,
            qualified_name=qualified,
            file_path=rel,
            line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            signature=f"{name}{params}",
            is_method=class_name is not None,
            is_async=is_async,
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
    ) -> ClassInfo | None:
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None
        name = self._node_text(name_node, source)

        bases: list[str] = []
        for child in node.children:
            if child.type == "class_heritage":
                for sub in child.children:
                    if sub.type not in ("extends",):
                        bases.append(self._node_text(sub, source))
                break

        body = node.child_by_field_name("body")
        methods: list[FunctionInfo] = []
        if body:
            for child in body.children:
                if child.type == "method_definition":
                    f = self._parse_function(child, source, rel, module, class_name=name)
                    if f:
                        methods.append(f)

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

    def _extract_arrow_funcs(
        self,
        decl_node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        functions: list[FunctionInfo],
        variables: list[VariableInfo],
    ) -> None:
        """Extract arrow function assignments and plain variable declarations."""
        for child in decl_node.children:
            if child.type != "variable_declarator":
                continue
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if not name_node:
                continue
            name = self._node_text(name_node, source)

            if value_node and value_node.type in self._ARROW_TYPES:
                params_node = value_node.child_by_field_name("parameters")
                if params_node:
                    params = self._node_text(params_node, source)
                else:
                    parameter_node = value_node.child_by_field_name("parameter")
                    params = self._node_text(parameter_node, source) if parameter_node else "()"
                is_async = any(c.type == "async" for c in value_node.children)
                functions.append(FunctionInfo(
                    name=name,
                    qualified_name=f"{module}.{name}",
                    file_path=rel,
                    line=child.start_point.row + 1,
                    end_line=child.end_point.row + 1,
                    signature=f"{name}({params})" if not params.startswith("(") else f"{name}{params}",
                    is_async=is_async,
                    language=self.language,
                    complexity=self._compute_complexity(value_node),
                ))
            elif value_node and value_node.type == "object":
                # Recurse into object literals to find arrow function properties
                self._extract_deep_arrow_funcs(value_node, source, rel, module, functions)
                variables.append(VariableInfo(
                    name=name,
                    file_path=rel,
                    line=child.start_point.row + 1,
                ))
            else:
                variables.append(VariableInfo(
                    name=name,
                    file_path=rel,
                    line=child.start_point.row + 1,
                ))

    def _extract_deep_arrow_funcs(
        self,
        node: tree_sitter.Node,
        source: bytes,
        rel: str,
        module: str,
        functions: list[FunctionInfo],
    ) -> None:
        """Recursively find named arrow functions inside objects, arrays, arguments."""
        # Object property: { key: (args) => body }
        if node.type == "pair":
            key_node = node.child_by_field_name("key")
            val_node = node.child_by_field_name("value")
            if key_node and val_node and val_node.type in self._ARROW_TYPES:
                name = self._node_text(key_node, source)
                params_node = val_node.child_by_field_name("parameters") or val_node.child_by_field_name("parameter")
                params = self._node_text(params_node, source) if params_node else "()"
                is_async = any(c.type == "async" for c in val_node.children)
                functions.append(FunctionInfo(
                    name=name,
                    qualified_name=f"{module}.{name}",
                    file_path=rel,
                    line=node.start_point.row + 1,
                    end_line=node.end_point.row + 1,
                    signature=f"{name}({params})" if not params.startswith("(") else f"{name}{params}",
                    is_async=is_async,
                    language=self.language,
                    complexity=self._compute_complexity(val_node),
                ))
                return  # don't recurse into the arrow body for structure extraction
        # Also catch: { key: function(...) {} } shorthand already handled by method_definition
        for child in node.children:
            self._extract_deep_arrow_funcs(child, source, rel, module, functions)

    def _parse_import(self, node: tree_sitter.Node, source: bytes, rel: str) -> ImportInfo | None:
        source_node = node.child_by_field_name("source")
        if not source_node:
            return None
        module_str = self._node_text(source_node, source).strip("'\"")
        names: list[str] = []
        for child in node.children:
            if child.type == "import_clause":
                for sub in child.children:
                    if sub.type == "identifier":
                        names.append(self._node_text(sub, source))
                    elif sub.type == "named_imports":
                        for spec in sub.children:
                            if spec.type == "import_specifier":
                                name_n = spec.child_by_field_name("name")
                                if name_n:
                                    names.append(self._node_text(name_n, source))
                    elif sub.type == "namespace_import":
                        names.append("*")
        return ImportInfo(
            module=module_str, names=names, is_from_import=True,
            file_path=rel, line=node.start_point.row + 1,
        )

    def _compute_complexity(self, node: tree_sitter.Node) -> int:
        complexity = 1
        branch_types = {"if_statement", "else_clause", "for_statement", "for_in_statement",
                        "while_statement", "do_statement", "switch_case", "catch_clause",
                        "ternary_expression"}
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in branch_types:
                complexity += 1
            elif n.type == "binary_expression":
                # Check operator for && / ||
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
        # Track current enclosing function
        if node.type in self._FUNC_TYPES:
            name_node = node.child_by_field_name("name")
            if name_node:
                fn = self._node_text(name_node, source)
                qn = f"{module}.{class_name}.{fn}" if class_name else f"{module}.{fn}"
                enclosing = FunctionInfo(
                    name=fn, qualified_name=qn, file_path=rel,
                    line=node.start_point.row + 1, language=self.language,
                )

        # Arrow function assigned to a variable: const foo = () => { ... }
        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            val_node = node.child_by_field_name("value")
            if name_node and val_node and val_node.type in self._ARROW_TYPES:
                fn = self._node_text(name_node, source)
                qn = f"{module}.{class_name}.{fn}" if class_name else f"{module}.{fn}"
                enclosing = FunctionInfo(
                    name=fn, qualified_name=qn, file_path=rel,
                    line=node.start_point.row + 1, language=self.language,
                )

        # Arrow function as object property: { key: () => { ... } }
        if node.type == "pair":
            key_node = node.child_by_field_name("key")
            val_node = node.child_by_field_name("value")
            if key_node and val_node and val_node.type in self._ARROW_TYPES:
                fn = self._node_text(key_node, source)
                qn = f"{module}.{fn}"
                enclosing = FunctionInfo(
                    name=fn, qualified_name=qn, file_path=rel,
                    line=node.start_point.row + 1, language=self.language,
                )

        if node.type in self._CLASS_TYPES:
            name_node = node.child_by_field_name("name")
            class_name = self._node_text(name_node, source) if name_node else class_name

        if node.type == "call_expression" and enclosing:
            callee_name = self._resolve_call(node, source)
            if callee_name:
                callee = FunctionInfo(
                    name=callee_name.split(".")[-1],
                    qualified_name=callee_name,
                    file_path="",
                    line=0,
                    language=self.language,
                )
                edges.append(CallEdge(
                    caller=enclosing, callee=callee,
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
        elif func.type == "member_expression":
            return self._node_text(func, source)
        return None


class JavaScriptPlugin(_JSPluginBase):
    language = Language.JAVASCRIPT
    extensions = (".js", ".jsx")
    _abstract = False

    def _get_parser(self, file_path: Path) -> tree_sitter.Parser:
        return _get_js_parser()


class TypeScriptPlugin(_JSPluginBase):
    language = Language.TYPESCRIPT
    extensions = (".ts", ".tsx")
    _abstract = False

    def _get_parser(self, file_path: Path) -> tree_sitter.Parser:
        if file_path.suffix == ".tsx":
            return _get_tsx_parser()
        return _get_ts_parser()
