#!/usr/bin/env python3
"""Shared deterministic helpers for the sync-project-facts scripts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


STATUS_VALUES = (
    "CONSISTENT",
    "EQUIVALENT",
    "SCOPED_DIFFERENCE",
    "STALE",
    "CONTRADICTED",
    "UNSUPPORTED",
    "MISSING",
    "UNRESOLVED",
)
SEVERITY_VALUES = ("Low", "Medium", "High", "Critical")
SEVERITY_RANK = {value: index for index, value in enumerate(SEVERITY_VALUES)}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: Any, length: int = 16) -> str:
    serialized = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}-{sha256_text(serialized)[:length]}"


def normalize_space(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u0000", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_token(value: Any) -> str:
    text = normalize_space(value).casefold()
    text = re.sub(r"[\s_–—]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _without_volatile(value: Any, volatile_keys: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_volatile(item, volatile_keys)
            for key, item in value.items()
            if key not in volatile_keys
        }
    if isinstance(value, list):
        return [_without_volatile(item, volatile_keys) for item in value]
    return value


def write_json_idempotent(
    path: Path,
    payload: dict[str, Any],
    *,
    volatile_keys: Iterable[str] = ("generated_at",),
) -> tuple[bool, dict[str, Any]]:
    """Write canonical UTF-8 JSON and avoid touching an unchanged output file."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    volatile = set(volatile_keys)
    if path.exists():
        try:
            existing = read_json(path)
        except (OSError, ValueError, TypeError):
            existing = None
        if isinstance(existing, dict):
            if _without_volatile(existing, volatile) == _without_volatile(payload, volatile):
                return False, existing
            for key in volatile:
                if key in existing and key in payload:
                    payload[key] = payload.get(key) or existing[key]

    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return True, payload


def write_text_idempotent(path: Path, text: str) -> bool:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n")
    if path.exists() and path.read_text(encoding="utf-8") == normalized:
        return False
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(normalized, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return True


def safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def truncate(value: Any, limit: int = 500) -> str:
    text = normalize_space(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))

