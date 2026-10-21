#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

execute=false
ack_data_sha=''
model_dir='models/Qwen3-8B'
out='runs/guarded-grpo'
while (( $# )); do
  case "$1" in
    --execute) execute=true; shift ;;
    --ack-data-sha|--model-dir|--out)
      if (( $# < 2 )) || [[ "$2" == --* ]]; then
        printf '%s\n' "Missing value for $1" >&2
        exit 2
      fi
      case "$1" in
        --ack-data-sha) ack_data_sha="$2" ;;
        --model-dir) model_dir="$2" ;;
        --out) out="$2" ;;
      esac
      shift 2
      ;;
    *) printf '%s\n' "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

# CPU preflight and caller-supplied review binding precede every install/download.
python3 -B grpo_guarded.py
if [[ "$execute" != true ]]; then
  printf '%s\n' 'Preflight only. Review train-review.txt and obtain direct user confirmation before --execute --ack-data-sha <SHA256>.'
  exit 0
fi
python3 -B - "$ack_data_sha" <<'PY'
from pathlib import Path
import sys
from grpo_guarded import load_bundle
manifest = load_bundle(Path.cwd())[0]
if not sys.argv[1] or sys.argv[1] != manifest['data_sha256']:
    raise SystemExit('Exact caller-supplied --ack-data-sha is required; review must come directly from the user.')
PY
if [[ -e "$out" || -L "$out" || -e "$(dirname -- "$out")/grpo-4090-guarded-results.zip" ]]; then
  printf '%s\n' 'Output already exists. Preserve results and do not retry.' >&2
  exit 2
fi
if ! mkdir -- .guarded-launch.lock; then
  printf '%s\n' 'Persistent launch lock exists. Download existing logs/results; do not retry or remove the lock.' >&2
  exit 2
fi
printf '%s\n' "$ack_data_sha" > .guarded-launch.lock/ack-data-sha.txt
stage='environment_setup'
finish() {
  local code=$?
  printf '%s\n' "Launcher exit=$code stage=$stage. Persistent lock and all results are retained."
  printf '%s\n' 'Download reports/checkpoints and .guarded-launch.lock/setup.log, then manually return the GPU instance to stop billing.'
  if (( code != 0 )); then
    printf '%s\n' 'Stopped without retry. Setup failure does not establish that GPU computation occurred.' >&2
  fi
}
trap finish EXIT

export USE_TF=0 TOKENIZERS_PARALLELISM=false HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_HUB_DISABLE_XET=1 WANDB_DISABLED=true
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
compatible() {
  "$1" -B - <<'PY'
import importlib.metadata
import json
from grpo import versions
with open('guarded-bundle.json', encoding='utf-8') as handle:
    manifest = json.load(handle)
assert versions() == manifest['versions'], 'incompatible pinned runtime'
assert importlib.metadata.version('torchvision') == '0.24.1+cu128', 'incompatible torchvision'
import torchvision
from trl import GRPOTrainer
PY
}
python_runtime=''
for candidate in "${VIRTUAL_ENV:+$VIRTUAL_ENV/bin/python}" .venv/bin/python .venv-guarded/bin/python "$(command -v python3)"; do
  if [[ -n "$candidate" && -x "$candidate" ]] && compatible "$candidate" > /dev/null 2>&1; then
    python_runtime="$candidate"
    break
  fi
done
if [[ -z "$python_runtime" ]]; then
  if [[ -e .venv-guarded || -L .venv-guarded ]]; then
    printf '%s\n' 'Existing .venv-guarded is incompatible; stopped without modifying it.' >&2
    exit 2
  fi
  python3 -m venv .venv-guarded 2>&1 | tee -a .guarded-launch.lock/setup.log
  python_runtime='.venv-guarded/bin/python'
  # Normal dependencies use the HTTPS mirror; exact +cu128 wheels use PyTorch.
  "$python_runtime" -m pip install 'torch==2.9.1+cu128' 'torchvision==0.24.1+cu128' \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --extra-index-url https://download.pytorch.org/whl/cu128 2>&1 | tee -a .guarded-launch.lock/setup.log
  "$python_runtime" -m pip install -r requirements-grpo.txt \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple 2>&1 | tee -a .guarded-launch.lock/setup.log
  compatible "$python_runtime" 2>&1 | tee -a .guarded-launch.lock/setup.log
fi
printf '%s\n' "Using compatible Python: $python_runtime" | tee -a .guarded-launch.lock/setup.log
stage='pinned_model_preparation'
"$python_runtime" -B grpo_guarded.py --prepare-model --ack-data-sha "$ack_data_sha" \
  --model-dir "$model_dir" 2>&1 | tee -a .guarded-launch.lock/setup.log

# The runner starts its three-hour wall-clock budget only now, after setup.
stage='bounded_compute'
export CUDA_VISIBLE_DEVICES=0 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
"$python_runtime" -B grpo_guarded.py --execute --ack-synthetic-data \
  --ack-data-sha "$ack_data_sha" --model-dir "$model_dir" --out "$out" \
  2>&1 | tee -a .guarded-launch.lock/setup.log
stage='finished_download_results'
