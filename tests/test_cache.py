"""Tests for the incremental analysis cache."""

from __future__ import annotations

import json
import shutil

from callchain.core.cache import AnalysisCache, CACHE_DIR
from callchain.core.callgraph import CallGraphBuilder
from callchain.core.models import CallEdge, ClassInfo, FunctionInfo, ImportInfo, Language, ModuleInfo, VariableInfo


def test_cache_hits_on_second_build(tmp_path, python_fixtures):
    project = tmp_path / "project"
    shutil.copytree(python_fixtures, project)
    shutil.rmtree(project / CACHE_DIR, ignore_errors=True)

    first = CallGraphBuilder(project, use_cache=True)
    first_result = first.build(languages=[Language.PYTHON])
    assert (project / CACHE_DIR / "index.json").exists()
    assert first._cache_misses == first_result.total_files

    second = CallGraphBuilder(project, use_cache=True)
    second_result = second.build(languages=[Language.PYTHON])

    assert second_result.total_files == first_result.total_files
    assert len(second_result.edges) == len(first_result.edges)
    assert second._cache_hits == second_result.total_files


def test_cache_invalidates_changed_files(tmp_path, python_fixtures):
    project = tmp_path / "project"
    shutil.copytree(python_fixtures, project)
    shutil.rmtree(project / CACHE_DIR, ignore_errors=True)

    CallGraphBuilder(project, use_cache=True).build(languages=[Language.PYTHON])

    sample = project / "sample.py"
    sample.write_text(sample.read_text(encoding="utf-8") + "\n# cache bust\n", encoding="utf-8")

    rebuilt = CallGraphBuilder(project, use_cache=True)
    rebuilt.build(languages=[Language.PYTHON])

    assert rebuilt._cache_misses >= 1


def test_cache_clear_removes_cached_index(tmp_path, python_fixtures):
    project = tmp_path / "project"
    shutil.copytree(python_fixtures, project)
    shutil.rmtree(project / CACHE_DIR, ignore_errors=True)

    CallGraphBuilder(project, use_cache=True).build(languages=[Language.PYTHON])
    cache = AnalysisCache(project)
    assert cache.stats["cached_files"] == 2

    cache.clear()

    assert cache.stats["cached_files"] == 0
    assert not (project / CACHE_DIR / "index.json").exists()


def test_cache_handles_invalid_index_version_mismatch_and_path_fallback(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    cache_dir = project / CACHE_DIR
    cache_dir.mkdir()
    (cache_dir / "index.json").write_text("{bad json", encoding="utf-8")

    cache = AnalysisCache(project)
    assert cache.stats["cached_files"] == 0

    sample = project / "sample.py"
    sample.write_text("print('ok')\n", encoding="utf-8")
    module = ModuleInfo(
        file_path="sample.py",
        language=Language.PYTHON,
        functions=[FunctionInfo(name="run", qualified_name="sample.run", file_path="sample.py", line=1)],
        classes=[ClassInfo(name="Thing", qualified_name="sample.Thing", file_path="sample.py", line=2, language=Language.PYTHON)],
        imports=[ImportInfo(module="os", file_path="sample.py", line=1)],
        variables=[VariableInfo(name="VALUE", file_path="sample.py", line=2)],
    )
    edge = CallEdge(
        caller=module.functions[0],
        callee=FunctionInfo(name="print", qualified_name="print", file_path="", line=0, language=Language.PYTHON),
        call_site_line=1,
        call_site_file="sample.py",
    )
    cache.put(sample, module, [edge])
    rel = cache._rel(sample)
    cache._index[rel]["version"] = 999

    assert cache.get_module(sample) is None
    assert cache.get_edges(sample) is None
    assert cache._rel(tmp_path / "outside.py").endswith("outside.py")

    monkeypatch.setattr(type(sample), "read_bytes", lambda self: (_ for _ in ()).throw(OSError("boom")))
    assert AnalysisCache._hash_file(sample) == ""


def test_cache_serializes_and_deserializes_cached_entries(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sample = project / "sample.py"
    sample.write_text("print('ok')\n", encoding="utf-8")

    cache = AnalysisCache(project)
    func = FunctionInfo(
        name="run",
        qualified_name="sample.run",
        file_path="sample.py",
        line=1,
        end_line=3,
        signature="run()",
        docstring="doc",
        decorators=["@task"],
        language=Language.PYTHON,
        complexity=6,
    )
    cls = ClassInfo(
        name="Thing",
        qualified_name="sample.Thing",
        file_path="sample.py",
        line=4,
        end_line=7,
        bases=["Base"],
        methods=[FunctionInfo(name="method", qualified_name="sample.Thing.method", file_path="sample.py", line=5)],
        language=Language.PYTHON,
    )
    module = ModuleInfo(
        file_path="sample.py",
        language=Language.PYTHON,
        functions=[func],
        classes=[cls],
        imports=[ImportInfo(module="pkg.mod", names=["name"], alias="alias", is_from_import=True, file_path="sample.py", line=1)],
        variables=[VariableInfo(name="VALUE", file_path="sample.py", line=2)],
    )
    edge = CallEdge(
        caller=func,
        callee=FunctionInfo(name="helper", qualified_name="pkg.helper", file_path="helpers.py", line=1, language=Language.PYTHON),
        call_site_line=2,
        call_site_file="sample.py",
    )
    cache.put(sample, module, [edge])
    cache.save()

    loaded = AnalysisCache(project)
    loaded_module = loaded.get_module(sample)
    loaded_edges = loaded.get_edges(sample)
    index_data = json.loads((project / CACHE_DIR / "index.json").read_text(encoding="utf-8"))

    assert loaded_module is not None
    assert loaded_edges is not None
    assert loaded_module.functions[0].docstring == "doc"
    assert loaded_module.classes[0].bases == ["Base"]
    assert loaded_module.imports[0].alias == "alias"
    assert loaded_edges[0].callee.qualified_name == "pkg.helper"
    assert index_data["sample.py"]["version"] >= 1
