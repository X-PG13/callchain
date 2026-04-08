"""Tests for the analyzer."""

from callchain.core.analyzer import Analyzer, _classify, _is_likely_entrypoint, _normalize_cycle
from callchain.core.callgraph import CallGraphBuilder
from callchain.core.models import AnalysisResult, ClassInfo, FunctionInfo, ImportInfo, Language, ModuleInfo, VariableInfo


def test_full_analysis(python_fixtures):
    builder = CallGraphBuilder(python_fixtures)
    result = builder.build(languages=[Language.PYTHON])
    analyzer = Analyzer(result)
    analyzer.run_all()

    assert result.complexity_distribution is not None
    assert sum(result.complexity_distribution.values()) > 0


def test_hotspots(python_fixtures):
    builder = CallGraphBuilder(python_fixtures)
    result = builder.build(languages=[Language.PYTHON])
    analyzer = Analyzer(result)
    hotspots = analyzer.compute_hotspots()
    # hotspots is a list of (FunctionInfo, count) tuples
    for func, count in hotspots:
        assert count > 0


def test_module_coupling(python_fixtures):
    builder = CallGraphBuilder(python_fixtures)
    result = builder.build(languages=[Language.PYTHON])
    analyzer = Analyzer(result)
    coupling = analyzer.compute_module_coupling()
    for mod, metrics in coupling.items():
        assert 0.0 <= metrics.instability <= 1.0


def test_to_dict(python_fixtures):
    builder = CallGraphBuilder(python_fixtures)
    result = builder.build(languages=[Language.PYTHON])
    analyzer = Analyzer(result)
    analyzer.run_all()
    d = result.to_dict()
    assert "summary" in d
    assert "analysis" in d
    assert "modules" in d


def test_unused_import_detection(python_fixtures):
    builder = CallGraphBuilder(python_fixtures)
    result = builder.build(languages=[Language.PYTHON])
    analyzer = Analyzer(result)
    unused = analyzer.detect_unused_imports()

    assert any(imp.module == "os" for imp in unused)
    assert any(imp.module == "pathlib" and imp.names == ["Path"] for imp in unused)


def test_class_hierarchy_builds_inheritance_tree(cpp_fixtures):
    builder = CallGraphBuilder(cpp_fixtures)
    result = builder.build(languages=[Language.CPP])
    analyzer = Analyzer(result)
    hierarchy = analyzer.build_class_hierarchy()

    parent = next(base for base, children in hierarchy.items() if any(child.endswith("AdvancedCalc") for child in children))
    assert parent.endswith("Calculator")


def test_circular_dependencies_detected_in_cross_file_calls(tmp_path):
    (tmp_path / "a.py").write_text(
        "from b import b_func\n\n\ndef a_func():\n    return b_func()\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "from a import a_func\n\n\ndef b_func():\n    return a_func()\n",
        encoding="utf-8",
    )

    builder = CallGraphBuilder(tmp_path)
    result = builder.build(languages=[Language.PYTHON])
    analyzer = Analyzer(result)
    cycles = analyzer.detect_circular_dependencies()

    assert any(cycle[0] == cycle[-1] and set(cycle[:-1]) == {"a.py", "b.py"} for cycle in cycles)


def test_analyzer_helper_functions_cover_entrypoints_buckets_and_cycle_normalization():
    buckets = {"low (1-5)": 0, "medium (6-10)": 0, "high (11-20)": 0, "very_high (21+)": 0}

    _classify(3, buckets)
    _classify(7, buckets)
    _classify(15, buckets)
    _classify(21, buckets)

    assert buckets == {
        "low (1-5)": 1,
        "medium (6-10)": 1,
        "high (11-20)": 1,
        "very_high (21+)": 1,
    }
    assert _normalize_cycle([]) == []
    assert _normalize_cycle(["b.py", "c.py", "a.py"]) == ["a.py", "b.py", "c.py"]
    assert _is_likely_entrypoint(FunctionInfo(name="test_feature", qualified_name="pkg.test_feature", file_path="a.py", line=1))
    assert _is_likely_entrypoint(FunctionInfo(name="__enter__", qualified_name="pkg.__enter__", file_path="a.py", line=1))
    assert _is_likely_entrypoint(
        FunctionInfo(
            name="index",
            qualified_name="pkg.index",
            file_path="a.py",
            line=1,
            decorators=["@app.get"],
        )
    )


