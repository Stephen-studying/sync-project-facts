#!/usr/bin/env python3
"""Validate schema conformance, evidence integrity, and decision preservation."""

from __future__ import annotations

import argparse
import importlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from project_facts_common import SEVERITY_RANK, STATUS_VALUES, read_json, sha256_file, write_json_idempotent


CONFLICT_STATUSES = {"SCOPED_DIFFERENCE", "STALE", "CONTRADICTED", "UNRESOLVED"}
LOCATOR_PATTERNS = {
    "markdown": re.compile(r"\bline\s+\d+\b", re.I),
    "text": re.compile(r"\bline\s+\d+\b", re.I),
    "code": re.compile(r"\bline\s+\d+\b", re.I),
    "config": re.compile(r"\bline\s+\d+\b", re.I),
    "pdf": re.compile(r"\bpage\s+\d+\b", re.I),
    "docx": re.compile(r"\b(?:paragraph|table|heading)\b", re.I),
    "pptx": re.compile(r"\bslide\s+\d+\b.*\btext\s+box\s+\d+\b", re.I),
    "xlsx": re.compile(r"\bsheet\s+.+,\s*cell\s+[A-Z]+\d+\b", re.I),
    "csv": re.compile(r"\brow\s+\d+\b.*\bcolumn\b", re.I),
    "json": re.compile(r"^\$"),
    "yaml": re.compile(r"^\$"),
}


@dataclass
class Finding:
    level: str
    code: str
    message: str
    fact_id: str | None = None


def _json_type_matches(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, True)


