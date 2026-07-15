#!/usr/bin/env python3
"""Render a concise, locator-rich Markdown synchronization report."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from project_facts_common import SEVERITY_RANK, read_json, write_text_idempotent


UNRESOLVED_STATUSES = {"CONTRADICTED", "UNSUPPORTED", "MISSING", "UNRESOLVED"}
STATUS_PRIORITY = {
    "CONTRADICTED": 0,
    "UNRESOLVED": 1,
    "UNSUPPORTED": 2,
    "MISSING": 3,
    "STALE": 4,
    "SCOPED_DIFFERENCE": 5,
    "EQUIVALENT": 6,
    "CONSISTENT": 7,
}


def md(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    if not rows:
        rows = [["—"] + [""] * (len(headers) - 1)]
    for row in rows:
        lines.append("| " + " | ".join(md(cell) for cell in row) + " |")
    return "\n".join(lines)


def fact_label(fact: dict[str, Any]) -> str:
    return f"{fact.get('category')}.{fact.get('key')} (`{fact.get('fact_id')}`)"


def value_with_unit(value: Any, unit: Any) -> str:
    if value is None:
        return "候选值待裁决"
    rendered = md(value)
    if unit and not (isinstance(value, str) and str(unit) in value):
        rendered += f" {unit}"
    return rendered


def evidence_text(fact: dict[str, Any]) -> str:
    items = []
    for evidence in fact.get("evidence", []):
        items.append(f"`{evidence['source_path']}` @ {evidence['locator']}")
    return "<br>".join(items) if items else "—"


def material_values(fact: dict[str, Any]) -> dict[str, list[str]]:
    evidence = {item["evidence_id"]: item for item in fact.get("evidence", [])}
    values: dict[str, list[str]] = defaultdict(list)
    for variant in fact.get("variants", []):
        rendered = value_with_unit(variant.get("value"), variant.get("unit"))
        scope = variant.get("scope") or {}
        if scope:
            rendered += f" [{md(scope)}]"
        for evidence_id in variant.get("evidence_ids", []):
            item = evidence.get(evidence_id)
            if not item:
                continue
            value = f"{rendered}<br><small>{item['locator']}</small>"
            if value not in values[item["source_path"]]:
                values[item["source_path"]].append(value)
    return values


def render_materials(ledger: dict[str, Any]) -> str:
    rows = []
    for material in ledger.get("materials", []):
        warning = "; ".join(material.get("warnings", [])) or "—"
        rows.append(
            [
                material["source_path"],
                material["source_type"],
                material["source_hash"][:12],
                material["modified_at"],
                material["size_bytes"],
                warning,
            ]
        )
    return table(["Material", "Type", "SHA-256", "Modified", "Bytes", "Extraction notes"], rows)


def render_ledger(ledger: dict[str, Any]) -> str:
    rows = []
    for fact in ledger.get("facts", []):
        candidates = "<br>".join(
            value_with_unit(variant.get("value"), variant.get("unit")) for variant in fact.get("variants", [])
        )
        canonical = value_with_unit(fact.get("canonical_value"), fact.get("unit"))
        if fact.get("canonical_value") is None:
            canonical = candidates or "—"
        rows.append(
            [
                fact_label(fact),
                canonical,
                fact.get("scope") or "—",
                fact["status"],
                fact["severity"],
                f"{float(fact['confidence']):.2f}",
                evidence_text(fact),
            ]
        )
    return table(["Fact", "Canonical / candidates", "Scope", "Status", "Severity", "Confidence", "Evidence"], rows)


def render_matrix(ledger: dict[str, Any]) -> str:
    material_paths = [material["source_path"] for material in ledger.get("materials", [])]
    material_a = material_paths[0] if material_paths else "Material A"
    material_b_paths = material_paths[1:] or ["Material B"]
    rows = []
    for fact in ledger.get("facts", []):
        values = material_values(fact)
        a_value = "<br>".join(values.get(material_a, [])) or "—"
        b_parts = []
        for path in material_b_paths:
            rendered = "<br>".join(values.get(path, [])) or "—"
            b_parts.append(f"**{md(path)}**: {rendered}")
        canonical = value_with_unit(fact.get("canonical_value"), fact.get("unit"))
        if fact.get("canonical_value") is None:
            canonical = " / ".join(value_with_unit(v.get("value"), v.get("unit")) for v in fact.get("variants", []))
        rows.append(
            [
                fact_label(fact),
                canonical,
                a_value,
                "<br>".join(b_parts),
                fact["status"],
                fact["severity"],
                evidence_text(fact),
                fact["repair"],
            ]
        )
    return table(
        ["Fact", "Canonical/候选事实", md(material_a), "Material B / others", "Status", "Severity", "Evidence", "Repair"],
        rows,
    )


def render_high_risk(ledger: dict[str, Any]) -> str:
    facts = [fact for fact in ledger.get("facts", []) if fact.get("severity") in {"High", "Critical"}]
    facts.sort(key=lambda fact: (-SEVERITY_RANK[fact["severity"]], STATUS_PRIORITY.get(fact["status"], 99), fact["key"]))
    if not facts:
        return "- 未发现 High 或 Critical 问题。"
    return "\n".join(
        f"- **{fact['severity']} · {fact['status']} · {fact_label(fact)}** — {md(fact['repair'])} Evidence: {evidence_text(fact)}"
        for fact in facts
    )


def render_repair_order(ledger: dict[str, Any]) -> str:
    facts = [fact for fact in ledger.get("facts", []) if fact.get("status") != "CONSISTENT"]
    facts.sort(key=lambda fact: (-SEVERITY_RANK[fact["severity"]], STATUS_PRIORITY.get(fact["status"], 99), fact["key"]))
    if not facts:
        return "1. 无需修复；保留当前事实与来源定位。"
    return "\n".join(
        f"{index}. **{fact['severity']} · {fact['status']} · {fact.get('key')}** — {md(fact['repair'])}"
        for index, fact in enumerate(facts, start=1)
    )


def render_unresolved(ledger: dict[str, Any]) -> str:
    facts = [fact for fact in ledger.get("facts", []) if fact.get("status") in UNRESOLVED_STATUSES]
    if not facts:
        return "- 无未决事实。"
    lines = []
    for fact in facts:
        variants = "; ".join(
            f"{value_with_unit(variant.get('value'), variant.get('unit'))} @ {', '.join(variant.get('evidence_ids', []))}"
            for variant in fact.get("variants", [])
        )
        lines.append(f"- **{fact_label(fact)} · {fact['status']}** — candidates: {variants}. {md(fact['repair'])}")
    return "\n".join(lines)


def render_sync_status(ledger: dict[str, Any]) -> str:
    rows = []
    for material in ledger.get("materials", []):
        path = material["source_path"]
        statuses = Counter()
        fact_count = 0
        for fact in ledger.get("facts", []):
            if any(evidence.get("source_path") == path for evidence in fact.get("evidence", [])):
                statuses[fact["status"]] += 1
                fact_count += 1
        explicit_missing = [
            fact["key"]
            for fact in ledger.get("facts", [])
            if fact.get("status") == "MISSING" and str(fact.get("target_material", "")).replace("\\", "/") == path.replace("\\", "/")
        ]
        summary = ", ".join(f"{status}={count}" for status, count in sorted(statuses.items())) or "no extracted facts"
        rows.append([path, fact_count, summary, ", ".join(explicit_missing) or "—"])
    return table(["Material", "Referenced facts", "Status summary", "Explicitly missing facts"], rows)


def render_report(ledger: dict[str, Any], template: str) -> str:
    replacements = {
        "{{PROJECT_NAME}}": md(ledger.get("project", {}).get("name", "Unnamed project")),
        "{{LEDGER_ID}}": md(ledger.get("ledger_id", "")),
        "{{MATERIALS}}": render_materials(ledger),
        "{{LEDGER}}": render_ledger(ledger),
        "{{MATRIX}}": render_matrix(ledger),
        "{{HIGH_RISK}}": render_high_risk(ledger),
        "{{REPAIR_ORDER}}": render_repair_order(ledger),
        "{{UNRESOLVED}}": render_unresolved(ledger),
        "{{SYNC_STATUS}}": render_sync_status(ledger),
    }
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered.rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render fact-sync-report.md from project-facts.json.")
    parser.add_argument("--ledger", required=True, help="project-facts.json path.")
    parser.add_argument("--output", required=True, help="Output fact-sync-report.md path.")
    parser.add_argument("--template", help="Override the bundled Markdown template.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    template_path = Path(args.template) if args.template else Path(__file__).resolve().parents[1] / "assets" / "sync-report.template.md"
    try:
        ledger = read_json(Path(args.ledger))
        template = template_path.read_text(encoding="utf-8")
        rendered = render_report(ledger, template)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    changed = write_text_idempotent(Path(args.output), rendered)
    print(f"{'Wrote' if changed else 'Unchanged'}: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
