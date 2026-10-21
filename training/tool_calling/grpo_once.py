"""One bounded GRPO experiment from the verified SFT v1 adapter; default is CPU preflight."""

from __future__ import annotations

import argparse
from collections import Counter, deque
from contextlib import contextmanager
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import zipfile

from baseline import encode_all, file_sha, model_inventory
from contracts import canonical, sha, tools_schema
from grpo import GROUP, MAX_INPUT, MAX_OUTPUT, SEED, offline_environment, read_rows, versions
from prompt_v3 import SYSTEM_V3, VERSION
from rewards import score


SCHEMA = "tool-grpo-once-v1"
PLAN = {"signal_groups": 32, "group_size": 4, "minimum_mixed_groups": 8,
        "optimizer_steps": 50, "timeout_seconds": 3600, "learning_rate": 5e-6,
        "temperature": 0.7, "top_p": 0.9, "top_k": 50, "beta": 0.02,
        "loss_type": "dr_grpo", "seed": SEED, "max_new_tokens": MAX_OUTPUT,
        "max_input_tokens": MAX_INPUT, "num_iterations": 1, "scale_rewards": "group"}
UNSAFE = {"network_not_authorized", "local_not_authorized", "provider_not_authorized",
          "document_not_authorized", "paper_not_authorized"}


def save_json(path, value):
    """Atomic progress file: a killed worker never leaves half-written JSON."""
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(canonical(value) + "\n", encoding="utf-8")
    temp.replace(path)


def public_rows(cases):
    return [{"id": c["id"], "prompt": [{"role": "system", "content": SYSTEM_V3},
            {"role": "user", "content": canonical({"task": c["instruction"],
             "context": c["context"], "tools": tools_schema()})}]} for c in cases]


