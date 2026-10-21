"""Independent, offline-first 200-example SFT pilot. No execution without --execute."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time

from baseline import encode_all, file_sha, model_inventory, run_predictions
from contracts import canonical, parse_response, sha, tools_schema
from grpo import EVAL_SETTINGS, MAX_INPUT, MAX_OUTPUT, SEED, check_before, offline_environment, read_rows, versions
from prompt_v3 import SYSTEM_V3, VERSION


RUNTIME_FILES = {
    "sft.py", "fetch_sft_model.py", "grpo.py", "baseline.py", "contracts.py",
    "prompt_v3.py", "environment.py", "rewards.py", "requirements-grpo.txt", "CLOUD_SFT.md",
}
FILES = RUNTIME_FILES | {
    "train.sft.jsonl", "validation.prompts.jsonl", "before/run.json", "before/predictions.jsonl",
}
SFT_SETTINGS = {
    "method": "sft-only", "lora_rank": 8, "lora_alpha": 16, "lora_dropout": 0.0,
    "target_modules": ["q_proj", "v_proj"], "learning_rate": 1e-4,
    "batch_size": 1, "gradient_accumulation": 4, "epochs": 1,
    "loss": "answer_and_eos_only", "packing": False, "truncation": False,
}


def load_bundle(directory):
    manifest = json.loads((directory / "sft-bundle.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != "tool-sft-bundle-v1" or manifest.get("prompt_version") != VERSION
            or manifest.get("counts") != {"train": 200, "validation": 200}
            or set(manifest.get("files", {})) != FILES
            or manifest.get("validation_labels") != 0 or manifest.get("blind_rows") != 0
            or manifest.get("human_reviewed") is not False):
        raise ValueError("invalid_sft_bundle")
    for name, digest in manifest["files"].items():
        if file_sha(directory / name) != digest:
            raise ValueError("sft_bundle_changed")
        if name.endswith(".py") and (Path(__file__).parent / name).read_bytes() != (directory / name).read_bytes():
            raise ValueError("runner_must_match_bundle")
    train = read_rows(directory / "train.sft.jsonl")
    validation = read_rows(directory / "validation.prompts.jsonl")
    families = manifest.get("train_families", [])
    if len(train) != 200 or len(validation) != 200 or len(families) != 25 or len(set(families)) != 25:
        raise ValueError("sft_coverage")
    ids = set()
    for split, rows in (("train", train), ("validation", validation)):
        for row in rows:
            fields = {"id", "prompt", "family", "completion"} if split == "train" else {"id", "prompt"}
            if (type(row) is not dict or set(row) != fields or type(row["id"]) is not str
                    or row["id"] in ids or type(row["prompt"]) is not list or len(row["prompt"]) != 2
                    or row["prompt"][0] != {"role": "system", "content": SYSTEM_V3}):
                raise ValueError("invalid_prompt_or_split_overlap")
            ids.add(row["id"])
            message = row["prompt"][1]
            if (type(message) is not dict or set(message) != {"role", "content"}
                    or message["role"] != "user" or type(message["content"]) is not str):
                raise ValueError("invalid_user_message")
            user = json.loads(message["content"])
            if type(user) is not dict or set(user) != {"task", "context", "tools"} or user["tools"] != tools_schema():
                raise ValueError("private_fields_in_prompt")
            if split == "train":
                if type(row["family"]) is not str or row["family"] not in families:
                    raise ValueError("invalid_train_family")
                validate_completion(row["completion"])
    if (Counter(r["family"] for r in train) != Counter({f: 8 for f in families})
            or manifest.get("train_ids_sha256") != sha(canonical([r["id"] for r in train]))):
        raise ValueError("train_selection_changed")
    before = check_before(directory / "before", manifest["baseline_grpo_bundle_sha256"], validation)
    return manifest, train, validation, before


def validate_completion(completion):
    parse_response(completion)
    if canonical(json.loads(completion)) != completion:
        raise ValueError("canonical_training_answer_required")


def encode_training(tokenizer, rows):
    """Inference-identical prefixes; only canonical answer and its EOS receive loss."""
    prefixes = encode_all(tokenizer, rows, MAX_INPUT)
    eos = tokenizer.eos_token_id
    if type(eos) is not int:
        raise ValueError("single_eos_required")
    special = set(tokenizer.all_special_ids)
    records = []
    for row, prefix in zip(rows, prefixes):
        answer = row["completion"]
        validate_completion(answer)
        target = tokenizer.encode(answer, add_special_tokens=False)
        if (type(target) is not list or not target or any(type(i) is not int or i in special for i in target)
                or tokenizer.decode(target, skip_special_tokens=False, clean_up_tokenization_spaces=False) != answer):
            raise ValueError("answer_tokenization_changed")
        target = target + [eos]
        if len(target) > MAX_OUTPUT:
            raise ValueError("answer_token_limit_no_truncation")
        records.append({"input_ids": prefix + target, "attention_mask": [1] * (len(prefix) + len(target)),
                        "labels": [-100] * len(prefix) + target})
    return records


def pad_records(records, pad_id):
    """Mask by position, not token value: genuine EOS stays supervised even if PAD==EOS."""
    maximum = max(len(r["input_ids"]) for r in records)
    result = {"input_ids": [], "attention_mask": [], "labels": []}
    for row in records:
        size = len(row["input_ids"])
        if (set(row) != set(result) or len(row["labels"]) != size or len(row["attention_mask"]) != size
                or not any(v != -100 for v in row["labels"])):
            raise ValueError("invalid_training_record")
        for name, padding in (("input_ids", pad_id), ("attention_mask", 0), ("labels", -100)):
            result[name].append(row[name] + [padding] * (maximum - size))
    return result


def training_config(out, phase):
    if phase not in ("smoke", "train"):
        raise ValueError("invalid_training_phase")
    return dict(output_dir=str(out / "trainer"), per_device_train_batch_size=1,
                gradient_accumulation_steps=4, num_train_epochs=1, max_steps=2 if phase == "smoke" else -1,
                learning_rate=1e-4, warmup_steps=0, lr_scheduler_type="constant", weight_decay=0.0,
                optim="adamw_torch", max_grad_norm=1.0, bf16=True, fp16=False,
                gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
                logging_steps=1, logging_nan_inf_filter=False, save_strategy="no", eval_strategy="no",
                report_to=[], push_to_hub=False, remove_unused_columns=False, dataloader_num_workers=0,
                dataloader_drop_last=False, seed=SEED, data_seed=SEED, skip_memory_metrics=True)


def validate_training(report, phase):
    steps, examples = (2, 8) if phase == "smoke" else (50, 200)
    losses, norms = report.get("losses", []), report.get("grad_norms", [])
    if (phase not in ("smoke", "train") or report.get("optimizer_steps") != steps
            or report.get("examples_seen") != examples or len(losses) != examples or len(norms) != steps
            or not all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in losses + norms)
            or not any(v > 0 for v in norms) or report.get("adapter_changed") is not True):
        raise ValueError("incomplete_or_nonfinite_training")


def check_prior(directory, phase, metadata):
    if directory is None:
        raise ValueError("prior_run_required")
    report = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    if (report.get("status") != "complete" or report.get("phase") != phase
            or report.get("selected") != 200 or report.get("real_tool_calls") != 0
            or report.get("settings") != SFT_SETTINGS
            or any(report.get(k) != metadata[k] for k in
                   ("bundle_sha256", "model_files_sha256", "versions", "gpu", "prompt_version"))):
        raise ValueError("complete_matching_prior_required")
    expected_config = training_config(directory, phase)
    actual_config = dict(report.get("config", {}))
    # The archive may move between cloud and local disk; only its output location is incidental.
    expected_config.pop("output_dir")
    actual_config.pop("output_dir", None)
    if actual_config != expected_config:
        raise ValueError("prior_training_config_changed")
    validate_training(report, phase)
    if phase == "train":
        adapter = directory / "adapter"
        hashes = {p.name: file_sha(p) for p in adapter.iterdir() if p.is_file()}
        if (report.get("adapter_files_sha256") != hashes or "adapter_model.safetensors" not in hashes
                or "adapter_config.json" not in hashes):
            raise ValueError("trained_adapter_changed")
    return report


def train_adapter(model, tokenizer, records, args, report):
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import Trainer, TrainerCallback, TrainingArguments

    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True,
                                            gradient_checkpointing_kwargs={"use_reentrant": False})
    model = get_peft_model(model, LoraConfig(r=8, lora_alpha=16, lora_dropout=0.0, bias="none",
                                            task_type="CAUSAL_LM", target_modules=["q_proj", "v_proj"]))
    parameters = [(n, p) for n, p in model.named_parameters() if p.requires_grad]
    if not parameters or any("lora_" not in n for n, _ in parameters):
        raise ValueError("only_lora_parameters_may_train")
    report.update(trainable_parameters=sum(p.numel() for _, p in parameters),
                  examples_seen=0, losses=[], grad_norms=[], optimizer_steps=0, adapter_changed=False)

    def weight_hash():
        digest = hashlib.sha256()
        for name, parameter in parameters:
            value = parameter.detach().float().cpu()
            if not torch.isfinite(value).all().item():
                raise ValueError("nonfinite_adapter_weights")
            digest.update(name.encode("utf-8"))
            digest.update(value.numpy().tobytes())
        return digest.hexdigest()

    initial = weight_hash()

    class ObservedTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            result = super().compute_loss(model, inputs, return_outputs=return_outputs,
                                          num_items_in_batch=num_items_in_batch)
            loss = result[0] if return_outputs else result
            number = float(loss.detach().cpu())
            if not math.isfinite(number) or number < 0:
                raise ValueError("nonfinite_training_loss")
            report["examples_seen"] += int(inputs["input_ids"].shape[0])
            report["losses"].append(number)
            return result

    class Monitor(TrainerCallback):
        def on_log(self, args_, state, control, logs=None, **kwargs):
            if logs and "grad_norm" in logs:
                norm = float(logs["grad_norm"])
                if not math.isfinite(norm):
                    raise ValueError("nonfinite_training_gradient")
                report["grad_norms"].append(norm)
                report["optimizer_steps"] = state.global_step
                (args.out / "run.json").write_text(canonical(report) + "\n", encoding="utf-8")

    def collate(rows):
        return {k: torch.tensor(v, dtype=torch.long) for k, v in pad_records(rows, tokenizer.eos_token_id).items()}

    trainer = ObservedTrainer(model=model, args=TrainingArguments(**training_config(args.out, args.phase)),
                              train_dataset=records, data_collator=collate, processing_class=tokenizer,
                              callbacks=[Monitor()])
    try:
        trainer.train(resume_from_checkpoint=None)
        report.update(optimizer_steps=trainer.state.global_step, adapter_changed=initial != weight_hash())
        validate_training(report, args.phase)
        # A smoke is a disposable run; full training always starts from the base, not this adapter.
        if args.phase == "train":
            model.save_pretrained(str(args.out / "adapter"), safe_serialization=True)
            report["adapter_files_sha256"] = {p.name: file_sha(p) for p in (args.out / "adapter").iterdir() if p.is_file()}
    finally:
        report["optimizer_steps"] = trainer.state.global_step


def execute(args, manifest, train, validation, before):
    offline_environment()
    runtime = versions()
    inventory = model_inventory(args.model_dir)
    if inventory != before["model_files_sha256"] or runtime != before["versions"]:
        raise ValueError("baseline_model_or_runtime_changed")
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed

    if (not torch.cuda.is_available() or torch.cuda.device_count() != 1 or not torch.cuda.is_bf16_supported()
            or int(os.environ.get("WORLD_SIZE", "1")) != 1):
        raise ValueError("single_bf16_cuda_gpu_required")
    gpu = torch.cuda.get_device_name(0)
    if gpu != before["gpu"]:
        raise ValueError("baseline_gpu_changed")
    metadata = {**EVAL_SETTINGS, "phase": args.phase, "method": "sft-only", "settings": SFT_SETTINGS,
                "prompt_version": VERSION, "bundle_sha256": file_sha(args.bundle / "sft-bundle.json"),
                "model_files_sha256": inventory, "versions": runtime, "gpu": gpu,
                "baseline_predictions_sha256": before["predictions_sha256"]}
    if args.phase == "train":
        check_prior(args.smoke_dir, "smoke", metadata)
        metadata["smoke_run_sha256"] = file_sha(args.smoke_dir / "run.json")
    elif args.phase == "after":
        trained = check_prior(args.train_dir, "train", metadata)
        metadata.update(train_run_sha256=file_sha(args.train_dir / "run.json"),
                        adapter_files_sha256=trained["adapter_files_sha256"], optimizer_steps=0)

    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir), local_files_only=True, trust_remote_code=False)
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right" if args.phase != "after" else "left"
    # Check ALL 200 training targets and public validation inputs before loading GPU weights.
    records = encode_training(tokenizer, train)
    encoded = encode_all(tokenizer, validation, MAX_INPUT)
    metadata["token_counts"] = {
        "train_max_total": max(len(r["input_ids"]) for r in records),
        "train_max_answer_including_eos": max(sum(v != -100 for v in r["labels"]) for r in records),
        "validation_max_prompt": max(map(len, encoded)), "truncated": 0,
    }
    set_seed(SEED)
    torch.cuda.reset_peak_memory_stats()
    model = AutoModelForCausalLM.from_pretrained(
        str(args.model_dir), local_files_only=True, trust_remote_code=False, use_safetensors=True,
        torch_dtype=torch.bfloat16, device_map={"": 0}, attn_implementation="sdpa",
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                             bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16))
    if args.phase in ("smoke", "train"):
        args.out.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        report = {**metadata, "status": "running", "selected": 200, "real_tool_calls": 0,
                  "generation_calls": 0, "config": training_config(args.out, args.phase),
                  "started_utc": datetime.now(timezone.utc).isoformat()}
        try:
            train_adapter(model, tokenizer, records, args, report)
            report["status"] = "complete"
        except Exception as exc:
            report.update(status="failed_no_retry", exception_type=type(exc).__name__)
        except KeyboardInterrupt:
            report["status"] = "interrupted_no_retry"
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            report["peak_gpu_allocated_bytes"] = torch.cuda.max_memory_allocated()
            report["peak_gpu_reserved_bytes"] = torch.cuda.max_memory_reserved()
            (args.out / "run.json").write_text(canonical(report) + "\n", encoding="utf-8")
        return report

    from peft import PeftModel
    model = PeftModel.from_pretrained(model, str(args.train_dir / "adapter"), local_files_only=True, is_trainable=False)
    model.eval()

    def generate(index):
        inputs = torch.tensor([encoded[index]], dtype=torch.long, device="cuda:0")
        with torch.inference_mode():
            result = model.generate(input_ids=inputs, attention_mask=torch.ones_like(inputs),
                                    max_new_tokens=MAX_OUTPUT, do_sample=False, num_beams=1,
                                    pad_token_id=tokenizer.eos_token_id, use_cache=True)
        tail = result[0, inputs.shape[1]:].tolist()
        eos = model.generation_config.eos_token_id
        eos = eos if isinstance(eos, list) else [eos]
        return tokenizer.decode(tail, skip_special_tokens=True, clean_up_tokenization_spaces=False), {
            "tokens_in": len(encoded[index]), "tokens_out": len(tail),
            "truncated": len(tail) >= MAX_OUTPUT and tail[-1] not in eos}

    metadata["selected_ids_sha256"] = sha(canonical([r["id"] for r in validation]))
    report = run_predictions(validation, generate, args.out, metadata)
    report["predictions_sha256"] = file_sha(args.out / "predictions.jsonl")
    report["peak_gpu_allocated_bytes"] = torch.cuda.max_memory_allocated()
    (args.out / "run.json").write_text(canonical(report) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--phase", choices=("smoke", "train", "after"), default="smoke")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ack-synthetic-data", action="store_true")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--smoke-dir", type=Path)
    parser.add_argument("--train-dir", type=Path)
    args = parser.parse_args()
    manifest, train, validation, before = load_bundle(args.bundle)
    if not args.execute:
        print(canonical({"status": "preflight_only", "phase": args.phase, "train_selected": 200,
                         "train_families": 25, "per_family": 8, "validation": 200,
                         "planned_optimizer_steps": {"smoke": 2, "train": 50, "after": 0}[args.phase],
                         "planned_completions": 200 if args.phase == "after" else 0,
                         "gpu_loaded": False, "model_downloaded": False, "real_tool_calls": 0,
                         "optimizer_steps": 0, "generation_calls": 0,
                         "human_reviewed": False, "gpu_memory_and_training_time": "unmeasured"}))
        return
    if args.model_dir is None or args.out is None or args.out.exists():
        raise ValueError("local_model_and_new_output_required")
    if args.phase in ("smoke", "train") and not args.ack_synthetic_data:
        raise ValueError("acknowledge_synthetic_data_before_training")
    if (args.phase == "train" and args.smoke_dir is None) or (args.phase == "after" and args.train_dir is None):
        raise ValueError("prior_complete_run_required")
    report = execute(args, manifest, train, validation, before)
    print(canonical({k: report[k] for k in (
        "status", "phase", "elapsed_seconds", "real_tool_calls", "selected", "completed",
        "generation_calls", "truncated_outputs", "optimizer_steps", "examples_seen", "adapter_changed",
        "trainable_parameters", "peak_gpu_allocated_bytes", "peak_gpu_reserved_bytes", "exception_type",
    ) if k in report}))
    if report["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # These codes describe only local preparation/runtime gates, never provider messages.
        reason = exc.args[0] if type(exc) is ValueError and len(exc.args) == 1 and type(exc.args[0]) is str else None
        if reason is not None and not reason.replace("_", "").isalnum():
            reason = None
        print(canonical({"status": "blocked_no_retry", "exception_type": type(exc).__name__, "stop_reason": reason}))
        raise SystemExit(2) from None
