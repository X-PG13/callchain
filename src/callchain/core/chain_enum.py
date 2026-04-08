"""Call chain enumeration via DFS on the call graph."""

from __future__ import annotations

from collections import Counter, defaultdict

from callchain.core.models import CallChain, CallEdge, FunctionInfo


class ChainEnumerator:
    """Enumerate call chains from a list of resolved edges."""

    def __init__(
        self,
        edges: list[CallEdge],
        max_depth: int = 20,
        max_chains: int = 50_000,
        only_cross_file: bool = False,
        restrict_dir: str | None = None,
    ):
        self.edges = edges
        self.max_depth = max_depth
        self.max_chains = max_chains
        self.only_cross_file = only_cross_file
        self.restrict_dir = restrict_dir

        # Build adjacency from qualified_name -> [FunctionInfo]
        self.adj: dict[str, list[FunctionInfo]] = defaultdict(list)
        self.in_degree: Counter[str] = Counter()
        self.node_map: dict[str, FunctionInfo] = {}

        for edge in edges:
            cqn = edge.caller.qualified_name
            eqn = edge.callee.qualified_name
            self.adj[cqn].append(edge.callee)
            self.in_degree[eqn] += 1
            self.node_map[cqn] = edge.caller
            self.node_map[eqn] = edge.callee

    def enumerate(self) -> list[CallChain]:
        """Run DFS enumeration and return discovered chains."""
        starts = self._find_starts()
        chains: list[CallChain] = []
        seen: set[tuple[str, ...]] = set()
        written = 0

        for start_qn in starts:
            if written >= self.max_chains:
                break
            start = self.node_map[start_qn]
            self._dfs(start, [start], set(), 1, chains, seen)
            written = len(chains)

        return chains

    def enumerate_with_summary(self) -> dict:
        """Enumerate chains and return a summary dict."""
        chains = self.enumerate()

        length_hist: Counter[int] = Counter()
        cross_hist: Counter[int] = Counter()
        best_chain: CallChain | None = None
        best_cross = -1
        best_len = -1

        for chain in chains:
            length_hist[chain.length] += 1
            cross = chain.cross_file_transitions
            cross_hist[cross] += 1
            if cross > best_cross or (cross == best_cross and chain.length > best_len):
                best_cross = cross
                best_len = chain.length
                best_chain = chain

        return {
            "chains": chains,
            "chains_written": len(chains),
            "length_hist": dict(length_hist),
            "cross_hist": dict(cross_hist),
            "longest_chain": best_chain,
            "total_edges": len(self.edges),
            "graph_nodes": len(self.node_map),
        }

    # ── Internal ─────────────────────────────────────────────────

    def _find_starts(self) -> list[str]:
        """Find starting nodes (in-degree 0, optionally filtered by restrict_dir)."""
        all_callers = set(self.adj.keys())
        starts = [qn for qn in all_callers if self.in_degree[qn] == 0 and self._in_restrict(qn)]

        if not starts and self.restrict_dir:
            starts = [qn for qn in all_callers if self._in_restrict(qn)]

        if not starts:
            starts = list(all_callers)

        return starts

    def _in_restrict(self, qn: str) -> bool:
        if not self.restrict_dir:
            return True
        func = self.node_map.get(qn)
        if not func:
            return True
        return func.file_path.startswith(self.restrict_dir)

    def _dfs(
        self,
        node: FunctionInfo,
        path: list[FunctionInfo],
        visited: set[str],
        depth: int,
        chains: list[CallChain],
        seen: set[tuple[str, ...]],
    ) -> None:
        if len(chains) >= self.max_chains:
            return

        visited.add(node.qualified_name)
        neighbors = self.adj.get(node.qualified_name, [])
        is_leaf = len(neighbors) == 0
        limit_reached = depth >= self.max_depth

        if is_leaf or limit_reached:
            key = tuple(n.qualified_name for n in path)
            if key not in seen:
                chain = CallChain(nodes=list(path))
                if not self.only_cross_file or chain.cross_file_transitions > 0:
                    chains.append(chain)
                seen.add(key)

        if depth < self.max_depth:
            for neighbor in neighbors:
                if neighbor.qualified_name in visited:
                    continue
                path.append(neighbor)
                self._dfs(neighbor, path, visited, depth + 1, chains, seen)
                path.pop()

        visited.remove(node.qualified_name)
