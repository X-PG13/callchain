#!/usr/bin/env python3
"""
整合 PyCG（scalpel_callgraph.json 边）+ Jedi（符号解析），为每条调用边/调用链输出函数的文件路径与行号。

用法示例：
  基本：
    python3 scripts/pycg_jedi_positions.py \
      --callgraph scalpel_callgraph.json \
      --structured output_all.json \
      --output scalpel_callgraph_positions.json

  指定入口（生成调用链，包含每个节点的 file+line）：
    python3 scripts/pycg_jedi_positions.py \
      --callgraph scalpel_callgraph.json \
      --structured output_all.json \
      --output scalpel_callgraph_positions.json \
      --entry call_chain_analyzer.main --entry code_remover.main \
      --max-depth 4

说明：
  - 优先使用结构化提取（output_all.json）中的索引；
  - 对未解析或不唯一项，使用 Jedi 项目搜索进行补充解析；
  - 入口为空时，仅输出增强的 edges；有入口时，额外输出 chains。
"""

import argparse
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import jedi
except Exception:
    jedi = None


def load_json(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ 已保存: {path.resolve()}")


def module_path_from_file(file_path: str) -> str:
    # e.g. "wordfreq/language_info.py" -> "wordfreq.language_info"
    if not file_path:
        return ''
    if file_path.endswith('.py'):
        file_path = file_path[:-3]
    return file_path.replace('/', '.').replace('\\', '.')


def file_path_from_module(project_root: Path, module: str) -> Optional[Path]:
    """尝试从模块名推导文件路径（简化版）。"""
    rel = Path(*module.split('.')).with_suffix('.py')
    candidate = project_root / rel
    return candidate if candidate.exists() else None


def build_index(structured: Dict):
    by_module_func: Dict[str, Dict] = {}
    by_simple_func: Dict[str, List[Dict]] = {}

    for f in structured.get('functions', []):
        module = module_path_from_file(f.get('file_path', ''))
        key = f"{module}.{f.get('name')}" if module else f.get('name')
        by_module_func[key] = f
        by_simple_func.setdefault(f.get('name'), []).append(f)

    by_method_full: Dict[str, Dict] = {}
    by_method_simple: Dict[str, List[Dict]] = {}
    for cls in structured.get('classes', []):
        module = module_path_from_file(cls.get('file_path', ''))
        class_name = cls.get('name')
        for m in cls.get('methods', []):
            enriched = {
                'name': m.get('name'),
                'signature': m.get('signature'),
                'file_path': cls.get('file_path'),
                'line_number': m.get('line_number'),
                'docstring': m.get('docstring'),
                'decorators': m.get('decorators', []),
                'is_async': m.get('is_async'),
                'class_name': class_name,
            }
            full_key = f"{module}.{class_name}.{m.get('name')}" if module else f"{class_name}.{m.get('name')}"
            by_method_full[full_key] = enriched
            by_method_simple.setdefault(m.get('name'), []).append(enriched)

    return {
        'by_module_func': by_module_func,
        'by_simple_func': by_simple_func,
        'by_method_full': by_method_full,
        'by_method_simple': by_method_simple,
    }


def _normalize_path(fp: Optional[str], project_root: Path) -> str:
    """Normalize file path to absolute path under project_root and unify separators."""
    if not fp:
        return ""
    p = Path(fp)
    try:
        # If inside project, resolve to absolute under project_root
        if not p.is_absolute():
            p = (project_root / p).resolve()
        else:
            p = p.resolve()
    except Exception:
        # Fallback to joining with project_root if resolution fails
        p = (project_root / fp).resolve()
    return str(p).replace("\\", "/")


def to_target_shape(meta: Dict, project_root: Path) -> Dict:
    return {
        'name': meta.get('name'),
        'signature': meta.get('signature'),
        'file_path': _normalize_path(meta.get('file_path'), project_root),
        'line_number': meta.get('line_number'),
        'docstring': meta.get('docstring'),
        'decorators': meta.get('decorators', []),
        'is_async': bool(meta.get('is_async')) if meta.get('is_async') is not None else False,
    }


def jedi_search(project: 'jedi.Project', identifier: str) -> Optional[Dict]:
    """使用 Jedi 项目搜索并返回最匹配的条目（带 file+line）。"""
    if project is None:
        return None
    try:
        names = project.search(identifier)
        # 选择最合理的定义：优先可解析到 Python 文件且有行号
        for n in names:
            module_path = getattr(n, 'module_path', None)
            line = getattr(n, 'line', None)
            if module_path and isinstance(module_path, Path) and line:
                return {
                    'name': n.name,
                    'signature': None,
                    'file_path': str(module_path.relative_to(project._path)) if project._path and module_path.is_relative_to(project._path) else str(module_path),
                    'line_number': line,
                    'docstring': None,
                    'decorators': [],
                    'is_async': False,
                }
    except Exception:
        return None
    return None


def resolve_identifier(name: str, index: Dict, project_root: Path, project: Optional['jedi.Project']) -> Optional[Dict]:
    # 完整函数匹配（module.func）
    if name in index['by_module_func']:
        return index['by_module_func'][name]

    # 完整方法匹配（module.Class.method）
    if name in index['by_method_full']:
        return index['by_method_full'][name]

    # 按模块推测文件，并用 AST 索引模糊匹配（fallback 简化为 Jedi 搜索）
    # 先尝试 Jedi 搜索完整标识符
    jmeta = jedi_search(project, name)
    if jmeta:
        return jmeta

    # 再尝试简单名搜索
    simple = name.split('.')[-1]
    # 结构化简单名唯一
    candidates = index['by_simple_func'].get(simple, [])
    if len(candidates) == 1:
        return candidates[0]
    m_candidates = index['by_method_simple'].get(simple, [])
    if len(m_candidates) == 1:
        return m_candidates[0]

    # Jedi 简单名兜底
    jmeta2 = jedi_search(project, simple)
    if jmeta2:
        return jmeta2

    return None


def build_graph(edges: List[List[str]]):
    adj: Dict[str, List[str]] = {}
    for u, v in edges:
        adj.setdefault(u, []).append(v)
    return adj


def dfs_chains(adj: Dict[str, List[str]], start: str, max_depth: int) -> List[List[str]]:
    chains: List[List[str]] = []
    stack: List[Tuple[str, List[str]]] = [(start, [start])]
    visited_depth: Dict[str, int] = {}
    while stack:
        node, path = stack.pop()
        if len(path) > max_depth:
            continue
        has_child = False
        for nxt in adj.get(node, []):
            has_child = True
            new_path = path + [nxt]
            stack.append((nxt, new_path))
        if not has_child:
            chains.append(path)
    return chains


def main():
    parser = argparse.ArgumentParser(description='PyCG+Jedi 解析位置，输出边与调用链的文件+行号')
    parser.add_argument('--callgraph', default='scalpel_callgraph.json', help='Scalpel 生成的调用图 JSON')
    parser.add_argument('--structured', default='output_all.json', help='code_extractor 生成的结构化输出 JSON')
    parser.add_argument('--output', default='scalpel_callgraph_positions.json', help='输出文件名')
    parser.add_argument('--project-root', default='.', help='项目根目录，用于 Jedi 搜索与模块路径推测')
    parser.add_argument('--entry', action='append', default=None, help='入口函数（可重复），用于输出调用链')
    parser.add_argument('--max-depth', type=int, default=5, help='调用链最大深度')
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    cg = load_json(Path(args.callgraph))
    structured = load_json(Path(args.structured))
    index = build_index(structured)

    project = None
    if jedi is not None:
        try:
            project = jedi.Project(path=project_root)
        except Exception:
            project = None

    edges = cg.get('edges', [])

    detailed_edges: List[Dict] = []
    unresolved: List[Tuple[str, str]] = []

    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 2:
            continue
        caller_name, callee_name = edge

        caller_meta = resolve_identifier(caller_name, index, project_root, project)
        callee_meta = resolve_identifier(callee_name, index, project_root, project)

        if caller_meta and callee_meta:
            detailed_edges.append({
                'caller': to_target_shape(caller_meta, project_root),
                'callee': to_target_shape(callee_meta, project_root),
                'raw': edge,
            })
        else:
            unresolved.append((caller_name, callee_name))

    output = {
        'edges': detailed_edges,
        'unresolved': unresolved,
        'stats': {
            'total_edges': len(edges),
            'resolved_edges': len(detailed_edges),
            'unresolved_edges': len(unresolved),
        }
    }

    # 调用链（可选）
    if args.entry:
        adj = build_graph(edges)
        chains_out: List[Dict] = []
        for entry in args.entry:
            chains = dfs_chains(adj, entry, args.max_depth)
            for ch in chains:
                nodes = []
                for ident in ch:
                    meta = resolve_identifier(ident, index, project_root, project)
                    nodes.append({
                        'identifier': ident,
                        'meta': to_target_shape(meta, project_root) if meta else None,
                    })
                chains_out.append({
                    'entry': entry,
                    'length': len(ch),
                    'nodes': nodes,
                })
        output['chains'] = chains_out

    save_json(output, Path(args.output))


if __name__ == '__main__':
    main()