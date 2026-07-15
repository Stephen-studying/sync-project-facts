#!/usr/bin/env python3
"""Enumerate immutable project materials and create a hash-backed manifest."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Iterable

from project_facts_common import iso_mtime, safe_relative, sha256_file, stable_id, utc_now, write_json_idempotent


SOURCE_TYPES = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".rst": "text",
    ".log": "text",
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".tsv": "csv",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "config",
    ".ini": "config",
    ".cfg": "config",
    ".conf": "config",
    ".xml": "config",
    ".py": "code",
    ".pyi": "code",
    ".js": "code",
    ".jsx": "code",
    ".ts": "code",
    ".tsx": "code",
    ".java": "code",
    ".c": "code",
    ".h": "code",
    ".cpp": "code",
    ".hpp": "code",
    ".cs": "code",
    ".go": "code",
    ".rs": "code",
    ".m": "code",
    ".r": "code",
    ".sql": "code",
    ".sh": "code",
    ".ps1": "code",
}

IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
}
IGNORED_EXACT_FILES = {
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
    "project-facts.json",
    "fact-sync-report.md",
    "source-manifest.json",
    "evidence.json",
    "comparison.json",
    "validation.json",
}
IGNORED_SUFFIXES = (
    ".tmp",
    ".temp",
    ".bak",
    ".swp",
    ".swo",
    ".part",
    ".crdownload",
    ".download",
)


def source_type(path: Path) -> str | None:
    return SOURCE_TYPES.get(path.suffix.casefold())


def ignored(path: Path, output: Path | None = None) -> tuple[bool, str | None]:
    name = path.name.casefold()
    if output is not None:
        try:
            if path.resolve() == output.resolve():
                return True, "manifest output"
        except OSError:
            pass
    if path.is_symlink():
        return True, "symbolic link"
    if any(part.casefold() in IGNORED_DIRS for part in path.parts):
        return True, "ignored directory"
    if name.startswith("~$") or name.startswith(".~lock.") or name.endswith("#"):
        return True, "temporary or lock file"
    if name in IGNORED_EXACT_FILES or name.endswith(IGNORED_SUFFIXES):
        return True, "generated, cached, or temporary file"
    return False, None


def iter_input_files(inputs: Iterable[Path]) -> Iterable[Path]:
    seen: set[str] = set()
    for raw in inputs:
        path = raw.resolve()
        if path.is_file():
            candidates = [path]
        elif path.is_dir():
            candidates = (candidate for candidate in path.rglob("*") if candidate.is_file())
        else:
            raise FileNotFoundError(f"Input does not exist: {raw}")
        for candidate in candidates:
            key = os.path.normcase(str(candidate.resolve()))
            if key in seen:
                continue
            seen.add(key)
            yield candidate


def common_root(inputs: list[Path]) -> Path:
    resolved = [path.resolve() for path in inputs]
    if len(resolved) == 1 and resolved[0].is_dir():
        return resolved[0]
    parents = [path if path.is_dir() else path.parent for path in resolved]
    return Path(os.path.commonpath([str(path) for path in parents])).resolve()


def build_manifest(inputs: list[Path], output: Path | None = None) -> dict:
    root = common_root(inputs)
    sources = []
    ignored_items = []
    for path in iter_input_files(inputs):
        skip, reason = ignored(path, output)
        if skip:
            ignored_items.append({"source_path": safe_relative(path, root), "reason": reason})
            continue
        detected = source_type(path)
        if detected is None:
            ignored_items.append({"source_path": safe_relative(path, root), "reason": "unsupported file type"})
            continue
        stat = path.stat()
        sources.append(
            {
                "source_id": stable_id("source", str(path.resolve()).casefold(), sha256_file(path)),
                "source_path": safe_relative(path, root),
                "absolute_path": str(path.resolve()),
                "source_type": detected,
                "source_hash": sha256_file(path),
                "size_bytes": stat.st_size,
                "modified_at": iso_mtime(path),
            }
        )
    sources.sort(key=lambda item: item["source_path"].casefold())
    ignored_items.sort(key=lambda item: item["source_path"].casefold())
    return {
        "manifest_version": "1.0",
        "manifest_id": stable_id("manifest", root.as_posix(), [(s["source_path"], s["source_hash"]) for s in sources]),
        "generated_at": utc_now(),
        "root": str(root),
        "source_count": len(sources),
        "sources": sources,
        "ignored": ignored_items,
        "warnings": [] if len(sources) >= 2 else ["Fewer than two supported project materials were found."],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a read-only manifest for project fact sources.")
    parser.add_argument("inputs", nargs="+", help="Files or directories belonging to one project.")
    parser.add_argument("--output", required=True, help="Output source-manifest.json path.")
    parser.add_argument(
        "--allow-single",
        action="store_true",
        help="Allow a one-source manifest for extractor debugging; the Skill itself still requires two sources.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    manifest = build_manifest([Path(value) for value in args.inputs], output)
    if manifest["source_count"] < 2 and not args.allow_single:
        print("ERROR: sync-project-facts requires at least two supported materials for the same project.")
        return 2
    changed, final_payload = write_json_idempotent(output, manifest)
    action = "Wrote" if changed else "Unchanged"
    print(f"{action}: {output.resolve()} ({final_payload['source_count']} sources)")
    for warning in final_payload.get("warnings", []):
        print(f"WARNING: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
