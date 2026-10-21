"""Prepare an independent SFT cloud bundle, or score downloaded results locally."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import zipfile

import dataset
import grpo
import sft
from contracts import canonical, sha
from prompt_v3 import VERSION, upgrade


RUNTIME_FILES = (
    "grpo.py", "baseline.py", "contracts.py", "prompt_v3.py", "environment.py",
    "rewards.py", "requirements-grpo.txt",
)
NEW_FILES = ("sft.py", "fetch_sft_model.py", "CLOUD_SFT.md")
DATA_FILES = (
    "train.sft.jsonl", "validation.prompts.jsonl",
    "before/run.json", "before/predictions.jsonl",
)
FILES = set(RUNTIME_FILES + NEW_FILES + DATA_FILES)


def rows_bytes(rows):
    return ("".join(canonical(row) + "\n" for row in rows)).encode("utf-8")


def export(dataset_dir, grpo_dir, before_dir, destination, archive_path):
    """Read original artifacts; create new outputs only, without loading a model."""
    if destination.exists() or archive_path.exists():
        raise ValueError("new_bundle_directory_and_zip_required")
    data = dataset.load_verified(dataset_dir)
    old_manifest, prompts, cases = grpo.load_bundle(grpo_dir)
    source_hash = sha((dataset_dir / "manifest.json").read_bytes())
    old_hash = sha((grpo_dir / "grpo-bundle.json").read_bytes())
    if (old_manifest["source_dataset_sha256"] != source_hash
            or (grpo_dir / "train.oracle.jsonl").read_bytes()
            != (dataset_dir / "train.oracle.jsonl").read_bytes()):
        raise ValueError("original_dataset_or_training_labels_changed")
    for split in ("train", "validation"):
        old_rows = grpo.read_rows(dataset_dir / f"{split}.prompts.jsonl")
        expected = [{"id": r["id"], "prompt": upgrade(r["prompt"])} for r in old_rows]
        if prompts[split] != expected:
            raise ValueError("sft_must_reuse_exact_v3_prompts")
    before = grpo.check_before(before_dir, old_hash, prompts["validation"])
    selected = grpo.select_train(prompts["train"], cases, 200)
    records = []
    for row in selected:
        case = cases[row["id"]]
        if case["reference"] != canonical(json.loads(case["reference"])):
            raise ValueError("canonical_training_reference_required")
        records.append({"id": row["id"], "family": case["family"],
                        "prompt": row["prompt"], "completion": case["reference"]})
    families = sorted(old_manifest["train_families"])
    if (Counter(r["family"] for r in records) != Counter({f: 8 for f in families})
            or {r["id"] for r in records} & {r["id"] for r in prompts["validation"]}
            or len({r["id"] for r in records}) != 200):
        raise ValueError("balanced_train_selection_required")

    source = Path(__file__).resolve().parent
    entries = {name: (grpo_dir / name).read_bytes() for name in RUNTIME_FILES}
    # Existing runtime copies must not silently drift when packaged for SFT.
    if any(entries[name] != (source / name).read_bytes() for name in RUNTIME_FILES):
        raise ValueError("original_runtime_changed")
    entries.update({name: (source / name).read_bytes() for name in NEW_FILES})
    entries["train.sft.jsonl"] = rows_bytes(records)
    entries["validation.prompts.jsonl"] = (grpo_dir / "validation.prompts.jsonl").read_bytes()
    entries["before/run.json"] = (before_dir / "run.json").read_bytes()
    entries["before/predictions.jsonl"] = (before_dir / "predictions.jsonl").read_bytes()
    if set(entries) != FILES:
        raise ValueError("bundle_whitelist_mismatch")
    # Verify the old score locally. Validation references are never packaged.
    evaluation = dataset.evaluate(dataset_dir, before_dir / "predictions.jsonl", "validation")
    manifest = {
        "schema": "tool-sft-bundle-v1", "prompt_version": VERSION,
        "counts": {"train": 200, "validation": 200}, "train_families": families,
        "baseline_grpo_bundle_sha256": old_hash, "source_dataset_sha256": source_hash,
        "train_ids_sha256": sha(canonical([r["id"] for r in records])),
        "selection": {"seed": grpo.SEED, "families": 25, "per_family": 8,
                      "method": "existing_grpo_select_train_sha_order"},
        "files": {name: sha(value) for name, value in entries.items()},
        "validation_labels": 0, "blind_rows": 0, "human_reviewed": False,
        "execute_default": False, "method": "sft-only",
    }
    entries["sft-bundle.json"] = (canonical(manifest) + "\n").encode("utf-8")
    destination.mkdir(parents=True, exist_ok=False)
    for name, value in entries.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(value)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    return {
        "status": "prepared_not_trained", "bundle": str(destination.resolve()),
        "zip": str(archive_path.resolve()), "zip_sha256": sha(archive_path.read_bytes()),
        "bundle_sha256": sha(entries["sft-bundle.json"]), "train": len(records),
        "validation": len(prompts["validation"]), "families": len(families),
        "per_family": 8, "validation_labels": 0, "blind_rows": 0,
        "human_reviewed": False, "before_successes": sum(r["task_success"] for r in evaluation["results"]),
        "before_completed": before["completed"], "gpu_loaded": False, "generation_calls": 0,
        "optimizer_steps": 0, "real_tool_calls": 0,
    }


def score_results(dataset_dir, bundle, after_dir, train_dir, output):
    """Local-only SFT comparison using the unchanged public dataset evaluator."""
    if output.exists():
        raise ValueError("new_score_output_required")
    manifest = json.loads((bundle / "sft-bundle.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != "tool-sft-bundle-v1"
            or manifest.get("prompt_version") != VERSION or set(manifest.get("files", {})) != FILES
            or manifest.get("counts") != {"train": 200, "validation": 200}
            or manifest.get("source_dataset_sha256") != sha((dataset_dir / "manifest.json").read_bytes())
            or manifest.get("validation_labels") != 0 or manifest.get("blind_rows") != 0):
        raise ValueError("invalid_sft_bundle")
    for name, expected in manifest["files"].items():
        if sha((bundle / name).read_bytes()) != expected:
            raise ValueError("sft_bundle_changed")
    validation = grpo.read_rows(bundle / "validation.prompts.jsonl")
    before_dir = bundle / "before"
    before = grpo.check_before(before_dir, manifest["baseline_grpo_bundle_sha256"], validation)
    after = json.loads((after_dir / "run.json").read_text(encoding="utf-8"))
    trained = sft.check_prior(train_dir, "train", {
        "bundle_sha256": sha((bundle / "sft-bundle.json").read_bytes()),
        "prompt_version": VERSION,
        **{k: before[k] for k in ("model_files_sha256", "versions", "gpu")},
    })
    adapter = train_dir / "adapter"
    adapter_hashes = {p.name: grpo.file_sha(p) for p in adapter.iterdir() if p.is_file()}
    if (trained.get("status") != "complete" or trained.get("phase") != "train"
            or trained.get("method") != "sft-only" or trained.get("optimizer_steps") != 50
            or trained.get("bundle_sha256") != sha((bundle / "sft-bundle.json").read_bytes())
            or not (adapter / "adapter_model.safetensors").is_file()
            or trained.get("adapter_files_sha256") != adapter_hashes
            or after.get("adapter_files_sha256") != adapter_hashes
            or after.get("train_run_sha256") != sha((train_dir / "run.json").read_bytes())
            or any(trained.get(k) != before.get(k) for k in ("model_files_sha256", "versions", "gpu"))):
        raise ValueError("complete_50_step_train_and_matching_adapter_required")
    predictions = grpo.read_rows(after_dir / "predictions.jsonl")
    if (after.get("status") != "complete" or after.get("phase") != "after"
            or after.get("method") != "sft-only" or after.get("optimizer_steps") != 0
            or after.get("prompt_version") != VERSION
            or after.get("bundle_sha256") != sha((bundle / "sft-bundle.json").read_bytes())
            or after.get("selected") != 200 or after.get("completed") != 200
            or after.get("generation_calls") != 200 or after.get("real_tool_calls") != 0
            or after.get("truncated_outputs") != 0
            or after.get("predictions_sha256") != sha((after_dir / "predictions.jsonl").read_bytes())
            or any(after.get(k) != v for k, v in grpo.EVAL_SETTINGS.items())
            or any(after.get(k) != before.get(k) for k in ("model_files_sha256", "versions", "gpu"))
            or not after.get("adapter_files_sha256") or not after.get("train_run_sha256")
            or any(type(r) is not dict or set(r) != {"id", "response"}
                   or type(r["response"]) is not str for r in predictions)
            or [r["id"] for r in predictions] != [r["id"] for r in validation]):
        raise ValueError("complete_comparable_sft_after_required")
    base_scores = dataset.evaluate(dataset_dir, before_dir / "predictions.jsonl", "validation")
    after_scores = dataset.evaluate(dataset_dir, after_dir / "predictions.jsonl", "validation")
    before_by_id = {r["id"]: r for r in base_scores["results"]}
    transitions = Counter()
    for row in after_scores["results"]:
        old = "correct" if before_by_id[row["id"]]["task_success"] else "wrong"
        new = "correct" if row["task_success"] else "wrong"
        transitions[f"{old}_to_{new}"] += 1
    comparison = {
        "method": "sft-only", "coverage": "200/200", "real_tool_calls": 0,
        "baseline_predictions_sha256": before["predictions_sha256"],
        "after_predictions_sha256": after["predictions_sha256"],
        "before": base_scores, "after": after_scores, "paired": dict(transitions),
        "success_rate_change": after_scores["overall"]["success_rate"] - base_scores["overall"]["success_rate"],
        "unsafe_rate_change": after_scores["overall"]["unsafe_rate"] - base_scores["overall"]["unsafe_rate"],
        "grpo_gain": "not_measured_no_grpo_training_in_this_bundle",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical(comparison) + "\n")
    return {"status": "scored", "output": str(output.resolve()), "method": "sft-only",
            "before_successes": sum(r["task_success"] for r in base_scores["results"]),
            "after_successes": sum(r["task_success"] for r in after_scores["results"]),
            "validation": 200, "success_rate_change": comparison["success_rate_change"],
            "unsafe_rate_change": comparison["unsafe_rate_change"], "paired": dict(transitions)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--dataset", required=True, type=Path)
    export_parser.add_argument("--grpo-bundle", required=True, type=Path)
    export_parser.add_argument("--before", required=True, type=Path)
    export_parser.add_argument("--out", required=True, type=Path)
    export_parser.add_argument("--zip", required=True, type=Path)
    score_parser = commands.add_parser("score")
    score_parser.add_argument("--dataset", required=True, type=Path)
    score_parser.add_argument("--bundle", required=True, type=Path)
    score_parser.add_argument("--after", required=True, type=Path)
    score_parser.add_argument("--train-dir", required=True, type=Path)
    score_parser.add_argument("--out", required=True, type=Path)
    argv = sys.argv[1:]
    # Keep the simple requested export CLI usable without a subcommand.
    if argv and argv[0] not in ("export", "score", "-h", "--help"):
        argv = ["export", *argv]
    args = parser.parse_args(argv)
    if args.command == "export":
        result = export(args.dataset, args.grpo_bundle, args.before, args.out, args.zip)
    else:
        result = score_results(args.dataset, args.bundle, args.after, args.train_dir, args.out)
    print(canonical(result))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(canonical({"status": "blocked_no_retry", "exception_type": type(exc).__name__}))
        raise SystemExit(2) from None
