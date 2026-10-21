"""Offline checks only; no torch, model download, GPU or production network tools."""

from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from contracts import canonical, parse_response
import dataset
import grpo_once as once
import grpo_once_bundle as previous
import rpo
import rpo_bundle
from rewards import score


class Tokenizer:
    eos_token_id = 0

    def __call__(self, text, **kwargs):
        return {"input_ids": [ord(c) for c in text]}

    def apply_chat_template(self, messages, tokenize, **kwargs):
        text = canonical(messages) + "<assistant><think></think>"
        return [ord(c) for c in text] if tokenize else text

    def decode(self, ids, **kwargs):
        return "".join(chr(i) for i in ids)


class RPOTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = dataset.build_cases()
        cls.cases = cls.original["train"] + [c for k in previous.PAIR_KINDS for i in range(20)
                                             for c in previous.make_pair(k, i)]
        cls.pairs = rpo_bundle.preference_pairs(cls.cases)

    def test_preferences_are_valid_wrong_decisions_not_format_negatives(self):
        result = rpo_bundle.audit(self.cases, self.pairs,
                                 self.original["validation"] + self.original["blind_test"])
        self.assertEqual((result["unique_train_prompts"], result["preference_pairs"]), (1200, 2400))
        self.assertEqual(len(result["train_families"]), 30)
        lookup = {c["id"]: c for c in self.cases}
        for p in self.pairs:
            self.assertEqual(canonical(parse_response(p["rejected"])), p["rejected"])
            self.assertEqual(score(p["chosen"], lookup[p["case_id"]])["reward"], 1)
            self.assertLess(score(p["rejected"], lookup[p["case_id"]])["reward"], 1)
        self.assertEqual(rpo_bundle.preference_pairs(list(reversed(self.cases))), self.pairs)
        self.assertFalse(result["human_reviewed"])
        # Reconstruct the earlier recipe to prove the edit's exact scope.
        with patch.object(rpo, "lowered_upper_bound", return_value=False):
            old = rpo_bundle.preference_pairs(self.cases)
        correction = rpo_bundle.correction_audit(self.cases, old, self.pairs)
        self.assertEqual((correction["corrected_pairs"], correction["unchanged_pairs"]), (200, 2200))
        self.assertEqual(correction["by_family"], {
            "search_hybrid_remote": 40, "search_multi_explicit": 40,
            "library_hybrid_local": 30, "library_subset": 30, "library_all": 30, "library_one_limit": 30})
        with self.assertRaisesRegex(ValueError, "ambiguous_lowered_upper_bound"):
            rpo_bundle.audit(self.cases, old, [])
        altered = deepcopy(self.pairs)
        altered[0]["human_reviewed"] = True
        with self.assertRaises(ValueError):
            rpo_bundle.correction_audit(self.cases, old, altered)
        training_queries = {a["arguments"]["query"] for c in self.cases
                            if (a := parse_response(c["reference"])).get("tool")
                            in ("search_papers", "search_library")}
        changed = [p for p in self.pairs if p["negative_kind"] == "wrong_query"]
        self.assertEqual(len(changed), 200)
        for p in changed:
            chosen, rejected = parse_response(p["chosen"]), parse_response(p["rejected"])
            self.assertIn(rejected["arguments"]["query"], training_queries)
            self.assertNotEqual(chosen["arguments"]["query"], rejected["arguments"]["query"])
            rejected["arguments"]["query"] = chosen["arguments"]["query"]
            self.assertEqual(chosen, rejected)

    def test_no_heldout_relabel_duplicate_or_reversed_preferences(self):
        for change in ("overlap", "chosen", "duplicate", "coverage"):
            pairs, held = deepcopy(self.pairs), deepcopy(self.original["validation"])
            if change == "overlap": held[0] = self.cases[0]
            if change == "chosen": pairs[0]["chosen"], pairs[0]["rejected"] = pairs[0]["rejected"], pairs[0]["chosen"]
            if change == "duplicate": pairs[1] = pairs[0]
            if change == "coverage": pairs.pop()
            with self.subTest(change=change), self.assertRaises(ValueError):
                rpo_bundle.audit(self.cases, pairs, held)
        review = rpo_bundle.review_packet(self.cases, self.pairs)
        self.assertEqual(review.count("人工结论：待审核"), 35)
        self.assertFalse(any(c["id"] in review for c in self.original["blind_test"]))
        approved = rpo_bundle.review_packet(self.cases, self.pairs, approval={"status": "approved"})
        self.assertEqual(approved.count("审核记录："), 35)
        self.assertEqual(approved.count("审核记录：已批准按规则修正"), 5)
        self.assertNotIn("人工结论：待审核", approved)
        self.assertIn("不是200对或全量2400对逐条人工审核", approved)

    def test_training_token_prefix_answers_and_no_truncation(self):
        pair = self.pairs[0]
        case = next(c for c in self.cases if c["id"] == pair["case_id"])
        records, encoded = rpo.training_records(Tokenizer(), {case["id"]: case}, [pair])
        self.assertEqual(set(records[0]), {"prompt", "chosen", "rejected"})
        self.assertNotIn("goal_ids", records[0]["prompt"])
        self.assertEqual(encoded[0]["prompt_input_ids"], once.encode_all(Tokenizer(), once.public_rows([case]), 8192)[0])
        for field in ("chosen", "rejected"):
            self.assertEqual(encoded[0][field + "_input_ids"], [ord(c) for c in pair[field]] + [0])
        long_pair = {**pair, "rejected": "x" * rpo.MAX_OUTPUT}
        with self.assertRaises(ValueError): rpo.training_records(Tokenizer(), {case["id"]: case}, [long_pair])
        long_case = {**case, "instruction": "x" * rpo.MAX_INPUT}
        with self.assertRaises(ValueError): rpo.training_records(Tokenizer(), {case["id"]: long_case}, [pair])

    @staticmethod
    def scores(correct=180, unsafe=3):
        return {"n": 200, "correct": correct, "unsafe": unsafe, "contract_errors": 4,
                "families": {"read": 18, "clarify": 12}}

    def test_one_pipeline_no_retry_and_blind_stays_sealed_if_dev_fails(self):
        test = self
        for stop in ("start", "train", "dev", "blind", None):
            calls = []
            class Backend:
                def verify_start(self):
                    calls.append("start")
                    if stop == "start": raise ValueError("canary_failed")
                def train(self):
                    calls.append("train")
                    if stop == "train": raise ValueError("training_failed")
                def validate(self):
                    calls.append("dev")
                    return test.scores(), test.scores(180 if stop == "dev" else 181, 0)
                def blind(self):
                    calls.append("blind")
                    return test.scores(), test.scores(179 if stop == "blind" else 181, 0)
            with self.subTest(stop=stop):
                if stop in ("start", "train"):
                    with self.assertRaises(ValueError): rpo.pipeline(Backend())
                else:
                    expected = "candidate_passed_offline_only" if stop is None else "retain_v1_" + stop + "_failed"
                    self.assertEqual(rpo.pipeline(Backend()), expected)
                self.assertEqual(calls, ["start", "train", "dev", "blind"][:len(calls)])
                if stop is not None: self.assertEqual(calls[-1], stop)
        self.assertFalse(rpo.dev_passed(self.scores(), self.scores(181, 1)))
        worse_family = self.scores(185, 0)
        worse_family["families"]["read"] -= 1
        self.assertFalse(rpo.dev_passed(self.scores(), worse_family))
        self.assertFalse(rpo.blind_passed(self.scores(), worse_family))

    def test_execution_requires_review_and_has_no_default_gpu_import(self):
        manifest = {"counts": {}, "preferences_sha256": "approved-sha"}
        with patch.object(rpo, "load_bundle", return_value=(manifest, {}, [])), \
                patch.object(rpo, "worker") as worker, patch.object(once, "supervise") as supervisor:
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                rpo.main([])
            self.assertFalse(json.loads(stdout.getvalue())["gpu_loaded"])
            for args in (["--execute"], ["--execute", "--ack-synthetic-data", "--reviewed-data-sha", "wrong"]):
                with self.assertRaises(ValueError): rpo.main(args)
            worker.assert_not_called()
            supervisor.assert_not_called()
        process = subprocess.run([sys.executable, "-B", "-c",
            "import sys; import rpo; assert not ({'torch','transformers','trl','peft'} & sys.modules.keys())"],
            cwd=Path(__file__).parent, capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_trainer_fixed_config_and_reference_restoration(self):
        # Reuse the previous CPU model fixture, not a new training/safety framework.
        from test_grpo_once import Model
        model = Model()
        flags = [(p, p.requires_grad) for _, p in model.named_parameters()]
        with self.assertRaisesRegex(ValueError, "failure"):
            with once.reference_scope(model):
                self.assertEqual(model.active_adapter, "reference")
                self.assertFalse(any(p.requires_grad for _, p in model.named_parameters()))
                raise ValueError("failure")
        self.assertEqual(model.active_adapter, "policy")
        self.assertTrue(all(p.requires_grad == expected for p, expected in flags))
        cfg = rpo.config(Path("unused"))
        self.assertEqual((cfg["loss_type"], cfg["rpo_alpha"], cfg["beta"]), ("sigmoid", 1.0, 0.1))
        self.assertEqual((cfg["model_adapter_name"], cfg["ref_adapter_name"]), ("policy", "reference"))
        self.assertIsNone(cfg["max_length"])
        self.assertIsNone(cfg["max_prompt_length"])
        self.assertFalse(cfg["precompute_ref_log_probs"])
        self.assertFalse(cfg["generate_during_eval"])

    def test_training_harness_counts_pairs_freezes_reference_and_saves_candidate(self):
        from test_grpo_once import Model, TORCH
        test = self
        for corrupt_tokens in (False, True):
            with self.subTest(corrupt_tokens=corrupt_tokens), tempfile.TemporaryDirectory() as temp:
                backend = object.__new__(rpo.GPUBackend)
                backend.model, backend.torch, backend.out = Model(), TORCH, Path(temp)
                backend.deadline = float("inf")
                backend.report = {"optimizer_steps": 0, "generation_calls": {"canary": 10}}
                # Exercise progress callbacks without 300 rapid atomic file replacements
                # (Windows scanners may transiently lock these fake-step log files).
                backend.flush = Mock()
                backend.audit = SimpleNamespace(stage="canary")
                backend.tokenizer = Tokenizer()
                tokens = {"prompt_input_ids": [1, 2], "chosen_input_ids": [3, 0], "rejected_input_ids": [4, 0]}
                backend.records = [dict(tokens) for _ in range(2400)]
                backend.expected = deepcopy(backend.records)

                class FakeTrainer:
                    def __init__(self, **kwargs):
                        test.assertNotIn("peft_config", kwargs)
                        test.assertEqual(kwargs["args"].rpo_alpha, 1.0)
                        self.model, self.ref_model = kwargs["model"], kwargs["ref_model"]
                        self.train_dataset = kwargs["train_dataset"]
                        self.state = SimpleNamespace(global_step=0)
                        self.callbacks = kwargs["callbacks"]
                        self.accelerator = SimpleNamespace(unwrap_model=lambda m: m)
                        if corrupt_tokens: self.train_dataset[0]["chosen_input_ids"] = [3]

                    def compute_loss(self, model, inputs, *args, **kwargs):
                        test.assertEqual(model.active_adapter, "policy")
                        with self.null_ref_context():
                            test.assertEqual(model(), "reference")
                            test.assertFalse(any(p.requires_grad for _, p in model.named_parameters()))
                        test.assertEqual(model.active_adapter, "policy")
                        test.assertTrue(all(p.requires_grad for _, p in once.policy_parameters(model)))
                        return 1.0

                    def train(self, resume_from_checkpoint):
                        test.assertIsNone(resume_from_checkpoint)
                        for i, row in enumerate(self.train_dataset, 1):
                            self.compute_loss(self.model, {"prompt_input_ids": [row["prompt_input_ids"]]})
                            if i % 8 == 0:
                                self.model.parameters["layer.lora_A.policy.weight"].value += 0.01
                                self.state.global_step += 1
                                for callback in self.callbacks:
                                    callback.on_step_end(None, self.state, None)
                                    callback.on_log(None, self.state, None, {"loss": 1.0, "grad_norm": 0.5})

                modules = {"torch": TORCH, "datasets": SimpleNamespace(Dataset=SimpleNamespace(from_list=lambda rows: rows)),
                    "transformers": SimpleNamespace(TrainerCallback=object),
                    "trl": SimpleNamespace(DPOConfig=lambda **kw: SimpleNamespace(**kw), DPOTrainer=FakeTrainer)}
                with patch.dict(sys.modules, modules):
                    backend.initial_hash = once.tensor_fingerprint(once.policy_parameters(backend.model))
                    if corrupt_tokens:
                        with self.assertRaisesRegex(ValueError, "trainer_changed_tokens"):
                            backend.train()
                        self.assertEqual(backend.report["optimizer_steps"], 0)
                        self.assertNotIn("reference_forward_calls", backend.report)
                    else:
                        backend.train()
                        self.assertEqual(backend.report["training_pairs_seen"], 2400)
                        self.assertEqual(backend.report["reference_forward_calls"], 2400)
                        self.assertEqual(backend.report["optimizer_steps"], 300)
                        self.assertEqual(backend.report["final_reference_sha256"], backend.initial_hash)
                        self.assertNotEqual(backend.report["final_policy_sha256"], backend.initial_hash)
                        self.assertEqual(set(backend.report["saved_adapters"]), {"checkpoint-150", "candidate-adapter"})
                        self.assertEqual(backend.report["generation_calls"], {"canary": 10})
                        self.assertGreaterEqual(backend.flush.call_count, 300)


if __name__ == "__main__":
    unittest.main()
