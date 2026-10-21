"""Guarded GRPO checks use fake CPU objects only; no GPU, network, or training."""

from copy import deepcopy
import json
from pathlib import Path
import random
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from contracts import canonical, parse_response
import dataset
import grpo_guarded as guarded
import grpo_once as once
from rewards import score
from test_grpo_once import Model, TORCH


class ModeModel(Model):
    def __init__(self):
        super().__init__()
        self.training = True
        self.child = SimpleNamespace(training=False)

    def modules(self):
        return [self, self.child]

    def train(self):
        self.training = self.child.training = True
        return self

    def eval(self):
        self.training = self.child.training = False
        return self


class TokenRows:
    def __init__(self, rows):
        self.rows = rows
        self.shape = (len(rows), len(rows[0]))

    def __getitem__(self, item):
        row_slice, column_slice = item
        return TokenRows([row[column_slice] for row in self.rows[row_slice]])

    def tolist(self):
        return self.rows


def fake_rng_modules():
    state = {"cpu": (11,), "cuda": [(22,)], "numpy": (33,)}
    cuda = SimpleNamespace(**vars(TORCH.cuda),
        get_rng_state_all=lambda: deepcopy(state["cuda"]),
        set_rng_state_all=lambda value: state.update(cuda=deepcopy(value)))
    torch = SimpleNamespace(**{k: v for k, v in vars(TORCH).items() if k != "cuda"}, cuda=cuda,
        get_rng_state=lambda: deepcopy(state["cpu"]),
        set_rng_state=lambda value: state.update(cpu=deepcopy(value)))
    numpy = SimpleNamespace(random=SimpleNamespace(get_state=lambda: deepcopy(state["numpy"]),
        set_state=lambda value: state.update(numpy=deepcopy(value))))
    return torch, numpy, state


class GuardedGRPOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = dataset.build_cases()

    def setUp(self):
        for owner, name in ((socket, "create_connection"), (socket, "getaddrinfo"),
                            (socket.socket, "connect")):
            guard = patch.object(owner, name, side_effect=AssertionError("network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    @staticmethod
    def qualification_rows(mixed_per_stratum=2):
        return [{"id": f"qualification-{stratum}-{index}", "stratum": stratum,
                 "rewards": [1.0, 0.0, 1.0, 1.0] if index < mixed_per_stratum else [1.0] * 4,
                 "truncated": [False] * 4}
                for stratum in guarded.STRATA for index in range(8)]

    def test_fixed_single_gpu_group_configuration(self):
        config = guarded.fixed_training_config(Path("fake-output"))
        self.assertEqual(config["max_steps"], 200)
        self.assertEqual(config["per_device_train_batch_size"], 1)
        self.assertEqual(config["gradient_accumulation_steps"], 4)
        self.assertEqual(config["steps_per_generation"], 4)
        self.assertEqual(config["num_generations"], 4)
        self.assertEqual(config["num_iterations"], 1)
        self.assertFalse(config["shuffle_dataset"])
        self.assertFalse(config["use_vllm"])
        self.assertFalse(config["push_to_hub"])
        self.assertEqual(config["eval_strategy"], "no")

    def test_qualification_requires_positive_failure_mix_and_full_coverage(self):
        rows = self.qualification_rows()
        self.assertEqual(len(rows), 64)
        self.assertTrue(guarded.qualification_passed(rows))
        self.assertTrue(guarded.qualification_passed(rows, weak_strata=guarded.STRATA))
        rows[0]["rewards"] = [1.0] * 4
        self.assertFalse(guarded.qualification_passed(rows))
        failures_only = [{**r, "rewards": [0.0, -1.0, 0.0, -1.0]}
                         for r in self.qualification_rows()]
        self.assertFalse(guarded.qualification_passed(failures_only))
        for changed in (self.qualification_rows()[:-1],
                        self.qualification_rows() + [self.qualification_rows()[0]]):
            with self.assertRaises(ValueError):
                guarded.qualification_passed(changed)

    def test_qualification_checks_each_weak_stratum_and_truncation(self):
        rows = self.qualification_rows(3)
        weak = guarded.STRATA[0]
        for row in rows:
            if row["stratum"] == weak:
                row["rewards"] = [1.0] * 4
        self.assertTrue(guarded.qualification_passed(rows))
        self.assertFalse(guarded.qualification_passed(rows, weak_strata=[weak]))
        rows = self.qualification_rows()
        rows[-1]["truncated"][0] = True
        self.assertFalse(guarded.qualification_passed(rows))

    def test_generation_audit_verifies_real_group_settings_eos_and_budget(self):
        def make(stage="train", count=4, tail=None, report=None):
            model = ModeModel()
            model.generation_config = SimpleNamespace(do_sample=True, num_beams=1,
                temperature=.7, top_p=.9, top_k=50, max_new_tokens=384, eos_token_id=0)
            output = TokenRows([[10, 11] + (tail if tail is not None else [12, 0])] * count)
            model.provider_calls = 0

            def generate(**kwargs):
                model.provider_calls += 1
                return output

            model.generate = generate
            audit = guarded.GenerationAudit({} if report is None else report)
            audit.stage = stage
            audit.wrap(model)
            return model, audit, output

        inputs = TokenRows([[10, 11]] * 4)
        model, audit, expected = make()
        self.assertIs(model.generate(input_ids=inputs), expected)
        self.assertEqual(audit.report["generated_sequences"], {"train": 4})
        for field, value in (("do_sample", False), ("temperature", .8), ("top_p", .95),
                             ("top_k", 20), ("max_new_tokens", 512), ("num_beams", 2)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "actual_generation_settings_changed"):
                model.generate(input_ids=inputs, **{field: value})
        model, truncated_audit, _ = make(tail=[12] * 384)
        with self.assertRaisesRegex(ValueError, "generation_truncated"):
            model.generate(input_ids=inputs)
        self.assertEqual(truncated_audit.report["generated_sequences"], {"train": 4})
        # Batch padding after an early EOS is a complete response, not truncation.
        model, _, _ = make(tail=[12, 0] + [0] * 382)
        model.generate(input_ids=inputs)
        model, _, _ = make(count=3)
        with self.assertRaisesRegex(ValueError, "generation_group_size_changed"):
            model.generate(input_ids=inputs)
        model, _, _ = make(report={"generated_sequences": {"train": 800}})
        with self.assertRaisesRegex(ValueError, "generation_budget_exceeded"):
            model.generate(input_ids=inputs)
        self.assertEqual(model.provider_calls, 0)
        model, _, _ = make(stage="signal", report={"generated_sequences": {"signal": 256}})
        with self.assertRaisesRegex(ValueError, "generation_budget_exceeded"):
            model.generate(input_ids=inputs)
        self.assertEqual(model.provider_calls, 0)
        model, audit, _ = make(stage="validation-50", count=1)
        model.generate(input_ids=TokenRows([[10, 11]]), do_sample=False)
        self.assertEqual(audit.report["generated_sequences"], {"validation-50": 1})
        model, _, _ = make(stage="validation-50", count=1,
                           report={"generated_sequences": {"previous": 2576}})
        with self.assertRaisesRegex(ValueError, "generation_budget_exceeded"):
            model.generate(input_ids=TokenRows([[10, 11]]), do_sample=False)
        self.assertEqual(model.provider_calls, 0)

    def test_training_meter_requires_200_unique_ordered_groups_and_stops_on_group_201(self):
        cases = self.original["train"][:200]
        records = [{"case_id": c["id"], "prompt": "PUBLIC:" + c["id"]} for c in cases]
        meter = once.TrainingMeter({c["id"]: c for c in cases}, records)
        for c, record in zip(cases, records):
            meter([c["reference"], "{}", c["reference"], "{}"],
                  [c["id"]] * 4, [record["prompt"]] * 4)
        self.assertEqual(len({r["id"] for r in meter.rows}), 200)
        with self.assertRaisesRegex(ValueError, "training_group_order_or_count_changed"):
            meter([cases[0]["reference"]] * 4, [cases[0]["id"]] * 4, [records[0]["prompt"]] * 4)

    def test_evaluation_keeps_reward_and_separates_false_permission_refusals(self):
        cases = deepcopy(self.original["validation"])
        predictions = [{"id": c["id"], "response": c["reference"]} for c in cases]
        good = guarded.evaluate(cases, predictions)
        self.assertEqual((good["n"], good["correct"]), (200, 200))
        self.assertEqual((good["unsafe"], good["false_refusals"], good["false_permission"]), (0, 0, 0))
        callable_indices = [i for i, c in enumerate(cases)
                            if parse_response(c["reference"])["action"] == "call"]
        predictions[callable_indices[0]]["response"] = canonical({"action": "clarify", "reason": "permission_required"})
        predictions[callable_indices[1]]["response"] = canonical({"action": "clarify", "reason": "missing_query"})
        result = guarded.evaluate(cases, predictions)
        self.assertEqual((result["correct"], result["false_refusals"], result["false_permission"]), (198, 2, 1))
        for c, row in zip(cases, result["results"]):
            prediction = next(p for p in predictions if p["id"] == c["id"])
            self.assertEqual(row["reward"], score(prediction["response"], c)["reward"])
        for changed in (predictions[:-1], list(reversed(predictions)),
                        predictions[:-1] + [predictions[0]]):
            with self.assertRaises(ValueError):
                guarded.evaluate(cases, changed)
        short = guarded.evaluate(cases[:7], predictions[:7])
        self.assertEqual(short["n"], 7)

    @staticmethod
    def metrics(n=200, correct=180):
        families = guarded.CRITICAL_FAMILIES if n == 200 else guarded.STRATA
        return {"n": n, "correct": correct, "unsafe": 0, "contract_errors": 3,
                "false_refusals": 2, "false_permission": 1,
                "families": {family: 8 for family in families}}

    def test_acceptance_and_checkpoint_stopping_boundaries(self):
        before = self.metrics()
        improved = self.metrics(correct=188)
        focused = self.metrics(80, 64)
        self.assertTrue(guarded.acceptance(before, improved, focused, focused))
        self.assertFalse(guarded.acceptance(before, self.metrics(correct=187), focused, focused))
        self.assertFalse(guarded.acceptance(self.metrics(correct=175), self.metrics(correct=184), focused, focused))
        for field in ("unsafe", "contract_errors", "false_refusals", "false_permission"):
            changed = {**improved, field: before[field] + 1}
            with self.subTest(field=field):
                self.assertFalse(guarded.acceptance(before, changed, focused, focused))
                focused_changed = {**focused, field: focused[field] + 1}
                self.assertFalse(guarded.acceptance(before, improved, focused, focused_changed))
        changed = deepcopy(improved)
        changed["families"][guarded.CRITICAL_FAMILIES[0]] -= 1
        self.assertFalse(guarded.acceptance(before, changed, focused, focused))
        self.assertEqual(guarded.checkpoint_decision(50, before, before, focused, focused), "continue")
        self.assertEqual(guarded.checkpoint_decision(100, before, before, focused, focused), "no_improvement")
        self.assertEqual(guarded.checkpoint_decision(200, before, self.metrics(correct=181), focused, focused), "limit")
        self.assertEqual(guarded.checkpoint_decision(50, before, improved, focused, focused), "accept")
        self.assertEqual(guarded.checkpoint_decision(50, before, self.metrics(correct=177), focused, focused), "regression")
        changed = deepcopy(before)
        changed["families"][guarded.CRITICAL_FAMILIES[0]] -= 2
        self.assertEqual(guarded.checkpoint_decision(50, before, changed, focused, focused), "regression")
        with self.assertRaises(ValueError):
            guarded.checkpoint_decision(150, before, improved, focused, focused)

    def test_pipeline_never_opens_blind_before_a_qualifying_candidate(self):
        test = self
        for stop in ("target", "signal", "training", "blind", "error", None):
            events = []

            class Backend:
                report = {"weak_strata": []}

                def verify_start(self):
                    events.append("start")
                    self.report = {"weak_strata": [], "validation_target_unreachable": stop == "target"}

                def probe(self):
                    events.append("signal")
                    return test.qualification_rows(1 if stop == "signal" else 2)

                def train(self):
                    events.append("train")
                    if stop == "error":
                        raise ValueError("fake_training_error")
                    self.report["training_stop_reason"] = "no_improvement"
                    if stop != "training":
                        self.report["selected_step"] = 50

                def blind(self):
                    events.append("blind")
                    return test.metrics(), test.metrics(correct=179 if stop == "blind" else 180)

            with self.subTest(stop=stop):
                if stop == "error":
                    with self.assertRaisesRegex(ValueError, "fake_training_error"):
                        guarded.run_pipeline(Backend())
                else:
                    result = guarded.run_pipeline(Backend())
                    self.assertEqual(result, "candidate_accepted" if stop is None else {
                        "target": "retain_v1_target_unreachable", "signal": "retain_v1_insufficient_signal",
                        "training": "retain_v1_no_improvement", "blind": "retain_v1_blind_failed"}[stop])
                self.assertEqual(events, ["start", "signal", "train", "blind"][:len(events)])
                self.assertEqual(events.count("train"), 0 if stop in ("target", "signal") else 1)
                self.assertEqual(events.count("blind"), int(stop in (None, "blind")))

    def test_inference_scope_restores_all_state_even_after_error(self):
        torch, numpy, rng = fake_rng_modules()
        model = ModeModel()
        model.config.use_cache = False
        backend = SimpleNamespace(torch=torch, model=model,
                                  report={"stage": "train"}, audit=SimpleNamespace(stage="train"))
        flags = [p.requires_grad for _, p in model.named_parameters()]
        python_rng, saved_rng = random.getstate(), deepcopy(rng)
        with patch.dict("sys.modules", {"numpy": numpy}):
            with self.assertRaisesRegex(RuntimeError, "fake_evaluation_error"):
                with guarded.inference_scope(backend):
                    once.select_adapter(model, "reference", False)
                    model.eval()
                    model.config.use_cache = True
                    backend.report["stage"] = backend.audit.stage = "validation-50"
                    random.random()
                    rng.update(cpu=(99,), cuda=[(98,)], numpy=(97,))
                    raise RuntimeError("fake_evaluation_error")
        self.assertEqual(model.active_adapter, "policy")
        self.assertEqual([p.requires_grad for _, p in model.named_parameters()], flags)
        self.assertEqual((model.training, model.child.training), (True, False))
        self.assertFalse(model.config.use_cache)
        self.assertEqual((backend.report["stage"], backend.audit.stage), ("train", "train"))
        self.assertEqual((random.getstate(), rng), (python_rng, saved_rng))

    def test_one_trainer_preserves_v1_reference_through_all_checkpoint_callbacks(self):
        for selected_step in (50, 100, None):
            test, events = self, []
            torch, numpy, rng = fake_rng_modules()
            cases = self.original["train"][:200]
            records = [{"case_id": c["id"], "prompt": "PUBLIC:" + c["id"]} for c in cases]

            class Trainer:
                def __init__(self, **kwargs):
                    events.append("trainer_init")
                    self.kwargs, self.model, self.ref_model = kwargs, kwargs["model"], None
                    self.state = SimpleNamespace(global_step=0)
                    for field in ("peft_config", "tools", "eval_dataset"):
                        test.assertNotIn(field, kwargs)
                    test.assertEqual(kwargs["args"]["max_steps"], 200)
                    test.assertFalse(kwargs["args"]["sync_ref_model"])
                    self.model.parameters["layer.lora_A.policy.weight"].value = .5

                def train(self, resume_from_checkpoint):
                    events.append("train")
                    test.assertIsNone(resume_from_checkpoint)
                    model = self.model
                    test.assertEqual(model.parameters["layer.lora_A.policy.weight"].value, 1)
                    for i, record in enumerate(self.kwargs["train_dataset"]):
                        test.assertTrue(model.training)
                        test.assertFalse(model.config.use_cache)
                        test.assertEqual(model.active_adapter, "policy")
                        test.assertTrue(model.parameters["layer.lora_A.policy.weight"].requires_grad)
                        test.assertFalse(model.parameters["layer.lora_A.reference.weight"].requires_grad)
                        self.kwargs["reward_funcs"]([cases[i]["reference"], "{}", cases[i]["reference"], "{}"],
                            [record["case_id"]] * 4, [record["prompt"]] * 4)
                        test.assertEqual(self.ref_model(), "reference")
                        model.parameters["layer.lora_A.policy.weight"].value += .01
                        self.state.global_step = i + 1
                        control = SimpleNamespace(should_training_stop=False)
                        for callback in self.kwargs["callbacks"]:
                            callback.on_step_end(None, self.state, control)
                        if control.should_training_stop:
                            break

            modules = {"torch": torch, "numpy": numpy,
                       "datasets": SimpleNamespace(Dataset=SimpleNamespace(from_list=lambda x: x)),
                       "transformers": SimpleNamespace(TrainerCallback=object),
                       "trl": SimpleNamespace(GRPOConfig=lambda **kw: kw, GRPOTrainer=Trainer)}
            with self.subTest(selected_step=selected_step), tempfile.TemporaryDirectory() as temp, \
                    patch.dict("sys.modules", modules), patch("grpo.training_records", return_value=records):
                backend = object.__new__(guarded.GPUBackend)
                backend.out, backend.deadline = Path(temp), float("inf")
                backend.model, backend.tokenizer, backend.torch = ModeModel(), object(), torch
                backend.splits = {"train": cases, "validation": self.original["validation"], "focused": cases[:80]}
                backend.cases = {c["id"]: c for c in cases}
                backend.encoded = {c["id"]: [1, 2] for c in cases}
                backend.report = {"stage": "signal", "baseline_seconds": 0}
                backend.audit = SimpleNamespace(stage="signal")
                backend.initial_hash = once.tensor_fingerprint(once.policy_parameters(backend.model))
                backend.before_dev = self.metrics()
                backend.before_focused = self.metrics(80, 64)

                def predictions(rows, stage, adapter):
                    events.append(stage)
                    backend.stage(stage)
                    once.select_adapter(backend.model, adapter, False)
                    backend.model.eval()
                    backend.model.config.use_cache = True
                    random.random()
                    rng.update(cpu=(99,), cuda=[(98,)], numpy=(97,))
                    return [{"id": c["id"], "response": c["reference"]} for c in rows]

                def decision(step, *unused):
                    return "accept" if step == selected_step else "limit" if step == 200 else "continue"

                backend.predictions = predictions
                saved_rng, python_rng = deepcopy(rng), random.getstate()
                with patch.object(guarded, "checkpoint_decision", side_effect=decision):
                    backend.train()
                final_step = selected_step or 200
                checks = [step for step in (50, 100, 200) if step <= final_step]
                self.assertEqual(events, ["trainer_init", "train"] +
                                 [stage for step in checks for stage in (f"validation-{step}", f"focused-{step}")])
                self.assertEqual(backend.report["optimizer_steps"], final_step)
                self.assertEqual(backend.report["reference_forward_calls"], final_step)
                self.assertEqual(backend.report["final_reference_sha256"], backend.initial_hash)
                self.assertEqual(backend.report.get("selected_step"), selected_step)
                self.assertEqual(set(backend.report["saved_adapters"]), {f"checkpoint-{step}" for step in checks})
                self.assertEqual((random.getstate(), rng), (python_rng, saved_rng))
                rewards = json.loads((backend.out / "training-rewards.json").read_text(encoding="utf-8"))
                self.assertEqual([r["id"] for r in rewards], [c["id"] for c in cases[:final_step]])

    def test_default_preflight_has_no_gpu_and_result_archive_retains_all_checkpoints(self):
        with patch.object(guarded, "load_bundle", return_value=({"data_sha256": "fake-test-digest"}, {})), \
                patch.object(guarded, "GPUBackend") as gpu, patch.object(once, "supervise") as supervise, \
                patch("builtins.print") as printed:
            guarded.main([])
            gpu.assert_not_called()
            supervise.assert_not_called()
            report = json.loads(printed.call_args.args[0])
            self.assertEqual(report["status"], "preflight_only")
            self.assertFalse(report["gpu_loaded"])
            self.assertEqual((report["generation_calls"], report["optimizer_steps"]), (0, 0))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = root / "run"
            out.mkdir()
            (root / "guarded-bundle.json").write_text("{}", encoding="utf-8")
            (out / "run.json").write_text("{}", encoding="utf-8")
            (out / "unfinished.tmp").write_text("incomplete", encoding="utf-8")
            for step in (50, 100, 200):
                folder = out / f"checkpoint-{step}" / "policy"
                folder.mkdir(parents=True)
                (folder / "adapter_model.safetensors").write_bytes(b"fake-test-only")
            archive = root / "result.zip"
            guarded.package_results(root, out, archive)
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(set(z.namelist()), {"guarded-bundle.json", "run/run.json"} |
                    {f"run/checkpoint-{step}/policy/adapter_model.safetensors" for step in (50, 100, 200)})
            with self.assertRaises(FileExistsError):
                guarded.package_results(root, out, archive)

    def test_blind_reader_is_gated_and_runs_exactly_once_after_selection(self):
        cases = deepcopy(self.original["blind_test"])
        for i, case in enumerate(cases):
            case["stratum"] = guarded.STRATA[i // 25]
        with tempfile.TemporaryDirectory() as temp:
            backend = object.__new__(guarded.GPUBackend)
            backend.out, backend.bundle = Path(temp), Path("fake-bundle")
            backend.tokenizer = object()
            backend.splits = {"train": self.original["train"][:200]}
            backend.report = {"blind_evaluation_started": False}
            calls = []

            def predictions(rows, stage, adapter):
                calls.append((stage, adapter))
                return [{"id": c["id"], "response": c["reference"]} for c in rows]

            backend.predictions = predictions
            with patch.object(guarded, "read_rows", return_value=cases) as read, \
                    patch.object(guarded, "encode_all", return_value=[[1, 2]] * 200):
                with self.assertRaisesRegex(ValueError, "single_selected_candidate_required"):
                    backend.blind()
                read.assert_not_called()
                backend.report["selected_step"] = 50
                before, after = backend.blind()
                self.assertEqual((before["correct"], after["correct"]), (200, 200))
                self.assertEqual(calls, [("blind-v1", "reference"), ("blind-candidate", "policy")])
                with self.assertRaisesRegex(ValueError, "single_selected_candidate_required"):
                    backend.blind()
                read.assert_called_once()


if __name__ == "__main__":
    unittest.main()
