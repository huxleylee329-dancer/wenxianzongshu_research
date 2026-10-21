#!/usr/bin/env bash
# No installation, downloads, retries, automatic resume or parameter search.
set -eu
cd -- "$(dirname -- "$0")"
export USE_TF=0 CUDA_VISIBLE_DEVICES=0 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1 WANDB_MODE=disabled HF_HUB_DISABLE_TELEMETRY=1
export PYTHONUNBUFFERED=1
if [ ! -x .venv/bin/python ]; then
  printf '%s\n' 'Missing isolated .venv; prepare the pinned environment first.'
  exit 2
fi
exec .venv/bin/python rpo_corrected.py "$@"
