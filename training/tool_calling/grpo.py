"""Offline-first Qwen3-8B LoRA GRPO pilot. Default: verification, never training."""

from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import time

from baseline import encode_all, file_sha, model_inventory, run_predictions
from contracts import canonical, sha, tools_schema
from prompt_v3 import SYSTEM_V3, VERSION
from rewards import score


FILES = {
    "grpo.py", "baseline.py", "contracts.py", "prompt_v3.py", "environment.py", "rewards.py",
    "requirements-grpo.txt", "train.prompts.jsonl", "train.oracle.jsonl",
    "validation.prompts.jsonl", "audit.json",
}
VERSIONS = {"torch": "2.9.1", "transformers": "4.57.3", "accelerate": "1.12.0",
            "bitsandbytes": "0.49.1", "trl": "0.26.0", "peft": "0.18.0", "datasets": "4.4.1"}
SEED = 20261003
GROUP = 4
MAX_INPUT = 8192
MAX_OUTPUT = 384
EVAL_SETTINGS = {"enable_thinking": False, "quantization": "nf4-double", "do_sample": False,
                 "seed": SEED, "max_input_tokens": MAX_INPUT, "max_new_tokens": MAX_OUTPUT}


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def load_bundle(directory):
    manifest = json.loads((directory / "grpo-bundle.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != "tool-grpo-bundle-v1" or manifest.get("prompt_version") != VERSION
            or manifest.get("counts") != {"train": 1000, "validation": 200}
            or set(manifest.get("files", {})) != FILES
            or manifest.get("blind_rows") != 0 or manifest.get("validation_labels") != 0):
        raise ValueError("invalid_bundle")
    for name, expected in manifest["files"].items():
        if sha((directory / name).read_bytes()) != expected:
            raise ValueError("bundle_changed")
        if name.endswith(".py") and (Path(__file__).parent / name).read_bytes() != (directory / name).read_bytes():
            raise ValueError("runner_must_match_bundle")
    prompts = {s: read_rows(directory / f"{s}.prompts.jsonl") for s in ("train", "validation")}
    all_ids = set()
    for split, rows in prompts.items():
        for row in rows:
            if (type(row) is not dict or set(row) != {"id", "prompt"} or type(row["id"]) is not str
                    or row["id"] in all_ids or type(row["prompt"]) is not list or len(row["prompt"]) != 2
                    or row["prompt"][0] != {"role": "system", "content": SYSTEM_V3}):
                raise ValueError("prompt_shape_or_overlap")
            message = row["prompt"][1]
            if (type(message) is not dict or set(message) != {"role", "content"}
                    or message["role"] != "user" or type(message["content"]) is not str):
                raise ValueError("invalid_user_message")
            user = json.loads(message["content"])
            if type(user) is not dict or set(user) != {"task", "context", "tools"} or user["tools"] != tools_schema():
                raise ValueError("private_fields_in_prompt")
            all_ids.add(row["id"])
        if len(rows) != manifest["counts"][split]:
            raise ValueError("prompt_coverage")
    oracle = read_rows(directory / "train.oracle.jsonl")
    families = manifest.get("train_families", [])
    if len(families) != 25 or len(set(families)) != 25 or len(oracle) != 1000:
        raise ValueError("train_coverage")
    if Counter(c["family"] for c in oracle) != Counter({f: 40 for f in families}):
        raise ValueError("train_family_coverage")
    for row, case in zip(prompts["train"], oracle):
        expected = canonical({"task": case["instruction"], "context": case["context"], "tools": tools_schema()})
        if (case["split"] != "train" or not case["synthetic"] or case["human_reviewed"]
                or row["id"] != case["id"] or row["prompt"][1]["content"] != expected
                or score(case["reference"], case)["reward"] != 1):
            raise ValueError("train_private_alignment")
    smoke = manifest.get("smoke_ids", [])
    if (len(smoke) != 20 or len(set(smoke)) != 20
            or not set(smoke) <= {r["id"] for r in prompts["validation"]}):
        raise ValueError("invalid_smoke_selection")
    return manifest, prompts, {c["id"]: c for c in oracle}


def select_train(rows, cases, limit):
    if type(limit) is not int or limit not in (100, 200, 1000):
        raise ValueError("train_limit_must_be_100_200_or_1000")
    # Equal representation of all25 templates; deterministic, not the first100 rows.
    selected = []
    for family in sorted({c["family"] for c in cases.values()}):
        pool = [r for r in rows if cases[r["id"]]["family"] == family]
        selected += sorted(pool, key=lambda r: sha(f"{SEED}/{r['id']}"))[:limit // 25]
    if len(selected) != limit:
        raise ValueError("incomplete_train_selection")
    return selected


def training_config(out, steps):
    if type(steps) is not int or not 1 <= steps <= 1000:
        raise ValueError("invalid_step_limit")
    # 1GPU * microbatch1 * accumulation4 / G4 = one prompt group per update.
    # TRL0.26 has no prompt truncation gate: tokenize/check every row ourselves.
    return dict(output_dir=str(out), max_steps=steps, per_device_train_batch_size=1,
                gradient_accumulation_steps=GROUP, steps_per_generation=GROUP, num_generations=GROUP,
                num_iterations=1, max_completion_length=MAX_OUTPUT, learning_rate=5e-6,
                beta=0.02, loss_type="dr_grpo", scale_rewards="group", bf16=True, fp16=False,
                gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
                optim="adamw_torch", use_vllm=False, temperature=0.7, top_p=0.9, top_k=50,
                chat_template_kwargs={"enable_thinking": False}, report_to=[], log_completions=False,
                logging_steps=1, save_strategy="no", eval_strategy="no", push_to_hub=False,
                seed=SEED, data_seed=SEED, mask_truncated_completions=True, remove_unused_columns=False,
                dataloader_num_workers=0, disable_tqdm=True)


def training_records(tokenizer, rows, encoded):
    records = []
    for row, ids in zip(rows, encoded):
        text = tokenizer.apply_chat_template(row["prompt"], tokenize=False, add_generation_prompt=True,
                                             enable_thinking=False)
        if tokenizer(text, add_special_tokens=False)["input_ids"] != ids:
            raise ValueError("training_tokenization_differs_from_baseline")
        # Private labels stay outside the HF dataset and are looked up by opaque ID.
        records.append({"prompt": text, "case_id": row["id"]})
    return records


class RewardMonitor:
    """Same frozen score; also stop a pilot with no usable signal in32 consecutive groups."""

    def __init__(self, cases, prompts):
        self.cases = cases
        self.prompts = prompts
        self.window = deque(maxlen=32)
        self.groups = self.different = self.positive = self.completions = 0
        self.codes = Counter()

    def __call__(self, completions, case_id, prompts, **kwargs):
        if not completions or len(completions) != len(case_id) or len(case_id) != len(prompts) or len(case_id) % GROUP:
            raise ValueError("reward_batch_alignment")
        values = []
        for raw, identity, prompt in zip(completions, case_id, prompts):
            if identity not in self.cases or prompt != self.prompts[identity]:
                raise ValueError("reward_prompt_identity_mismatch")
            result = score(raw, self.cases[identity])
            values.append(result["reward"])
            self.codes[result["code"]] += 1
        for i in range(0, len(values), GROUP):
            if len(set(case_id[i:i + GROUP])) != 1:
                raise ValueError("reward_group_alignment")
            group = values[i:i + GROUP]
            different, positive = len(set(group)) > 1, any(v > 0 for v in group)
            self.window.append((different, positive))
            self.groups += 1
            self.different += int(different)
            self.positive += sum(v > 0 for v in group)
            self.completions += GROUP
        if len(self.window) == 32 and (not any(x[0] for x in self.window) or not any(x[1] for x in self.window)):
            raise ValueError("no_learning_signal_in_32_groups_stop_no_retry")
        return values

    def summary(self):
        return dict(groups=self.groups, groups_with_reward_variance=self.different,
                    positive_completions=self.positive, scored_completions=self.completions,
                    codes=dict(self.codes))


def check_before(directory, manifest_hash, validation):
    report = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    rows = read_rows(directory / "predictions.jsonl")
    if (report.get("status") != "complete" or report.get("phase") != "before"
            or report.get("prompt_version") != VERSION or report.get("bundle_sha256") != manifest_hash
            or report.get("selected") != 200 or report.get("completed") != 200
            or report.get("generation_calls") != 200 or report.get("truncated_outputs") != 0
            or report.get("real_tool_calls") != 0
            or any(report.get(k) != v for k, v in EVAL_SETTINGS.items())
            or report.get("predictions_sha256") != file_sha(directory / "predictions.jsonl")
            or any(set(r) != {"id", "response"} or type(r["response"]) is not str for r in rows)
            or [r["id"] for r in rows] != [r["id"] for r in validation]):
        raise ValueError("complete_same_prompt_baseline_required")
    return report


def offline_environment():
    for key, value in {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
                       "HF_HUB_DISABLE_TELEMETRY": "1", "WANDB_DISABLED": "true", "USE_TF": "0",
                       "TOKENIZERS_PARALLELISM": "false"}.items():
        os.environ[key] = value


def versions():
    found = {name: importlib.metadata.version(name) for name in VERSIONS}
    if any(found[k].split("+")[0] != v for k, v in VERSIONS.items()):
        raise ValueError("pinned_runtime_versions_required")
    return found


def train_adapter(model, tokenizer, records, cases, args, report):
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from trl import GRPOConfig, GRPOTrainer

    model.config.use_cache = False
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True,
                                            gradient_checkpointing_kwargs={"use_reentrant": False})
    model = get_peft_model(model, LoraConfig(r=8, lora_alpha=16, lora_dropout=0.0,
                                            bias="none", task_type="CAUSAL_LM",
                                            target_modules=["q_proj", "v_proj"]))
    prompts = {r["case_id"]: r["prompt"] for r in records}
    meter = RewardMonitor({i: cases[i] for i in prompts}, prompts)

    def task_reward(completions, case_id, prompts, **kwargs):
        return meter(completions, case_id, prompts, **kwargs)

    trainer = GRPOTrainer(model=model, args=GRPOConfig(**training_config(args.out, args.steps)),
                          processing_class=tokenizer, reward_funcs=task_reward,
                          train_dataset=Dataset.from_list(records))
    try:
        trainer.train(resume_from_checkpoint=None)
        trainer.save_model(str(args.out / "adapter"))
        tokenizer.save_pretrained(str(args.out / "adapter"))
        report["adapter_files_sha256"] = {p.name: file_sha(p) for p in (args.out / "adapter").iterdir() if p.is_file()}
    finally:
        report["optimizer_steps"] = trainer.state.global_step
        report["reward_monitor"] = meter.summary()
    report["trainer_metrics"] = trainer.state.log_history


def execute(args, manifest, prompts, cases):
    offline_environment()
    runtime_versions = versions()
    inventory = model_inventory(args.model_dir)
    bundle_hash = file_sha(args.bundle / "grpo-bundle.json")
    before = None
    if args.phase != "before":
        before = check_before(args.baseline_dir, bundle_hash, prompts["validation"])
        if before["model_files_sha256"] != inventory or before["versions"] != runtime_versions:
            raise ValueError("baseline_model_or_runtime_changed")
    if args.phase == "after":
        trained = json.loads((args.adapter.parent / "run.json").read_text(encoding="utf-8"))
        if (trained.get("status") != "complete" or trained.get("phase") != "train"
                or trained.get("bundle_sha256") != bundle_hash or trained.get("model_files_sha256") != inventory
                or trained.get("versions") != runtime_versions
                or trained.get("adapter_files_sha256") != {p.name: file_sha(p) for p in args.adapter.iterdir() if p.is_file()}
                or not (args.adapter / "adapter_model.safetensors").is_file()):
            raise ValueError("complete_matching_adapter_required")
    rows = select_train(prompts["train"], cases, args.train_limit) if args.phase == "train" else prompts["validation"]
    if args.smoke:
        lookup = {r["id"]: r for r in rows}
        rows = [lookup[i] for i in manifest["smoke_ids"]]
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
    if (not torch.cuda.is_available() or torch.cuda.device_count() != 1
            or not torch.cuda.is_bf16_supported() or int(os.environ.get("WORLD_SIZE", "1")) != 1):
        raise ValueError("single_bf16_cuda_gpu_required")
    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir), local_files_only=True, trust_remote_code=False)
    tokenizer.padding_side = "left"
    tokenizer.pad_token = tokenizer.eos_token
    encoded = encode_all(tokenizer, rows, MAX_INPUT)
    records = training_records(tokenizer, rows, encoded) if args.phase == "train" else None
    if before and before["gpu"] != torch.cuda.get_device_name(0):
        raise ValueError("baseline_gpu_changed")
    set_seed(SEED)
    model = AutoModelForCausalLM.from_pretrained(
        str(args.model_dir), local_files_only=True, trust_remote_code=False, use_safetensors=True,
        torch_dtype=torch.bfloat16, device_map={"": 0}, attn_implementation="sdpa",
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                             bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16))
    metadata = {**EVAL_SETTINGS, "phase": args.phase, "prompt_version": VERSION,
                "bundle_sha256": bundle_hash, "model_files_sha256": inventory,
                "versions": runtime_versions, "gpu": torch.cuda.get_device_name(0),
                "selected_ids_sha256": sha(canonical([r["id"] for r in rows]))}
    if args.phase == "train":
        args.out.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        report = {**metadata, "status": "running", "real_tool_calls": 0, "selected": len(rows),
                  "config": training_config(args.out, args.steps), "started_utc": datetime.now(timezone.utc).isoformat()}
        try:
            train_adapter(model, tokenizer, records, cases, args, report)
            report["status"] = "complete"
        except Exception as exc:
            report.update(status="failed_no_retry", exception_type=type(exc).__name__)
            if type(exc) is ValueError and exc.args == ("no_learning_signal_in_32_groups_stop_no_retry",):
                report["stop_reason"] = exc.args[0]
        except KeyboardInterrupt:
            report["status"] = "interrupted_no_retry"
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            report["peak_gpu_allocated_bytes"] = torch.cuda.max_memory_allocated()
            (args.out / "run.json").write_text(canonical(report) + "\n", encoding="utf-8")
        return report
    if args.phase == "after":
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, str(args.adapter), local_files_only=True, is_trainable=False)
        metadata["adapter_files_sha256"] = trained["adapter_files_sha256"]
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

    report = run_predictions(rows, generate, args.out, metadata)
    report["predictions_sha256"] = file_sha(args.out / "predictions.jsonl")
    (args.out / "run.json").write_text(canonical(report) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).parent)
    parser.add_argument("--phase", choices=("before", "train", "after"), default="before")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--ack-synthetic-data", action="store_true", help="Acknowledge template-generated, not human-approved data")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--train-limit", type=int, choices=(100, 200, 1000), default=100)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--smoke", action="store_true", help="Before only:20 rows, not sufficient to unlock training")
    args = parser.parse_args()
    manifest, prompts, cases = load_bundle(args.bundle)
    if (not 1 <= args.steps <= args.train_limit or (args.smoke and args.phase != "before")
            or (args.adapter is not None and args.phase != "after")):
        raise ValueError("invalid_run_plan")
    plan = {"status": "preflight_only", "phase": args.phase, "prompt_version": VERSION,
            "train_available": 1000, "train_selected": args.train_limit, "validation": 200,
            "planned_optimizer_steps": args.steps if args.phase == "train" else 0,
            "planned_completions": args.steps * GROUP if args.phase == "train" else (20 if args.smoke else 200),
            "group_size": GROUP, "gpu_loaded": False, "trained": False, "real_tool_calls": 0,
            "human_reviewed": False, "gpu_memory_and_training_time": "unmeasured"}
    if not args.execute:
        print(canonical(plan))
        return
    if args.model_dir is None or args.out is None or args.out.exists():
        raise ValueError("local_model_and_new_output_required")
    if args.phase != "before" and args.baseline_dir is None:
        raise ValueError("same_prompt_baseline_required")
    if args.phase == "after" and args.adapter is None:
        raise ValueError("local_adapter_required")
    if args.phase == "train" and not args.ack_synthetic_data:
        raise ValueError("acknowledge_synthetic_data_before_training")
    result = execute(args, manifest, prompts, cases)
    print(canonical({k: result[k] for k in ("status", "phase", "elapsed_seconds", "real_tool_calls")}))
    if result["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(canonical({"status": "blocked_no_retry", "exception_type": type(exc).__name__}))
        raise SystemExit(2) from None
