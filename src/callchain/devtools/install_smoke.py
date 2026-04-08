"""Install-smoke helpers for validating the published CallChain package path."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

MIN_SMOKE_COUNTS = {
    "total_files": 1,
    "total_functions": 1,
    "total_edges": 1,
    "total_chains": 1,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an install-smoke check against a built CallChain artifact.")
    parser.add_argument("--project-root", default=".", help="Project root that contains dist/ and examples/.")
    parser.add_argument("--dist-dir", default="dist", help="Directory that contains built wheel/sdist artifacts.")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--artifact", default=None, help="Explicit wheel or sdist path to install.")
    source_group.add_argument(
        "--package-spec",
        default=None,
        help="Package spec to install from an index, e.g. callchain==<version>.",
    )
    parser.add_argument("--index-url", default=None, help="Optional primary package index URL for package-spec mode.")
    parser.add_argument(
        "--extra-index-url",
        default=None,
        help="Optional fallback package index URL for package-spec mode.",
    )
    parser.add_argument("--example", default="examples/smoke_repo", help="Example project to analyze after install.")
    parser.add_argument("--python", dest="python_executable", default=sys.executable, help="Python executable for the smoke venv.")
    parser.add_argument("--output", default=None, help="Optional output file for the rendered smoke summary.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text.")
    args = parser.parse_args(argv)

    result = run_install_smoke(
        project_root=Path(args.project_root).resolve(),
        dist_dir=Path(args.dist_dir),
        example=Path(args.example),
        artifact=Path(args.artifact) if args.artifact else None,
        package_spec=args.package_spec,
        index_url=args.index_url,
        extra_index_url=args.extra_index_url,
        python_executable=args.python_executable,
    )
    rendered = json.dumps(result, indent=2) if args.json else format_install_smoke_summary(result)
    _write_output(rendered, args.output)
    return 0


def run_install_smoke(
    *,
    project_root: Path,
    dist_dir: Path,
    example: Path,
    artifact: Path | None = None,
    package_spec: str | None = None,
    index_url: str | None = None,
    extra_index_url: str | None = None,
    python_executable: str | None = None,
    workspace: Path | None = None,
    runner: Callable[[list[str], Path | None], None] | None = None,
) -> dict[str, Any]:
    install_target = _select_install_target(
        project_root=project_root,
        dist_dir=dist_dir,
        artifact=artifact,
        package_spec=package_spec,
        index_url=index_url,
        extra_index_url=extra_index_url,
    )
    example_path = _resolve_existing_directory(project_root, example, label="Example project")
    execute = runner or _run_command
    python_cmd = python_executable or sys.executable

    if workspace is not None:
        return _run_install_smoke_in_workspace(
            project_root=project_root,
            install_target=install_target,
            example_path=example_path,
            python_executable=python_cmd,
            workspace=workspace,
            runner=execute,
        )

    with tempfile.TemporaryDirectory(prefix="callchain-install-smoke-") as temp_dir:
        return _run_install_smoke_in_workspace(
            project_root=project_root,
            install_target=install_target,
            example_path=example_path,
            python_executable=python_cmd,
            workspace=Path(temp_dir),
            runner=execute,
        )


def _run_install_smoke_in_workspace(
    *,
    project_root: Path,
    install_target: dict[str, Any],
    example_path: Path,
    python_executable: str,
    workspace: Path,
    runner: Callable[[list[str], Path | None], None],
) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    report_dir = workspace / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = workspace / "venv"
    json_report = report_dir / "analysis.json"
    html_report = report_dir / "analysis.html"
    venv_python = _venv_python_path(venv_dir)
    callchain_executable = _venv_executable(venv_dir, "callchain")

    commands = [
        [python_executable, "-m", "venv", str(venv_dir)],
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
        _build_install_command(venv_python=venv_python, install_target=install_target),
        [str(callchain_executable), "--version"],
        [str(callchain_executable), "analyze", str(example_path), "--format", "json", "--output", str(json_report)],
        [str(callchain_executable), "analyze", str(example_path), "--format", "html", "--output", str(html_report)],
    ]
    for command in commands:
        runner(command, project_root)

    analysis = _load_smoke_analysis_report(json_report)
    _validate_html_report(html_report)
    return {
        "install_mode": install_target["mode"],
        "install_target": install_target["value"],
        "artifact": str(install_target["artifact_path"]) if install_target["artifact_path"] is not None else None,
        "package_spec": install_target["package_spec"],
        "index_url": install_target["index_url"],
        "extra_index_url": install_target["extra_index_url"],
        "example": str(example_path),
        "workspace": str(workspace),
        "venv_python": str(venv_python),
        "callchain_executable": str(callchain_executable),
        "analysis": analysis,
        "commands": [shlex.join(command) for command in commands],
    }


def _select_install_artifact(*, project_root: Path, dist_dir: Path, artifact: Path | None) -> Path:
    if artifact is not None:
        artifact_path = _resolve_existing_file(project_root, artifact, label="Install artifact")
        if artifact_path.suffix not in {".whl", ".gz"}:
            raise ValueError("Install artifact must be a wheel or source distribution.")
        return artifact_path

    dist_path = _resolve_existing_directory(project_root, dist_dir, label="Distribution directory")
    wheels = sorted(dist_path.glob("*.whl"))
    if wheels:
        return wheels[-1].resolve()
    sdists = sorted(dist_path.glob("*.tar.gz"))
    if sdists:
        return sdists[-1].resolve()
    raise ValueError(f"No wheel or source distribution artifacts found in {dist_path}.")


def _select_install_target(
    *,
    project_root: Path,
    dist_dir: Path,
    artifact: Path | None,
    package_spec: str | None,
    index_url: str | None,
    extra_index_url: str | None,
) -> dict[str, Any]:
    if package_spec is not None:
        cleaned = package_spec.strip()
        if not cleaned:
            raise ValueError("Package spec must be a non-empty string.")
        return {
            "mode": "package-spec",
            "value": cleaned,
            "artifact_path": None,
            "package_spec": cleaned,
            "index_url": index_url,
            "extra_index_url": extra_index_url,
        }
    if index_url is not None or extra_index_url is not None:
        raise ValueError("Index URLs can only be used together with --package-spec.")
    artifact_path = _select_install_artifact(project_root=project_root, dist_dir=dist_dir, artifact=artifact)
    return {
        "mode": "artifact",
        "value": str(artifact_path),
        "artifact_path": artifact_path,
        "package_spec": None,
        "index_url": None,
        "extra_index_url": None,
    }


def _build_install_command(*, venv_python: Path, install_target: dict[str, Any]) -> list[str]:
    command = [str(venv_python), "-m", "pip", "install"]
    if install_target["index_url"]:
        command.extend(["--index-url", install_target["index_url"]])
    if install_target["extra_index_url"]:
        command.extend(["--extra-index-url", install_target["extra_index_url"]])
    command.append(install_target["value"])
    return command


def _resolve_existing_directory(project_root: Path, path: Path, *, label: str) -> Path:
    resolved = path if path.is_absolute() else (project_root / path)
    resolved = resolved.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"{label} {resolved} does not exist.")
    return resolved


def _resolve_existing_file(project_root: Path, path: Path, *, label: str) -> Path:
    resolved = path if path.is_absolute() else (project_root / path)
    resolved = resolved.resolve()
    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"{label} {resolved} does not exist.")
    return resolved


def _venv_bin_dir(venv_dir: Path, *, windows: bool | None = None) -> Path:
    use_windows = windows if windows is not None else os_name_is_windows()
    return venv_dir / ("Scripts" if use_windows else "bin")


def _venv_python_path(venv_dir: Path, *, windows: bool | None = None) -> Path:
    binary = "python.exe" if (windows if windows is not None else os_name_is_windows()) else "python"
    return _venv_bin_dir(venv_dir, windows=windows) / binary


def _venv_executable(venv_dir: Path, executable: str, *, windows: bool | None = None) -> Path:
    suffix = ".exe" if (windows if windows is not None else os_name_is_windows()) else ""
    return _venv_bin_dir(venv_dir, windows=windows) / f"{executable}{suffix}"


def os_name_is_windows() -> bool:
    return sys.platform.startswith("win")


def _run_command(command: list[str], cwd: Path | None) -> None:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise ValueError(f"Command failed ({completed.returncode}): {shlex.join(command)}\n{stderr}")


def _load_smoke_analysis_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Install smoke analysis report {path} does not exist.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Install smoke analysis report {path} is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Install smoke analysis report must be a JSON object.")

    languages = payload.get("languages")
    if not isinstance(languages, list) or any(not isinstance(item, str) or not item for item in languages):
        raise ValueError("Install smoke analysis report languages must be a list of non-empty strings.")
    if not languages:
        raise ValueError("Install smoke analysis report must include at least one detected language.")

    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("Install smoke analysis report summary must be an object.")
    normalized_summary: dict[str, int] = {}
    for key, minimum in MIN_SMOKE_COUNTS.items():
        value = summary.get(key)
        if not isinstance(value, int):
            raise ValueError(f"Install smoke analysis report summary.{key} must be an integer.")
        if value < minimum:
            raise ValueError(f"Install smoke analysis report summary.{key} must be >= {minimum}.")
        normalized_summary[key] = value

    total_classes = summary.get("total_classes")
    if not isinstance(total_classes, int):
        raise ValueError("Install smoke analysis report summary.total_classes must be an integer.")

    parse_errors = payload.get("parse_errors")
    if not isinstance(parse_errors, list):
        raise ValueError("Install smoke analysis report parse_errors must be a list.")
    if parse_errors:
        raise ValueError("Install smoke analysis report parse_errors must be empty.")

    return {
        "languages": languages,
        "summary": {
            **normalized_summary,
            "total_classes": total_classes,
        },
        "parse_errors": parse_errors,
    }


def _validate_html_report(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"Install smoke HTML report {path} does not exist.")
    content = path.read_text(encoding="utf-8")
    if "<html" not in content.lower():
        raise ValueError("Install smoke HTML report does not look like HTML output.")


def format_install_smoke_summary(result: dict[str, Any]) -> str:
    analysis = result["analysis"]
    summary = analysis["summary"]
    languages = ", ".join(analysis["languages"])
    lines = ["Install smoke passed:"]
    if result["artifact"] is not None:
        lines.append(f"  artifact: {result['artifact']}")
    if result["package_spec"] is not None:
        lines.append(f"  package spec: {result['package_spec']}")
    if result["index_url"] is not None:
        lines.append(f"  index url: {result['index_url']}")
    if result["extra_index_url"] is not None:
        lines.append(f"  extra index url: {result['extra_index_url']}")
    lines.extend(
        [
            f"  example: {result['example']}",
            f"  languages: {languages}",
            (
                "  totals: "
                f"files={summary['total_files']}, functions={summary['total_functions']}, "
                f"classes={summary['total_classes']}, edges={summary['total_edges']}, "
                f"chains={summary['total_chains']}"
            ),
            f"  callchain: {result['callchain_executable']}",
        ]
    )
    return "\n".join(lines)


def _write_output(rendered: str, output: str | None) -> None:
    if output is None:
        print(rendered)
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered + ("\n" if not rendered.endswith("\n") else ""), encoding="utf-8")
