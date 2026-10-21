"""V2 data and cloud-runner behavior checked without GPU, torch, or network."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import baseline
import dataset
from cases import public_prompt
from contracts import canonical, sha
from rewards import score


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = dataset.build_cases()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.package = cls.root / "data"
        cls.report = dataset.build(cls.package)
        cls.archive = cls.root / "cloud.zip"
        dataset.export_cloud(cls.package, cls.archive)
        cls.cloud = cls.root / "cloud"
        with zipfile.ZipFile(cls.archive) as handle:
            handle.extractall(cls.cloud)

    def setUp(self):
        for owner, name in ((socket, "create_connection"), (socket, "getaddrinfo"), (socket.socket, "connect")):
            guard = patch.object(owner, name, side_effect=AssertionError("network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    def test_counts_determinism_separation_and_reference_mutations(self):
        self.assertEqual(self.data, dataset.build_cases())
        self.assertEqual({s: len(v) for s, v in self.data.items()}, {"train": 1000, "validation": 200, "blind_test": 200})
        self.assertTrue(all(v == 0 for v in self.report["cross_split_overlap"].values()))
        self.assertEqual(sum(self.report[s]["negative_checks"] for s in dataset.SPLITS), 8040)
        self.assertEqual(set(self.report["train"]["modes"]), {"web", "local", "hybrid"})
        self.assertTrue(all(count >= 100 for count in self.report["train"]["modes"].values()))
        for split in dataset.SPLITS:
            self.assertEqual(self.report[split]["reference_successes"], len(self.data[split]))
            self.assertIsNone(self.report[split]["model_accuracy"])
            self.assertEqual(len(self.report[split]["categories"]), 6)

    def test_overlap_is_detected_even_with_unique_row_ids(self):
        changed = deepcopy(self.data)
        changed["validation"][0]["topic"] = changed["train"][0]["topic"]
        with self.assertRaisesRegex(ValueError, "split_leakage_topic"):
            dataset.audit(changed)

    def test_permissions_and_candidate_disambiguation_are_observable(self):
        for split, cases in self.data.items():
            for c in cases:
                action = json.loads(c["reference"])
                if c["category"] == "read_paper":
                    target = next(p for p in c["context"]["candidates"] if p["paper_id"] == action["arguments"]["paper_id"])
                    self.assertIn(target["paper_id"], c["goal_ids"])
                    if "homonym" in c["family"]:
                        matches = [p for p in c["context"]["candidates"] if p["title"] == target["title"]]
                        self.assertEqual(len(matches), 2)
                        field = "year" if "year" in c["family"] else "provider"
                        self.assertEqual(len([p for p in matches if p[field] == target[field]]), 1)
                if "exclusion" in c["family"]:
                    args = action["arguments"]
                    key = "providers" if c["category"] == "search_papers" else "document_ids"
                    allowed = c["context"]["allowed_" + key]
                    omitted = set(allowed) - set(args[key])
                    self.assertEqual(len(omitted), 1)
                    self.assertIn(next(iter(omitted)), c["instruction"])
                    wrong = deepcopy(action)
                    wrong["arguments"][key] = allowed
                    self.assertFalse(score(canonical(wrong), c)["task_success"])
        # Result limits actually affect the mock result, not only a schema label.
        one = next(c for c in self.data["train"] if c["family"] == "search_limit_one")
        self.assertEqual(len(one["goal_ids"]), 1)
        self.assertEqual(sum(p["provider"] == json.loads(one["reference"])["arguments"]["providers"][0]
                             for p in one["world"]["papers"]), 3)

    def test_public_views_exclude_private_data_and_export_excludes_blind_set(self):
        for c in self.data["validation"]:
            changed = deepcopy(c)
            changed.update(reference="ANSWER_SECRET", goal_ids=["GOAL_SECRET"], world={"SECRET": 1})
            self.assertEqual(public_prompt(c), public_prompt(changed))
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(set(archive.namelist()), baseline.PUBLIC_FILES | {"bundle.json"})
            self.assertNotIn(b"\"reference\":", archive.read("validation.prompts.jsonl"))
            self.assertNotIn(b"human_reviewed", archive.read("validation.prompts.jsonl"))
        _, small = baseline.load_public(self.cloud)
        _, full = baseline.load_public(self.cloud, True)
        families = {c["id"]: c["family"] for c in self.data["validation"]}
        self.assertEqual(Counter(families[r["id"]] for r in small), Counter({f: 2 for f in dataset.FAMILIES["validation"]}))
        self.assertEqual(len(full), 200)

    def test_integrity_and_exclusive_creation(self):
        with self.assertRaises(FileExistsError):
            dataset.export_cloud(self.package, self.archive)
        path = self.cloud / "validation.prompts.jsonl"
        original = path.read_bytes()
        try:
            path.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "public_bundle_changed"):
                baseline.load_public(self.cloud)
        finally:
            path.write_bytes(original)

    def test_score_cannot_hide_partial_coverage_or_use_blind_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "predictions.jsonl"
            first = self.data["validation"][0]
            # Synthetic reference fixture is ONLY for checking the scorer, not model evidence.
            line = canonical({"id": first["id"], "response": first["reference"]}) + "\n"
            path.write_text(line, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "prediction_coverage"):
                dataset.evaluate(self.package, path, "validation")
            report = dataset.evaluate(self.package, path, "validation", True)
            self.assertEqual(report["coverage"], "1/200")
            self.assertTrue(report["subset_only"])
            self.assertEqual(report["overall"]["success_rate"], 1)
            path.write_text(line + line, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "prediction_coverage"):
                dataset.evaluate(self.package, path, "validation", True)
            with self.assertRaisesRegex(ValueError, "blind_test_reserved"):
                dataset.evaluate(self.package, path, "blind_test")

    def test_oversized_input_stops_before_generation_without_truncation(self):
        class Tokenizer:
            def __init__(self):
                self.seen = []

            def apply_chat_template(self, messages, **kwargs):
                self.seen.append((messages, kwargs))
                return [7] * (9 if len(self.seen) == 2 else 8)
        _, rows = baseline.load_public(self.cloud)
        tokenizer = Tokenizer()
        with self.assertRaisesRegex(ValueError, "input_token_limit_no_truncation"):
            baseline.encode_all(tokenizer, rows, 8)
        self.assertEqual(len(tokenizer.seen), 2)
        self.assertEqual(tokenizer.seen[0][1], {"tokenize": True, "add_generation_prompt": True, "enable_thinking": False})
        self.assertEqual(tokenizer.seen[0][0], rows[0]["prompt"])

    def test_default_cli_never_loads_gpu_or_downloads(self):
        with patch("sys.argv", ["baseline.py", "--bundle", str(self.cloud)]), patch.object(baseline, "execute") as execute, patch("builtins.print") as output:
            baseline.main()
        execute.assert_not_called()
        report = json.loads(output.call_args.args[0])
        self.assertEqual(report["generation_calls"], 0)
        self.assertEqual(report["status"], "preflight_only")

    def test_single_pass_keeps_raw_output_and_stops_on_failure(self):
        _, rows = baseline.load_public(self.cloud)
        raw = '  {"action":"finish","reason":"no_tool_needed"}\n'
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temp:
                calls = []
                def generate(i):
                    calls.append(i)
                    if fail and i == 1:
                        raise RuntimeError("not printed")
                    return raw, {"tokens_in": 3, "tokens_out": 4, "truncated": False}
                out = Path(temp) / "new"
                report = baseline.run_predictions(rows[:3], generate, out, {"fake_backend": True})
                self.assertEqual(calls, [0, 1] if fail else [0, 1, 2])
                self.assertEqual(report["completed"], 1 if fail else 3)
                self.assertEqual(report["status"], "failed_no_retry" if fail else "complete")
                predictions = [json.loads(line) for line in (out / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertTrue(all(row["response"] == raw for row in predictions))
                self.assertNotIn("not printed", (out / "run.json").read_text(encoding="utf-8"))
                with self.assertRaises(FileExistsError):
                    baseline.run_predictions(rows, generate, out, {})

    def test_model_inventory_requires_local_8b_safetensors(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            with self.assertRaisesRegex(ValueError, "safetensors_required"):
                baseline.model_inventory(directory)
            (directory / "model.safetensors").write_bytes(b"fake header: not loaded")
            config = {"model_type": "qwen3", "hidden_size": 4096, "num_hidden_layers": 36}
            (directory / "config.json").write_text(canonical(config), encoding="utf-8")
            result = baseline.model_inventory(directory)
            self.assertEqual(result["model.safetensors"], sha(b"fake header: not loaded"))
            config["hidden_size"] = 2048
            (directory / "config.json").write_text(canonical(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected_qwen3_8b_config"):
                baseline.model_inventory(directory)


if __name__ == "__main__":
    unittest.main()
