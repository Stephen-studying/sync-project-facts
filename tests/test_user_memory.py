from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "manage_user_memory.py"
SCHEMA = ROOT / "schemas" / "user-memory.schema.json"
sys.path.insert(0, str(ROOT / "scripts"))

from manage_user_memory import default_profile, validate_profile


class LocalUserMemoryTests(unittest.TestCase):
    def run_memory(self, memory_dir: Path, *arguments: str, expected: int = 0) -> dict:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPT),
                "--memory-dir",
                str(memory_dir),
                *arguments,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {"output": result.stdout}

    def test_explicit_feedback_is_learned_and_applied_next_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="本地偏好-") as temporary:
            memory_dir = Path(temporary) / "记忆"
            learned = self.run_memory(
                memory_dir,
                "learn-feedback",
                "--text",
                "以后回答简洁一点，先给结论，并且用中文。",
            )
            self.assertEqual(
                {item["key"] for item in learned["learned"]},
                {"response_detail", "answer_order", "language"},
            )

            recalled = self.run_memory(memory_dir, "recall", "--query", "下一次项目事实核对")
            self.assertEqual(recalled["preferences"]["response_detail"]["value"], "concise")
            self.assertEqual(recalled["preferences"]["answer_order"]["value"], "outcome_first")
            self.assertTrue(any("concise" in item for item in recalled["style_directives"]))
            self.assertTrue(recalled["current_request_overrides_memory"])

    def test_ambiguous_feedback_waits_for_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            memory_dir = Path(temporary) / "memory"
            learned = self.run_memory(
                memory_dir,
                "learn-feedback",
                "--text",
                "这个回答感觉不太对，下次注意。",
            )
            self.assertEqual(learned["learned"], [])
            pending = learned["pending_inference"]
            self.assertIsNotNone(pending)

            recalled = self.run_memory(memory_dir, "recall", "--query", "")
            self.assertEqual(recalled["preferences"], {})
            self.assertEqual(len(recalled["pending_inferences"]), 1)

            confirmed = self.run_memory(
                memory_dir,
                "confirm-pending",
                "--pending-id",
                pending["pending_id"],
                "--key",
                "tone",
                "--value",
                "direct",
            )
            self.assertEqual(confirmed["confirmed"]["source"], "confirmed_inference")

    def test_cross_session_history_recall_uses_question_and_answer_summaries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="历史召回-") as temporary:
            memory_dir = Path(temporary) / "memory"
            first = self.run_memory(
                memory_dir,
                "record-interaction",
                "--question",
                "核对光伏项目论文和PPT中的指标",
                "--answer-summary",
                "发现mAP数值冲突并保留两个候选值",
                "--feedback",
                "以后先列出高风险冲突",
                "--project",
                "光伏缺陷检测",
                "--tags",
                "光伏",
                "指标",
            )
            self.assertTrue(first["changed"])

            recalled = self.run_memory(memory_dir, "recall", "--query", "光伏项目指标")
            self.assertEqual(len(recalled["remembered_interactions"]), 1)
            self.assertIn("mAP", recalled["remembered_interactions"][0]["answer_summary"])

    def test_duplicate_interaction_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            memory_dir = Path(temporary) / "memory"
            arguments = (
                "record-interaction",
                "--question",
                "同一个问题",
                "--answer-summary",
                "同一个回答",
            )
            self.assertTrue(self.run_memory(memory_dir, *arguments)["changed"])
            self.assertFalse(self.run_memory(memory_dir, *arguments)["changed"])
            shown = self.run_memory(memory_dir, "show")
            self.assertEqual(shown["history_entries"], 1)

    def test_forget_and_reset_give_user_control(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            memory_dir = Path(temporary) / "memory"
            self.run_memory(
                memory_dir,
                "set-preference",
                "--key",
                "answer_format",
                "--value",
                "table_when_useful",
            )
            forgotten = self.run_memory(memory_dir, "forget", "--key", "answer_format")
            self.assertTrue(forgotten["changed"])
            recalled = self.run_memory(memory_dir, "recall", "--query", "")
            self.assertNotIn("answer_format", recalled["preferences"])

            reset = self.run_memory(memory_dir, "reset", "--confirm", "RESET")
            self.assertGreaterEqual(len(reset["removed"]), 1)
            self.assertFalse((memory_dir / "user-memory.json").exists())
            self.assertFalse((memory_dir / "interaction-history.jsonl").exists())

    def test_profile_policy_and_schema_are_local_and_conservative(self) -> None:
        profile = default_profile()
        self.assertEqual(validate_profile(profile), [])
        self.assertTrue(profile["policy"]["local_only"])
        self.assertFalse(profile["policy"]["store_full_conversation"])
        self.assertTrue(profile["policy"]["inferred_preferences_require_confirmation"])

        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "urn:sync-project-facts:schema:user-memory:1.0")
        self.assertIn("pending_inferences", schema["required"])
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".local-memory/", gitignore)

    def test_memory_script_has_no_network_clients(self) -> None:
        banned = {"requests", "httpx", "urllib", "socket", "aiohttp"}
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(imports & banned)

    def test_trigger_evals_cover_memory_recall_learning_and_confirmation_boundary(self) -> None:
        cases = json.loads((ROOT / "evals" / "trigger-cases.json").read_text(encoding="utf-8"))["cases"]
        recall = next(item for item in cases if item["id"] == "positive-local-preference-recall")
        learning = next(item for item in cases if item["id"] == "positive-explicit-feedback-learning")
        inference = next(item for item in cases if item["id"] == "negative-silent-preference-inference")
        self.assertEqual(recall["expected_sequence"][0], "memory-recall")
        self.assertEqual(learning["expected_sequence"], ["memory-learn"])
        self.assertFalse(inference["should_trigger"])


if __name__ == "__main__":
    unittest.main()
