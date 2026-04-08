"""JSON output formatter."""

from __future__ import annotations

import json
from pathlib import Path

from callchain.core.models import AnalysisResult


def write_json(result: AnalysisResult, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
    return path


def write_chains_jsonl(result: AnalysisResult, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for chain in result.chains:
            record = {
                "length": chain.length,
                "cross_file_transitions": chain.cross_file_transitions,
                "chain": [
                    {
                        "name": n.qualified_name,
                        "file_path": n.file_path,
                        "line": n.line,
                        "language": n.language.value,
                    }
                    for n in chain.nodes
                ],
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
