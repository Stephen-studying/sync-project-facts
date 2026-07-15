from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCHEMA = ROOT / "schemas" / "project-facts.schema.json"
FIXTURES = ROOT / "tests" / "fixtures" / "中文项目"
sys.path.insert(0, str(SCRIPTS))

from build_fact_ledger import build_ledger_data
from compare_artifacts import add_missing_requirements, compare_candidates
from extract_evidence import block, candidates_from_block, extract_docx, extract_json, extract_pptx, extract_xlsx, extract_yaml
from project_facts_common import read_json, stable_id
from scan_sources import build_manifest
from validate_fact_ledger import validate_ledger


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def candidate(
    value,
    *,
    index: int,
    key: str = "map_0_5",
    category: str = "metric",
    unit: str | None = "%",
    scope: dict | None = None,
    evidence_kind: str = "narrative",
    authority: str = "summary",
    lifecycle: str = "unspecified",
    subtype: str | None = None,
    ownership_level: str | None = None,
    requires_support: bool = False,
    source_type: str = "markdown",
):
    source_hash = f"{index + 1:064x}"[-64:]
    evidence_id = stable_id("evidence", source_hash, f"line {index + 1}", value, evidence_kind)
    result = {
        "candidate_id": stable_id("candidate", index, category, key, value, scope or {}),
        "category": category,
        "key": key,
        "value": value,
        "value_type": "percentage" if unit == "%" else "number" if isinstance(value, (int, float)) else "string",
        "unit": unit,
        "scope": scope or {},
        "aliases": [],
        "lifecycle": lifecycle,
        "subtype": subtype,
        "source_authority": authority,
        "requires_support": requires_support,
        "evidence": {
            "evidence_id": evidence_id,
            "source_path": f"material-{index + 1}.md",
            "source_type": source_type,
            "source_hash": source_hash,
            "locator": f"line {index + 1}",
            "excerpt": f"{key} = {value}{unit or ''}",
            "extracted_value": value,
            "modified_at": "2026-01-01T00:00:00Z",
            "evidence_kind": evidence_kind,
        },
    }
    if ownership_level:
        result["ownership_level"] = ownership_level
    return result


def comparison_payload(facts: list[dict], root: str = "C:/project") -> dict:
    hashes = {}
    for fact in facts:
        for evidence in fact.get("evidence", []):
            if evidence.get("evidence_kind") != "sync_requirement":
                hashes[evidence["source_path"]] = evidence["source_hash"]
    materials = [
        {
            "source_path": path,
            "source_type": "markdown",
            "source_hash": source_hash,
            "modified_at": "2026-01-01T00:00:00Z",
            "size_bytes": 100,
            "warnings": [],
        }
        for path, source_hash in sorted(hashes.items())
    ]
    while len(materials) < 2:
        index = len(materials) + 1
        materials.append(
            {
                "source_path": f"material-{index}.md",
                "source_type": "markdown",
                "source_hash": f"{index:064x}"[-64:],
                "modified_at": "2026-01-01T00:00:00Z",
                "size_bytes": 100,
                "warnings": [],
            }
        )
    return {
        "root": root,
        "manifest_id": "manifest-test",
        "evidence_id": "evidence-set-test",
        "materials": materials,
        "comparisons": facts,
        "warnings": [],
    }


