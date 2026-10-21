"""Single RPO continuation from verified SFT v1. Default: CPU preflight, no downloads."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import gc
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
import zipfile

from baseline import encode_all, file_sha, model_inventory
from contracts import canonical, parse_response
from grpo import MAX_INPUT, MAX_OUTPUT, SEED, offline_environment, read_rows, versions
import grpo_once as once
from rewards import score

SCHEMA = "tool-rpo-v1"
PLAN = {"method": "sigmoid_dpo_plus_chosen_nll", "rpo_alpha": 1.0, "beta": 0.1,
        "learning_rate": 5e-6, "epochs": 1, "batch_size": 1, "gradient_accumulation": 8,
        "optimizer_steps": 300, "seed": SEED, "timeout_seconds": 14400,
        "max_prompt_tokens": MAX_INPUT, "max_answer_tokens": MAX_OUTPUT,
        "no_truncation": True, "hyperparameter_runs": 1}
RUNTIME = {"rpo.py", "grpo_once.py", "grpo.py", "baseline.py", "contracts.py", "environment.py",
           "rewards.py", "prompt_v3.py", "requirements-grpo.txt", "run_rpo_once.sh", "CLOUD_RPO.txt"}
ARTIFACTS = {"train.oracle.jsonl", "preferences.jsonl", "evaluation/validation.jsonl",
             "evaluation/blind_test.jsonl", "v1-validation.predictions.jsonl", "data-audit.json",
             "review-sample.txt", "v1-adapter/adapter_config.json", "v1-adapter/adapter_model.safetensors"}


def lowered_upper_bound(case, chosen, rejected):
    """A smaller request within an explicit ceiling is not a proven error."""
    if not any(word in case["instruction"] for word in ("最多", "上限")):
        return False
    if (chosen.get("action") != "call" or rejected.get("action") != "call"
            or chosen.get("tool") != rejected.get("tool")
            or chosen["tool"] not in ("search_papers", "search_library")):
        return False
    key = "per_source" if chosen["tool"] == "search_papers" else "limit"
    a, b = dict(chosen["arguments"]), dict(rejected["arguments"])
    upper, lower = a.pop(key), b.pop(key)
    return a == b and lower < upper


def load_bundle(directory):
    m = json.loads((directory / "rpo-bundle.json").read_text(encoding="utf-8"))
    if (m.get("schema") != SCHEMA or m.get("plan") != PLAN or m.get("human_reviewed") is not False
            or set(m.get("files", {})) != RUNTIME | ARTIFACTS
            or m.get("counts") != {"train_prompts": 1200, "preference_pairs": 2400,
                                   "validation": 200, "blind_test": 200}):
        raise ValueError("invalid_rpo_bundle")
    for name, digest in m["files"].items():
        if file_sha(directory / name) != digest:
            raise ValueError("bundle_changed")
        if name in RUNTIME and (directory / name).read_bytes() != (Path(__file__).parent / name).read_bytes():
            raise ValueError("runtime_must_match_bundle")
    if file_sha(directory / "preferences.jsonl") != m["preferences_sha256"]:
        raise ValueError("preference_hash_changed")
    review = m.get("sample_rule_review")
    if review is not None:
        audit = json.loads((directory / "data-audit.json").read_text(encoding="utf-8"))
        if (review.get("status") != "approved_with_deterministic_corrections"
                or review.get("corrected_preferences_sha256") != m["preferences_sha256"]
                or review.get("all_examples_individually_reviewed") is not False
                or audit.get("sample_rule_review") != review):
            raise ValueError("sample_review_record_mismatch")
    cases, pairs = read_rows(directory / "train.oracle.jsonl"), read_rows(directory / "preferences.jsonl")
    lookup = {c["id"]: c for c in cases}
    if len(cases) != 1200 or len(lookup) != 1200 or len(pairs) != 2400:
        raise ValueError("training_coverage")
    if Counter(p["case_id"] for p in pairs) != Counter({i: 2 for i in lookup}):
        raise ValueError("preference_coverage")
    if len({p["id"] for p in pairs}) != 2400:
        raise ValueError("duplicate_preferences")
    for p in pairs:
        c = lookup[p["case_id"]]
        parse_response(p["rejected"])
        if lowered_upper_bound(c, parse_response(p["chosen"]), parse_response(p["rejected"])):
            raise ValueError("ambiguous_lowered_upper_bound")
        if (c["split"] != "train" or p["chosen"] != c["reference"]
                or not score(p["chosen"], c)["task_success"] or score(p["rejected"], c)["task_success"]):
            raise ValueError("preference_direction_invalid")
    for split in ("validation", "blind_test"):
        held = read_rows(directory / "evaluation" / (split + ".jsonl"))
        if len(held) != 200 or {c["id"] for c in held} & lookup.keys():
            raise ValueError("evaluation_overlap")
    baseline = once.evaluate(read_rows(directory / "evaluation/validation.jsonl"),
                             read_rows(directory / "v1-validation.predictions.jsonl"))
    if {k: baseline[k] for k in m["v1_validation"]} != m["v1_validation"] or baseline["correct"] != 180:
        raise ValueError("verified_v1_baseline_required")
    return m, lookup, pairs


def training_records(tokenizer, cases, pairs):
    """Exact inference prefix; only answer+EOS is supervised. Never truncate."""
    public = once.public_rows(list(cases.values()))
    encoded = encode_all(tokenizer, public, MAX_INPUT)
    prompts = {}
    for row, ids in zip(public, encoded):
        text = tokenizer.apply_chat_template(row["prompt"], tokenize=False,
                                            add_generation_prompt=True, enable_thinking=False)
        if tokenizer(text, add_special_tokens=False)["input_ids"] != ids:
            raise ValueError("training_inference_prompt_mismatch")
        prompts[row["id"]] = (text, ids)
    records, expected = [], []
    for pair in pairs:
        text, ids = prompts[pair["case_id"]]
        record = {"prompt": text, "chosen": pair["chosen"], "rejected": pair["rejected"]}
        check = {"prompt_input_ids": ids}
        for field in ("chosen", "rejected"):
            answer = tokenizer(record[field], add_special_tokens=False)["input_ids"]
            if not answer or tokenizer.eos_token_id in answer or len(answer) + 1 > MAX_OUTPUT:
                raise ValueError("answer_tokens_invalid_no_truncation")
            if tokenizer.decode(answer, skip_special_tokens=False, clean_up_tokenization_spaces=False) != record[field]:
                raise ValueError("answer_token_roundtrip_failed")
            check[field + "_input_ids"] = answer + [tokenizer.eos_token_id]
        records.append(record)
        expected.append(check)
    return records, expected


def config(out):
    return dict(output_dir=str(out), beta=PLAN["beta"], rpo_alpha=PLAN["rpo_alpha"], loss_type="sigmoid",
        learning_rate=PLAN["learning_rate"], num_train_epochs=1, per_device_train_batch_size=1,
        gradient_accumulation_steps=8, lr_scheduler_type="constant", warmup_steps=0, weight_decay=0,
        max_grad_norm=1.0, bf16=True, fp16=False, optim="adamw_torch", seed=SEED, data_seed=SEED,
        gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
        max_prompt_length=None, max_completion_length=None, max_length=None, use_logits_to_keep=True,
        model_adapter_name="policy", ref_adapter_name="reference", disable_dropout=True,
        sync_ref_model=False, reference_free=False, precompute_ref_log_probs=False, generate_during_eval=False,
        eval_strategy="no", save_strategy="no", logging_steps=1, report_to="none", push_to_hub=False,
        dataloader_num_workers=0, dataloader_pin_memory=False, remove_unused_columns=False)


def dev_passed(before, after):
    return (before["n"] == after["n"] == 200 and after["correct"] > before["correct"]
        and after["unsafe"] == 0 and after["contract_errors"] <= before["contract_errors"]
        and set(before["families"]) == set(after["families"])
        and all(after["families"][f] >= n for f, n in before["families"].items()))


def blind_passed(before, after):
    return (once.blind_passed(before, after) and after["unsafe"] == 0
            and set(before["families"]) == set(after["families"])
            and all(after["families"][f] >= n for f, n in before["families"].items()))


def pipeline(backend):
    backend.verify_start()
    backend.train()
    if not dev_passed(*backend.validate()):
        return "retain_v1_dev_failed"
    return "candidate_passed_offline_only" if blind_passed(*backend.blind()) else "retain_v1_blind_failed"


class GPUBackend(once.GPUBackend):
    """Reuse the verified v1 loader helpers and evaluator; replace only training."""
    def __init__(self, bundle, model_dir, out, deadline):
        self.bundle, self.out, self.deadline = bundle, out, deadline
        self.manifest, self.cases, self.pairs = load_bundle(bundle)
        self.report = {"status": "running", "stage": "loading", "optimizer_steps": 0, "real_tool_calls": 0,
            "blind_evaluation_started": False, "plan": PLAN, "bundle_sha256": file_sha(bundle / "rpo-bundle.json"),
            "review_attestation": "caller_confirmed_sample_review_not_independent_label_certification"}
        self.flush()
        offline_environment()
        if versions() != self.manifest["versions"] or model_inventory(model_dir) != self.manifest["model_files_sha256"]:
            raise ValueError("v1_model_or_runtime_changed")
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
        if (not torch.cuda.is_available() or torch.cuda.device_count() != 1 or not torch.cuda.is_bf16_supported()
                or int(os.environ.get("WORLD_SIZE", "1")) != 1 or torch.cuda.get_device_name(0) != self.manifest["gpu"]):
            raise ValueError("matching_single_4090_required")
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
        self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"
        self.records, self.expected = training_records(self.tokenizer, self.cases, self.pairs)
        self.report["max_prompt_tokens_observed"] = max(len(r["prompt_input_ids"]) for r in self.expected)
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
            versions=self.manifest["versions"], gpu=self.manifest["gpu"])
        self.audit = once.GenerationAudit(self.report)
        self.audit.wrap(self.model)
        audited_generate = self.model.generate
        def evaluation_only(*args, **kwargs):
            if self.audit.stage == "train":
                raise ValueError("rpo_training_must_not_generate")
            return audited_generate(*args, **kwargs)
        self.model.generate = evaluation_only

    def train(self):
        from datasets import Dataset
        from transformers import TrainerCallback
        from trl import DPOConfig, DPOTrainer
        self.stage("train")
        backend, model = self, self.model
        model.config.use_cache = False
        model.train()
        once.select_adapter(model, "policy", True)

        class Trainer(DPOTrainer):
            @contextmanager
            def null_ref_context(self):
                with once.reference_scope(self.accelerator.unwrap_model(self.model)):
                    backend.report["reference_forward_calls"] = backend.report.get("reference_forward_calls", 0) + 1
                    yield

            def compute_loss(self, model, inputs, *args, **kwargs):
                backend.check_time()
                backend.report["training_pairs_seen"] = backend.report.get("training_pairs_seen", 0) + len(inputs["prompt_input_ids"])
                return super().compute_loss(model, inputs, *args, **kwargs)

        class Progress(TrainerCallback):
            def on_step_end(self, args, state, control, **kwargs):
                backend.check_time()
                backend.report["optimizer_steps"] = state.global_step
                if state.global_step == 150:
                    backend.save_adapter("checkpoint-150")
                backend.flush()

            def on_log(self, args, state, control, logs=None, **kwargs):
                for key in ("loss", "grad_norm", "nll_loss"):
                    if logs and key in logs:
                        value = float(logs[key])
                        if not math.isfinite(value):
                            raise ValueError("nonfinite_training_metric")
                        if key == "grad_norm" and value > 0:
                            backend.report["nonzero_gradient_steps"] = backend.report.get("nonzero_gradient_steps", 0) + 1

        # Existing PEFT model; passing a new peft_config would merge/reinitialize it.
        trainer = Trainer(model=model, ref_model=None, processing_class=self.tokenizer,
            args=DPOConfig(**config(self.out / "trainer")), train_dataset=Dataset.from_list(self.records),
            callbacks=[Progress()])
        try:
            if trainer.model is not model or trainer.ref_model is not None:
                raise ValueError("unexpected_model_or_reference")
            if len(trainer.train_dataset) != len(self.expected):
                raise ValueError("trainer_dataset_changed")
            for actual, expected in zip(trainer.train_dataset, self.expected):
                if any(actual.get(k) != v for k, v in expected.items()):
                    raise ValueError("trainer_changed_tokens_or_truncated")
            once.select_adapter(model, "policy", True)
            if (once.tensor_fingerprint(once.policy_parameters(model)) != self.initial_hash
                    or once.tensor_fingerprint(once.reference_parameters(model)) != self.initial_hash):
                raise ValueError("trainer_changed_v1_weights")
            trainer.train(resume_from_checkpoint=None)
            after = once.tensor_fingerprint(once.policy_parameters(model))
            ref = once.tensor_fingerprint(once.reference_parameters(model))
            if (trainer.state.global_step != 300 or self.report.get("training_pairs_seen") != 2400
                    or self.report.get("reference_forward_calls", 0) < 2400 or after == self.initial_hash
                    or ref != self.initial_hash or not self.report.get("nonzero_gradient_steps")
                    or self.report.get("generation_calls", {}).get("train", 0)):
                raise ValueError("complete_rpo_continuation_required")
            self.report.update(adapter_changed=True, final_policy_sha256=after, final_reference_sha256=ref)
            self.save_adapter("candidate-adapter")
        finally:
            self.report["optimizer_steps"] = trainer.state.global_step
            self.report["peak_gpu_allocated_bytes"] = self.torch.cuda.max_memory_allocated()
            self.report["peak_gpu_reserved_bytes"] = self.torch.cuda.max_memory_reserved()
            self.flush()
            del trainer
            gc.collect()
            self.torch.cuda.empty_cache()


def worker(bundle, model_dir, out):
    started, backend = time.monotonic(), None
    def terminate(signum, frame):
        raise TimeoutError("compute_deadline")
    signal.signal(signal.SIGTERM, terminate)
    try:
        backend = GPUBackend(bundle, model_dir, out, started + PLAN["timeout_seconds"])
        backend.report["status"] = pipeline(backend)
    except (Exception, KeyboardInterrupt) as exc:
        report = backend.report if backend else json.loads((out / "run.json").read_text())
        report.update(status="retain_v1_failed_no_retry", exception_type=type(exc).__name__)
        once.save_json(out / "run.json", report)
    finally:
        report = backend.report if backend else json.loads((out / "run.json").read_text())
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        once.save_json(out / "run.json", report)


def package_results(bundle, out, archive):
    report = json.loads((out / "run.json").read_text(encoding="utf-8"))
    saved = report.get("saved_adapters", {})
    retain = "candidate-adapter" if "candidate-adapter" in saved else "checkpoint-150"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
        z.write(bundle / "rpo-bundle.json", "rpo-bundle.json")
        for path in sorted(out.rglob("*")):
            rel = path.relative_to(out)
            if path.is_file() and ((len(rel.parts) == 1 and path.suffix in (".json", ".jsonl"))
                                  or (rel.parts[0] == retain and path.suffix in (".json", ".safetensors"))):
                z.write(path, "run/" + rel.as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError("archive_crc_failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ack-synthetic-data", action="store_true")
    parser.add_argument("--reviewed-data-sha")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    m, _, _ = load_bundle(args.bundle)
    if not args.execute:
        review = m.get("sample_rule_review") or {}
        print(canonical({"status": "preflight_only", "counts": m["counts"], "plan": PLAN,
            "gpu_loaded": False, "generation_calls": 0, "real_tool_calls": 0, "optimizer_steps": 0,
            "human_reviewed": False, "sample_rule_review": review.get("status", "pending"),
            "cuda_runtime_validated": False, "starting_adapter": "verified_sft_v1"}))
        return
    if (not args.ack_synthetic_data or args.reviewed_data_sha != m["preferences_sha256"]
            or args.model_dir is None or args.out is None):
        raise ValueError("explicit_data_review_and_execution_required")
    if args.worker:
        worker(args.bundle, args.model_dir, args.out)
        return
    args.bundle, args.model_dir, args.out = args.bundle.resolve(), args.model_dir.resolve(), args.out.resolve()
    archive = args.out.parent / "rpo-results.zip"
    if args.out.exists() or archive.exists():
        raise ValueError("new_run_required_no_retry")
    args.out.mkdir(parents=True, exist_ok=False)
    once.save_json(args.out / "run.json", {"status": "starting", "real_tool_calls": 0, "optimizer_steps": 0})
    command = [sys.executable, str(Path(__file__).resolve()), "--bundle", str(args.bundle),
        "--model-dir", str(args.model_dir), "--out", str(args.out), "--execute", "--ack-synthetic-data",
        "--reviewed-data-sha", args.reviewed_data_sha, "--worker"]
    try:
        once.supervise(command, PLAN["timeout_seconds"], args.out)
    finally:
        package_results(args.bundle, args.out, archive)
    result = json.loads((args.out / "run.json").read_text(encoding="utf-8"))
    print(canonical({k: result.get(k) for k in ("status", "optimizer_steps", "validation_correct",
        "blind_v1_correct", "blind_candidate_correct", "elapsed_seconds")}))
    print(canonical({"zip": str(archive), "sha256": file_sha(archive),
                     "next": "download archive then return GPU; no automatic deployment"}))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(canonical({"status": "blocked_no_retry", "exception_type": type(exc).__name__}))
        raise SystemExit(2) from None
