"""Offline integration tests; fake training/evaluation only, no GPU or downloads."""

from collections import Counter, defaultdict
from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from contracts import canonical, sha
import correction_data
import dataset
import grpo_once as once
import grpo_once_bundle as previous
from rewards import score
import rpo
import rpo_bundle
import rpo_corrected as corrected
import rpo_corrected_bundle as bundle
from test_rpo import Tokenizer


class CorrectedRPOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = dataset.build_cases()
        cls.old_cases = cls.original["train"] + [c for k in previous.PAIR_KINDS for i in range(20)
                                                 for c in previous.make_pair(k, i)]
        cls.old_pairs = rpo_bundle.preference_pairs(cls.old_cases)
        cls.new = correction_data.build_cases()
        cls.cases, cls.pairs = bundle.mixed_training(cls.old_cases, cls.old_pairs, cls.new["train"])

    def test_fixed_mixture_replay_contrasts_and_no_input_mutation(self):
        snapshot = canonical([self.old_cases, self.old_pairs, self.new])
        cases, pairs = bundle.mixed_training(list(reversed(self.old_cases)), list(reversed(self.old_pairs)),
                                            list(reversed(self.new["train"])))
        self.assertEqual((cases, pairs), (self.cases, self.pairs))
        self.assertEqual(canonical([self.old_cases, self.old_pairs, self.new]), snapshot)
        self.assertEqual((len(cases), len(pairs)), (1440, 2400))
        self.assertEqual(corrected.PLAN, rpo.PLAN)
        replay = [c for c in cases if not c["id"].startswith("correction:")]
        counts = Counter(Counter(c["family"] for c in replay).values())
        self.assertEqual(counts, {36: 20, 40: 10})
        sides = Counter(c["pair_id"] for c in replay if "pair_id" in c)
        self.assertEqual(set(sides.values()), {2})
        selected = {c["id"] for c in cases}
        self.assertFalse(selected & {c["id"] for c in self.new["probe"]})
        lookup = corrected.validate_training(cases, pairs)
        self.assertEqual(len(lookup), 1440)

    def test_raw_malformed_rejections_reach_tokens_unchanged_not_runtime_parser(self):
        c = next(c for c in self.new["train"] if c["scenario"] == "duplicate_paper_key")
        pair = next(p for p in self.pairs if p["case_id"] == c["id"])
        records, expected = rpo.training_records(Tokenizer(), {c["id"]: c}, [pair])
        self.assertEqual(records[0]["rejected"], c["rejected"])
        self.assertEqual(json.loads(c["rejected"]), json.loads(c["reference"]))
        self.assertEqual(score(c["rejected"], c)["code"], "invalid_json")
        self.assertEqual(expected[0]["rejected_input_ids"], [ord(s) for s in c["rejected"]] + [0])
        self.assertNotIn("rejected", json.loads(once.public_rows([c])[0]["prompt"][1]["content"]))
        self.assertIs(corrected.GPUBackend.train, rpo.GPUBackend.train)

    def test_changed_positive_negative_exposures_or_probe_in_training_are_rejected(self):
        for kind in ("chosen", "rejected", "code", "weights", "probe"):
            cases, pairs = deepcopy(self.cases), deepcopy(self.pairs)
            if kind == "chosen": pairs[0]["chosen"] = canonical({"action": "finish", "reason": "already_satisfied"})
            if kind == "rejected": pairs[0]["rejected"] = pairs[0]["chosen"]
            if kind == "code": pairs[0]["rejected_code"] = "invented"
            if kind == "weights": pairs[0]["case_id"] = pairs[1]["case_id"]
            if kind == "probe": cases[0]["split"] = "probe"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                corrected.validate_training(cases, pairs)

    def test_pipeline_early_stops_no_blind_on_dev_or_probe_failure(self):
        for stop in ("start", "train", "dev", "floor", "probe", "blind", None):
            calls = []
            class Backend:
                def verify_start(self):
                    calls.append("start")
                    if stop == "start": raise ValueError("canary_failed")
                def train(self):
                    calls.append("train")
                    if stop == "train": raise ValueError("train_failed")
                def validate(self):
                    calls.append("dev")
                    a = {"n": 200, "correct": 180, "unsafe": 3, "contract_errors": 4, "families": {"a": 18}}
                    b = {**a, "correct": 180 if stop == "dev" else 181, "unsafe": 0}
                    return a, b
                def historical_floor_passed(self):
                    calls.append("floor")
                    return stop != "floor"
                def correction_probe(self):
                    calls.append("probe")
                    a = {"n": 80, "correct": 70, "unsafe": 2, "contract_errors": 3, "families": {"a": 17}}
                    return a, {**a, "unsafe": 1 if stop == "probe" else 0}
                def blind(self):
                    calls.append("blind")
                    a = {"n": 200, "correct": 180, "unsafe": 3, "contract_errors": 4, "families": {"a": 18}}
                    return a, {**a, "correct": 179 if stop == "blind" else 181, "unsafe": 0}
            with self.subTest(stop=stop):
                if stop in ("start", "train"):
                    with self.assertRaises(ValueError): corrected.pipeline(Backend())
                else:
                    result = corrected.pipeline(Backend())
                    suffix = "correction_probe" if stop == "probe" else "dev" if stop == "floor" else stop
                    self.assertEqual(result, f"retain_v1_{suffix}_failed" if stop else "candidate_passed_offline_only")
                self.assertEqual(calls, ["start", "train", "dev", "floor", "probe", "blind"][:len(calls)])
                if stop:
                    self.assertEqual(calls[-1], stop)

    def test_probe_scores_use_exact_existing_scorer_and_each_nonregression_gate(self):
        cases = self.new["probe"]
        predictions = [{"id": c["id"], "response": c["reference"]} for c in cases]
        good = corrected.evaluate_probe(cases, predictions)
        self.assertEqual(good["correct"], 80)
        self.assertEqual(good["families"], {g: 20 for g in correction_data.GROUPS})
        self.assertTrue(corrected.probe_passed(good, good))
        predictions[0]["response"] = cases[0]["rejected"]
        worse = corrected.evaluate_probe(cases, predictions)
        self.assertEqual(worse["correct"], 79)
        self.assertFalse(corrected.probe_passed(good, worse))
        for field, value in (("unsafe", 1), ("contract_errors", 1), ("families", {"other": 80})):
            self.assertFalse(corrected.probe_passed(good, {**good, field: value}))
        with self.assertRaises(ValueError): corrected.evaluate_probe(cases, predictions[:-1])
        with self.assertRaises(ValueError): corrected.evaluate_probe(cases, list(reversed(predictions)))

    def test_bundle_roundtrip_review_binding_sealed_blind_preflight_and_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, corrections, out = root / "old", root / "corrections", root / "prepared"
            old.mkdir()
            corrections.mkdir()
            data = root / "tool-calling-v2"
            data.mkdir()
            entries = {"train.oracle.jsonl": bundle.rows_bytes(self.old_cases),
                       "preferences.jsonl": bundle.rows_bytes(self.old_pairs),
                       "evaluation/validation.jsonl": bundle.rows_bytes(self.original["validation"]),
                       "evaluation/blind_test.jsonl": b"sealed-blind-test-do-not-parse-during-preparation",
                       "v1-adapter/adapter_config.json": b"{}",
                       "v1-adapter/adapter_model.safetensors": b"unit-test-only-never-load"}
            predictions = [{"id": c["id"], "response": c["reference"] if i < 180 else "invalid-json"}
                           for i, c in enumerate(self.original["validation"])]
            entries["v1-validation.predictions.jsonl"] = bundle.rows_bytes(predictions)
            for name, raw in entries.items():
                path = old / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            old_manifest = {"plan": rpo.PLAN, "sample_rule_review": {"status": "approved_with_deterministic_corrections"},
                            "files": {n: sha(raw) for n, raw in entries.items()}, "versions": {}, "model_files_sha256": {},
                            "gpu": "NVIDIA GeForce RTX 4090", "v1_adapter_sha256": {}, "v1_validation": {"n": 200, "correct": 180}}
            (old / "rpo-bundle.json").write_text(canonical(old_manifest), encoding="utf-8")
            new_entries = {"train.oracle.jsonl": bundle.rows_bytes(self.new["train"]),
                           "probe.oracle.jsonl": bundle.rows_bytes(self.new["probe"]),
                           "probe.prompts.jsonl": bundle.rows_bytes({"id": c["id"]} for c in self.new["probe"])}
            for name, raw in new_entries.items(): (corrections / name).write_bytes(raw)
            (corrections / "manifest.json").write_text(canonical({"files_sha256": {n: sha(raw) for n, raw in new_entries.items()}}), encoding="utf-8")
            for split in ("validation", "blind_test"):
                (data / f"{split}.prompts.jsonl").write_bytes(bundle.rows_bytes({"id": c["id"]} for c in self.original[split]))
            protected = {p: sha(p.read_bytes()) for p in root.rglob("*") if p.is_file()}
            with self.assertRaisesRegex(ValueError, "explicit_sample_review"):
                bundle.build(old, corrections, out, False)
            result = bundle.build(old, corrections, out, True)
            m, cases, pairs = corrected.load_bundle(out)
            self.assertEqual((len(cases), len(pairs)), (1440, 2400))
            self.assertEqual(m["review"]["user_statement"], "审核完成")
            self.assertEqual({p: sha(p.read_bytes()) for p in protected}, protected)
            with patch.object(corrected, "worker") as worker, patch.object(once, "supervise") as supervisor:
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    corrected.main(["--bundle", str(out)])
                self.assertEqual(json.loads(stdout.getvalue())["status"], "preflight_only")
                with self.assertRaisesRegex(ValueError, "explicit_data_review"):
                    corrected.main(["--bundle", str(out), "--execute"])
                worker.assert_not_called()
                supervisor.assert_not_called()
            with zipfile.ZipFile(result["zip"]) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(set(z.namelist()), corrected.RUNTIME | corrected.ARTIFACTS | {"corrected-bundle.json"})
            with self.assertRaisesRegex(ValueError, "new_corrected_output"):
                bundle.build(old, corrections, out, True)
            adapted = root / "prepared-5090"
            bundle.build(old, corrections, adapted, True, "NVIDIA GeForce RTX 5090")
            adapted_manifest, _, _ = corrected.load_bundle(adapted)
            self.assertEqual(adapted_manifest["gpu_capability"], [12, 0])
            self.assertEqual(adapted_manifest["historical_gpu"], "NVIDIA GeForce RTX 4090")
            self.assertEqual(adapted_manifest["baseline_policy"], corrected.BASELINE_POLICY)
            # Hardware support must not regenerate labels, alter the plan or v1.
            for name in corrected.ARTIFACTS - {"CLOUD_RPO_CORRECTED.txt"}:
                self.assertEqual((out / name).read_bytes(), (adapted / name).read_bytes(), name)
            self.assertEqual(adapted_manifest["plan"], m["plan"])
            for field, value in (("gpu_capability", [8, 9]), ("baseline_policy", "skip"), ("gpu", "unverified")):
                (adapted / "corrected-bundle.json").write_text(canonical({**adapted_manifest, field: value}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "explicit_hardware"):
                    corrected.load_bundle(adapted)
            (out / "preferences.jsonl").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "bundle_changed"):
                corrected.load_bundle(out)

    def test_same_gpu_full_baseline_precedes_training_and_old_floor_is_not_relaxed(self):
        cases = self.original["validation"]
        def predictions(correct):
            return [{"id": c["id"], "response": c["reference"] if i < correct else "invalid-json"}
                    for i, c in enumerate(cases)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "evaluation").mkdir()
            (root / "evaluation/validation.jsonl").write_bytes(bundle.rows_bytes(cases))
            (root / "v1-validation.predictions.jsonl").write_bytes(bundle.rows_bytes(predictions(180)))
            for baseline_n, after_n in ((182, 181), (178, 179), (179, 181)):
                backend = object.__new__(corrected.GPUBackend)
                backend.bundle = root
                backend.out = root / str(baseline_n)
                backend.out.mkdir()
                backend.report = {"optimizer_steps": 0}
                backend.flush = lambda: None
                calls = []
                def generate(rows, stage, adapter):
                    self.assertEqual(rows, cases)
                    self.assertEqual(adapter, "policy")
                    calls.append(stage)
                    self.assertEqual(backend.report["optimizer_steps"], 0 if stage.endswith("current-gpu") else 300)
                    return predictions(baseline_n if stage.endswith("current-gpu") else after_n)
                backend.predictions = generate
                with self.assertRaisesRegex(ValueError, "current_gpu_baseline_required"):
                    backend.validate()
                backend.verify_start()
                self.assertEqual(backend.report["current_gpu_v1_correct"], baseline_n)
                backend.report["optimizer_steps"] = 300
                before, after = backend.validate()
                self.assertEqual((before["correct"], after["correct"]), (baseline_n, after_n))
                self.assertEqual(rpo.dev_passed(before, after), after_n > baseline_n)
                self.assertEqual(backend.historical_floor_passed(), after_n > 180)
                self.assertEqual(calls, ["validation-v1-current-gpu", "validation"])
                with self.assertRaisesRegex(ValueError, "baseline_must_precede_training"):
                    backend.verify_start()
                scores = json.loads((backend.out / "validation-scores.json").read_text())
                self.assertEqual(scores["v1"]["correct"], baseline_n)
                self.assertEqual(scores["historical_4090"]["correct"], 180)

    def test_model_preparation_reuses_verified_files_and_never_overwrites_or_trains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_bytes(b"verified")
            expected = {"config.json": sha(b"verified"), "tokenizer.json": sha(b"missing")}
            manifest = {"versions": {}, "model_files_sha256": expected, "model_revision": corrected.MODEL_REVISION}
            calls = []
            def download(**kwargs):
                calls.append(kwargs)
                self.assertEqual(kwargs["allow_patterns"], ["tokenizer.json"])
                (root / "tokenizer.json").write_bytes(b"missing")
            with patch.object(corrected, "versions", return_value={}), \
                 patch.object(corrected, "model_inventory", return_value=expected), \
                 patch.dict(sys.modules, {"huggingface_hub": SimpleNamespace(snapshot_download=download)}), \
                 patch.object(corrected, "worker") as worker, contextlib.redirect_stdout(io.StringIO()):
                corrected.prepare_model(manifest, root)
                corrected.prepare_model(manifest, root)
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["revision"], corrected.MODEL_REVISION)
                self.assertIs(calls[0]["token"], False)
                (root / "config.json").write_bytes(b"wrong")
                with self.assertRaisesRegex(ValueError, "no_overwrite"):
                    corrected.prepare_model(manifest, root)
                self.assertEqual(len(calls), 1)
                self.assertEqual((root / "config.json").read_bytes(), b"wrong")
                worker.assert_not_called()

    def test_hardware_gate_checks_real_name_capability_count_and_bf16_before_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = {"review": {}, "versions": {}, "model_files_sha256": {},
                        "gpu": "NVIDIA GeForce RTX 5090", "gpu_capability": [12, 0]}
            root = Path(tmp)
            (root / "corrected-bundle.json").write_bytes(b"test")
            actual = {"name": "NVIDIA GeForce RTX 5090", "capability": (12, 0), "count": 1, "bf16": True}
            cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: actual["count"],
                is_bf16_supported=lambda: actual["bf16"], get_device_name=lambda _: actual["name"],
                get_device_capability=lambda _: actual["capability"])
            tokenizer = SimpleNamespace(from_pretrained=lambda *a, **kw: (_ for _ in ()).throw(ValueError("tokenizer_reached")))
            transformers = SimpleNamespace(AutoModelForCausalLM=None, AutoTokenizer=tokenizer,
                                           BitsAndBytesConfig=None, set_seed=None)
            with patch.object(corrected, "load_bundle", return_value=(manifest, {}, [])), \
                 patch.object(corrected, "versions", return_value={}), \
                 patch.object(corrected, "model_inventory", return_value={}), \
                 patch.object(corrected, "offline_environment"), \
                 patch.dict(os.environ, {"WORLD_SIZE": "1"}), \
                 patch.dict(sys.modules, {"torch": SimpleNamespace(cuda=cuda), "transformers": transformers}):
                for field, value in (("name", "NVIDIA GeForce RTX 4090"), ("capability", (8, 9)),
                                     ("count", 2), ("bf16", False)):
                    old = actual[field]
                    actual[field] = value
                    with self.assertRaisesRegex(ValueError, "matching_single_gpu_required"):
                        corrected.GPUBackend(root, root, root, 0)
                    actual[field] = old
                with self.assertRaisesRegex(ValueError, "tokenizer_reached"):
                    corrected.GPUBackend(root, root, root, 0)

    def test_import_and_default_path_do_not_load_gpu_libraries(self):
        result = subprocess.run([sys.executable, "-B", "-c",
            "import sys; import rpo_corrected; assert not ({'torch','transformers','trl','peft'} & sys.modules.keys())"],
            cwd=Path(__file__).parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
