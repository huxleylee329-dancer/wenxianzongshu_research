"""Explicitly gated, single-GPU offline Qwen3-8B pilot; never rents a GPU."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from prepare import check_split, read_rows, validate_row
from rewards import grounded_reward, score
from runtime import canonical, digest, source_hashes


MAX_PROMPT_TOKENS = 2048
MAX_NEW_TOKENS = 384
MODEL_ID = "Qwen/Qwen3-8B"


def model_fingerprint(directory: Path) -> dict:
    """Bind baseline, training and evaluation to identical local model bytes."""
    weights = sorted(directory.glob("*.safetensors"))
    if not weights:
        raise ValueError("local safetensors model weights missing")
    files = sorted(set(weights + list(directory.glob("*.json"))
                       + list(directory.glob("*.jinja"))))
    hashes = {}
    for path in files:
        sha = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                sha.update(block)
        hashes[path.name] = sha.hexdigest()
    return hashes


def load_data(directory: Path):
    train, evaluate = (read_rows(directory / name) for name in ("train.jsonl", "eval.jsonl"))
    for row in train + evaluate:
        validate_row(row, for_training=True)
    check_split(train, evaluate)
    if len(train) < 16 or len(evaluate) < 4:
        raise ValueError("pilot needs at least 16 train / 4 held-out examples; recommend 60 / 20")
    return train, evaluate


def remaining_seconds(start: str, hourly_price: float, reserve: float,
                      runtime_minutes: int, *, now: datetime | None = None) -> int:
    if (not math.isfinite(hourly_price) or hourly_price <= 0
            or not math.isfinite(reserve) or not 0 <= reserve < 15
            or not 1 <= runtime_minutes <= 360):
        raise ValueError("budget settings invalid")
    rented = datetime.fromisoformat(start)
    if rented.tzinfo is None:
        raise ValueError("rental start must include timezone")
    elapsed = ((now or datetime.now(timezone.utc)) - rented).total_seconds()
    if elapsed < 0:
        raise ValueError("rental start cannot be in the future")
    seconds = min(runtime_minutes * 60, (15 - reserve) / hourly_price * 3600 - elapsed)
    if seconds < 120:
        raise ValueError("insufficient remaining compute budget")
    return int(seconds)


def token_check(tokenizer, rows: list[dict]) -> list[int]:
    lengths = []
    for row in rows:
        tokens = tokenizer.apply_chat_template(row["prompt"], tokenize=True,
                                              add_generation_prompt=True, enable_thinking=False)
        lengths.append(len(tokens))
        if len(tokens) > MAX_PROMPT_TOKENS:
            # Never let TRL left-truncate away the system instruction or evidence.
            raise ValueError("prompt exceeds pilot token budget; select a shorter task")
        answer = canonical({"content": "\n\n".join(
            unit["variants"][0] + "".join("[[cite:" + sid + "]]" for sid in unit["source_ids"])
            for unit in row["rubric"]["units"])})
        if len(tokenizer.encode(answer, add_special_tokens=False)) + 1 > MAX_NEW_TOKENS:
            raise ValueError("rubric answer cannot fit completion token budget")
    return lengths


def summary(results: list[dict]) -> dict:
    return {
        "examples": len(results),
        "parser_pass_rate": sum(r["parser_passed"] for r in results) / len(results),
        "mean_closed_rubric_reward": sum(r["reward"] for r in results) / len(results),
        "mean_rubric_coverage": sum(r["rubric_coverage"] for r in results) / len(results),
        "general_factuality_proven": False,
    }


def check_baseline(directory: Path | None, data: Path) -> dict:
    if directory is None:
        raise ValueError("complete before evaluation before training or after evaluation")
    baseline = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    if (baseline.get("phase") != "before" or baseline.get("model") != MODEL_ID
            or baseline.get("production_sha256") != source_hashes()
            or baseline.get("train_sha256") != digest((data / "train.jsonl").read_bytes())
            or baseline.get("eval_sha256") != digest((data / "eval.jsonl").read_bytes())):
        raise ValueError("baseline data or production version mismatch")
    if baseline.get("mean_closed_rubric_reward", 0) <= 0:
        raise ValueError("rubric too sparse for baseline; calibrate before spending on GRPO")
    return baseline


def worker(args) -> None:
    # Heavy imports happen only behind --execute, data approval and budget gates.
    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
    from trl import GRPOConfig, GRPOTrainer

    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise ValueError("one visible CUDA GPU required")
    if int(os.environ.get("WORLD_SIZE", "1")) != 1:
        raise ValueError("distributed training is outside this pilot")
    train, evaluate = load_data(args.data)
    baseline = check_baseline(args.baseline_dir, args.data) if args.phase != "before" else None
    config = json.loads((args.model_dir / "config.json").read_text(encoding="utf-8"))
    if config.get("model_type") != "qwen3" or config.get("hidden_size") != 4096:
        raise ValueError("expected local Qwen3-8B snapshot")
    if args.phase == "after" and args.adapter is None:
        raise ValueError("after evaluation needs the trained local adapter")
    if args.phase == "after":
        trained = json.loads((args.adapter.parent / "summary.json").read_text(encoding="utf-8"))
        if (trained.get("phase") != "train" or trained.get("eval_sha256") != baseline["eval_sha256"]
                or trained.get("train_sha256") != baseline["train_sha256"]
                or trained.get("production_sha256") != source_hashes()):
            raise ValueError("adapter/data pairing differs")
    fingerprint = model_fingerprint(args.model_dir)
    if baseline is not None and baseline.get("model_files_sha256") != fingerprint:
        raise ValueError("base model/tokenizer bytes changed since baseline")
    if args.phase == "after" and trained.get("model_files_sha256") != fingerprint:
        raise ValueError("adapter was trained against different base bytes")
    set_seed(42)
    tokenizer = AutoTokenizer.from_pretrained(args.model_dir, local_files_only=True,
                                             trust_remote_code=False, padding_side="left")
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    lengths = token_check(tokenizer, train + evaluate)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    model = AutoModelForCausalLM.from_pretrained(
        args.model_dir, local_files_only=True, trust_remote_code=False,
        device_map={"": 0}, torch_dtype=dtype, attn_implementation="sdpa",
        quantization_config=BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=dtype),
    )
    model.config.use_cache = False
    args.out.mkdir(parents=True, exist_ok=False)
    meta = {
        "model": MODEL_ID, "model_config_sha256": digest((args.model_dir / "config.json").read_bytes()),
        "model_files_sha256": fingerprint,
        "production_sha256": source_hashes(), "phase": args.phase,
        "train_sha256": digest((args.data / "train.jsonl").read_bytes()),
        "eval_sha256": digest((args.data / "eval.jsonl").read_bytes()),
        "max_prompt_tokens": max(lengths), "max_new_tokens": MAX_NEW_TOKENS,
        "seed": 42, "thinking": False, "production_retry": False,
        "torch": torch.__version__, "gpu": torch.cuda.get_device_name(0),
    }
    if baseline is not None and baseline.get("model_config_sha256") != meta["model_config_sha256"]:
        raise ValueError("baseline used a different model config")
    if args.phase == "train":
        if args.adapter is not None:
            raise ValueError("no implicit resume/second adapter in this pilot")
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
        model = get_peft_model(model, LoraConfig(
            r=8, lora_alpha=16, lora_dropout=0.0, bias="none", task_type="CAUSAL_LM",
            target_modules=["q_proj", "v_proj"]))
        # Explicit settings for pinned TRL 0.26.0. 1 * 4 accumulation / G=2.
        training_args = GRPOConfig(
            output_dir=str(args.out), max_steps=args.max_steps,
            per_device_train_batch_size=1, gradient_accumulation_steps=4,
            num_generations=2, max_completion_length=MAX_NEW_TOKENS,
            learning_rate=5e-6, beta=0.02, loss_type="grpo",
            bf16=dtype == torch.bfloat16, fp16=dtype == torch.float16,
            gradient_checkpointing=True, optim="adamw_torch",
            use_vllm=False, temperature=0.7, top_p=0.8, top_k=20,
            chat_template_kwargs={"enable_thinking": False},
            report_to=[], log_completions=False, logging_steps=1,
            save_strategy="steps", save_steps=4, save_total_limit=2,
            save_only_model=True, push_to_hub=False, seed=42,
            mask_truncated_completions=True, remove_unused_columns=False,
        )
        batches = {"count": 0, "positive": 0, "different": 0}

        def measured_reward(completions, allowed_source_ids, rubric, **kwargs):
            values = grounded_reward(completions, allowed_source_ids, rubric, **kwargs)
            batches["count"] += 1
            batches["positive"] += sum(value > 0 for value in values)
            batches["different"] += sum(len(set(values[i:i + 2])) > 1
                                        for i in range(0, len(values), 2))
            # An exact-variant rubric may be too sparse. Do not spend the whole
            # allowance optimizing only JSON or repeating zero-advantage groups.
            if batches["count"] >= 8 and (batches["positive"] == 0 or batches["different"] == 0):
                raise ValueError("reward calibration failed; stop without automatic retry")
            return values

        dataset = Dataset.from_list([{key: row[key] for key in
                                      ("prompt", "allowed_source_ids", "rubric")} for row in train])
        trainer = GRPOTrainer(model=model, args=training_args, processing_class=tokenizer,
                              reward_funcs=measured_reward, train_dataset=dataset)
        trainer.train(resume_from_checkpoint=None)
        trainer.save_model(str(args.out / "adapter"))
        tokenizer.save_pretrained(args.out / "adapter")
        meta["optimizer_steps"] = trainer.state.global_step
        meta["reward_groups"] = batches
    else:
        if args.phase == "after":
            model = PeftModel.from_pretrained(model, args.adapter, local_files_only=True)
        model.eval()
        results = []
        # Full generated text goes ONLY into an explicit local human-review file,
        # never stdout/telemetry; not included by the upload bundle builder.
        with (args.out / "human-review.jsonl").open("x", encoding="utf-8") as handle:
            for row in evaluate:
                text = tokenizer.apply_chat_template(row["prompt"], tokenize=False,
                                                     add_generation_prompt=True, enable_thinking=False)
                inputs = tokenizer(text, add_special_tokens=False, return_tensors="pt").to(model.device)
                with torch.inference_mode():
                    output = model.generate(**inputs, do_sample=False,
                                            max_new_tokens=MAX_NEW_TOKENS,
                                            pad_token_id=tokenizer.pad_token_id)
                raw = tokenizer.decode(output[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
                result = score(raw, row["allowed_source_ids"], row["rubric"])
                results.append(result)
                handle.write(canonical({"id": row["id"], "response": raw, **result}) + "\n")
        meta.update(summary(results))
    (args.out / "summary.json").write_text(canonical(meta) + "\n", encoding="utf-8")
    print(canonical({"phase": args.phase, "status": "finished", "summary_path": str(args.out / "summary.json")}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--phase", choices=("before", "train", "after"), default="before")
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--execute", action="store_true", help="explicit permission to use a GPU")
    parser.add_argument("--rental-start", help="actual rental timestamp with timezone, e.g. 2026-10-03T12:00:00+08:00")
    parser.add_argument("--hourly-price", type=float, default=1.87)
    parser.add_argument("--reserve-yuan", type=float, default=3.0)
    parser.add_argument("--max-runtime-minutes", type=int, default=90)
    parser.add_argument("--max-steps", type=int, default=32)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    train, evaluate = load_data(args.data)
    if args.phase != "before":
        check_baseline(args.baseline_dir, args.data)
    if not args.execute:
        print(canonical({"status": "offline_preflight_passed", "train": len(train),
                         "eval": len(evaluate), "model_loaded": False,
                         "gpu_and_token_fit": "not_yet_verified"}))
        return
    if args.model_dir is None or args.out is None or not args.rental_start:
        raise ValueError("execution requires local model, new output path and actual rental start")
    if not args.model_dir.is_dir() or args.out.exists() or not 1 <= args.max_steps <= 64:
        raise ValueError("local model/output/steps invalid")
    seconds = remaining_seconds(args.rental_start, args.hourly_price,
                                args.reserve_yuan, args.max_runtime_minutes)
    if args.worker:
        if os.environ.get("SECTION_WRITER_BUDGET_CHILD") != "1":
            raise ValueError("worker must be launched by budget supervisor")
        worker(args)
        return
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               HF_DATASETS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
               WANDB_DISABLED="true", TOKENIZERS_PARALLELISM="false",
               PYTHONDONTWRITEBYTECODE="1", SECTION_WRITER_BUDGET_CHILD="1")
    started = time.monotonic()
    # This supervisor kills the worker on timeout but DOES NOT return the cloud
    # instance. The user must return it in Featurize; billing can continue.
    command = [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:], "--worker"]
    child = subprocess.Popen(command, env=env)
    try:
        code = child.wait(timeout=seconds)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        child.kill()
        child.wait()
        code = 2
    print(canonical({"worker_exit": code, "elapsed_seconds": round(time.monotonic() - started, 1),
                     "billing_stopped": False, "action": "backup results and RETURN the Featurize instance"}))
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(canonical({"status": "blocked_or_failed", "error_type": type(exc).__name__,
                         "retry": False}))
        raise SystemExit(2) from None