class ConflictRuleTests(unittest.TestCase):
    def test_same_scope_different_map_is_contradicted_without_picking_high_value(self) -> None:
        scope = {"dataset": "PV-Synth", "modality": "RGB", "metric_definition": "mAP@0.5"}
        fact = compare_candidates(
            [candidate(93.7, index=0, scope=scope), candidate(94.2, index=1, scope=scope)]
        )[0]
        self.assertEqual(fact["status"], "CONTRADICTED")
        self.assertIsNone(fact["canonical_value"])
        self.assertEqual({variant["value"] for variant in fact["variants"]}, {93.7, 94.2})

    def test_decimal_and_percent_are_equivalent(self) -> None:
        scope = {"metric_definition": "mAP@0.5"}
        fact = compare_candidates(
            [candidate(0.937, index=0, unit=None, scope=scope), candidate(93.7, index=1, unit="%", scope=scope)]
        )[0]
        self.assertEqual(fact["status"], "EQUIVALENT")
        self.assertAlmostEqual(fact["canonical_value"], 93.7)

    def test_convertible_units_are_equivalent(self) -> None:
        fact = compare_candidates(
            [
                candidate(500, index=0, key="latency", unit="ms"),
                candidate(0.5, index=1, key="latency", unit="s"),
            ]
        )[0]
        self.assertEqual(fact["status"], "EQUIVALENT")
        self.assertEqual(fact["unit"], "s")

    def test_rgb_and_ir_are_scoped_difference(self) -> None:
        fact = compare_candidates(
            [
                candidate(93.7, index=0, scope={"modality": "RGB", "metric_definition": "mAP@0.5"}),
                candidate(91.7, index=1, scope={"modality": "IR", "metric_definition": "mAP@0.5"}),
            ]
        )[0]
        self.assertEqual(fact["status"], "SCOPED_DIFFERENCE")
        self.assertIn("modality", fact["scope"]["extra"]["differing_dimensions"])

    def test_metric_definitions_are_scoped_difference(self) -> None:
        fact = compare_candidates(
            [
                candidate(93.7, index=0, scope={"metric_definition": "mAP@0.5"}),
                candidate(71.3, index=1, scope={"metric_definition": "mAP@0.5:0.95"}),
            ]
        )[0]
        self.assertEqual(fact["status"], "SCOPED_DIFFERENCE")
        self.assertNotEqual(fact["status"], "CONTRADICTED")

    def test_explicit_old_module_is_stale(self) -> None:
        fact = compare_candidates(
            [
                candidate("ECFP", index=0, key="module_name", category="model_method", unit=None, lifecycle="old"),
                candidate("DMMA", index=1, key="module_name", category="model_method", unit=None, lifecycle="current"),
            ]
        )[0]
        self.assertEqual(fact["status"], "STALE")
        self.assertEqual(fact["canonical_value"], "DMMA")
        self.assertIn("modification time was not used", " ".join(fact["notes"]).lower())

    def test_ownership_conflict_is_critical(self) -> None:
        fact = compare_candidates(
            [
                candidate(
                    "独立设计并训练全部模型",
                    index=0,
                    key="personal_contribution",
                    category="contribution",
                    unit=None,
                    subtype="OWNERSHIP",
                    ownership_level="sole",
                    evidence_kind="contribution_statement",
                ),
                candidate(
                    "负责数据整理、标注和部分写作",
                    index=1,
                    key="personal_contribution",
                    category="contribution",
                    unit=None,
                    subtype="OWNERSHIP",
                    ownership_level="partial",
                    evidence_kind="contribution_statement",
                    authority="primary",
                ),
            ]
        )[0]
        self.assertEqual(fact["status"], "CONTRADICTED")
        self.assertEqual(fact["subtype"], "OWNERSHIP")
        self.assertIn(fact["severity"], {"High", "Critical"})

    def test_improvement_claim_without_proof_is_unsupported(self) -> None:
        fact = compare_candidates(
            [
                candidate(
                    12,
                    index=0,
                    key="performance_improvement",
                    unit="%",
                    evidence_kind="claim",
                    authority="claim",
                    requires_support=True,
                )
            ]
        )[0]
        self.assertEqual(fact["status"], "UNSUPPORTED")
        self.assertIsNone(fact["canonical_value"])

    def test_two_formal_result_tables_are_unresolved(self) -> None:
        scope = {"dataset": "PV-Synth", "modality": "RGB", "metric_definition": "mAP@0.5"}
        fact = compare_candidates(
            [
                candidate(93.7, index=0, scope=scope, evidence_kind="result_table", authority="formal"),
                candidate(94.2, index=1, scope=scope, evidence_kind="result_table", authority="formal"),
            ]
        )[0]
        self.assertEqual(fact["status"], "UNRESOLVED")
        self.assertIsNone(fact["canonical_value"])

    def test_missing_requires_explicit_target_record(self) -> None:
        source_candidates = [candidate(93.7, index=0)]
        comparisons = compare_candidates(source_candidates)
        with tempfile.TemporaryDirectory() as temporary:
            requirement_path = Path(temporary) / "requirements.json"
            requirement_path.write_text(
                json.dumps(
                    {
                        "requirements": [
                            {
                                "category": "metric",
                                "key": "map_0_5",
                                "target_path": "material-2.md",
                                "scope": {"metric_definition": "mAP@0.5"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = add_missing_requirements(comparisons, source_candidates, requirement_path)
        missing = [fact for fact in result if fact["status"] == "MISSING"]
        self.assertEqual(len(missing), 1)
        self.assertTrue(any(item["evidence_kind"] == "sync_requirement" for item in missing[0]["evidence"]))


class LedgerAndValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        scope = {"dataset": "PV-Synth", "modality": "RGB", "metric_definition": "mAP@0.5"}
        self.fact = compare_candidates(
            [candidate(93.7, index=0, scope=scope), candidate(93.7, index=1, scope=scope)]
        )[0]

    def test_schema_and_semantic_validation_pass(self) -> None:
        ledger = build_ledger_data(comparison_payload([self.fact]), project_name="PV Test")
        findings = validate_ledger(ledger, read_json(SCHEMA))
        errors = [finding for finding in findings if finding.level == "ERROR"]
        self.assertEqual(errors, [], "\n".join(f"{item.code}: {item.message}" for item in errors))

    def test_confirmed_decision_is_not_overwritten(self) -> None:
        first = build_ledger_data(comparison_payload([self.fact]), project_name="PV Test")
        first_fact = first["facts"][0]
        first_fact["canonical_value"] = 92.5
        first_fact["decision"] = {
            "state": "confirmed",
            "human_confirmed": True,
            "selected_variant_id": None,
            "decided_by": "user",
            "decided_at": "2026-01-02T00:00:00Z",
            "rationale": "Confirmed from archived run.",
            "history": [{"at": "2026-01-02T00:00:00Z", "action": "confirmed"}],
        }
        changed = compare_candidates(
            [candidate(94.2, index=0), candidate(94.2, index=1)]
        )[0]
        second = build_ledger_data(comparison_payload([changed]), existing=first, project_name="PV Test")
        second_fact = second["facts"][0]
        self.assertEqual(second_fact["canonical_value"], 92.5)
        self.assertEqual(second_fact["decision"], first_fact["decision"])
        self.assertEqual(second_fact["status"], "CONTRADICTED")

    def test_ownership_below_high_is_rejected(self) -> None:
        ownership = copy.deepcopy(self.fact)
        ownership["subtype"] = "OWNERSHIP"
        ownership["severity"] = "Medium"
        ledger = build_ledger_data(comparison_payload([ownership]))
        findings = validate_ledger(ledger, read_json(SCHEMA))
        self.assertTrue(any(finding.code == "OWNERSHIP_SEVERITY" for finding in findings))


class LocatorExtractorTests(unittest.TestCase):
    def test_deployment_readiness_is_not_conflated_with_actual_deployment(self) -> None:
        source = {
            "source_path": "项目汇报.md",
            "source_type": "markdown",
            "source_hash": "b" * 64,
            "modified_at": "2026-01-01T00:00:00Z",
        }
        item = block("line 9", "性能提升 12%，已达到部署要求。", "narrative")
        facts = candidates_from_block(source, item)
        keys = {fact["key"] for fact in facts}
        self.assertIn("deployment_readiness", keys)
        self.assertNotIn("deployment_status", keys)
        readiness = next(fact for fact in facts if fact["key"] == "deployment_readiness")
        compared = compare_candidates([readiness])[0]
        self.assertEqual(compared["status"], "UNSUPPORTED")

    def test_markdown_ownership_keeps_the_located_text(self) -> None:
        source = {
            "source_path": "项目汇报.md",
            "source_type": "markdown",
            "source_hash": "a" * 64,
            "modified_at": "2026-01-01T00:00:00Z",
        }
        item = block("line 11", "个人工作：独立设计并训练全部模型。", "narrative")
        facts = candidates_from_block(source, item)
        ownership = next(fact for fact in facts if fact["key"] == "personal_contribution")
        self.assertEqual(ownership["value"], "个人工作:独立设计并训练全部模型。")
        self.assertNotEqual(ownership["value"], "None")

    def test_json_and_yaml_use_key_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "事实.json"
            json_path.write_text('{"facts": [{"key": "map_0_5", "value": 93.7}]}', encoding="utf-8")
            json_blocks, _, _ = extract_json(json_path)
            self.assertTrue(any(item["locator"] == "$.facts[0].value" for item in json_blocks))
            yaml_path = root / "事实.yaml"
            yaml_path.write_text("model:\n  version: v2.0\n", encoding="utf-8")
            yaml_blocks, _, _ = extract_yaml(yaml_path)
            self.assertTrue(any(item["locator"] == "$.model.version" for item in yaml_blocks))

    def test_ooxml_locators_are_precise(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docx = root / "报告.docx"
            with zipfile.ZipFile(docx, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>mAP@0.5 = 93.7%</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>93.7%</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>""",
                )
            docx_blocks, warnings, _ = extract_docx(docx)
            self.assertEqual(warnings, [])
            self.assertTrue(any("paragraph 1" in item["locator"] for item in docx_blocks))
            self.assertTrue(any("table 1, row 1, cell A1" in item["locator"] for item in docx_blocks))

            pptx = root / "汇报.pptx"
            with zipfile.ZipFile(pptx, "w") as archive:
                archive.writestr(
                    "ppt/slides/slide1.xml",
                    """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>mAP@0.5 = 93.7%</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>""",
                )
            pptx_blocks, warnings, _ = extract_pptx(pptx)
            self.assertEqual(warnings, [])
            self.assertEqual(pptx_blocks[0]["locator"], "slide 1, text box 1")

            xlsx = root / "结果.xlsx"
            with zipfile.ZipFile(xlsx, "w") as archive:
                archive.writestr(
                    "xl/workbook.xml",
                    """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="结果" sheetId="1" r:id="rId1"/></sheets></workbook>""",
                )
                archive.writestr(
                    "xl/_rels/workbook.xml.rels",
                    """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="worksheet"/></Relationships>""",
                )
                archive.writestr(
                    "xl/worksheets/sheet1.xml",
                    """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="B2" t="inlineStr"><is><t>93.7%</t></is></c></row></sheetData></worksheet>""",
                )
            xlsx_blocks, warnings, _ = extract_xlsx(xlsx)
            self.assertEqual(warnings, [])
            self.assertEqual(xlsx_blocks[0]["locator"], 'sheet "结果", cell B2')


class EndToEndTests(unittest.TestCase):
    def run_script(self, script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / script), *map(str, arguments)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_chinese_path_read_only_idempotent_offline_pipeline(self) -> None:
        before = {path.name: file_hash(path) for path in FIXTURES.iterdir() if path.is_file()}
        with tempfile.TemporaryDirectory(prefix="事实同步-") as temporary:
            output = Path(temporary) / "输出"
            output.mkdir()
            manifest = output / "source-manifest.json"
            evidence = output / "evidence.json"
            comparison = output / "comparison.json"
            ledger = output / "project-facts.json"
            validation = output / "validation.json"
            report = output / "fact-sync-report.md"

            commands = [
                ("scan_sources.py", str(FIXTURES), "--output", str(manifest)),
                ("extract_evidence.py", "--manifest", str(manifest), "--output", str(evidence)),
                ("compare_artifacts.py", "--evidence", str(evidence), "--output", str(comparison)),
                ("build_fact_ledger.py", "--comparison", str(comparison), "--output", str(ledger), "--project-name", "中文测试项目"),
                (
                    "validate_fact_ledger.py",
                    "--ledger",
                    str(ledger),
                    "--schema",
                    str(SCHEMA),
                    "--check-sources",
                    "--output",
                    str(validation),
                ),
                ("render_sync_report.py", "--ledger", str(ledger), "--output", str(report)),
            ]
            for command in commands:
                self.run_script(*command)

            outputs = [manifest, evidence, comparison, ledger, validation, report]
            first_hashes = {path.name: file_hash(path) for path in outputs}
            for command in commands:
                self.run_script(*command)
            second_hashes = {path.name: file_hash(path) for path in outputs}
            self.assertEqual(first_hashes, second_hashes)

            ledger_data = read_json(ledger)
            statuses = {fact["key"]: fact["status"] for fact in ledger_data["facts"]}
            self.assertEqual(statuses["map_0_5"], "EQUIVALENT")
            report_text = report.read_text(encoding="utf-8")
            for heading in (
                "## 1. 材料清单",
                "## 2. 规范化事实总账",
                "## 3. 冲突矩阵",
                "## 4. 高风险问题",
                "## 5. 建议修复顺序",
                "## 6. 未决问题",
                "## 7. 各材料同步状态",
            ):
                self.assertIn(heading, report_text)
            self.assertRegex(report_text, r"line\s+\d+")

        after = {path.name: file_hash(path) for path in FIXTURES.iterdir() if path.is_file()}
        self.assertEqual(before, after)

    def test_core_scripts_do_not_import_network_clients(self) -> None:
        banned = {"requests", "httpx", "urllib", "socket", "aiohttp"}
        for path in SCRIPTS.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".", 1)[0])
            self.assertFalse(imports & banned, f"{path.name} imports network client(s): {imports & banned}")

    def test_scanner_ignores_locks_caches_and_generated_residue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "paper.md").write_text("mAP@0.5 = 93.7%", encoding="utf-8")
            (root / "slides.txt").write_text("mAP@0.5 = 93.7%", encoding="utf-8")
            (root / "~$locked.docx").write_bytes(b"lock")
            (root / "project-facts.json").write_text("{}", encoding="utf-8")
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "cached.py").write_text("x = 1", encoding="utf-8")
            manifest = build_manifest([root], root / "source-manifest.json")
        self.assertEqual(manifest["source_count"], 2)
        ignored = {item["source_path"] for item in manifest["ignored"]}
        self.assertIn("~$locked.docx", ignored)
        self.assertIn("project-facts.json", ignored)
        self.assertIn("__pycache__/cached.py", ignored)


class TriggerBoundaryTests(unittest.TestCase):
    def test_trigger_evals_cover_positive_negative_and_handoff(self) -> None:
        data = read_json(ROOT / "evals" / "trigger-cases.json")
        cases = data["cases"]
        self.assertGreaterEqual(len(cases), 10)
        self.assertTrue(any(case["should_trigger"] for case in cases))
        self.assertTrue(any(not case["should_trigger"] for case in cases))
        combined = next(case for case in cases if case["id"] == "combined-sync-then-defense")
        self.assertEqual(combined["expected_sequence"], ["sync-project-facts", "defense-beating-simulator"])
        negative = next(case for case in cases if case["id"] == "negative-single-polish")
        self.assertFalse(negative["should_trigger"])

    def test_frontmatter_contains_only_name_and_description(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = skill.split("---", 2)[1].strip().splitlines()
        keys = [line.split(":", 1)[0].strip() for line in frontmatter if ":" in line]
        self.assertEqual(keys, ["name", "description"])
        openai_yaml = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("$sync-project-facts", openai_yaml)
        self.assertNotIn("TO" + "DO", skill)


if __name__ == "__main__":
    unittest.main()
