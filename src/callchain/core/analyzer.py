"""Advanced analysis: complexity, coupling, dead code, circular dependencies, hotspots."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace

from callchain.core.models import (
    AnalysisResult,
    CouplingMetrics,
    FunctionInfo,
    ImportInfo,
)


class Analyzer:
    """Run advanced analysis on a parsed call graph."""

    def __init__(self, result: AnalysisResult):
        self.result = result

    def run_all(self) -> AnalysisResult:
        """Run every analysis and populate the result."""
        self.compute_hotspots()
        self.compute_dead_functions()
        self.compute_module_coupling()
        self.detect_circular_dependencies()
        self.compute_complexity_distribution()
        self.detect_unused_imports()
        self.build_class_hierarchy()
        return self.result

    # ── Hotspot functions (most called) ──────────────────────────

    def compute_hotspots(self, top_n: int = 20) -> list[tuple[FunctionInfo, int]]:
        call_count: Counter[str] = Counter()
        func_map: dict[str, FunctionInfo] = {}
        for edge in self.result.edges:
            qn = edge.callee.qualified_name
            call_count[qn] += 1
            func_map[qn] = edge.callee

        hotspots = [
            (func_map[qn], count)
            for qn, count in call_count.most_common(top_n)
            if qn in func_map
        ]
        self.result.hotspot_functions = hotspots
        return hotspots

    # ── Dead code detection ──────────────────────────────────────

    def compute_dead_functions(self) -> list[FunctionInfo]:
        """Find functions that are never called (no incoming edges).

        Excludes common entry points like main, __init__, test_*, etc.
        """
        called: set[str] = {edge.callee.qualified_name for edge in self.result.edges}

        all_funcs: dict[str, FunctionInfo] = {}
        for mod in self.result.modules:
            for func in mod.functions:
                all_funcs[func.qualified_name] = func
            for cls in mod.classes:
                for meth in cls.methods:
                    all_funcs[meth.qualified_name] = meth

        dead: list[FunctionInfo] = []
        for qn, func in all_funcs.items():
            if qn in called:
                continue
            # Skip common entry points
            if _is_likely_entrypoint(func):
                continue
            dead.append(func)

        self.result.dead_functions = dead
        return dead

    # ── Module coupling (fan-in / fan-out) ───────────────────────

    def compute_module_coupling(self) -> dict[str, CouplingMetrics]:
        # Module = file_path
        fan_out: dict[str, set[str]] = defaultdict(set)
        fan_in: dict[str, set[str]] = defaultdict(set)

        for edge in self.result.edges:
            src_mod = edge.caller.file_path
            dst_mod = edge.callee.file_path
            if src_mod and dst_mod and src_mod != dst_mod:
                fan_out[src_mod].add(dst_mod)
                fan_in[dst_mod].add(src_mod)

        all_mods = {m.file_path for m in self.result.modules}
        coupling: dict[str, CouplingMetrics] = {}
        for mod in all_mods:
            fi = len(fan_in.get(mod, set()))
            fo = len(fan_out.get(mod, set()))
            instability = fo / (fi + fo) if (fi + fo) > 0 else 0.0
            coupling[mod] = CouplingMetrics(fan_in=fi, fan_out=fo, instability=instability)

        self.result.module_coupling = coupling
        return coupling

    # ── Circular dependency detection ────────────────────────────

    def detect_circular_dependencies(self) -> list[list[str]]:
        """Detect cycles between modules (files) using DFS, with deduplication."""
        adj: dict[str, set[str]] = defaultdict(set)
        for edge in self.result.edges:
            src = edge.caller.file_path
            dst = edge.callee.file_path
            if src and dst and src != dst:
                adj[src].add(dst)

        raw_cycles: list[list[str]] = []
        visited: set[str] = set()
        on_stack: set[str] = set()
        path: list[str] = []

        def dfs(node: str) -> None:
            visited.add(node)
            on_stack.add(node)
            path.append(node)

            for neighbor in adj.get(node, set()):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in on_stack:
                    idx = path.index(neighbor)
                    cycle = path[idx:]  # don't append neighbor again
                    raw_cycles.append(cycle)

            path.pop()
            on_stack.remove(node)

        for node in adj:
            if node not in visited:
                dfs(node)

        # Deduplicate: normalize each cycle by rotating to smallest element
        seen: set[tuple[str, ...]] = set()
        unique: list[list[str]] = []
        for cycle in raw_cycles:
            normalized = _normalize_cycle(cycle)
            key = tuple(normalized)
            if key not in seen:
                seen.add(key)
                # Display with closing node for readability: A -> B -> C -> A
                unique.append(normalized + [normalized[0]])

        self.result.circular_dependencies = unique
        return unique

    # ── Unused import detection ───────────────────────────────────

    def detect_unused_imports(self) -> list[ImportInfo]:
        """Find imports whose imported names are never referenced in call edges or function/class names."""
        unused: list[ImportInfo] = []

        for mod in self.result.modules:
            if not mod.imports:
                continue

            # Collect all names defined or referenced in this file
            used_names: set[str] = set()
            for func in mod.functions:
                used_names.add(func.name)
            for cls in mod.classes:
                used_names.add(cls.name)
                for base in cls.bases:
                    used_names.add(base.split(".")[-1])
                for meth in cls.methods:
                    used_names.add(meth.name)
            for var in mod.variables:
                used_names.add(var.name)

            # Collect callee names from edges originating in this file
            for edge in self.result.edges:
                if edge.caller.file_path == mod.file_path:
                    used_names.add(edge.callee.name)
                    # Also add full qualified parts
                    for part in edge.callee.qualified_name.split("."):
                        used_names.add(part)

            for imp in mod.imports:
                if not imp.names:
                    # Bare import (import X) — check if module name is used
                    mod_base = imp.module.split(".")[-1].split("/")[-1]
                    alias_name = imp.alias
                    if mod_base not in used_names and (not alias_name or alias_name not in used_names):
                        unused.append(imp)
                else:
                    # from X import a, b — report only the unused imported names
                    if imp.names == ["*"]:
                        continue  # star imports are always "used"
                    unused_names = [name for name in imp.names if name not in used_names]
                    if unused_names:
                        unused.append(replace(imp, names=unused_names))

        self.result.unused_imports = unused
        return unused

    # ── Class hierarchy analysis ────────────────────────────────

    def build_class_hierarchy(self) -> dict[str, list[str]]:
        """Build a parent -> [children] hierarchy map from all parsed classes."""
        hierarchy: dict[str, list[str]] = {}

        for mod in self.result.modules:
            for cls in mod.classes:
                # Ensure every class has an entry
                if cls.qualified_name not in hierarchy:
                    hierarchy[cls.qualified_name] = []

                for base in cls.bases:
                    # Try to find the base class in the index
                    base_qn = self._resolve_base_class(base)
                    if base_qn not in hierarchy:
                        hierarchy[base_qn] = []
                    hierarchy[base_qn].append(cls.qualified_name)

        for base_name, children in hierarchy.items():
            hierarchy[base_name] = sorted(set(children))

        self.result.class_hierarchy = hierarchy
        return hierarchy

    def _resolve_base_class(self, base_name: str) -> str:
        """Try to resolve a base class name to its qualified name."""
        for mod in self.result.modules:
            for cls in mod.classes:
                if cls.name == base_name:
                    return cls.qualified_name
                if cls.qualified_name.endswith(f".{base_name}"):
                    return cls.qualified_name
        return base_name  # unresolved — use as-is

    # ── Complexity distribution ──────────────────────────────────

    def compute_complexity_distribution(self) -> dict[str, int]:
        """Bucket functions by complexity ranges."""
        buckets = {"low (1-5)": 0, "medium (6-10)": 0, "high (11-20)": 0, "very_high (21+)": 0}

        for mod in self.result.modules:
            for func in mod.functions:
                _classify(func.complexity, buckets)
            for cls in mod.classes:
                for meth in cls.methods:
                    _classify(meth.complexity, buckets)

        self.result.complexity_distribution = buckets
        return buckets


def _normalize_cycle(cycle: list[str]) -> list[str]:
    """Rotate cycle so that the lexicographically smallest element comes first."""
    if not cycle:
        return cycle
    min_idx = cycle.index(min(cycle))
    return cycle[min_idx:] + cycle[:min_idx]


def _classify(complexity: int, buckets: dict[str, int]) -> None:
    if complexity <= 5:
        buckets["low (1-5)"] += 1
    elif complexity <= 10:
        buckets["medium (6-10)"] += 1
    elif complexity <= 20:
        buckets["high (11-20)"] += 1
    else:
        buckets["very_high (21+)"] += 1


_ENTRYPOINT_NAMES = frozenset({
    "main", "__init__", "__main__", "setup", "teardown",
    "run", "execute", "start", "serve", "init", "configure",
    "Main", "Run", "Execute", "Start", "Serve", "Init",
})

_ENTRYPOINT_PREFIXES = ("test_", "Test", "bench_", "Bench", "example_", "Example")


def _is_likely_entrypoint(func: FunctionInfo) -> bool:
    name = func.name
    if name in _ENTRYPOINT_NAMES:
        return True
    if any(name.startswith(p) for p in _ENTRYPOINT_PREFIXES):
        return True
    # Python dunder methods
    if name.startswith("__") and name.endswith("__"):
        return True
    # Decorated functions that are likely endpoints (routes, handlers)
    for dec in func.decorators:
        if any(kw in dec.lower() for kw in ("route", "endpoint", "handler", "api", "get", "post", "put", "delete")):
            return True
    return False
