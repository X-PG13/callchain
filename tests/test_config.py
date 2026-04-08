"""Tests for config loading helpers."""

from __future__ import annotations

import pytest

from callchain.core.config import load_config, load_config_file, merge_cli_config


def test_load_config_searches_parent_directories(tmp_path):
    project = tmp_path / "repo"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    (project / ".callchain.toml").write_text(
        "[analyze]\nlang = [\"python\"]\ncache = true\n",
        encoding="utf-8",
    )

    config = load_config(nested)

    assert config["lang"] == ["python"]
    assert config["cache"] is True


def test_load_config_file_returns_empty_for_invalid_toml(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("[analyze\n", encoding="utf-8")

    assert load_config_file(path) == {}
    with pytest.raises(Exception):
        load_config_file(path, strict=True)


def test_merge_cli_config_prefers_explicit_values():
    merged = merge_cli_config(
        {"lang": ["python"], "cache": True, "exclude": ["tests/**"]},
        {"lang": ("rust",), "cache": False, "exclude": ()},
    )

    assert merged["lang"] == ("rust",)
    assert merged["cache"] is False
    assert merged["exclude"] == ["tests/**"]


def test_merge_cli_config_ignores_none_values():
    merged = merge_cli_config({"lang": ["python"], "cache": True}, {"lang": None, "cache": None})

    assert merged == {"lang": ["python"], "cache": True}
