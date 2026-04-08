"""Incremental analysis cache — skip unchanged files on re-analysis."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from callchain.core.models import (
    CallEdge,
    ClassInfo,
    FunctionInfo,
    ImportInfo,
    Language,
    ModuleInfo,
    VariableInfo,
)

CACHE_DIR = ".callchain_cache"
CACHE_VERSION = 1


class AnalysisCache:
    """File-level cache backed by content hashes.

    Stores parsed ModuleInfo and raw CallEdge data per file.
    A file is considered unchanged when its SHA-256 hash matches the cached entry.
    """

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.cache_dir = project_root / CACHE_DIR
        self._index: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._load_index()

    # ── Public API ──────────────────────────────────────────────

    def get_module(self, file_path: Path) -> ModuleInfo | None:
        """Return cached ModuleInfo if the file is unchanged, else None."""
        rel = self._rel(file_path)
        entry = self._index.get(rel)
        if entry is None:
            return None
        current_hash = self._hash_file(file_path)
        if entry.get("hash") != current_hash:
            return None
        if entry.get("version") != CACHE_VERSION:
            return None
        return _deserialize_module(entry["module"])

    def get_edges(self, file_path: Path) -> list[CallEdge] | None:
        """Return cached CallEdge list if the file is unchanged, else None."""
        rel = self._rel(file_path)
        entry = self._index.get(rel)
        if entry is None:
            return None
        current_hash = self._hash_file(file_path)
        if entry.get("hash") != current_hash:
            return None
        if entry.get("version") != CACHE_VERSION:
            return None
        return _deserialize_edges(entry.get("edges", []))

    def put(self, file_path: Path, module: ModuleInfo, edges: list[CallEdge]) -> None:
        """Cache the parse result for a file."""
        rel = self._rel(file_path)
        self._index[rel] = {
            "version": CACHE_VERSION,
            "hash": self._hash_file(file_path),
            "module": _serialize_module(module),
            "edges": _serialize_edges(edges),
        }
        self._dirty = True

    def save(self) -> None:
        """Write cache to disk."""
        if not self._dirty:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        index_path = self.cache_dir / "index.json"
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False)
        self._dirty = False

    def clear(self) -> None:
        """Remove all cached data."""
        index_path = self.cache_dir / "index.json"
        if index_path.exists():
            index_path.unlink()
        self._index.clear()

    @property
    def stats(self) -> dict[str, int]:
        return {"cached_files": len(self._index)}

    # ── Internal ────────────────────────────────────────────────

    def _load_index(self) -> None:
        index_path = self.cache_dir / "index.json"
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    self._index = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._index = {}

    def _rel(self, file_path: Path) -> str:
        try:
            return str(file_path.relative_to(self.project_root))
        except ValueError:
            return str(file_path)

    @staticmethod
    def _hash_file(file_path: Path) -> str:
        try:
            data = file_path.read_bytes()
            return hashlib.sha256(data).hexdigest()
        except OSError:
            return ""


# ── Serialization helpers ───────────────────────────────────────

def _serialize_func(f: FunctionInfo) -> dict[str, Any]:
    return {
        "name": f.name,
        "qualified_name": f.qualified_name,
        "file_path": f.file_path,
        "line": f.line,
        "end_line": f.end_line,
        "signature": f.signature,
        "docstring": f.docstring,
        "is_method": f.is_method,
        "is_async": f.is_async,
        "is_static": f.is_static,
        "class_name": f.class_name,
        "decorators": f.decorators,
        "language": f.language.value,
        "complexity": f.complexity,
    }


def _deserialize_func(d: dict[str, Any]) -> FunctionInfo:
    return FunctionInfo(
        name=d["name"],
        qualified_name=d["qualified_name"],
        file_path=d["file_path"],
        line=d["line"],
        end_line=d.get("end_line"),
        signature=d.get("signature", ""),
        docstring=d.get("docstring"),
        is_method=d.get("is_method", False),
        is_async=d.get("is_async", False),
        is_static=d.get("is_static", False),
        class_name=d.get("class_name"),
        decorators=d.get("decorators", []),
        language=Language(d.get("language", "python")),
        complexity=d.get("complexity", 1),
    )


def _serialize_module(m: ModuleInfo) -> dict[str, Any]:
    return {
        "file_path": m.file_path,
        "language": m.language.value,
        "functions": [_serialize_func(f) for f in m.functions],
        "classes": [
            {
                "name": c.name,
                "qualified_name": c.qualified_name,
                "file_path": c.file_path,
                "line": c.line,
                "end_line": c.end_line,
                "bases": c.bases,
                "methods": [_serialize_func(meth) for meth in c.methods],
                "language": c.language.value,
            }
            for c in m.classes
        ],
        "imports": [
            {"module": i.module, "names": i.names, "alias": i.alias,
             "is_from_import": i.is_from_import, "file_path": i.file_path, "line": i.line}
            for i in m.imports
        ],
        "variables": [
            {"name": v.name, "file_path": v.file_path, "line": v.line}
            for v in m.variables
        ],
    }


def _deserialize_module(d: dict[str, Any]) -> ModuleInfo:
    return ModuleInfo(
        file_path=d["file_path"],
        language=Language(d["language"]),
        functions=[_deserialize_func(f) for f in d.get("functions", [])],
        classes=[
            ClassInfo(
                name=c["name"],
                qualified_name=c["qualified_name"],
                file_path=c["file_path"],
                line=c["line"],
                end_line=c.get("end_line"),
                bases=c.get("bases", []),
                methods=[_deserialize_func(m) for m in c.get("methods", [])],
                language=Language(c.get("language", "python")),
            )
            for c in d.get("classes", [])
        ],
        imports=[
            ImportInfo(
                module=i["module"],
                names=i.get("names", []),
                alias=i.get("alias"),
                is_from_import=i.get("is_from_import", False),
                file_path=i.get("file_path", ""),
                line=i.get("line", 0),
            )
            for i in d.get("imports", [])
        ],
        variables=[
            VariableInfo(name=v["name"], file_path=v["file_path"], line=v["line"])
            for v in d.get("variables", [])
        ],
    )


def _serialize_edges(edges: list[CallEdge]) -> list[dict[str, Any]]:
    return [
        {
            "caller": _serialize_func(e.caller),
            "callee": _serialize_func(e.callee),
            "call_site_line": e.call_site_line,
            "call_site_file": e.call_site_file,
        }
        for e in edges
    ]


def _deserialize_edges(data: list[dict[str, Any]]) -> list[CallEdge]:
    return [
        CallEdge(
            caller=_deserialize_func(d["caller"]),
            callee=_deserialize_func(d["callee"]),
            call_site_line=d.get("call_site_line"),
            call_site_file=d.get("call_site_file"),
        )
        for d in data
    ]
