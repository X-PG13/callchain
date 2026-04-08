"""Tests for report output writers."""

from __future__ import annotations

import json

from callchain.core.analyzer import Analyzer
from callchain.core.callgraph import CallGraphBuilder
from callchain.core.chain_enum import ChainEnumerator
from callchain.core.models import AnalysisResult, CallChain, CallEdge, FunctionInfo, Language
from callchain.output.dot_output import write_dot
from callchain.output.html_output import write_html
from callchain.output.json_output import write_chains_jsonl, write_json
from callchain.output.mermaid_output import write_mermaid_callgraph, write_mermaid_chain


def _build_full_result(project_path, language: Language) -> AnalysisResult:
    builder = CallGraphBuilder(project_path)
    result = builder.build(languages=[language])
    result.chains = ChainEnumerator(edges=result.edges).enumerate()
    Analyzer(result).run_all()
    return result


def test_write_json_and_jsonl_outputs(tmp_path, python_fixtures):
    result = _build_full_result(python_fixtures, Language.PYTHON)

    json_path = write_json(result, tmp_path / "report.json")
    jsonl_path = write_chains_jsonl(result, tmp_path / "chains.jsonl")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()

    assert data["summary"]["total_files"] == result.total_files
    assert len(lines) == len(result.chains)
    assert json.loads(lines[0])["length"] >= 1


def test_write_graph_outputs(tmp_path, python_fixtures):
    result = _build_full_result(python_fixtures, Language.PYTHON)

    dot_path = write_dot(result, tmp_path / "graph.dot")
    mermaid_graph = write_mermaid_callgraph(result, tmp_path / "graph.md")
    mermaid_chain = write_mermaid_chain(result.chains[0], tmp_path / "chain.md")

    assert "digraph callgraph" in dot_path.read_text(encoding="utf-8")
    assert "flowchart LR" in mermaid_graph.read_text(encoding="utf-8")
    assert "flowchart TD" in mermaid_chain.read_text(encoding="utf-8")


def test_write_html_includes_interactive_controls_and_escaped_data(tmp_path, python_fixtures):
    result = _build_full_result(python_fixtures, Language.PYTHON)
    result.project_path = "</script><!--"

    html_path = write_html(result, tmp_path / "report.html")
    html = html_path.read_text(encoding="utf-8")

    assert 'id="global-search"' in html
    assert 'id="language-filter"' in html
    assert "Unused Imports" in html
    assert "Class Hierarchy" in html
    assert "<\\/script>" in html
    assert "<\\!--" in html


def test_write_mermaid_outputs_cover_truncation_cross_file_and_chain_properties(tmp_path):
    src = FunctionInfo(name="source", qualified_name="pkg.source", file_path="a.py", line=1, language=Language.PYTHON)
    mid = FunctionInfo(name="mid", qualified_name="pkg.mid", file_path="b.py", line=2, language=Language.PYTHON)
    dst = FunctionInfo(name="dest", qualified_name="pkg.dest", file_path="b.py", line=3, language=Language.PYTHON)
    result = AnalysisResult(
        project_path="demo",
        edges=[
            CallEdge(caller=src, callee=mid, call_site_line=1, call_site_file="a.py"),
            CallEdge(caller=mid, callee=dst, call_site_line=2, call_site_file="b.py"),
        ],
    )
    chain = CallChain(nodes=[src, mid, dst])

    graph_path = write_mermaid_callgraph(result, tmp_path / "graph.md", max_edges=1)
    chain_path = write_mermaid_chain(chain, tmp_path / "chain.md")
    graph_text = graph_path.read_text(encoding="utf-8")
    chain_text = chain_path.read_text(encoding="utf-8")

    assert "more edges (truncated)" in graph_text
    assert "-.->|cross-file|" in chain_text
    assert chain.files_involved == {"a.py", "b.py"}
