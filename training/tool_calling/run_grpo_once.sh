#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ "${1:-}" != "--execute" || "$#" != 1 ]]; then
  python3 -B grpo_once.py
  printf '%s\n' 'Run explicitly: bash run_grpo_once.sh --execute'
  exit 0
fi

if [[ -e runs/once || -e runs/grpo-once-results.zip ]]; then
  printf '%s\n' 'This experiment already started. Download existing results; do not retry.'
  exit 2
fi

export USE_TF=0 TOKENIZERS_PARALLELISM=false HF_HUB_DISABLE_TELEMETRY=1
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -B -c 'from grpo import versions; versions(); import torchvision; from trl import GRPOTrainer' >/dev/null 2>&1; then
  .venv/bin/python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu128
  .venv/bin/python -m pip install -r requirements-grpo.txt
fi
.venv/bin/python -B grpo_once.py
.venv/bin/python -B grpo_once_setup.py --execute
model_dir="$(.venv/bin/python -c 'import json; print(json.load(open("selected-model.json"))["path"])')"
export CUDA_VISIBLE_DEVICES=0 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
.venv/bin/python -B grpo_once.py --execute --ack-synthetic-data --model-dir "$model_dir" --out runs/once
printf '%s\n' 'Download runs/grpo-once-results.zip, then return the instance. Exiting this script does not return it.'