def _resolve_local_ref(root: dict[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ValueError(f"Bundled validator accepts local JSON Pointers only: {reference}")
    value: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        value = value[part]
    return value


def _schema_errors(value: Any, schema: Any, root: dict[str, Any], path: str = "$") -> list[str]:
    if schema is True:
        return []
    if schema is False:
        return [f"{path}: value is disallowed by the schema"]
    if not isinstance(schema, dict):
        return [f"{path}: invalid schema node"]
    if "$ref" in schema:
        try:
            resolved = _resolve_local_ref(root, schema["$ref"])
        except (KeyError, TypeError, ValueError) as exc:
            return [f"{path}: invalid $ref {schema['$ref']!r}: {exc}"]
        return _schema_errors(value, resolved, root, path)
    if "oneOf" in schema:
        branch_results = [_schema_errors(value, branch, root, path) for branch in schema["oneOf"]]
        matched = sum(1 for result in branch_results if not result)
        if matched != 1:
            return [f"{path}: expected exactly one oneOf branch to match, got {matched}"]
    if "const" in schema and value != schema["const"]:
        return [f"{path}: expected constant {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} is not in the allowed enum"]
    expected_types = schema.get("type")
    if expected_types:
        if isinstance(expected_types, str):
            expected_types = [expected_types]
        if not any(_json_type_matches(value, item) for item in expected_types):
            return [f"{path}: expected type {expected_types}, got {type(value).__name__}"]

    errors: list[str] = []
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in properties:
                errors.extend(_schema_errors(child, properties[key], root, child_path))
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                errors.append(f"{child_path}: additional property is not allowed")
            elif isinstance(additional, (dict, bool)):
                errors.extend(_schema_errors(child, additional, root, child_path))
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: expected at most {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            rendered = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(rendered) != len(set(rendered)):
                errors.append(f"{path}: array items are not unique")
        if "items" in schema:
            for index, child in enumerate(value):
                errors.extend(_schema_errors(child, schema["items"], root, f"{path}[{index}]"))
    elif isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: string does not match {schema['pattern']!r}")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: value is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: value is above maximum {schema['maximum']}")
    return errors


def schema_findings(ledger: dict[str, Any], schema: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    try:
        jsonschema = importlib.import_module("jsonschema")
    except ModuleNotFoundError:
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" or not isinstance(schema.get("$defs"), dict):
            findings.append(Finding("ERROR", "SCHEMA_ENGINE", "Schema is not a supported self-contained Draft 2020-12 document."))
            return findings
        for message in _schema_errors(ledger, schema, schema):
            findings.append(Finding("ERROR", "JSON_SCHEMA", message))
        return findings
    try:
        validator_class = jsonschema.Draft202012Validator
        validator_class.check_schema(schema)
        validator = validator_class(schema)
        for error in sorted(validator.iter_errors(ledger), key=lambda item: list(item.absolute_path)):
            path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.absolute_path)
            findings.append(Finding("ERROR", "JSON_SCHEMA", f"{path}: {error.message}"))
    except Exception as exc:
        findings.append(Finding("ERROR", "SCHEMA_ENGINE", f"Could not validate the JSON Schema: {exc}"))
    return findings


def locator_is_precise(evidence: dict[str, Any]) -> bool:
    pattern = LOCATOR_PATTERNS.get(evidence.get("source_type"))
    return bool(pattern and pattern.search(str(evidence.get("locator", ""))))


def semantic_findings(ledger: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    facts = ledger.get("facts", [])
    fact_ids: set[str] = set()
    material_hashes = {material.get("source_hash") for material in ledger.get("materials", [])}
    for fact in facts:
        fact_id = fact.get("fact_id")
        if fact_id in fact_ids:
            findings.append(Finding("ERROR", "DUPLICATE_FACT_ID", f"Duplicate fact_id: {fact_id}", fact_id))
        fact_ids.add(fact_id)
        status = fact.get("status")
        if status not in STATUS_VALUES:
            findings.append(Finding("ERROR", "STATUS", f"Unsupported status: {status}", fact_id))
        evidence = fact.get("evidence") or []
        variants = fact.get("variants") or []
        if not evidence:
            findings.append(Finding("ERROR", "NO_EVIDENCE", "Every fact judgment must have evidence.", fact_id))
        if not variants:
            findings.append(Finding("ERROR", "NO_VARIANTS", "Every fact must retain at least one candidate variant.", fact_id))
        evidence_ids: set[str] = set()
        for item in evidence:
            evidence_id = item.get("evidence_id")
            if evidence_id in evidence_ids:
                findings.append(Finding("ERROR", "DUPLICATE_EVIDENCE", f"Duplicate evidence_id: {evidence_id}", fact_id))
            evidence_ids.add(evidence_id)
            if not locator_is_precise(item):
                findings.append(
                    Finding(
                        "ERROR",
                        "IMPRECISE_LOCATOR",
                        f"{item.get('source_path')} has an imprecise {item.get('source_type')} locator: {item.get('locator')}",
                        fact_id,
                    )
                )
            if item.get("evidence_kind") != "sync_requirement" and item.get("source_hash") not in material_hashes:
                findings.append(
                    Finding(
                        "ERROR",
                        "UNKNOWN_SOURCE_HASH",
                        f"Evidence hash is not present in the material inventory: {item.get('source_path')}",
                        fact_id,
                    )
                )
        referenced: set[str] = set()
        for variant in variants:
            refs = variant.get("evidence_ids") or []
            if not refs:
                findings.append(Finding("ERROR", "VARIANT_NO_EVIDENCE", "Variant has no evidence_ids.", fact_id))
            unknown = set(refs) - evidence_ids
            if unknown:
                findings.append(
                    Finding("ERROR", "VARIANT_UNKNOWN_EVIDENCE", f"Variant references unknown evidence IDs: {sorted(unknown)}", fact_id)
                )
            referenced.update(refs)
        candidate_evidence = {
            item["evidence_id"] for item in evidence if item.get("evidence_kind") != "sync_requirement"
        }
        if candidate_evidence - referenced:
            findings.append(
                Finding(
                    "ERROR",
                    "DROPPED_CANDIDATE_SOURCE",
                    f"Candidate evidence was not retained by any variant: {sorted(candidate_evidence - referenced)}",
                    fact_id,
                )
            )
        if status in CONFLICT_STATUSES and len(variants) < 2:
            findings.append(Finding("ERROR", "CONFLICT_VARIANTS", f"{status} must retain at least two variants.", fact_id))
        if status == "MISSING" and not any(item.get("evidence_kind") == "sync_requirement" for item in evidence):
            findings.append(
                Finding("ERROR", "MISSING_WITHOUT_REQUIREMENT", "MISSING requires an explicit sync_requirement locator.", fact_id)
            )
        if status == "UNSUPPORTED" and any(item.get("evidence_kind") in {"experiment_result", "result_table", "raw_log", "calculation"} for item in evidence):
            findings.append(
                Finding("ERROR", "UNSUPPORTED_HAS_PROOF", "UNSUPPORTED contains proof-grade evidence and should be reclassified.", fact_id)
            )
        if fact.get("subtype") == "OWNERSHIP" and SEVERITY_RANK.get(fact.get("severity"), -1) < SEVERITY_RANK["High"]:
            findings.append(Finding("ERROR", "OWNERSHIP_SEVERITY", "OWNERSHIP severity must be High or Critical.", fact_id))
        decision = fact.get("decision") or {}
        if decision.get("human_confirmed") and decision.get("state") != "confirmed":
            findings.append(
                Finding("ERROR", "DECISION_STATE", "human_confirmed=true requires decision.state=confirmed.", fact_id)
            )
        selected = decision.get("selected_variant_id")
        variant_ids = {variant.get("variant_id") for variant in variants}
        if selected and selected not in variant_ids:
            findings.append(
                Finding("ERROR", "SELECTED_VARIANT", f"selected_variant_id does not exist: {selected}", fact_id)
            )
        if status in {"CONTRADICTED", "UNRESOLVED"} and fact.get("canonical_value") is not None and not decision.get("human_confirmed"):
            findings.append(
                Finding("ERROR", "AUTO_ADJUDICATION", f"{status} cannot have an automatic canonical_value.", fact_id)
            )
    return findings


def baseline_findings(ledger: dict[str, Any], baseline: dict[str, Any] | None) -> list[Finding]:
    if not baseline:
        return []
    findings: list[Finding] = []
    current = {fact.get("fact_id"): fact for fact in ledger.get("facts", [])}
    for prior in baseline.get("facts", []):
        decision = prior.get("decision") or {}
        if not (decision.get("state") == "confirmed" or decision.get("human_confirmed")):
            continue
        fact_id = prior.get("fact_id")
        now = current.get(fact_id)
        if now is None:
            findings.append(Finding("ERROR", "CONFIRMED_DROPPED", "A human-confirmed fact was dropped.", fact_id))
            continue
        if now.get("canonical_value") != prior.get("canonical_value") or now.get("decision") != prior.get("decision"):
            findings.append(
                Finding("ERROR", "CONFIRMED_OVERWRITTEN", "A human-confirmed canonical value or decision was overwritten.", fact_id)
            )
    return findings


def source_hash_findings(ledger: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    root_value = ledger.get("project", {}).get("root")
    if not root_value:
        return [Finding("WARNING", "SOURCE_ROOT", "No project root is recorded; live source hashes were not checked.")]
    root = Path(root_value)
    for material in ledger.get("materials", []):
        source_path = Path(material["source_path"])
        path = source_path if source_path.is_absolute() else root / source_path
        if not path.exists():
            findings.append(Finding("WARNING", "SOURCE_MISSING", f"Cannot recheck absent source: {path}"))
            continue
        current_hash = sha256_file(path)
        if current_hash != material.get("source_hash"):
            findings.append(Finding("ERROR", "SOURCE_HASH_CHANGED", f"Source hash changed: {path}"))
    return findings


def validate_ledger(
    ledger: dict[str, Any],
    schema: dict[str, Any],
    *,
    baseline: dict[str, Any] | None = None,
    check_sources: bool = False,
) -> list[Finding]:
    findings = schema_findings(ledger, schema)
    findings.extend(semantic_findings(ledger))
    findings.extend(baseline_findings(ledger, baseline))
    if check_sources:
        findings.extend(source_hash_findings(ledger))
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate project-facts.json and its evidence/decision invariants.")
    parser.add_argument("--ledger", required=True, help="project-facts.json path.")
    parser.add_argument("--schema", required=True, help="project-facts.schema.json path.")
    parser.add_argument("--baseline", help="Optional prior ledger used to detect overwritten human decisions.")
    parser.add_argument("--check-sources", action="store_true", help="Re-hash currently accessible materials.")
    parser.add_argument("--output", help="Optional validation.json result path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        ledger = read_json(Path(args.ledger))
        schema = read_json(Path(args.schema))
        baseline = read_json(Path(args.baseline)) if args.baseline else None
        findings = validate_ledger(ledger, schema, baseline=baseline, check_sources=args.check_sources)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    errors = [finding for finding in findings if finding.level == "ERROR"]
    warnings = [finding for finding in findings if finding.level == "WARNING"]
    for finding in findings:
        suffix = f" [{finding.fact_id}]" if finding.fact_id else ""
        print(f"{finding.level} {finding.code}{suffix}: {finding.message}")
    print(f"Validation {'failed' if errors else 'passed'}: {len(errors)} error(s), {len(warnings)} warning(s).")
    if args.output:
        payload = {
            "valid": not errors,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "findings": [asdict(finding) for finding in findings],
        }
        write_json_idempotent(Path(args.output), payload, volatile_keys=())
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
