"""Explicit cloud-only model download; verify all files against the existing V3 baseline."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from baseline import model_inventory
from contracts import canonical
from sft import load_bundle


def verify_model(directory, expected):
    actual = model_inventory(directory)
    if actual != expected:
        raise ValueError("download_does_not_match_existing_baseline_stop")
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--revision", help="Optional full 40-hex HF commit, never an unpinned tag")
    args = parser.parse_args()
    _, _, _, before = load_bundle(args.bundle)
    expected = before["model_files_sha256"]
    if args.revision is not None and re.fullmatch(r"[0-9a-f]{40}", args.revision) is None:
        raise ValueError("full_model_commit_required")
    if not args.execute:
        print(canonical({"status": "preflight_only", "repo": "Qwen/Qwen3-8B", "files": len(expected),
                         "downloaded": False, "gpu_loaded": False,
                         "next": "explicit --execute resolves a commit, downloads, then checks baseline hashes"}))
        return
    if args.model_dir.exists():
        # Never silently overwrite a partial or different snapshot.
        verify_model(args.model_dir, expected)
        print(canonical({"status": "already_verified", "files": len(expected), "gpu_loaded": False}))
        return

    from huggingface_hub import HfApi, snapshot_download
    info = HfApi().model_info("Qwen/Qwen3-8B", revision=args.revision or "main")
    revision = info.sha
    if re.fullmatch(r"[0-9a-f]{40}", revision or "") is None:
        raise ValueError("unresolved_model_commit")
    if not set(expected) <= {entry.rfilename for entry in info.siblings}:
        raise ValueError("baseline_files_missing_from_snapshot")
    # Resolve once, download precisely that commit. A changing main cannot mix shards.
    snapshot_download(repo_id="Qwen/Qwen3-8B", revision=revision,
                      allow_patterns=sorted(expected), local_dir=str(args.model_dir))
    verify_model(args.model_dir, expected)
    record = args.model_dir.parent / (args.model_dir.name + ".sft-download.json")
    with record.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical({"repo": "Qwen/Qwen3-8B", "revision": revision,
                                "model_files_sha256": expected,
                                "verified_utc": datetime.now(timezone.utc).isoformat()}) + "\n")
    print(canonical({"status": "downloaded_and_verified", "revision": revision,
                     "files": len(expected), "gpu_loaded": False}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(canonical({"status": "blocked_no_training", "exception_type": type(exc).__name__}))
        raise SystemExit(2) from None
