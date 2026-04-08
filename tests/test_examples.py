"""Regression tests for user-facing example projects."""

from __future__ import annotations

from pathlib import Path

from callchain.core.analyzer import Analyzer
from callchain.core.callgraph import CallGraphBuilder
from callchain.core.chain_enum import ChainEnumerator
from callchain.core.models import Language


def _analyze_example(example_path: Path, languages: list[Language]):
    builder = CallGraphBuilder(example_path)
    result = builder.build(languages=languages)
    result.chains = ChainEnumerator(result.edges).enumerate()
    Analyzer(result).run_all()
    return result


def test_python_service_example_analyzes_as_layered_backend():
    result = _analyze_example(Path("examples/python_service"), [Language.PYTHON])

    assert result.total_files >= 6
    assert result.total_functions >= 5
    assert len(result.edges) >= 4
    assert len(result.chains) >= 1
    assert result.parse_errors == []
    assert Language.PYTHON in result.languages_detected


def test_typescript_dashboard_example_analyzes_as_frontend_flow():
    result = _analyze_example(Path("examples/ts_dashboard"), [Language.TYPESCRIPT])

    assert result.total_files >= 4
    assert result.total_functions >= 4
    assert len(result.edges) >= 3
    assert len(result.chains) >= 1
    assert result.parse_errors == []
    assert Language.TYPESCRIPT in result.languages_detected


def test_cpp_library_example_analyzes_as_cross_file_native_library():
    result = _analyze_example(Path("examples/cpp_library"), [Language.CPP])

    assert result.total_files >= 3
    assert result.total_functions >= 3
    assert len(result.edges) >= 2
    assert len(result.chains) >= 1
    assert result.parse_errors == []
    assert Language.CPP in result.languages_detected
