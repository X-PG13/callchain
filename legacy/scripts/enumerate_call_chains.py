import argparse
import json
import os
from collections import defaultdict, Counter


def unify_sep(p: str) -> str:
    """将路径中的分隔符统一为正斜杠。"""
    return (p or "").replace("\\", "/")


def normalize_fname(path: str) -> str:
    if not path:
        return ""
    return os.path.basename(path)


def normalize_for_filter(fp: str, restrict_dir: str | None = None) -> str:
    if not fp:
        return ""
    fp_norm = unify_sep(fp)
    if restrict_dir:
        r = unify_sep(restrict_dir)
        # 统一到 restrict_dir 前缀，避免裸文件名误判
        if fp_norm.startswith(r):
            return fp_norm
        if "/" not in fp_norm and fp_norm.endswith(".py"):
            return f"{r}{fp_norm}"
        return fp_norm
    # 无目录限制时直接返回
    return fp_norm


def load_edges(input_path: str):
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    edges = data.get("edges", [])
    return edges, len(edges)


def build_graph(edges, restrict_dir: str | None = None):
    adj = defaultdict(set)
    indeg = Counter()
    nodes = set()

    for e in edges:
        caller = e.get("caller", {})
        callee = e.get("callee", {})
        if not caller or not callee:
            continue
        u_fp = caller.get("file_path") or ""
        v_fp = callee.get("file_path") or ""
        # 节点包含行号，便于后续删除定位
        u = (u_fp, caller.get("name"), caller.get("line_number"))
        v = (v_fp, callee.get("name"), callee.get("line_number"))
        if u == ("", None) or v == ("", None):
            continue
        if v not in adj[u]:
            adj[u].add(v)
            indeg[v] += 1
        nodes.add(u)
        nodes.add(v)
    return adj, indeg, nodes


def enumerate_chains(adj, indeg, max_depth: int, max_chains: int, only_cross_file: bool, out_path: str,
                     restrict_dir: str | None = None,
                     head_in_restrict_rest_out: bool = False,
                     head_in_restrict_has_outside: bool = False,
                     min_outside_ratio: float = 0.0,
                     tail_must_be_outside: bool = False):
    # Start nodes: nodes with in-degree == 0, optionally restricted to a directory prefix
    def in_restrict(fp: str) -> bool:
        if not restrict_dir:
            return True
        fp_norm = normalize_for_filter(fp or "", restrict_dir)
        return fp_norm.startswith(restrict_dir)

    def out_of_restrict(fp: str) -> bool:
        if not restrict_dir:
            return True
        fp_norm = normalize_for_filter(fp or "", restrict_dir)
        return not fp_norm.startswith(restrict_dir)

    starts = [n for n in adj.keys() if indeg[n] == 0 and in_restrict(n[0])]
    if not starts and restrict_dir:
        # If no in-degree-0 nodes in restrict, start from any node within restrict
        starts = [n for n in adj.keys() if in_restrict(n[0])]
    if not starts:
        # Fallback: use all nodes to ensure coverage
        starts = list(adj.keys())

    written = 0
    length_hist = Counter()
    cross_hist = Counter()
    seen_paths = set()
    best = {
        "cross": -1,
        "length": -1,
        "record": None,
        "index": -1,
    }

    def path_key(path):
        return tuple(path)

    def compute_cross(path):
        cross = 0
        prev = None
        for fp, _name, _line in path:
            curr = normalize_fname(fp)
            if prev is not None and curr and prev and curr != prev:
                cross += 1
            prev = curr
        return cross

    with open(out_path, "w", encoding="utf-8") as out_f:
        def dfs(node, path, visited, depth):
            nonlocal written
            if written >= max_chains:
                return
            path.append(node)
            visited.add(node)

            # If leaf or depth limit reached, emit path
            leaf = len(adj.get(node, [])) == 0
            limit_reached = depth >= max_depth
            if leaf or limit_reached:
                key = path_key(path)
                if key not in seen_paths:
                    cross = compute_cross(path)
                    if (not only_cross_file) or (cross > 0):
                        # 可选筛选：严格或松弛 + 比例/尾部条件
                        if restrict_dir:
                            head_fp = path[0][0]
                            tail_fps = [fp for fp, _n, _l in path[1:]]
                            # 严格：链头在 restrict，其余全部在 restrict 之外
                            if head_in_restrict_rest_out:
                                if not (in_restrict(head_fp) and all(out_of_restrict(fp) for fp in tail_fps)):
                                    seen_paths.add(key)
                                    return
                            # 松弛：链头在 restrict，且链中至少包含一个非 restrict 节点
                            if head_in_restrict_has_outside:
                                if not (in_restrict(head_fp) and any(out_of_restrict(fp) for fp in tail_fps)):
                                    seen_paths.add(key)
                                    return
                            # 比例筛选：整条链在 restrict 之外的节点比例至少为 min_outside_ratio
                            if min_outside_ratio > 0.0:
                                total_nodes = len(path)
                                outside_nodes = sum(1 for fp, _n, _l in path if out_of_restrict(fp))
                                if total_nodes == 0 or (outside_nodes / total_nodes) < min_outside_ratio:
                                    seen_paths.add(key)
                                    return
                            # 尾部条件：链底部必须在 restrict 外
                            if tail_must_be_outside:
                                tail_fp = path[-1][0]
                                if not out_of_restrict(tail_fp):
                                    seen_paths.add(key)
                                    return
                        # Write one chain as JSONL line
                        chain = []
                        for fp, name, line in path:
                            base = os.path.splitext(os.path.basename(fp or ""))[0]
                            raw_label = f"{base}.{name}" if base and name else name or base or ""
                            chain.append({"raw": raw_label, "file_path": fp or "", "line_number": line})
                        record = {
                            "length": len(path),
                            "cross_file_transitions": cross,
                            "chain": chain,
                        }
                        out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                        written += 1
                        length_hist[len(path)] += 1
                        cross_hist[cross] += 1
                        # 记录最佳（跨文件跳转优先，其次长度）
                        if cross > best["cross"] or (cross == best["cross"] and len(path) > best["length"]):
                            best["cross"] = cross
                            best["length"] = len(path)
                            best["record"] = record
                            best["index"] = written - 1
                    seen_paths.add(key)

            # Continue DFS if depth limit not reached
            if depth < max_depth:
                for nbr in adj.get(node, []):
                    if nbr in visited:
                        continue
                    dfs(nbr, path, visited, depth + 1)

            path.pop()
            visited.remove(node)

        for s in starts:
            if written >= max_chains:
                break
            dfs(s, [], set(), 1)

    return {
        "chains_written": written,
        "length_hist": dict(length_hist),
        "cross_hist": dict(cross_hist),
        "starts": len(starts),
        "longest_chain_index": best["index"],
        "longest_chain": best["record"],
    }


