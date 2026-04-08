"""Release tooling for version bumps and metadata validation."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

PYPROJECT_PATH = Path("pyproject.toml")
INIT_PATH = Path("src/callchain/__init__.py")
CHANGELOG_PATH = Path("CHANGELOG.md")
CITATION_PATH = Path("CITATION.cff")
UNRELEASED_HEADING = "## [Unreleased]"
RELEASE_CORPUS_AUDIT_MARKER = "callchain-release-corpus-audit"

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|(?:\.post|\.dev)\d+)?$")
_PYPROJECT_VERSION_RE = re.compile(r'(?m)^version = "([^"]+)"$')
_INIT_VERSION_RE = re.compile(r'(?m)^__version__ = "([^"]+)"$')
_VERSION_HEADING_RE = re.compile(r"(?m)^## \[([^\]]+)\] - ")
_CITATION_VERSION_RE = re.compile(r'(?m)^version: "?([^"\n]+)"?$')
_CITATION_DATE_RE = re.compile(r'(?m)^date-released: "?([^"\n]+)"?$')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release helpers for CallChain.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate version metadata and changelog consistency.")
    validate_parser.add_argument("--project-root", default=".", help="Project root to validate.")
    validate_parser.add_argument("--expected-tag", default=None, help="Optional release tag to compare against.")
    validate_parser.add_argument(
        "--corpus-baseline-state",
        default=None,
        help="Optional JSON file describing the latest official baseline run and latest refresh candidate run.",
    )
    validate_parser.add_argument(
        "--allow-pending-corpus-refresh",
        action="store_true",
        help="Allow releases to proceed even if a newer corpus baseline refresh candidate exists.",
    )

    bump_parser = subparsers.add_parser("bump", help="Bump project version and roll unreleased changelog entries.")
    bump_parser.add_argument("version", help="New version string.")
    bump_parser.add_argument("--project-root", default=".", help="Project root to update.")
    bump_parser.add_argument(
        "--date",
        dest="release_date",
        default=date.today().isoformat(),
        help="Release date in YYYY-MM-DD format.",
    )

    corpus_state_parser = subparsers.add_parser(
        "corpus-state",
        help="Render a human-readable summary of release-time corpus baseline state.",
    )
    corpus_state_parser.add_argument("--state", required=True, help="Path to the corpus baseline state JSON file.")
    corpus_state_parser.add_argument(
        "--allow-pending-corpus-refresh",
        action="store_true",
        help="Render the state as if pending refresh candidates are allowed.",
    )
    corpus_state_group = corpus_state_parser.add_mutually_exclusive_group()
    corpus_state_group.add_argument("--json", action="store_true", help="Emit JSON instead of a text summary.")
    corpus_state_group.add_argument(
        "--markdown",
        action="store_true",
        help="Emit a Markdown summary suitable for release job summaries.",
    )
    corpus_state_group.add_argument(
        "--release-notes",
        action="store_true",
        help="Emit a GitHub Release body section for corpus audit evidence.",
    )
    corpus_state_parser.add_argument("--output", default=None, help="Optional output file for the summary.")
    corpus_state_parser.add_argument(
        "--compare-report",
        default=None,
        help="Optional corpus-baseline-compare JSON report to fold into the rendered release corpus state.",
    )
    corpus_state_parser.add_argument(
        "--compare-markdown",
        default=None,
        help="Optional corpus-baseline-compare Markdown report to excerpt into the rendered release corpus state.",
    )
    corpus_state_parser.add_argument("--release-tag", default=None, help="Optional release tag to show in release notes mode.")
    corpus_state_parser.add_argument(
        "--workflow-run-url",
        default=None,
        help="Optional GitHub Actions workflow run URL for release notes mode.",
    )
    corpus_state_parser.add_argument(
        "--state-artifact-url",
        default=None,
        help="Optional release-corpus-state artifact URL for release notes mode.",
    )
    corpus_state_parser.add_argument(
        "--dist-artifact-url",
        default=None,
        help="Optional release-dist artifact URL for release notes mode.",
    )

    args = parser.parse_args(argv)

    if args.command == "validate":
        project_root = Path(args.project_root).resolve()
        validate_project(
            project_root,
            expected_tag=args.expected_tag,
            corpus_baseline_state=Path(args.corpus_baseline_state).resolve() if args.corpus_baseline_state else None,
            allow_pending_corpus_refresh=args.allow_pending_corpus_refresh,
        )
        print("Release metadata validation passed.")
        return 0

    if args.command == "bump":
        project_root = Path(args.project_root).resolve()
        bump_project_version(project_root, args.version, args.release_date)
        print(f"Bumped project version to {args.version}.")
        return 0

    if args.command == "corpus-state":
        state = load_corpus_baseline_state(Path(args.state).resolve())
        if args.compare_report:
            state = attach_compare_report_summary(state, Path(args.compare_report).resolve())
        if args.compare_markdown:
            state = attach_compare_markdown_excerpt(state, Path(args.compare_markdown).resolve())
        report = summarize_corpus_baseline_state(
            state,
            allow_pending_refresh=args.allow_pending_corpus_refresh,
        )
        if args.json:
            rendered = json.dumps(report, indent=2)
        elif args.release_notes:
            rendered = format_corpus_baseline_release_notes(
                report,
                release_tag=args.release_tag,
                workflow_run_url=args.workflow_run_url,
                state_artifact_url=args.state_artifact_url,
                dist_artifact_url=args.dist_artifact_url,
            )
        elif args.markdown:
            rendered = format_corpus_baseline_state_markdown(report)
        else:
            rendered = format_corpus_baseline_state(report)
        _write_output(rendered, args.output)
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2


def validate_project(
    project_root: Path,
    expected_tag: str | None = None,
    *,
    corpus_baseline_state: Path | None = None,
    allow_pending_corpus_refresh: bool = False,
) -> None:
    """Validate release-critical metadata consistency."""
    pyproject_text = _read_text(project_root / PYPROJECT_PATH)
    init_text = _read_text(project_root / INIT_PATH)
    changelog_text = _read_text(project_root / CHANGELOG_PATH)
    citation_text = _read_text(project_root / CITATION_PATH)

    pyproject_version = _extract_single(_PYPROJECT_VERSION_RE, pyproject_text, "pyproject version")
    init_version = _extract_single(_INIT_VERSION_RE, init_text, "__version__")

    if pyproject_version != init_version:
        raise ValueError(
            f"Version mismatch: pyproject.toml has {pyproject_version!r}, "
            f"but src/callchain/__init__.py has {init_version!r}."
        )

    if UNRELEASED_HEADING not in changelog_text:
        raise ValueError("CHANGELOG.md is missing the '## [Unreleased]' section.")

    release_date = _extract_release_date(changelog_text, pyproject_version)
    citation_version = _extract_single(_CITATION_VERSION_RE, citation_text, "CITATION.cff version")
    citation_date = _extract_single(_CITATION_DATE_RE, citation_text, "CITATION.cff date-released")

    if citation_version != pyproject_version:
        raise ValueError(
            f"Version mismatch: CITATION.cff has {citation_version!r}, "
            f"but pyproject.toml has {pyproject_version!r}."
        )
    if citation_date != release_date:
        raise ValueError(
            f"Release date mismatch: CITATION.cff has {citation_date!r}, "
            f"but CHANGELOG.md has {release_date!r} for version {pyproject_version!r}."
        )

    if expected_tag is not None:
        normalized_tag = expected_tag.lstrip("v")
        if pyproject_version != normalized_tag:
            raise ValueError(
                f"Expected release tag {normalized_tag!r} does not match project version {pyproject_version!r}."
            )

    if corpus_baseline_state is not None:
        state = load_corpus_baseline_state(corpus_baseline_state)
        validate_corpus_baseline_state(state, allow_pending_refresh=allow_pending_corpus_refresh)


def load_corpus_baseline_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Corpus baseline state file {path} does not exist.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corpus baseline state file {path} is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Corpus baseline state must be a JSON object.")
    return payload


def attach_compare_report_summary(state: dict[str, Any], compare_report_path: Path) -> dict[str, Any]:
    compare_report = load_compare_report_summary(compare_report_path)
    enriched = dict(state)
    compare_focus_excerpt = compare_report.pop("focus_excerpt", None)
    enriched["compare_report"] = compare_report
    if compare_focus_excerpt is not None:
        enriched["compare_focus_excerpt"] = compare_focus_excerpt
    return enriched


def attach_compare_markdown_excerpt(state: dict[str, Any], compare_markdown_path: Path) -> dict[str, Any]:
    enriched = dict(state)
    enriched["compare_markdown_excerpt"] = load_compare_markdown_excerpt(compare_markdown_path)
    return enriched


def load_compare_report_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Corpus compare report file {path} does not exist.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corpus compare report file {path} is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Corpus compare report must be a JSON object.")

    metric = payload.get("metric")
    if not isinstance(metric, str) or not metric:
        raise ValueError("Corpus compare report metric must be a non-empty string.")

    has_summary_drift = payload.get("has_summary_drift")
    if not isinstance(has_summary_drift, bool):
        raise ValueError("Corpus compare report has_summary_drift must be a boolean.")
    has_changed_files_context = payload.get("has_changed_files_context", False)
    if not isinstance(has_changed_files_context, bool):
        raise ValueError("Corpus compare report has_changed_files_context must be a boolean when present.")

    drift_cases = payload.get("summary_drift_cases")
    if not isinstance(drift_cases, list) or any(not isinstance(item, str) or not item for item in drift_cases):
        raise ValueError("Corpus compare report summary_drift_cases must be a list of non-empty strings.")
    changed_files = _normalize_compare_changed_files(payload.get("changed_files", []), label="Corpus compare report")
    if changed_files:
        has_changed_files_context = True

    comparisons = payload.get("comparisons")
    if not isinstance(comparisons, list):
        raise ValueError("Corpus compare report comparisons must be a list.")

    drift_details: list[str] = []
    normalized_comparisons: list[dict[str, Any]] = []
    for item in comparisons:
        if not isinstance(item, dict):
            raise ValueError("Corpus compare report comparisons must contain JSON objects.")
        name = item.get("name")
        summary_delta = item.get("summary_delta", {})
        if not isinstance(name, str) or not name:
            raise ValueError("Corpus compare report comparison names must be non-empty strings.")
        if not isinstance(summary_delta, dict):
            raise ValueError("Corpus compare report summary_delta entries must be objects.")
        formatted_delta = _format_local_summary_delta(summary_delta)
        status = item.get("status")
        if status is None:
            status = "changed" if formatted_delta else "unchanged"
        if not isinstance(status, str) or not status:
            raise ValueError("Corpus compare report comparison statuses must be non-empty strings.")
        delta = item.get("delta")
        if delta is not None and not isinstance(delta, (int, float)):
            raise ValueError("Corpus compare report comparison delta values must be numbers when present.")
        delta_pct = item.get("delta_pct")
        if delta_pct is not None and not isinstance(delta_pct, (int, float)):
            raise ValueError("Corpus compare report comparison delta_pct values must be numbers when present.")
        if formatted_delta:
            drift_details.append(f"{name}: {', '.join(formatted_delta)}")
        normalized_comparisons.append(
            {
                "name": name,
                "status": status,
                "summary_delta": summary_delta,
                "summary_bits": formatted_delta,
                "delta": delta,
                "delta_pct": delta_pct,
            }
        )

    focus_excerpt = _build_compare_focus_excerpt(metric, normalized_comparisons)
    return {
        "metric": metric,
        "has_summary_drift": has_summary_drift,
        "has_changed_files_context": has_changed_files_context,
        "changed_files": changed_files,
        "summary_drift_cases": drift_cases,
        "drift_details": drift_details,
        "comparison_count": len(comparisons),
        "category_summary": _build_compare_category_summary(normalized_comparisons),
        "attribution_summary": _build_compare_attribution_summary(normalized_comparisons),
        "owner_hints": _normalize_compare_owner_hints(
            payload.get("owner_hints"),
            label="Corpus compare report owner_hints",
        ),
        "owner_focus": _normalize_compare_owner_focus(
            payload.get("owner_focus"),
            label="Corpus compare report owner_focus",
        ),
        "reviewer_candidates": _normalize_compare_reviewer_candidates(
            payload.get("reviewer_candidates"),
            label="Corpus compare report reviewer_candidates",
        ),
        "review_request_plan": _normalize_compare_review_request_plan(
            payload.get("review_request_plan"),
            label="Corpus compare report review_request_plan",
        ),
        "focus_excerpt": focus_excerpt,
    }


def load_compare_markdown_excerpt(path: Path, *, max_table_rows: int = 5) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Corpus compare markdown file {path} does not exist.")
    text = path.read_text(encoding="utf-8")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0] == "# Corpus Baseline Compare":
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    if not lines:
        raise ValueError("Corpus compare markdown excerpt is empty.")

    table_header_index = next((idx for idx, line in enumerate(lines) if line.startswith("| ")), None)
    if table_header_index is None:
        excerpt_lines = lines
        table_row_count = 0
        truncated = False
    else:
        excerpt_lines = lines[:table_header_index]
        table_lines = lines[table_header_index:]
        if len(table_lines) < 2:
            raise ValueError("Corpus compare markdown excerpt is missing the table separator row.")
        header_lines = table_lines[:2]
        body_lines = [line for line in table_lines[2:] if line.startswith("| ")]
        table_row_count = len(body_lines)
        visible_rows = body_lines[:max_table_rows]
        truncated = table_row_count > len(visible_rows)
        excerpt_lines.extend(header_lines)
        excerpt_lines.extend(visible_rows)
        if truncated:
            excerpt_lines.append(f"_... {table_row_count - len(visible_rows)} more compare row(s) in the artifact._")

    content = "\n".join(excerpt_lines).strip()
    return {
        "content": content,
        "truncated": truncated,
        "table_row_count": table_row_count,
    }


def validate_corpus_baseline_state(state: dict[str, Any], *, allow_pending_refresh: bool = False) -> None:
    report = summarize_corpus_baseline_state(state, allow_pending_refresh=allow_pending_refresh)
    if report["baseline"] is None:
        raise ValueError("Corpus baseline state is missing baseline metadata.")
    if report["pending_refresh"] and not allow_pending_refresh:
        refresh = report["refresh"]
        baseline = report["baseline"]
        assert refresh is not None
        assert baseline is not None
        refresh_pr = refresh.get("pr_number")
        refresh_pr_text = f" for PR #{refresh_pr}" if isinstance(refresh_pr, int) else ""
        artifact_name = refresh.get("artifact_name")
        artifact_text = f", artifact {artifact_name!r}" if isinstance(artifact_name, str) and artifact_name else ""
        raise ValueError(
            "Pending corpus baseline refresh candidate detected: "
            f"refresh run {refresh['run_id']}{refresh_pr_text} at {refresh['created_at']} is newer than "
            f"official baseline run {baseline['run_id']} at {baseline['created_at']}{artifact_text}. "
            "Review or promote the refresh candidate before publishing a release."
        )


def summarize_corpus_baseline_state(
    state: dict[str, Any],
    *,
    allow_pending_refresh: bool = False,
) -> dict[str, Any]:
    baseline = _normalize_corpus_run_record(state.get("baseline"), label="baseline", allow_missing=True)
    compare = _normalize_corpus_run_record(state.get("compare"), label="compare", allow_missing=True)
    compare_report = _normalize_compare_report_summary(state.get("compare_report"), allow_missing=True)
    compare_focus_excerpt = _normalize_compare_focus_excerpt(
        state.get("compare_focus_excerpt"),
        allow_missing=True,
    )
    compare_markdown_excerpt = _normalize_compare_markdown_excerpt(
        state.get("compare_markdown_excerpt"),
        allow_missing=True,
    )
    refresh = _normalize_corpus_run_record(state.get("refresh"), label="refresh", allow_missing=True)

    baseline_created = _parse_timestamp(baseline["created_at"], label="baseline.created_at") if baseline else None
    refresh_created = _parse_timestamp(refresh["created_at"], label="refresh.created_at") if refresh else None
    pending_refresh = bool(
        baseline is not None
        and refresh is not None
        and baseline_created is not None
        and refresh_created is not None
        and refresh_created > baseline_created
    )

    if baseline is None:
        status = "missing_official_baseline"
        recommendation = "Capture or locate an official Corpus Baseline run before publishing a release."
        notes = ["No official `Corpus Baseline` run metadata was found for this release."]
    elif pending_refresh:
        status = "pending_refresh_allowed" if allow_pending_refresh else "pending_refresh_candidate"
        recommendation = (
            "Pending refresh candidate accepted for this release run."
            if allow_pending_refresh
            else "Review or promote the newer Corpus Baseline Refresh candidate before publishing."
        )
        notes = [
            "A newer `Corpus Baseline Refresh` candidate exists."
            + (" The current run is configured to allow it." if allow_pending_refresh else "")
        ]
    elif refresh is None:
        status = "official_baseline_only"
        recommendation = "Official baseline is the only release-time corpus reference."
        notes = ["No refresh candidate exists; the official baseline is the current release reference."]
    else:
        status = "official_baseline_current"
        recommendation = "Official baseline is current relative to the latest refresh candidate."
        notes = ["The latest refresh candidate is not newer than the official baseline."]

    compare_review_hints = _build_compare_review_hints(compare_report)
    compare_owner_hints = _build_compare_owner_hints(compare_report)
    compare_owner_focus = _build_compare_owner_focus(compare_report)
    compare_reviewer_candidates = _build_compare_reviewer_candidates(compare_report)
    compare_review_request_plan = _build_compare_review_request_plan(compare_report)
    compare_changed_file_overlap = _build_compare_changed_file_overlap(compare_report, compare_review_hints)
    compare_changed_file_focus = _build_compare_changed_file_focus(compare_changed_file_overlap)
    return {
        "status": status,
        "allow_pending_refresh": allow_pending_refresh,
        "pending_refresh": pending_refresh,
        "recommendation": recommendation,
        "notes": notes,
        "baseline": baseline,
        "compare": compare,
        "compare_report": compare_report,
        "compare_review_hints": compare_review_hints,
        "compare_owner_hints": compare_owner_hints,
        "compare_owner_focus": compare_owner_focus,
        "compare_reviewer_candidates": compare_reviewer_candidates,
        "compare_review_request_plan": compare_review_request_plan,
        "compare_changed_file_overlap": compare_changed_file_overlap,
        "compare_changed_file_focus": compare_changed_file_focus,
        "compare_focus_excerpt": compare_focus_excerpt,
        "compare_markdown_excerpt": compare_markdown_excerpt,
        "refresh": refresh,
    }


def format_corpus_baseline_state(report: dict[str, Any]) -> str:
    compare_review_hints = report.get("compare_review_hints", [])
    compare_owner_hints = report.get("compare_owner_hints", [])
    compare_owner_focus = report.get("compare_owner_focus", [])
    compare_review_request_plan = report.get("compare_review_request_plan")
    compare_changed_file_overlap = report.get("compare_changed_file_overlap", [])
    compare_changed_file_focus = report.get("compare_changed_file_focus", [])
    lines = ["Release corpus baseline state:"]
    lines.append(f"  status: {report['status']}")
    lines.append(f"  pending refresh allowed: {report['allow_pending_refresh']}")
    lines.append(f"  recommendation: {report['recommendation']}")
    lines.append("  official baseline: " + _format_corpus_run_line(report["baseline"]))
    lines.append("  latest branch compare: " + _format_corpus_run_line(report["compare"]))
    if report["compare_report"] is not None:
        lines.append("  latest branch compare drift: " + _format_compare_report_line(report["compare_report"]))
        lines.append("  latest branch compare categories: " + _format_compare_category_summary_line(report["compare_report"]))
        lines.append("  latest branch compare attribution: " + _format_compare_attribution_summary_line(report["compare_report"]))
        lines.append("  latest branch compare review hints: " + _format_compare_review_hints_line(compare_review_hints))
        if compare_review_request_plan:
            lines.append(
                "  latest branch compare review-request dry-run: "
                + _format_compare_review_request_plan_line(compare_review_request_plan)
            )
        if compare_owner_focus:
            lines.append("  latest branch compare owner focus: " + _format_compare_owner_focus_line(compare_owner_focus))
        if compare_owner_hints:
            lines.append("  latest branch compare owner hints: " + _format_compare_owner_hints_line(compare_owner_hints))
        if report["compare_report"]["has_changed_files_context"]:
            lines.append("  latest branch compare changed files: " + _format_compare_changed_files_context_line(report["compare_report"]))
            lines.append(
                "  latest branch compare changed-file overlap: "
                + _format_compare_changed_file_overlap_line(compare_changed_file_overlap)
            )
            lines.append(
                "  latest branch compare changed-file focus: "
                + _format_compare_changed_file_focus_line(compare_changed_file_focus)
            )
    if report["compare_focus_excerpt"] is not None:
        lines.append(
            "  latest branch compare focus: "
            + _format_compare_focus_excerpt_line(report["compare_focus_excerpt"])
        )
    elif report["compare_markdown_excerpt"] is not None:
        lines.append(
            "  latest branch compare excerpt: "
            + _format_compare_markdown_excerpt_line(report["compare_markdown_excerpt"])
        )
    lines.append("  refresh candidate: " + _format_corpus_run_line(report["refresh"]))
    if report["notes"]:
        lines.append("  notes:")
        for note in report["notes"]:
            lines.append(f"    - {note}")
    return "\n".join(lines)


def format_corpus_baseline_state_markdown(report: dict[str, Any], *, heading: str = "## Release Corpus State") -> str:
    compare_review_hints = report.get("compare_review_hints", [])
    compare_owner_hints = report.get("compare_owner_hints", [])
    compare_owner_focus = report.get("compare_owner_focus", [])
    compare_review_request_plan = report.get("compare_review_request_plan")
    compare_changed_file_overlap = report.get("compare_changed_file_overlap", [])
    compare_changed_file_focus = report.get("compare_changed_file_focus", [])
    lines = [
        heading,
        "",
        f"- Status: `{report['status']}`",
        f"- Pending refresh allowed: `{str(report['allow_pending_refresh']).lower()}`",
        f"- Recommendation: {report['recommendation']}",
        "",
        "| Record | Run | Created | Branch | Commit | Event | Artifact |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        _format_corpus_run_markdown_row("Official baseline", report["baseline"]),
        _format_corpus_run_markdown_row("Latest branch compare", report["compare"]),
        _format_corpus_run_markdown_row("Refresh candidate", report["refresh"]),
    ]
    if report["compare_report"] is not None:
        lines.extend(
            [
                "",
                f"- Latest branch compare drift: {_format_compare_report_markdown(report['compare_report'])}",
                f"- Drift categories: {_format_compare_category_summary_markdown(report['compare_report'])}",
                f"- Drift attribution: {_format_compare_attribution_summary_markdown(report['compare_report'])}",
                f"- Likely modules to review: {_format_compare_review_hints_markdown(compare_review_hints)}",
            ]
        )
        if compare_review_request_plan:
            lines.append(f"- Review-request dry-run: {_format_compare_review_request_plan_markdown(compare_review_request_plan)}")
        if compare_owner_focus:
            lines.append(f"- Owner focus: {_format_compare_owner_focus_markdown(compare_owner_focus)}")
        if compare_owner_hints:
            lines.append(f"- Likely owners to review: {_format_compare_owner_hints_markdown(compare_owner_hints)}")
        if report["compare_report"]["has_changed_files_context"]:
            lines.extend(
                [
                    f"- Changed files context: {_format_compare_changed_files_context_markdown(report['compare_report'])}",
                    f"- Changed-file overlap: {_format_compare_changed_file_overlap_markdown(compare_changed_file_overlap)}",
                    f"- Changed-file focus: {_format_compare_changed_file_focus_markdown(compare_changed_file_focus)}",
                ]
            )
        drift_details = report["compare_report"]["drift_details"]
        if drift_details:
            lines.extend(["- Drift details:"])
            lines.extend(f"  - `{detail.split(': ', 1)[0]}`: {detail.split(': ', 1)[1]}" for detail in drift_details)
    if report["compare_focus_excerpt"] is not None:
        lines.extend(
            [
                "",
                f"- Latest branch compare focus: {_format_compare_focus_excerpt_line(report['compare_focus_excerpt'])}",
                "",
                "<details>",
                "<summary>Latest branch compare focus</summary>",
                "",
                report["compare_focus_excerpt"]["content"],
                "",
                "</details>",
            ]
        )
    elif report["compare_markdown_excerpt"] is not None:
        lines.extend(
            [
                "",
                f"- Latest branch compare excerpt: {_format_compare_markdown_excerpt_line(report['compare_markdown_excerpt'])}",
                "",
                "<details>",
                "<summary>Latest branch compare excerpt</summary>",
                "",
                report["compare_markdown_excerpt"]["content"],
                "",
                "</details>",
            ]
        )
    if report["notes"]:
        lines.extend([""])
        lines.extend(f"> {note}" for note in report["notes"])
    return "\n".join(lines)


def format_corpus_baseline_release_notes(
    report: dict[str, Any],
    *,
    release_tag: str | None = None,
    workflow_run_url: str | None = None,
    state_artifact_url: str | None = None,
    dist_artifact_url: str | None = None,
) -> str:
    compare_review_hints = report.get("compare_review_hints", [])
    compare_owner_hints = report.get("compare_owner_hints", [])
    compare_owner_focus = report.get("compare_owner_focus", [])
    compare_review_request_plan = report.get("compare_review_request_plan")
    compare_changed_file_overlap = report.get("compare_changed_file_overlap", [])
    compare_changed_file_focus = report.get("compare_changed_file_focus", [])
    heading = "## Release Corpus Audit"
    if release_tag:
        heading += f" (`{release_tag}`)"

    lines = [
        f"<!-- {RELEASE_CORPUS_AUDIT_MARKER}:start -->",
        heading,
        "",
        f"- Status: `{report['status']}`",
        f"- Recommendation: {report['recommendation']}",
    ]
    evidence_links: list[str] = []
    if workflow_run_url:
        evidence_links.append(_markdown_link("release workflow run", workflow_run_url))
    if state_artifact_url:
        evidence_links.append(_markdown_link("corpus state artifact", state_artifact_url))
    if dist_artifact_url:
        evidence_links.append(_markdown_link("distribution artifact bundle", dist_artifact_url))
    if evidence_links:
        lines.append("- Evidence: " + ", ".join(evidence_links))
    if report["compare_report"] is not None:
        lines.append(f"- Latest branch compare drift: {_format_compare_report_markdown(report['compare_report'])}")
        lines.append(f"- Drift categories: {_format_compare_category_summary_markdown(report['compare_report'])}")
        lines.append(f"- Drift attribution: {_format_compare_attribution_summary_markdown(report['compare_report'])}")
        lines.append(f"- Likely modules to review: {_format_compare_review_hints_markdown(compare_review_hints)}")
        if compare_review_request_plan:
            lines.append(f"- Review-request dry-run: {_format_compare_review_request_plan_markdown(compare_review_request_plan)}")
        if compare_owner_focus:
            lines.append(f"- Owner focus: {_format_compare_owner_focus_markdown(compare_owner_focus)}")
        if compare_owner_hints:
            lines.append(f"- Likely owners to review: {_format_compare_owner_hints_markdown(compare_owner_hints)}")
        if report["compare_report"]["has_changed_files_context"]:
            lines.append(f"- Changed files context: {_format_compare_changed_files_context_markdown(report['compare_report'])}")
            lines.append(f"- Changed-file overlap: {_format_compare_changed_file_overlap_markdown(compare_changed_file_overlap)}")
            lines.append(f"- Changed-file focus: {_format_compare_changed_file_focus_markdown(compare_changed_file_focus)}")
        for detail in report["compare_report"]["drift_details"]:
            case_name, summary = detail.split(": ", 1)
            lines.append(f"- Drift detail: `{case_name}` {summary}")
    if report["compare_focus_excerpt"] is not None:
        lines.append(
            f"- Latest branch compare focus: {_format_compare_focus_excerpt_line(report['compare_focus_excerpt'])}"
        )
    elif report["compare_markdown_excerpt"] is not None:
        lines.append(
            f"- Latest branch compare excerpt: {_format_compare_markdown_excerpt_line(report['compare_markdown_excerpt'])}"
        )
    for note in report["notes"]:
        lines.append(f"- Note: {note}")
    lines.extend(
        [
            "",
            "<details>",
            "<summary>Corpus release state</summary>",
            "",
            format_corpus_baseline_state_markdown(report, heading="### Corpus Release State"),
            "",
            "</details>",
        ]
    )
    if report["compare_focus_excerpt"] is not None:
        lines.extend(
            [
                "",
                "<details>",
                "<summary>Latest branch compare focus</summary>",
                "",
                report["compare_focus_excerpt"]["content"],
                "",
                "</details>",
            ]
        )
    elif report["compare_markdown_excerpt"] is not None:
        lines.extend(
            [
                "",
                "<details>",
                "<summary>Latest branch compare excerpt</summary>",
                "",
                report["compare_markdown_excerpt"]["content"],
                "",
                "</details>",
            ]
        )
    lines.append(f"<!-- {RELEASE_CORPUS_AUDIT_MARKER}:end -->")
    return "\n".join(lines)


def bump_project_version(project_root: Path, new_version: str, release_date: str) -> None:
    """Update package version files and roll unreleased changelog notes into a release entry."""
    if not _VERSION_RE.fullmatch(new_version):
        raise ValueError(f"Unsupported version format: {new_version!r}")
    _validate_date(release_date)

    pyproject_path = project_root / PYPROJECT_PATH
    init_path = project_root / INIT_PATH
    changelog_path = project_root / CHANGELOG_PATH
    citation_path = project_root / CITATION_PATH

    pyproject_text = _read_text(pyproject_path)
    init_text = _read_text(init_path)
    changelog_text = _read_text(changelog_path)
    citation_text = _read_text(citation_path)

    current_version = _extract_single(_PYPROJECT_VERSION_RE, pyproject_text, "pyproject version")
    if current_version == new_version:
        raise ValueError(f"Project is already at version {new_version!r}.")

    if re.search(rf"^## \[{re.escape(new_version)}\] - ", changelog_text, re.MULTILINE):
        raise ValueError(f"CHANGELOG.md already contains version {new_version!r}.")

    updated_pyproject = _replace_single(_PYPROJECT_VERSION_RE, pyproject_text, f'version = "{new_version}"')
    updated_init = _replace_single(_INIT_VERSION_RE, init_text, f'__version__ = "{new_version}"')
    updated_changelog = _roll_unreleased_section(changelog_text, new_version, release_date)
    updated_citation = _replace_single(_CITATION_VERSION_RE, citation_text, f"version: {new_version}")
    updated_citation = _replace_single(_CITATION_DATE_RE, updated_citation, f"date-released: {release_date}")

    pyproject_path.write_text(updated_pyproject, encoding="utf-8")
    init_path.write_text(updated_init, encoding="utf-8")
    changelog_path.write_text(updated_changelog, encoding="utf-8")
    citation_path.write_text(updated_citation, encoding="utf-8")


def _roll_unreleased_section(changelog_text: str, version: str, release_date: str) -> str:
    marker_index = changelog_text.find(UNRELEASED_HEADING)
    if marker_index == -1:
        raise ValueError("CHANGELOG.md must contain '## [Unreleased]' before bumping a version.")

    heading_end = changelog_text.find("\n", marker_index)
    if heading_end == -1:
        heading_end = len(changelog_text)

    after_heading_index = heading_end + 1
    next_heading = _VERSION_HEADING_RE.search(changelog_text, after_heading_index)
    release_notes_end = next_heading.start() if next_heading else len(changelog_text)

    unreleased_body = changelog_text[after_heading_index:release_notes_end]
    before = changelog_text[:marker_index]
    after = changelog_text[release_notes_end:]

    new_section = (
        f"{UNRELEASED_HEADING}\n\n"
        f"## [{version}] - {release_date}"
        f"{unreleased_body}"
    )
    return before + new_section + after


def _extract_release_date(changelog_text: str, version: str) -> str:
    match = re.search(rf"^## \[{re.escape(version)}\] - ([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})$", changelog_text, re.MULTILINE)
    if not match:
        raise ValueError(f"CHANGELOG.md is missing an entry for version {version!r}.")
    return match.group(1)


def _extract_single(pattern: re.Pattern[str], text: str, label: str) -> str:
    match = pattern.search(text)
    if not match:
        raise ValueError(f"Could not find {label}.")
    return match.group(1)


def _replace_single(pattern: re.Pattern[str], text: str, replacement: str) -> str:
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise ValueError("Expected exactly one version field to replace.")
    return updated


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_output(rendered: str, output: str | None) -> None:
    if output is None:
        print(rendered)
        return
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")


def _normalize_corpus_run_record(
    data: Any,
    *,
    label: str,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    if data is None:
        if allow_missing:
            return None
        raise ValueError(f"Corpus baseline state is missing {label} metadata.")
    if not isinstance(data, dict):
        raise ValueError(f"Corpus baseline state {label} entry must be an object.")

    run_id = data.get("run_id")
    created_at = data.get("created_at")
    if not isinstance(run_id, int):
        raise ValueError(f"Corpus baseline state {label}.run_id must be an integer.")
    if not isinstance(created_at, str) or not created_at:
        raise ValueError(f"Corpus baseline state {label}.created_at must be a non-empty string.")

    normalized = {"run_id": run_id, "created_at": created_at}
    for key in ("artifact_name", "artifact_url", "html_url", "head_sha", "head_branch", "event"):
        value = data.get(key)
        if value is None or isinstance(value, str):
            normalized[key] = value
        else:
            raise ValueError(f"Corpus baseline state {label}.{key} must be a string when present.")

    artifact_id = data.get("artifact_id")
    if artifact_id is None or isinstance(artifact_id, int):
        normalized["artifact_id"] = artifact_id
    else:
        raise ValueError(f"Corpus baseline state {label}.artifact_id must be an integer when present.")

    artifact_expired = data.get("artifact_expired")
    if artifact_expired is None or isinstance(artifact_expired, bool):
        normalized["artifact_expired"] = artifact_expired
    else:
        raise ValueError(f"Corpus baseline state {label}.artifact_expired must be a boolean when present.")

    pr_number = data.get("pr_number")
    if pr_number is None or isinstance(pr_number, int):
        normalized["pr_number"] = pr_number
    else:
        raise ValueError(f"Corpus baseline state {label}.pr_number must be an integer when present.")

    return normalized


def _normalize_compare_report_summary(
    data: Any,
    *,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    if data is None:
        if allow_missing:
            return None
        raise ValueError("Corpus baseline state is missing compare_report metadata.")
    if not isinstance(data, dict):
        raise ValueError("Corpus baseline state compare_report entry must be an object.")

    metric = data.get("metric")
    if not isinstance(metric, str) or not metric:
        raise ValueError("Corpus baseline state compare_report.metric must be a non-empty string.")
    has_summary_drift = data.get("has_summary_drift")
    if not isinstance(has_summary_drift, bool):
        raise ValueError("Corpus baseline state compare_report.has_summary_drift must be a boolean.")
    has_changed_files_context = data.get("has_changed_files_context", False)
    if not isinstance(has_changed_files_context, bool):
        raise ValueError("Corpus baseline state compare_report.has_changed_files_context must be a boolean.")
    comparison_count = data.get("comparison_count")
    if not isinstance(comparison_count, int):
        raise ValueError("Corpus baseline state compare_report.comparison_count must be an integer.")

    summary_drift_cases = data.get("summary_drift_cases")
    if not isinstance(summary_drift_cases, list) or any(not isinstance(item, str) or not item for item in summary_drift_cases):
        raise ValueError("Corpus baseline state compare_report.summary_drift_cases must be a list of non-empty strings.")
    changed_files = _normalize_compare_changed_files(
        data.get("changed_files", []),
        label="Corpus baseline state compare_report",
    )
    if changed_files:
        has_changed_files_context = True

    drift_details = data.get("drift_details")
    if not isinstance(drift_details, list) or any(not isinstance(item, str) or not item for item in drift_details):
        raise ValueError("Corpus baseline state compare_report.drift_details must be a list of non-empty strings.")
    category_summary_data = data.get("category_summary")
    category_summary = (
        _empty_compare_category_summary()
        if category_summary_data is None
        else _normalize_compare_category_summary(category_summary_data)
    )
    attribution_summary_data = data.get("attribution_summary")
    attribution_summary = (
        _empty_compare_attribution_summary()
        if attribution_summary_data is None
        else _normalize_compare_attribution_summary(attribution_summary_data)
    )
    owner_hints = _normalize_compare_owner_hints(
        data.get("owner_hints"),
        label="Corpus baseline state compare_report.owner_hints",
    )
    owner_focus = _normalize_compare_owner_focus(
        data.get("owner_focus"),
        label="Corpus baseline state compare_report.owner_focus",
    )
    reviewer_candidates = _normalize_compare_reviewer_candidates(
        data.get("reviewer_candidates"),
        label="Corpus baseline state compare_report.reviewer_candidates",
    )
    review_request_plan = _normalize_compare_review_request_plan(
        data.get("review_request_plan"),
        label="Corpus baseline state compare_report.review_request_plan",
    )

    return {
        "metric": metric,
        "has_summary_drift": has_summary_drift,
        "has_changed_files_context": has_changed_files_context,
        "changed_files": changed_files,
        "summary_drift_cases": summary_drift_cases,
        "drift_details": drift_details,
        "comparison_count": comparison_count,
        "category_summary": category_summary,
        "attribution_summary": attribution_summary,
        "owner_hints": owner_hints,
        "owner_focus": owner_focus,
        "reviewer_candidates": reviewer_candidates,
        "review_request_plan": review_request_plan,
    }


def _normalize_compare_focus_excerpt(
    data: Any,
    *,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    if data is None:
        if allow_missing:
            return None
        raise ValueError("Corpus baseline state is missing compare_focus_excerpt metadata.")
    if not isinstance(data, dict):
        raise ValueError("Corpus baseline state compare_focus_excerpt entry must be an object.")

    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Corpus baseline state compare_focus_excerpt.content must be a non-empty string.")
    highlight_count = data.get("highlight_count")
    if not isinstance(highlight_count, int):
        raise ValueError("Corpus baseline state compare_focus_excerpt.highlight_count must be an integer.")
    total_count = data.get("total_count")
    if not isinstance(total_count, int):
        raise ValueError("Corpus baseline state compare_focus_excerpt.total_count must be an integer.")
    if total_count < highlight_count:
        raise ValueError("Corpus baseline state compare_focus_excerpt.total_count must be >= highlight_count.")
    truncated = data.get("truncated")
    if not isinstance(truncated, bool):
        raise ValueError("Corpus baseline state compare_focus_excerpt.truncated must be a boolean.")
    source = data.get("source")
    if not isinstance(source, str) or not source:
        raise ValueError("Corpus baseline state compare_focus_excerpt.source must be a non-empty string.")

    return {
        "content": content,
        "highlight_count": highlight_count,
        "total_count": total_count,
        "truncated": truncated,
        "source": source,
    }


def _normalize_compare_markdown_excerpt(
    data: Any,
    *,
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    if data is None:
        if allow_missing:
            return None
        raise ValueError("Corpus baseline state is missing compare_markdown_excerpt metadata.")
    if not isinstance(data, dict):
        raise ValueError("Corpus baseline state compare_markdown_excerpt entry must be an object.")

    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Corpus baseline state compare_markdown_excerpt.content must be a non-empty string.")
    truncated = data.get("truncated")
    if not isinstance(truncated, bool):
        raise ValueError("Corpus baseline state compare_markdown_excerpt.truncated must be a boolean.")
    table_row_count = data.get("table_row_count")
    if not isinstance(table_row_count, int):
        raise ValueError("Corpus baseline state compare_markdown_excerpt.table_row_count must be an integer.")

    return {
        "content": content,
        "truncated": truncated,
        "table_row_count": table_row_count,
    }


def _format_corpus_run_line(run: dict[str, Any] | None) -> str:
    if run is None:
        return "none"
    parts = [f"run {run['run_id']}", f"created {run['created_at']}"]
    if isinstance(run.get("head_branch"), str) and run["head_branch"]:
        parts.append(f"branch {run['head_branch']}")
    if isinstance(run.get("head_sha"), str) and run["head_sha"]:
        parts.append(f"commit {_short_sha(run['head_sha'])}")
    if isinstance(run.get("event"), str) and run["event"]:
        parts.append(f"event {run['event']}")
    if isinstance(run.get("pr_number"), int):
        parts.append(f"PR #{run['pr_number']}")
    if isinstance(run.get("artifact_name"), str) and run["artifact_name"]:
        parts.append(f"artifact {run['artifact_name']}")
    if isinstance(run.get("artifact_expired"), bool):
        parts.append("artifact expired" if run["artifact_expired"] else "artifact active")
    if isinstance(run.get("html_url"), str) and run["html_url"]:
        parts.append(f"run url {run['html_url']}")
    if isinstance(run.get("artifact_url"), str) and run["artifact_url"]:
        parts.append(f"artifact url {run['artifact_url']}")
    return ", ".join(parts)


def _format_corpus_run_markdown(run: dict[str, Any] | None) -> str:
    if run is None:
        return "`none`"
    label = f"run {run['run_id']}"
    if isinstance(run.get("html_url"), str) and run["html_url"]:
        label_text = f"[`{label}`]({run['html_url']})"
    else:
        label_text = f"`{label}`"
    parts = [label_text, f"created `{run['created_at']}`"]
    if isinstance(run.get("head_branch"), str) and run["head_branch"]:
        parts.append(f"branch `{run['head_branch']}`")
    if isinstance(run.get("head_sha"), str) and run["head_sha"]:
        parts.append(f"commit `{_short_sha(run['head_sha'])}`")
    if isinstance(run.get("event"), str) and run["event"]:
        parts.append(f"event `{run['event']}`")
    if isinstance(run.get("pr_number"), int):
        parts.append(f"PR `#{run['pr_number']}`")
    if isinstance(run.get("artifact_name"), str) and run["artifact_name"]:
        if isinstance(run.get("artifact_url"), str) and run["artifact_url"]:
            parts.append(f"artifact [`{run['artifact_name']}`]({run['artifact_url']})")
        else:
            parts.append(f"artifact `{run['artifact_name']}`")
    if isinstance(run.get("artifact_expired"), bool):
        parts.append("artifact `expired`" if run["artifact_expired"] else "artifact `active`")
    return ", ".join(parts)


def _format_corpus_run_markdown_row(label: str, run: dict[str, Any] | None) -> str:
    if run is None:
        return f"| {label} | `none` | `n/a` | `n/a` | `n/a` | `n/a` | `none` |"
    branch = f"`{run['head_branch']}`" if isinstance(run.get("head_branch"), str) and run["head_branch"] else "`n/a`"
    commit = f"`{_short_sha(run['head_sha'])}`" if isinstance(run.get("head_sha"), str) and run["head_sha"] else "`n/a`"
    event = f"`{run['event']}`" if isinstance(run.get("event"), str) and run["event"] else "`n/a`"
    artifact = "`none`"
    if isinstance(run.get("artifact_name"), str) and run["artifact_name"]:
        if isinstance(run.get("artifact_url"), str) and run["artifact_url"]:
            artifact = f"[`{run['artifact_name']}`]({run['artifact_url']})"
        else:
            artifact = f"`{run['artifact_name']}`"
        if isinstance(run.get("artifact_expired"), bool):
            artifact += " (expired)" if run["artifact_expired"] else " (active)"
    return (
        f"| {label} | {_format_corpus_run_markdown(run if run else None).split(',')[0]} "
        f"| `{run['created_at']}` | {branch} | {commit} | {event} | {artifact} |"
    )


def _format_compare_report_line(report: dict[str, Any]) -> str:
    drift_cases = report["summary_drift_cases"]
    drift_label = f"{len(drift_cases)} case(s)" if drift_cases else "none"
    if not report["drift_details"]:
        return f"metric {report['metric']}, drift {drift_label}"
    return f"metric {report['metric']}, drift {drift_label}, details: {'; '.join(report['drift_details'])}"


def _format_compare_report_markdown(report: dict[str, Any]) -> str:
    drift_cases = report["summary_drift_cases"]
    drift_label = f"{len(drift_cases)} case(s)" if drift_cases else "none"
    cases = ", ".join(f"`{item}`" for item in drift_cases) if drift_cases else "`none`"
    return f"metric `{report['metric']}`, drift `{drift_label}`, cases: {cases}"


def _format_compare_category_summary_line(report: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in _COMPARE_CATEGORY_LABELS:
        category = report["category_summary"][key]
        if category["count"] == 0:
            continue
        parts.append(f"{label} {category['count']} case(s) ({', '.join(category['cases'])})")
    return "; ".join(parts) if parts else "none"


def _format_compare_category_summary_markdown(report: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in _COMPARE_CATEGORY_LABELS:
        category = report["category_summary"][key]
        if category["count"] == 0:
            continue
        cases = ", ".join(f"`{item}`" for item in category["cases"])
        parts.append(f"{label} {cases}")
    return "; ".join(parts) if parts else "`none`"


def _format_compare_attribution_summary_line(report: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in _COMPARE_ATTRIBUTION_LABELS:
        category = report["attribution_summary"][key]
        if category["count"] == 0:
            continue
        parts.append(f"{label} {category['count']} case(s) ({', '.join(category['cases'])})")
    return "; ".join(parts) if parts else "none"


def _format_compare_attribution_summary_markdown(report: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in _COMPARE_ATTRIBUTION_LABELS:
        category = report["attribution_summary"][key]
        if category["count"] == 0:
            continue
        cases = ", ".join(f"`{item}`" for item in category["cases"])
        parts.append(f"{label} {cases}")
    return "; ".join(parts) if parts else "`none`"


def _format_compare_review_hints_line(hints: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for hint in hints:
        paths = ", ".join(hint["paths"])
        parts.append(f"{hint['label']} -> {paths} ({', '.join(hint['cases'])})")
    return "; ".join(parts) if parts else "none"


def _format_compare_review_hints_markdown(hints: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for hint in hints:
        paths = ", ".join(f"`{path}`" for path in hint["paths"])
        cases = ", ".join(f"`{case}`" for case in hint["cases"])
        parts.append(f"{hint['label']} -> {paths} (cases: {cases})")
    return "; ".join(parts) if parts else "`none`"


def _format_compare_owner_hints_line(hints: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for hint in hints:
        owners = ", ".join(hint["owners"])
        parts.append(f"{hint['label']} -> {owners} ({', '.join(hint['cases'])})")
    return "; ".join(parts) if parts else "none"


def _format_compare_owner_hints_markdown(hints: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for hint in hints:
        owners = ", ".join(f"`{owner}`" for owner in hint["owners"])
        cases = ", ".join(f"`{case}`" for case in hint["cases"])
        parts.append(f"{hint['label']} -> {owners} (cases: {cases})")
    return "; ".join(parts) if parts else "`none`"


def _format_compare_owner_focus_line(focus: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in focus:
        labels = ", ".join(item["labels"])
        files = ", ".join(item["matched_changed_files"])
        parts.append(f"{item['priority']} {item['owner']} -> {labels} [{files}] ({', '.join(item['cases'])})")
    return "; ".join(parts) if parts else "none"


def _format_compare_owner_focus_markdown(focus: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in focus:
        labels = ", ".join(f"`{label}`" for label in item["labels"])
        files = ", ".join(f"`{path}`" for path in item["matched_changed_files"])
        cases = ", ".join(f"`{case}`" for case in item["cases"])
        parts.append(f"{item['priority']} `{item['owner']}` -> {labels} [{files}] (cases: {cases})")
    return "; ".join(parts) if parts else "`none`"


def _format_compare_review_request_plan_line(plan: dict[str, list[str]]) -> str:
    parts: list[str] = []
    if plan["users"]:
        parts.append("users " + ", ".join(plan["users"]))
    if plan["teams"]:
        parts.append("teams " + ", ".join(plan["teams"]))
    if plan["unsupported"]:
        parts.append("unsupported " + ", ".join(plan["unsupported"]))
    return "; ".join(parts) if parts else "none"


def _format_compare_review_request_plan_markdown(plan: dict[str, list[str]]) -> str:
    parts: list[str] = []
    if plan["users"]:
        parts.append("users " + ", ".join(f"`{owner}`" for owner in plan["users"]))
    if plan["teams"]:
        parts.append("teams " + ", ".join(f"`{owner}`" for owner in plan["teams"]))
    if plan["unsupported"]:
        parts.append("unsupported " + ", ".join(f"`{owner}`" for owner in plan["unsupported"]))
    return "; ".join(parts) if parts else "`none`"


def _format_compare_changed_files_context_line(report: dict[str, Any]) -> str:
    return f"{len(report['changed_files'])} file(s)"


def _format_compare_changed_files_context_markdown(report: dict[str, Any]) -> str:
    return f"`{len(report['changed_files'])} file(s)`"


def _format_compare_changed_file_overlap_line(overlap: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in overlap:
        parts.append(f"{item['label']} -> {', '.join(item['matched_changed_files'])} ({', '.join(item['cases'])})")
    return "; ".join(parts) if parts else "none"


def _format_compare_changed_file_overlap_markdown(overlap: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in overlap:
        matched = ", ".join(f"`{path}`" for path in item["matched_changed_files"])
        cases = ", ".join(f"`{case}`" for case in item["cases"])
        parts.append(f"{item['label']} -> {matched} (cases: {cases})")
    return "; ".join(parts) if parts else "`none`"


def _format_compare_changed_file_focus_line(focus: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in focus:
        matched = ", ".join(item["matched_changed_files"])
        parts.append(f"{item['priority']} {item['label']} -> {matched} ({', '.join(item['cases'])})")
    return "; ".join(parts) if parts else "none"


def _format_compare_changed_file_focus_markdown(focus: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in focus:
        matched = ", ".join(f"`{path}`" for path in item["matched_changed_files"])
        cases = ", ".join(f"`{case}`" for case in item["cases"])
        parts.append(f"{item['priority']} `{item['label']}` -> {matched} (cases: {cases})")
    return "; ".join(parts) if parts else "`none`"


def _format_compare_markdown_excerpt_line(excerpt: dict[str, Any]) -> str:
    row_label = f"{excerpt['table_row_count']} row(s)"
    if excerpt["truncated"]:
        row_label += ", truncated"
    return row_label


def _format_compare_focus_excerpt_line(excerpt: dict[str, Any]) -> str:
    label = f"{excerpt['highlight_count']} highlighted case(s)"
    if excerpt["total_count"] != excerpt["highlight_count"]:
        label += f" of {excerpt['total_count']}"
    if excerpt["truncated"]:
        label += ", truncated"
    return label


def _build_compare_focus_excerpt(
    metric: str,
    comparisons: list[dict[str, Any]],
    *,
    max_cases: int = 3,
) -> dict[str, Any] | None:
    highlighted = [item for item in comparisons if _comparison_is_highlight(item)]
    if not highlighted:
        return None
    ordered = sorted(highlighted, key=_comparison_focus_sort_key)
    visible = ordered[:max_cases]
    uses_summary_delta = any(item["summary_bits"] for item in visible)
    change_header = "Summary Delta" if uses_summary_delta else "Change"
    lines = [
        f"- Metric: `{metric}`",
        f"- Showing top release-view cases: `{len(visible)} of {len(ordered)}`",
        "",
        f"| Case | Status | {change_header} |",
        "| --- | --- | --- |",
    ]
    for item in visible:
        lines.append(
            f"| `{item['name']}` | `{item['status']}` | {_format_compare_focus_change(item, uses_summary_delta)} |"
        )
    if len(ordered) > len(visible):
        lines.extend(
            [
                "",
                f"_... {len(ordered) - len(visible)} more highlighted case(s) omitted from the release view._",
            ]
        )
    return {
        "content": "\n".join(lines),
        "highlight_count": len(visible),
        "total_count": len(ordered),
        "truncated": len(ordered) > len(visible),
        "source": "compare_report",
    }


def _comparison_is_highlight(item: dict[str, Any]) -> bool:
    if item["summary_bits"]:
        return True
    return bool(item["status"] != "unchanged")


def _build_compare_category_summary(comparisons: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    categories: dict[str, list[str]] = {key: [] for key, _label in _COMPARE_CATEGORY_LABELS}
    for item in comparisons:
        matched = False
        summary_delta = item["summary_delta"]
        if any(int(summary_delta.get(field, 0)) != 0 for field in ("files", "functions", "classes")):
            categories["parser"].append(item["name"])
            matched = True
        if any(int(summary_delta.get(field, 0)) != 0 for field in ("edges", "chains")):
            categories["resolver"].append(item["name"])
            matched = True
        if int(summary_delta.get("parse_errors", 0)) != 0:
            categories["parse_health"].append(item["name"])
            matched = True
        if _comparison_is_highlight(item) and not matched:
            categories["non_structural"].append(item["name"])
    return {
        key: {"count": len(cases), "cases": cases}
        for key, cases in categories.items()
    }


def _build_compare_attribution_summary(comparisons: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    categories: dict[str, list[str]] = {key: [] for key, _label in _COMPARE_ATTRIBUTION_LABELS}
    for item in comparisons:
        matched = False
        summary_delta = item["summary_delta"]
        if int(summary_delta.get("files", 0)) != 0:
            categories["discovery"].append(item["name"])
            matched = True
        if any(int(summary_delta.get(field, 0)) != 0 for field in ("functions", "classes")):
            categories["symbol_extraction"].append(item["name"])
            matched = True
        if int(summary_delta.get("edges", 0)) != 0:
            categories["call_resolution"].append(item["name"])
            matched = True
        if int(summary_delta.get("chains", 0)) != 0:
            categories["chain_enumeration"].append(item["name"])
            matched = True
        if int(summary_delta.get("parse_errors", 0)) != 0:
            categories["parse_health"].append(item["name"])
            matched = True
        if _comparison_is_highlight(item) and not matched:
            categories["non_structural"].append(item["name"])
    return {
        key: {"count": len(cases), "cases": cases}
        for key, cases in categories.items()
    }


def _build_compare_review_hints(compare_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if compare_report is None:
        return []
    hints: list[dict[str, Any]] = []
    for key, label, paths, reason in _COMPARE_REVIEW_HINTS:
        cases = compare_report["attribution_summary"][key]["cases"]
        if not cases:
            continue
        hints.append(
            {
                "key": key,
                "label": label,
                "cases": list(cases),
                "paths": list(paths),
                "reason": reason,
            }
        )
    return hints


def _build_compare_owner_hints(compare_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if compare_report is None:
        return []
    return [dict(item) for item in compare_report.get("owner_hints", [])]


def _build_compare_owner_focus(compare_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if compare_report is None:
        return []
    return [dict(item) for item in compare_report.get("owner_focus", [])]


def _build_compare_reviewer_candidates(compare_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if compare_report is None:
        return []
    return [dict(item) for item in compare_report.get("reviewer_candidates", [])]


def _build_compare_review_request_plan(compare_report: dict[str, Any] | None) -> dict[str, list[str]] | None:
    if compare_report is None:
        return None
    plan = compare_report.get("review_request_plan")
    if plan is None:
        return None
    return dict(plan)


def _build_compare_changed_file_overlap(
    compare_report: dict[str, Any] | None,
    compare_review_hints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if compare_report is None or not compare_report["has_changed_files_context"]:
        return []
    changed_files = compare_report["changed_files"]
    overlap: list[dict[str, Any]] = []
    for hint in compare_review_hints:
        matched_changed_files = [path for path in changed_files if _matches_review_hint(path, hint["paths"])]
        if not matched_changed_files:
            continue
        overlap.append(
            {
                "key": hint["key"],
                "label": hint["label"],
                "cases": list(hint["cases"]),
                "matched_changed_files": matched_changed_files,
            }
        )
    return overlap


def _build_compare_changed_file_focus(overlap: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(overlap, key=_compare_changed_file_focus_sort_key)
    return [
        {
            "key": item["key"],
            "label": item["label"],
            "cases": list(item["cases"]),
            "matched_changed_files": list(item["matched_changed_files"]),
            "priority": _compare_changed_file_priority(item["matched_changed_files"]),
            "score": _compare_changed_file_score(item["matched_changed_files"], item["cases"]),
        }
        for item in ordered
    ]


def _empty_compare_category_summary() -> dict[str, dict[str, Any]]:
    return {key: {"count": 0, "cases": []} for key, _label in _COMPARE_CATEGORY_LABELS}


def _empty_compare_attribution_summary() -> dict[str, dict[str, Any]]:
    return {key: {"count": 0, "cases": []} for key, _label in _COMPARE_ATTRIBUTION_LABELS}


def _normalize_compare_category_summary(data: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("Corpus baseline state compare_report.category_summary must be an object.")
    normalized: dict[str, dict[str, Any]] = {}
    for key, _label in _COMPARE_CATEGORY_LABELS:
        category = data.get(key)
        if not isinstance(category, dict):
            raise ValueError(f"Corpus baseline state compare_report.category_summary.{key} must be an object.")
        count = category.get("count")
        if not isinstance(count, int):
            raise ValueError(f"Corpus baseline state compare_report.category_summary.{key}.count must be an integer.")
        cases = category.get("cases")
        if not isinstance(cases, list) or any(not isinstance(item, str) or not item for item in cases):
            raise ValueError(
                f"Corpus baseline state compare_report.category_summary.{key}.cases must be a list of non-empty strings."
            )
        if count != len(cases):
            raise ValueError(f"Corpus baseline state compare_report.category_summary.{key}.count must match cases.")
        normalized[key] = {"count": count, "cases": cases}
    return normalized


def _normalize_compare_attribution_summary(data: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("Corpus baseline state compare_report.attribution_summary must be an object.")
    normalized: dict[str, dict[str, Any]] = {}
    for key, _label in _COMPARE_ATTRIBUTION_LABELS:
        category = data.get(key)
        if not isinstance(category, dict):
            raise ValueError(f"Corpus baseline state compare_report.attribution_summary.{key} must be an object.")
        count = category.get("count")
        if not isinstance(count, int):
            raise ValueError(f"Corpus baseline state compare_report.attribution_summary.{key}.count must be an integer.")
        cases = category.get("cases")
        if not isinstance(cases, list) or any(not isinstance(item, str) or not item for item in cases):
            raise ValueError(
                f"Corpus baseline state compare_report.attribution_summary.{key}.cases must be a list of non-empty strings."
            )
        if count != len(cases):
            raise ValueError(f"Corpus baseline state compare_report.attribution_summary.{key}.count must match cases.")
        normalized[key] = {"count": count, "cases": cases}
    return normalized


def _normalize_compare_owner_hints(data: Any, *, label: str) -> list[dict[str, Any]]:
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"{label} must be a list of objects.")
    normalized: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be a list of objects.")
        key = item.get("key")
        hint_label = item.get("label")
        if not isinstance(key, str) or not key:
            raise ValueError(f"{label} keys must be non-empty strings.")
        if not isinstance(hint_label, str) or not hint_label:
            raise ValueError(f"{label} labels must be non-empty strings.")
        cases = item.get("cases")
        if not isinstance(cases, list) or any(not isinstance(case, str) or not case for case in cases):
            raise ValueError(f"{label} cases must be a list of non-empty strings.")
        paths = item.get("paths")
        if not isinstance(paths, list) or any(not isinstance(path, str) or not path for path in paths):
            raise ValueError(f"{label} paths must be a list of non-empty strings.")
        owners = item.get("owners")
        if not isinstance(owners, list) or any(not isinstance(owner, str) or not owner for owner in owners):
            raise ValueError(f"{label} owners must be a list of non-empty strings.")
        matched_changed_files = _normalize_compare_changed_files(
            item.get("matched_changed_files", []),
            label=f"{label} matched_changed_files",
        )
        ownerless_changed_files = _normalize_compare_changed_files(
            item.get("ownerless_changed_files", []),
            label=f"{label} ownerless_changed_files",
        )
        normalized.append(
            {
                "key": key,
                "label": hint_label,
                "cases": cases,
                "paths": paths,
                "owners": owners,
                "matched_changed_files": matched_changed_files,
                "ownerless_changed_files": ownerless_changed_files,
            }
        )
    return normalized


def _normalize_compare_owner_focus(data: Any, *, label: str) -> list[dict[str, Any]]:
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"{label} must be a list of objects.")
    normalized: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be a list of objects.")
        owner = item.get("owner")
        if not isinstance(owner, str) or not owner:
            raise ValueError(f"{label} owners must be non-empty strings.")
        labels = item.get("labels")
        if not isinstance(labels, list) or any(not isinstance(value, str) or not value for value in labels):
            raise ValueError(f"{label} labels must be a list of non-empty strings.")
        cases = item.get("cases")
        if not isinstance(cases, list) or any(not isinstance(value, str) or not value for value in cases):
            raise ValueError(f"{label} cases must be a list of non-empty strings.")
        matched_changed_files = _normalize_compare_changed_files(
            item.get("matched_changed_files", []),
            label=f"{label} matched_changed_files",
        )
        priority = item.get("priority")
        if priority not in {"critical", "high", "medium", "low"}:
            raise ValueError(f"{label} priority values must be one of critical/high/medium/low.")
        score = item.get("score")
        if not isinstance(score, int):
            raise ValueError(f"{label} score values must be integers.")
        normalized.append(
            {
                "owner": owner,
                "labels": labels,
                "cases": cases,
                "matched_changed_files": matched_changed_files,
                "priority": priority,
                "score": score,
            }
        )
    return normalized


def _normalize_compare_reviewer_candidates(data: Any, *, label: str) -> list[dict[str, Any]]:
    if data is None:
        return []
    if not isinstance(data, list):
        raise ValueError(f"{label} must be a list of objects.")
    normalized: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"{label} must be a list of objects.")
        owner = item.get("owner")
        kind = item.get("kind")
        priority = item.get("priority")
        score = item.get("score")
        labels = item.get("labels")
        cases = item.get("cases")
        if not isinstance(owner, str) or not owner:
            raise ValueError(f"{label} owners must be non-empty strings.")
        if kind not in {"user", "team", "unsupported"}:
            raise ValueError(f"{label} kinds must be one of user/team/unsupported.")
        if priority not in {"critical", "high", "medium", "low"}:
            raise ValueError(f"{label} priority values must be one of critical/high/medium/low.")
        if not isinstance(score, int):
            raise ValueError(f"{label} score values must be integers.")
        if not isinstance(labels, list) or any(not isinstance(value, str) or not value for value in labels):
            raise ValueError(f"{label} labels must be a list of non-empty strings.")
        if not isinstance(cases, list) or any(not isinstance(value, str) or not value for value in cases):
            raise ValueError(f"{label} cases must be a list of non-empty strings.")
        matched_changed_files = _normalize_compare_changed_files(
            item.get("matched_changed_files", []),
            label=f"{label} matched_changed_files",
        )
        normalized.append(
            {
                "owner": owner,
                "kind": kind,
                "priority": priority,
                "score": score,
                "labels": labels,
                "cases": cases,
                "matched_changed_files": matched_changed_files,
            }
        )
    return normalized


def _normalize_compare_review_request_plan(data: Any, *, label: str) -> dict[str, list[str]] | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be an object.")
    normalized: dict[str, list[str]] = {}
    for key in ("users", "teams", "unsupported"):
        value = data.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise ValueError(f"{label}.{key} must be a list of non-empty strings.")
        normalized[key] = value
    return normalized


def _normalize_compare_changed_files(data: Any, *, label: str) -> list[str]:
    if not isinstance(data, list):
        raise ValueError(f"{label} changed_files must be a list of non-empty strings.")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} changed_files must be a list of non-empty strings.")
        cleaned = item.strip()
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        if cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)
    return normalized


def _matches_review_hint(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _compare_changed_file_focus_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    matched_changed_files = item["matched_changed_files"]
    cases = item["cases"]
    score = _compare_changed_file_score(matched_changed_files, cases)
    max_weight = max((_changed_file_weight(path) for path in matched_changed_files), default=0)
    return (-score, -max_weight, -len(matched_changed_files), -len(cases), item["label"])


def _compare_changed_file_score(matched_changed_files: list[str], cases: list[str]) -> int:
    path_score = sum(_changed_file_weight(path) for path in matched_changed_files)
    return path_score + len(matched_changed_files) + (len(cases) * 2)


def _compare_changed_file_priority(matched_changed_files: list[str]) -> str:
    max_weight = max((_changed_file_weight(path) for path in matched_changed_files), default=0)
    if max_weight >= 8:
        return "critical"
    if max_weight >= 6:
        return "high"
    if max_weight >= 4:
        return "medium"
    return "low"


def _changed_file_weight(path: str) -> int:
    if path == "src/callchain/core/callgraph.py":
        return 9
    if path == "src/callchain/core/chain_enum.py":
        return 8
    if path.startswith("src/callchain/core/"):
        return 7
    if path == "src/callchain/languages/base.py":
        return 6
    if path.startswith("src/callchain/languages/"):
        return 5
    if path.startswith("src/callchain/devtools/"):
        return 3
    if path.startswith(".github/"):
        return 2
    return 1


def _comparison_focus_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    status_rank = {
        "regression": 4,
        "changed": 3,
        "within_threshold": 2,
        "improvement": 1,
        "unchanged": 0,
    }.get(item["status"], 0)
    summary_magnitude = sum(abs(delta) for delta in item["summary_delta"].values())
    summary_field_count = len(item["summary_bits"])
    delta_pct = abs(float(item["delta_pct"])) if isinstance(item.get("delta_pct"), (int, float)) else 0.0
    delta = abs(float(item["delta"])) if isinstance(item.get("delta"), (int, float)) else 0.0
    return (-status_rank, -summary_field_count, -summary_magnitude, -delta_pct, -delta, item["name"])


def _format_compare_focus_change(item: dict[str, Any], uses_summary_delta: bool) -> str:
    if item["summary_bits"]:
        return ", ".join(item["summary_bits"])
    if not uses_summary_delta and isinstance(item.get("delta_pct"), (int, float)):
        return f"{float(item['delta_pct']):+.1f}%"
    if not uses_summary_delta and isinstance(item.get("delta"), (int, float)):
        return f"{float(item['delta']):+.3f}"
    return "No summary drift"


def _format_local_summary_delta(summary_delta: dict[str, Any]) -> list[str]:
    formatted: list[str] = []
    for name, delta in summary_delta.items():
        if not isinstance(name, str) or not name:
            raise ValueError("Corpus compare report summary_delta keys must be non-empty strings.")
        if not isinstance(delta, int):
            raise ValueError("Corpus compare report summary_delta values must be integers.")
        if delta != 0:
            formatted.append(f"{name} {delta:+d}")
    return formatted


def _markdown_link(label: str, url: str) -> str:
    return f"[{label}]({url})"


def _short_sha(value: str) -> str:
    return value[:7]


def _parse_timestamp(value: str, *, label: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Corpus baseline state {label} must be an ISO-8601 timestamp.") from exc


def _validate_date(value: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid release date {value!r}; expected YYYY-MM-DD.") from exc


_COMPARE_CATEGORY_LABELS = (
    ("parser", "parser"),
    ("resolver", "resolver"),
    ("parse_health", "parse-health"),
    ("non_structural", "non-structural"),
)

_COMPARE_ATTRIBUTION_LABELS = (
    ("discovery", "discovery"),
    ("symbol_extraction", "symbol-extraction"),
    ("call_resolution", "call-resolution"),
    ("chain_enumeration", "chain-enumeration"),
    ("parse_health", "parse-health"),
    ("non_structural", "non-structural"),
)

_COMPARE_REVIEW_HINTS = (
    (
        "discovery",
        "discovery",
        ("src/callchain/languages/base.py", "src/callchain/core/callgraph.py"),
        "Review file discovery, skip-dir rules, path filtering, and language auto-detection.",
    ),
    (
        "symbol_extraction",
        "symbol-extraction",
        ("src/callchain/languages/*.py",),
        "Review language parsers that extract functions, classes, methods, imports, and variables.",
    ),
    (
        "call_resolution",
        "call-resolution",
        ("src/callchain/core/callgraph.py", "src/callchain/languages/*.py"),
        "Review raw call extraction and cross-file edge resolution.",
    ),
    (
        "chain_enumeration",
        "chain-enumeration",
        ("src/callchain/core/chain_enum.py",),
        "Review chain traversal, depth/count limits, and cross-file filtering.",
    ),
    (
        "parse_health",
        "parse-health",
        ("src/callchain/languages/*.py", "src/callchain/core/callgraph.py"),
        "Review parser failures, parse-error collection, and file-level error handling.",
    ),
    (
        "non_structural",
        "non-structural",
        ("src/callchain/devtools/corpus.py", ".github/workflows/corpus-baseline-compare.yml"),
        "Review corpus thresholds, compare rendering, and workflow gating rather than structural analysis.",
    ),
)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
