#!/usr/bin/env python3
"""Build or update the canonical fact ledger without overwriting human decisions."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from compare_artifacts import normalize_value, values_equivalent
from project_facts_common import read_json, stable_id, utc_now, write_json_idempotent


def is_confirmed(decision: Any) -> bool:
    return isinstance(decision, dict) and (
        decision.get("state") == "confirmed" or decision.get("human_confirmed") is True
    )


def canonical_matches_variant(fact: dict[str, Any], canonical_value: Any) -> bool:
    if canonical_value is None:
        return False
    if isinstance(canonical_value, (dict, list)):
        return any(variant.get("value") == canonical_value for variant in fact.get("variants", []))
    canonical = normalize_value(
        {
            "key": fact.get("key"),
            "value": canonical_value,
            "unit": fact.get("unit"),
            "value_type": fact.get("value_type"),
        }
    )
    for variant in fact.get("variants", []):
        variant_normalized = {
            "kind": "number" if isinstance(variant.get("normalized_value"), (int, float)) or str(variant.get("normalized_value", "")).replace(".", "", 1).replace("-", "", 1).isdigit() else "string",
            "comparison_key": str(variant.get("normalized_value")),
            "normalized_value": variant.get("normalized_value"),
            "resolution": "0",
            "canonical_value": variant.get("value"),
            "canonical_unit": variant.get("unit"),
            "value_type": variant.get("value_type"),
        }
        if canonical["kind"] == variant_normalized["kind"] and values_equivalent(canonical, variant_normalized):
            return True
        if variant.get("value") == canonical_value:
            return True
    return False


def merge_confirmed(existing: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(current)
    merged["canonical_value"] = copy.deepcopy(existing.get("canonical_value"))
    merged["decision"] = copy.deepcopy(existing.get("decision"))
    merged["notes"] = list(dict.fromkeys(list(merged.get("notes", [])) + ["Preserved a prior human-confirmed decision."]))
    if not canonical_matches_variant(merged, merged.get("canonical_value")):
        merged["status"] = "CONTRADICTED"
        merged["severity"] = "Critical" if merged.get("subtype") == "OWNERSHIP" else "High"
        merged["confidence"] = max(float(merged.get("confidence", 0)), 0.9)
        merged["notes"].append("Current source candidates do not match the preserved human-confirmed canonical value.")
        merged["repair"] = (
            "Do not overwrite the human decision. Recheck the changed sources and ask the decision owner whether to revise the ledger."
        )
    return merged


def build_ledger_data(
    comparison: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    project_name: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    root = project_root or comparison.get("root") or ""
    name = project_name or (Path(root).name if root else "Unnamed project")
    existing_by_id = {
        fact["fact_id"]: fact
        for fact in (existing or {}).get("facts", [])
        if isinstance(fact, dict) and isinstance(fact.get("fact_id"), str)
    }
    facts: list[dict[str, Any]] = []
    current_ids: set[str] = set()
    for comparison_fact in comparison.get("comparisons", []):
        current = copy.deepcopy(comparison_fact)
        current_ids.add(current["fact_id"])
        prior = existing_by_id.get(current["fact_id"])
        if prior and is_confirmed(prior.get("decision")):
            current = merge_confirmed(prior, current)
        elif prior and isinstance(prior.get("decision"), dict) and prior["decision"].get("history"):
            current["decision"]["history"] = copy.deepcopy(prior["decision"]["history"])
        facts.append(current)

    for fact_id, prior in existing_by_id.items():
        if fact_id in current_ids or not is_confirmed(prior.get("decision")):
            continue
        retained = copy.deepcopy(prior)
        retained["notes"] = list(
            dict.fromkeys(
                list(retained.get("notes", []))
                + ["This confirmed fact was not re-extracted in the current run; it was retained without inventing a replacement."]
            )
        )
        facts.append(retained)

    facts.sort(key=lambda fact: (fact.get("category", ""), fact.get("key", ""), fact["fact_id"]))
    return {
        "$schema": "urn:sync-project-facts:schema:project-facts:1.0",
        "schema_version": "1.0",
        "ledger_id": stable_id("ledger", name, root),
        "generated_at": utc_now(),
        "project": {
            "project_id": stable_id("project", name, root),
            "name": name,
            "root": root,
        },
        "source_manifest_id": comparison.get("manifest_id"),
        "evidence_set_id": comparison.get("evidence_id"),
        "materials": copy.deepcopy(comparison.get("materials", [])),
        "facts": facts,
        "warnings": copy.deepcopy(comparison.get("warnings", [])),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or update project-facts.json from comparison results.")
    parser.add_argument("--comparison", required=True, help="comparison.json produced by compare_artifacts.py.")
    parser.add_argument("--output", required=True, help="Output project-facts.json path.")
    parser.add_argument("--existing", help="Optional prior project-facts.json. Defaults to --output when it exists.")
    parser.add_argument("--project-name", help="Human-facing project name.")
    parser.add_argument("--project-root", help="Override the source project root recorded in the ledger.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = Path(args.output)
    existing_path = Path(args.existing) if args.existing else output
    try:
        comparison = read_json(Path(args.comparison))
        existing = read_json(existing_path) if existing_path.exists() else None
        payload = build_ledger_data(
            comparison,
            existing=existing,
            project_name=args.project_name,
            project_root=args.project_root,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    changed, final_payload = write_json_idempotent(output, payload)
    action = "Wrote" if changed else "Unchanged"
    confirmed = sum(1 for fact in final_payload["facts"] if is_confirmed(fact.get("decision")))
    print(f"{action}: {output.resolve()} ({len(final_payload['facts'])} facts, {confirmed} human-confirmed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
