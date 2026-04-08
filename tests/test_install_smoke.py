"""Tests for packaged-install smoke tooling."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from callchain.devtools import install_smoke


def test_select_install_artifact_prefers_explicit_and_wheels(tmp_path):
    project_root = tmp_path
    dist_dir = project_root / "dist"
    dist_dir.mkdir()
    sdist = dist_dir / "callchain-0.1.0.tar.gz"
    wheel = dist_dir / "callchain-0.1.0-py3-none-any.whl"
    newer_wheel = dist_dir / "callchain-0.1.1-py3-none-any.whl"
    sdist.write_text("sdist", encoding="utf-8")
    wheel.write_text("wheel", encoding="utf-8")
    newer_wheel.write_text("newer wheel", encoding="utf-8")
    explicit = project_root / "custom.whl"
    explicit.write_text("explicit", encoding="utf-8")
    bad_explicit = project_root / "custom.zip"
    bad_explicit.write_text("zip", encoding="utf-8")

    assert install_smoke._select_install_artifact(
        project_root=project_root,
        dist_dir=Path("dist"),
        artifact=None,
    ) == newer_wheel.resolve()
    assert install_smoke._select_install_artifact(
        project_root=project_root,
        dist_dir=Path("dist"),
        artifact=Path("custom.whl"),
    ) == explicit.resolve()

    newer_wheel.unlink()
    wheel.unlink()
    assert install_smoke._select_install_artifact(
        project_root=project_root,
        dist_dir=Path("dist"),
        artifact=None,
    ) == sdist.resolve()

    with pytest.raises(ValueError, match="Install artifact .* does not exist"):
        install_smoke._select_install_artifact(
            project_root=project_root,
            dist_dir=Path("dist"),
            artifact=Path("missing.whl"),
        )
    with pytest.raises(ValueError, match="must be a wheel or source distribution"):
        install_smoke._select_install_artifact(
            project_root=project_root,
            dist_dir=Path("dist"),
            artifact=Path("custom.zip"),
        )

    empty_dist = project_root / "empty-dist"
    empty_dist.mkdir()
    with pytest.raises(ValueError, match="No wheel or source distribution artifacts found"):
        install_smoke._select_install_artifact(
            project_root=project_root,
            dist_dir=Path("empty-dist"),
            artifact=None,
        )

    assert install_smoke._select_install_target(
        project_root=project_root,
        dist_dir=Path("dist"),
        artifact=None,
        package_spec="callchain==0.1.0",
        index_url="https://test.pypi.org/simple/",
        extra_index_url="https://pypi.org/simple/",
    ) == {
        "mode": "package-spec",
        "value": "callchain==0.1.0",
        "artifact_path": None,
        "package_spec": "callchain==0.1.0",
        "index_url": "https://test.pypi.org/simple/",
        "extra_index_url": "https://pypi.org/simple/",
    }
    assert install_smoke._select_install_target(
        project_root=project_root,
        dist_dir=Path("dist"),
        artifact=None,
        package_spec=None,
        index_url=None,
        extra_index_url=None,
    )["mode"] == "artifact"
    with pytest.raises(ValueError, match="Package spec must be a non-empty string"):
        install_smoke._select_install_target(
            project_root=project_root,
            dist_dir=Path("dist"),
            artifact=None,
            package_spec="   ",
            index_url=None,
            extra_index_url=None,
        )
    with pytest.raises(ValueError, match="Index URLs can only be used together with --package-spec"):
        install_smoke._select_install_target(
            project_root=project_root,
            dist_dir=Path("dist"),
            artifact=None,
            package_spec=None,
            index_url="https://test.pypi.org/simple/",
            extra_index_url=None,
        )


def test_run_install_smoke_builds_commands_and_validates_outputs(tmp_path):
    project_root = tmp_path
    dist_dir = project_root / "dist"
    dist_dir.mkdir()
    artifact = dist_dir / "callchain-0.1.0-py3-none-any.whl"
    artifact.write_text("wheel", encoding="utf-8")
    example = project_root / "examples" / "smoke_repo"
    example.mkdir(parents=True)

    calls: list[tuple[list[str], Path | None]] = []

    def fake_runner(command: list[str], cwd: Path | None) -> None:
        calls.append((command, cwd))
        if "--output" in command:
            output = Path(command[command.index("--output") + 1])
            if "json" in command:
                output.write_text(
                    json.dumps(
                        {
                            "languages": ["python"],
                            "summary": {
                                "total_files": 3,
                                "total_functions": 3,
                                "total_classes": 0,
                                "total_edges": 2,
                                "total_chains": 1,
                            },
                            "parse_errors": [],
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                output.write_text("<html><body>CallChain</body></html>", encoding="utf-8")

    result = install_smoke.run_install_smoke(
        project_root=project_root,
        dist_dir=Path("dist"),
        example=Path("examples/smoke_repo"),
        python_executable="python-test",
        workspace=project_root / "workspace",
        runner=fake_runner,
    )

    assert result["artifact"] == str(artifact.resolve())
    assert result["example"] == str(example.resolve())
    assert result["workspace"] == str((project_root / "workspace").resolve())
    assert result["analysis"]["languages"] == ["python"]
    assert result["analysis"]["summary"]["total_files"] == 3
    assert len(result["commands"]) == 6
    assert calls[0][0] == ["python-test", "-m", "venv", str(project_root / "workspace" / "venv")]
    assert calls[0][1] == project_root
    assert calls[3][0][-1] == "--version"
    assert calls[4][0][1:4] == ["analyze", str(example.resolve()), "--format"]
    assert calls[5][0][4] == "html"
    rendered = install_smoke.format_install_smoke_summary(result)
    assert "Install smoke passed:" in rendered
    assert "languages: python" in rendered
    assert "totals: files=3, functions=3, classes=0, edges=2, chains=1" in rendered


def test_run_install_smoke_with_package_spec_uses_index_urls(tmp_path):
    project_root = tmp_path
    example = project_root / "examples" / "smoke_repo"
    example.mkdir(parents=True)
    calls: list[list[str]] = []

    def fake_runner(command: list[str], cwd: Path | None) -> None:
        del cwd
        calls.append(command)
        if "--output" in command:
            output = Path(command[command.index("--output") + 1])
            if "json" in command:
                output.write_text(
                    json.dumps(
                        {
                            "languages": ["python"],
                            "summary": {
                                "total_files": 2,
                                "total_functions": 2,
                                "total_classes": 0,
                                "total_edges": 1,
                                "total_chains": 1,
                            },
                            "parse_errors": [],
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                output.write_text("<html><body>ok</body></html>", encoding="utf-8")

    result = install_smoke.run_install_smoke(
        project_root=project_root,
        dist_dir=Path("dist"),
        example=Path("examples/smoke_repo"),
        package_spec="callchain==0.1.0",
        index_url="https://test.pypi.org/simple/",
        extra_index_url="https://pypi.org/simple/",
        python_executable="python-test",
        workspace=project_root / "workspace",
        runner=fake_runner,
    )

    assert result["install_mode"] == "package-spec"
    assert result["install_target"] == "callchain==0.1.0"
    assert result["artifact"] is None
    assert result["package_spec"] == "callchain==0.1.0"
    assert result["index_url"] == "https://test.pypi.org/simple/"
    assert result["extra_index_url"] == "https://pypi.org/simple/"
    assert calls[2] == [
        str(project_root / "workspace" / "venv" / "bin" / "python"),
        "-m",
        "pip",
        "install",
        "--index-url",
        "https://test.pypi.org/simple/",
        "--extra-index-url",
        "https://pypi.org/simple/",
        "callchain==0.1.0",
    ]
    assert "package spec: callchain==0.1.0" in install_smoke.format_install_smoke_summary(result)
    assert "index url: https://test.pypi.org/simple/" in install_smoke.format_install_smoke_summary(result)
    assert "extra index url: https://pypi.org/simple/" in install_smoke.format_install_smoke_summary(result)


def test_run_install_smoke_without_workspace_uses_tempdir(monkeypatch, tmp_path):
    project_root = tmp_path
    dist_dir = project_root / "dist"
    dist_dir.mkdir()
    artifact = dist_dir / "callchain-0.1.0-py3-none-any.whl"
    artifact.write_text("wheel", encoding="utf-8")
    example = project_root / "examples" / "smoke_repo"
    example.mkdir(parents=True)
    delegated: dict[str, object] = {}

    def fake_run_install_smoke_in_workspace(**kwargs: object) -> dict[str, object]:
        delegated.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(install_smoke, "_run_install_smoke_in_workspace", fake_run_install_smoke_in_workspace)
    assert install_smoke.run_install_smoke(
        project_root=project_root,
        dist_dir=Path("dist"),
        example=Path("examples/smoke_repo"),
    ) == {"status": "ok"}
    assert delegated["install_target"] == {
        "mode": "artifact",
        "value": str(artifact.resolve()),
        "artifact_path": artifact.resolve(),
        "package_spec": None,
        "index_url": None,
        "extra_index_url": None,
    }
    assert delegated["example_path"] == example.resolve()
    assert isinstance(delegated["workspace"], Path)


def test_report_validation_and_command_failures(tmp_path):
    good_report = tmp_path / "good.json"
    good_report.write_text(
        json.dumps(
            {
                "languages": ["python"],
                "summary": {
                    "total_files": 1,
                    "total_functions": 1,
                    "total_classes": 0,
                    "total_edges": 1,
                    "total_chains": 1,
                },
                "parse_errors": [],
            }
        ),
        encoding="utf-8",
    )
    assert install_smoke._load_smoke_analysis_report(good_report) == {
        "languages": ["python"],
        "summary": {
            "total_files": 1,
            "total_functions": 1,
            "total_classes": 0,
            "total_edges": 1,
            "total_chains": 1,
        },
        "parse_errors": [],
    }

    html = tmp_path / "report.html"
    html.write_text("<html><body>ok</body></html>", encoding="utf-8")
    install_smoke._validate_html_report(html)

    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="does not exist"):
        install_smoke._load_smoke_analysis_report(missing)

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        install_smoke._load_smoke_analysis_report(bad_json)

    wrong_root = tmp_path / "wrong-root.json"
    wrong_root.write_text(json.dumps([]), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        install_smoke._load_smoke_analysis_report(wrong_root)

    no_languages = tmp_path / "no-languages.json"
    no_languages.write_text(
        json.dumps(
            {
                "languages": [],
                "summary": {
                    "total_files": 1,
                    "total_functions": 1,
                    "total_classes": 0,
                    "total_edges": 1,
                    "total_chains": 1,
                },
                "parse_errors": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="at least one detected language"):
        install_smoke._load_smoke_analysis_report(no_languages)

    bad_languages = tmp_path / "bad-languages.json"
    bad_languages.write_text(
        json.dumps(
            {
                "languages": [""],
                "summary": {
                    "total_files": 1,
                    "total_functions": 1,
                    "total_classes": 0,
                    "total_edges": 1,
                    "total_chains": 1,
                },
                "parse_errors": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="languages must be a list of non-empty strings"):
        install_smoke._load_smoke_analysis_report(bad_languages)

    bad_summary = tmp_path / "bad-summary.json"
    bad_summary.write_text(json.dumps({"languages": ["python"], "summary": [], "parse_errors": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="summary must be an object"):
        install_smoke._load_smoke_analysis_report(bad_summary)

    low_counts = tmp_path / "low-counts.json"
    low_counts.write_text(
        json.dumps(
            {
                "languages": ["python"],
                "summary": {
                    "total_files": 0,
                    "total_functions": 1,
                    "total_classes": 0,
                    "total_edges": 1,
                    "total_chains": 1,
                },
                "parse_errors": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="summary.total_files must be >= 1"):
        install_smoke._load_smoke_analysis_report(low_counts)

    missing_integer = tmp_path / "missing-integer.json"
    missing_integer.write_text(
        json.dumps(
            {
                "languages": ["python"],
                "summary": {
                    "total_files": 1,
                    "total_functions": "1",
                    "total_classes": 0,
                    "total_edges": 1,
                    "total_chains": 1,
                },
                "parse_errors": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="summary.total_functions must be an integer"):
        install_smoke._load_smoke_analysis_report(missing_integer)

    bad_classes = tmp_path / "bad-classes.json"
    bad_classes.write_text(
        json.dumps(
            {
                "languages": ["python"],
                "summary": {
                    "total_files": 1,
                    "total_functions": 1,
                    "total_classes": "0",
                    "total_edges": 1,
                    "total_chains": 1,
                },
                "parse_errors": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="summary.total_classes must be an integer"):
        install_smoke._load_smoke_analysis_report(bad_classes)

    non_list_errors = tmp_path / "non-list-errors.json"
    non_list_errors.write_text(
        json.dumps(
            {
                "languages": ["python"],
                "summary": {
                    "total_files": 1,
                    "total_functions": 1,
                    "total_classes": 0,
                    "total_edges": 1,
                    "total_chains": 1,
                },
                "parse_errors": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="parse_errors must be a list"):
        install_smoke._load_smoke_analysis_report(non_list_errors)

    bad_errors = tmp_path / "bad-errors.json"
    bad_errors.write_text(
        json.dumps(
            {
                "languages": ["python"],
                "summary": {
                    "total_files": 1,
                    "total_functions": 1,
                    "total_classes": 0,
                    "total_edges": 1,
                    "total_chains": 1,
                },
                "parse_errors": [{"file": "broken.py"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="parse_errors must be empty"):
        install_smoke._load_smoke_analysis_report(bad_errors)

    missing_html = tmp_path / "missing.html"
    with pytest.raises(ValueError, match="does not exist"):
        install_smoke._validate_html_report(missing_html)

    bad_html = tmp_path / "bad.html"
    bad_html.write_text("not html", encoding="utf-8")
    with pytest.raises(ValueError, match="does not look like HTML output"):
        install_smoke._validate_html_report(bad_html)

    install_smoke._run_command([sys.executable, "-c", "print('ok')"], tmp_path)
    with pytest.raises(ValueError, match="Command failed"):
        install_smoke._run_command(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"],
            tmp_path,
        )


def test_path_helpers_and_main_output(monkeypatch, tmp_path, capsys):
    assert install_smoke._venv_bin_dir(Path("venv"), windows=False) == Path("venv/bin")
    assert install_smoke._venv_bin_dir(Path("venv"), windows=True) == Path("venv/Scripts")
    assert install_smoke._venv_python_path(Path("venv"), windows=False) == Path("venv/bin/python")
    assert install_smoke._venv_python_path(Path("venv"), windows=True) == Path("venv/Scripts/python.exe")
    assert install_smoke._venv_executable(Path("venv"), "callchain", windows=False) == Path("venv/bin/callchain")
    assert install_smoke._venv_executable(Path("venv"), "callchain", windows=True) == Path("venv/Scripts/callchain.exe")
    assert install_smoke.os_name_is_windows() is sys.platform.startswith("win")
    assert install_smoke._build_install_command(
        venv_python=Path("/tmp/venv/bin/python"),
        install_target={
            "mode": "package-spec",
            "value": "callchain==0.1.0",
            "artifact_path": None,
            "package_spec": "callchain==0.1.0",
            "index_url": "https://test.pypi.org/simple/",
            "extra_index_url": "https://pypi.org/simple/",
        },
    ) == [
        "/tmp/venv/bin/python",
        "-m",
        "pip",
        "install",
        "--index-url",
        "https://test.pypi.org/simple/",
        "--extra-index-url",
        "https://pypi.org/simple/",
        "callchain==0.1.0",
    ]
    with pytest.raises(ValueError, match="Example project .* does not exist"):
        install_smoke._resolve_existing_directory(tmp_path, Path("missing"), label="Example project")
    with pytest.raises(ValueError, match="Install artifact .* does not exist"):
        install_smoke._resolve_existing_file(tmp_path, Path("missing.whl"), label="Install artifact")

    project_root = tmp_path / "repo"
    project_root.mkdir()
    smoke_result = {
        "artifact": "/tmp/dist/callchain.whl",
        "install_mode": "artifact",
        "install_target": "/tmp/dist/callchain.whl",
        "package_spec": None,
        "index_url": None,
        "extra_index_url": None,
        "example": "/tmp/repo/examples/smoke_repo",
        "workspace": "/tmp/workspace",
        "venv_python": "/tmp/workspace/venv/bin/python",
        "callchain_executable": "/tmp/workspace/venv/bin/callchain",
        "analysis": {
            "languages": ["python"],
            "summary": {
                "total_files": 3,
                "total_functions": 3,
                "total_classes": 0,
                "total_edges": 2,
                "total_chains": 1,
            },
            "parse_errors": [],
        },
        "commands": ["cmd"],
    }
    monkeypatch.setattr(install_smoke, "run_install_smoke", lambda **_: smoke_result)

    assert install_smoke.main(["--project-root", str(project_root)]) == 0
    plain_output = capsys.readouterr().out
    assert "Install smoke passed:" in plain_output
    assert "artifact: /tmp/dist/callchain.whl" in plain_output

    output_path = tmp_path / "build" / "install-smoke.json"
    assert install_smoke.main(["--project-root", str(project_root), "--json", "--output", str(output_path)]) == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == smoke_result
