import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime


def find_tests_dir(workspace_root: Path, library_subdir: str | None, tests_hint: str | None) -> Path:
    base = workspace_root
    if library_subdir:
        base = workspace_root / library_subdir
    if tests_hint:
        cand = base / tests_hint
        if cand.is_dir():
            return cand
    # fallbacks: common names
    for name in ("tests", "test", "testing"):
        cand = base / name
        if cand.is_dir():
            return cand
    raise FileNotFoundError(f"未找到 tests 目录，请提供 --tests-dir 或检查 {base}")


def run_enumerate(workspace_root: Path, input_callgraph: Path, restrict_dir_rel: str,
                  output_jsonl: Path, summary_json: Path, max_depth: int = 40,
                  only_cross_file: bool = True,
                  head_in_restrict_rest_out: bool = False,
                  head_in_restrict_has_outside: bool = True) -> None:
    tool_dir = Path(__file__).resolve().parent
    cmd = [
        sys.executable,
        str(tool_dir / "enumerate_call_chains.py"),
        "--input", str(input_callgraph),
        "--restrict-dir", restrict_dir_rel,
        "--output", str(output_jsonl),
        "--summary", str(summary_json),
        "--max-depth", str(max_depth),
    ]
    if only_cross_file:
        cmd.append("--only-cross-file")
    if head_in_restrict_rest_out:
        cmd.append("--head-in-restrict-rest-out")
    if head_in_restrict_has_outside:
        cmd.append("--head-in-restrict-has-outside")
    subprocess.run(cmd, check=True, cwd=str(workspace_root))


def compose_dotted_name(rel_file_path: str, raw_label: str) -> str:
    # rel_file_path like "tqdm/tqdm/std.py" -> "tqdm.tqdm.std"
    parts = rel_file_path.replace("\\", "/").split("/")
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    module = ".".join(parts)
    name_part = raw_label.split(".", 1)[1] if (raw_label and "." in raw_label) else raw_label
    return f"{module}.{name_part}" if name_part else module


