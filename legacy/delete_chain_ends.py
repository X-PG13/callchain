import argparse
import json
import os
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

import ast


def load_chain(chains_jsonl: str, chain_index: Optional[int], summary_json: Optional[str]) -> Dict[str, Any]:
    """Load a specific chain by index from JSONL. If chain_index is None,
    try to use longest_chain_index from summary_json.
    """
    if chain_index is None:
        if not summary_json:
            raise ValueError("chain_index is None and no summary_json provided")
        with open(summary_json, "r", encoding="utf-8") as f:
            summary = json.load(f)
        idx = summary.get("longest_chain_index")
        if idx is None or idx < 0:
            raise ValueError("Summary does not contain a valid longest_chain_index")
        chain_index = idx

    # Read JSONL and return the chain at index
    with open(chains_jsonl, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i == chain_index:
                return json.loads(line)

    raise IndexError(f"Chain index {chain_index} out of range for {chains_jsonl}")


def find_node_spanning_line(module: ast.Module, line: int) -> Tuple[Optional[ast.AST], Optional[List[str]]]:
    """Find the FunctionDef or AsyncFunctionDef (or method) that spans the given line.
    Returns the node and a list representing the qualified name path [class?, func]."""
    target: Optional[ast.AST] = None
    qname: Optional[List[str]] = None

    class StackVisitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: List[str] = []
            self.found: Optional[ast.AST] = None
            self.qname: Optional[List[str]] = None

        def generic_visit(self, node: ast.AST):
            # Only nodes with lineno and end_lineno are relevant
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is not None and end is not None and start <= line <= end:
                # Track class names in stack
                if isinstance(node, ast.ClassDef):
                    self.stack.append(node.name)
                    super().generic_visit(node)
                    self.stack.pop()
                    return
                # If a function spans the line, record it
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.found = node
                    # Build qualified name path
                    path = list(self.stack)
                    path.append(node.name)
                    self.qname = path
                    # Still visit children in case of nested defs, to pick the innermost
                    super().generic_visit(node)
                    return
            super().generic_visit(node)

    v = StackVisitor()
    v.visit(module)
    return v.found, v.qname


def remove_span_from_file(file_path: str, start_line: int, end_line: int, backup_dir: Optional[str]) -> Tuple[str, str]:
    """Remove lines [start_line, end_line] (1-indexed, inclusive) from file.
    Returns a tuple of (deleted_content, new_content). Optionally writes a backup copy.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    lines = content.splitlines(True)  # keep line endings

    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        raise ValueError(f"Invalid span {start_line}-{end_line} for file with {len(lines)} lines")

    deleted = "".join(lines[start_line - 1 : end_line])
    new_lines = lines[: start_line - 1] + lines[end_line :]
    new_content = "".join(new_lines)

    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, os.path.basename(file_path))
        shutil.copyfile(file_path, backup_path)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return deleted, new_content


def process_endpoint(endpoint: Dict[str, Any], backup_dir: Optional[str]) -> Dict[str, Any]:
    """Given an endpoint with file_path and line_number, locate enclosing function and remove it.
    Returns a log dict with details and deleted content.
    """
    file_path = endpoint.get("file_path")
    line_number = endpoint.get("line_number")
    raw_name = endpoint.get("raw")
    if not file_path or line_number is None:
        raise ValueError(f"Endpoint missing file_path or line_number: {endpoint}")

    # Parse file and locate the function spanning the line
    with open(file_path, "r", encoding="utf-8") as f:
        source = f.read()
    module = ast.parse(source)
    node, qname = find_node_spanning_line(module, int(line_number))
    if node is None:
        raise RuntimeError(f"No function spanning line {line_number} in {file_path}")

    start_line = int(getattr(node, "lineno"))
    end_line = int(getattr(node, "end_lineno"))

    deleted, _ = remove_span_from_file(file_path, start_line, end_line, backup_dir)

    return {
        "file_path": file_path,
        "line_number": line_number,
        "function_name": getattr(node, "name", None),
        "qualified_name": qname,
        "start_line": start_line,
        "end_line": end_line,
        "raw": raw_name,
        "deleted_content": deleted,
    }


def main():
    parser = argparse.ArgumentParser(description="Delete endpoints of a selected call chain and log deletions. Optionally create a new repo copy containing the changes and log.")
    parser.add_argument("--chains-jsonl", required=True, help="Path to chains JSONL file")
    parser.add_argument("--summary", required=False, help="Path to chains summary JSON file (for longest chain selection)")
    parser.add_argument("--chain-index", type=int, required=False, help="Index of the chain in JSONL (0-based). If omitted, uses longest from summary")
    parser.add_argument("--output-log", required=False, help="Path to write deletion log JSON")
    parser.add_argument("--backup-dir", required=False, default=None, help="Directory to store backups of modified files (defaults depend on repo mode)")
    parser.add_argument("--project-root", required=False, default=".", help="Project root to resolve relative file paths in chains")
    parser.add_argument("--repo", required=False, help="Create a new repo directory (name) containing a copy of project with deletions and log")
    parser.add_argument("--repo-base", required=False, default="repos", help="Base directory under which to create the new repo")
    parser.add_argument("--tests-dir", required=False, default=None, help="Tests directory name relative to project root (e.g., 'tests')")
    parser.add_argument("--delete-policy", required=False, default="delete_top_bottom",
                        choices=["delete_top_bottom", "delete_first_call_and_bottom"],
                        help="Endpoint deletion policy: delete top+bottom, or skip test top and delete first call + bottom")

    args = parser.parse_args()

    chain = load_chain(args.chains_jsonl, args.chain_index, args.summary)
    entries: List[Dict[str, Any]] = chain.get("chain", [])
    if not entries or len(entries) < 2:
        print("Selected chain has fewer than 2 entries; nothing to delete.", file=sys.stderr)
        sys.exit(1)

    def is_test_entry(ep: Dict[str, Any]) -> bool:
        fp = str(ep.get("file_path", "")).replace("\\", "/")
        if not fp:
            return False
        # prefer explicit tests dir
        if args.tests_dir:
            td = args.tests_dir.strip("/")
            return fp.startswith(td + "/") or ("/" + td + "/") in fp or fp == td
        # fallback heuristics
        return fp.startswith("tests/") or "/tests/" in fp or fp.endswith("/tests")

    # pick endpoints per policy
    top_idx = 0
    bottom_idx = len(entries) - 1
    if args.delete_policy == "delete_first_call_and_bottom":
        if len(entries) >= 2 and is_test_entry(entries[0]):
            top_idx = 1
        else:
            top_idx = 0
    top = entries[top_idx]
    bottom = entries[bottom_idx]

    # If --repo is provided, copy project_root into a new repo and operate on the copy
    target_root = args.project_root
    output_log_path = args.output_log
    backup_dir = args.backup_dir

    def sanitize_name(name: str) -> str:
        return "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "_" for ch in name)

    if args.repo:
        repo_name = sanitize_name(args.repo)
        os.makedirs(args.repo_base, exist_ok=True)
        repo_dir = os.path.join(args.repo_base, repo_name)
        if os.path.exists(repo_dir):
            print(f"Target repo directory already exists: {repo_dir}", file=sys.stderr)
            sys.exit(1)
        os.makedirs(repo_dir, exist_ok=True)

        # Copy the project root into the repo directory, excluding repo-base and common caches/venvs
        target_root = os.path.join(repo_dir, os.path.basename(args.project_root.rstrip(os.sep)))

        def _ignore_copy(dirpath: str, names: List[str]):
            ignored: List[str] = []
            # Avoid recursively copying the repo-base directory inside the project
            repo_base_name = os.path.basename(args.repo_base.rstrip(os.sep))
            if repo_base_name in names:
                ignored.append(repo_base_name)
            # Skip common environment/cache directories to keep the copy lean and avoid path blowups
            for n in ("venv", ".venv", ".venv311", "__pycache__", ".pytest_cache"):
                if n in names:
                    ignored.append(n)
            return ignored

        shutil.copytree(args.project_root, target_root, ignore=_ignore_copy)

        # Default output log and backup dir inside the repo if not provided
        if not output_log_path:
            output_log_path = os.path.join(repo_dir, "deletion_log.json")
        if not backup_dir:
            backup_dir = os.path.join(repo_dir, "backups")

    # Perform deletions and collect logs
    logs: List[Dict[str, Any]] = []
    # Resolve file paths relative to project_root if needed
    def resolve(ep: Dict[str, Any]) -> Dict[str, Any]:
        fp = ep.get("file_path")
        if fp and not os.path.isabs(fp):
            # Resolve against target_root copy
            candidate = os.path.join(target_root, os.path.relpath(fp, args.project_root) if os.path.isabs(fp) else fp)
            ep = dict(ep)
            ep["file_path"] = candidate
        return ep

    # avoid duplicate deletion if indices coincide
    logs.append(process_endpoint(resolve(top), backup_dir))
    if bottom_idx != top_idx:
        logs.append(process_endpoint(resolve(bottom), backup_dir))

    # Write log
    os.makedirs(os.path.dirname(output_log_path) or ".", exist_ok=True)

    # Compose dotted names summary if possible
    def dotted_from_log(log: Dict[str, Any]) -> Optional[str]:
        fp = log.get("file_path")
        func = log.get("function_name")
        if not fp or not func:
            return None
        # Try to make path relative to target_root
        try:
            rel = os.path.relpath(fp, target_root)
        except Exception:
            rel = fp
        rel = rel.replace(os.sep, ".")
        if rel.endswith(".py"):
            rel = rel[:-3]
        # Remove trailing .__init__
        rel = rel.replace(".__init__", "")
        return f"{rel}.{func}"

    summary_list = [dotted_from_log(l) for l in logs]
    summary_list = [s for s in summary_list if s]

    payload = {
        "chain_index": args.chain_index if args.chain_index is not None else "longest",
        "source": os.path.abspath(args.chains_jsonl),
        "project_root": os.path.abspath(target_root),
        "delete_policy": args.delete_policy,
        "deletions": logs,
    }
    if summary_list:
        payload["repo_summary"] = f"deleted {', '.join(summary_list)}"

    with open(output_log_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Deletion log written to {output_log_path}")


if __name__ == "__main__":
    main()