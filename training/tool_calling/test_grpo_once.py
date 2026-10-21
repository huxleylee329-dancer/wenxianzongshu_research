"""CPU-only checks for the finite experiment, labels, adapter identity and cost gates."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
import json
import math
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from contracts import canonical
import dataset
import grpo_once as once
import grpo_once_bundle as bundle
from rewards import score


class Tensor:
    def __init__(self, value, requires_grad=False):
        self.value, self.requires_grad, self.device = value, requires_grad, "fake-cpu"

    @property
    def data(self):
        return self

    @data.setter
    def data(self, value):
        self.value = value.value

    def requires_grad_(self, value):
        self.requires_grad = value
        return self

    def detach(self): return self
    def float(self): return self
    def cpu(self): return self
    def contiguous(self): return self
    def clone(self): return Tensor(self.value)
    def to(self, **kwargs): return self.clone()
    def numpy(self): return SimpleNamespace(tobytes=lambda: struct.pack("f", self.value))


class Model:
    def __init__(self):
        self.active_adapter = "policy"
        self.config = SimpleNamespace(use_cache=True)
        self.parameters = {"layer.lora_A.policy.weight": Tensor(1, True),
                           "layer.lora_A.reference.weight": Tensor(1), "base.weight": Tensor(3)}
        self.forward_adapters = []
        self.generation_config = SimpleNamespace(do_sample=False, max_new_tokens=384, num_beams=1)
        self.calls = 0

    def named_parameters(self): return list(self.parameters.items())
    def set_adapter(self, name):
        self.active_adapter = name
        for key, parameter in self.parameters.items():
            parameter.requires_grad_(f".{name}." in key)
    def train(self): return self
    def __call__(self, **kwargs):
        self.forward_adapters.append(self.active_adapter)
        return self.active_adapter
    def generate(self, *args, **kwargs):
        self.calls += 1
        return kwargs
    def save_pretrained(self, destination, **kwargs):
        if kwargs != {"selected_adapters": ["policy"], "safe_serialization": True}:
            raise AssertionError("only policy must be saved")
        path = Path(destination) / "policy"
        path.mkdir(parents=True)
        (path / "adapter_model.safetensors").write_bytes(b"fake-unit-test-not-model-weights")


class NoGrad:
    def __enter__(self): pass
    def __exit__(self, *args): pass


class Module:
    def __call__(self, **kwargs): return self.forward(**kwargs)


TORCH = SimpleNamespace(isfinite=lambda x: SimpleNamespace(all=lambda: SimpleNamespace(item=lambda: math.isfinite(x.value))),
                        no_grad=NoGrad, nn=SimpleNamespace(Module=Module),
                        cuda=SimpleNamespace(max_memory_allocated=lambda: 0, max_memory_reserved=lambda: 0,
                                             empty_cache=lambda: None))


class OnceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = dataset.build_cases()
        cls.pairs = [c for kind in bundle.PAIR_KINDS for index in range(20) for c in bundle.make_pair(kind, index)]

    def test_paired_constraints_and_full_old_coverage(self):
        audit = bundle.audit_pairs(self.pairs, self.original)
        self.assertEqual(audit["reference_successes"], 200)
        self.assertEqual(audit["negative_checks"], 1000)
        for left, right in zip(self.pairs[::2], self.pairs[1::2]):
            self.assertNotEqual(left["reference"], right["reference"])
            # The opposite side's answer cannot earn reward without its matching condition.
            self.assertNotEqual(score(left["reference"], right)["reward"], 1)
            self.assertNotEqual(score(right["reference"], left)["reward"], 1)
        pool = self.original["train"] + self.pairs
        self.assertEqual(Counter(c["family"] for c in pool if not c["family"].startswith("paired_")),
                         Counter({f: 40 for f in dataset.FAMILIES["train"]}))
        bad = deepcopy(self.pairs)
        bad[0]["id"] = self.original["validation"][0]["id"]
        with self.assertRaises(ValueError): bundle.audit_pairs(bad, self.original)
        bad = deepcopy(self.pairs)
        bad[0]["reference"] = bad[1]["reference"]
        with self.assertRaises(ValueError): bundle.audit_pairs(bad, self.original)

    def test_fixed_selections_and_no_private_training_columns(self):
        train, signal = bundle.select_ids(self.original["train"], self.pairs)
        self.assertEqual((len(train), len(signal)), (50, 32))
        self.assertFalse(set(train) & set(signal))
        lookup = {c["id"]: c for c in self.original["train"] + self.pairs}
        self.assertEqual(sum(i.startswith("once:") for i in train), 20)
        self.assertEqual(sum(i.startswith("once:") for i in signal), 20)
        self.assertEqual({lookup[i]["family"] for i in train if not i.startswith("once:")},
                         set(dataset.FAMILIES["train"]))
        self.assertEqual(bundle.select_ids(list(reversed(self.original["train"])), self.pairs), (train, signal))
        for r in once.public_rows(self.pairs):
            self.assertEqual(set(json.loads(r["prompt"][1]["content"])), {"task", "context", "tools"})
            self.assertNotIn("reference", r["prompt"][1]["content"])
            self.assertNotIn("goal_ids", r["prompt"][1]["content"])

    @staticmethod
    def signals(mixed=8):
        return [{"id": str(i), "rewards": [1, 0, 1, 1] if i < mixed else [1, 1, 1, 1],
                 "truncated": [False] * 4} for i in range(32)]

    @staticmethod
    def scores(correct=180, ambiguous=12):
        return {"n": 200, "correct": correct, "unsafe": 3, "contract_errors": 4,
                "families": {"doi_two_choose_one": 20, "read_homonym_year": 13, "clarify_ambiguous": ambiguous}}

    def test_signal_requires_success_failure_mix_not_text_diversity(self):
        self.assertTrue(once.signal_passed(self.signals()))
        self.assertFalse(once.signal_passed(self.signals(7)))
        self.assertFalse(once.signal_passed([{**r, "rewards": [0, -1, 0, -1]} for r in self.signals()]))
        rows = self.signals()
        rows[-1]["truncated"][0] = True
        self.assertFalse(once.signal_passed(rows))
        with self.assertRaises(ValueError): once.signal_passed(rows[:-1])

    def test_pipeline_stops_at_each_gate_and_only_unblinds_after_validation(self):
        test = self
        for stop, expected in (("signal", "retain_v1_insufficient_signal"),
                               ("train", "error"), ("validation", "retain_v1_validation_failed"),
                               ("blind", "retain_v1_blind_failed"), (None, "candidate_accepted")):
            events = []
            class Backend:
                def verify_start(self): events.append("start")
                def probe(self):
                    events.append("signal")
                    return test.signals(7 if stop == "signal" else 8)
                def train(self):
                    events.append("train")
                    if stop == "train": raise RuntimeError("fake training failure")
                def validate(self):
                    events.append("validation")
                    return test.scores(), test.scores(179 if stop == "validation" else 185, 18)
                def blind(self):
                    events.append("blind")
                    return test.scores(), test.scores(170 if stop == "blind" else 182)
            if expected == "error":
                with self.assertRaises(RuntimeError): once.run_pipeline(Backend())
            else:
                self.assertEqual(once.run_pipeline(Backend()), expected)
            self.assertEqual(events, ["start", "signal", "train", "validation", "blind"][:
                {"signal": 2, "train": 3, "validation": 4, "blind": 5, None: 5}[stop]])
        base = self.scores()
        for key in ("unsafe", "contract_errors"):
            improved = self.scores(190, 20) | {key: base[key] + 1}
            self.assertFalse(once.validation_passed(base, improved))
        for family in ("doi_two_choose_one", "read_homonym_year"):
            improved = self.scores(190, 20)
            improved["families"][family] -= 1
            self.assertFalse(once.validation_passed(base, improved))
        self.assertFalse(once.validation_passed(base, self.scores(180, 20)))
        self.assertFalse(once.validation_passed(base, self.scores(190, 12)))

    def test_reference_adapter_restores_flags_even_when_forward_fails(self):
        model = Model()
        flags = [p.requires_grad for _, p in model.named_parameters()]
        with self.assertRaises(RuntimeError):
            with once.reference_scope(model):
                self.assertEqual(model.active_adapter, "reference")
                self.assertFalse(any(p.requires_grad for _, p in model.named_parameters()))
                raise RuntimeError("fake reference forward failure")
        self.assertEqual(model.active_adapter, "policy")
        self.assertEqual([p.requires_grad for _, p in model.named_parameters()], flags)

    def test_loader_uses_existing_v1_twice_and_never_initializes_new_lora(self):
        events, model = [], Model()
        def loaded(base, path, **kwargs):
            events.append((path, kwargs))
            return model
        def load_adapter(path, **kwargs): events.append((path, kwargs))
        model.load_adapter = load_adapter
        with patch.dict("sys.modules", {"torch": TORCH, "peft": SimpleNamespace(
                PeftModel=SimpleNamespace(from_pretrained=loaded))}):
            self.assertIs(once.load_policy(object(), Path("verified-v1")), model)
        self.assertEqual([e[1]["adapter_name"] for e in events], ["policy", "reference"])
        self.assertEqual([e[1]["is_trainable"] for e in events], [True, False])
        self.assertEqual(events[0][0], events[1][0])
        self.assertFalse(model.parameters["base.weight"].requires_grad)

    def test_actual_generate_settings_are_checked_without_changing_output(self):
        model, report = Model(), {}
        audit = once.GenerationAudit(report)
        audit.wrap(model)
        audit.stage = "signal"
        sampling = dict(do_sample=True, max_new_tokens=384, num_beams=1, temperature=.7, top_p=.9, top_k=50)
        self.assertEqual(model.generate(**sampling), sampling)
        for key, bad in (("do_sample", False), ("temperature", 1.0), ("top_p", .95), ("max_new_tokens", 12)):
            with self.assertRaises(ValueError): model.generate(**(sampling | {key: bad}))
        self.assertEqual(model.calls, 1)
        audit.stage = "train"
        cfg = SimpleNamespace(**sampling)
        model.generate(generation_config=cfg)
        self.assertEqual(report["generation_calls"], {"signal": 1, "train": 1})
        audit.stage = "validation"
        with self.assertRaises(ValueError): model.generate(**sampling)
        model.generate(do_sample=False, max_new_tokens=384, num_beams=1)
        self.assertEqual(model.calls, 3)

    def test_training_order_and_32_group_no_signal_stop(self):
        cases = self.original["train"][:50]
        lookup = {c["id"]: c for c in cases}
        records = [{"case_id": c["id"], "prompt": "public:" + c["id"]} for c in cases]
        meter = once.TrainingMeter(lookup, records)
        with self.assertRaises(ValueError): meter(["{}"] * 4, [cases[1]["id"]] * 4, [records[1]["prompt"]] * 4)
        for i in range(31):
            meter([cases[i]["reference"]] * 4, [cases[i]["id"]] * 4, [records[i]["prompt"]] * 4)
        with self.assertRaisesRegex(ValueError, "no_mixed_learning_signal"):
            meter([cases[31]["reference"]] * 4, [cases[31]["id"]] * 4, [records[31]["prompt"]] * 4)
        meter = once.TrainingMeter(lookup, records)
        for c, record in zip(cases, records):
            meter([c["reference"], "{}", c["reference"], "{}"], [c["id"]] * 4, [record["prompt"]] * 4)
        with self.assertRaisesRegex(ValueError, "training_group_order_or_count_changed"):
            meter([cases[0]["reference"]] * 4, [cases[0]["id"]] * 4, [records[0]["prompt"]] * 4)

    def test_real_training_bridge_preserves_v1_and_uses_reference_view(self):
        test, events = self, []
        cases = self.original["train"][:50]
        records = [{"case_id": c["id"], "prompt": "PUBLIC:" + c["id"]} for c in cases]
        class Trainer:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.model = kwargs["model"]
                self.ref_model = None
                self.state = SimpleNamespace(global_step=0)
                test.assertNotIn("peft_config", kwargs)
                test.assertNotIn("tools", kwargs)
                test.assertNotIn("eval_dataset", kwargs)
                test.assertFalse(kwargs["args"]["shuffle_dataset"])
                test.assertEqual(kwargs["args"]["max_steps"], 50)
                # Simulate TRL's adapter cast: production must restore the original value.
                kwargs["model"].parameters["layer.lora_A.policy.weight"].value = 0.5
            def train(self, resume_from_checkpoint):
                test.assertIsNone(resume_from_checkpoint)
                model = self.kwargs["model"]
                test.assertEqual(model.parameters["layer.lora_A.policy.weight"].value, 1)
                for i, record in enumerate(self.kwargs["train_dataset"]):
                    self.kwargs["reward_funcs"]([cases[i]["reference"], "{}", cases[i]["reference"], "{}"],
                        [record["case_id"]] * 4, [record["prompt"]] * 4)
                    test.assertEqual(self.ref_model(), "reference")
                    test.assertEqual(model.active_adapter, "policy")
                    test.assertTrue(model.parameters["layer.lora_A.policy.weight"].requires_grad)
                    test.assertFalse(model.parameters["layer.lora_A.reference.weight"].requires_grad)
                    model.parameters["layer.lora_A.policy.weight"].value += .01
                    self.state.global_step = i + 1
                    for callback in self.kwargs["callbacks"]:
                        callback.on_step_end(None, self.state, None)
                events.append("trained")
        modules = {"torch": TORCH, "datasets": SimpleNamespace(Dataset=SimpleNamespace(from_list=lambda x: x)),
                   "transformers": SimpleNamespace(TrainerCallback=object),
                   "trl": SimpleNamespace(GRPOConfig=lambda **kw: kw, GRPOTrainer=Trainer)}
        with tempfile.TemporaryDirectory() as temp, patch.dict("sys.modules", modules), \
                patch("grpo.training_records", return_value=records):
            backend = object.__new__(once.GPUBackend)
            backend.out = Path(temp)
            backend.deadline = float("inf")
            backend.model, backend.tokenizer, backend.torch = Model(), object(), TORCH
            backend.manifest = {"train_ids": [c["id"] for c in cases]}
            backend.cases = {c["id"]: c for c in cases}
            backend.encoded = {c["id"]: [1, 2] for c in cases}
            backend.report = {}
            backend.audit = SimpleNamespace(stage="canary")
            backend.initial_hash = once.tensor_fingerprint(once.policy_parameters(backend.model))
            backend.train()
            self.assertEqual(events, ["trained"])
            self.assertEqual(backend.report["optimizer_steps"], 50)
            self.assertEqual(backend.report["reference_forward_calls"], 50)
            self.assertEqual(set(backend.model.forward_adapters), {"reference"})
            self.assertEqual(backend.report["final_reference_sha256"], backend.initial_hash)
            self.assertTrue(backend.report["adapter_changed"])
            self.assertEqual(len(backend.report["saved_adapters"]), 6)

    def test_watchdog_rejects_late_success_and_archives_partial_results(self):
        class Process:
            returncode = 0
            waits = 0
            terminated = killed = False
            def wait(self, timeout=None):
                self.waits += 1
                if self.waits <= 2: raise subprocess.TimeoutExpired("fake", timeout)
            def terminate(self): self.terminated = True
            def kill(self): self.killed = True
        process = Process()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "run"
            out.mkdir()
            once.save_json(out / "run.json", {"status": "candidate_accepted"})
            with patch.object(once.subprocess, "Popen", return_value=process):
                once.supervise(["never executed"], 1, out)
            self.assertTrue(process.terminated and process.killed)
            self.assertEqual(json.loads((out / "run.json").read_text())["status"], "retain_v1_timeout")
            (root / "once-bundle.json").write_text("{}")
            (out / "not-complete.tmp").write_text("partial")
            archive = root / "result.zip"
            once.package_results(root, out, archive)
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(set(z.namelist()), {"once-bundle.json", "run/run.json"})

    def test_scoring_keeps_existing_reward_and_rejects_partial_coverage(self):
        cases = self.original["validation"]
        predictions = [{"id": c["id"], "response": c["reference"]} for c in cases]
        self.assertEqual(once.evaluate(cases, predictions)["correct"], 200)
        with self.assertRaises(ValueError): once.evaluate(cases, predictions[:-1])
        with self.assertRaises(ValueError): once.evaluate(cases, list(reversed(predictions)))
        predictions[0]["response"] = "{}"
        scored = once.evaluate(cases, predictions)
        self.assertEqual((scored["correct"], scored["contract_errors"]), (199, 1))

    def test_bundle_roundtrip_default_zero_gpu_and_integrity_gates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data, results, cloud = root / "data", root / "results", root / "cloud"
            data.mkdir()
            (data / "manifest.json").write_text("{}")
            after = results / "runs/sft-after"
            after.mkdir(parents=True)
            (after / "predictions.jsonl").write_text("\n".join(canonical({"id": c["id"], "response": c["reference"]})
                for c in self.original["validation"]), encoding="utf-8")
            adapter = results / "runs/sft-train/adapter"
            adapter.mkdir(parents=True)
            (adapter / "adapter_model.safetensors").write_bytes(b"fake-test-only-not-weights")
            (adapter / "adapter_config.json").write_text("{}")
            base = {"gpu": "fake-test-only", "versions": {}, "model_files_sha256": {}}
            with patch.object(bundle.dataset, "load_verified", return_value=self.original), \
                    patch.object(bundle.sft, "load_bundle", return_value=({}, [], [], base)), \
                    patch.object(bundle.sft_bundle, "score_results") as verify, \
                    patch.object(once, "evaluate", return_value=self.scores()):
                report = bundle.build(data, root / "v1-bundle", results, cloud, root / "cloud.zip")
                verify.assert_called_once()
            self.assertEqual(report["train_pool"], 1200)
            self.assertEqual(report["model_calls"], 0)
            manifest, cases = once.load_bundle(cloud)
            self.assertEqual(len(cases), 1200)
            self.assertEqual(list(cases)[:1000], [c["id"] for c in self.original["train"]])
            self.assertFalse(set(cases) & {c["id"] for c in self.original["validation"] + self.original["blind_test"]})
            with zipfile.ZipFile(root / "cloud.zip") as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(set(z.namelist()), set(manifest["files"]) | {"once-bundle.json"})
                self.assertEqual(z.read("v1-adapter/adapter_model.safetensors"), (adapter / "adapter_model.safetensors").read_bytes())
            with patch("sys.argv", ["grpo_once.py", "--bundle", str(cloud)]), \
                    patch.object(once, "supervise") as execute, patch("builtins.print") as printed:
                once.main()
                execute.assert_not_called()
                self.assertFalse(json.loads(printed.call_args.args[0])["gpu_loaded"])
            (cloud / "train.oracle.jsonl").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "bundle_changed"): once.load_bundle(cloud)


if __name__ == "__main__":
    unittest.main()