def test_analyzer_handles_unresolved_bases_partial_unused_imports_and_entrypoint_skips():
    route = FunctionInfo(
        name="serve",
        qualified_name="pkg.serve",
        file_path="pkg.py",
        line=1,
        decorators=["@router.post"],
    )
    helper = FunctionInfo(name="helper", qualified_name="pkg.helper", file_path="pkg.py", line=5, complexity=8)
    cls_method = FunctionInfo(
        name="method",
        qualified_name="pkg.Child.method",
        file_path="pkg.py",
        line=10,
        class_name="Child",
        is_method=True,
        complexity=22,
    )
    mod = ModuleInfo(
        file_path="pkg.py",
        language=Language.PYTHON,
        functions=[route, helper],
        classes=[
            ClassInfo(
                name="Child",
                qualified_name="pkg.Child",
                file_path="pkg.py",
                line=9,
                bases=["pkg.Base", "UnknownBase"],
                methods=[cls_method],
                language=Language.PYTHON,
            )
        ],
        imports=[
            ImportInfo(module="pkg.mod", file_path="pkg.py", line=1),
            ImportInfo(module="helpers", names=["used_name", "unused_name"], is_from_import=True, file_path="pkg.py", line=2),
            ImportInfo(module="starry", names=["*"], is_from_import=True, file_path="pkg.py", line=3),
        ],
        variables=[VariableInfo(name="mod", file_path="pkg.py", line=4)],
    )
    empty_mod = ModuleInfo(file_path="empty.py", language=Language.PYTHON)
    edge = _edge(helper, "used_name", "used_name")
    result = AnalysisResult(project_path="demo", modules=[mod, empty_mod], edges=[edge])

    analyzer = Analyzer(result)
    unused = analyzer.detect_unused_imports()
    hierarchy = analyzer.build_class_hierarchy()
    dead = analyzer.compute_dead_functions()

    assert any(imp.module == "helpers" and imp.names == ["unused_name"] for imp in unused)
    assert not any(imp.module == "starry" for imp in unused)
    assert hierarchy["pkg.Base"] == ["pkg.Child"]
    assert hierarchy["UnknownBase"] == ["pkg.Child"]
    assert helper in dead
    assert route not in dead


def test_analyzer_resolves_base_class_by_suffix_match():
    base_mod = ModuleInfo(
        file_path="shared.py",
        language=Language.PYTHON,
        classes=[
            ClassInfo(
                name="Base",
                qualified_name="pkg.shared.Base",
                file_path="shared.py",
                line=1,
                language=Language.PYTHON,
            )
        ],
    )
    child_mod = ModuleInfo(
        file_path="child.py",
        language=Language.PYTHON,
        classes=[
            ClassInfo(
                name="Child",
                qualified_name="pkg.child.Child",
                file_path="child.py",
                line=1,
                bases=["shared.Base"],
                language=Language.PYTHON,
            )
        ],
    )

    hierarchy = Analyzer(AnalysisResult(project_path="demo", modules=[base_mod, child_mod])).build_class_hierarchy()

    assert hierarchy["pkg.shared.Base"] == ["pkg.child.Child"]


def _edge(caller: FunctionInfo, callee_name: str, qualified_name: str):
    from callchain.core.models import CallEdge

    callee = FunctionInfo(
        name=callee_name,
        qualified_name=qualified_name,
        file_path="other.py",
        line=1,
        language=caller.language,
    )
    return CallEdge(caller=caller, callee=callee, call_site_line=caller.line, call_site_file=caller.file_path)
