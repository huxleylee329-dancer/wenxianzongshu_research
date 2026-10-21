"""Reviewed corrective RPO continuation. Default is CPU preflight, never training."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import signal
import sys
import time
import zipfile

from baseline import encode_all, file_sha, model_inventory
from contracts import canonical
from grpo import MAX_INPUT, SEED, offline_environment, read_rows, versions
import grpo_once as once
from rewards import score
import rpo


SCHEMA = "tool-rpo-corrected-same-gpu-v1"
GPUS = {"NVIDIA GeForce RTX 4090": [8, 9], "NVIDIA GeForce RTX 5090": [12, 0]}
BASELINE_POLICY = "full_validation_on_current_gpu_before_training"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
PLAN = dict(rpo.PLAN)  # Retain the measured trainer, not another hyperparameter search.
COUNTS = {"train_prompts": 1440, "unique_preference_pairs": 1440,
          "preference_exposures": 2400, "replay_prompts": 1120,
          "correction_prompts": 320, "correction_repeats": 4,
          "validation": 200, "correction_probe": 80, "blind_test": 200}
RUNTIME = {"rpo_corrected.py", "rpo.py", "grpo_once.py", "grpo.py", "baseline.py",
           "contracts.py", "environment.py", "rewards.py", "prompt_v3.py",
           "requirements-grpo.txt", "run_rpo_corrected.sh"}
ARTIFACTS = {"train.oracle.jsonl", "preferences.jsonl", "evaluation/validation.jsonl",
             "evaluation/blind_test.jsonl", "evaluation/correction_probe.jsonl",
             "evaluation/ids.json", "v1-validation.predictions.jsonl", "data-audit.json",
             "review.json", "reviewed-source-manifest.json", "CLOUD_RPO_CORRECTED.txt",
             "v1-adapter/adapter_config.json", "v1-adapter/adapter_model.safetensors"}


def validate_training(cases, pairs):
    lookup = {c["id"]: c for c in cases}
    if len(lookup) != 1440 or len(cases) != 1440 or len(pairs) != 2400:
        raise ValueError("corrected_training_coverage")
    correction_ids = {c["id"] for c in cases if c["id"].startswith("correction:")}
    if len(correction_ids) != 320 or len({p["id"] for p in pairs}) != 2400:
        raise ValueError("corrected_identity_coverage")
    expected = Counter({identity: 4 if identity in correction_ids else 1 for identity in lookup})
    if Counter(p["case_id"] for p in pairs) != expected:
        raise ValueError("corrected_exposure_weights")
    unique = {}
    for p in pairs:
        c = lookup[p["case_id"]]
        if c["split"] != "train" or p["chosen"] != c["reference"] or not score(p["chosen"], c)["task_success"]:
            raise ValueError("corrected_positive_invalid")
        # A rejected answer may intentionally violate the parser. Keep its bytes;
        # NEVER make the runtime parser accept it or repair JSON duplicate keys.
        wrong = score(p["rejected"], c)
        if wrong["task_success"] or wrong["code"] != p["rejected_code"]:
            raise ValueError("corrected_negative_invalid")
        value = (p["chosen"], p["rejected"], p["rejected_code"])
        if p["case_id"] in unique and unique[p["case_id"]] != value:
            raise ValueError("correction_repeat_changed")
        unique[p["case_id"]] = value
    return lookup


def load_bundle(directory):
    m = json.loads((directory / "corrected-bundle.json").read_text(encoding="utf-8"))
    if m.get("schema") != SCHEMA or m.get("plan") != PLAN or m.get("counts") != COUNTS:
        raise ValueError("corrected_manifest_invalid")
    if (m.get("gpu") not in GPUS or m.get("gpu_capability") != GPUS[m["gpu"]]
            or m.get("baseline_policy") != BASELINE_POLICY
            or m.get("model_revision") != MODEL_REVISION
            or m.get("historical_gpu") != "NVIDIA GeForce RTX 4090"):
        raise ValueError("explicit_hardware_baseline_required")
    if set(m.get("files", {})) != RUNTIME | ARTIFACTS:
        raise ValueError("corrected_file_boundary")
    for name, digest in m["files"].items():
        if file_sha(directory / name) != digest:
            raise ValueError("corrected_bundle_changed")
        if name in RUNTIME and (directory / name).read_bytes() != (Path(__file__).parent / name).read_bytes():
            raise ValueError("corrected_runtime_changed")
    review = json.loads((directory / "review.json").read_text(encoding="utf-8"))
    if (review != m.get("review") or review.get("status") != "user_confirmed_sample_review_completed"
            or review.get("all_examples_individually_certified") is not False
            or review.get("source_manifest_sha256") != file_sha(directory / "reviewed-source-manifest.json")
            or m.get("preferences_sha256") != file_sha(directory / "preferences.jsonl")):
        raise ValueError("corrected_review_not_bound")
    cases = read_rows(directory / "train.oracle.jsonl")
    pairs = read_rows(directory / "preferences.jsonl")
    lookup = validate_training(cases, pairs)
    ids = json.loads((directory / "evaluation/ids.json").read_text(encoding="utf-8"))
    if set(ids) != {"validation", "blind_test", "correction_probe"}:
        raise ValueError("evaluation_identity_manifest")
    seen = set(lookup)
    for split, count in (("validation", 200), ("blind_test", 200), ("correction_probe", 80)):
        identities = ids[split]
        if len(identities) != count or len(set(identities)) != count or seen & set(identities):
            raise ValueError("corrected_evaluation_overlap")
        seen.update(identities)
    # Blind answers remain unparsed until all development gates have passed.
    for split in ("validation", "correction_probe"):
        if [c["id"] for c in read_rows(directory / f"evaluation/{split}.jsonl")] != ids[split]:
            raise ValueError("evaluation_identity_changed")
    baseline = once.evaluate(read_rows(directory / "evaluation/validation.jsonl"),
                             read_rows(directory / "v1-validation.predictions.jsonl"))
    if {k: baseline[k] for k in m["v1_validation"]} != m["v1_validation"] or baseline["correct"] != 180:
        raise ValueError("verified_v1_required")
    return m, lookup, pairs


def evaluate_probe(cases, predictions):
    if (len(cases) != 80 or len(predictions) != 80 or len({c["id"] for c in cases}) != 80
            or [p["id"] for p in predictions] != [c["id"] for c in cases]
            or any(set(p) != {"id", "response"} or type(p["response"]) is not str for p in predictions)):
        raise ValueError("complete_probe_required")
    rows = [{"id": c["id"], "family": c["family"], **score(p["response"], c)} for c, p in zip(cases, predictions)]
    return {"n": 80, "correct": sum(r["task_success"] for r in rows),
            "unsafe": sum(r["code"] in once.UNSAFE for r in rows),
            "contract_errors": sum(r["reward"] < 0 and r["code"] not in once.UNSAFE for r in rows),
            "families": {f: sum(r["task_success"] for r in rows if r["family"] == f)
                         for f in sorted({r["family"] for r in rows})}, "results": rows}


def probe_passed(before, after):
    return (before["n"] == after["n"] == 80 and after["correct"] >= before["correct"]
            and after["unsafe"] == 0 and after["contract_errors"] <= before["contract_errors"]
            and set(before["families"]) == set(after["families"])
            and all(after["families"][f] >= n for f, n in before["families"].items()))


def pipeline(backend):
    backend.verify_start()
    backend.train()
    if not rpo.dev_passed(*backend.validate()) or not backend.historical_floor_passed():
        return "retain_v1_dev_failed"
    if not probe_passed(*backend.correction_probe()):
        return "retain_v1_correction_probe_failed"
    return "candidate_passed_offline_only" if rpo.blind_passed(*backend.blind()) else "retain_v1_blind_failed"


class GPUBackend(rpo.GPUBackend):
    """Inherit the unchanged, measured 300-step trainer and v1 evaluation machinery."""
    def __init__(self, bundle, model_dir, out, deadline):
        self.bundle, self.out, self.deadline = bundle, out, deadline
        self.manifest, self.cases, self.pairs = load_bundle(bundle)
        self.report = {"status": "running", "stage": "loading", "optimizer_steps": 0, "real_tool_calls": 0,
                       "blind_evaluation_started": False, "plan": PLAN,
                       "bundle_sha256": file_sha(bundle / "corrected-bundle.json"),
                       "review": self.manifest["review"], "counts": COUNTS}
        self.flush()
        offline_environment()
        if versions() != self.manifest["versions"] or model_inventory(model_dir) != self.manifest["model_files_sha256"]:
            raise ValueError("v1_model_or_runtime_changed")
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed
        if (not torch.cuda.is_available() or torch.cuda.device_count() != 1 or not torch.cuda.is_bf16_supported()
                or int(os.environ.get("WORLD_SIZE", "1")) != 1 or torch.cuda.get_device_name(0) != self.manifest["gpu"]
                or list(torch.cuda.get_device_capability(0)) != self.manifest["gpu_capability"]):
            raise ValueError("matching_single_gpu_required")
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True, trust_remote_code=False)
        self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"
        self.records, self.expected = rpo.training_records(self.tokenizer, self.cases, self.pairs)
        # Validate dev/probe prompts before loading GPU weights; blind stays gated.
        public_evaluation = json.loads((bundle / "evaluation/ids.json").read_text(encoding="utf-8"))
        for split in ("validation", "correction_probe"):
            rows = read_rows(bundle / f"evaluation/{split}.jsonl")
            if [c["id"] for c in rows] != public_evaluation[split]:
                raise ValueError("evaluation_identity_changed")
            encode_all(self.tokenizer, once.public_rows(rows), MAX_INPUT)
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
                           versions=self.manifest["versions"], gpu=torch.cuda.get_device_name(0),
                           gpu_capability=list(torch.cuda.get_device_capability(0)),
                           baseline_policy=self.manifest["baseline_policy"], historical_gpu=self.manifest["historical_gpu"])
        self.audit = once.GenerationAudit(self.report)
        self.audit.wrap(self.model)
        audited_generate = self.model.generate
        def evaluation_only(*args, **kwargs):
            if self.audit.stage == "train":
                raise ValueError("rpo_training_must_not_generate")
            return audited_generate(*args, **kwargs)
        self.model.generate = evaluation_only

    def verify_start(self):
        # Cross-GPU exact strings are not a reproduction gate. Verify original
        # tensors above, then measure all 200 dev cases before any optimizer step.
        if self.report["optimizer_steps"] != 0:
            raise ValueError("baseline_must_precede_training")
        cases = read_rows(self.bundle / "evaluation/validation.jsonl")
        predictions = self.predictions(cases, "validation-v1-current-gpu", "policy")
        self.current_baseline = once.evaluate(cases, predictions)
        historical = once.evaluate(cases, read_rows(self.bundle / "v1-validation.predictions.jsonl"))
        once.save_json(self.out / "baseline-scores.json", {
            "current_gpu": self.current_baseline, "historical_4090": historical,
            "policy": BASELINE_POLICY, "optimizer_steps": 0})
        self.report.update(current_gpu_v1_correct=self.current_baseline["correct"],
                           baseline_completed_before_training=True)
        self.flush()

    def validate(self):
        if not self.report.get("baseline_completed_before_training"):
            raise ValueError("current_gpu_baseline_required")
        cases = read_rows(self.bundle / "evaluation/validation.jsonl")
        after = once.evaluate(cases, self.predictions(cases, "validation", "policy"))
        historical = once.evaluate(cases, read_rows(self.bundle / "v1-validation.predictions.jsonl"))
        once.save_json(self.out / "validation-scores.json", {
            "v1": self.current_baseline, "candidate": after, "historical_4090": historical,
            "comparison": "same_gpu_v1_vs_candidate"})
        self.report.update(validation_correct=after["correct"],
                           historical_dev_floor_passed=rpo.dev_passed(historical, after))
        self.flush()
        return self.current_baseline, after

    def historical_floor_passed(self):
        # Preserve the original quality floor as an additional guard, not as
        # evidence that an apparent cross-hardware gain came from training.
        return self.report.get("historical_dev_floor_passed") is True

    def correction_probe(self):
        cases = read_rows(self.bundle / "evaluation/correction_probe.jsonl")
        before = evaluate_probe(cases, self.predictions(cases, "correction-v1", "reference"))
        after = evaluate_probe(cases, self.predictions(cases, "correction-candidate", "policy"))
        once.save_json(self.out / "correction-scores.json", {"v1": before, "candidate": after})
        self.report.update(correction_v1_correct=before["correct"], correction_candidate_correct=after["correct"])
        self.flush()
        return before, after

    def blind(self):
        ids = json.loads((self.bundle / "evaluation/ids.json").read_text(encoding="utf-8"))["blind_test"]
        if [c["id"] for c in read_rows(self.bundle / "evaluation/blind_test.jsonl")] != ids:
            raise ValueError("blind_identity_changed")
        return super().blind()


def worker(bundle, model_dir, out):
    started, backend = time.monotonic(), None
    def terminate(signum, frame):
        raise TimeoutError("compute_deadline")
    signal.signal(signal.SIGTERM, terminate)
    try:
        backend = GPUBackend(bundle, model_dir, out, started + PLAN["timeout_seconds"])
        backend.report["status"] = pipeline(backend)
    except (Exception, KeyboardInterrupt) as exc:
        report = backend.report if backend else json.loads((out / "run.json").read_text(encoding="utf-8"))
        report.update(status="retain_v1_failed_no_retry", exception_type=type(exc).__name__)
        once.save_json(out / "run.json", report)
    finally:
        report = backend.report if backend else json.loads((out / "run.json").read_text(encoding="utf-8"))
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        once.save_json(out / "run.json", report)


def package_results(bundle, out, archive):
    report = json.loads((out / "run.json").read_text(encoding="utf-8"))
    saved = report.get("saved_adapters", {})
    retain = "candidate-adapter" if "candidate-adapter" in saved else "checkpoint-150"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
        for name in ("corrected-bundle.json", "review.json"):
            z.write(bundle / name, name)
        for p in sorted(out.rglob("*")):
            rel = p.relative_to(out)
            if p.is_file() and ((len(rel.parts) == 1 and p.suffix in (".json", ".jsonl"))
                               or (rel.parts[0] == retain and p.suffix in (".json", ".safetensors"))):
                z.write(p, "run/" + rel.as_posix())
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError("archive_crc_failed")


def prepare_model(manifest, model_dir):
    """Explicit download-only operation; never imports torch or starts training."""
    if versions() != manifest["versions"]:
        raise ValueError("v1_runtime_changed")
    expected = manifest["model_files_sha256"]
    missing = []
    for name, digest in expected.items():
        path = model_dir / name
        if not path.exists():
            missing.append(name)
        elif not path.is_file() or file_sha(path) != digest:
            raise ValueError("existing_model_file_changed_no_overwrite")
    if missing:
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id="Qwen/Qwen3-8B", revision=manifest["model_revision"],
                          allow_patterns=missing, local_dir=str(model_dir),
                          max_workers=4, token=False)
    if model_inventory(model_dir) != expected:
        raise ValueError("model_sha_mismatch")
    print(canonical({"status": "model_verified_not_trained", "files": len(expected),
                     "downloaded_files": len(missing), "optimizer_steps": 0, "gpu_loaded": False}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--out", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--execute", action="store_true")
    action.add_argument("--prepare-model", action="store_true", help="Verify pinned runtime; explicitly download only missing model files, never train")
    parser.add_argument("--ack-synthetic-data", action="store_true")
    parser.add_argument("--reviewed-data-sha")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    m, _, _ = load_bundle(args.bundle)
    if args.prepare_model:
        if args.model_dir is None:
            raise ValueError("model_directory_required")
        prepare_model(m, args.model_dir.resolve())
        return
    if not args.execute:
        print(canonical({"status": "preflight_only", "counts": COUNTS, "plan": PLAN,
                         "review_status": m["review"]["status"], "gpu_loaded": False,
                         "generation_calls": 0, "optimizer_steps": 0, "real_tool_calls": 0,
                         "cuda_runtime_validated": False, "starting_adapter": "verified_sft_v1",
                         "required_gpu": m["gpu"], "baseline_policy": m["baseline_policy"]}))
        return
    if (not args.ack_synthetic_data or args.reviewed_data_sha != m["preferences_sha256"]
            or args.model_dir is None or args.out is None):
        raise ValueError("explicit_data_review_and_execution_required")
    if args.worker:
        worker(args.bundle, args.model_dir, args.out)
        return
    args.bundle, args.model_dir, args.out = args.bundle.resolve(), args.model_dir.resolve(), args.out.resolve()
    archive = args.out.parent / "rpo-corrected-results.zip"
    if args.out.exists() or archive.exists():
        raise ValueError("new_run_required_no_retry")
    args.out.mkdir(parents=True, exist_ok=False)
    once.save_json(args.out / "run.json", {"status": "starting", "real_tool_calls": 0, "optimizer_steps": 0})
    command = [sys.executable, str(Path(__file__).resolve()), "--bundle", str(args.bundle),
               "--model-dir", str(args.model_dir), "--out", str(args.out), "--execute",
               "--ack-synthetic-data", "--reviewed-data-sha", args.reviewed_data_sha, "--worker"]
    try:
        once.supervise(command, PLAN["timeout_seconds"], args.out)
    finally:
        package_results(args.bundle, args.out, archive)
    result = json.loads((args.out / "run.json").read_text(encoding="utf-8"))
    print(canonical({k: result.get(k) for k in ("status", "optimizer_steps", "validation_correct",
        "correction_v1_correct", "correction_candidate_correct", "blind_v1_correct",
        "blind_candidate_correct", "elapsed_seconds")}))
    print(canonical({"result_zip": str(archive), "sha256": file_sha(archive),
                     "next": "download results then return GPU; never auto-deploy"}))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(canonical({"status": "blocked_no_retry", "exception_type": type(exc).__name__}))
        raise SystemExit(2) from None
