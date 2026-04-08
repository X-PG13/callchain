"""Tests for release automation helpers."""

from __future__ import annotations

import argparse
import json
import runpy
import subprocess
import sys
from typing import Any
import warnings
from pathlib import Path

import pytest

from callchain.devtools import release
from callchain.devtools.release import bump_project_version, validate_project


def test_validate_project_accepts_consistent_metadata(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")
    state = _write_corpus_baseline_state(
        tmp_path,
        baseline={"run_id": 10, "created_at": "2026-04-07T00:00:00Z"},
        refresh={"run_id": 9, "created_at": "2026-04-06T00:00:00Z"},
    )

    validate_project(project)
    validate_project(project, expected_tag="v0.1.0")
    validate_project(project, corpus_baseline_state=state)


def test_validate_project_rejects_mismatched_versions(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")
    (project / "src/callchain/__init__.py").write_text('__version__ = "0.2.0"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Version mismatch"):
        validate_project(project)


def test_validate_project_rejects_missing_unreleased_section(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")
    (project / "CHANGELOG.md").write_text("# Changelog\n\n## [0.1.0] - 2026-04-06\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing the '## \\[Unreleased\\]' section"):
        validate_project(project)


def test_validate_project_rejects_missing_version_heading(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")
    (project / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing an entry for version"):
        validate_project(project)


def test_validate_project_rejects_citation_metadata_drift(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")
    (project / "CITATION.cff").write_text(
        (project / "CITATION.cff").read_text(encoding="utf-8").replace("version: 0.1.0", "version: 0.2.0"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="CITATION.cff has"):
        validate_project(project)


def test_validate_project_rejects_citation_release_date_drift(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")
    (project / "CITATION.cff").write_text(
        (project / "CITATION.cff").read_text(encoding="utf-8").replace("date-released: 2026-04-06", "date-released: 2026-04-07"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Release date mismatch"):
        validate_project(project)


def test_validate_project_rejects_mismatched_expected_tag(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")

    with pytest.raises(ValueError, match="Expected release tag"):
        validate_project(project, expected_tag="v0.2.0")


def test_validate_project_rejects_pending_corpus_refresh_candidate(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")
    state = _write_corpus_baseline_state(
        tmp_path,
        baseline={"run_id": 10, "created_at": "2026-04-07T00:00:00Z"},
        refresh={
            "run_id": 11,
            "created_at": "2026-04-08T00:00:00Z",
            "pr_number": 42,
            "artifact_name": "corpus-baseline-refresh-42-11",
        },
    )

    with pytest.raises(ValueError, match="Pending corpus baseline refresh candidate detected"):
        validate_project(project, corpus_baseline_state=state)

    validate_project(project, corpus_baseline_state=state, allow_pending_corpus_refresh=True)


def test_bump_project_version_rolls_unreleased_notes(tmp_path):
    project = _write_project_fixture(
        tmp_path,
        version="0.1.0",
        changelog_extra="\n### Added\n- fresh feature\n\n### Fixed\n- critical bug\n",
    )

    bump_project_version(project, "0.1.1", "2026-04-07")

    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")
    init_text = (project / "src/callchain/__init__.py").read_text(encoding="utf-8")
    changelog = (project / "CHANGELOG.md").read_text(encoding="utf-8")
    citation = (project / "CITATION.cff").read_text(encoding="utf-8")

    assert 'version = "0.1.1"' in pyproject
    assert '__version__ = "0.1.1"' in init_text
    assert "## [Unreleased]\n\n## [0.1.1] - 2026-04-07" in changelog
    assert "version: 0.1.1" in citation
    assert "date-released: 2026-04-07" in citation
    assert "- fresh feature" in changelog
    assert "- critical bug" in changelog


def test_bump_project_version_rejects_invalid_inputs(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")

    with pytest.raises(ValueError, match="Unsupported version format"):
        bump_project_version(project, "invalid", "2026-04-07")

    with pytest.raises(ValueError, match="Invalid release date"):
        bump_project_version(project, "0.1.1", "2026/04/07")


def test_bump_project_version_rejects_existing_or_duplicate_versions(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")

    with pytest.raises(ValueError, match="already at version"):
        bump_project_version(project, "0.1.0", "2026-04-07")

    (project / "CHANGELOG.md").write_text(
        (project / "CHANGELOG.md").read_text(encoding="utf-8") + "\n## [0.1.1] - 2026-04-05\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="already contains version"):
        bump_project_version(project, "0.1.1", "2026-04-06")


def test_bump_project_version_requires_unreleased_section(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")
    (project / "CHANGELOG.md").write_text("# Changelog\n\n## [0.1.0] - 2026-04-06\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain '## \\[Unreleased\\]'"):
        bump_project_version(project, "0.1.1", "2026-04-07")


def test_release_wrapper_scripts_work_on_temp_project(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0", changelog_extra="\n### Added\n- release prep\n")
    state = _write_corpus_baseline_state(
        tmp_path,
        baseline={"run_id": 10, "created_at": "2026-04-07T00:00:00Z"},
        refresh=None,
    )
    repo_root = Path(__file__).resolve().parents[1]

    validate_proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/check_release.py"),
            "--project-root",
            str(project),
            "--corpus-baseline-state",
            str(state),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert validate_proc.returncode == 0
    assert "validation passed" in validate_proc.stdout.lower()

    bump_proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/bump_version.py"),
            "0.1.1",
            "--date",
            "2026-04-07",
            "--project-root",
            str(project),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert bump_proc.returncode == 0
    assert "Bumped project version to 0.1.1." in bump_proc.stdout


def test_release_main_entrypoints_and_module_execution(tmp_path, capsys):
    project = _write_project_fixture(tmp_path, version="0.1.0", changelog_extra="\n### Added\n- cli path\n")
    state = _write_corpus_baseline_state(
        tmp_path,
        baseline={"run_id": 10, "created_at": "2026-04-07T00:00:00Z"},
        refresh=None,
    )

    assert release.main(["validate", "--project-root", str(project), "--corpus-baseline-state", str(state)]) == 0
    assert "validation passed" in capsys.readouterr().out.lower()

    assert release.main(["bump", "0.1.1", "--project-root", str(project), "--date", "2026-04-07"]) == 0
    assert "Bumped project version to 0.1.1." in capsys.readouterr().out

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "callchain.devtools.release",
            "validate",
            "--project-root",
            str(project),
            "--corpus-baseline-state",
            str(state),
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "validation passed" in proc.stdout.lower()


def test_release_corpus_state_rendering_and_output_paths(tmp_path, capsys):
    state = _write_corpus_baseline_state(
        tmp_path,
        baseline={
            "run_id": 10,
            "created_at": "2026-04-07T00:00:00Z",
            "html_url": "https://example.com/baseline/10",
            "artifact_url": "https://example.com/baseline/10/artifacts/100",
            "artifact_id": 100,
            "artifact_expired": False,
            "head_branch": "main",
            "head_sha": "abcdef1234567890",
            "event": "schedule",
            "artifact_name": "corpus-baseline-10",
        },
        compare={
            "run_id": 12,
            "created_at": "2026-04-07T08:00:00Z",
            "html_url": "https://example.com/compare/12",
            "artifact_url": "https://example.com/compare/12/artifacts/102",
            "artifact_id": 102,
            "artifact_expired": False,
            "head_branch": "main",
            "head_sha": "fedcba0987654321",
            "event": "pull_request",
            "artifact_name": "corpus-baseline-compare-12",
        },
        refresh={
            "run_id": 11,
            "created_at": "2026-04-08T00:00:00Z",
            "html_url": "https://example.com/refresh/11",
            "artifact_url": "https://example.com/refresh/11/artifacts/101",
            "artifact_id": 101,
            "artifact_expired": False,
            "head_branch": "feature/release-review",
            "head_sha": "1234567890abcdef",
            "event": "pull_request_target",
            "pr_number": 42,
            "artifact_name": "corpus-baseline-refresh-42-11",
        },
    )
    compare_report = _write_compare_report(
        tmp_path,
        drift_cases=[
            (
                "sample",
                {
                    "files": 0,
                    "functions": 1,
                    "classes": 1,
                    "edges": 1,
                    "chains": 0,
                    "parse_errors": 0,
                },
            )
        ],
        changed_files=[
            "src/callchain/languages/python_lang.py",
            "src/callchain/core/callgraph.py",
            "./src/callchain/languages/python_lang.py",
            "docs/release-notes.md",
        ],
        owner_hints=[
            {
                "key": "symbol_extraction",
                "label": "symbol-extraction",
                "cases": ["sample"],
                "paths": ["src/callchain/languages/*.py"],
                "owners": ["@callchain-languages"],
                "matched_changed_files": ["src/callchain/languages/python_lang.py"],
                "ownerless_changed_files": [],
            },
            {
                "key": "call_resolution",
                "label": "call-resolution",
                "cases": ["sample"],
                "paths": ["src/callchain/core/callgraph.py", "src/callchain/languages/*.py"],
                "owners": ["@callchain-languages", "@callchain-graph"],
                "matched_changed_files": [
                    "src/callchain/languages/python_lang.py",
                    "src/callchain/core/callgraph.py",
                ],
                "ownerless_changed_files": [],
            },
        ],
        owner_focus=[
            {
                "owner": "@callchain-graph",
                "labels": ["call-resolution"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/core/callgraph.py"],
                "priority": "critical",
                "score": 12,
            },
            {
                "owner": "@callchain-languages",
                "labels": ["symbol-extraction", "call-resolution"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/languages/python_lang.py"],
                "priority": "medium",
                "score": 10,
            },
        ],
        reviewer_candidates=[
            {
                "owner": "@callchain-graph",
                "kind": "user",
                "priority": "critical",
                "score": 12,
                "labels": ["call-resolution"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/core/callgraph.py"],
            },
            {
                "owner": "@callchain-languages",
                "kind": "user",
                "priority": "medium",
                "score": 10,
                "labels": ["symbol-extraction", "call-resolution"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            },
            {
                "owner": "@callchain/core-reviewers",
                "kind": "team",
                "priority": "high",
                "score": 9,
                "labels": ["call-resolution"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/core/callgraph.py"],
            },
            {
                "owner": "docs-team",
                "kind": "unsupported",
                "priority": "low",
                "score": 2,
                "labels": ["non-structural"],
                "cases": ["sample"],
                "matched_changed_files": ["docs/release-notes.md"],
            },
        ],
        review_request_plan={
            "users": ["@callchain-graph", "@callchain-languages"],
            "teams": ["@callchain/core-reviewers"],
            "unsupported": ["docs-team"],
        },
    )
    compare_markdown = _write_compare_markdown(
        tmp_path,
        [
            "# Corpus Baseline Compare",
            "",
            "- Baseline: `build/base.json`",
            "- Candidate: `build/head.json`",
            "- Metric: `summary`",
            "- Summary drift: `1 case(s)`",
            "",
            "| Case | Status | Summary Delta |",
            "| --- | --- | --- |",
            "| `sample` | `changed` | functions +1, classes +1, edges +1 |",
            "| `stable` | `unchanged` | No summary drift |",
        ],
    )

    enriched_state = release.attach_compare_report_summary(release.load_corpus_baseline_state(state), compare_report)
    enriched_state = release.attach_compare_markdown_excerpt(enriched_state, compare_markdown)
    report = release.summarize_corpus_baseline_state(enriched_state)
    assert report["status"] == "pending_refresh_candidate"
    assert report["pending_refresh"] is True
    assert report["recommendation"] == "Review or promote the newer Corpus Baseline Refresh candidate before publishing."
    assert report["compare_report"]["summary_drift_cases"] == ["sample"]
    assert report["compare_report"]["category_summary"]["parser"]["cases"] == ["sample"]
    assert report["compare_report"]["category_summary"]["resolver"]["cases"] == ["sample"]
    assert report["compare_report"]["attribution_summary"]["symbol_extraction"]["cases"] == ["sample"]
    assert report["compare_report"]["attribution_summary"]["call_resolution"]["cases"] == ["sample"]
    assert report["compare_report"]["has_changed_files_context"] is True
    assert report["compare_report"]["changed_files"] == [
        "src/callchain/languages/python_lang.py",
        "src/callchain/core/callgraph.py",
        "docs/release-notes.md",
    ]
    assert report["compare_review_hints"] == [
        {
            "key": "symbol_extraction",
            "label": "symbol-extraction",
            "cases": ["sample"],
            "paths": ["src/callchain/languages/*.py"],
            "reason": "Review language parsers that extract functions, classes, methods, imports, and variables.",
        },
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["sample"],
            "paths": ["src/callchain/core/callgraph.py", "src/callchain/languages/*.py"],
            "reason": "Review raw call extraction and cross-file edge resolution.",
        },
    ]
    assert report["compare_owner_hints"] == [
        {
            "key": "symbol_extraction",
            "label": "symbol-extraction",
            "cases": ["sample"],
            "paths": ["src/callchain/languages/*.py"],
            "owners": ["@callchain-languages"],
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            "ownerless_changed_files": [],
        },
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["sample"],
            "paths": ["src/callchain/core/callgraph.py", "src/callchain/languages/*.py"],
            "owners": ["@callchain-languages", "@callchain-graph"],
            "matched_changed_files": [
                "src/callchain/languages/python_lang.py",
                "src/callchain/core/callgraph.py",
            ],
            "ownerless_changed_files": [],
        },
    ]
    assert report["compare_owner_focus"] == [
        {
            "owner": "@callchain-graph",
            "labels": ["call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
            "priority": "critical",
            "score": 12,
        },
        {
            "owner": "@callchain-languages",
            "labels": ["symbol-extraction", "call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            "priority": "medium",
            "score": 10,
        },
    ]
    assert report["compare_reviewer_candidates"] == [
        {
            "owner": "@callchain-graph",
            "kind": "user",
            "priority": "critical",
            "score": 12,
            "labels": ["call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
        },
        {
            "owner": "@callchain-languages",
            "kind": "user",
            "priority": "medium",
            "score": 10,
            "labels": ["symbol-extraction", "call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
        },
        {
            "owner": "@callchain/core-reviewers",
            "kind": "team",
            "priority": "high",
            "score": 9,
            "labels": ["call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
        },
        {
            "owner": "docs-team",
            "kind": "unsupported",
            "priority": "low",
            "score": 2,
            "labels": ["non-structural"],
            "cases": ["sample"],
            "matched_changed_files": ["docs/release-notes.md"],
        },
    ]
    assert report["compare_review_request_plan"] == {
        "users": ["@callchain-graph", "@callchain-languages"],
        "teams": ["@callchain/core-reviewers"],
        "unsupported": ["docs-team"],
    }
    assert report["compare_changed_file_overlap"] == [
        {
            "key": "symbol_extraction",
            "label": "symbol-extraction",
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
        },
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["sample"],
            "matched_changed_files": [
                "src/callchain/languages/python_lang.py",
                "src/callchain/core/callgraph.py",
            ],
        },
    ]
    assert report["compare_changed_file_focus"] == [
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["sample"],
            "matched_changed_files": [
                "src/callchain/languages/python_lang.py",
                "src/callchain/core/callgraph.py",
            ],
            "priority": "critical",
            "score": 18,
        },
        {
            "key": "symbol_extraction",
            "label": "symbol-extraction",
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            "priority": "medium",
            "score": 8,
        },
    ]
    assert report["compare_focus_excerpt"]["highlight_count"] == 1
    assert report["compare_markdown_excerpt"]["table_row_count"] == 2

    text_summary = release.format_corpus_baseline_state(report)
    assert "status: pending_refresh_candidate" in text_summary
    assert "run 10" in text_summary
    assert "run 12" in text_summary
    assert "run 11" in text_summary
    assert "branch main" in text_summary
    assert "commit abcdef1" in text_summary
    assert "artifact url https://example.com/refresh/11/artifacts/101" in text_summary
    assert "latest branch compare drift: metric summary, drift 1 case(s), details: sample: functions +1, classes +1, edges +1" in text_summary
    assert "latest branch compare categories: parser 1 case(s) (sample); resolver 1 case(s) (sample)" in text_summary
    assert "latest branch compare attribution: symbol-extraction 1 case(s) (sample); call-resolution 1 case(s) (sample)" in text_summary
    assert (
        "latest branch compare review hints: symbol-extraction -> src/callchain/languages/*.py (sample); "
        "call-resolution -> src/callchain/core/callgraph.py, src/callchain/languages/*.py (sample)"
    ) in text_summary
    assert (
        "latest branch compare owner focus: critical @callchain-graph -> call-resolution "
        "[src/callchain/core/callgraph.py] (sample); medium @callchain-languages -> "
        "symbol-extraction, call-resolution [src/callchain/languages/python_lang.py] (sample)"
    ) in text_summary
    assert (
        "latest branch compare owner hints: symbol-extraction -> @callchain-languages (sample); "
        "call-resolution -> @callchain-languages, @callchain-graph (sample)"
    ) in text_summary
    assert (
        "latest branch compare review-request dry-run: users @callchain-graph, @callchain-languages; "
        "teams @callchain/core-reviewers; unsupported docs-team"
    ) in text_summary
    assert "latest branch compare changed files: 3 file(s)" in text_summary
    assert (
        "latest branch compare changed-file overlap: symbol-extraction -> src/callchain/languages/python_lang.py (sample); "
        "call-resolution -> src/callchain/languages/python_lang.py, src/callchain/core/callgraph.py (sample)"
    ) in text_summary
    assert (
        "latest branch compare changed-file focus: critical call-resolution -> src/callchain/languages/python_lang.py, "
        "src/callchain/core/callgraph.py (sample); medium symbol-extraction -> "
        "src/callchain/languages/python_lang.py (sample)"
    ) in text_summary
    assert "latest branch compare focus: 1 highlighted case(s)" in text_summary

    markdown_summary = release.format_corpus_baseline_state_markdown(report)
    assert "## Release Corpus State" in markdown_summary
    assert "| Record | Run | Created | Branch | Commit | Event | Artifact |" in markdown_summary
    assert "[`run 10`](https://example.com/baseline/10)" in markdown_summary
    assert "| Latest branch compare | [`run 12`](https://example.com/compare/12) | `2026-04-07T08:00:00Z` | `main` | `fedcba0` | `pull_request` | [`corpus-baseline-compare-12`](https://example.com/compare/12/artifacts/102) (active) |" in markdown_summary
    assert "- Latest branch compare drift: metric `summary`, drift `1 case(s)`, cases: `sample`" in markdown_summary
    assert "- Drift categories: parser `sample`; resolver `sample`" in markdown_summary
    assert "- Drift attribution: symbol-extraction `sample`; call-resolution `sample`" in markdown_summary
    assert (
        "- Likely modules to review: symbol-extraction -> `src/callchain/languages/*.py` (cases: `sample`); "
        "call-resolution -> `src/callchain/core/callgraph.py`, `src/callchain/languages/*.py` (cases: `sample`)"
    ) in markdown_summary
    assert (
        "- Owner focus: critical `@callchain-graph` -> `call-resolution` "
        "[`src/callchain/core/callgraph.py`] (cases: `sample`); medium `@callchain-languages` -> "
        "`symbol-extraction`, `call-resolution` [`src/callchain/languages/python_lang.py`] "
        "(cases: `sample`)"
    ) in markdown_summary
    assert (
        "- Likely owners to review: symbol-extraction -> `@callchain-languages` (cases: `sample`); "
        "call-resolution -> `@callchain-languages`, `@callchain-graph` (cases: `sample`)"
    ) in markdown_summary
    assert (
        "- Review-request dry-run: users `@callchain-graph`, `@callchain-languages`; "
        "teams `@callchain/core-reviewers`; unsupported `docs-team`"
    ) in markdown_summary
    assert "- Changed files context: `3 file(s)`" in markdown_summary
    assert (
        "- Changed-file overlap: symbol-extraction -> `src/callchain/languages/python_lang.py` (cases: `sample`); "
        "call-resolution -> `src/callchain/languages/python_lang.py`, `src/callchain/core/callgraph.py` "
        "(cases: `sample`)"
    ) in markdown_summary
    assert (
        "- Changed-file focus: critical `call-resolution` -> `src/callchain/languages/python_lang.py`, "
        "`src/callchain/core/callgraph.py` (cases: `sample`); medium `symbol-extraction` -> "
        "`src/callchain/languages/python_lang.py` (cases: `sample`)"
    ) in markdown_summary
    assert "  - `sample`: functions +1, classes +1, edges +1" in markdown_summary
    assert "- Latest branch compare focus: 1 highlighted case(s)" in markdown_summary
    assert "<summary>Latest branch compare focus</summary>" in markdown_summary
    assert "| Case | Status | Summary Delta |" in markdown_summary
    assert "| `sample` | `changed` | functions +1, classes +1, edges +1 |" in markdown_summary
    assert "[`corpus-baseline-10`](https://example.com/baseline/10/artifacts/100)" in markdown_summary
    assert "`feature/release-review`" in markdown_summary
    assert "`1234567`" in markdown_summary
    assert "[`corpus-baseline-refresh-42-11`](https://example.com/refresh/11/artifacts/101)" in markdown_summary
    assert "> A newer `Corpus Baseline Refresh` candidate exists." in markdown_summary

    assert release.main(
        [
            "corpus-state",
            "--state",
            str(state),
            "--compare-report",
            str(compare_report),
            "--compare-markdown",
            str(compare_markdown),
            "--json",
        ]
    ) == 0
    rendered_json = json.loads(capsys.readouterr().out)
    assert rendered_json["status"] == "pending_refresh_candidate"
    assert rendered_json["baseline"]["artifact_id"] == 100
    assert rendered_json["compare"]["artifact_id"] == 102
    assert rendered_json["compare_report"]["summary_drift_cases"] == ["sample"]
    assert rendered_json["compare_report"]["category_summary"]["parser"]["cases"] == ["sample"]
    assert rendered_json["compare_report"]["attribution_summary"]["symbol_extraction"]["cases"] == ["sample"]
    assert rendered_json["compare_report"]["changed_files"] == [
        "src/callchain/languages/python_lang.py",
        "src/callchain/core/callgraph.py",
        "docs/release-notes.md",
    ]
    assert rendered_json["compare_report"]["reviewer_candidates"][0]["owner"] == "@callchain-graph"
    assert rendered_json["compare_report"]["reviewer_candidates"][2]["kind"] == "team"
    assert rendered_json["compare_report"]["review_request_plan"] == {
        "users": ["@callchain-graph", "@callchain-languages"],
        "teams": ["@callchain/core-reviewers"],
        "unsupported": ["docs-team"],
    }
    assert rendered_json["compare_report"]["owner_hints"][0]["owners"] == ["@callchain-languages"]
    assert rendered_json["compare_report"]["owner_focus"][0]["owner"] == "@callchain-graph"
    assert rendered_json["compare_review_hints"][0]["key"] == "symbol_extraction"
    assert rendered_json["compare_review_hints"][0]["paths"] == ["src/callchain/languages/*.py"]
    assert rendered_json["compare_reviewer_candidates"][0]["owner"] == "@callchain-graph"
    assert rendered_json["compare_reviewer_candidates"][2]["kind"] == "team"
    assert rendered_json["compare_review_request_plan"] == {
        "users": ["@callchain-graph", "@callchain-languages"],
        "teams": ["@callchain/core-reviewers"],
        "unsupported": ["docs-team"],
    }
    assert rendered_json["compare_owner_focus"][0]["owner"] == "@callchain-graph"
    assert rendered_json["compare_owner_focus"][1]["labels"] == ["symbol-extraction", "call-resolution"]
    assert rendered_json["compare_owner_hints"][0]["key"] == "symbol_extraction"
    assert rendered_json["compare_owner_hints"][1]["owners"] == ["@callchain-languages", "@callchain-graph"]
    assert rendered_json["compare_review_hints"][1]["key"] == "call_resolution"
    assert rendered_json["compare_changed_file_overlap"][0]["key"] == "symbol_extraction"
    assert rendered_json["compare_changed_file_overlap"][0]["matched_changed_files"] == [
        "src/callchain/languages/python_lang.py",
    ]
    assert rendered_json["compare_changed_file_focus"][0]["key"] == "call_resolution"
    assert rendered_json["compare_changed_file_focus"][0]["priority"] == "critical"
    assert rendered_json["compare_changed_file_focus"][0]["matched_changed_files"] == [
        "src/callchain/languages/python_lang.py",
        "src/callchain/core/callgraph.py",
    ]
    assert rendered_json["compare_focus_excerpt"]["highlight_count"] == 1
    assert rendered_json["compare_markdown_excerpt"]["table_row_count"] == 2
    assert rendered_json["refresh"]["artifact_url"] == "https://example.com/refresh/11/artifacts/101"

    assert (
        release.main(
            [
                "corpus-state",
                "--state",
                str(state),
                "--compare-report",
                str(compare_report),
                "--compare-markdown",
                str(compare_markdown),
                "--release-notes",
                "--release-tag",
                "v0.1.0",
                "--workflow-run-url",
                "https://example.com/actions/runs/500",
                "--state-artifact-url",
                "https://example.com/actions/artifacts/600",
                "--dist-artifact-url",
                "https://example.com/actions/artifacts/601",
            ]
        )
        == 0
    )
    release_notes = capsys.readouterr().out
    assert "<!-- callchain-release-corpus-audit:start -->" in release_notes
    assert "## Release Corpus Audit (`v0.1.0`)" in release_notes
    assert (
        "- Owner focus: critical `@callchain-graph` -> `call-resolution` "
        "[`src/callchain/core/callgraph.py`] (cases: `sample`); medium `@callchain-languages` -> "
        "`symbol-extraction`, `call-resolution` [`src/callchain/languages/python_lang.py`] "
        "(cases: `sample`)"
    ) in release_notes
    assert (
        "- Likely owners to review: symbol-extraction -> `@callchain-languages` (cases: `sample`); "
        "call-resolution -> `@callchain-languages`, `@callchain-graph` (cases: `sample`)"
    ) in release_notes
    assert (
        "- Review-request dry-run: users `@callchain-graph`, `@callchain-languages`; "
        "teams `@callchain/core-reviewers`; unsupported `docs-team`"
    ) in release_notes
    assert "[release workflow run](https://example.com/actions/runs/500)" in release_notes
    assert "[corpus state artifact](https://example.com/actions/artifacts/600)" in release_notes
    assert "[distribution artifact bundle](https://example.com/actions/artifacts/601)" in release_notes
    assert "<summary>Corpus release state</summary>" in release_notes
    assert "<summary>Latest branch compare focus</summary>" in release_notes
    assert "Latest branch compare" in release_notes
    assert "- Latest branch compare drift: metric `summary`, drift `1 case(s)`, cases: `sample`" in release_notes
    assert "- Drift categories: parser `sample`; resolver `sample`" in release_notes
    assert "- Drift attribution: symbol-extraction `sample`; call-resolution `sample`" in release_notes
    assert (
        "- Likely modules to review: symbol-extraction -> `src/callchain/languages/*.py` (cases: `sample`); "
        "call-resolution -> `src/callchain/core/callgraph.py`, `src/callchain/languages/*.py` (cases: `sample`)"
    ) in release_notes
    assert "- Changed files context: `3 file(s)`" in release_notes
    assert (
        "- Changed-file overlap: symbol-extraction -> `src/callchain/languages/python_lang.py` (cases: `sample`); "
        "call-resolution -> `src/callchain/languages/python_lang.py`, `src/callchain/core/callgraph.py` "
        "(cases: `sample`)"
    ) in release_notes
    assert (
        "- Changed-file focus: critical `call-resolution` -> `src/callchain/languages/python_lang.py`, "
        "`src/callchain/core/callgraph.py` (cases: `sample`); medium `symbol-extraction` -> "
        "`src/callchain/languages/python_lang.py` (cases: `sample`)"
    ) in release_notes
    assert "- Latest branch compare focus: 1 highlighted case(s)" in release_notes
    assert "- Drift detail: `sample` functions +1, classes +1, edges +1" in release_notes
    assert "| `sample` | `changed` | functions +1, classes +1, edges +1 |" in release_notes

    assert release.main(
        [
            "corpus-state",
            "--state",
            str(state),
            "--compare-report",
            str(compare_report),
            "--compare-markdown",
            str(compare_markdown),
        ]
    ) == 0
    plain_summary = capsys.readouterr().out
    assert "Release corpus baseline state:" in plain_summary
    assert "status: pending_refresh_candidate" in plain_summary

    output_path = tmp_path / "nested" / "release-corpus-state.md"
    assert release.main(
        [
            "corpus-state",
            "--state",
            str(state),
            "--compare-report",
            str(compare_report),
            "--compare-markdown",
            str(compare_markdown),
            "--markdown",
            "--output",
            str(output_path),
        ]
    ) == 0
    assert "## Release Corpus State" in output_path.read_text(encoding="utf-8")


def test_release_corpus_state_markdown_covers_allowed_and_missing_statuses():
    allowed_report = release.summarize_corpus_baseline_state(
        {
            "baseline": {"run_id": 10, "created_at": "2026-04-07T00:00:00Z"},
            "refresh": {"run_id": 11, "created_at": "2026-04-08T00:00:00Z"},
        },
        allow_pending_refresh=True,
    )
    allowed_markdown = release.format_corpus_baseline_state_markdown(allowed_report)
    assert "pending_refresh_allowed" in allowed_markdown
    assert "configured to allow it" in allowed_markdown
    assert "| Official baseline | `run 10` | `2026-04-07T00:00:00Z` | `n/a` | `n/a` | `n/a` | `none` |" in allowed_markdown

    missing_report = release.summarize_corpus_baseline_state({"baseline": None, "compare": None, "refresh": None})
    missing_markdown = release.format_corpus_baseline_state_markdown(missing_report)
    assert "missing_official_baseline" in missing_markdown
    assert "No official `Corpus Baseline` run metadata was found" in missing_markdown
    assert "| Latest branch compare | `none` | `n/a` | `n/a` | `n/a` | `n/a` | `none` |" in missing_markdown
    assert "| Refresh candidate | `none` | `n/a` | `n/a` | `n/a` | `n/a` | `none` |" in missing_markdown


def test_release_corpus_audit_release_notes_support_optional_evidence_links():
    report = release.summarize_corpus_baseline_state(
        {
            "baseline": {"run_id": 10, "created_at": "2026-04-07T00:00:00Z"},
            "compare": {"run_id": 12, "created_at": "2026-04-07T08:00:00Z"},
            "compare_report": {
                "metric": "summary",
                "has_summary_drift": False,
                "summary_drift_cases": [],
                "drift_details": [],
                "comparison_count": 1,
                "category_summary": {
                    "parser": {"count": 0, "cases": []},
                    "resolver": {"count": 0, "cases": []},
                    "parse_health": {"count": 0, "cases": []},
                    "non_structural": {"count": 0, "cases": []},
                },
            },
            "refresh": None,
        }
    )

    notes = release.format_corpus_baseline_release_notes(report, release_tag="v0.1.0")
    assert "<!-- callchain-release-corpus-audit:start -->" in notes
    assert "## Release Corpus Audit (`v0.1.0`)" in notes
    assert "- Status: `official_baseline_only`" in notes
    assert "- Evidence:" not in notes
    assert "### Corpus Release State" in notes
    assert "<!-- callchain-release-corpus-audit:end -->" in notes


def test_release_corpus_state_accepts_legacy_compare_report_without_category_summary():
    report = release.summarize_corpus_baseline_state(
        {
            "baseline": {"run_id": 10, "created_at": "2026-04-07T00:00:00Z"},
            "compare": {"run_id": 12, "created_at": "2026-04-07T08:00:00Z"},
            "compare_report": {
                "metric": "summary",
                "has_summary_drift": False,
                "summary_drift_cases": [],
                "drift_details": [],
                "comparison_count": 1,
            },
            "refresh": None,
        }
    )
    assert report["compare_report"]["category_summary"]["parser"]["cases"] == []
    assert report["compare_report"]["attribution_summary"]["discovery"]["cases"] == []
    assert report["compare_review_hints"] == []
    assert report["compare_owner_hints"] == []
    assert report["compare_owner_focus"] == []
    assert report["compare_changed_file_overlap"] == []
    assert "latest branch compare categories: none" in release.format_corpus_baseline_state(report)
    assert "latest branch compare attribution: none" in release.format_corpus_baseline_state(report)
    assert "latest branch compare review hints: none" in release.format_corpus_baseline_state(report)


def test_release_compare_report_summary_handles_no_drift_details(tmp_path):
    summary = release.load_compare_report_summary(
        _write_compare_report(
            tmp_path,
            drift_cases=[],
        )
    )
    assert summary["has_summary_drift"] is False
    assert summary["drift_details"] == []
    assert release._format_compare_report_line(summary) == "metric summary, drift none"
    assert release._format_compare_report_markdown(summary) == "metric `summary`, drift `none`, cases: `none`"


def test_release_compare_markdown_excerpt_truncates_table_rows(tmp_path):
    excerpt = release.load_compare_markdown_excerpt(
        _write_compare_markdown(
            tmp_path,
            [
                "# Corpus Baseline Compare",
                "",
                "- Baseline: `build/base.json`",
                "- Candidate: `build/head.json`",
                "- Metric: `summary`",
                "",
                "| Case | Status | Summary Delta |",
                "| --- | --- | --- |",
                "| `one` | `changed` | functions +1 |",
                "| `two` | `changed` | edges +1 |",
                "| `three` | `unchanged` | No summary drift |",
            ],
            name="compare-many.md",
        ),
        max_table_rows=2,
    )
    assert excerpt["truncated"] is True
    assert excerpt["table_row_count"] == 3
    assert "_... 1 more compare row(s) in the artifact._" in excerpt["content"]
    assert release._format_compare_markdown_excerpt_line(excerpt) == "3 row(s), truncated"


def test_release_compare_markdown_excerpt_supports_leading_blank_lines_and_summary_only_markdown(tmp_path):
    excerpt = release.load_compare_markdown_excerpt(
        _write_compare_markdown(
            tmp_path,
            [
                "",
                "",
                "# Corpus Baseline Compare",
                "",
                "- Baseline: `build/base.json`",
                "- Candidate: `build/head.json`",
                "- Metric: `summary`",
            ],
            name="compare-summary-only.md",
        )
    )
    assert excerpt["truncated"] is False
    assert excerpt["table_row_count"] == 0
    assert excerpt["content"] == (
        "- Baseline: `build/base.json`\n"
        "- Candidate: `build/head.json`\n"
        "- Metric: `summary`"
    )
    assert release._format_compare_markdown_excerpt_line(excerpt) == "0 row(s)"


def test_release_compare_report_summary_builds_severity_sorted_focus_excerpt(tmp_path):
    compare_report = tmp_path / "compare-focus.json"
    compare_report.write_text(
        json.dumps(
            {
                "metric": "summary",
                "has_summary_drift": True,
                "summary_drift_cases": ["wider", "deeper", "smaller", "timing-only"],
                "comparisons": [
                    {
                        "name": "smaller",
                        "status": "changed",
                        "summary_delta": {"functions": 1, "classes": 0, "edges": 0},
                    },
                    {
                        "name": "wider",
                        "status": "changed",
                        "summary_delta": {"functions": 1, "classes": 1, "edges": 1},
                    },
                    {
                        "name": "deeper",
                        "summary_delta": {"functions": 3, "classes": 0, "edges": 0},
                    },
                    {
                        "name": "timing-only",
                        "status": "within_threshold",
                        "summary_delta": {"functions": 0, "classes": 0, "edges": 0},
                        "delta_pct": 4.0,
                    },
                    {
                        "name": "stable",
                        "status": "unchanged",
                        "summary_delta": {"functions": 0, "classes": 0, "edges": 0},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = release.load_compare_report_summary(compare_report)
    focus = summary["focus_excerpt"]
    categories = summary["category_summary"]
    assert focus["highlight_count"] == 3
    assert focus["total_count"] == 4
    assert focus["truncated"] is True
    lines = focus["content"].splitlines()
    assert lines[0] == "- Metric: `summary`"
    assert "| `wider` | `changed` | functions +1, classes +1, edges +1 |" in focus["content"]
    assert "| `deeper` | `changed` | functions +3 |" in focus["content"]
    assert "| `smaller` | `changed` | functions +1 |" in focus["content"]
    assert "| `timing-only` | `within_threshold` | +4.0% |" not in focus["content"]
    assert "_... 1 more highlighted case(s) omitted from the release view._" in focus["content"]
    assert release._format_compare_focus_excerpt_line(focus) == "3 highlighted case(s) of 4, truncated"
    assert categories["parser"]["cases"] == ["smaller", "wider", "deeper"]
    assert categories["resolver"]["cases"] == ["wider"]
    assert categories["parse_health"]["cases"] == []
    assert categories["non_structural"]["cases"] == ["timing-only"]


def test_release_corpus_state_renderers_fall_back_to_raw_compare_markdown_excerpt(tmp_path):
    state = _write_corpus_baseline_state(
        tmp_path,
        baseline={"run_id": 10, "created_at": "2026-04-07T00:00:00Z"},
        compare={"run_id": 12, "created_at": "2026-04-07T08:00:00Z"},
        refresh=None,
    )
    compare_markdown = _write_compare_markdown(
        tmp_path,
        [
            "# Corpus Baseline Compare",
            "",
            "- Baseline: `build/base.json`",
            "- Candidate: `build/head.json`",
            "",
            "| Case | Status | Summary Delta |",
            "| --- | --- | --- |",
            "| `sample` | `changed` | functions +1 |",
        ],
        name="compare-fallback.md",
    )

    report = release.summarize_corpus_baseline_state(
        release.attach_compare_markdown_excerpt(release.load_corpus_baseline_state(state), compare_markdown)
    )
    assert report["compare_focus_excerpt"] is None
    assert report["compare_markdown_excerpt"]["table_row_count"] == 1

    plain = release.format_corpus_baseline_state(report)
    assert "latest branch compare excerpt: 1 row(s)" in plain

    markdown = release.format_corpus_baseline_state_markdown(report)
    assert "- Latest branch compare excerpt: 1 row(s)" in markdown
    assert "<summary>Latest branch compare excerpt</summary>" in markdown
    assert "| `sample` | `changed` | functions +1 |" in markdown

    release_notes = release.format_corpus_baseline_release_notes(report, release_tag="v0.1.0")
    assert "- Latest branch compare excerpt: 1 row(s)" in release_notes
    assert "<summary>Latest branch compare excerpt</summary>" in release_notes
    assert "| `sample` | `changed` | functions +1 |" in release_notes


def test_release_corpus_state_wrapper_script_supports_markdown_output(tmp_path):
    state = _write_corpus_baseline_state(
        tmp_path,
        baseline={"run_id": 10, "created_at": "2026-04-07T00:00:00Z"},
        refresh=None,
    )
    repo_root = Path(__file__).resolve().parents[1]

    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/render_release_corpus_state.py"),
            "--state",
            str(state),
            "--markdown",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "## Release Corpus State" in proc.stdout
    assert "official_baseline_only" in proc.stdout


def test_release_internal_helpers_cover_edge_cases(tmp_path):
    project = _write_project_fixture(tmp_path, version="0.1.0")

    assert release._roll_unreleased_section("# Changelog\n\n## [Unreleased]", "0.1.1", "2026-04-07").endswith(
        "## [Unreleased]\n\n## [0.1.1] - 2026-04-07"
    )

    with pytest.raises(ValueError, match="Could not find missing field"):
        release._extract_single(release._PYPROJECT_VERSION_RE, "name = 'callchain'\n", "missing field")

    with pytest.raises(ValueError, match="Expected exactly one version field to replace"):
        release._replace_single(release._PYPROJECT_VERSION_RE, "name = 'callchain'\n", 'version = "0.1.1"')

    assert release.load_corpus_baseline_state(_write_corpus_baseline_state(tmp_path, baseline={"run_id": 1, "created_at": "2026-04-07T00:00:00Z"}, refresh=None))["baseline"]["run_id"] == 1
    with pytest.raises(ValueError, match="does not exist"):
        release.load_corpus_baseline_state(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="does not exist"):
        release.load_compare_report_summary(tmp_path / "missing-compare.json")
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        release.load_corpus_baseline_state(bad_json)
    with pytest.raises(ValueError, match="not valid JSON"):
        release.load_compare_report_summary(bad_json)
    wrong_root = tmp_path / "wrong-root.json"
    wrong_root.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        release.load_corpus_baseline_state(wrong_root)
    with pytest.raises(ValueError, match="JSON object"):
        release.load_compare_report_summary(wrong_root)
    bad_compare = tmp_path / "bad-compare.json"
    bad_compare.write_text(json.dumps({"metric": "", "has_summary_drift": True, "summary_drift_cases": [], "comparisons": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="metric must be a non-empty string"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(json.dumps({"metric": "summary", "has_summary_drift": "yes", "summary_drift_cases": [], "comparisons": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="has_summary_drift must be a boolean"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(json.dumps({"metric": "summary", "has_summary_drift": True, "summary_drift_cases": [""], "comparisons": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="summary_drift_cases must be a list of non-empty strings"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(json.dumps({"metric": "summary", "has_summary_drift": True, "summary_drift_cases": [], "comparisons": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="comparisons must be a list"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(json.dumps({"metric": "summary", "has_summary_drift": True, "summary_drift_cases": [], "comparisons": [1]}), encoding="utf-8")
    with pytest.raises(ValueError, match="comparisons must contain JSON objects"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(json.dumps({"metric": "summary", "has_summary_drift": True, "summary_drift_cases": [], "comparisons": [{}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="comparison names must be non-empty strings"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(
        json.dumps(
            {
                "metric": "summary",
                "has_summary_drift": True,
                "has_changed_files_context": "yes",
                "summary_drift_cases": [],
                "comparisons": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="has_changed_files_context must be a boolean"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(
        json.dumps(
            {
                "metric": "summary",
                "has_summary_drift": True,
                "summary_drift_cases": [],
                "changed_files": [""],
                "comparisons": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="changed_files must be a list of non-empty strings"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(
        json.dumps(
            {
                "metric": "summary",
                "has_summary_drift": True,
                "summary_drift_cases": [],
                "comparisons": [{"name": "sample", "summary_delta": []}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="summary_delta entries must be objects"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(
        json.dumps(
            {
                "metric": "summary",
                "has_summary_drift": True,
                "summary_drift_cases": [],
                "comparisons": [{"name": "sample", "status": "", "summary_delta": {}}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="comparison statuses must be non-empty strings"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(
        json.dumps(
            {
                "metric": "summary",
                "has_summary_drift": True,
                "summary_drift_cases": [],
                "comparisons": [{"name": "sample", "status": "changed", "summary_delta": {}, "delta": "x"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="comparison delta values must be numbers when present"):
        release.load_compare_report_summary(bad_compare)
    bad_compare.write_text(
        json.dumps(
            {
                "metric": "summary",
                "has_summary_drift": True,
                "summary_drift_cases": [],
                "comparisons": [{"name": "sample", "status": "changed", "summary_delta": {}, "delta_pct": "x"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="comparison delta_pct values must be numbers when present"):
        release.load_compare_report_summary(bad_compare)
    missing_markdown = tmp_path / "missing-compare.md"
    with pytest.raises(ValueError, match="does not exist"):
        release.load_compare_markdown_excerpt(missing_markdown)
    empty_markdown = tmp_path / "empty-compare.md"
    empty_markdown.write_text("# Corpus Baseline Compare\n", encoding="utf-8")
    with pytest.raises(ValueError, match="excerpt is empty"):
        release.load_compare_markdown_excerpt(empty_markdown)
    bad_markdown = tmp_path / "bad-compare.md"
    bad_markdown.write_text("# Corpus Baseline Compare\n\n| Case | Status | Summary Delta |\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing the table separator row"):
        release.load_compare_markdown_excerpt(bad_markdown)

    with pytest.raises(ValueError, match="missing baseline metadata"):
        release._normalize_corpus_run_record(None, label="baseline")
    with pytest.raises(ValueError, match="missing compare_report metadata"):
        release._normalize_compare_report_summary(None)
    with pytest.raises(ValueError, match="missing compare_markdown_excerpt metadata"):
        release._normalize_compare_markdown_excerpt(None)
    with pytest.raises(ValueError, match="missing compare_focus_excerpt metadata"):
        release._normalize_compare_focus_excerpt(None)
    with pytest.raises(ValueError, match="compare_report entry must be an object"):
        release._normalize_compare_report_summary(1)
    with pytest.raises(ValueError, match="compare_report.metric must be a non-empty string"):
        release._normalize_compare_report_summary({"metric": "", "has_summary_drift": False, "summary_drift_cases": [], "drift_details": [], "comparison_count": 0, "category_summary": {"parser": {"count": 0, "cases": []}, "resolver": {"count": 0, "cases": []}, "parse_health": {"count": 0, "cases": []}, "non_structural": {"count": 0, "cases": []}}})
    with pytest.raises(ValueError, match="compare_report.has_summary_drift must be a boolean"):
        release._normalize_compare_report_summary({"metric": "summary", "has_summary_drift": "no", "summary_drift_cases": [], "drift_details": [], "comparison_count": 0, "category_summary": {"parser": {"count": 0, "cases": []}, "resolver": {"count": 0, "cases": []}, "parse_health": {"count": 0, "cases": []}, "non_structural": {"count": 0, "cases": []}}})
    with pytest.raises(ValueError, match="compare_report.has_changed_files_context must be a boolean"):
        release._normalize_compare_report_summary({"metric": "summary", "has_summary_drift": False, "has_changed_files_context": "no", "summary_drift_cases": [], "drift_details": [], "comparison_count": 0, "category_summary": {"parser": {"count": 0, "cases": []}, "resolver": {"count": 0, "cases": []}, "parse_health": {"count": 0, "cases": []}, "non_structural": {"count": 0, "cases": []}}})
    with pytest.raises(ValueError, match="compare_report.comparison_count must be an integer"):
        release._normalize_compare_report_summary({"metric": "summary", "has_summary_drift": False, "summary_drift_cases": [], "drift_details": [], "comparison_count": "0", "category_summary": {"parser": {"count": 0, "cases": []}, "resolver": {"count": 0, "cases": []}, "parse_health": {"count": 0, "cases": []}, "non_structural": {"count": 0, "cases": []}}})
    with pytest.raises(ValueError, match="compare_report.summary_drift_cases must be a list of non-empty strings"):
        release._normalize_compare_report_summary({"metric": "summary", "has_summary_drift": False, "summary_drift_cases": [""], "drift_details": [], "comparison_count": 0, "category_summary": {"parser": {"count": 0, "cases": []}, "resolver": {"count": 0, "cases": []}, "parse_health": {"count": 0, "cases": []}, "non_structural": {"count": 0, "cases": []}}})
    with pytest.raises(ValueError, match="compare_report changed_files must be a list of non-empty strings"):
        release._normalize_compare_report_summary({"metric": "summary", "has_summary_drift": False, "summary_drift_cases": [], "changed_files": [""], "drift_details": [], "comparison_count": 0, "category_summary": {"parser": {"count": 0, "cases": []}, "resolver": {"count": 0, "cases": []}, "parse_health": {"count": 0, "cases": []}, "non_structural": {"count": 0, "cases": []}}})
    with pytest.raises(ValueError, match="compare_report.drift_details must be a list of non-empty strings"):
        release._normalize_compare_report_summary({"metric": "summary", "has_summary_drift": False, "summary_drift_cases": [], "drift_details": [""], "comparison_count": 0, "category_summary": {"parser": {"count": 0, "cases": []}, "resolver": {"count": 0, "cases": []}, "parse_health": {"count": 0, "cases": []}, "non_structural": {"count": 0, "cases": []}}})
    with pytest.raises(ValueError, match="compare_report.category_summary must be an object"):
        release._normalize_compare_report_summary(
            {
                "metric": "summary",
                "has_summary_drift": False,
                "summary_drift_cases": [],
                "drift_details": [],
                "comparison_count": 0,
                "category_summary": [],
            }
        )
    with pytest.raises(ValueError, match="compare_report.category_summary.parser.count must be an integer"):
        release._normalize_compare_category_summary(
            {
                "parser": {"count": "1", "cases": ["sample"]},
                "resolver": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        )
    with pytest.raises(ValueError, match="compare_report.category_summary.parser.count must match cases"):
        release._normalize_compare_category_summary(
            {
                "parser": {"count": 0, "cases": ["sample"]},
                "resolver": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        )
    with pytest.raises(ValueError, match="compare_report.category_summary.parser must be an object"):
        release._normalize_compare_category_summary(
            {
                "parser": None,
                "resolver": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        )
    with pytest.raises(ValueError, match="compare_report.category_summary.parser.cases must be a list of non-empty strings"):
        release._normalize_compare_category_summary(
            {
                "parser": {"count": 1, "cases": [""]},
                "resolver": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        )
    with pytest.raises(ValueError, match="compare_report.attribution_summary must be an object"):
        release._normalize_compare_attribution_summary([])
    with pytest.raises(ValueError, match="compare_report.attribution_summary.discovery.count must be an integer"):
        release._normalize_compare_attribution_summary(
            {
                "discovery": {"count": "1", "cases": ["sample"]},
                "symbol_extraction": {"count": 0, "cases": []},
                "call_resolution": {"count": 0, "cases": []},
                "chain_enumeration": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        )
    with pytest.raises(ValueError, match="compare_report.attribution_summary.discovery.count must match cases"):
        release._normalize_compare_attribution_summary(
            {
                "discovery": {"count": 0, "cases": ["sample"]},
                "symbol_extraction": {"count": 0, "cases": []},
                "call_resolution": {"count": 0, "cases": []},
                "chain_enumeration": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        )
    with pytest.raises(ValueError, match="compare_report.attribution_summary.discovery must be an object"):
        release._normalize_compare_attribution_summary(
            {
                "discovery": None,
                "symbol_extraction": {"count": 0, "cases": []},
                "call_resolution": {"count": 0, "cases": []},
                "chain_enumeration": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        )
    with pytest.raises(ValueError, match="compare_report.attribution_summary.discovery.cases must be a list of non-empty strings"):
        release._normalize_compare_attribution_summary(
            {
                "discovery": {"count": 1, "cases": [""]},
                "symbol_extraction": {"count": 0, "cases": []},
                "call_resolution": {"count": 0, "cases": []},
                "chain_enumeration": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        )
    with pytest.raises(ValueError, match="compare_report.owner_hints must be a list of objects"):
        release._normalize_compare_owner_hints({}, label="compare_report.owner_hints")
    with pytest.raises(ValueError, match="compare_report.owner_hints must be a list of objects"):
        release._normalize_compare_owner_hints([1], label="compare_report.owner_hints")
    with pytest.raises(ValueError, match="compare_report.owner_hints keys must be non-empty strings"):
        release._normalize_compare_owner_hints(
            [
                {
                    "key": "",
                    "label": "call-resolution",
                    "cases": ["sample"],
                    "paths": ["src/callchain/core/callgraph.py"],
                    "owners": ["@callchain-graph"],
                }
            ],
            label="compare_report.owner_hints",
        )
    with pytest.raises(ValueError, match="compare_report.owner_hints labels must be non-empty strings"):
        release._normalize_compare_owner_hints(
            [
                {
                    "key": "call_resolution",
                    "label": "",
                    "cases": ["sample"],
                    "paths": ["src/callchain/core/callgraph.py"],
                    "owners": ["@callchain-graph"],
                }
            ],
            label="compare_report.owner_hints",
        )
    with pytest.raises(ValueError, match="compare_report.owner_hints cases must be a list of non-empty strings"):
        release._normalize_compare_owner_hints(
            [
                {
                    "key": "call_resolution",
                    "label": "call-resolution",
                    "cases": [""],
                    "paths": ["src/callchain/core/callgraph.py"],
                    "owners": ["@callchain-graph"],
                }
            ],
            label="compare_report.owner_hints",
        )
    with pytest.raises(ValueError, match="compare_report.owner_hints paths must be a list of non-empty strings"):
        release._normalize_compare_owner_hints(
            [
                {
                    "key": "call_resolution",
                    "label": "call-resolution",
                    "cases": ["sample"],
                    "paths": [""],
                    "owners": ["@callchain-graph"],
                }
            ],
            label="compare_report.owner_hints",
        )
    with pytest.raises(ValueError, match="compare_report.owner_hints owners must be a list of non-empty strings"):
        release._normalize_compare_owner_hints(
            [
                {
                    "key": "call_resolution",
                    "label": "call-resolution",
                    "cases": ["sample"],
                    "paths": ["src/callchain/core/callgraph.py"],
                    "owners": [""],
                }
            ],
            label="compare_report.owner_hints",
        )
    with pytest.raises(ValueError, match="compare_report.owner_focus must be a list of objects"):
        release._normalize_compare_owner_focus({}, label="compare_report.owner_focus")
    with pytest.raises(ValueError, match="compare_report.owner_focus must be a list of objects"):
        release._normalize_compare_owner_focus([1], label="compare_report.owner_focus")
    with pytest.raises(ValueError, match="compare_report.owner_focus owners must be non-empty strings"):
        release._normalize_compare_owner_focus(
            [
                {
                    "owner": "",
                    "labels": ["call-resolution"],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                    "priority": "critical",
                    "score": 12,
                }
            ],
            label="compare_report.owner_focus",
        )
    with pytest.raises(ValueError, match="compare_report.owner_focus labels must be a list of non-empty strings"):
        release._normalize_compare_owner_focus(
            [
                {
                    "owner": "@callchain-graph",
                    "labels": [""],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                    "priority": "critical",
                    "score": 12,
                }
            ],
            label="compare_report.owner_focus",
        )
    with pytest.raises(ValueError, match="compare_report.owner_focus cases must be a list of non-empty strings"):
        release._normalize_compare_owner_focus(
            [
                {
                    "owner": "@callchain-graph",
                    "labels": ["call-resolution"],
                    "cases": [""],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                    "priority": "critical",
                    "score": 12,
                }
            ],
            label="compare_report.owner_focus",
        )
    with pytest.raises(ValueError, match="compare_report.owner_focus priority values must be one of critical/high/medium/low"):
        release._normalize_compare_owner_focus(
            [
                {
                    "owner": "@callchain-graph",
                    "labels": ["call-resolution"],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                    "priority": "urgent",
                    "score": 12,
                }
            ],
            label="compare_report.owner_focus",
        )
    with pytest.raises(ValueError, match="compare_report.owner_focus score values must be integers"):
        release._normalize_compare_owner_focus(
            [
                {
                    "owner": "@callchain-graph",
                    "labels": ["call-resolution"],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                    "priority": "critical",
                    "score": "12",
                }
            ],
            label="compare_report.owner_focus",
        )
    with pytest.raises(ValueError, match="compare_report.reviewer_candidates must be a list of objects"):
        release._normalize_compare_reviewer_candidates({}, label="compare_report.reviewer_candidates")
    with pytest.raises(ValueError, match="compare_report.reviewer_candidates must be a list of objects"):
        release._normalize_compare_reviewer_candidates([1], label="compare_report.reviewer_candidates")
    with pytest.raises(ValueError, match="compare_report.reviewer_candidates owners must be non-empty strings"):
        release._normalize_compare_reviewer_candidates(
            [
                {
                    "owner": "",
                    "kind": "user",
                    "priority": "critical",
                    "score": 12,
                    "labels": ["call-resolution"],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                }
            ],
            label="compare_report.reviewer_candidates",
        )
    with pytest.raises(ValueError, match="compare_report.reviewer_candidates kinds must be one of user/team/unsupported"):
        release._normalize_compare_reviewer_candidates(
            [
                {
                    "owner": "@callchain-graph",
                    "kind": "bot",
                    "priority": "critical",
                    "score": 12,
                    "labels": ["call-resolution"],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                }
            ],
            label="compare_report.reviewer_candidates",
        )
    with pytest.raises(ValueError, match="compare_report.reviewer_candidates priority values must be one of critical/high/medium/low"):
        release._normalize_compare_reviewer_candidates(
            [
                {
                    "owner": "@callchain-graph",
                    "kind": "user",
                    "priority": "urgent",
                    "score": 12,
                    "labels": ["call-resolution"],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                }
            ],
            label="compare_report.reviewer_candidates",
        )
    with pytest.raises(ValueError, match="compare_report.reviewer_candidates score values must be integers"):
        release._normalize_compare_reviewer_candidates(
            [
                {
                    "owner": "@callchain-graph",
                    "kind": "user",
                    "priority": "critical",
                    "score": "12",
                    "labels": ["call-resolution"],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                }
            ],
            label="compare_report.reviewer_candidates",
        )
    with pytest.raises(ValueError, match="compare_report.reviewer_candidates labels must be a list of non-empty strings"):
        release._normalize_compare_reviewer_candidates(
            [
                {
                    "owner": "@callchain-graph",
                    "kind": "user",
                    "priority": "critical",
                    "score": 12,
                    "labels": [""],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                }
            ],
            label="compare_report.reviewer_candidates",
        )
    with pytest.raises(ValueError, match="compare_report.reviewer_candidates cases must be a list of non-empty strings"):
        release._normalize_compare_reviewer_candidates(
            [
                {
                    "owner": "@callchain-graph",
                    "kind": "user",
                    "priority": "critical",
                    "score": 12,
                    "labels": ["call-resolution"],
                    "cases": [""],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                }
            ],
            label="compare_report.reviewer_candidates",
        )
    with pytest.raises(ValueError, match="compare_report.review_request_plan must be an object"):
        release._normalize_compare_review_request_plan([], label="compare_report.review_request_plan")
    with pytest.raises(ValueError, match="compare_report.review_request_plan.users must be a list of non-empty strings"):
        release._normalize_compare_review_request_plan(
            {"users": [""], "teams": [], "unsupported": []},
            label="compare_report.review_request_plan",
        )
    with pytest.raises(ValueError, match="compare_report.review_request_plan.teams must be a list of non-empty strings"):
        release._normalize_compare_review_request_plan(
            {"users": [], "teams": [""], "unsupported": []},
            label="compare_report.review_request_plan",
        )
    with pytest.raises(ValueError, match="compare_report.review_request_plan.unsupported must be a list of non-empty strings"):
        release._normalize_compare_review_request_plan(
            {"users": [], "teams": [], "unsupported": [""]},
            label="compare_report.review_request_plan",
        )
    with pytest.raises(ValueError, match="compare_focus_excerpt entry must be an object"):
        release._normalize_compare_focus_excerpt(1)
    with pytest.raises(ValueError, match="compare_focus_excerpt.content must be a non-empty string"):
        release._normalize_compare_focus_excerpt(
            {"content": "", "highlight_count": 0, "total_count": 0, "truncated": False, "source": "compare_report"}
        )
    with pytest.raises(ValueError, match="compare_focus_excerpt.highlight_count must be an integer"):
        release._normalize_compare_focus_excerpt(
            {"content": "x", "highlight_count": "0", "total_count": 0, "truncated": False, "source": "compare_report"}
        )
    with pytest.raises(ValueError, match="compare_focus_excerpt.total_count must be an integer"):
        release._normalize_compare_focus_excerpt(
            {"content": "x", "highlight_count": 0, "total_count": "0", "truncated": False, "source": "compare_report"}
        )
    with pytest.raises(ValueError, match="compare_focus_excerpt.total_count must be >= highlight_count"):
        release._normalize_compare_focus_excerpt(
            {"content": "x", "highlight_count": 2, "total_count": 1, "truncated": False, "source": "compare_report"}
        )
    with pytest.raises(ValueError, match="compare_focus_excerpt.truncated must be a boolean"):
        release._normalize_compare_focus_excerpt(
            {"content": "x", "highlight_count": 0, "total_count": 0, "truncated": "no", "source": "compare_report"}
        )
    with pytest.raises(ValueError, match="compare_focus_excerpt.source must be a non-empty string"):
        release._normalize_compare_focus_excerpt(
            {"content": "x", "highlight_count": 0, "total_count": 0, "truncated": False, "source": ""}
        )
    with pytest.raises(ValueError, match="compare_markdown_excerpt entry must be an object"):
        release._normalize_compare_markdown_excerpt(1)
    with pytest.raises(ValueError, match="compare_markdown_excerpt.content must be a non-empty string"):
        release._normalize_compare_markdown_excerpt({"content": "", "truncated": False, "table_row_count": 0})
    with pytest.raises(ValueError, match="compare_markdown_excerpt.truncated must be a boolean"):
        release._normalize_compare_markdown_excerpt({"content": "x", "truncated": "no", "table_row_count": 0})
    with pytest.raises(ValueError, match="compare_markdown_excerpt.table_row_count must be an integer"):
        release._normalize_compare_markdown_excerpt({"content": "x", "truncated": False, "table_row_count": "0"})
    assert release._format_corpus_run_line(None) == "none"
    assert release._format_corpus_run_markdown(None) == "`none`"
    assert release._format_corpus_run_markdown_row("Refresh candidate", None) == (
        "| Refresh candidate | `none` | `n/a` | `n/a` | `n/a` | `n/a` | `none` |"
    )
    artifact_only_run = {"run_id": 6, "created_at": "2026-04-07T00:00:00Z", "artifact_name": "corpus-baseline-6"}
    assert release._format_corpus_run_markdown(artifact_only_run) == (
        "`run 6`, created `2026-04-07T00:00:00Z`, artifact `corpus-baseline-6`"
    )
    assert release._format_corpus_run_markdown_row("Official baseline", artifact_only_run) == (
        "| Official baseline | `run 6` | `2026-04-07T00:00:00Z` | `n/a` | `n/a` | `n/a` | `corpus-baseline-6` |"
    )
    assert (
        release._format_corpus_run_markdown({"run_id": 5, "created_at": "2026-04-07T00:00:00Z"})
        == "`run 5`, created `2026-04-07T00:00:00Z`"
    )
    assert release._markdown_link("artifact", "https://example.com/a") == "[artifact](https://example.com/a)"
    assert release._format_compare_report_line(
        {
            "metric": "summary",
            "has_summary_drift": True,
            "summary_drift_cases": ["sample"],
            "drift_details": ["sample: functions +1"],
            "comparison_count": 1,
        }
    ) == "metric summary, drift 1 case(s), details: sample: functions +1"
    assert release._format_compare_report_markdown(
        {
            "metric": "summary",
            "has_summary_drift": True,
            "summary_drift_cases": ["sample"],
            "drift_details": ["sample: functions +1"],
            "comparison_count": 1,
            "category_summary": {
                "parser": {"count": 1, "cases": ["sample"]},
                "resolver": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            },
            "attribution_summary": {
                "discovery": {"count": 0, "cases": []},
                "symbol_extraction": {"count": 1, "cases": ["sample"]},
                "call_resolution": {"count": 0, "cases": []},
                "chain_enumeration": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            },
        }
    ) == "metric `summary`, drift `1 case(s)`, cases: `sample`"
    assert release._format_compare_category_summary_line(
        {
            "category_summary": {
                "parser": {"count": 1, "cases": ["sample"]},
                "resolver": {"count": 1, "cases": ["sample"]},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        }
    ) == "parser 1 case(s) (sample); resolver 1 case(s) (sample)"
    assert release._format_compare_category_summary_markdown(
        {
            "category_summary": {
                "parser": {"count": 1, "cases": ["sample"]},
                "resolver": {"count": 1, "cases": ["sample"]},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        }
    ) == "parser `sample`; resolver `sample`"
    assert release._format_compare_attribution_summary_line(
        {
            "attribution_summary": {
                "discovery": {"count": 1, "cases": ["sample"]},
                "symbol_extraction": {"count": 1, "cases": ["sample"]},
                "call_resolution": {"count": 0, "cases": []},
                "chain_enumeration": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        }
    ) == "discovery 1 case(s) (sample); symbol-extraction 1 case(s) (sample)"
    assert release._format_compare_attribution_summary_markdown(
        {
            "attribution_summary": {
                "discovery": {"count": 1, "cases": ["sample"]},
                "symbol_extraction": {"count": 1, "cases": ["sample"]},
                "call_resolution": {"count": 0, "cases": []},
                "chain_enumeration": {"count": 0, "cases": []},
                "parse_health": {"count": 0, "cases": []},
                "non_structural": {"count": 0, "cases": []},
            }
        }
    ) == "discovery `sample`; symbol-extraction `sample`"
    assert release._format_compare_category_summary_line(
        {"category_summary": release._empty_compare_category_summary()}
    ) == "none"
    assert release._format_compare_category_summary_markdown(
        {"category_summary": release._empty_compare_category_summary()}
    ) == "`none`"
    assert release._format_compare_attribution_summary_line(
        {"attribution_summary": release._empty_compare_attribution_summary()}
    ) == "none"
    assert release._format_compare_attribution_summary_markdown(
        {"attribution_summary": release._empty_compare_attribution_summary()}
    ) == "`none`"
    assert release._format_compare_review_hints_line(
        [
            {
                "label": "discovery",
                "cases": ["sample"],
                "paths": ["src/callchain/languages/base.py", "src/callchain/core/callgraph.py"],
                "reason": "x",
            }
        ]
    ) == "discovery -> src/callchain/languages/base.py, src/callchain/core/callgraph.py (sample)"
    assert release._format_compare_review_hints_markdown(
        [
            {
                "label": "discovery",
                "cases": ["sample"],
                "paths": ["src/callchain/languages/base.py", "src/callchain/core/callgraph.py"],
                "reason": "x",
            }
        ]
    ) == (
        "discovery -> `src/callchain/languages/base.py`, `src/callchain/core/callgraph.py` "
        "(cases: `sample`)"
    )
    assert release._format_compare_review_hints_line([]) == "none"
    assert release._format_compare_review_hints_markdown([]) == "`none`"
    assert release._format_compare_owner_hints_line(
        [
            {
                "label": "call-resolution",
                "cases": ["sample"],
                "owners": ["@callchain-languages", "@callchain-graph"],
            }
        ]
    ) == "call-resolution -> @callchain-languages, @callchain-graph (sample)"
    assert release._format_compare_owner_hints_markdown(
        [
            {
                "label": "call-resolution",
                "cases": ["sample"],
                "owners": ["@callchain-languages", "@callchain-graph"],
            }
        ]
    ) == "call-resolution -> `@callchain-languages`, `@callchain-graph` (cases: `sample`)"
    assert release._format_compare_owner_hints_line([]) == "none"
    assert release._format_compare_owner_hints_markdown([]) == "`none`"
    assert release._format_compare_owner_focus_line(
        [
            {
                "owner": "@callchain-graph",
                "labels": ["call-resolution", "parse-health"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/core/callgraph.py"],
                "priority": "critical",
                "score": 12,
            }
        ]
    ) == "critical @callchain-graph -> call-resolution, parse-health [src/callchain/core/callgraph.py] (sample)"
    assert release._format_compare_owner_focus_markdown(
        [
            {
                "owner": "@callchain-graph",
                "labels": ["call-resolution", "parse-health"],
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/core/callgraph.py"],
                "priority": "critical",
                "score": 12,
            }
        ]
    ) == "critical `@callchain-graph` -> `call-resolution`, `parse-health` [`src/callchain/core/callgraph.py`] (cases: `sample`)"
    assert release._format_compare_owner_focus_line([]) == "none"
    assert release._format_compare_owner_focus_markdown([]) == "`none`"
    assert release._format_compare_review_request_plan_line(
        {
            "users": ["@callchain-graph", "@callchain-languages"],
            "teams": ["@callchain/core-reviewers"],
            "unsupported": ["docs-team"],
        }
    ) == "users @callchain-graph, @callchain-languages; teams @callchain/core-reviewers; unsupported docs-team"
    assert release._format_compare_review_request_plan_markdown(
        {
            "users": ["@callchain-graph", "@callchain-languages"],
            "teams": ["@callchain/core-reviewers"],
            "unsupported": ["docs-team"],
        }
    ) == (
        "users `@callchain-graph`, `@callchain-languages`; teams `@callchain/core-reviewers`; "
        "unsupported `docs-team`"
    )
    assert release._format_compare_review_request_plan_line({"users": [], "teams": [], "unsupported": []}) == "none"
    assert (
        release._format_compare_review_request_plan_markdown({"users": [], "teams": [], "unsupported": []}) == "`none`"
    )
    assert release._format_compare_changed_files_context_line({"changed_files": ["a", "b"]}) == "2 file(s)"
    assert release._format_compare_changed_files_context_markdown({"changed_files": ["a", "b"]}) == "`2 file(s)`"
    assert release._format_compare_changed_file_overlap_line(
        [
            {
                "label": "symbol-extraction",
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            }
        ]
    ) == "symbol-extraction -> src/callchain/languages/python_lang.py (sample)"
    assert release._format_compare_changed_file_overlap_markdown(
        [
            {
                "label": "symbol-extraction",
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            }
        ]
    ) == "symbol-extraction -> `src/callchain/languages/python_lang.py` (cases: `sample`)"
    assert release._format_compare_changed_file_overlap_line([]) == "none"
    assert release._format_compare_changed_file_overlap_markdown([]) == "`none`"
    assert release._format_compare_changed_file_focus_line(
        [
            {
                "priority": "critical",
                "label": "call-resolution",
                "cases": ["sample"],
                "matched_changed_files": [
                    "src/callchain/languages/python_lang.py",
                    "src/callchain/core/callgraph.py",
                ],
            }
        ]
    ) == (
        "critical call-resolution -> src/callchain/languages/python_lang.py, "
        "src/callchain/core/callgraph.py (sample)"
    )
    assert release._format_compare_changed_file_focus_markdown(
        [
            {
                "priority": "critical",
                "label": "call-resolution",
                "cases": ["sample"],
                "matched_changed_files": [
                    "src/callchain/languages/python_lang.py",
                    "src/callchain/core/callgraph.py",
                ],
            }
        ]
    ) == (
        "critical `call-resolution` -> `src/callchain/languages/python_lang.py`, "
        "`src/callchain/core/callgraph.py` (cases: `sample`)"
    )
    assert release._format_compare_changed_file_focus_line([]) == "none"
    assert release._format_compare_changed_file_focus_markdown([]) == "`none`"
    assert release._format_compare_markdown_excerpt_line(
        {"content": "x", "truncated": False, "table_row_count": 1}
    ) == "1 row(s)"
    assert release._format_compare_focus_excerpt_line(
        {"content": "x", "highlight_count": 1, "total_count": 1, "truncated": False, "source": "compare_report"}
    ) == "1 highlighted case(s)"
    assert (
        release._format_compare_focus_change(
            {"summary_bits": [], "delta_pct": 4.0, "delta": None},
            False,
        )
        == "+4.0%"
    )
    assert (
        release._format_compare_focus_change(
            {"summary_bits": [], "delta_pct": None, "delta": 2.5},
            False,
        )
        == "+2.500"
    )
    assert (
        release._format_compare_focus_change(
            {"summary_bits": [], "delta_pct": None, "delta": None},
            False,
        )
        == "No summary drift"
    )
    assert release._build_compare_category_summary(
        [
            {
                "name": "parse-health",
                "status": "changed",
                "summary_delta": {"files": 0, "functions": 0, "classes": 0, "edges": 0, "chains": 0, "parse_errors": 1},
                "summary_bits": ["parse_errors +1"],
                "delta": None,
                "delta_pct": None,
            },
            {
                "name": "timing-only",
                "status": "within_threshold",
                "summary_delta": {"files": 0, "functions": 0, "classes": 0, "edges": 0, "chains": 0, "parse_errors": 0},
                "summary_bits": [],
                "delta": None,
                "delta_pct": 4.0,
            },
        ]
    ) == {
        "parser": {"count": 0, "cases": []},
        "resolver": {"count": 0, "cases": []},
        "parse_health": {"count": 1, "cases": ["parse-health"]},
        "non_structural": {"count": 1, "cases": ["timing-only"]},
    }
    assert release._build_compare_attribution_summary(
        [
            {
                "name": "mixed",
                "status": "changed",
                "summary_delta": {"files": 1, "functions": 2, "classes": 1, "edges": 3, "chains": 4, "parse_errors": 0},
                "summary_bits": ["files +1", "functions +2", "classes +1", "edges +3", "chains +4"],
                "delta": None,
                "delta_pct": None,
            },
            {
                "name": "parse-health",
                "status": "changed",
                "summary_delta": {"files": 0, "functions": 0, "classes": 0, "edges": 0, "chains": 0, "parse_errors": 1},
                "summary_bits": ["parse_errors +1"],
                "delta": None,
                "delta_pct": None,
            },
            {
                "name": "timing-only",
                "status": "within_threshold",
                "summary_delta": {"files": 0, "functions": 0, "classes": 0, "edges": 0, "chains": 0, "parse_errors": 0},
                "summary_bits": [],
                "delta": None,
                "delta_pct": 4.0,
            },
        ]
    ) == {
        "discovery": {"count": 1, "cases": ["mixed"]},
        "symbol_extraction": {"count": 1, "cases": ["mixed"]},
        "call_resolution": {"count": 1, "cases": ["mixed"]},
        "chain_enumeration": {"count": 1, "cases": ["mixed"]},
        "parse_health": {"count": 1, "cases": ["parse-health"]},
        "non_structural": {"count": 1, "cases": ["timing-only"]},
    }
    assert release._build_compare_review_hints(
        {
            "attribution_summary": {
                "discovery": {"count": 1, "cases": ["mixed"]},
                "symbol_extraction": {"count": 1, "cases": ["mixed"]},
                "call_resolution": {"count": 1, "cases": ["mixed"]},
                "chain_enumeration": {"count": 1, "cases": ["mixed"]},
                "parse_health": {"count": 1, "cases": ["parse-health"]},
                "non_structural": {"count": 1, "cases": ["timing-only"]},
            }
        }
    ) == [
        {
            "key": "discovery",
            "label": "discovery",
            "cases": ["mixed"],
            "paths": ["src/callchain/languages/base.py", "src/callchain/core/callgraph.py"],
            "reason": "Review file discovery, skip-dir rules, path filtering, and language auto-detection.",
        },
        {
            "key": "symbol_extraction",
            "label": "symbol-extraction",
            "cases": ["mixed"],
            "paths": ["src/callchain/languages/*.py"],
            "reason": "Review language parsers that extract functions, classes, methods, imports, and variables.",
        },
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["mixed"],
            "paths": ["src/callchain/core/callgraph.py", "src/callchain/languages/*.py"],
            "reason": "Review raw call extraction and cross-file edge resolution.",
        },
        {
            "key": "chain_enumeration",
            "label": "chain-enumeration",
            "cases": ["mixed"],
            "paths": ["src/callchain/core/chain_enum.py"],
            "reason": "Review chain traversal, depth/count limits, and cross-file filtering.",
        },
        {
            "key": "parse_health",
            "label": "parse-health",
            "cases": ["parse-health"],
            "paths": ["src/callchain/languages/*.py", "src/callchain/core/callgraph.py"],
            "reason": "Review parser failures, parse-error collection, and file-level error handling.",
        },
        {
            "key": "non_structural",
            "label": "non-structural",
            "cases": ["timing-only"],
            "paths": ["src/callchain/devtools/corpus.py", ".github/workflows/corpus-baseline-compare.yml"],
            "reason": "Review corpus thresholds, compare rendering, and workflow gating rather than structural analysis.",
        },
    ]
    assert release._build_compare_changed_file_overlap(
        {
            "has_changed_files_context": True,
            "changed_files": [
                "src/callchain/languages/python_lang.py",
                "src/callchain/core/callgraph.py",
                "README.md",
            ],
        },
        [
            {
                "key": "symbol_extraction",
                "label": "symbol-extraction",
                "cases": ["sample"],
                "paths": ["src/callchain/languages/*.py"],
            },
            {
                "key": "call_resolution",
                "label": "call-resolution",
                "cases": ["sample"],
                "paths": ["src/callchain/core/callgraph.py", "src/callchain/languages/*.py"],
            },
        ],
    ) == [
        {
            "key": "symbol_extraction",
            "label": "symbol-extraction",
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
        },
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["sample"],
            "matched_changed_files": [
                "src/callchain/languages/python_lang.py",
                "src/callchain/core/callgraph.py",
            ],
        },
    ]
    assert release._build_compare_changed_file_overlap(
        {"has_changed_files_context": False, "changed_files": ["src/callchain/core/callgraph.py"]},
        [
            {
                "key": "call_resolution",
                "label": "call-resolution",
                "cases": ["sample"],
                "paths": ["src/callchain/core/callgraph.py"],
            }
        ],
    ) == []
    assert release._build_compare_changed_file_overlap(
        {"has_changed_files_context": True, "changed_files": ["README.md"]},
        [
            {
                "key": "call_resolution",
                "label": "call-resolution",
                "cases": ["sample"],
                "paths": ["src/callchain/core/callgraph.py"],
            }
        ],
    ) == []
    assert release._build_compare_changed_file_focus(
        [
            {
                "key": "symbol_extraction",
                "label": "symbol-extraction",
                "cases": ["sample"],
                "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            },
            {
                "key": "call_resolution",
                "label": "call-resolution",
                "cases": ["sample"],
                "matched_changed_files": [
                    "src/callchain/languages/python_lang.py",
                    "src/callchain/core/callgraph.py",
                ],
            },
        ]
    ) == [
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["sample"],
            "matched_changed_files": [
                "src/callchain/languages/python_lang.py",
                "src/callchain/core/callgraph.py",
            ],
            "priority": "critical",
            "score": 18,
        },
        {
            "key": "symbol_extraction",
            "label": "symbol-extraction",
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/languages/python_lang.py"],
            "priority": "medium",
            "score": 8,
        },
    ]
    assert release._build_compare_changed_file_focus([]) == []
    assert release._build_compare_review_hints(None) == []
    assert release._build_compare_owner_hints(
        {
            "owner_hints": [
                {
                    "key": "call_resolution",
                    "label": "call-resolution",
                    "cases": ["sample"],
                    "paths": ["src/callchain/core/callgraph.py"],
                    "owners": ["@callchain-graph"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                    "ownerless_changed_files": [],
                }
            ]
        }
    ) == [
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["sample"],
            "paths": ["src/callchain/core/callgraph.py"],
            "owners": ["@callchain-graph"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
            "ownerless_changed_files": [],
        }
    ]
    assert release._build_compare_owner_focus(
        {
            "owner_focus": [
                {
                    "owner": "@callchain-graph",
                    "labels": ["call-resolution"],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                    "priority": "critical",
                    "score": 12,
                }
            ]
        }
    ) == [
        {
            "owner": "@callchain-graph",
            "labels": ["call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
            "priority": "critical",
            "score": 12,
        }
    ]
    assert release._build_compare_reviewer_candidates(
        {
            "reviewer_candidates": [
                {
                    "owner": "@callchain-graph",
                    "kind": "user",
                    "priority": "critical",
                    "score": 12,
                    "labels": ["call-resolution"],
                    "cases": ["sample"],
                    "matched_changed_files": ["src/callchain/core/callgraph.py"],
                }
            ]
        }
    ) == [
        {
            "owner": "@callchain-graph",
            "kind": "user",
            "priority": "critical",
            "score": 12,
            "labels": ["call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
        }
    ]
    assert release._build_compare_review_request_plan(
        {
            "review_request_plan": {
                "users": ["@callchain-graph"],
                "teams": ["@callchain/core-reviewers"],
                "unsupported": ["docs-team"],
            }
        }
    ) == {
        "users": ["@callchain-graph"],
        "teams": ["@callchain/core-reviewers"],
        "unsupported": ["docs-team"],
    }
    assert release._build_compare_owner_hints(None) == []
    assert release._build_compare_owner_focus(None) == []
    assert release._build_compare_reviewer_candidates(None) == []
    assert release._build_compare_review_request_plan(None) is None
    assert release._normalize_compare_changed_files(["./src/callchain/core/callgraph.py", "src/callchain/core/callgraph.py"], label="Corpus compare report") == [
        "src/callchain/core/callgraph.py"
    ]
    assert release._normalize_compare_owner_hints(
        [
            {
                "key": "call_resolution",
                "label": "call-resolution",
                "cases": ["sample"],
                "paths": ["src/callchain/core/callgraph.py"],
                "owners": ["@callchain-graph"],
                "matched_changed_files": ["./src/callchain/core/callgraph.py"],
                "ownerless_changed_files": [],
            }
        ],
        label="Corpus compare report owner_hints",
    ) == [
        {
            "key": "call_resolution",
            "label": "call-resolution",
            "cases": ["sample"],
            "paths": ["src/callchain/core/callgraph.py"],
            "owners": ["@callchain-graph"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
            "ownerless_changed_files": [],
        }
    ]
    assert release._normalize_compare_owner_focus(
        [
            {
                "owner": "@callchain-graph",
                "labels": ["call-resolution"],
                "cases": ["sample"],
                "matched_changed_files": ["./src/callchain/core/callgraph.py"],
                "priority": "critical",
                "score": 12,
            }
        ],
        label="Corpus compare report owner_focus",
    ) == [
        {
            "owner": "@callchain-graph",
            "labels": ["call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
            "priority": "critical",
            "score": 12,
        }
    ]
    assert release._normalize_compare_reviewer_candidates(
        [
            {
                "owner": "@callchain-graph",
                "kind": "user",
                "priority": "critical",
                "score": 12,
                "labels": ["call-resolution"],
                "cases": ["sample"],
                "matched_changed_files": ["./src/callchain/core/callgraph.py"],
            }
        ],
        label="Corpus compare report reviewer_candidates",
    ) == [
        {
            "owner": "@callchain-graph",
            "kind": "user",
            "priority": "critical",
            "score": 12,
            "labels": ["call-resolution"],
            "cases": ["sample"],
            "matched_changed_files": ["src/callchain/core/callgraph.py"],
        }
    ]
    assert release._normalize_compare_review_request_plan(
        {
            "users": ["@callchain-graph"],
            "teams": ["@callchain/core-reviewers"],
            "unsupported": ["docs-team"],
        },
        label="Corpus compare report review_request_plan",
    ) == {
        "users": ["@callchain-graph"],
        "teams": ["@callchain/core-reviewers"],
        "unsupported": ["docs-team"],
    }
    assert release._compare_changed_file_priority(["src/callchain/core/callgraph.py"]) == "critical"
    assert release._compare_changed_file_priority(["src/callchain/core/chain_enum.py"]) == "critical"
    assert release._compare_changed_file_priority(["src/callchain/core/cache.py"]) == "high"
    assert release._compare_changed_file_priority(["src/callchain/languages/base.py"]) == "high"
    assert release._compare_changed_file_priority(["src/callchain/languages/python_lang.py"]) == "medium"
    assert release._compare_changed_file_priority(["src/callchain/devtools/corpus.py"]) == "low"
    assert release._compare_changed_file_priority([".github/workflows/corpus-baseline-compare.yml"]) == "low"
    assert release._compare_changed_file_priority(["README.md"]) == "low"
    assert release._compare_changed_file_score(["src/callchain/languages/python_lang.py"], ["sample"]) == 8
    assert release._changed_file_weight("src/callchain/core/chain_enum.py") == 8
    assert release._changed_file_weight("src/callchain/languages/base.py") == 6
    assert release._changed_file_weight("src/callchain/devtools/corpus.py") == 3
    assert release._changed_file_weight(".github/workflows/corpus-baseline-compare.yml") == 2
    with pytest.raises(ValueError, match="changed_files must be a list of non-empty strings"):
        release._normalize_compare_changed_files({}, label="Corpus compare report")
    with pytest.raises(ValueError, match="changed_files must be a list of non-empty strings"):
        release._normalize_compare_changed_files([""], label="Corpus compare report")
    assert release._format_local_summary_delta({"functions": 1, "classes": 0, "edges": -1}) == [
        "functions +1",
        "edges -1",
    ]
    with pytest.raises(ValueError, match="summary_delta keys must be non-empty strings"):
        release._format_local_summary_delta({"": 1})
    with pytest.raises(ValueError, match="summary_delta values must be integers"):
        release._format_local_summary_delta({"functions": "1"})
    assert release._short_sha("1234567890abcdef") == "1234567"

    with pytest.raises(ValueError, match="missing baseline metadata"):
        release.validate_corpus_baseline_state({})
    with pytest.raises(ValueError, match="baseline entry must be an object"):
        release.validate_corpus_baseline_state({"baseline": 1})
    with pytest.raises(ValueError, match="baseline.run_id"):
        release.validate_corpus_baseline_state({"baseline": {"run_id": "x", "created_at": "2026-04-07T00:00:00Z"}})
    with pytest.raises(ValueError, match="baseline.created_at must be a non-empty string"):
        release.validate_corpus_baseline_state({"baseline": {"run_id": 1, "created_at": ""}})
    with pytest.raises(ValueError, match="ISO-8601"):
        release.validate_corpus_baseline_state({"baseline": {"run_id": 1, "created_at": "bad-date"}})
    with pytest.raises(ValueError, match="baseline.html_url must be a string"):
        release.validate_corpus_baseline_state({"baseline": {"run_id": 1, "created_at": "2026-04-07T00:00:00Z", "html_url": 1}})
    with pytest.raises(ValueError, match="baseline.artifact_id must be an integer"):
        release.validate_corpus_baseline_state(
            {"baseline": {"run_id": 1, "created_at": "2026-04-07T00:00:00Z", "artifact_id": "x"}}
        )
    with pytest.raises(ValueError, match="baseline.artifact_expired must be a boolean"):
        release.validate_corpus_baseline_state(
            {"baseline": {"run_id": 1, "created_at": "2026-04-07T00:00:00Z", "artifact_expired": "no"}}
        )
    with pytest.raises(ValueError, match="refresh.pr_number must be an integer"):
        release.validate_corpus_baseline_state(
            {
                "baseline": {"run_id": 2, "created_at": "2026-04-08T00:00:00Z"},
                "refresh": {"run_id": 3, "created_at": "2026-04-09T00:00:00Z", "pr_number": "42"},
            }
        )
    release.validate_corpus_baseline_state(
        {
            "baseline": {"run_id": 2, "created_at": "2026-04-08T00:00:00Z"},
            "refresh": {"run_id": 1, "created_at": "2026-04-07T00:00:00Z"},
        }
    )
    assert release.summarize_corpus_baseline_state({"baseline": None, "refresh": None})["status"] == "missing_official_baseline"
    allowed_report = release.summarize_corpus_baseline_state(
        {
            "baseline": {"run_id": 2, "created_at": "2026-04-08T00:00:00Z"},
            "refresh": {"run_id": 3, "created_at": "2026-04-09T00:00:00Z"},
        },
        allow_pending_refresh=True,
    )
    assert allowed_report["status"] == "pending_refresh_allowed"

    argv = sys.argv[:]
    sys.argv = ["callchain.devtools.release", "validate", "--project-root", str(project)]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with pytest.raises(SystemExit, match="0"):
                runpy.run_module("callchain.devtools.release", run_name="__main__")
    finally:
        sys.argv = argv


def test_release_main_returns_error_for_unsupported_command(monkeypatch):
    error_messages: list[str] = []

    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self, argv=None: argparse.Namespace(
            command="unsupported",
            project_root=".",
            expected_tag=None,
            version="0.1.0",
            release_date="2026-04-07",
        ),
    )
    monkeypatch.setattr(argparse.ArgumentParser, "error", lambda self, message: error_messages.append(message))

    result = release.main([])

    assert result == 2
    assert error_messages == ["Unsupported command: unsupported"]


def _write_project_fixture(tmp_path: Path, *, version: str, changelog_extra: str = "") -> Path:
    project = tmp_path / "project"
    (project / "src/callchain").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        f'[project]\nname = "callchain"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (project / "src/callchain/__init__.py").write_text(
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )
    (project / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n"
        f"{changelog_extra}\n"
        f"## [{version}] - 2026-04-06\n\n"
        "### Added\n"
        "- initial release\n",
        encoding="utf-8",
    )
    (project / "CITATION.cff").write_text(
        "cff-version: 1.2.0\n"
        'message: "If you use CallChain, please cite it using this metadata."\n'
        "title: CallChain\n"
        "type: software\n"
        "license: Apache-2.0\n"
        "version: "
        f"{version}\n"
        "date-released: 2026-04-06\n",
        encoding="utf-8",
    )
    return project


def _write_corpus_baseline_state(
    tmp_path: Path,
    *,
    baseline: dict[str, object] | None,
    refresh: dict[str, object] | None,
    compare: dict[str, object] | None = None,
) -> Path:
    path = tmp_path / "corpus-baseline-state.json"
    payload = {"baseline": baseline, "compare": compare, "refresh": refresh}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_compare_report(
    tmp_path: Path,
    *,
    drift_cases: list[tuple[str, dict[str, int]]],
    changed_files: list[str] | None = None,
    owner_hints: list[dict[str, Any]] | None = None,
    owner_focus: list[dict[str, Any]] | None = None,
    reviewer_candidates: list[dict[str, Any]] | None = None,
    review_request_plan: dict[str, list[str]] | None = None,
) -> Path:
    path = tmp_path / "corpus-baseline-compare.json"
    comparisons = [
        {
            "name": name,
            "status": "changed",
            "summary_delta": summary_delta,
        }
        for name, summary_delta in drift_cases
    ]
    path.write_text(
        json.dumps(
            {
                "metric": "summary",
                "has_summary_drift": bool(drift_cases),
                "has_changed_files_context": changed_files is not None,
                "changed_files": changed_files or [],
                "summary_drift_cases": [name for name, _ in drift_cases],
                "comparisons": comparisons,
                "owner_hints": owner_hints or [],
                "owner_focus": owner_focus or [],
                "reviewer_candidates": reviewer_candidates or [],
                "review_request_plan": review_request_plan or {"users": [], "teams": [], "unsupported": []},
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_compare_markdown(tmp_path: Path, lines: list[str], *, name: str = "corpus-baseline-compare.md") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
