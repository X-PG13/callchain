"""Graphviz DOT output."""

from __future__ import annotations

from pathlib import Path

from callchain.core.models import AnalysisResult


def _dot_escape(text: str) -> str:
    """Escape text for use inside DOT double-quoted strings."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def write_dot(result: AnalysisResult, output_path: str | Path, max_edges: int = 2000) -> Path:
    """Write call graph as a Graphviz DOT file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "digraph callgraph {",
        '    rankdir=LR;',
        '    node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];',
        '    edge [fontsize=8];',
    ]

    # Group nodes by file (subgraphs)
    file_nodes: dict[str, set[str]] = {}
    node_ids: dict[str, str] = {}
    counter = 0

    def get_id(qname: str) -> str:
        nonlocal counter
        if qname not in node_ids:
            node_ids[qname] = f"n{counter}"
            counter += 1
        return node_ids[qname]

    edges = result.edges[:max_edges]
    for edge in edges:
        src_file = edge.caller.file_path or "unknown"
        dst_file = edge.callee.file_path or "unknown"
        src_id = get_id(edge.caller.qualified_name)
        dst_id = get_id(edge.callee.qualified_name)

        file_nodes.setdefault(src_file, set()).add(edge.caller.qualified_name)
        file_nodes.setdefault(dst_file, set()).add(edge.callee.qualified_name)

    # Emit subgraphs
    colors = ["#e8f5e9", "#e3f2fd", "#fff3e0", "#fce4ec", "#f3e5f5", "#e0f7fa", "#fff9c4", "#efebe9"]
    for i, (fpath, qnames) in enumerate(file_nodes.items()):
        color = colors[i % len(colors)]
        safe_label = _dot_escape(fpath)
        lines.append(f'    subgraph "cluster_{i}" {{')
        lines.append(f'        label="{safe_label}";')
        lines.append(f'        style=filled; color="{color}";')
        for qn in qnames:
            nid = node_ids[qn]
            label = _dot_escape(qn.split(".")[-1])
            lines.append(f'        {nid} [label="{label}"];')
        lines.append("    }")

    # Emit edges
    for edge in edges:
        src_id = node_ids[edge.caller.qualified_name]
        dst_id = node_ids[edge.callee.qualified_name]
        style = 'style=dashed, color=red' if edge.is_cross_file() else ""
        if style:
            lines.append(f"    {src_id} -> {dst_id} [{style}];")
        else:
            lines.append(f"    {src_id} -> {dst_id};")

    lines.append("}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
