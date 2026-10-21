"""CPU-only SFT preparation checks; fake tokenization, no model or network."""

from collections import Counter
from copy import deepcopy
import importlib
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import dataset
import fetch_sft_model
import grpo
import grpo_bundle
import sft
import sft_bundle
from contracts import canonical, sha


class FakeTokenizer:
    eos_token_id = 2
    all_special_ids = [0, 1, 2]

    def __init__(self):
        self.prefix = [10, 11, 12]
        self.template_calls = []

    def apply_chat_template(self, messages, **kwargs):
        self.template_calls.append((deepcopy(messages), kwargs))
        return list(self.prefix)

    def encode(self, text, **kwargs):
        if kwargs != {"add_special_tokens": False}:
            raise AssertionError("unexpected tokenizer options")
        return [ord(c) + 1000 for c in text]

    def decode(self, ids, **kwargs):
        if kwargs != {"skip_special_tokens": False, "clean_up_tokenization_spaces": False}:
            raise AssertionError("unexpected decode options")
        return "".join(chr(i - 1000) for i in ids)


class SftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.original = cls.root / "dataset"
        dataset.build(cls.original)
        old_zip = cls.root / "grpo.zip"
        grpo_bundle.export(cls.original, old_zip)
        cls.grpo_dir = cls.root / "grpo"
        with zipfile.ZipFile(old_zip) as archive:
            archive.extractall(cls.grpo_dir)
        old_manifest, prompts, _ = grpo.load_bundle(cls.grpo_dir)
        cls.before = cls.root / "before"
        cls.before.mkdir()
        references = {r["id"]: r["reference"] for r in grpo.read_rows(cls.original / "validation.oracle.jsonl")}
        predictions = [{"id": r["id"], "response": references[r["id"]]} for r in prompts["validation"]]
        (cls.before / "predictions.jsonl").write_bytes(grpo_bundle.rows_bytes(predictions))
        before = {
            **grpo.EVAL_SETTINGS, "status": "complete", "phase": "before", "prompt_version": grpo.VERSION,
            "bundle_sha256": sha((cls.grpo_dir / "grpo-bundle.json").read_bytes()),
            "selected": 200, "completed": 200, "generation_calls": 200, "real_tool_calls": 0,
            "truncated_outputs": 0, "predictions_sha256": grpo.file_sha(cls.before / "predictions.jsonl"),
            "model_files_sha256": {"test-model.safetensors": "fake-offline-fixture"},
            "versions": {"torch": "test-only"}, "gpu": "fake-offline-fixture",
        }
        (cls.before / "run.json").write_text(canonical(before), encoding="utf-8")
        cls.protected = {
            str(p): grpo.file_sha(p)
            for directory in (cls.original, cls.grpo_dir, cls.before)
            for p in directory.rglob("*") if p.is_file()
        }
        cls.bundle = cls.root / "sft"
        cls.archive = cls.root / "sft.zip"
        cls.exported = sft_bundle.export(cls.original, cls.grpo_dir, cls.before, cls.bundle, cls.archive)
        cls.manifest, cls.train, cls.validation, cls.before_report = sft.load_bundle(cls.bundle)

    def setUp(self):
        for owner, name in ((socket, "create_connection"), (socket, "getaddrinfo"), (socket.socket, "connect")):
            guard = patch.object(owner, name, side_effect=AssertionError("network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_bundle_is_balanced_disjoint_and_preserves_originals(self):
        self.assertEqual(len(self.train), 200)
        self.assertEqual(len(self.validation), 200)
        self.assertEqual(Counter(r["family"] for r in self.train),
                         Counter({f: 8 for f in self.manifest["train_families"]}))
        self.assertFalse({r["id"] for r in self.train} & {r["id"] for r in self.validation})
        _, old_prompts, cases = grpo.load_bundle(self.grpo_dir)
        expected = grpo.select_train(old_prompts["train"], cases, 200)
        self.assertEqual([r["id"] for r in self.train], [r["id"] for r in expected])
        for row, old in zip(self.train, expected):
            self.assertEqual(row["prompt"], old["prompt"])
            self.assertEqual(row["completion"], cases[row["id"]]["reference"])
            self.assertEqual(set(json.loads(row["prompt"][1]["content"])), {"task", "context", "tools"})
        self.assertEqual(self.validation, old_prompts["validation"])
        self.assertEqual({name: grpo.file_sha(Path(name)) for name in self.protected}, self.protected)
        self.assertEqual(self.exported["optimizer_steps"], 0)
        self.assertEqual(self.exported["generation_calls"], 0)
        self.assertFalse(self.exported["human_reviewed"])
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(set(archive.namelist()), sft.FILES | {"sft-bundle.json"})
            self.assertFalse(any("oracle" in name or "blind" in name for name in archive.namelist()))
            self.assertEqual(archive.read("validation.prompts.jsonl"),
                             (self.grpo_dir / "validation.prompts.jsonl").read_bytes())
        with self.assertRaisesRegex(ValueError, "new_bundle_directory_and_zip_required"):
            sft_bundle.export(self.original, self.grpo_dir, self.before, self.bundle, self.archive)

    def test_bundle_hash_and_semantic_tampering_fail(self):
        path = self.bundle / "train.sft.jsonl"
        manifest_path = self.bundle / "sft-bundle.json"
        original, original_manifest = path.read_bytes(), manifest_path.read_bytes()
        try:
            path.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "sft_bundle_changed"):
                sft.load_bundle(self.bundle)
            for mutation, expected in (
                ("private_prompt", "private_fields_in_prompt"),
                ("noncanonical_answer", "canonical_training_answer_required"),
                ("overlap", "invalid_prompt_or_split_overlap"),
                ("family", "invalid_train_family"),
                ("invalid_answer", ""),
            ):
                rows = deepcopy(self.train)
                if mutation == "private_prompt":
                    user = json.loads(rows[0]["prompt"][1]["content"])
                    user["reference"] = "not a public input"
                    rows[0]["prompt"][1]["content"] = canonical(user)
                elif mutation == "noncanonical_answer":
                    rows[0]["completion"] = " " + rows[0]["completion"]
                elif mutation == "overlap":
                    rows[0]["id"] = self.validation[0]["id"]
                elif mutation == "family":
                    rows[0]["family"] = "unknown"
                else:
                    rows[0]["completion"] = "{}"
                path.write_bytes(grpo_bundle.rows_bytes(rows))
                manifest = json.loads(original_manifest)
                manifest["files"][path.name] = grpo.file_sha(path)
                manifest_path.write_text(canonical(manifest), encoding="utf-8")
                with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, expected):
                    sft.load_bundle(self.bundle)
        finally:
            path.write_bytes(original)
            manifest_path.write_bytes(original_manifest)

    def test_answer_only_mask_preserves_eos_when_pad_equals_eos(self):
        rows = [deepcopy(self.train[0]), deepcopy(self.train[1])]
        rows[0]["completion"] = canonical({"action": "finish", "reason": "no_tool_needed"})
        rows[1]["completion"] = canonical({"action": "clarify", "reason": "permission_required"})
        tokenizer = FakeTokenizer()
        records = sft.encode_training(tokenizer, rows)
        self.assertEqual(len(tokenizer.template_calls), 2)
        for row, record, (messages, options) in zip(rows, records, tokenizer.template_calls):
            self.assertEqual(messages, row["prompt"])
            self.assertEqual(options, {"tokenize": True, "add_generation_prompt": True, "enable_thinking": False})
            self.assertEqual(set(record), {"input_ids", "attention_mask", "labels"})
            self.assertEqual(record["labels"][:3], [-100] * 3)
            self.assertEqual(record["labels"][3:-1], tokenizer.encode(row["completion"], add_special_tokens=False))
            self.assertEqual(record["labels"][-1], tokenizer.eos_token_id)
        batch = sft.pad_records(records, tokenizer.eos_token_id)
        self.assertEqual(len(batch["input_ids"][0]), len(batch["input_ids"][1]))
        for original, labels, attention in zip(records, batch["labels"], batch["attention_mask"]):
            end = len(original["input_ids"])
            self.assertEqual(labels[end - 1], tokenizer.eos_token_id)
            self.assertTrue(all(value == -100 for value in labels[end:]))
            self.assertTrue(all(value == 0 for value in attention[end:]))
        invalid = deepcopy(records[0])
        invalid["labels"] = [-100] * len(invalid["input_ids"])
        with self.assertRaisesRegex(ValueError, "invalid_training_record"):
            sft.pad_records([invalid], tokenizer.eos_token_id)

    def test_token_boundaries_reject_without_truncation(self):
        row = deepcopy(self.train[0])
        row["completion"] = canonical({"action": "finish", "reason": "no_tool_needed"})
        tokenizer = FakeTokenizer()
        tokenizer.prefix = [10] * sft.MAX_INPUT
        record = sft.encode_training(tokenizer, [row])[0]
        self.assertEqual(record["labels"][:sft.MAX_INPUT], [-100] * sft.MAX_INPUT)
        tokenizer.prefix.append(10)
        with self.assertRaisesRegex(ValueError, "input_token_limit_no_truncation"):
            sft.encode_training(tokenizer, [row])
        tokenizer.prefix = [10]
        with patch.object(tokenizer, "encode", return_value=[20] * (sft.MAX_OUTPUT - 1)), \
                patch.object(tokenizer, "decode", return_value=row["completion"]):
            self.assertEqual(len(sft.encode_training(tokenizer, [row])[0]["labels"]), sft.MAX_OUTPUT + 1)
        with patch.object(tokenizer, "encode", return_value=[20] * sft.MAX_OUTPUT), \
                patch.object(tokenizer, "decode", return_value=row["completion"]):
            with self.assertRaisesRegex(ValueError, "answer_token_limit_no_truncation"):
                sft.encode_training(tokenizer, [row])
        for tokens in ([], [2], [True], (20,)):
            with self.subTest(tokens=tokens), patch.object(tokenizer, "encode", return_value=tokens):
                with self.assertRaisesRegex(ValueError, "answer_tokenization_changed"):
                    sft.encode_training(tokenizer, [row])
        with patch.object(tokenizer, "decode", return_value="altered"):
            with self.assertRaisesRegex(ValueError, "answer_tokenization_changed"):
                sft.encode_training(tokenizer, [row])
        with patch.object(tokenizer, "eos_token_id", [2]):
            with self.assertRaisesRegex(ValueError, "single_eos_required"):
                sft.encode_training(tokenizer, [row])

    def test_preflight_imports_no_gpu_stack_and_execution_requires_opt_in(self):
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in {"torch", "transformers", "peft", "trl", "datasets", "bitsandbytes"}:
                raise AssertionError("GPU stack imported during preflight")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            importlib.reload(sft)
            for phase, steps in (("smoke", 2), ("train", 50), ("after", 0)):
                with patch("sys.argv", ["sft.py", "--bundle", str(self.bundle), "--phase", phase]), \
                        patch.object(sft, "execute") as execute, patch("builtins.print") as output:
                    sft.main()
                    execute.assert_not_called()
                    report = json.loads(output.call_args.args[0])
                    self.assertEqual(report["status"], "preflight_only")
                    self.assertFalse(report["gpu_loaded"])
                    self.assertFalse(report["model_downloaded"])
                    self.assertEqual(report["planned_optimizer_steps"], steps)
                    self.assertEqual(report["real_tool_calls"], 0)
        for phase in ("smoke", "train"):
            with patch("sys.argv", ["sft.py", "--bundle", str(self.bundle), "--phase", phase, "--execute",
                                    "--model-dir", "unused", "--out", str(self.root / "not-created")]), \
                    patch.object(sft, "execute") as execute:
                with self.assertRaisesRegex(ValueError, "acknowledge_synthetic_data_before_training"):
                    sft.main()
                execute.assert_not_called()

    def test_single_epoch_config_and_training_evidence(self):
        for phase, steps, examples in (("smoke", 2, 8), ("train", 50, 200)):
            cfg = sft.training_config(self.root / "new-run", phase)
            self.assertEqual(cfg["num_train_epochs"], 1)
            self.assertEqual(cfg["per_device_train_batch_size"], 1)
            self.assertEqual(cfg["gradient_accumulation_steps"], 4)
            self.assertEqual(cfg["max_steps"], 2 if phase == "smoke" else -1)
            self.assertEqual(cfg["save_strategy"], "no")
            self.assertEqual(cfg["eval_strategy"], "no")
            self.assertFalse(cfg["push_to_hub"])
            self.assertEqual(cfg["report_to"], [])
            valid = {"optimizer_steps": steps, "examples_seen": examples, "losses": [0.5] * examples,
                     "grad_norms": [0.1] * steps, "adapter_changed": True}
            sft.validate_training(valid, phase)
            for key, value in (("optimizer_steps", steps - 1), ("examples_seen", examples - 1),
                               ("losses", [float("nan")] * examples), ("losses", [-1.0] * examples),
                               ("grad_norms", [float("inf")] * steps), ("grad_norms", [0.0] * steps),
                               ("adapter_changed", False)):
                with self.subTest(phase=phase, key=key), self.assertRaisesRegex(ValueError, "incomplete_or_nonfinite_training"):
                    sft.validate_training({**valid, key: value}, phase)
        with self.assertRaisesRegex(ValueError, "invalid_training_phase"):
            sft.training_config(self.root, "after")

    def test_prior_requires_complete_matching_smoke_or_train_and_intact_adapter(self):
        metadata = {"bundle_sha256": "bundle", "model_files_sha256": {"weights": "sha"},
                    "versions": {"torch": "test"}, "gpu": "test", "prompt_version": grpo.VERSION}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            run = directory / "run.json"
            report = {
                **metadata, "status": "complete", "phase": "smoke", "selected": 200, "real_tool_calls": 0,
                "settings": sft.SFT_SETTINGS, "config": sft.training_config(directory, "smoke"),
                "optimizer_steps": 2, "examples_seen": 8, "losses": [0.5] * 8,
                "grad_norms": [0.1] * 2, "adapter_changed": True,
            }
            run.write_text(canonical(report), encoding="utf-8")
            self.assertEqual(sft.check_prior(directory, "smoke", metadata), report)
            for key, value in (("status", "interrupted_no_retry"), ("phase", "train"),
                               ("bundle_sha256", "different"), ("gpu", "different"),
                               ("versions", {}), ("settings", {}), ("adapter_changed", False),
                               ("config", {**report["config"], "learning_rate": 1.0})):
                run.write_text(canonical({**report, key: value}), encoding="utf-8")
                with self.subTest(key=key), self.assertRaises(ValueError):
                    sft.check_prior(directory, "smoke", metadata)
            report.update(phase="train", config=sft.training_config(directory, "train"), optimizer_steps=50,
                          examples_seen=200, losses=[0.5] * 200, grad_norms=[0.1] * 50)
            adapter = directory / "adapter"
            adapter.mkdir()
            (adapter / "adapter_model.safetensors").write_bytes(b"FAKE test adapter, not real weights")
            (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
            report["adapter_files_sha256"] = {p.name: grpo.file_sha(p) for p in adapter.iterdir()}
            run.write_text(canonical(report), encoding="utf-8")
            self.assertEqual(sft.check_prior(directory, "train", metadata), report)
            (adapter / "adapter_model.safetensors").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "trained_adapter_changed"):
                sft.check_prior(directory, "train", metadata)
        with self.assertRaisesRegex(ValueError, "prior_run_required"):
            sft.check_prior(None, "smoke", metadata)

    def test_local_scoring_reuses_unchanged_labels_and_identifies_sft_only(self):
        with tempfile.TemporaryDirectory() as temp:
            train_dir = Path(temp) / "train"
            adapter = train_dir / "adapter"
            adapter.mkdir(parents=True)
            (adapter / "adapter_model.safetensors").write_bytes(b"FAKE test adapter, not real weights")
            (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
            hashes = {p.name: grpo.file_sha(p) for p in adapter.iterdir()}
            trained = {
                **self.before_report, "phase": "train", "method": "sft-only", "optimizer_steps": 50,
                "bundle_sha256": grpo.file_sha(self.bundle / "sft-bundle.json"),
                "settings": sft.SFT_SETTINGS, "config": sft.training_config(train_dir, "train"),
                "examples_seen": 200, "losses": [0.5] * 200, "grad_norms": [0.1] * 50,
                "adapter_changed": True, "adapter_files_sha256": hashes,
            }
            (train_dir / "run.json").write_text(canonical(trained), encoding="utf-8")
            after_dir = Path(temp) / "after"
            after_dir.mkdir()
            predictions = (self.before / "predictions.jsonl").read_bytes()
            (after_dir / "predictions.jsonl").write_bytes(predictions)
            after = {
                **self.before_report, "phase": "after", "method": "sft-only", "optimizer_steps": 0,
                "bundle_sha256": grpo.file_sha(self.bundle / "sft-bundle.json"),
                "adapter_files_sha256": hashes, "train_run_sha256": grpo.file_sha(train_dir / "run.json"),
            }
            (after_dir / "run.json").write_text(canonical(after), encoding="utf-8")
            output = Path(temp) / "comparison.json"
            report = sft_bundle.score_results(self.original, self.bundle, after_dir, train_dir, output)
            self.assertEqual(report["before_successes"], 200)
            self.assertEqual(report["after_successes"], 200)
            self.assertEqual(report["paired"], {"correct_to_correct": 200})
            self.assertEqual(report["method"], "sft-only")
            self.assertEqual(report["success_rate_change"], 0)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(saved["grpo_gain"], "not_measured_no_grpo_training_in_this_bundle")
            after["phase"] = "train"
            (after_dir / "run.json").write_text(canonical(after), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "complete_comparable_sft_after_required"):
                sft_bundle.score_results(self.original, self.bundle, after_dir, train_dir, Path(temp) / "wrong.json")

    def test_model_fetch_preflight_never_downloads_and_snapshot_must_match(self):
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in {"huggingface_hub", "torch", "transformers"}:
                raise AssertionError("model fetch imported during preflight")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import), \
                patch("sys.argv", ["fetch_sft_model.py", "--bundle", str(self.bundle),
                                   "--model-dir", str(self.root / "not-downloaded")]), \
                patch("builtins.print") as output:
            fetch_sft_model.main()
            report = json.loads(output.call_args.args[0])
            self.assertEqual(report["status"], "preflight_only")
            self.assertFalse(report["downloaded"])
            self.assertFalse(report["gpu_loaded"])
            self.assertFalse((self.root / "not-downloaded").exists())
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp)
            (model / "config.json").write_text(canonical({"model_type": "qwen3", "hidden_size": 4096,
                                                          "num_hidden_layers": 36}), encoding="utf-8")
            weights = model / "model.safetensors"
            weights.write_bytes(b"FAKE snapshot, no actual model")
            inventory = grpo.model_inventory(model)
            self.assertEqual(fetch_sft_model.verify_model(model, inventory), inventory)
            weights.write_bytes(b"different fake snapshot")
            with self.assertRaisesRegex(ValueError, "download_does_not_match_existing_baseline_stop"):
                fetch_sft_model.verify_model(model, inventory)


if __name__ == "__main__":
    unittest.main()