def load_bundle(directory):
    manifest = json.loads((directory / "once-bundle.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != SCHEMA or manifest.get("plan") != PLAN
            or manifest.get("prompt_version") != VERSION
            or manifest.get("counts") != {"train": 1200, "validation": 200, "blind_test": 200}
            or manifest.get("human_reviewed") is not False):
        raise ValueError("invalid_once_bundle")
    for name, digest in manifest["files"].items():
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
            raise ValueError("invalid_bundle_path")
        if file_sha(directory / name) != digest:
            raise ValueError("bundle_changed")
        if name.endswith(".py") and (directory / name).read_bytes() != (Path(__file__).parent / name).read_bytes():
            raise ValueError("runner_must_match_bundle")
    cases = read_rows(directory / "train.oracle.jsonl")
    lookup = {c["id"]: c for c in cases}
    if len(cases) != len(lookup) or len(cases) != 1200:
        raise ValueError("training_coverage")
    for c in cases:
        if c["split"] != "train" or not score(c["reference"], c)["task_success"]:
            raise ValueError("training_reference_changed")
    for key, size in (("signal_ids", 32), ("train_ids", 50)):
        ids = manifest[key]
        if len(ids) != size or len(set(ids)) != size or not set(ids) <= lookup.keys():
            raise ValueError("fixed_selection_changed")
    if set(manifest["signal_ids"]) & set(manifest["train_ids"]):
        raise ValueError("signal_training_selection_overlap")
    return manifest, lookup


def evaluate(cases, predictions):
    if (len(cases) != 200 or len(predictions) != 200
            or [r["id"] for r in predictions] != [c["id"] for c in cases]
            or len({r["id"] for r in predictions}) != 200
            or any(set(r) != {"id", "response"} or type(r["response"]) is not str for r in predictions)):
        raise ValueError("complete_evaluation_required")
    scored = [{"id": c["id"], "family": c["family"], **score(r["response"], c)}
              for c, r in zip(cases, predictions)]
    return {"n": 200, "correct": sum(r["task_success"] for r in scored),
            "unsafe": sum(r["code"] in UNSAFE for r in scored),
            "contract_errors": sum(r["reward"] < 0 and r["code"] not in UNSAFE for r in scored),
            "families": {f: sum(r["task_success"] for r in scored if r["family"] == f)
                         for f in sorted({r["family"] for r in scored})}, "results": scored}


def validation_passed(before, after):
    return (after["n"] == before["n"] == 200 and after["correct"] > before["correct"]
            and after["unsafe"] <= before["unsafe"] and after["contract_errors"] <= before["contract_errors"]
            and after["families"]["doi_two_choose_one"] >= before["families"]["doi_two_choose_one"]
            and after["families"]["read_homonym_year"] >= before["families"]["read_homonym_year"]
            and after["families"]["clarify_ambiguous"] > before["families"]["clarify_ambiguous"])


def blind_passed(before, after):
    return (before["n"] == after["n"] == 200 and after["correct"] >= before["correct"]
            and after["unsafe"] <= before["unsafe"] and after["contract_errors"] <= before["contract_errors"])


def mixed_group(rewards):
    return len(rewards) == GROUP and 1.0 in rewards and any(v < 1.0 for v in rewards)


def signal_passed(rows):
    if (len(rows) != 32 or len({r["id"] for r in rows}) != 32
            or any(len(r["rewards"]) != 4 or len(r["truncated"]) != 4 for r in rows)):
        raise ValueError("complete_signal_probe_required")
    return not any(any(r["truncated"]) for r in rows) and sum(mixed_group(r["rewards"]) for r in rows) >= 8


def fixed_training_config(out):
    from grpo import training_config
    config = training_config(out, 50)
    config.update(shuffle_dataset=False, warmup_steps=0, lr_scheduler_type="constant",
                  disable_dropout=True, save_strategy="no", max_grad_norm=1.0,
                  generation_kwargs={"do_sample": True, "num_beams": 1},
                  dataloader_pin_memory=False)
    return config


class TrainingMeter:
    def __init__(self, cases, records):
        self.cases = cases
        self.order = [r["case_id"] for r in records]
        self.prompts = {r["case_id"]: r["prompt"] for r in records}
        self.rows = []
        self.window = deque(maxlen=32)

    def __call__(self, completions, case_id, prompts, **kwargs):
        if (len(self.rows) >= len(self.order) or len(completions) != GROUP
                or case_id != [self.order[len(self.rows)]] * GROUP):
            raise ValueError("training_group_order_or_count_changed")
        identity = case_id[0]
        if prompts != [self.prompts[identity]] * GROUP:
            raise ValueError("training_prompt_changed")
        results = [score(raw, self.cases[identity]) for raw in completions]
        rewards = [r["reward"] for r in results]
        self.rows.append({"id": identity, "rewards": rewards, "codes": [r["code"] for r in results]})
        self.window.append(mixed_group(rewards))
        if len(self.window) == 32 and not any(self.window):
            raise ValueError("no_mixed_learning_signal_in_32_groups")
        return rewards


def policy_parameters(model):
    return [(n, p) for n, p in model.named_parameters() if ".policy." in n and "lora_" in n]


def reference_parameters(model):
    return [(n, p) for n, p in model.named_parameters() if ".reference." in n and "lora_" in n]


def select_adapter(model, name, trainable=False):
    model.set_adapter(name)
    for key, parameter in model.named_parameters():
        parameter.requires_grad_(trainable and ".policy." in key and "lora_" in key)


@contextmanager
def reference_scope(model):
    """Shared quantized base, frozen v1 LoRA; restore every gradient flag on exit."""
    active = model.active_adapter
    flags = [(p, p.requires_grad) for _, p in model.named_parameters()]
    try:
        select_adapter(model, "reference", False)
        yield
    finally:
        model.set_adapter(active)
        for parameter, value in flags:
            parameter.requires_grad_(value)


def tensor_fingerprint(parameters):
    import torch
    digest = hashlib.sha256()
    for key, value in sorted(parameters):
        value = value.detach().float().cpu().contiguous()
        if not torch.isfinite(value).all().item():
            raise ValueError("nonfinite_adapter")
        digest.update(key.replace(".policy.", ".").replace(".reference.", ".").encode())
        digest.update(value.numpy().tobytes())
    return digest.hexdigest()


def load_policy(base, directory):
    from peft import PeftModel
    # Preserve the same quantized-base dtypes as the verified v1 inference run.
    # select_adapter freezes every base parameter; TRL/Trainer enable input grads
    # and gradient checkpointing on the existing PEFT model before optimization.
    model = PeftModel.from_pretrained(base, str(directory), adapter_name="policy", is_trainable=True,
                                      local_files_only=True)
    model.load_adapter(str(directory), adapter_name="reference", is_trainable=False, local_files_only=True)
    select_adapter(model, "policy", True)
    if not policy_parameters(model) or not reference_parameters(model):
        raise ValueError("both_v1_adapters_required")
    if tensor_fingerprint(policy_parameters(model)) != tensor_fingerprint(reference_parameters(model)):
        raise ValueError("policy_reference_initial_weights_differ")
    return model


class GenerationAudit:
    def __init__(self, report):
        self.report = report
        self.stage = "canary"

    def wrap(self, model):
        original = model.generate
        def generate(*args, **kwargs):
            cfg = kwargs.get("generation_config", model.generation_config)
            effective = {k: kwargs.get(k, getattr(cfg, k, None))
                         for k in ("do_sample", "temperature", "top_p", "top_k", "max_new_tokens", "num_beams")}
            sampling = self.stage in ("signal", "train")
            if effective["do_sample"] is not sampling or effective["max_new_tokens"] != MAX_OUTPUT or effective["num_beams"] != 1:
                raise ValueError("actual_generation_settings_changed")
            if sampling and any(effective[k] != PLAN[k] for k in ("temperature", "top_p", "top_k")):
                raise ValueError("sampling_settings_not_forwarded")
            counts = self.report.setdefault("generation_calls", {})
            counts[self.stage] = counts.get(self.stage, 0) + 1
            self.report.setdefault("effective_generation_settings", {})[self.stage] = effective
            return original(*args, **kwargs)
        model.generate = generate


def run_pipeline(backend):
    """No loop over parameters or retry: each expensive stage is called at most once."""
    backend.verify_start()
    signals = backend.probe()
    if not signal_passed(signals):
        return "retain_v1_insufficient_signal"
    backend.train()
    baseline, candidate = backend.validate()
    if not validation_passed(baseline, candidate):
        return "retain_v1_validation_failed"
    original_blind, candidate_blind = backend.blind()
    return "candidate_accepted" if blind_passed(original_blind, candidate_blind) else "retain_v1_blind_failed"


class GPUBackend:
    def __init__(self, bundle, model_dir, out, deadline):
        self.bundle, self.out, self.deadline = bundle, out, deadline
        self.manifest, self.cases = load_bundle(bundle)
        self.report = {"status": "running", "stage": "loading", "optimizer_steps": 0,
                       "real_tool_calls": 0, "blind_evaluation_started": False, "plan": PLAN,
                       "bundle_sha256": file_sha(bundle / "once-bundle.json")}
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
        chosen = [self.cases[i] for i in self.manifest["signal_ids"] + self.manifest["train_ids"]]
        self.encoded = dict(zip([c["id"] for c in chosen], encode_all(self.tokenizer, public_rows(chosen), MAX_INPUT)))
        set_seed(SEED)
        torch.cuda.reset_peak_memory_stats()
        base = AutoModelForCausalLM.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False,
            use_safetensors=True, torch_dtype=torch.bfloat16, device_map={"": 0}, attn_implementation="sdpa",
            quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16))
        self.model = load_policy(base, bundle / "v1-adapter")
        self.initial_hash = tensor_fingerprint(policy_parameters(self.model))
        from safetensors.torch import load_file
        source_hash = tensor_fingerprint(load_file(str(bundle / "v1-adapter/adapter_model.safetensors")).items())
        if source_hash != self.initial_hash:
            raise ValueError("loaded_policy_does_not_match_v1_tensors")
        self.report.update(initial_policy_sha256=self.initial_hash, initial_reference_sha256=self.initial_hash,
                           source_tensor_sha256=source_hash,
                           source_adapter_sha256=self.manifest["v1_adapter_sha256"], versions=self.manifest["versions"],
                           model_files_sha256=self.manifest["model_files_sha256"], gpu=self.manifest["gpu"])
        self.audit = GenerationAudit(self.report)
        self.audit.wrap(self.model)

    def check_time(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("compute_deadline")

    def flush(self):
        save_json(self.out / "run.json", self.report)

    def stage(self, name):
        self.check_time()
        self.report["stage"] = name
        self.audit.stage = name
        self.flush()

    def generate(self, ids, count=1, sampling=False):
        self.check_time()
        torch = self.torch
        inputs = torch.tensor([ids], device="cuda:0", dtype=torch.long)
        kwargs = dict(max_new_tokens=MAX_OUTPUT, num_beams=1, do_sample=sampling,
                      num_return_sequences=count, pad_token_id=self.tokenizer.eos_token_id, use_cache=True)
        if sampling:
            kwargs.update(temperature=PLAN["temperature"], top_p=PLAN["top_p"], top_k=PLAN["top_k"])
        with torch.inference_mode():
            result = self.model.generate(input_ids=inputs, attention_mask=torch.ones_like(inputs), **kwargs)
        eos = self.model.generation_config.eos_token_id
        eos = eos if isinstance(eos, list) else [eos]
        answers, truncated = [], []
        for row in result:
            tail = row[len(ids):].tolist()
            # num_return_sequences pads shorter completions; EOS can precede trailing PAD.
            terminated = any(token in eos for token in tail)
            truncated.append(len(tail) >= MAX_OUTPUT and not terminated)
            answers.append(self.tokenizer.decode(tail, skip_special_tokens=True, clean_up_tokenization_spaces=False))
        return answers, truncated

    def verify_start(self):
        self.stage("canary")
        select_adapter(self.model, "policy", False)
        self.model.eval()
        expected = {r["id"]: r["response"] for r in read_rows(self.bundle / "v1-validation.predictions.jsonl")}
        cases = read_rows(self.bundle / "evaluation/validation.jsonl")
        first = {c["family"]: c for c in reversed(cases)}
        sample = [first[f] for f in sorted(first)]
        passed = 0
        for c, ids in zip(sample, encode_all(self.tokenizer, public_rows(sample), MAX_INPUT)):
            answers, truncated = self.generate(ids)
            if truncated[0] or answers[0] != expected[c["id"]]:
                raise ValueError("v1_canary_does_not_reproduce_stop")
            passed += 1
        self.report["v1_canary_exact_matches"] = passed
        self.flush()

    def probe(self):
        self.stage("signal")
        rows = []
        with (self.out / "signal.jsonl").open("x", encoding="utf-8") as handle:
            for identity in self.manifest["signal_ids"]:
                answers, truncated = self.generate(self.encoded[identity], GROUP, True)
                results = [score(a, self.cases[identity]) for a in answers]
                row = {"id": identity, "rewards": [r["reward"] for r in results],
                       "codes": [r["code"] for r in results], "truncated": truncated,
                       "response_sha256": [sha(a) for a in answers]}
                handle.write(canonical(row) + "\n")
                handle.flush()
                rows.append(row)
                self.report["signal_groups_completed"] = len(rows)
                self.report["signal_mixed_groups"] = sum(mixed_group(r["rewards"]) for r in rows)
                self.flush()
        return rows

    def train(self):
        from datasets import Dataset
        from transformers import TrainerCallback
        from trl import GRPOConfig, GRPOTrainer
        from grpo import training_records
        self.stage("train")
        self.model.config.use_cache = False
        self.model.train()
        select_adapter(self.model, "policy", True)
        cases = [self.cases[i] for i in self.manifest["train_ids"]]
        records = training_records(self.tokenizer, public_rows(cases), [self.encoded[c["id"]] for c in cases])
        meter = TrainingMeter(self.cases, records)
        torch, model, backend = self.torch, self.model, self
        # TRL0.26 casts QLoRA trainable parameters to BF16 in __init__. Preserve the
        # actual F32 v1 adapter, instead of silently rounding the policy but not the reference.
        initial_values = {n: p.detach().cpu().clone() for n, p in policy_parameters(model)}

        class ReferenceView(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.shared = model
                self.calls = 0

            def forward(self, **kwargs):
                with torch.no_grad(), reference_scope(self.shared):
                    self.calls += 1
                    return self.shared(**kwargs)

        class Progress(TrainerCallback):
            def on_step_end(self, args, state, control, **kwargs):
                backend.check_time()
                backend.report["optimizer_steps"] = state.global_step
                backend.report["training_groups"] = len(meter.rows)
                if state.global_step % 10 == 0:
                    backend.save_adapter("checkpoint-" + str(state.global_step))
                backend.flush()

            def on_log(self, args, state, control, logs=None, **kwargs):
                for key in ("loss", "grad_norm", "kl"):
                    if logs and key in logs and not math.isfinite(float(logs[key])):
                        raise ValueError("nonfinite_training_metric")

        def reward(completions, case_id, prompts, **kwargs):
            backend.check_time()
            return meter(completions, case_id, prompts, **kwargs)

        trainer = GRPOTrainer(model=model, processing_class=self.tokenizer, reward_funcs=reward,
                              args=GRPOConfig(**fixed_training_config(self.out / "trainer")),
                              train_dataset=Dataset.from_list(records), callbacks=[Progress()])
        if trainer.ref_model is not None or trainer.model is not model:
            raise ValueError("unexpected_reference_model")
        for name, parameter in policy_parameters(model):
            parameter.data = initial_values[name].to(device=parameter.device)
        del initial_values
        select_adapter(model, "policy", True)
        if (tensor_fingerprint(policy_parameters(model)) != self.initial_hash
                or tensor_fingerprint(reference_parameters(model)) != self.initial_hash):
            raise ValueError("trainer_changed_v1_initial_weights")
        # Use frozen v1 adapter over the shared base. Never use TRL's disable_adapter
        # fallback, which would otherwise compute KL against the untrained base model.
        trainer.ref_model = ReferenceView()
        try:
            trainer.train(resume_from_checkpoint=None)
            changed = tensor_fingerprint(policy_parameters(model)) != self.initial_hash
            if (trainer.state.global_step != 50 or len(meter.rows) != 50
                    or [r["id"] for r in meter.rows] != self.manifest["train_ids"]
                    or trainer.ref_model.calls < 50 or not changed
                    or tensor_fingerprint(reference_parameters(model)) != self.initial_hash):
                raise ValueError("complete_50_step_v1_training_required")
            self.report.update(adapter_changed=changed, reference_forward_calls=trainer.ref_model.calls,
                               final_reference_sha256=tensor_fingerprint(reference_parameters(model)))
            self.save_adapter("candidate-adapter")
        finally:
            self.report["optimizer_steps"] = trainer.state.global_step
            save_json(self.out / "training-rewards.json", meter.rows)
            self.report["peak_gpu_allocated_bytes"] = torch.cuda.max_memory_allocated()
            self.report["peak_gpu_reserved_bytes"] = torch.cuda.max_memory_reserved()
            self.flush()
            trainer.ref_model = None
            del trainer
            gc.collect()
            torch.cuda.empty_cache()

    def save_adapter(self, name):
        target = self.out / name
        if target.exists():
            raise ValueError("adapter_output_already_exists")
        self.model.save_pretrained(str(target), selected_adapters=["policy"], safe_serialization=True)
        # PEFT stores a non-default adapter inside its name subdirectory.
        actual = target / "policy"
        if not (actual / "adapter_model.safetensors").is_file():
            raise ValueError("candidate_adapter_missing")
        self.report.setdefault("saved_adapters", {})[name] = {
            p.relative_to(target).as_posix(): file_sha(p) for p in target.rglob("*") if p.is_file()}

    def predictions(self, cases, stage, adapter):
        self.stage(stage)
        select_adapter(self.model, adapter, False)
        self.model.eval()
        rows = []
        with (self.out / (stage + ".predictions.jsonl")).open("x", encoding="utf-8") as handle:
            for c, ids in zip(cases, encode_all(self.tokenizer, public_rows(cases), MAX_INPUT)):
                answers, truncated = self.generate(ids)
                if truncated[0]:
                    raise ValueError("evaluation_truncated_no_retry")
                row = {"id": c["id"], "response": answers[0]}
                rows.append(row)
                handle.write(canonical(row) + "\n")
                handle.flush()
                self.report.setdefault("evaluation_completed", {})[stage] = len(rows)
                self.flush()
        return rows

    def validate(self):
        cases = read_rows(self.bundle / "evaluation/validation.jsonl")
        baseline = evaluate(cases, read_rows(self.bundle / "v1-validation.predictions.jsonl"))
        after = evaluate(cases, self.predictions(cases, "validation", "policy"))
        save_json(self.out / "validation-scores.json", {"v1": baseline, "candidate": after})
        self.report["validation_correct"] = after["correct"]
        self.flush()
        return baseline, after

    def blind(self):
        self.report["blind_evaluation_started"] = True
        self.flush()
        cases = read_rows(self.bundle / "evaluation/blind_test.jsonl")
        before = evaluate(cases, self.predictions(cases, "blind-v1", "reference"))
        after = evaluate(cases, self.predictions(cases, "blind-candidate", "policy"))
        save_json(self.out / "blind-scores.json", {"v1": before, "candidate": after})
        self.report.update(blind_v1_correct=before["correct"], blind_candidate_correct=after["correct"])
        return before, after


def worker(bundle, model_dir, out):
    started = time.monotonic()
    backend = None
    def cancelled(signum, frame):
        raise TimeoutError("compute_deadline_or_termination")
    signal.signal(signal.SIGTERM, cancelled)
    try:
        backend = GPUBackend(bundle, model_dir, out, started + PLAN["timeout_seconds"])
        backend.report["status"] = run_pipeline(backend)
    except (Exception, KeyboardInterrupt) as exc:
        current = backend.report if backend else json.loads((out / "run.json").read_text(encoding="utf-8"))
        reason = exc.args[0] if type(exc) is ValueError and len(exc.args) == 1 and type(exc.args[0]) is str else None
        if reason is not None and not reason.replace("_", "").isalnum():
            reason = None
        current.update(status="retain_v1_failed_no_retry", exception_type=type(exc).__name__, stop_reason=reason)
        save_json(out / "run.json", current)
    finally:
        current = backend.report if backend else json.loads((out / "run.json").read_text(encoding="utf-8"))
        current["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save_json(out / "run.json", current)


def supervise(command, timeout, out):
    """Parent watchdog also handles GPU/C-extension hangs; no retry or automatic resume."""
    try:
        child = subprocess.Popen(command, start_new_session=os.name != "nt")
    except OSError as exc:
        save_json(out / "run.json", {"status": "retain_v1_process_start_failed", "exception_type": type(exc).__name__,
                                      "real_tool_calls": 0, "optimizer_steps": 0})
        return
    timed_out = interrupted = False
    try:
        child.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        timed_out = isinstance(exc, subprocess.TimeoutExpired)
        interrupted = not timed_out
        child.terminate()
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    path = out / "run.json"
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if (timed_out or interrupted or child.returncode != 0
            or report.get("status") in (None, "starting", "running")):
        report.update(status="retain_v1_timeout" if timed_out else "retain_v1_process_stopped",
                      child_returncode=child.returncode, real_tool_calls=0)
        save_json(path, report)


def package_results(bundle, out, archive):
    report = json.loads((out / "run.json").read_text(encoding="utf-8"))
    saved = report.get("saved_adapters", {})
    checkpoints = [name for name in saved if name.startswith("checkpoint-")]
    retained = "candidate-adapter" if "candidate-adapter" in saved else (
        max(checkpoints, key=lambda name: int(name.split("-")[1])) if checkpoints else None)
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
        z.write(bundle / "once-bundle.json", "once-bundle.json")
        for path in sorted(out.rglob("*")):
            first = path.relative_to(out).parts[0]
            if (first.startswith("checkpoint-") or first == "candidate-adapter") and first != retained:
                continue
            if path.is_file() and path.suffix != ".tmp":
                z.write(path, "run/" + path.relative_to(out).as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError("result_zip_crc_failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ack-synthetic-data", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    manifest, _ = load_bundle(args.bundle)
    if not args.execute:
        print(canonical({"status": "preflight_only", "gpu_loaded": False, "generation_calls": 0,
                         "optimizer_steps": 0, "real_tool_calls": 0, "plan": PLAN,
                         "counts": manifest["counts"], "starting_adapter": "verified_sft_v1_180_of_200"}))
        return
    if not args.ack_synthetic_data or args.model_dir is None or args.out is None:
        raise ValueError("explicit_execution_inputs_required")
    if args.worker:
        worker(args.bundle, args.model_dir, args.out)
        return
    args.bundle, args.model_dir, args.out = args.bundle.resolve(), args.model_dir.resolve(), args.out.resolve()
    archive = args.out.parent / "grpo-once-results.zip"
    if args.out.exists() or archive.exists():
        raise ValueError("new_run_and_archive_required_no_retry")
    args.out.mkdir(parents=True, exist_ok=False)
    save_json(args.out / "run.json", {"status": "starting", "real_tool_calls": 0, "optimizer_steps": 0})
    command = [sys.executable, str(Path(__file__).resolve()), "--bundle", str(args.bundle), "--model-dir", str(args.model_dir),
               "--out", str(args.out), "--execute", "--ack-synthetic-data", "--worker"]
    try:
        supervise(command, PLAN["timeout_seconds"], args.out)
    finally:
        package_results(args.bundle, args.out, archive)
    result = json.loads((args.out / "run.json").read_text(encoding="utf-8"))
    print(canonical({k: result.get(k) for k in ("status", "optimizer_steps", "signal_mixed_groups", "validation_correct",
                                               "blind_v1_correct", "blind_candidate_correct", "elapsed_seconds")}))
    print(canonical({"results_zip": str(archive), "sha256": file_sha(archive),
                     "next": "download results, then return the GPU instance"}))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(canonical({"status": "blocked_no_retry", "exception_type": type(exc).__name__}))
        raise SystemExit(2) from None
