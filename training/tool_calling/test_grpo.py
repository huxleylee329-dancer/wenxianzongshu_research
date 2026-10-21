"""Local GRPO preparation checks, with fake trainer only; no GPU or network."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import dataset
import grpo
import grpo_bundle
from contracts import SYSTEM, canonical, sha
from prompt_v3 import NEW_RULE, OLD_RULE, SYSTEM_V3, upgrade
from rewards import score


class GrpoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.original = cls.root / "original"
        dataset.build(cls.original)
        cls.archive = cls.root / "grpo.zip"
        cls.export = grpo_bundle.export(cls.original, cls.archive)
        cls.bundle = cls.root / "bundle"
        with zipfile.ZipFile(cls.archive) as archive:
            archive.extractall(cls.bundle)
        cls.manifest, cls.prompts, cls.cases = grpo.load_bundle(cls.bundle)

    def setUp(self):
        for owner, name in ((socket, "create_connection"), (socket, "getaddrinfo"), (socket.socket, "connect")):
            guard = patch.object(owner, name, side_effect=AssertionError("network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_prompt_revision_only_changes_scoped_rule_and_preserves_v2(self):
        self.assertIn(OLD_RULE, SYSTEM)
        self.assertNotIn(OLD_RULE, SYSTEM_V3)
        self.assertEqual(SYSTEM_V3.replace(NEW_RULE, OLD_RULE), SYSTEM)
        for split in ("train", "validation"):
            old = grpo.read_rows(self.original / f"{split}.prompts.jsonl")
            snapshot = deepcopy(old)
            for before, after in zip(old, self.prompts[split]):
                self.assertEqual(before["id"], after["id"])
                self.assertEqual(upgrade(before["prompt"]), after["prompt"])
                self.assertEqual(before["prompt"][1], after["prompt"][1])
            self.assertEqual(old, snapshot)
        # The original dataset remains verifiable, including reserved blind data.
        self.assertEqual(len(dataset.load_verified(self.original)["train"]), 1000)

    def test_cloud_bundle_contains_no_blind_or_validation_labels(self):
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(set(archive.namelist()), grpo.FILES | {"grpo-bundle.json"})
        self.assertEqual(self.export["blind_rows"], 0)
        self.assertEqual(self.export["validation_labels"], 0)
        self.assertEqual((self.bundle / "train.oracle.jsonl").read_bytes(),
                         (self.original / "train.oracle.jsonl").read_bytes())
        for split in ("train", "validation"):
            for row in self.prompts[split]:
                self.assertEqual(set(json.loads(row["prompt"][1]["content"])), {"task", "context", "tools"})
        audit = self.export["audit"]
        self.assertFalse(audit["human_reviewed"])
        self.assertEqual(audit["splits"]["train"]["reference_successes"], 1000)
        self.assertTrue(all(n < 100 for n in audit["splits"]["train"]["constant_policy_successes"].values()))
        with self.assertRaises(FileExistsError):
            grpo_bundle.export(self.original, self.archive)

    def test_bundle_tamper_and_private_prompt_mismatch_fail(self):
        path = self.bundle / "train.prompts.jsonl"
        manifest_path = self.bundle / "grpo-bundle.json"
        original, original_manifest = path.read_bytes(), manifest_path.read_bytes()
        try:
            path.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "bundle_changed"):
                grpo.load_bundle(self.bundle)
            rows = grpo.read_rows(self.original / "train.prompts.jsonl")
            rows = [{"id": r["id"], "prompt": upgrade(r["prompt"])} for r in rows]
            user = json.loads(rows[0]["prompt"][1]["content"])
            user["reference"] = "PRIVATE"
            rows[0]["prompt"][1]["content"] = canonical(user)
            path.write_bytes(grpo_bundle.rows_bytes(rows))
            manifest = json.loads(original_manifest)
            manifest["files"][path.name] = sha(path.read_bytes())
            manifest_path.write_text(canonical(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "private_fields_in_prompt"):
                grpo.load_bundle(self.bundle)
            user.pop("reference")
            user["task"] = "Wrong task with a valid public shape"
            rows[0]["prompt"][1]["content"] = canonical(user)
            path.write_bytes(grpo_bundle.rows_bytes(rows))
            manifest["files"][path.name] = sha(path.read_bytes())
            manifest_path.write_text(canonical(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "train_private_alignment"):
                grpo.load_bundle(self.bundle)
        finally:
            path.write_bytes(original)
            manifest_path.write_bytes(original_manifest)

    def test_stratified_pilot_and_batch_config(self):
        for limit in (100, 200, 1000):
            rows = grpo.select_train(self.prompts["train"], self.cases, limit)
            self.assertEqual(len(rows), limit)
            self.assertEqual(rows, grpo.select_train(list(reversed(self.prompts["train"])), self.cases, limit))
            self.assertEqual(Counter(self.cases[r["id"]]["family"] for r in rows),
                             Counter({f: limit // 25 for f in self.manifest["train_families"]}))
            self.assertFalse({r["id"] for r in rows} & {r["id"] for r in self.prompts["validation"]})
        cfg = grpo.training_config(Path("new-run"), 100)
        self.assertEqual(cfg["per_device_train_batch_size"] * cfg["gradient_accumulation_steps"], grpo.GROUP)
        self.assertEqual(cfg["num_generations"], 4)
        self.assertEqual(cfg["max_steps"], 100)
        self.assertEqual(cfg["num_iterations"], 1)
        self.assertFalse(cfg["use_vllm"])
        self.assertFalse(cfg["push_to_hub"])
        self.assertEqual(cfg["report_to"], [])

    def test_default_preflight_and_missing_approval_never_execute(self):
        for phase in ("before", "train", "after"):
            with patch("sys.argv", ["grpo.py", "--bundle", str(self.bundle), "--phase", phase]), \
                    patch.object(grpo, "execute") as execute, patch("builtins.print") as output:
                grpo.main()
                execute.assert_not_called()
                result = json.loads(output.call_args.args[0])
                self.assertEqual(result["status"], "preflight_only")
                self.assertFalse(result["gpu_loaded"])
                self.assertEqual(result["planned_completions"], 400 if phase == "train" else 200)
        with patch("sys.argv", ["grpo.py", "--bundle", str(self.bundle), "--phase", "train", "--execute",
                                "--model-dir", "unused", "--out", str(self.root / "unused"),
                                "--baseline-dir", "unused"]), patch.object(grpo, "execute") as execute:
            with self.assertRaisesRegex(ValueError, "acknowledge_synthetic"):
                grpo.main()
            execute.assert_not_called()

    def test_baseline_gate_requires_complete_v3_matching_run(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            predictions = [{"id": r["id"], "response": "fake generation for integrity test only"}
                           for r in self.prompts["validation"]]
            (path / "predictions.jsonl").write_bytes(grpo_bundle.rows_bytes(predictions))
            report = {**grpo.EVAL_SETTINGS, "status": "complete", "phase": "before", "prompt_version": grpo.VERSION,
                      "bundle_sha256": "test-hash", "selected": 200, "completed": 200, "generation_calls": 200,
                      "real_tool_calls": 0, "truncated_outputs": 0,
                      "predictions_sha256": grpo.file_sha(path / "predictions.jsonl")}
            (path / "run.json").write_text(canonical(report), encoding="utf-8")
            self.assertEqual(grpo.check_before(path, "test-hash", self.prompts["validation"]), report)
            for key, value in (("status", "failed_no_retry"), ("prompt_version", "v2"),
                               ("selected", 20), ("completed", 199), ("truncated_outputs", 1),
                               ("do_sample", True), ("predictions_sha256", "wrong")):
                with self.subTest(key=key):
                    changed = {**report, key: value}
                    (path / "run.json").write_text(canonical(changed), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "complete_same_prompt_baseline_required"):
                        grpo.check_before(path, "test-hash", self.prompts["validation"])

    def test_training_render_matches_evaluation_without_private_labels(self):
        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                self.test.assertFalse(kwargs["enable_thinking"])
                return "RENDERED PUBLIC PROMPT"

            def __call__(self, text, **kwargs):
                self.test.assertFalse(kwargs["add_special_tokens"])
                return {"input_ids": [1, 2, 3]}
        tokenizer = Tokenizer()
        tokenizer.test = self
        rows = self.prompts["train"][:2]
        result = grpo.training_records(tokenizer, rows, [[1, 2, 3], [1, 2, 3]])
        self.assertEqual(set(result[0]), {"prompt", "case_id"})
        with self.assertRaisesRegex(ValueError, "training_tokenization_differs"):
            grpo.training_records(tokenizer, rows, [[1, 2, 4], [1, 2, 3]])

    def test_reward_exact_scoring_alignment_and_zero_variance_stop(self):
        case = next(iter(self.cases.values()))
        identity = case["id"]
        meter = grpo.RewardMonitor({identity: case}, {identity: "PUBLIC"})
        answers = [case["reference"], "{}", canonical({"action": "finish", "reason": "no_tool_needed"}), case["reference"]]
        self.assertEqual(meter(answers, [identity] * 4, ["PUBLIC"] * 4), [score(x, case)["reward"] for x in answers])
        self.assertEqual(meter.summary()["groups_with_reward_variance"], 1)
        with self.assertRaisesRegex(ValueError, "reward_prompt_identity_mismatch"):
            meter(answers, [identity] * 4, ["PRIVATE"] * 4)
        with self.assertRaisesRegex(ValueError, "reward_batch_alignment"):
            meter(answers[:3], [identity] * 4, ["PUBLIC"] * 4)
        meter = grpo.RewardMonitor({identity: case}, {identity: "PUBLIC"})
        for _ in range(31):
            meter(["{}"] * 4, [identity] * 4, ["PUBLIC"] * 4)
        with self.assertRaisesRegex(ValueError, "no_learning_signal"):
            meter(["{}"] * 4, [identity] * 4, ["PUBLIC"] * 4)
        self.assertEqual(meter.summary()["scored_completions"], 128)

    def test_fake_trainer_bridge_has_no_tools_and_only_lora_is_saved(self):
        events = []
        case = next(iter(self.cases.values()))
        records = [{"case_id": case["id"], "prompt": "PUBLIC"}]
        model = SimpleNamespace(config=SimpleNamespace(use_cache=True))

        def prepare(value, **kwargs):
            events.append("prepare")
            self.assertIs(value, model)
            self.assertTrue(kwargs["use_gradient_checkpointing"])
            return value

        def attach(value, config):
            events.append("lora")
            self.assertEqual(config["target_modules"], ["q_proj", "v_proj"])
            return value

        test = self
        class Trainer:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.state = SimpleNamespace(global_step=0, log_history=[])
                test.assertNotIn("tools", kwargs)
                test.assertNotIn("eval_dataset", kwargs)
                test.assertEqual(kwargs["train_dataset"], records)

            def train(self, resume_from_checkpoint):
                events.append("train")
                test.assertIsNone(resume_from_checkpoint)
                result = self.kwargs["reward_funcs"](
                    [case["reference"], "{}", "{}", "{}"], [case["id"]] * 4, ["PUBLIC"] * 4)
                test.assertEqual(result, [1, -1, -1, -1])
                self.state.global_step = 1

            def save_model(self, path):
                events.append("save")
                Path(path).mkdir()
                (Path(path) / "adapter_model.safetensors").write_bytes(b"FAKE TEST ONLY")

        modules = {"datasets": SimpleNamespace(Dataset=SimpleNamespace(from_list=lambda x: x)),
                   "peft": SimpleNamespace(LoraConfig=lambda **kw: kw, prepare_model_for_kbit_training=prepare,
                                           get_peft_model=attach),
                   "trl": SimpleNamespace(GRPOConfig=lambda **kw: kw, GRPOTrainer=Trainer)}
        with tempfile.TemporaryDirectory() as temp, patch.dict("sys.modules", modules):
            report = {}
            grpo.train_adapter(model, SimpleNamespace(save_pretrained=lambda path: None), records,
                               {case["id"]: case}, SimpleNamespace(out=Path(temp), steps=1), report)
            self.assertEqual(events, ["prepare", "lora", "train", "save"])
            self.assertEqual(report["optimizer_steps"], 1)
            self.assertEqual(report["reward_monitor"]["scored_completions"], 4)
            self.assertFalse(model.config.use_cache)
            self.assertEqual(set(report["adapter_files_sha256"]), {"adapter_model.safetensors"})


if __name__ == "__main__":
    unittest.main()
