#!/usr/bin/env python3
import argparse
import importlib
import json
import os
import sys
from pathlib import Path

def _ensure_pycg_importable():
    try:
        import pycg  # noqa: F401
        return
    except Exception:
        # 某些 PyPI 版本将包名安装为 "PyCG" 而不是 "pycg"
        try:
            pycg_pkg = importlib.import_module("PyCG")
            sys.modules["pycg"] = pycg_pkg
            for submodule in ("formats", "machinery", "processing", "utils"):
                sys.modules[f"pycg.{submodule}"] = importlib.import_module(f"PyCG.{submodule}")
            sys.modules["pycg.pycg"] = importlib.import_module("PyCG.pycg")
            return
        except Exception:
            pass
        # 尝试手工加载 site-packages 下的 pycg
        import site
        import importlib.util
        for sp in site.getsitepackages() + [site.getusersitepackages()]:
            pkg_path = Path(sp) / "pycg" / "__init__.py"
            if pkg_path.exists():
                try:
                    spec = importlib.util.spec_from_file_location("pycg", str(pkg_path))
                    m = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(m)  # type: ignore[attr-defined]
                    sys.modules["pycg"] = m
                    return
                except Exception as e:
                    print(f"[WARN] 手工加载 pycg 失败: {e}")
        # 最终失败
        raise ImportError("无法导入 pycg，请确保安装成功: pip install pycg")


_ensure_pycg_importable()
USING_PYCG_RAW = False
try:
    # 优先使用 scalpel 的封装（适配新版 pycg）
    from scalpel.call_graph.pycg import CallGraphGenerator, formats  # type: ignore
except Exception:
    # 回退：直接使用 pycg 的原始接口（适配老版 0.0.3）
    try:
        from pycg.pycg import CallGraphGenerator  # type: ignore
        from pycg import formats, utils  # type: ignore
        USING_PYCG_RAW = True
        print("[WARN] 使用 pycg 原始接口（未通过 scalpel 封装）")
    except Exception as e:
        print("[ERROR] 无法导入 pycg 或其格式化器: {}".format(e))
        print("请先运行: pip install python-scalpel pycg")
        sys.exit(1)


def discover_py_files(root: Path) -> list[str]:
    files = []
    for p in root.rglob("*.py"):
        # 排除虚拟环境、隐藏目录和常见不需要分析的目录
        rel = p.relative_to(root)
        parts = rel.parts
        if any(part.startswith(".") for part in parts):
            continue
        if any(part in {"venv", ".venv", "env", "node_modules", "__pycache__"} for part in parts):
            continue
        files.append(str(p))
    return files


def main():
    parser = argparse.ArgumentParser(
        description="使用 Scalpel(PyCG) 生成 Python 项目调用图并输出 JSON"
    )
    parser.add_argument(
        "--package",
        type=str,
        default=str(Path.cwd()),
        help="包根目录（用于计算模块名），默认当前目录",
    )
    parser.add_argument(
        "--entry",
        type=str,
        nargs="*",
        help="入口文件列表（相对或绝对路径），不提供则自动发现所有 .py 文件",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="scalpel_callgraph.json",
        help="输出 JSON 文件路径，默认 scalpel_callgraph.json",
    )
    args = parser.parse_args()

    package_root = Path(args.package).resolve()
    if not package_root.exists():
        print(f"[ERROR] package 路径不存在: {package_root}")
        sys.exit(2)

    if args.entry and len(args.entry) > 0:
        entry_points = [str(Path(e).resolve()) for e in args.entry]
    else:
        print("[INFO] 未提供 --entry，自动发现入口文件（所有 .py）...")
        entry_points = discover_py_files(package_root)
        if not entry_points:
            print("[ERROR] 未在包目录下发现任何 .py 文件")
            sys.exit(3)

    print(f"[INFO] 包根目录: {package_root}")
    print(f"[INFO] 入口文件数: {len(entry_points)}")

    # 生成调用图：根据是否使用原始 pycg 接口选择不同初始化参数
    if USING_PYCG_RAW:
        # 在 pycg 0.0.3 中需要提供 max_iter 与 operation
        # max_iter=-1 表示直到收敛；operation 使用 CALL_GRAPH_OP
        cg_generator = CallGraphGenerator(entry_points, str(package_root), -1, utils.constants.CALL_GRAPH_OP)
    else:
        cg_generator = CallGraphGenerator(entry_points, str(package_root))
    # 规避 pycg 在 Python 3.12 下 import 钩子导致的 ImportManagerError
    try:
        cg_generator.import_manager.install_hooks = lambda: None  # type: ignore
        cg_generator.import_manager.remove_hooks = lambda: None  # type: ignore
    except Exception:
        pass
    cg_generator.analyze()

    # 使用 Simple 格式化器生成 JSON 结构（包含节点与边）
    # 注意：Simple 期望传入 generator 本身，而非其 output 字典
    formatter = formats.Simple(cg_generator)
    results = formatter.generate()

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 如果 Simple 返回空，尝试使用原始 edges 回退输出
    edges = results.get("edges") if isinstance(results, dict) else None
    if not edges:
        try:
            raw_edges = cg_generator.output_edges()
        except Exception:
            raw_edges = []
        results = {
            "internal_modules": list(getattr(cg_generator, "output_internal_mods", lambda: [])() or []),
            "external_modules": list(getattr(cg_generator, "output_external_mods", lambda: [])() or []),
            "edges": raw_edges,
        }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 辅助输出摘要
    nodes = results.get("nodes", {}) if isinstance(results, dict) else {}
    edges = results.get("edges", []) if isinstance(results, dict) else []
    print(f"[DONE] 已输出调用图: {out_path}")
    if nodes:
        print(f"[SUMMARY] 节点数: {len(nodes)}，边数: {len(edges)}")
    else:
        print(f"[SUMMARY] 边数: {len(edges)}（raw edges 回退）")
    # 打印前若干条边示例
    for i, e in enumerate(edges[:10]):
        print(f"  edge[{i}]: {e}")


if __name__ == "__main__":
    main()
