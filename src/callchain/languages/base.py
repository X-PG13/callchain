"""Abstract base class for language plugins."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

from callchain.core.models import (
    CallEdge,
    Language,
    ModuleInfo,
)

if TYPE_CHECKING:
    import tree_sitter

# Global registry: Language -> plugin class
_REGISTRY: dict[Language, type[LanguagePlugin]] = {}

_PLUGIN_MODULES = {
    Language.PYTHON: "python_lang",
    Language.JAVASCRIPT: "javascript_lang",
    Language.TYPESCRIPT: "javascript_lang",
    Language.JAVA: "java_lang",
    Language.GO: "go_lang",
    Language.RUST: "rust_lang",
    Language.C: "c_lang",
    Language.CPP: "cpp_lang",
}

# Directories to skip during file discovery
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".callchain_cache",
    "node_modules", "bower_components",
    "venv", ".venv", "env", ".env",
    "build", "dist", "target", "out", "bin", "obj",
    ".idea", ".vscode", ".eclipse",
    "vendor",
})


def get_plugin(language: Language) -> LanguagePlugin:
    _ensure_plugin_registered(language)
    cls = _REGISTRY.get(language)
    if cls is None:
        raise ValueError(f"No plugin registered for {language.value}")
    return cls()


def get_all_plugins() -> dict[Language, LanguagePlugin]:
    for language in _PLUGIN_MODULES:
        _ensure_plugin_registered(language)
    return {lang: cls() for lang, cls in _REGISTRY.items()}


def detect_languages(project_path: str | Path) -> list[Language]:
    """Detect which languages are present in a project directory."""
    found: set[Language] = set()
    project = Path(project_path)
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            ext = Path(fname).suffix.lstrip(".")
            lang = Language.from_extension(ext)
            if lang is not None:
                found.add(lang)
    return sorted(found, key=lambda x: x.value)


class LanguagePlugin(ABC):
    """Base class that every language plugin must implement."""

    language: Language
    extensions: tuple[str, ...]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "language") and not getattr(cls, "_abstract", False):
            _REGISTRY[cls.language] = cls

    # ── File discovery ──────────────────────────────────────────────

    def discover_files(self, project_path: str | Path) -> list[Path]:
        """Find all source files for this language under *project_path*."""
        result: list[Path] = []
        root = Path(project_path)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                if any(fname.endswith(ext) for ext in self.extensions):
                    result.append(Path(dirpath) / fname)
        return sorted(result)

    # ── Parsing ─────────────────────────────────────────────────────

    @abstractmethod
    def parse_file(self, file_path: Path, project_root: Path) -> ModuleInfo:
        """Parse a single source file and return structured info."""
        ...

    @abstractmethod
    def extract_calls(self, file_path: Path, project_root: Path) -> list[CallEdge]:
        """Extract call edges from a single source file."""
        ...

    # ── Helpers shared by all tree-sitter based plugins ────────────

    @staticmethod
    def _read_file(path: Path) -> bytes:
        return path.read_bytes()

    @staticmethod
    def _node_text(node: tree_sitter.Node, source: bytes) -> str:
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _rel_path(file_path: Path, project_root: Path) -> str:
        try:
            return str(file_path.relative_to(project_root))
        except ValueError:
            return str(file_path)


def _ensure_plugin_registered(language: Language) -> None:
    if language in _REGISTRY:
        return
    module_name = _PLUGIN_MODULES.get(language)
    if module_name is None:
        return
    import_module(f"callchain.languages.{module_name}")