def main():
    parser = argparse.ArgumentParser(description="Enumerate call chains from position-enhanced PyCG edges")
    parser.add_argument("--input", default="scalpel_callgraph_positions_fastapi_pycg.json", help="Path to position-enhanced call graph JSON")
    parser.add_argument("--output", default="all_call_chains.jsonl", help="Output JSONL file with chains")
    parser.add_argument("--summary", default="all_call_chains_summary.json", help="Output summary JSON path")
    parser.add_argument("--restrict-dir", default=None, help="Restrict nodes to a directory prefix like 'fastapi/' or 'test_repo/'")
    parser.add_argument("--max-depth", type=int, default=20, help="Max depth for simple path enumeration")
    parser.add_argument("--max-chains", type=int, default=50000, help="Global cap of emitted chains")
    parser.add_argument("--only-cross-file", action="store_true", help="Emit only chains with at least one cross-file transition")
    parser.add_argument("--head-in-restrict-rest-out", action="store_true", help="Require chain head in restrict and all subsequent nodes outside restrict")
    parser.add_argument("--head-in-restrict-has-outside", action="store_true", help="Require chain head in restrict and chain contains at least one node outside restrict")
    parser.add_argument("--min-outside-ratio", type=float, default=0.0, help="Minimum ratio of nodes outside restrict across the whole chain (0~1)")
    parser.add_argument("--tail-must-be-outside", action="store_true", help="Require the chain tail to be outside restrict")
    args = parser.parse_args()

    edges, total_edges = load_edges(args.input)
    # 规范 restrict_dir，确保以斜杠结尾
    restrict = unify_sep(args.restrict_dir) if args.restrict_dir else None
    if restrict and not restrict.endswith("/"):
        restrict = restrict + "/"
    adj, indeg, nodes = build_graph(edges, restrict_dir=restrict)

    summary = enumerate_chains(
        adj, indeg, args.max_depth, args.max_chains, args.only_cross_file, args.output,
        restrict, args.head_in_restrict_rest_out, args.head_in_restrict_has_outside,
        args.min_outside_ratio, args.tail_must_be_outside
    )
    summary.update({
        "total_edges": total_edges,
        "graph_nodes": len(nodes)
    })

    with open(args.summary, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"Wrote chains to {args.output}")
    print(f"Summary saved to {args.summary}")


if __name__ == "__main__":
    main()