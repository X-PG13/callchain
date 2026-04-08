"""Core data models for call chain analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Language(Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    C = "c"
    CPP = "cpp"

    @classmethod
    def from_extension(cls, ext: str) -> Language | None:
        return _EXT_MAP.get(ext.lstrip(".").lower())


_EXT_MAP: dict[str, Language] = {
    "py": Language.PYTHON,
    "js": Language.JAVASCRIPT,
    "jsx": Language.JAVASCRIPT,
    "ts": Language.TYPESCRIPT,
    "tsx": Language.TYPESCRIPT,
    "java": Language.JAVA,
    "go": Language.GO,
    "rs": Language.RUST,
    "c": Language.C,
    "h": Language.C,
    "cpp": Language.CPP,
    "cc": Language.CPP,
    "cxx": Language.CPP,
    "hpp": Language.CPP,
    "hxx": Language.CPP,
}


@dataclass
class Position:
    file_path: str
    line: int
    column: int = 0
    end_line: int | None = None
    end_column: int | None = None


@dataclass
class FunctionInfo:
    name: str
    qualified_name: str
    file_path: str
    line: int
    end_line: int | None = None
    signature: str = ""
    docstring: str | None = None
    is_method: bool = False
    is_async: bool = False
    is_static: bool = False
    class_name: str | None = None
    decorators: list[str] = field(default_factory=list)
    language: Language = Language.PYTHON
    complexity: int = 1

    @property
    def display_name(self) -> str:
        if self.class_name:
            return f"{self.class_name}.{self.name}"
        return self.name


@dataclass
class ClassInfo:
    name: str
    qualified_name: str
    file_path: str
    line: int
    end_line: int | None = None
    bases: list[str] = field(default_factory=list)
    methods: list[FunctionInfo] = field(default_factory=list)
    docstring: str | None = None
    decorators: list[str] = field(default_factory=list)
    language: Language = Language.PYTHON


@dataclass
class ImportInfo:
    module: str
    names: list[str] = field(default_factory=list)
    alias: str | None = None
    is_from_import: bool = False
    file_path: str = ""
    line: int = 0


@dataclass
class VariableInfo:
    name: str
    file_path: str
    line: int
    type_annotation: str | None = None
    value_repr: str | None = None


@dataclass
class CallEdge:
    caller: FunctionInfo
    callee: FunctionInfo
    call_site_line: int | None = None
    call_site_file: str | None = None

    def is_cross_file(self) -> bool:
        return self.caller.file_path != self.callee.file_path


@dataclass
class CallChain:
    nodes: list[FunctionInfo]
    edges: list[CallEdge] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.nodes)

    @property
    def cross_file_transitions(self) -> int:
        count = 0
        for i in range(1, len(self.nodes)):
            if self.nodes[i].file_path != self.nodes[i - 1].file_path:
                count += 1
        return count

    @property
    def files_involved(self) -> set[str]:
        return {n.file_path for n in self.nodes}


@dataclass
class ModuleInfo:
    file_path: str
    language: Language
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    imports: list[ImportInfo] = field(default_factory=list)
    variables: list[VariableInfo] = field(default_factory=list)


@dataclass
class CouplingMetrics:
    fan_in: int = 0
    fan_out: int = 0
    instability: float = 0.0


@dataclass
class AnalysisResult:
    """Complete analysis result for a project."""

    project_path: str
    languages_detected: list[Language] = field(default_factory=list)
    modules: list[ModuleInfo] = field(default_factory=list)
    edges: list[CallEdge] = field(default_factory=list)
    chains: list[CallChain] = field(default_factory=list)

    # Analysis metrics
    total_functions: int = 0
    total_classes: int = 0
    total_files: int = 0

    # Parse errors
    parse_errors: list[dict[str, str]] = field(default_factory=list)

    # Advanced analysis
    hotspot_functions: list[tuple[FunctionInfo, int]] = field(default_factory=list)
    dead_functions: list[FunctionInfo] = field(default_factory=list)
    circular_dependencies: list[list[str]] = field(default_factory=list)
    module_coupling: dict[str, CouplingMetrics] = field(default_factory=dict)
    complexity_distribution: dict[str, int] = field(default_factory=dict)
    unused_imports: list[ImportInfo] = field(default_factory=list)
    class_hierarchy: dict[str, list[str]] = field(default_factory=dict)  # class -> [subclasses]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_path": self.project_path,
            "languages": [lang.value for lang in self.languages_detected],
            "summary": {
                "total_files": self.total_files,
                "total_functions": self.total_functions,
                "total_classes": self.total_classes,
                "total_edges": len(self.edges),
                "total_chains": len(self.chains),
            },
            "parse_errors": self.parse_errors,
            "modules": [_module_to_dict(m) for m in self.modules],
            "edges": [_edge_to_dict(e) for e in self.edges],
            "chains": [_chain_to_dict(c) for c in self.chains],
            "analysis": {
                "hotspot_functions": [
                    {"function": f.qualified_name, "file": f.file_path, "call_count": count}
                    for f, count in self.hotspot_functions
                ],
                "dead_functions": [
                    {"function": f.qualified_name, "file": f.file_path, "line": f.line}
                    for f in self.dead_functions
                ],
                "circular_dependencies": self.circular_dependencies,
                "module_coupling": {
                    k: {"fan_in": v.fan_in, "fan_out": v.fan_out, "instability": round(v.instability, 3)}
                    for k, v in self.module_coupling.items()
                },
                "complexity_distribution": self.complexity_distribution,
                "unused_imports": [
                    {"module": i.module, "names": i.names, "file": i.file_path, "line": i.line}
                    for i in self.unused_imports
                ],
                "class_hierarchy": self.class_hierarchy,
            },
        }


def _func_to_dict(f: FunctionInfo) -> dict[str, Any]:
    return {
        "name": f.name,
        "qualified_name": f.qualified_name,
        "file_path": f.file_path,
        "line": f.line,
        "end_line": f.end_line,
        "signature": f.signature,
        "is_method": f.is_method,
        "is_async": f.is_async,
        "class_name": f.class_name,
        "decorators": f.decorators,
        "language": f.language.value,
        "complexity": f.complexity,
    }


def _edge_to_dict(e: CallEdge) -> dict[str, Any]:
    return {
        "caller": _func_to_dict(e.caller),
        "callee": _func_to_dict(e.callee),
        "call_site_line": e.call_site_line,
    }


def _chain_to_dict(c: CallChain) -> dict[str, Any]:
    return {
        "length": c.length,
        "cross_file_transitions": c.cross_file_transitions,
        "nodes": [_func_to_dict(n) for n in c.nodes],
    }


def _module_to_dict(m: ModuleInfo) -> dict[str, Any]:
    return {
        "file_path": m.file_path,
        "language": m.language.value,
        "functions": [_func_to_dict(f) for f in m.functions],
        "classes": [
            {
                "name": c.name,
                "qualified_name": c.qualified_name,
                "file_path": c.file_path,
                "line": c.line,
                "bases": c.bases,
                "methods": [_func_to_dict(meth) for meth in c.methods],
            }
            for c in m.classes
        ],
        "imports": [
            {"module": i.module, "names": i.names, "alias": i.alias, "line": i.line}
            for i in m.imports
        ],
    }
