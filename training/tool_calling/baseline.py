"""Opt-in, local-model-only Qwen3 baseline. No real tool dispatch or training."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import time

from contracts import canonical, sha


PUBLIC_FILES = {"baseline.py", "contracts.py", "requirements-baseline.txt", "CLOUD_BASELINE.md", "validation.prompts.jsonl"}


def load_public(directory, full=False):
    manifest = json.loads((directory / "bundle.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != "tool-baseline-public-v1" or manifest.get("split") != "validation"
            or manifest.get("count") != 200 or set(manifest.get("files", {})) != PUBLIC_FILES):
        raise ValueError("invalid_public_manifest")
    for name, digest in manifest["files"].items():
        if sha((directory / name).read_bytes()) != digest:
            raise ValueError("public_bundle_changed")
    rows = [json.loads(line) for line in (directory / "validation.prompts.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if (type(row) is not dict or set(row) != {"id", "prompt"} or type(row["id"]) is not str
                or type(row["prompt"]) is not list or len(row["prompt"]) != 2):
            raise ValueError("invalid_prompt")
        for message, role in zip(row["prompt"], ("system", "user")):
            if (type(message) is not dict or set(message) != {"role", "content"}
                    or message["role"] != role or type(message["content"]) is not str):
                raise ValueError("invalid_message")
        user = json.loads(row["prompt"][1]["content"])
        if type(user) is not dict or set(user) != {"task", "context", "tools"}:
            raise ValueError("private_fields_in_prompt")
    known = {r["id"] for r in rows}
    smoke = manifest.get("smoke_ids")
    if (len(rows) != 200 or len(known) != 200 or type(smoke) is not list or len(smoke) != 20
            or any(type(i) is not str for i in smoke) or len(set(smoke)) != 20 or not set(smoke) <= known):
        raise ValueError("public_coverage")
    selected = rows if full else [next(r for r in rows if r["id"] == i) for i in smoke]
    return manifest, selected


def encode_all(tokenizer, rows, max_input_tokens):
    """Check ALL selected prompts before allocating the model; no truncation."""
    encoded = []
    for row in rows:
        ids = tokenizer.apply_chat_template(row["prompt"], tokenize=True, add_generation_prompt=True,
                                            enable_thinking=False)
        if type(ids) is not list or not ids or any(type(i) is not int for i in ids):
            raise ValueError("invalid_tokenization")
        if len(ids) > max_input_tokens:
            raise ValueError("input_token_limit_no_truncation")
        encoded.append(ids)
    return encoded


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def model_inventory(directory):
    """Pin the actual local snapshot; do not download or execute custom model code."""
    if not directory.is_dir():
        raise ValueError("local_model_directory_required")
    files = sorted(p for p in directory.rglob("*") if p.is_file())
    relevant = [p for p in files if p.suffix in (".json", ".safetensors", ".model", ".txt", ".jinja")]
    if not any(p.suffix == ".safetensors" for p in relevant):
        raise ValueError("local_safetensors_required")
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    if config.get("model_type") != "qwen3" or config.get("hidden_size") != 4096 or config.get("num_hidden_layers") != 36:
        raise ValueError("expected_qwen3_8b_config")
    return {str(p.relative_to(directory)): file_sha(p) for p in relevant}


def run_predictions(rows, generate, out, metadata):
    """Exactly one generation per row. Save partial progress, never retry/resume."""
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {**metadata, "status": "running", "selected": len(rows), "completed": 0,
              "generation_calls": 0, "real_tool_calls": 0, "truncated_outputs": 0,
              "started_utc": datetime.now(timezone.utc).isoformat(), "tokens_in": 0, "tokens_out": 0}
    try:
        with (out / "predictions.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
            for i, row in enumerate(rows):
                report["generation_calls"] += 1
                response, info = generate(i)
                if type(response) is not str:
                    raise ValueError("non_string_generation")
                handle.write(canonical({"id": row["id"], "response": response}) + "\n")
                handle.flush()
                report["completed"] += 1
                report["tokens_in"] += info["tokens_in"]
                report["tokens_out"] += info["tokens_out"]
                report["truncated_outputs"] += int(info["truncated"])
        report["status"] = "complete"
    except Exception as exc:
        report.update(status="failed_no_retry", exception_type=type(exc).__name__)
    except KeyboardInterrupt:
        report.update(status="interrupted_no_retry")
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        with (out / "run.json").open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical(report) + "\n")
    return report


def execute(directory, rows, model_dir, out, max_input_tokens, max_new_tokens):
    # Offline flags before importing the optional GPU stack. No hidden HF fetch.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    if out.exists():
        raise ValueError("new_output_directory_required")
    inventory = model_inventory(model_dir)
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise ValueError("expose_exactly_one_cuda_gpu")
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
    encoded = encode_all(tokenizer, rows, max_input_tokens)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    set_seed(20261003)
    model = AutoModelForCausalLM.from_pretrained(
        str(model_dir), local_files_only=True, trust_remote_code=False, use_safetensors=True,
        torch_dtype=dtype, device_map={"": 0},
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                             bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype))
    model.eval()
    def generate(index):
        inputs = torch.tensor([encoded[index]], dtype=torch.long, device="cuda:0")
        with torch.inference_mode():
            result = model.generate(input_ids=inputs, attention_mask=torch.ones_like(inputs),
                                    max_new_tokens=max_new_tokens, do_sample=False, num_beams=1,
                                    pad_token_id=tokenizer.eos_token_id, use_cache=True)
        tail = result[0, inputs.shape[1]:].tolist()
        eos = model.generation_config.eos_token_id
        eos_ids = eos if isinstance(eos, list) else [eos]
        # No stripping, JSON repair, fallback, or second generation.
        response = tokenizer.decode(tail, skip_special_tokens=True, clean_up_tokenization_spaces=False)
        return response, {"tokens_in": len(encoded[index]), "tokens_out": len(tail),
                          "truncated": len(tail) >= max_new_tokens and tail[-1] not in eos_ids}
    metadata = {"dataset_bundle_sha256": file_sha(directory / "bundle.json"),
                "selected_ids_sha256": sha(canonical([r["id"] for r in rows])),
                "model_label": "Qwen/Qwen3-8B (local config checked; hashes recorded)",
                "model_files_sha256": inventory, "enable_thinking": False, "quantization": "nf4-double",
                "compute_dtype": str(dtype), "do_sample": False, "seed": 20261003,
                "max_input_tokens": max_input_tokens, "max_new_tokens": max_new_tokens,
                "gpu": torch.cuda.get_device_name(0),
                "versions": {name: importlib.metadata.version(name) for name in
                             ("torch", "transformers", "accelerate", "bitsandbytes")}}
    return run_predictions(rows, generate, out, metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--execute", action="store_true", help="Explicit opt-in to local GPU inference (not training)")
    parser.add_argument("--full", action="store_true", help="All200 validation tasks; default is stratified20")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--max-input-tokens", type=int, default=8192)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    args = parser.parse_args()
    manifest, rows = load_public(args.bundle, args.full)
    if not (1 <= args.max_input_tokens <= 30000 and 1 <= args.max_new_tokens <= 2048
            and args.max_input_tokens + args.max_new_tokens <= 32768):
        raise ValueError("invalid_token_limits")
    if not args.execute:
        print(canonical({"status": "preflight_only", "selected": len(rows), "available": manifest["count"],
                         "gpu_loaded": False, "model_downloaded": False, "generation_calls": 0,
                         "next": "manual approval, local model snapshot, then explicit --execute"}))
        return
    if args.model_dir is None or args.out is None:
        raise ValueError("model_dir_and_new_output_required")
    report = execute(args.bundle, rows, args.model_dir, args.out, args.max_input_tokens, args.max_new_tokens)
    print(canonical({k: report[k] for k in ("status", "selected", "completed", "generation_calls",
                                          "real_tool_calls", "elapsed_seconds", "truncated_outputs")}))
    if report["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(canonical({"status": "blocked_no_retry", "exception_type": type(exc).__name__}))
        raise SystemExit(2) from None
