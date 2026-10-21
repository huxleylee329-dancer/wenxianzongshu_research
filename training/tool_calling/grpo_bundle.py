"""Export audited TRAIN labels and public validation prompts; no blind data."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import zipfile

import dataset
from contracts import canonical, sha
from prompt_v3 import upgrade, VERSION
from prepare import mutations
from rewards import score


CODE_FILES = (
    "grpo.py", "baseline.py", "contracts.py", "prompt_v3.py", "environment.py",
    "rewards.py", "requirements-grpo.txt",
)
DATA_FILES = ("train.prompts.jsonl", "train.oracle.jsonl", "validation.prompts.jsonl", "audit.json")


def rows_bytes(rows):
    return ("".join(canonical(row) + "\n" for row in rows)).encode("utf-8")


def review(data):
    """Mechanical review, not human sign-off or semantic ground-truth proof."""
    report = {"human_reviewed": False, "llm_calls": 0, "real_tool_calls": 0,
              "synthetic_templates": True, "splits": {}}
    for split in ("train", "validation"):
        records = data[split]
        negative = 0
        for case in records:
            if score(case["reference"], case)["reward"] != 1:
                raise ValueError("invalid_reference")
            for _, raw in mutations(case):
                if score(raw, case)["reward"] > 0:
                    raise ValueError("reward_mutation_survived")
                negative += 1
        # A universal clarify/finish answer must not dominate the benchmark.
        constants = {}
        for action, reason in (("clarify", "missing_query"), ("clarify", "permission_required"),
                               ("finish", "no_tool_needed"), ("finish", "already_satisfied")):
            constants[reason] = sum(score(canonical({"action": action, "reason": reason}), c)["task_success"]
                                    for c in records)
        report["splits"][split] = {
            "count": len(records), "reference_successes": len(records), "negative_checks": negative,
            "families": dict(Counter(c["family"] for c in records)),
            "categories": dict(Counter(c["category"] for c in records)),
            "constant_policy_successes": constants,
        }
    return report


def export(directory, destination):
    data = dataset.load_verified(directory)
    report = review(data)
    entries = {name: (Path(__file__).parent / name).read_bytes() for name in CODE_FILES}
    for split in ("train", "validation"):
        old = [json.loads(line) for line in (directory / f"{split}.prompts.jsonl").read_text(encoding="utf-8").splitlines()]
        entries[f"{split}.prompts.jsonl"] = rows_bytes(
            {"id": row["id"], "prompt": upgrade(row["prompt"])} for row in old)
    # Preserve oracle bytes. Do not put reference/world/goal into the model messages.
    entries["train.oracle.jsonl"] = (directory / "train.oracle.jsonl").read_bytes()
    entries["audit.json"] = (canonical(report) + "\n").encode("utf-8")
    manifest = {
        "schema": "tool-grpo-bundle-v1", "prompt_version": VERSION,
        "counts": {"train": 1000, "validation": 200}, "human_reviewed": False,
        "source_dataset_sha256": sha((directory / "manifest.json").read_bytes()),
        "original_validation_sha256": sha((directory / "validation.prompts.jsonl").read_bytes()),
        "production_sha256": json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["production_sha256"],
        "smoke_ids": [c["id"] for f in dataset.FAMILIES["validation"]
                      for c in [x for x in data["validation"] if x["family"] == f][:2]],
        "train_families": list(dataset.FAMILIES["train"]),
        "files": {name: sha(value) for name, value in entries.items()},
        "blind_rows": 0, "validation_labels": 0, "execute_default": False,
    }
    entries["grpo-bundle.json"] = (canonical(manifest) + "\n").encode("utf-8")
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    return {"zip": str(destination.resolve()), "sha256": sha(destination.read_bytes()),
            "prompt_version": VERSION, "train": 1000, "validation": 200,
            "blind_rows": 0, "validation_labels": 0, "audit": report,
            "gpu_loaded": False, "trained": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(canonical(export(args.data, args.out)))
