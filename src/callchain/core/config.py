"""Config file loading for CallChain.

Loads defaults from .callchain.toml in the project root (or current directory).

Example config:

    [analyze]
    lang = ["python", "javascript"]
    restrict_dir = "src"
    exclude = ["tests/**", "build"]
    max_depth = 30
    only_cross_file = false
    format = "summary"
    cache = true
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

CONFIG_FILENAME = ".callchain.toml"


def load_config(project_path: Path) -> dict[str, Any]:
    """Load config from .callchain.toml in project_path or its parents.

    Returns an empty dict if no config file is found.
    """
    # Search up to project_path and a couple of parents
    candidates = [project_path / CONFIG_FILENAME]
    for parent in project_path.parents[:3]:
        candidates.append(parent / CONFIG_FILENAME)

    for candidate in candidates:
        if candidate.exists():
            return load_config_file(candidate)

    return {}


def load_config_file(path: Path, *, strict: bool = False) -> dict[str, Any]:
    """Load analyze config from an explicit TOML file path."""
    toml = _import_toml_module()
    decode_error = getattr(toml, "TOMLDecodeError", ValueError)

    try:
        with open(path, "rb") as file_obj:
            data = toml.load(file_obj)
        return data.get("analyze", {}) if isinstance(data, dict) else {}
    except (OSError, decode_error):
        if strict:
            raise
        return {}


def merge_cli_config(config: dict[str, Any], cli_args: dict[str, Any]) -> dict[str, Any]:
    """Merge config file values with CLI arguments.

    CLI arguments take precedence when they are explicitly set (non-default/non-empty).
    """
    merged = dict(config)
    for key, val in cli_args.items():
        # Only override config if CLI value is "set"
        if val is None:
            continue
        if isinstance(val, (tuple, list)) and len(val) == 0:
            continue
        merged[key] = val
    return merged


def _import_toml_module() -> Any:
    module_name = "tomllib" if sys.version_info >= (3, 11) else "tomli"
    return importlib.import_module(module_name)
