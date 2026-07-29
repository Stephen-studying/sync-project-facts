#!/usr/bin/env python3
"""Manage local, inspectable user preferences and cross-session summaries."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from project_facts_common import normalize_space, normalize_token, read_json, stable_id, utc_now, write_json_idempotent


SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY_DIR = SKILL_ROOT / ".local-memory"
PROFILE_NAME = "user-memory.json"
HISTORY_NAME = "interaction-history.jsonl"

STYLE_GUIDANCE = {
    ("response_detail", "concise"): "Keep the answer concise and remove nonessential explanation.",
    ("response_detail", "detailed"): "Give a detailed explanation with enough context to be independently useful.",
    ("answer_format", "table_when_useful"): "Use a table when it makes comparisons or mappings easier to scan.",
    ("answer_format", "prose"): "Prefer cohesive prose and avoid unnecessary lists or tables.",
    ("answer_order", "outcome_first"): "Lead with the result or recommendation before implementation details.",
    ("language", "zh-CN"): "Respond in clear Simplified Chinese unless the user requests another language.",
    ("language", "en"): "Respond in English unless the user requests another language.",
    ("tone", "direct"): "Use a direct, task-shaped tone without generic praise or filler.",
    ("claim_policy", "conservative_ownership"): "Do not expand personal contribution beyond explicit evidence.",
}

EXPLICIT_FEEDBACK_PATTERNS = (
    (re.compile(r"(简洁|精简|短一点|再短|不要.{0,4}(啰嗦|展开))", re.I), "response_detail", "concise"),
    (re.compile(r"(详细|展开|解释清楚|多解释|说具体)", re.I), "response_detail", "detailed"),
    (re.compile(r"(用|做成|整理成).{0,4}表格", re.I), "answer_format", "table_when_useful"),
    (re.compile(r"(不要|少用).{0,4}(表格|列表|项目符号)", re.I), "answer_format", "prose"),
    (re.compile(r"(先给|先说|直接给).{0,4}(结论|结果|建议)", re.I), "answer_order", "outcome_first"),
    (re.compile(r"(用中文|中文回答|说中文)", re.I), "language", "zh-CN"),
    (re.compile(r"(用英文|英文回答|in english)", re.I), "language", "en"),
    (re.compile(r"(直接一点|直接回答|别客套|不要客套)", re.I), "tone", "direct"),
    (re.compile(r"(不要|不能|别).{0,8}(扩大|夸大).{0,8}(个人贡献|贡献|工作)", re.I), "claim_policy", "conservative_ownership"),
)


def default_profile() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "updated_at": utc_now(),
        "policy": {
            "local_only": True,
            "explicit_feedback_auto_save": True,
            "inferred_preferences_require_confirmation": True,
            "store_full_conversation": False,
            "max_history_entries": 200,
        },
        "preferences": {},
        "correction_rules": [],
        "pending_inferences": [],
    }


def memory_paths(memory_dir: Path) -> tuple[Path, Path]:
    root = memory_dir.expanduser().resolve()
    return root / PROFILE_NAME, root / HISTORY_NAME


def validate_profile(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if profile.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    policy = profile.get("policy")
    if not isinstance(policy, dict) or policy.get("local_only") is not True:
        errors.append("policy.local_only must be true")
    if not isinstance(policy, dict) or policy.get("inferred_preferences_require_confirmation") is not True:
        errors.append("inferred preferences must require confirmation")
    if not isinstance(policy, dict) or policy.get("store_full_conversation") is not False:
        errors.append("full conversation storage must remain disabled")
    if not isinstance(policy, dict) or not isinstance(policy.get("max_history_entries"), int):
        errors.append("policy.max_history_entries must be an integer")
    if not isinstance(profile.get("preferences"), dict):
        errors.append("preferences must be an object")
    if not isinstance(profile.get("correction_rules"), list):
        errors.append("correction_rules must be an array")
    if not isinstance(profile.get("pending_inferences"), list):
        errors.append("pending_inferences must be an array")
    return errors


def load_profile(memory_dir: Path, *, create: bool = True) -> tuple[dict[str, Any], Path]:
    profile_path, _ = memory_paths(memory_dir)
    if profile_path.exists():
        profile = read_json(profile_path)
        if not isinstance(profile, dict):
            raise ValueError(f"Invalid memory profile: {profile_path}")
    elif create:
        profile = default_profile()
        write_json_idempotent(profile_path, profile, volatile_keys=("updated_at",))
    else:
        profile = default_profile()
    errors = validate_profile(profile)
    if errors:
        raise ValueError("; ".join(errors))
    return profile, profile_path


def save_profile(profile: dict[str, Any], profile_path: Path) -> bool:
    profile["updated_at"] = utc_now()
    changed, _ = write_json_idempotent(profile_path, profile, volatile_keys=("updated_at",))
    return changed


def parse_value(value: str) -> Any:
    stripped = value.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped


def set_preference(
    profile: dict[str, Any],
    *,
    key: str,
    value: Any,
    source: str,
    evidence_summary: str,
    instruction: str | None = None,
) -> dict[str, Any]:
    normalized_key = normalize_token(key).replace("-", "_")
    if source not in {"explicit_feedback", "explicit_setting", "confirmed_inference"}:
        raise ValueError(f"Unsupported preference source: {source}")
    timestamp = utc_now()
    preference = {
        "key": normalized_key,
        "value": value,
        "source": source,
        "confidence": 1.0 if source != "confirmed_inference" else 0.95,
        "evidence_summary": normalize_space(evidence_summary),
        "updated_at": timestamp,
    }
    profile["preferences"][normalized_key] = preference

    guidance = instruction or STYLE_GUIDANCE.get((normalized_key, str(value)))
    profile["correction_rules"] = [
        item for item in profile["correction_rules"] if item.get("preference_key") != normalized_key
    ]
    if guidance:
        profile["correction_rules"].append(
            {
                "rule_id": stable_id("rule", normalized_key, value, guidance),
                "preference_key": normalized_key,
                "instruction": normalize_space(guidance),
                "source": source,
                "active": True,
                "created_at": timestamp,
            }
        )
    return preference


def infer_explicit_feedback(text: str) -> list[tuple[str, str]]:
    normalized = normalize_space(text)
    matches: list[tuple[str, str]] = []
    for pattern, key, value in EXPLICIT_FEEDBACK_PATTERNS:
        if pattern.search(normalized):
            matches.append((key, value))
    deduplicated: dict[str, str] = {}
    for key, value in matches:
        deduplicated[key] = value
    return list(deduplicated.items())


def pending_inference(profile: dict[str, Any], feedback: str) -> dict[str, Any]:
    item = {
        "pending_id": stable_id("pending", feedback),
        "feedback": normalize_space(feedback),
        "suggested_key": None,
        "suggested_value": None,
        "created_at": utc_now(),
    }
    if not any(existing.get("pending_id") == item["pending_id"] for existing in profile["pending_inferences"]):
        profile["pending_inferences"].append(item)
    return item


def read_history(history_path: Path) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with history_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid history JSONL at line {line_number}: {exc}") from exc
            if isinstance(value, dict):
                entries.append(value)
    return entries


def write_history(history_path: Path, entries: list[dict[str, Any]]) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = "".join(json.dumps(item, ensure_ascii=False, sort_keys=False) + "\n" for item in entries)
    temporary = history_path.with_name(history_path.name + ".tmp")
    temporary.write_text(rendered, encoding="utf-8", newline="\n")
    os.replace(temporary, history_path)


def append_history(memory_dir: Path, profile: dict[str, Any], entry: dict[str, Any]) -> bool:
    _, history_path = memory_paths(memory_dir)
    entries = read_history(history_path)
    if any(existing.get("event_id") == entry.get("event_id") for existing in entries):
        return False
    entries.append(entry)
    maximum = int(profile["policy"].get("max_history_entries", 200))
    write_history(history_path, entries[-maximum:])
    return True


def keywords(value: str) -> set[str]:
    normalized = normalize_space(value).casefold()
    latin = re.findall(r"[a-z0-9_.@-]{2,}", normalized)
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    chinese: set[str] = set(chinese_runs)
    for run in chinese_runs:
        for size in (2, 3, 4):
            chinese.update(run[index : index + size] for index in range(max(0, len(run) - size + 1)))
    return set(latin) | chinese


def recall_history(entries: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    query_words = keywords(query)
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, entry in enumerate(entries):
        text = " ".join(
            str(entry.get(key, ""))
            for key in ("question_summary", "answer_summary", "feedback", "project", "tags")
        )
        overlap = len(query_words & keywords(text)) if query_words else 0
        if query_words and overlap == 0:
            continue
        scored.append((overlap, index, entry))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[: max(1, limit)]]


def style_directives(profile: dict[str, Any]) -> list[str]:
    return [
        item["instruction"]
        for item in profile["correction_rules"]
        if item.get("active") and normalize_space(item.get("instruction"))
    ]


def command_init(args: argparse.Namespace) -> int:
    profile, profile_path = load_profile(args.memory_dir)
    _, history_path = memory_paths(args.memory_dir)
    print(
        json.dumps(
            {
                "profile_path": str(profile_path),
                "history_path": str(history_path),
                "preferences": len(profile["preferences"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_show(args: argparse.Namespace) -> int:
    profile, profile_path = load_profile(args.memory_dir)
    _, history_path = memory_paths(args.memory_dir)
    print(
        json.dumps(
            {
                "profile_path": str(profile_path),
                "history_path": str(history_path),
                "profile": profile,
                "history_entries": len(read_history(history_path)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_set(args: argparse.Namespace) -> int:
    profile, profile_path = load_profile(args.memory_dir)
    preference = set_preference(
        profile,
        key=args.key,
        value=parse_value(args.value),
        source="explicit_setting",
        evidence_summary=args.evidence or f"User explicitly set {args.key}.",
        instruction=args.instruction,
    )
    changed = save_profile(profile, profile_path)
    print(json.dumps({"changed": changed, "preference": preference}, ensure_ascii=False, indent=2))
    return 0


def command_learn(args: argparse.Namespace) -> int:
    profile, profile_path = load_profile(args.memory_dir)
    learned = []
    matches = infer_explicit_feedback(args.text)
    for key, value in matches:
        learned.append(
            set_preference(
                profile,
                key=key,
                value=value,
                source="explicit_feedback",
                evidence_summary=args.text,
            )
        )
    pending = None
    if not learned:
        pending = pending_inference(profile, args.text)
    changed = save_profile(profile, profile_path)
    append_history(
        args.memory_dir,
        profile,
        {
            "event_id": stable_id("feedback", args.text, args.context or ""),
            "timestamp": utc_now(),
            "kind": "feedback",
            "question_summary": "",
            "answer_summary": "",
            "feedback": normalize_space(args.text),
            "project": normalize_space(args.context or ""),
            "tags": ["feedback"],
        },
    )
    print(
        json.dumps(
            {
                "changed": changed,
                "learned": learned,
                "pending_inference": pending,
                "style_directives": style_directives(profile),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_confirm(args: argparse.Namespace) -> int:
    profile, profile_path = load_profile(args.memory_dir)
    pending = next(
        (item for item in profile["pending_inferences"] if item.get("pending_id") == args.pending_id),
        None,
    )
    if pending is None:
        print(f"ERROR: pending inference not found: {args.pending_id}")
        return 2
    preference = set_preference(
        profile,
        key=args.key,
        value=parse_value(args.value),
        source="confirmed_inference",
        evidence_summary=pending["feedback"],
        instruction=args.instruction,
    )
    profile["pending_inferences"] = [
        item for item in profile["pending_inferences"] if item.get("pending_id") != args.pending_id
    ]
    save_profile(profile, profile_path)
    print(json.dumps({"confirmed": preference}, ensure_ascii=False, indent=2))
    return 0


def command_record(args: argparse.Namespace) -> int:
    profile, _ = load_profile(args.memory_dir)
    entry = {
        "event_id": stable_id(
            "interaction",
            args.question,
            args.answer_summary,
            args.feedback or "",
            args.project or "",
        ),
        "timestamp": utc_now(),
        "kind": "interaction",
        "question_summary": normalize_space(args.question),
        "answer_summary": normalize_space(args.answer_summary),
        "feedback": normalize_space(args.feedback or ""),
        "project": normalize_space(args.project or ""),
        "tags": [normalize_space(item) for item in (args.tags or []) if normalize_space(item)],
    }
    changed = append_history(args.memory_dir, profile, entry)
    print(json.dumps({"changed": changed, "entry": entry}, ensure_ascii=False, indent=2))
    return 0


def command_recall(args: argparse.Namespace) -> int:
    profile, profile_path = load_profile(args.memory_dir)
    _, history_path = memory_paths(args.memory_dir)
    remembered = recall_history(read_history(history_path), args.query, args.limit)
    print(
        json.dumps(
            {
                "profile_path": str(profile_path),
                "preferences": profile["preferences"],
                "style_directives": style_directives(profile),
                "remembered_interactions": remembered,
                "pending_inferences": profile["pending_inferences"],
                "current_request_overrides_memory": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_forget(args: argparse.Namespace) -> int:
    profile, profile_path = load_profile(args.memory_dir)
    key = normalize_token(args.key).replace("-", "_")
    removed = profile["preferences"].pop(key, None)
    profile["correction_rules"] = [
        item for item in profile["correction_rules"] if item.get("preference_key") != key
    ]
    changed = save_profile(profile, profile_path) if removed is not None else False
    print(json.dumps({"changed": changed, "forgotten": key}, ensure_ascii=False, indent=2))
    return 0


def command_reset(args: argparse.Namespace) -> int:
    if args.confirm != "RESET":
        print("ERROR: use --confirm RESET to remove the local memory profile and history.")
        return 2
    profile_path, history_path = memory_paths(args.memory_dir)
    removed = []
    for path in (profile_path, history_path):
        if path.exists():
            path.unlink()
            removed.append(str(path))
    print(json.dumps({"removed": removed}, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage local user preferences for sync-project-facts.")
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=DEFAULT_MEMORY_DIR,
        help="Local memory directory; defaults to <skill-root>/.local-memory.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create the local profile if it does not exist.")
    subparsers.add_parser("show", help="Inspect preferences, policy, and history count.")

    set_parser = subparsers.add_parser("set-preference", help="Save an explicit user preference.")
    set_parser.add_argument("--key", required=True)
    set_parser.add_argument("--value", required=True)
    set_parser.add_argument("--evidence")
    set_parser.add_argument("--instruction")

    learn_parser = subparsers.add_parser("learn-feedback", help="Learn safe style preferences from explicit feedback.")
    learn_parser.add_argument("--text", required=True)
    learn_parser.add_argument("--context")

    confirm_parser = subparsers.add_parser("confirm-pending", help="Confirm one previously inferred preference.")
    confirm_parser.add_argument("--pending-id", required=True)
    confirm_parser.add_argument("--key", required=True)
    confirm_parser.add_argument("--value", required=True)
    confirm_parser.add_argument("--instruction")

    record_parser = subparsers.add_parser("record-interaction", help="Store a cross-session question and answer summary.")
    record_parser.add_argument("--question", required=True)
    record_parser.add_argument("--answer-summary", required=True)
    record_parser.add_argument("--feedback")
    record_parser.add_argument("--project")
    record_parser.add_argument("--tags", nargs="*")

    recall_parser = subparsers.add_parser("recall", help="Load preferences and relevant prior interaction summaries.")
    recall_parser.add_argument("--query", default="")
    recall_parser.add_argument("--limit", type=int, default=5)

    forget_parser = subparsers.add_parser("forget", help="Remove one stored preference and its correction rule.")
    forget_parser.add_argument("--key", required=True)

    reset_parser = subparsers.add_parser("reset", help="Remove the complete local profile and interaction history.")
    reset_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    commands = {
        "init": command_init,
        "show": command_show,
        "set-preference": command_set,
        "learn-feedback": command_learn,
        "confirm-pending": command_confirm,
        "record-interaction": command_record,
        "recall": command_recall,
        "forget": command_forget,
        "reset": command_reset,
    }
    try:
        return commands[args.command](args)
    except (OSError, ValueError, TypeError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
