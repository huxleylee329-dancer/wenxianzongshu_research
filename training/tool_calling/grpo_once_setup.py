"""Explicit download/reuse of the exact v1 base snapshot; no GPU allocation."""

import argparse
import json
from pathlib import Path
import re

from baseline import model_inventory
from contracts import canonical
from grpo_once import load_bundle, save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest, _ = load_bundle(root)
    if not args.execute:
        print(canonical({"status": "preflight_only", "downloaded": False, "gpu_loaded": False}))
        return
    expected = manifest["model_files_sha256"]
    candidates = [root / "models/Qwen3-8B"] + [Path("/home/featurize") / name / "models/Qwen3-8B"
                 for name in ("tool-sft-v2", "tool-sft-v1", "tool-baseline", "tool-grpo-v3")]
    for path in candidates:
        if (path / "model.safetensors.index.json").is_file():
            try:
                matches = model_inventory(path) == expected
            except (OSError, ValueError, KeyError):
                matches = False
            if matches:
                save_json(root / "selected-model.json", {"path": str(path.resolve()), "source": "existing_verified"})
                print(canonical({"status": "model_verified", "source": "existing_snapshot", "gpu_loaded": False}))
                return
    destination = candidates[0]
    if destination.exists():
        raise ValueError("incomplete_model_directory_stop_no_overwrite")
    from huggingface_hub import HfApi, snapshot_download
    info = HfApi().model_info("Qwen/Qwen3-8B")
    if re.fullmatch(r"[0-9a-f]{40}", info.sha or "") is None:
        raise ValueError("unresolved_model_commit")
    snapshot_download(repo_id="Qwen/Qwen3-8B", revision=info.sha, allow_patterns=sorted(expected),
                      local_dir=str(destination))
    if model_inventory(destination) != expected:
        raise ValueError("download_does_not_match_v1_stop")
    save_json(root / "selected-model.json", {"path": str(destination.resolve()), "revision": info.sha,
                                             "source": "downloaded_verified"})
    print(canonical({"status": "model_verified", "source": "download", "gpu_loaded": False}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(canonical({"status": "setup_failed_no_training", "exception_type": type(exc).__name__}))
        raise SystemExit(2) from None
