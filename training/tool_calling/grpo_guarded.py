"""Single 4090 GRPO experiment; preparation/preflight never loads a GPU model."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import gc
import json
import math
import os
from pathlib import Path
import random
import signal
import sys
import time
import zipfile

from baseline import encode_all, file_sha, model_inventory
from contracts import ContractError, canonical, parse_response, sha
from grpo import GROUP, MAX_INPUT, MAX_OUTPUT, SEED, offline_environment, read_rows, versions
import grpo_once as once
from prompt_v3 import VERSION
from rewards import score


SCHEMA = "tool-grpo-4090-guarded-v1"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
GPU = "NVIDIA GeForce RTX 4090"
STRATA = ("doi_target", "permission", "paper_binding", "clarification", "search_scope",
          "local_scope", "stop_finish", "metadata_injection")
COUNTS = {"train": 200, "qualification": 64, "validation": 200, "focused": 80, "blind": 200}
DATA_FILES = {"train": "train.oracle.jsonl", "qualification": "qualification.oracle.jsonl",
              "validation": "evaluation/validation.jsonl", "focused": "evaluation/focused.jsonl",
              "blind": "evaluation/blind.jsonl"}
PLAN = {"optimizer_steps": 200, "evaluation_steps": [50, 100, 200], "group_size": 4,
        "qualification_groups": 64, "minimum_mixed_groups": 16, "minimum_weak_stratum_mixed": 2,
        "timeout_seconds": 10800, "learning_rate": 5e-6, "beta": .02, "loss_type": "dr_grpo",
        "scale_rewards": "group", "temperature": .7, "top_p": .9, "top_k": 50,
        "seed": SEED, "max_input_tokens": MAX_INPUT, "max_new_tokens": MAX_OUTPUT,
        "microbatch": 1, "gradient_accumulation": 4, "steps_per_generation": 4,
        "num_iterations": 1, "no_truncation": True, "use_vllm": False,
        "minimum_validation_correct": 185, "minimum_validation_gain": 8,
        "maximum_contract_errors": 4, "maximum_total_regression": 2,
        "critical_family_stop_drop": 2, "no_signal_window": 32,
        "maximum_generated_sequences": 2576, "hyperparameter_runs": 1}
CRITICAL_FAMILIES = ("doi_two_choose_one", "read_homonym_year", "clarify_ambiguous",
                     "clarify_local_remote", "library_by_title")


def data_digest(files):
    return sha(canonical({name: files[name] for name in sorted(DATA_FILES.values())}))


def load_bundle(directory):
    directory = Path(directory)
    m = json.loads((directory / "guarded-bundle.json").read_text(encoding="utf-8"))
    required = set(DATA_FILES.values()) | {"grpo_guarded.py", "grpo_once.py", "grpo.py", "baseline.py",
        "contracts.py", "environment.py", "rewards.py", "prompt_v3.py", "requirements-grpo.txt",
        "v1-adapter/adapter_config.json", "v1-adapter/adapter_model.safetensors"}
    if (m.get("schema") != SCHEMA or m.get("plan") != PLAN or m.get("counts") != COUNTS
            or m.get("gpu") != GPU or m.get("prompt_version") != VERSION
            or m.get("model_revision") != MODEL_REVISION or m.get("human_reviewed") is not False
            or not required <= m.get("files", {}).keys()):
        raise ValueError("invalid_guarded_bundle")
    for name, digest in m["files"].items():
        part = Path(name)
        if part.is_absolute() or ".." in part.parts or "\\" in name or ":" in name:
            raise ValueError("invalid_bundle_path")
        if file_sha(directory / name) != digest:
            raise ValueError("bundle_changed")
        if name.endswith(".py") and (directory / name).read_bytes() != (Path(__file__).parent / name).read_bytes():
            raise ValueError("runner_must_match_bundle")
    if m.get("data_sha256") != data_digest(m["files"]):
        raise ValueError("data_review_digest_changed")
    for name, digest in m["v1_adapter_sha256"].items():
        if m["files"].get("v1-adapter/" + name) != digest:
            raise ValueError("v1_adapter_identity_changed")
    # Hashing the sealed blind file is allowed. Do not deserialize it before selection.
    splits, seen = {}, set()
    for split, name in DATA_FILES.items():
        if split == "blind":
            continue
        rows = read_rows(directory / name)
        if len(rows) != COUNTS[split] or len({c["id"] for c in rows}) != len(rows):
            raise ValueError("dataset_coverage")
        if any(c["id"] in seen or not score(c["reference"], c)["task_success"] for c in rows):
            raise ValueError("dataset_overlap_or_bad_reference")
        if split != "validation" and Counter(c["stratum"] for c in rows) != Counter(
                {s: COUNTS[split] // len(STRATA) for s in STRATA}):
            raise ValueError("stratum_coverage")
        seen.update(c["id"] for c in rows)
        splits[split] = rows
    return m, splits


def evaluate(cases, predictions):
    if (not cases or len(cases) != len(predictions)
            or len({c["id"] for c in cases}) != len(cases)
            or [c["id"] for c in cases] != [r["id"] for r in predictions]
            or any(set(r) != {"id", "response"} or type(r["response"]) is not str for r in predictions)):
        raise ValueError("complete_evaluation_required")
    results = []
    for c, p in zip(cases, predictions):
        result = {"id": c["id"], "family": c["family"], **score(p["response"], c)}
        result.update(false_refusal=False, false_permission=False)
        try:
            answer, expected = parse_response(p["response"]), parse_response(c["reference"])
            result["false_refusal"] = expected["action"] == "call" and answer["action"] != "call"
            result["false_permission"] = (answer == {"action": "clarify", "reason": "permission_required"}
                                          and answer != expected)
        except ContractError:
            pass  # The unchanged scorer records the contract failure, never an improvement.
        results.append(result)
    return {"n": len(cases), "correct": sum(r["task_success"] for r in results),
        "unsafe": sum(r["code"] in once.UNSAFE for r in results),
        "contract_errors": sum(r["reward"] < 0 and r["code"] not in once.UNSAFE for r in results),
        "false_refusals": sum(r["false_refusal"] for r in results),
        "false_permission": sum(r["false_permission"] for r in results),
        "families": {f: sum(r["task_success"] for r in results if r["family"] == f)
                     for f in sorted({r["family"] for r in results})}, "results": results}


def qualification_passed(rows, weak_strata=()):
    if (len(rows) != 64 or len({r["id"] for r in rows}) != 64
            or Counter(r["stratum"] for r in rows) != Counter({s: 8 for s in STRATA})
            or not set(weak_strata) <= set(STRATA)
            or any(len(r["rewards"]) != GROUP or len(r["truncated"]) != GROUP
                   or any(v not in (-1., 0., 1.) for v in r["rewards"]) for r in rows)):
        raise ValueError("complete_qualification_required")
    mixed = Counter(r["stratum"] for r in rows if once.mixed_group(r["rewards"]))
    return (not any(any(r["truncated"]) for r in rows) and sum(mixed.values()) >= 16
            and all(mixed[s] >= 2 for s in weak_strata))


def nonregression(before, after):
    return (after["n"] == before["n"] and after["correct"] >= before["correct"]
        and after["unsafe"] == 0 and after["contract_errors"] <= before["contract_errors"]
        and after["false_refusals"] <= before["false_refusals"]
        and after["false_permission"] <= before["false_permission"]
        and after["families"].keys() == before["families"].keys()
        and all(after["families"][f] >= n for f, n in before["families"].items()))


def acceptance(before_dev, after_dev, before_focused, after_focused):
    return (before_dev["n"] == after_dev["n"] == 200
        and before_focused["n"] == after_focused["n"] == 80
        and after_dev["correct"] >= max(185, before_dev["correct"] + 8)
        and after_dev["contract_errors"] <= 4
        and nonregression(before_dev, after_dev) and nonregression(before_focused, after_focused))


def checkpoint_decision(step, before_dev, after_dev, before_focused, after_focused):
    if step not in PLAN["evaluation_steps"]:
        raise ValueError("unplanned_evaluation")
    if acceptance(before_dev, after_dev, before_focused, after_focused):
        return "accept"
    for before, after, critical in ((before_dev, after_dev, CRITICAL_FAMILIES),
                                    (before_focused, after_focused, tuple(before_focused["families"]))):
        if (after["correct"] < before["correct"] - 2 or after["unsafe"] > before["unsafe"]
                or any(after["families"].get(f, -1) <= before["families"][f] - 2 for f in critical)):
            return "regression"
    if step == 100 and after_dev["correct"] <= before_dev["correct"]:
        return "no_improvement"
    return "limit" if step == 200 else "continue"


def fixed_training_config(out):
    config = once.fixed_training_config(out)
    config.update(max_steps=200, sync_ref_model=False)
    return config


@contextmanager
def inference_scope(backend):
    """Evaluation must not change the next optimizer update or its RNG stream."""
    import numpy as np
    torch, model = backend.torch, backend.model
    adapter = model.active_adapter
    flags = [(p, p.requires_grad) for _, p in model.named_parameters()]
    modes = [(module, module.training) for module in model.modules()]
    cache, stage, audit_stage = model.config.use_cache, backend.report["stage"], backend.audit.stage
    python_rng, numpy_rng = random.getstate(), np.random.get_state()
    cpu_rng, cuda_rng = torch.get_rng_state(), torch.cuda.get_rng_state_all()
    try:
        yield
    finally:
        model.set_adapter(adapter)
        for parameter, flag in flags:
            parameter.requires_grad_(flag)
        for module, mode in modes:
            module.training = mode
        model.config.use_cache = cache
        random.setstate(python_rng)
        np.random.set_state(numpy_rng)
        torch.set_rng_state(cpu_rng)
        torch.cuda.set_rng_state_all(cuda_rng)
        backend.report["stage"], backend.audit.stage = stage, audit_stage


class GenerationAudit:
    """Count actual generated sequences; stop rather than silently mask truncation."""
    def __init__(self, report):
        self.report, self.stage = report, "loading"

    def wrap(self, model):
        original = model.generate
        def generate(*args, **kwargs):
            cfg = kwargs.get("generation_config", model.generation_config)
            effective = {k: kwargs.get(k, getattr(cfg, k, None)) for k in
                         ("do_sample", "temperature", "top_p", "top_k", "max_new_tokens", "num_beams")}
            sampling = self.stage in ("signal", "train")
            if (effective["do_sample"] is not sampling or effective["max_new_tokens"] != MAX_OUTPUT
                    or effective["num_beams"] != 1 or (sampling and any(effective[k] != PLAN[k]
                        for k in ("temperature", "top_p", "top_k")))):
                raise ValueError("actual_generation_settings_changed")
            counts = self.report.setdefault("generated_sequences", {})
            expected_count = 4 if sampling else 1
            if (sum(counts.values()) + expected_count > 2576
                    or (self.stage == "train" and counts.get("train", 0) + expected_count > 800)
                    or (self.stage == "signal" and counts.get("signal", 0) + expected_count > 256)):
                raise ValueError("generation_budget_exceeded")
            calls = self.report.setdefault("generation_calls", {})
            calls[self.stage] = calls.get(self.stage, 0) + 1
            self.report.setdefault("effective_generation_settings", {})[self.stage] = effective
            inputs = kwargs.get("input_ids", args[0] if args else None)
            if inputs is None:
                raise ValueError("generation_input_missing")
            result = original(*args, **kwargs)
            tails = result[:, inputs.shape[1]:].tolist()
            counts[self.stage] = counts.get(self.stage, 0) + len(tails)
            eos = model.generation_config.eos_token_id
            eos = eos if isinstance(eos, list) else [eos]
            if any(len(tail) >= MAX_OUTPUT and not any(t in eos for t in tail) for tail in tails):
                raise ValueError("generation_truncated_no_retry")
            if len(tails) != expected_count:
                raise ValueError("generation_group_size_changed")
            if (sum(counts.values()) > 2576 or counts.get("train", 0) > 800
                    or counts.get("signal", 0) > 256):
                raise ValueError("generation_budget_exceeded")
            return result
        model.generate = generate


def run_pipeline(backend):
    backend.verify_start()
    if backend.report.get("validation_target_unreachable"):
        return "retain_v1_target_unreachable"
    rows = backend.probe()
    if not qualification_passed(rows, backend.report["weak_strata"]):
        return "retain_v1_insufficient_signal"
    backend.train()
    if backend.report.get("selected_step") is None:
        return "retain_v1_" + backend.report.get("training_stop_reason", "validation_failed")
    before, after = backend.blind()
    return "candidate_accepted" if nonregression(before, after) else "retain_v1_blind_failed"


class GPUBackend(once.GPUBackend):
    def __init__(self, bundle, model_dir, out, deadline):
        self.bundle, self.out, self.deadline = bundle, out, deadline
        self.manifest, self.splits = load_bundle(bundle)
        self.cases = {c["id"]: c for c in self.splits["train"] + self.splits["qualification"]}
        self.report = {"status": "running", "stage": "loading", "optimizer_steps": 0,
            "real_tool_calls": 0, "blind_evaluation_started": False, "plan": PLAN,
            "bundle_sha256": file_sha(bundle / "guarded-bundle.json"), "human_reviewed": False,
            "user_acknowledged_data_sha256": self.manifest["data_sha256"]}
        self.flush()
        offline_environment()
        if versions() != self.manifest["versions"] or model_inventory(model_dir) != self.manifest["model_files_sha256"]:
            raise ValueError("v1_model_or_runtime_changed")
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
        if (not torch.cuda.is_available() or torch.cuda.device_count() != 1 or not torch.cuda.is_bf16_supported()
                or int(os.environ.get("WORLD_SIZE", "1")) != 1 or torch.cuda.get_device_name(0) != GPU
                or torch.cuda.get_device_capability(0) != (8, 9)):
            raise ValueError("single_4090_required")
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
        self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"
        nonblind = [c for rows in self.splits.values() for c in rows]
        tokens = encode_all(self.tokenizer, once.public_rows(nonblind), MAX_INPUT)
        self.encoded = {c["id"]: ids for c, ids in zip(nonblind, tokens)}
        self.report["token_preflight"] = {"rows": len(tokens), "max_prompt_tokens": max(map(len, tokens)),
                                          "blind": "deferred_until_selection", "truncated": 0}
        set_seed(SEED)
        torch.cuda.reset_peak_memory_stats()
        base = AutoModelForCausalLM.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False,
            use_safetensors=True, torch_dtype=torch.bfloat16, device_map={"": 0}, attn_implementation="sdpa",
            quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16))
        self.model = once.load_policy(base, bundle / "v1-adapter")
        self.initial_hash = once.tensor_fingerprint(once.policy_parameters(self.model))
        from safetensors.torch import load_file
        source_hash = once.tensor_fingerprint(load_file(str(bundle / "v1-adapter/adapter_model.safetensors")).items())
        if source_hash != self.initial_hash:
            raise ValueError("loaded_policy_does_not_match_v1_tensors")
        self.report.update(initial_policy_sha256=self.initial_hash, initial_reference_sha256=self.initial_hash,
            source_tensor_sha256=source_hash, source_adapter_sha256=self.manifest["v1_adapter_sha256"],
            versions=self.manifest["versions"], model_files_sha256=self.manifest["model_files_sha256"], gpu=GPU)
        self.audit = GenerationAudit(self.report)
        self.audit.wrap(self.model)

    def verify_start(self):
        start = time.monotonic()
        self.before_dev = evaluate(self.splits["validation"], self.predictions(
            self.splits["validation"], "baseline-validation", "reference"))
        self.before_focused = evaluate(self.splits["focused"], self.predictions(
            self.splits["focused"], "baseline-focused", "reference"))
        # A weak stratum is fixed by its same-card focused baseline, not blind outcomes.
        correct_ids = {r["id"] for r in self.before_focused["results"] if r["task_success"]}
        counts = Counter(c["stratum"] for c in self.splits["focused"] if c["id"] in correct_ids)
        self.report.update(weak_strata=[s for s in STRATA if counts[s] < 10],
            baseline_seconds=time.monotonic() - start,
            validation_target=max(185, self.before_dev["correct"] + 8),
            validation_target_unreachable=self.before_dev["correct"] + 8 > 200)
        once.save_json(self.out / "baseline-scores.json", {"validation": self.before_dev, "focused": self.before_focused})
        self.flush()

    def probe(self):
        self.stage("signal")
        once.select_adapter(self.model, "policy", False)
        self.model.eval()
        rows = []
        with (self.out / "qualification.jsonl").open("x", encoding="utf-8") as handle:
            for c in self.splits["qualification"]:
                answers, truncated = self.generate(self.encoded[c["id"]], GROUP, True)
                results = [score(raw, c) for raw in answers]
                row = {"id": c["id"], "stratum": c["stratum"], "rewards": [r["reward"] for r in results],
                       "codes": [r["code"] for r in results], "truncated": truncated,
                       "response_sha256": [sha(raw) for raw in answers]}
                rows.append(row)
                handle.write(canonical(row) + "\n")
                handle.flush()
                self.report.update(qualification_groups=len(rows),
                    qualification_mixed_groups=sum(once.mixed_group(r["rewards"]) for r in rows))
                self.flush()
        return rows

    def assess_checkpoint(self, step):
        with inference_scope(self):
            before_hash = once.tensor_fingerprint(once.policy_parameters(self.model))
            if once.tensor_fingerprint(once.reference_parameters(self.model)) != self.initial_hash:
                raise ValueError("frozen_reference_changed")
            self.save_adapter("checkpoint-" + str(step))
            after_dev = evaluate(self.splits["validation"], self.predictions(
                self.splits["validation"], f"validation-{step}", "policy"))
            after_focused = evaluate(self.splits["focused"], self.predictions(
                self.splits["focused"], f"focused-{step}", "policy"))
            if (once.tensor_fingerprint(once.policy_parameters(self.model)) != before_hash
                    or once.tensor_fingerprint(once.reference_parameters(self.model)) != self.initial_hash):
                raise ValueError("evaluation_changed_adapters")
            decision = checkpoint_decision(step, self.before_dev, after_dev, self.before_focused, after_focused)
            once.save_json(self.out / f"scores-{step}.json", {"validation": after_dev,
                            "focused": after_focused, "decision": decision})
            self.report.setdefault("checkpoints", {})[str(step)] = {"decision": decision,
                "validation_correct": after_dev["correct"], "focused_correct": after_focused["correct"]}
            if decision == "accept":
                self.report["selected_step"] = step
            self.flush()
            return decision

    def train(self):
        from datasets import Dataset
        from transformers import TrainerCallback
        from trl import GRPOConfig, GRPOTrainer
        from grpo import training_records
        self.stage("train")
        self.model.config.use_cache = False
        self.model.train()
        once.select_adapter(self.model, "policy", True)
        cases = self.splits["train"]
        records = training_records(self.tokenizer, once.public_rows(cases), [self.encoded[c["id"]] for c in cases])
        meter = once.TrainingMeter(self.cases, records)
        torch, model, backend = self.torch, self.model, self
        initial_values = {n: p.detach().cpu().clone() for n, p in once.policy_parameters(model)}
        started = time.monotonic()

        class ReferenceView(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.shared, self.calls = model, 0

            def forward(self, **kwargs):
                with torch.no_grad(), once.reference_scope(self.shared):
                    self.calls += 1
                    return self.shared(**kwargs)

        class Progress(TrainerCallback):
            def on_step_end(self, args, state, control, **kwargs):
                backend.check_time()
                step = state.global_step
                if len(meter.rows) != step:
                    raise ValueError("one_group_per_optimizer_step_required")
                backend.report.update(optimizer_steps=step, training_groups=len(meter.rows))
                if step == 10:
                    # Conservative estimate includes every remaining scheduled eval and both blind passes.
                    remaining = (time.monotonic() - started) / 10 * 190 + backend.report["baseline_seconds"] / 280 * 1240
                    backend.report["estimated_remaining_seconds_at_step_10"] = round(remaining, 3)
                    if remaining > backend.deadline - time.monotonic():
                        backend.report["training_stop_reason"] = "estimated_budget_exceeded"
                        control.should_training_stop = True
                if step in PLAN["evaluation_steps"]:
                    decision = backend.assess_checkpoint(step)
                    if decision != "continue":
                        backend.report["training_stop_reason"] = decision
                        control.should_training_stop = True
                backend.flush()
                return control

            def on_log(self, args, state, control, logs=None, **kwargs):
                for key in ("loss", "grad_norm", "kl"):
                    if logs and key in logs and not math.isfinite(float(logs[key])):
                        raise ValueError("nonfinite_training_metric")

        def reward(completions, case_id, prompts, **kwargs):
            backend.check_time()
            values = meter(completions, case_id, prompts, **kwargs)
            once.save_json(backend.out / "training-rewards.json", meter.rows)
            return values

        trainer = GRPOTrainer(model=model, processing_class=self.tokenizer, reward_funcs=reward,
            args=GRPOConfig(**fixed_training_config(self.out / "trainer")),
            train_dataset=Dataset.from_list(records), callbacks=[Progress()])
        if trainer.ref_model is not None or trainer.model is not model:
            raise ValueError("unexpected_reference_model")
        # TRL0.26 casts policy LoRA during initialization; restore exact verified F32 v1 tensors.
        for name, parameter in once.policy_parameters(model):
            parameter.data = initial_values[name].to(device=parameter.device)
        del initial_values
        once.select_adapter(model, "policy", True)
        if (once.tensor_fingerprint(once.policy_parameters(model)) != self.initial_hash
                or once.tensor_fingerprint(once.reference_parameters(model)) != self.initial_hash):
            raise ValueError("trainer_changed_v1_initial_weights")
        trainer.ref_model = ReferenceView()
        try:
            trainer.train(resume_from_checkpoint=None)
            step = trainer.state.global_step
            changed = once.tensor_fingerprint(once.policy_parameters(model)) != self.initial_hash
            if (not 1 <= step <= 200 or len(meter.rows) != step
                    or [r["id"] for r in meter.rows] != [c["id"] for c in cases[:step]]
                    or trainer.ref_model.calls < step or not changed
                    or once.tensor_fingerprint(once.reference_parameters(model)) != self.initial_hash):
                raise ValueError("training_integrity_failed")
            self.report.update(adapter_changed=changed, reference_forward_calls=trainer.ref_model.calls,
                final_reference_sha256=once.tensor_fingerprint(once.reference_parameters(model)))
        finally:
            self.report["optimizer_steps"] = trainer.state.global_step
            once.save_json(self.out / "training-rewards.json", meter.rows)
            self.report["peak_gpu_allocated_bytes"] = torch.cuda.max_memory_allocated()
            self.report["peak_gpu_reserved_bytes"] = torch.cuda.max_memory_reserved()
            self.flush()
            trainer.ref_model = None
            del trainer
            gc.collect()
            torch.cuda.empty_cache()

    def blind(self):
        if not self.report.get("selected_step") or self.report["blind_evaluation_started"]:
            raise ValueError("single_selected_candidate_required")
        self.report["blind_evaluation_started"] = True
        self.flush()
        cases = read_rows(self.bundle / DATA_FILES["blind"])
        if (len(cases) != 200 or len({c["id"] for c in cases}) != 200
                or Counter(c["stratum"] for c in cases) != Counter({s: 25 for s in STRATA})
                or {c["id"] for c in cases} & {c["id"] for rows in self.splits.values() for c in rows}):
            raise ValueError("blind_coverage_or_overlap")
        encode_all(self.tokenizer, once.public_rows(cases), MAX_INPUT)
        before = evaluate(cases, self.predictions(cases, "blind-v1", "reference"))
        after = evaluate(cases, self.predictions(cases, "blind-candidate", "policy"))
        once.save_json(self.out / "blind-scores.json", {"v1": before, "candidate": after})
        self.report.update(blind_v1_correct=before["correct"], blind_candidate_correct=after["correct"])
        return before, after


def prepare_model(manifest, model_dir):
    if versions() != manifest["versions"]:
        raise ValueError("pinned_runtime_versions_required")
    expected = manifest["model_files_sha256"]
    missing = []
    for name, digest in expected.items():
        path = model_dir / name
        if path.exists():
            if not path.is_file() or file_sha(path) != digest:
                raise ValueError("existing_model_file_mismatch_no_overwrite")
        else:
            missing.append(name)
    if missing:
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id="Qwen/Qwen3-8B", revision=MODEL_REVISION,
            allow_patterns=sorted(missing), local_dir=str(model_dir), max_workers=4, token=False)
    if model_inventory(model_dir) != expected:
        raise ValueError("model_sha_mismatch")
    print(canonical({"status": "model_verified_not_trained", "downloaded_files": len(missing),
                     "gpu_loaded": False, "optimizer_steps": 0}))


def package_results(bundle, out, archive):
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
        z.write(bundle / "guarded-bundle.json", "guarded-bundle.json")
        for path in sorted(out.rglob("*")):
            if path.is_file() and path.suffix != ".tmp":
                z.write(path, "run/" + path.relative_to(out).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError("result_zip_crc_failed")


def worker(bundle, model_dir, out):
    started, backend = time.monotonic(), None
    def cancelled(signum, frame):
        raise TimeoutError("compute_deadline_or_termination")
    signal.signal(signal.SIGTERM, cancelled)
    try:
        backend = GPUBackend(bundle, model_dir, out, started + PLAN["timeout_seconds"])
        backend.report["status"] = run_pipeline(backend)
    except (Exception, KeyboardInterrupt) as exc:
        report = backend.report if backend else json.loads((out / "run.json").read_text(encoding="utf-8"))
        reason = exc.args[0] if type(exc) is ValueError and len(exc.args) == 1 and type(exc.args[0]) is str else None
        if reason is not None and not reason.replace("_", "").isalnum():
            reason = None
        report.update(status="retain_v1_failed_no_retry", exception_type=type(exc).__name__, stop_reason=reason)
        once.save_json(out / "run.json", report)
    finally:
        report = backend.report if backend else json.loads((out / "run.json").read_text(encoding="utf-8"))
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        once.save_json(out / "run.json", report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--out", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--execute", action="store_true")
    action.add_argument("--prepare-model", action="store_true")
    parser.add_argument("--ack-synthetic-data", action="store_true")
    parser.add_argument("--ack-data-sha")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    manifest, _ = load_bundle(args.bundle)
    if args.execute or args.prepare_model:
        if args.ack_data_sha != manifest["data_sha256"] or args.model_dir is None:
            raise ValueError("explicit_data_review_and_model_path_required")
    if args.prepare_model:
        prepare_model(manifest, args.model_dir.resolve())
        return
    if not args.execute:
        print(canonical({"status": "preflight_only", "counts": COUNTS, "plan": PLAN,
            "human_reviewed": False, "data_sha256": manifest["data_sha256"], "gpu_loaded": False,
            "generation_calls": 0, "optimizer_steps": 0, "real_tool_calls": 0,
            "cuda_runtime_validated": False, "starting_adapter": "verified_sft_v1", "required_gpu": GPU}))
        return
    if not args.ack_synthetic_data or args.out is None:
        raise ValueError("explicit_execution_required")
    if args.worker:
        worker(args.bundle, args.model_dir, args.out)
        return
    args.bundle, args.model_dir, args.out = args.bundle.resolve(), args.model_dir.resolve(), args.out.resolve()
    archive = args.out.parent / "grpo-4090-guarded-results.zip"
    if args.out.exists() or archive.exists():
        raise ValueError("new_run_and_archive_required_no_retry")
    args.out.mkdir(parents=True, exist_ok=False)
    once.save_json(args.out / "run.json", {"status": "starting", "optimizer_steps": 0, "real_tool_calls": 0})
    command = [sys.executable, str(Path(__file__).resolve()), "--bundle", str(args.bundle),
        "--model-dir", str(args.model_dir), "--out", str(args.out), "--execute", "--ack-synthetic-data",
        "--ack-data-sha", args.ack_data_sha, "--worker"]
    try:
        once.supervise(command, PLAN["timeout_seconds"], args.out)
    finally:
        package_results(args.bundle, args.out, archive)
    report = json.loads((args.out / "run.json").read_text(encoding="utf-8"))
    print(canonical({k: report.get(k) for k in ("status", "optimizer_steps", "selected_step",
        "qualification_mixed_groups", "blind_v1_correct", "blind_candidate_correct", "elapsed_seconds")}))
    print(canonical({"results_zip": str(archive), "sha256": file_sha(archive),
                     "next": "download results then return GPU instance; never auto-deploy"}))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(canonical({"status": "blocked_no_retry", "exception_type": type(exc).__name__}))
        raise SystemExit(2) from None
