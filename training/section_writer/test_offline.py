"""CPU-only preparation checks. No model weights, GPU, API, or repository writes."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from prepare import (
    build_bundle, check_split, demo_rows, make_row, read_rows, split_rows,
    validate_row, write_rows,
)
from rewards import grounded_reward, score
from run import check_baseline, load_data, model_fingerprint, remaining_seconds, summary, token_check
from runtime import approved_view, canonical, digest, production, source_hashes, writer_prompt


def checkpoint(row: dict, *, running: bool = False) -> dict:
    """Synthetic checkpoint with real DTO/digest contracts, never training data."""
    state = production("state")
    message = json.loads(row["prompt"][1]["content"])
    request = {
        "workflow_mode": "academic_langgraph", "workflow_id": "offline", "thread_id": "offline",
        "run_id": "offline", "query": message["root_topic"], "report_type": "research_report",
        "report_source": "local", "tone": "Objective", "language": "zh-CN",
        "source_urls": [], "document_urls": [], "query_domains": [], "max_search_results": 1,
        "report_mode": "stem_literature_review", "report_locale": "zh-CN",
    }
    outline = copy.deepcopy(message["outline"])
    outline.update(evidence_id="evidence:000001", attempt=1,
                   report_mode=request["report_mode"], report_locale="zh-CN")
    profile = production("report_profiles")._get_report_profile(request["report_mode"])
    for section, (role, _) in zip(outline["sections"], profile, strict=True):
        section["section_role"] = role
    trusted_outline = state.WorkflowOutline.model_validate_json(canonical(outline))
    events = [(kind, node) for node in ("topic_planner", "research_evidence", "outline_writer", "outline_approval")
              for kind in ("node_started", "node_completed")]
    if not running:
        events.append(("workflow_completed", None))
    return {
        "schema_version": "1", "workflow_id": "offline", "thread_id": "offline", "run_id": "offline",
        "phase": "outline_approved", "status": "running" if running else "completed",
        "request": request,
        "topic_plan": {"topic_plan_id": "topic-plan:000001", "workflow_id": "offline", "run_id": "offline",
                       "attempt": 1, "research_topic": message["root_topic"],
                       "research_questions": message["research_questions"]},
        "research_evidence": {
            "evidence_id": "evidence:000001", "topic_plan_id": "topic-plan:000001", "attempt": 1,
            "context_blocks": message["context_blocks"],
            "sources": [{"source_id": s["source_id"], "title": s["title"], "url": s["url"],
                         "order": i, "candidate_id": None}
                        for i, s in enumerate(message["evidence_sources"], 1)],
            "provenance": [{"source_id": sid, "evidence_blocks": blocks}
                           for sid, blocks in row["evidence"].items()],
        },
        "outline": outline,
        "outline_decision": {
            "decision_id": "outline-decision:000001", "schema_version": "1",
            "workflow_id": "offline", "thread_id": "offline", "run_id": "offline",
            "outline_id": outline["outline_id"], "outline_digest": state._outline_digest(trusted_outline),
            "decision": "approve", "actor_assertion": "offline-test", "attempt": 1,
        }, "errors": [],
        "events": [{"event_id": f"event:{i:06d}", "order": i, "event_type": kind,
                    "node_id": node, "attempt": 1} for i, (kind, node) in enumerate(events, 1)],
    }


class OfflinePreparationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")).start()
        patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")).start()
        self.rows = demo_rows()
        self.row = self.rows[0]
        self.text = self.row["rubric"]["units"][0]["variants"][0]
        self.marker = "[[cite:evidence-source:000001]]"

    def rated(self, body):
        return score(canonical({"content": body}), self.row["allowed_source_ids"], self.row["rubric"])

    def test_demo_is_valid_but_never_real_training(self):
        self.assertEqual(len(self.rows), 6)
        for row in self.rows:
            validate_row(row)
            with self.assertRaises(ValueError):
                validate_row(row, for_training=True)
        self.assertNotIn("gpt_researcher.agent", sys.modules)
        self.assertNotIn("torch", sys.modules)

    def test_actual_adapter_prompt_and_parser_without_provider(self):
        state = approved_view(checkpoint(self.row))
        captured = []
        expected = writer_prompt(state, "section:000004")[0]
        raw = canonical({"content": self.text + self.marker})

        class Client:
            async def complete(self, *, system_message, user_message):
                captured.append((system_message, user_message))
                return raw

        factory = unittest.mock.Mock(side_effect=Client)
        writer = production("section_writer")
        # Fake completion never suspends. Drive the coroutine directly so the
        # Windows event-loop self-pipe cannot be mistaken for network traffic.
        coroutine = writer.GPTResearcherSectionWriterAdapter(
            section_writer_client_factory=factory).write_section(state, "section:000004")
        try:
            with self.assertRaises(StopIteration) as finished:
                coroutine.send(None)
            draft = finished.exception.value
        finally:
            coroutine.close()
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(captured, [(expected[0]["content"], expected[1]["content"])])
        self.assertEqual(draft.content, self.text + self.marker)

    def test_checkpoint_execution_view_is_private_and_validated(self):
        for running in (False, True):
            payload = checkpoint(self.row, running=running)
            before = canonical(payload)
            view = approved_view({"workflow": payload})
            self.assertEqual((view.phase, view.status), ("outline_approved", "completed"))
            self.assertEqual(canonical(payload), before)
            exported = make_row(view, "section:000004", "real-group")
            validate_row(exported)
            self.assertEqual(exported["rubric"], {"units": []})
            self.assertFalse(exported["annotation_approved"])
            self.assertFalse(exported["cloud_ok"])
        payload["outline_decision"]["outline_digest"] = "0" * 64
        with self.assertRaises(Exception):
            approved_view(payload)

    def test_semantic_errors_do_not_gain_format_reward(self):
        self.assertEqual(self.rated(self.text + self.marker)["reward"], 1)
        self.assertEqual(self.rated((self.text + self.marker) * 2)["reward"], 1)
        for content in (self.text.replace("25", "95") + self.marker,
                        self.text + self.marker + "该设备适用于所有环境。" + self.marker,
                        "该设备没有经过测试。" + self.marker,
                        self.marker):
            with self.subTest(content_kind=len(content)):
                result = self.rated(content)
                self.assertTrue(result["parser_passed"])
                self.assertEqual(result["reward"], 0)

    def test_actual_parser_failures_and_no_reward_retry(self):
        writer = production("section_writer")
        with patch.object(writer, "_create_production_section_writer_client", side_effect=AssertionError("factory")) as factory:
            for body in (self.text, self.text + "[1]", self.text + "【1】",
                         self.text + "[[cite:unknown]]", self.text + "[[cite:evidence-source:000001]"):
                self.assertEqual(self.rated(body)["reward"], -1)
            factory.assert_not_called()
        legacy = canonical({"content": self.text + self.marker, "citations": ["unknown", "unknown"]})
        self.assertEqual(score(legacy, self.row["allowed_source_ids"], self.row["rubric"])["reward"], 1)
        with patch.object(writer, "_parse_response", return_value=writer._CONTRACT_FAILURE):
            with self.assertRaises(RuntimeError):
                self.rated(self.text + self.marker)

    def test_trl_reward_alignment_and_local_attribution(self):
        ids = self.row["allowed_source_ids"] + ["evidence-source:000002"]
        wrong = canonical({"content": self.text + "[[cite:evidence-source:000002]]"})
        right = canonical({"content": self.text + self.marker})
        values = grounded_reward([[{"role": "assistant", "content": right}], wrong],
                                 [ids, ids], [self.row["rubric"], self.row["rubric"]])
        self.assertEqual(values, [1, 0])
        with self.assertRaises(ValueError):
            grounded_reward([right], [], [])

    def test_annotation_must_anchor_visible_original_evidence(self):
        for mutation in ("quote", "index", "source", "flags", "hash"):
            row = copy.deepcopy(self.row)
            if mutation == "quote":
                row["rubric"]["units"][0]["evidence_quotes"][0]["quote"] = "不存在的证据句子，仅供反例。"
            elif mutation == "index":
                row["rubric"]["units"][0]["evidence_quotes"][0]["block_index"] = 99
            elif mutation == "source":
                row["allowed_source_ids"] = ["evidence-source:000002"]
            elif mutation == "flags":
                row["cloud_ok"] = "true"
            else:
                row["production_sha256"]["section_writer.py"] = "0" * 64
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_row(row)

    def test_split_blocks_paper_and_topic_leakage(self):
        # ONLY this test simulates human-approved real rows to exercise splitting.
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row.update(synthetic=False, annotation_approved=True, cloud_ok=True)
        train, evaluate = split_rows(rows)
        check_split(train, evaluate)
        self.assertEqual(len(train) + len(evaluate), len(rows))
        self.assertFalse({r["group"] for r in train} & {r["group"] for r in evaluate})
        with self.assertRaises(ValueError):
            check_split(train, train[:1])
        with self.assertRaises(ValueError):
            split_rows(rows[:2])
        with self.assertRaises(ValueError):
            split_rows(rows + rows[:1])

    def test_token_budget_counts_chat_template_and_never_truncates(self):
        tokenizer = unittest.mock.Mock()
        tokenizer.apply_chat_template.return_value = list(range(2048))
        tokenizer.encode.return_value = list(range(383))
        self.assertEqual(token_check(tokenizer, [self.row]), [2048])
        self.assertEqual(tokenizer.apply_chat_template.call_args.kwargs["enable_thinking"], False)
        tokenizer.apply_chat_template.return_value = list(range(2049))
        with self.assertRaises(ValueError):
            token_check(tokenizer, [self.row])
        tokenizer.apply_chat_template.return_value = [1]
        tokenizer.encode.return_value = list(range(384))
        with self.assertRaises(ValueError):
            token_check(tokenizer, [self.row])

    def test_budget_includes_time_before_process_started(self):
        now = datetime(2026, 10, 3, 6, tzinfo=timezone.utc)
        seconds = remaining_seconds("2026-10-03T08:00:00+08:00", 1.87, 3, 90, now=now)
        self.assertEqual(seconds, int(12 / 1.87 * 3600 - 6 * 3600))
        for start, price in (("2026-10-03T00:00:00", 1.87),
                             ("2026-10-03T07:00:00+00:00", 1.87),
                             ("2026-10-02T00:00:00+00:00", 1.87),
                             ("2026-10-03T00:00:00+00:00", float("nan"))):
            with self.assertRaises(ValueError):
                remaining_seconds(start, price, 3, 90, now=now)

    def test_exclusive_outputs_and_training_data_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_rows(directory / "train.jsonl", self.rows[:4])
            write_rows(directory / "eval.jsonl", self.rows[4:])
            self.assertEqual(read_rows(directory / "train.jsonl"), self.rows[:4])
            with self.assertRaises(FileExistsError):
                write_rows(directory / "train.jsonl", [])
            with self.assertRaises(ValueError):
                load_data(directory)
            with self.assertRaises(ValueError):
                check_baseline(None, directory)
            (directory / "weights.safetensors").write_bytes(b"fake bytes for hash test only")
            first = model_fingerprint(directory)
            self.assertEqual(first["weights.safetensors"], digest(b"fake bytes for hash test only"))
            (directory / "weights.safetensors").write_bytes(b"different weights, same config")
            self.assertNotEqual(model_fingerprint(directory), first)

    def test_bundle_is_allowlisted_portable_and_never_uploaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            archive_path = directory / "code.zip"
            build_bundle(None, archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
                self.assertNotIn(".env", names)
                self.assertNotIn("train.jsonl", names)
                self.assertNotIn("eval.jsonl", names)
                manifest = json.loads(archive.read("manifest.json"))
                self.assertFalse(manifest["real_training_data_included"])
                for name, sha in manifest["files"].items():
                    self.assertEqual(digest(archive.read(name)), sha)
                archive.extractall(directory / "unpacked")
            result = subprocess.run(
                [sys.executable, "-B", str(directory / "unpacked/prepare.py"), "check",
                 "--data", str(directory / "unpacked/demo.jsonl")],
                cwd=directory, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, "standalone bundle failed offline check")
            self.assertEqual(json.loads(result.stdout), {"examples": 6, "training_eligible": 0})

    def test_summary_is_not_a_truth_claim(self):
        result = summary([self.rated(self.text + self.marker), self.rated(self.text)])
        self.assertEqual(result["parser_pass_rate"], 0.5)
        self.assertFalse(result["general_factuality_proven"])


if __name__ == "__main__":
    unittest.main()