def read_longest_chain(summary_json: Path) -> int:
    with open(summary_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "longest_chain_index" in data:
        return data["longest_chain_index"]
    # fallback: pick the max by length
    chains = data.get("chains", [])
    if not chains:
        raise ValueError("未在 summary 中找到 chains")
    return max(range(len(chains)), key=lambda i: len(chains[i]))


def read_chain_endpoints(chains_jsonl: Path, chain_index: int) -> tuple[dict, dict]:
    with open(chains_jsonl, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx == chain_index:
                chain = json.loads(line)
                nodes = chain.get("chain", chain)
                if not nodes:
                    raise ValueError("链为空")
                return nodes[0], nodes[-1]
    raise IndexError(f"链索引 {chain_index} 超出范围")


def run_delete(workspace_root: Path, chains_jsonl: Path, chain_index: int, repo_name: str,
               tests_rel: str | None, delete_policy: str) -> None:
    tool_dir = Path(__file__).resolve().parent
    cmd = [
        sys.executable,
        str(tool_dir / "delete_chain_ends.py"),
        "--chains-jsonl", str(chains_jsonl),
        "--chain-index", str(chain_index),
        "--project-root", str(workspace_root),
        "--repo", repo_name,
    ]
    if tests_rel:
        cmd.extend(["--tests-dir", tests_rel])
    if delete_policy:
        cmd.extend(["--delete-policy", delete_policy])
    subprocess.run(cmd, check=True, cwd=str(workspace_root))


def main():
    parser = argparse.ArgumentParser(description="从 tests 枚举调用链，选最长链并在副本仓库删除两端")
    parser.add_argument("--workspace-root", default="test_repo", help="工作区根目录，包含库及其 tests")
    parser.add_argument("--input-callgraph", default="scalpel_callgraph_positions_test_repo.json",
                        help="已计算的带位置的调用图 JSON（可为绝对路径或相对 workspace-root）")
    parser.add_argument("--library-subdir", default=None, help="库子目录（相对 workspace-root），例如 tqdm 或 wordfreq")
    parser.add_argument("--tests-dir", default=None, help="tests 子目录名（相对 library-subdir 或 workspace-root）")
    parser.add_argument("--max-depth", type=int, default=40)
    parser.add_argument("--output-jsonl", default="tmp_test_chains.jsonl")
    parser.add_argument("--summary-json", default="tmp_test_chains_summary.json")
    parser.add_argument("--only-cross-file", action="store_true", default=True)
    parser.add_argument("--repo-suffix", default=None, help="可选的仓库名后缀，默认使用时间戳避免重名")
    parser.add_argument("--delete-policy", default="delete_first_call_and_bottom",
                        choices=["delete_top_bottom", "delete_first_call_and_bottom"],
                        help="删除策略：删除链顶+底，或保留 test 顶层删除首个被调用函数+底层")
    parser.add_argument("--head-in-restrict-rest-out", action="store_true", default=False,
                        help="严格筛选：链头必须在 tests，其余节点全部不在 tests")
    parser.add_argument("--head-in-restrict-has-outside", action="store_true", default=True,
                        help="松弛筛选（默认）：链头在 tests，且链中至少包含一个非 tests 节点")

    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    input_callgraph_arg = Path(args.input_callgraph)
    input_callgraph = (input_callgraph_arg if input_callgraph_arg.is_absolute()
                       else (workspace_root / input_callgraph_arg))
    if not input_callgraph.exists():
        raise FileNotFoundError(f"未找到调用图文件: {input_callgraph}")

    tests_dir = find_tests_dir(workspace_root, args.library_subdir, args.tests_dir)
    # restrict dir must be relative to workspace_root and match paths in callgraph
    restrict_rel = os.path.relpath(tests_dir, workspace_root)

    output_jsonl = workspace_root / args.output_jsonl
    summary_json = workspace_root / args.summary_json

    run_enumerate(workspace_root, input_callgraph, restrict_rel, output_jsonl, summary_json,
                  max_depth=args.max_depth, only_cross_file=args.only_cross_file,
                  head_in_restrict_rest_out=args.head_in_restrict_rest_out,
                  head_in_restrict_has_outside=args.head_in_restrict_has_outside)

    # 读取汇总，若无匹配链则提示退出
    with open(summary_json, "r", encoding="utf-8") as sf:
        summary_data = json.load(sf)
    chain_index = summary_data.get("longest_chain_index", -1)
    chains_written = summary_data.get("chains_written", 0)
    if chain_index is None or chain_index < 0 or chains_written == 0:
        print("未找到满足筛选条件的调用链（链头在 tests，且链中至少包含一个非 tests 节点）。")
        print("建议：确认测试是否直接调用库代码；或去掉 --head-in-restrict-has-outside 进一步放宽条件。")
        return
    # 读取链并根据策略确定命名所用的两个端点（若保留test顶层则用第二个作为上端）
    with open(output_jsonl, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            if idx == chain_index:
                chain = json.loads(line)
                nodes = chain.get("chain", chain)
                if not nodes:
                    raise ValueError("链为空")
                # 选择用于仓库命名的上、下端点
                def is_test_node(ep: dict) -> bool:
                    fp = str(ep.get("file_path", "")).replace("\\", "/")
                    return fp.startswith(restrict_rel + "/") or fp.startswith("tests/") or "/tests/" in fp
                top_idx = 0
                if args.delete_policy == "delete_first_call_and_bottom" and len(nodes) >= 2 and is_test_node(nodes[0]):
                    top_idx = 1
                top, bottom = nodes[top_idx], nodes[-1]
                break

    # repo name uses dotted summary from top and bottom
    dotted_top = compose_dotted_name(top["file_path"], top["raw"])
    dotted_bottom = compose_dotted_name(bottom["file_path"], bottom["raw"])
    suffix = args.repo_suffix or datetime.now().strftime("%Y%m%d-%H%M%S")
    repo_name = f"deleted_{dotted_top}__{dotted_bottom}_{suffix}"

    run_delete(workspace_root, output_jsonl, chain_index, repo_name, restrict_rel, args.delete_policy)

    print("完成：")
    print(f"  tests 目录: {tests_dir}")
    print(f"  最长链索引: {chain_index}")
    print(f"  新仓库: {repo_name}")
    print(f"  日志: {repo_name}/deletion_log.json")


if __name__ == "__main__":
    main()