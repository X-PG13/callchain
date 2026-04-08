"""Mermaid diagram output."""

from __future__ import annotations

from pathlib import Path

from callchain.core.models import AnalysisResult, CallChain


def write_mermaid_callgraph(result: AnalysisResult, output_path: str | Path, max_edges: int = 500) -> Path:
    """Write the full call graph as a Mermaid flowchart."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["```mermaid", "flowchart LR"]

    # Collect unique node IDs
    node_ids: dict[str, str] = {}
    counter = 0

    def get_id(qname: str) -> str:
        nonlocal counter
        if qname not in node_ids:
            node_ids[qname] = f"N{counter}"
            counter += 1
        return node_ids[qname]

    edges = result.edges[:max_edges]
    for edge in edges:
        src_id = get_id(edge.caller.qualified_name)
        dst_id = get_id(edge.callee.qualified_name)
        lines.append(f"    {src_id}[{_escape(edge.caller.display_name)}] --> {dst_id}[{_escape(edge.callee.display_name)}]")

    if len(result.edges) > max_edges:
        lines.append(f"    %% ... and {len(result.edges) - max_edges} more edges (truncated)")

    lines.append("```")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_mermaid_chain(chain: CallChain, output_path: str | Path) -> Path:
    """Write a single call chain as a Mermaid flowchart."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["```mermaid", "flowchart TD"]
    for i, node in enumerate(chain.nodes):
        label = _escape(node.display_name)
        file_label = _escape(node.file_path.rsplit("/", 1)[-1]) if node.file_path else ""
        lines.append(f"    N{i}[\"{label}<br/><small>{file_label}:{node.line}</small>\"]")
        if i > 0:
            if chain.nodes[i].file_path != chain.nodes[i - 1].file_path:
                lines.append(f"    N{i-1} -.->|cross-file| N{i}")
            else:
                lines.append(f"    N{i-1} --> N{i}")

    lines.append("```")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _escape(text: str) -> str:
    """Escape text for Mermaid node labels."""
    return (
        text.replace('"', "#quot;")
        .replace("'", "#apos;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("(", "#40;")
        .replace(")", "#41;")
        .replace("[", "#91;")
        .replace("]", "#93;")
        .replace("{", "#123;")
        .replace("}", "#125;")
    )
