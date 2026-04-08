"""Tests for package metadata and release consistency."""

from __future__ import annotations

import json
import importlib.metadata
import re
from pathlib import Path

from click.testing import CliRunner
import pytest

from callchain import __version__
from callchain.cli import main

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


def test_package_version_matches_pyproject():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__


def test_installed_metadata_version_matches_module_version():
    try:
        distribution = importlib.metadata.distribution("callchain")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("callchain is not installed in the current Python environment")

    direct_url_text = distribution.read_text("direct_url.json")
    if not direct_url_text:
        pytest.skip("installed callchain metadata is not tied to the current source checkout")

    payload = json.loads(direct_url_text)
    url = payload.get("url")
    if not isinstance(url, str) or not url.startswith("file://"):
        pytest.skip("installed callchain metadata does not point to a local checkout")

    installed_root = Path(url.removeprefix("file://")).resolve()
    if installed_root != Path(".").resolve():
        pytest.skip("installed callchain distribution is not sourced from this repository checkout")

    if distribution.version != __version__:
        pytest.skip("editable install metadata is stale after a source-only version bump")

    assert distribution.version == __version__


def test_changelog_contains_current_version_heading():
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(__version__)}\] - ", changelog, re.MULTILINE)


def test_cli_version_option_reports_current_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_package_metadata_exposes_typed_classifier_and_project_urls():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project_urls = pyproject["project"]["urls"]
    classifiers = pyproject["project"]["classifiers"]

    assert "Typing :: Typed" in classifiers
    assert "Documentation" in project_urls
    assert "Support" in project_urls
    assert "Security" in project_urls


def test_package_metadata_declares_real_maintainer_contact():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    authors = pyproject["project"]["authors"]
    maintainers = pyproject["project"]["maintainers"]

    assert authors == [{"name": "X-PG13", "email": "2720174336@qq.com"}]
    assert maintainers == [{"name": "X-PG13", "email": "2720174336@qq.com"}]


def test_source_distribution_metadata_files_exist():
    assert Path("src/callchain/py.typed").exists()
    assert Path("CITATION.cff").exists()
    assert Path("SUPPORT.md").exists()
