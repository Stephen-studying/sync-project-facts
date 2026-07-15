#!/usr/bin/env python3
"""Normalize candidates and classify cross-artifact fact relationships."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from project_facts_common import (
    iso_mtime,
    normalize_space,
    normalize_token,
    read_json,
    sha256_file,
    stable_id,
    utc_now,
    write_json_idempotent,
)


RATIO_METRICS = {
    "map_0_5",
    "map_0_5_0_95",
    "precision",
    "recall",
    "accuracy",
    "f1_score",
    "performance_improvement",
}
PROOF_KINDS = {"experiment_result", "config_value", "source_code", "result_table", "raw_log", "calculation"}
STRONG_AUTHORITIES = {"raw", "primary", "formal"}
SCOPE_KEYS = (
    "dataset",
    "data_split",
    "modality",
    "model_version",
    "metric_definition",
    "iou_threshold",
    "confidence_threshold",
    "experiment_date",
    "training_conditions",
    "test_conditions",
)


def decimal_from(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = normalize_space(value).replace(",", "")
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*%?", text)
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def decimal_places(value: Any) -> int:
    text = normalize_space(value).replace(",", "").rstrip("%")
    if "." not in text:
        return 0
    return len(text.rsplit(".", 1)[1])


def decimal_string(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def normalize_metric_definition(value: Any) -> str:
    token = normalize_space(value).casefold().replace(" ", "")
    token = token.replace("map50-95", "map@0.5:0.95").replace("map50:95", "map@0.5:0.95")
    token = token.replace("map50", "map@0.5")
    return token


def normalize_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(scope, dict):
        return {}
    aliases = {
        "split": "data_split",
        "data-split": "data_split",
        "model-version": "model_version",
        "metric-definition": "metric_definition",
        "iou-threshold": "iou_threshold",
        "confidence-threshold": "confidence_threshold",
        "experiment-date": "experiment_date",
        "training-conditions": "training_conditions",
        "test-conditions": "test_conditions",
    }
    normalized: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for raw_key, raw_value in scope.items():
        if raw_value in (None, "", [], {}):
            continue
        key = aliases.get(normalize_token(raw_key), normalize_token(raw_key).replace("-", "_"))
        if isinstance(raw_value, list):
            value: Any = sorted({normalize_space(item) for item in raw_value}, key=str.casefold)
        elif isinstance(raw_value, (int, float, bool)):
            value = raw_value
        else:
            value = normalize_space(raw_value)
            if key == "modality":
                modality = {"infrared": "IR", "红外": "IR", "visible": "RGB", "可见光": "RGB", "电致发光": "EL"}
                value = modality.get(value.casefold(), value.upper())
            elif key == "metric_definition":
                value = normalize_metric_definition(value)
        if key in SCOPE_KEYS:
            normalized[key] = value
        elif key != "extra":
            extra[key] = value
        elif isinstance(raw_value, dict):
            extra.update(raw_value)
    if extra:
        normalized["extra"] = extra
    return normalized


def unit_token(unit: Any) -> str:
    if unit is None:
        return ""
    return normalize_space(unit).casefold().replace(" ", "")


def normalize_value(candidate: dict[str, Any]) -> dict[str, Any]:
    value = candidate.get("value")
    key = candidate.get("key", "")
    unit = unit_token(candidate.get("unit"))
    number = decimal_from(value)
    places = decimal_places(value)
    if number is not None:
        scale = Decimal("1")
        canonical_unit = candidate.get("unit")
        value_type = candidate.get("value_type", "number")
        if unit in {"%", "percent", "percentage", "percentagepoints", "pp"}:
            scale = Decimal("0.01")
            canonical_unit = "%"
            value_type = "percentage"
        elif key in RATIO_METRICS:
            if abs(number) > 1:
                scale = Decimal("0.01")
            canonical_unit = "%"
            value_type = "percentage"
        elif unit in {"ms", "millisecond", "milliseconds"}:
            scale = Decimal("0.001")
            canonical_unit = "s"
        elif unit in {"min", "minute", "minutes"}:
            scale = Decimal("60")
            canonical_unit = "s"
        elif unit in {"kb", "kib"}:
            scale = Decimal("1024")
            canonical_unit = "bytes"
        elif unit in {"mb", "mib"}:
            scale = Decimal(1024**2)
            canonical_unit = "bytes"
        elif unit in {"gb", "gib"}:
            scale = Decimal(1024**3)
            canonical_unit = "bytes"
        elif key == "parameters" and unit in {"k", "thousand"}:
            scale = Decimal(1000)
            canonical_unit = "parameters"
        elif key == "parameters" and unit in {"m", "million"}:
            scale = Decimal(1000000)
            canonical_unit = "parameters"
        elif key == "parameters" and unit in {"b", "billion"}:
            scale = Decimal(1000000000)
            canonical_unit = "parameters"
        elif key == "flops" and unit == "mflops":
            scale = Decimal(1000000)
            canonical_unit = "FLOPs"
        elif key == "flops" and unit == "gflops":
            scale = Decimal(1000000000)
            canonical_unit = "FLOPs"
        normalized = number * scale
        resolution = (Decimal(10) ** Decimal(-places)) * abs(scale)
        display: Any
        if value_type == "percentage":
            display_decimal = normalized * Decimal(100)
            display = float(display_decimal) if display_decimal != display_decimal.to_integral() else int(display_decimal)
        else:
            display = float(normalized) if normalized != normalized.to_integral() else int(normalized)
        return {
            "kind": "number",
            "comparison_key": decimal_string(normalized),
            "normalized_value": decimal_string(normalized),
            "resolution": decimal_string(resolution),
            "canonical_value": display,
            "canonical_unit": canonical_unit,
            "value_type": value_type,
        }
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "kind": "json",
            "comparison_key": rendered,
            "normalized_value": value,
            "resolution": None,
            "canonical_value": value,
            "canonical_unit": candidate.get("unit"),
            "value_type": "object" if isinstance(value, dict) else "list",
        }
    token = normalize_token(value)
    return {
        "kind": "string",
        "comparison_key": token,
        "normalized_value": token,
        "resolution": None,
        "canonical_value": value,
        "canonical_unit": candidate.get("unit"),
        "value_type": candidate.get("value_type", "string"),
    }


def values_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["kind"] != right["kind"]:
        return False
    if left["kind"] != "number":
        return left["comparison_key"] == right["comparison_key"]
    left_value = Decimal(left["comparison_key"])
    right_value = Decimal(right["comparison_key"])
    if left_value == right_value:
        return True
    left_resolution = Decimal(left["resolution"] or "0")
    right_resolution = Decimal(right["resolution"] or "0")
    tolerance = max(left_resolution, right_resolution) / Decimal(2)
    return tolerance > 0 and abs(left_value - right_value) <= tolerance


def aliases_for(candidate: dict[str, Any]) -> set[str]:
    values = {normalize_token(candidate.get("value"))}
    values.update(normalize_token(alias) for alias in candidate.get("aliases", []))
    return {value for value in values if value}


def all_values_equivalent(candidates: list[dict[str, Any]]) -> bool:
    normalized = [normalize_value(candidate) for candidate in candidates]
    first = normalized[0]
    for index, value in enumerate(normalized[1:], start=1):
        if values_equivalent(first, value):
            continue
        if aliases_for(candidates[0]) & aliases_for(candidates[index]):
            continue
        return False
    return True


def raw_representations(candidates: list[dict[str, Any]]) -> set[str]:
    return {
        json.dumps(
            {"value": candidate.get("value"), "unit": candidate.get("unit")},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for candidate in candidates
    }


def normalized_scope_signature(candidate: dict[str, Any]) -> str:
    return json.dumps(normalize_scope(candidate.get("scope")), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def differing_scope_dimensions(candidates: list[dict[str, Any]]) -> list[str]:
    dimensions = []
    scopes = [normalize_scope(candidate.get("scope")) for candidate in candidates]
    for key in SCOPE_KEYS:
        explicit = {
            json.dumps(scope[key], ensure_ascii=False, sort_keys=True)
            for scope in scopes
            if key in scope and scope[key] not in (None, "")
        }
        if len(explicit) > 1:
            dimensions.append(key)
    return dimensions


def common_scope(candidates: list[dict[str, Any]], differing: list[str]) -> dict[str, Any]:
    scopes = [normalize_scope(candidate.get("scope")) for candidate in candidates]
    result: dict[str, Any] = {}
    for key in SCOPE_KEYS:
        values = [scope.get(key) for scope in scopes if key in scope]
        if values and len(values) == len(scopes) and all(value == values[0] for value in values):
            result[key] = values[0]
    if differing:
        result["extra"] = {"differing_dimensions": differing}
    return result


def source_sort(candidate: dict[str, Any]) -> tuple[str, str, str]:
    evidence = candidate["evidence"]
    return evidence["source_path"].casefold(), evidence["locator"], candidate["candidate_id"]


def make_variants(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    buckets: dict[str, dict[str, Any]] = {}
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for candidate in sorted(candidates, key=source_sort):
        normalized = normalize_value(candidate)
        signature = json.dumps(
            {
                "value": candidate.get("value"),
                "unit": candidate.get("unit"),
                "scope": normalize_scope(candidate.get("scope")),
                "lifecycle": candidate.get("lifecycle", "unspecified"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence = candidate["evidence"]
        evidence_by_id[evidence["evidence_id"]] = evidence
        if signature not in buckets:
            buckets[signature] = {
                "variant_id": stable_id("variant", candidate["category"], candidate["key"], signature),
                "value": candidate.get("value"),
                "normalized_value": normalized["normalized_value"],
                "value_type": normalized["value_type"],
                "unit": normalized["canonical_unit"],
                "scope": normalize_scope(candidate.get("scope")),
                "lifecycle": candidate.get("lifecycle", "unspecified"),
                "source_authority": candidate.get("source_authority", "summary"),
                "evidence_ids": [],
            }
        buckets[signature]["evidence_ids"].append(evidence["evidence_id"])
    variants = list(buckets.values())
    for variant in variants:
        variant["evidence_ids"] = sorted(set(variant["evidence_ids"]))
    variants.sort(key=lambda item: item["variant_id"])
    evidence = [evidence_by_id[key] for key in sorted(evidence_by_id)]
    return variants, evidence


def canonical_scoped(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for candidate in sorted(candidates, key=source_sort):
        normalized = normalize_value(candidate)
        item = {
            "value": normalized["canonical_value"],
            "unit": normalized["canonical_unit"],
            "scope": normalize_scope(candidate.get("scope")),
        }
        signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if signature not in seen:
            seen.add(signature)
            result.append(item)
    return result


def repair_for(status: str, subtype: str | None = None) -> str:
    if subtype == "OWNERSHIP":
        return "Stop using the broader ownership claim; obtain an explicit team/user confirmation before synchronizing contribution wording."
    return {
        "CONSISTENT": "No repair required; preserve the locator-backed wording.",
        "EQUIVALENT": "Choose one display convention and record the conversion or rounding rule.",
        "SCOPED_DIFFERENCE": "Keep values separate and label dataset, modality, metric definition, thresholds, version, and conditions.",
        "STALE": "Replace the explicitly superseded name/value in stale materials and retain the confirmed-current source.",
        "CONTRADICTED": "Do not choose a value automatically; trace the originating result/configuration and obtain confirmation.",
        "UNSUPPORTED": "Remove or qualify the claim until a result table, log, calculation, configuration, or explicit record is linked.",
        "MISSING": "Add the confirmed fact to the explicitly named target material without changing its scope.",
        "UNRESOLVED": "Keep every candidate value and ask the user to adjudicate against primary experiment evidence.",
    }[status]


def compare_group(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = sorted(candidates, key=source_sort)
    category = candidates[0]["category"]
    key = candidates[0]["key"]
    differing = differing_scope_dimensions(candidates)
    scope_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        scope_groups[normalized_scope_signature(candidate)].append(candidate)
    within_scope_conflict = any(len(group) > 1 and not all_values_equivalent(group) for group in scope_groups.values())
    lifecycles = {candidate.get("lifecycle", "unspecified") for candidate in candidates}
    current = [candidate for candidate in candidates if candidate.get("lifecycle") == "current"]
    old = [candidate for candidate in candidates if candidate.get("lifecycle") == "old"]
    subtype = "OWNERSHIP" if category == "contribution" and any(c.get("subtype") == "OWNERSHIP" for c in candidates) else None
    requires_support = any(candidate.get("requires_support") for candidate in candidates)
    supported = any(candidate["evidence"].get("evidence_kind") in PROOF_KINDS for candidate in candidates)
    notes: list[str] = []

    if subtype == "OWNERSHIP" and len({candidate.get("ownership_level") for candidate in candidates if candidate.get("ownership_level")}) > 1:
        status = "CONTRADICTED"
        severity = "Critical" if {candidate.get("ownership_level") for candidate in candidates} >= {"sole", "partial"} else "High"
        canonical_value = None
        decision_state = "pending"
        notes.append("Ownership cannot be inferred from file authorship, code authorship, or stronger wording.")
    elif current and old and all_values_equivalent(current) and not all_values_equivalent(current + old):
        status = "STALE"
        severity = "High" if category in {"metric", "contribution", "deployment", "testing"} else "Medium"
        current_normalized = normalize_value(current[0])
        canonical_value = current_normalized["canonical_value"]
        decision_state = "rule_based"
        notes.append("The current/old relation is explicit in the sources; modification time was not used.")
    elif requires_support and not supported:
        status = "UNSUPPORTED"
        severity = "High" if category in {"metric", "deployment", "testing", "outcome_status", "contribution"} else "Medium"
        canonical_value = None
        decision_state = "pending"
        notes.append("Only claim-level evidence was found for a fact that requires traceable support.")
    elif differing and not within_scope_conflict:
        status = "SCOPED_DIFFERENCE"
        severity = "Low"
        canonical_value = canonical_scoped(candidates)
        decision_state = "not_required"
        notes.append("Different explicit scope dimensions prevent a hard-conflict classification: " + ", ".join(differing) + ".")
    elif all_values_equivalent(candidates):
        normalized = normalize_value(candidates[0])
        canonical_value = normalized["canonical_value"]
        if len(raw_representations(candidates)) > 1:
            status = "EQUIVALENT"
            severity = "Low"
            notes.append("Values are equal after conservative unit, percentage, alias, or rounding normalization.")
        else:
            status = "CONSISTENT"
            severity = "Low"
            if len({candidate["evidence"]["source_path"] for candidate in candidates}) == 1:
                notes.append("This is a single-source candidate; no cross-material contradiction was observed.")
        decision_state = "not_required"
    else:
        all_strong = all(candidate.get("source_authority") in STRONG_AUTHORITIES for candidate in candidates)
        if all_strong:
            status = "UNRESOLVED"
            notes.append("Incompatible primary/formal candidates were retained because authority alone cannot settle the hard conflict.")
        else:
            status = "CONTRADICTED"
            notes.append("Values are incompatible within the same explicit scope; no value was selected.")
        severity = "High" if category in {"metric", "contribution", "deployment", "testing", "outcome_status"} else "Medium"
        canonical_value = None
        decision_state = "pending"

    variants, evidence = make_variants(candidates)
    normalized_first = normalize_value(candidates[0])
    aliases = sorted({alias for candidate in candidates for alias in candidate.get("aliases", [])}, key=str.casefold)
    decision = {
        "state": decision_state,
        "human_confirmed": False,
        "selected_variant_id": None,
        "decided_by": "comparison_rule" if decision_state == "rule_based" else None,
        "decided_at": None,
        "rationale": notes[0] if decision_state == "rule_based" and notes else None,
        "history": [],
    }
    return {
        "fact_id": stable_id("fact", category, key),
        "category": category,
        "key": key,
        "canonical_value": canonical_value,
        "value_type": normalized_first["value_type"],
        "unit": normalized_first["canonical_unit"],
        "scope": common_scope(candidates, differing),
        "aliases": aliases,
        "status": status,
        "severity": severity,
        "confidence": {
            "CONSISTENT": 0.95 if len(evidence) > 1 else 0.6,
            "EQUIVALENT": 0.9,
            "SCOPED_DIFFERENCE": 0.9,
            "STALE": 0.85,
            "CONTRADICTED": 0.95,
            "UNSUPPORTED": 0.7,
            "UNRESOLVED": 0.8,
        }[status],
        "evidence": evidence,
        "variants": variants,
        "decision": decision,
        "notes": notes,
        "subtype": subtype,
        "repair": repair_for(status, subtype),
    }


def compare_candidates(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id") or stable_id("candidate", candidate)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        category = normalize_token(candidate.get("category", "other")).replace("-", "_")
        key = normalize_token(candidate.get("key", "fact")).replace("-", "_")
        candidate = dict(candidate)
        candidate["candidate_id"] = candidate_id
        candidate["category"] = category
        candidate["key"] = key
        groups[(category, key)].append(candidate)
    comparisons = [compare_group(group) for _, group in sorted(groups.items())]
    comparisons.sort(key=lambda item: (item["category"], item["key"], item["fact_id"]))
    return comparisons


def add_missing_requirements(
    comparisons: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    requirements_path: Path,
) -> list[dict[str, Any]]:
    data = read_json(requirements_path)
    requirements = data.get("requirements", []) if isinstance(data, dict) else []
    requirement_hash = sha256_file(requirements_path)
    by_key = {(item["category"], item["key"]): item for item in comparisons}
    result = list(comparisons)
    for index, requirement in enumerate(requirements):
        if not isinstance(requirement, dict) or not all(key in requirement for key in ("category", "key", "target_path")):
            continue
        category = normalize_token(requirement["category"]).replace("-", "_")
        key = normalize_token(requirement["key"]).replace("-", "_")
        target = str(requirement["target_path"]).replace("\\", "/").casefold()
        present = any(
            candidate["category"] == category
            and candidate["key"] == key
            and candidate["evidence"]["source_path"].replace("\\", "/").casefold() == target
            for candidate in candidates
        )
        if present:
            continue
        base = by_key.get((category, key))
        locator = f"$.requirements[{index}]"
        evidence_id = stable_id("evidence", requirement_hash, locator, target, key)
        requirement_evidence = {
            "evidence_id": evidence_id,
            "source_path": requirements_path.name,
            "source_type": "json",
            "source_hash": requirement_hash,
            "locator": locator,
            "excerpt": json.dumps(requirement, ensure_ascii=False, sort_keys=True),
            "extracted_value": target,
            "modified_at": iso_mtime(requirements_path),
            "evidence_kind": "sync_requirement",
        }
        evidence = list(base["evidence"] if base else []) + [requirement_evidence]
        canonical = requirement.get("expected_value", base.get("canonical_value") if base else None)
        variants = list(base["variants"] if base else [])
        if not variants:
            variants = [
                {
                    "variant_id": stable_id("variant", category, key, "missing", target),
                    "value": canonical,
                    "normalized_value": canonical,
                    "value_type": requirement.get("value_type", "string"),
                    "unit": requirement.get("unit"),
                    "scope": normalize_scope(requirement.get("scope", {})),
                    "lifecycle": "unspecified",
                    "source_authority": "formal",
                    "evidence_ids": [evidence_id],
                }
            ]
        result.append(
            {
                "fact_id": stable_id("fact", category, key, "missing", target),
                "category": category,
                "key": key,
                "canonical_value": canonical,
                "value_type": requirement.get("value_type", base.get("value_type") if base else "string"),
                "unit": requirement.get("unit", base.get("unit") if base else None),
                "scope": normalize_scope(requirement.get("scope", base.get("scope") if base else {})),
                "aliases": base.get("aliases", []) if base else [],
                "status": "MISSING",
                "severity": requirement.get("severity", "High"),
                "confidence": 0.9,
                "evidence": evidence,
                "variants": variants,
                "decision": {
                    "state": "pending",
                    "human_confirmed": False,
                    "selected_variant_id": None,
                    "decided_by": None,
                    "decided_at": None,
                    "rationale": None,
                    "history": [],
                },
                "notes": [f"This absence is MISSING only because {target} was explicitly named as a sync target."],
                "subtype": None,
                "repair": repair_for("MISSING"),
                "target_material": requirement["target_path"],
            }
        )
    return sorted(result, key=lambda item: (item["category"], item["key"], item["fact_id"]))


def compare_evidence(evidence_data: dict[str, Any], requirements: Path | None = None) -> dict[str, Any]:
    candidates = list(evidence_data.get("candidates", []))
    comparisons = compare_candidates(candidates)
    if requirements:
        comparisons = add_missing_requirements(comparisons, candidates, requirements)
    return {
        "comparison_version": "1.0",
        "comparison_id": stable_id(
            "comparison", evidence_data.get("evidence_id"), [(item["fact_id"], item["status"]) for item in comparisons]
        ),
        "generated_at": utc_now(),
        "root": evidence_data.get("root"),
        "manifest_id": evidence_data.get("manifest_id"),
        "evidence_id": evidence_data.get("evidence_id"),
        "materials": evidence_data.get("materials", []),
        "comparisons": comparisons,
        "warnings": evidence_data.get("warnings", []),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare normalized fact candidates across project artifacts.")
    parser.add_argument("--evidence", required=True, help="evidence.json produced by extract_evidence.py.")
    parser.add_argument("--output", required=True, help="Output comparison.json path.")
    parser.add_argument(
        "--requirements",
        help="Optional JSON declaring facts that must appear in explicitly named target materials; only this enables MISSING.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        evidence_data = read_json(Path(args.evidence))
        payload = compare_evidence(evidence_data, Path(args.requirements) if args.requirements else None)
    except (OSError, ValueError, TypeError, InvalidOperation) as exc:
        print(f"ERROR: {exc}")
        return 1
    changed, final_payload = write_json_idempotent(Path(args.output), payload)
    action = "Wrote" if changed else "Unchanged"
    counts: dict[str, int] = defaultdict(int)
    for item in final_payload["comparisons"]:
        counts[item["status"]] += 1
    rendered = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
    print(f"{action}: {Path(args.output).resolve()} ({rendered or 'no facts'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
