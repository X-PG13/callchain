"""Language-agnostic call graph builder and symbol resolver."""

from __future__ import annotations

import fnmatch
from collections import defaultdict
from pathlib import Path

from callchain.core.models import (
    AnalysisResult,
    CallEdge,
    FunctionInfo,
    Language,
    ModuleInfo,
)
from callchain.core.cache import AnalysisCache
from callchain.languages.base import LanguagePlugin, detect_languages, get_plugin


class ParseError:
    """Record of a file that failed to parse."""
    __slots__ = ("file_path", "phase", "error")

    def __init__(self, file_path: str, phase: str, error: str):
        self.file_path = file_path
        self.phase = phase
        self.error = error

    def __repr__(self) -> str:
        return f"ParseError({self.file_path!r}, {self.phase}, {self.error!r})"


class CallGraphBuilder:
    """Build a unified call graph from one or more language plugins."""

    def __init__(
        self,
        project_path: str | Path,
        use_cache: bool = False,
        exclude: list[str] | None = None,
    ):
        self.project_root = Path(project_path).resolve()
        self.modules: list[ModuleInfo] = []
        self.raw_edges: list[CallEdge] = []
        self.resolved_edges: list[CallEdge] = []
        self.parse_errors: list[ParseError] = []
        self._use_cache = use_cache
        self._cache = AnalysisCache(self.project_root) if use_cache else None
        self._cache_hits = 0
        self._cache_misses = 0
        self._exclude = exclude or []

        # Symbol lookup tables built during parsing
        self._func_by_qname: dict[str, FunctionInfo] = {}
        self._func_by_simple: dict[str, list[FunctionInfo]] = defaultdict(list)

    def build(
        self,
        languages: list[Language] | None = None,
        restrict_dir: str | None = None,
    ) -> AnalysisResult:
        """Run the full pipeline: discover -> parse -> extract calls -> resolve."""
        if languages is None:
            languages = detect_languages(self.project_root)
        if not languages:
            return AnalysisResult(project_path=str(self.project_root), languages_detected=[])

        # Phase 1: parse all files to build the symbol index
        for lang in languages:
            plugin = get_plugin(lang)
            self._parse_language(plugin, restrict_dir)

        # Phase 2: extract raw call edges
        for lang in languages:
            plugin = get_plugin(lang)
            self._extract_calls(plugin, restrict_dir)

        # Phase 3: resolve callee stubs to actual definitions
        self._resolve_edges()

        # Save cache if enabled
        if self._cache:
            self._cache.save()

        result = AnalysisResult(
            project_path=str(self.project_root),
            languages_detected=languages,
            modules=self.modules,
            edges=self.resolved_edges,
            total_files=len(self.modules),
            total_functions=sum(
                len(m.functions) + sum(len(c.methods) for c in m.classes)
                for m in self.modules
            ),
            total_classes=sum(len(m.classes) for m in self.modules),
            parse_errors=[
                {"file": e.file_path, "phase": e.phase, "error": e.error}
                for e in self.parse_errors
            ],
        )
        return result

    # ── Phase 1: parse ───────────────────────────────────────────

    def _parse_language(self, plugin: LanguagePlugin, restrict_dir: str | None) -> None:
        files = plugin.discover_files(self.project_root)
        files = self._filter_files(files, restrict_dir)

        for fpath in files:
            # Try cache first
            if self._cache:
                cached_mod = self._cache.get_module(fpath)
                if cached_mod is not None:
                    self._cache_hits += 1
                    self.modules.append(cached_mod)
                    self._index_module(cached_mod)
                    continue
                self._cache_misses += 1

            try:
                mod = plugin.parse_file(fpath, self.project_root)
            except Exception as exc:
                rel = self._rel_path(fpath)
                self.parse_errors.append(ParseError(rel, "parse", f"{type(exc).__name__}: {exc}"))
                continue
            self.modules.append(mod)
            self._index_module(mod)

    def _index_module(self, mod: ModuleInfo) -> None:
        for func in mod.functions:
            self._func_by_qname[func.qualified_name] = func
            self._func_by_simple[func.name].append(func)
        for cls in mod.classes:
            for meth in cls.methods:
                self._func_by_qname[meth.qualified_name] = meth
                self._func_by_simple[meth.name].append(meth)
                # Also index as ClassName.method
                short = f"{cls.name}.{meth.name}"
                self._func_by_simple[short].append(meth)

    # ── Phase 2: extract calls ───────────────────────────────────

    def _extract_calls(self, plugin: LanguagePlugin, restrict_dir: str | None) -> None:
        files = plugin.discover_files(self.project_root)
        files = self._filter_files(files, restrict_dir)

        for fpath in files:
            # Try cache first
            if self._cache:
                cached_edges = self._cache.get_edges(fpath)
                if cached_edges is not None:
                    self.raw_edges.extend(cached_edges)
                    continue

            try:
                edges = plugin.extract_calls(fpath, self.project_root)
            except Exception as exc:
                rel = self._rel_path(fpath)
                self.parse_errors.append(ParseError(rel, "extract_calls", f"{type(exc).__name__}: {exc}"))
                continue
            self.raw_edges.extend(edges)

            # Store in cache
            if self._cache:
                rel = self._rel_path(fpath)
                # Find corresponding module
                mod = next((m for m in self.modules if m.file_path == rel), None)
                if mod:
                    self._cache.put(fpath, mod, edges)

    # ── Phase 3: resolve ─────────────────────────────────────────

    def _resolve_edges(self) -> None:
        for edge in self.raw_edges:
            resolved_callee = self._resolve_function(edge.callee, caller=edge.caller)
            if resolved_callee:
                self.resolved_edges.append(CallEdge(
                    caller=edge.caller,
                    callee=resolved_callee,
                    call_site_line=edge.call_site_line,
                    call_site_file=edge.call_site_file,
                ))

    def _resolve_function(self, stub: FunctionInfo, caller: FunctionInfo | None = None) -> FunctionInfo | None:
        qname = stub.qualified_name

        # Try exact qualified name match
        if qname in self._func_by_qname:
            return self._func_by_qname[qname]

        simple = qname.split(".")[-1] if "." in qname else qname

        # Handle self.method / this.method — resolve to caller's own class
        if "." in qname:
            prefix = qname.rsplit(".", 1)[0].split(".")[-1]
            if prefix in ("self", "this") and caller and caller.class_name:
                class_key = f"{caller.class_name}.{simple}"
                candidates = self._func_by_simple.get(class_key, [])
                if candidates:
                    # Prefer same-file match
                    same_file = [c for c in candidates if c.file_path == caller.file_path]
                    return same_file[0] if same_file else candidates[0]

        # Try ClassName.method (two-part match)
        if "." in qname:
            parts = qname.rsplit(".", 1)
            two_part = f"{parts[-2].split('.')[-1]}.{parts[-1]}" if len(parts) == 2 else simple
            candidates = self._func_by_simple.get(two_part, [])
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                # Disambiguate: prefer same file, then same class
                if caller:
                    same_file = [c for c in candidates if c.file_path == caller.file_path]
                    if len(same_file) == 1:
                        return same_file[0]
                    if caller.class_name:
                        same_class = [c for c in candidates if c.class_name == caller.class_name]
                        if len(same_class) == 1:
                            return same_class[0]
                return candidates[0]

        # Try simple name
        candidates = self._func_by_simple.get(simple, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1 and caller:
            # Disambiguate: prefer same file
            same_file = [c for c in candidates if c.file_path == caller.file_path]
            if len(same_file) == 1:
                return same_file[0]
            # Prefer same class
            if caller.class_name:
                same_class = [c for c in candidates if c.class_name == caller.class_name]
                if len(same_class) == 1:
                    return same_class[0]

        # Ambiguous or unresolved — skip
        return None

    def _rel_path(self, fpath: Path) -> str:
        try:
            return str(fpath.relative_to(self.project_root))
        except ValueError:
            return str(fpath)

    def _filter_files(self, files: list[Path], restrict_dir: str | None) -> list[Path]:
        """Apply restrict_dir and exclude patterns to a list of files."""
        if restrict_dir:
            restrict = (self.project_root / restrict_dir).resolve()
            files = [f for f in files if f.resolve().is_relative_to(restrict)]
        if self._exclude:
            filtered: list[Path] = []
            for f in files:
                rel = self._rel_path(f)
                if not _matches_any(rel, self._exclude):
                    filtered.append(f)
            files = filtered
        return files


def _matches_any(path: str, patterns: list[str]) -> bool:
    """Check if a path matches any glob pattern. Supports directory-prefix matching."""
    for pat in patterns:
        if fnmatch.fnmatch(path, pat):
            return True
        # Also match files under a directory prefix: "dir" matches "dir/foo.py"
        if "/" not in pat and "*" not in pat and "?" not in pat:
            if path.startswith(pat + "/") or path == pat:
                return True
        # Match "dir/**" style
        if pat.endswith("/**") and path.startswith(pat[:-3] + "/"):
            return True
    return False
