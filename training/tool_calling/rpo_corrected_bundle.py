"""Bind the user's sample-review confirmation to a new, non-overwriting RPO bundle."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import zipfile

from contracts import canonical, sha
from grpo import read_rows
import rpo_corrected as runtime


def select_replay(cases, pairs):
    """1,120 old prompts, one existing preference each; keep contrast pairs together."""
    families = sorted({c["family"] for c in cases})
    if len(cases) != 1200 or len(families) != 30 or any(sum(c["family"] == f for c in cases) != 40 for f in families):
        raise ValueError("expected_original_thirty_families")
    full = set(sorted(families, key=lambda f: sha("replay-full/" + f))[:10])
    selected = []
    for family in families:
        blocks = defaultdict(list)
        for c in cases:
            if c["family"] == family:
                blocks[c.get("pair_id", c["id"])].append(c)
        sizes = {len(v) for v in blocks.values()}
        if len(sizes) != 1 or next(iter(sizes)) not in (1, 2):
            raise ValueError("replay_contrast_block_changed")
        count = 40 if family in full else 36
        keys = sorted(blocks, key=lambda k: sha("replay-select/" + k))[:count // next(iter(sizes))]
        selected.extend(c for key in keys for c in sorted(blocks[key], key=lambda c: c["id"]))
    by_case = defaultdict(list)
    for pair in pairs:
        by_case[pair["case_id"]].append(pair)
    chosen_pairs = []
    for c in selected:
        candidates = by_case[c["id"]]
        if len(candidates) != 2:
            raise ValueError("original_pair_coverage")
        chosen_pairs.append(min(candidates, key=lambda p: sha("replay-negative/" + p["id"])))
    return selected, chosen_pairs


def mixed_training(old_cases, old_pairs, corrections):
    replay, pairs = select_replay(old_cases, old_pairs)
    if len(corrections) != 320 or len({c["id"] for c in corrections}) != 320:
        raise ValueError("reviewed_correction_coverage")
    pairs = [dict(p) for p in pairs]
    for c in corrections:
        result = runtime.score(c["rejected"], c)
        for repeat in range(4):
            pairs.append({"id": "correction-preference:" + sha(c["id"])[:24] + f":{repeat}",
                          "case_id": c["id"], "family": c["family"], "chosen": c["reference"],
                          "rejected": c["rejected"], "rejected_code": result["code"],
                          "negative_kind": c["scenario"], "repeat_index": repeat,
                          "origin": "reviewed_correction_rule_not_new_independent_example"})
    cases = sorted([*replay, *corrections], key=lambda c: c["id"])
    pairs.sort(key=lambda p: sha("mixed-pair-order/" + p["id"]))
    runtime.validate_training(cases, pairs)
    return cases, pairs


def rows_bytes(rows):
    return ("\n".join(canonical(row) for row in rows) + "\n").encode("utf-8")


def instructions(preferences_sha, gpu):
    return f"""Reviewed corrective RPO — local preparation only

审核确认：用户于 2026-10-07 表示“审核完成”。记录的是纠错样例/规则审核完成，
不是声称每条合成数据由独立专家逐条认证；不是授权自动租卡或训练。

唯一训练设置仍为原 RPO：Qwen3-8B + verified SFT v1 LoRA，单 {gpu}，
sigmoid DPO + chosen NLL，beta=0.1，rpo_alpha=1，lr=5e-6，batch=1，累积8，300步。
不用上次未达标的185/200候选作为起点，不搜索参数，不自动重试/恢复。

数据：1120个原训练题（30类全部覆盖，保留成对对照），每题一个旧偏好；
320个新纠错题，每题同一偏好重复4次。共1440个独立题目/偏好，2400次曝光。
重复曝光不是新增独立数据。新负例保留原始错误JSON，不修复、不放宽评分器。

顺序：当前同卡v1完整开发集200题基线 → 300步 → 候选开发集200题 → 纠错检查v1/候选各80题
→ 两道门禁通过后盲测v1/候选各200题 → 自动打包。失败即停止并保留v1。
训练前验证原v1权重SHA和模型/运行时版本，不要求跨4090/5090的生成文本逐字相同。
开发门禁：候选总分高于同卡新基线、unsafe=0、格式错误不增加、各类不回退。
同时保留原4090质量下限：总分高于180、格式错误不高于4、各类不低于原v1。
原4090记录只作为历史质量下限，不以跨GPU差异宣称训练增益。
纠错门禁：总分和各类不回退、unsafe=0、格式错误不增加。
盲测沿用旧标准；probe是定向检查，不是新无偏盲测。没有自动部署。
最多960次离线模型生成；训练本身不生成。不调用真实论文工具、网络或API。

本地检查通过不等于CUDA已验证，更不等于效果有保证。最长计算4小时后停止；
此前4090同配置300步加开发集约118分钟；5090耗时尚未实测，不保证按算力比例加速。
新增检查/盲测会增加时间，模型准备另计。不要改batch、步数或学习率。
脚本不会归还实例；下载结果后须在平台手动归还，停止计费。

只读预检（不会下载或加载GPU）：
  python rpo_corrected.py

启动前另行确认执行；先准备相同固定版本的独立.venv及本地完整模型，
模型revision/SHA与corrected-bundle.json一致。训练脚本不会安装、下载或修改环境。
若已准备好旧包的.venv，复用它，不要重装。可显式准备模型（此动作不训练）：
  USE_TF=0 HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \\
    .venv/bin/python rpo_corrected.py --prepare-model --model-dir /绝对路径/Qwen3-8B
该动作只下载缺少的固定revision文件，已有文件SHA不符则停止，不覆盖；完整模型不重复下载。
仅当确认后，在Linux的本包目录运行：
  bash run_rpo_corrected.sh --execute --ack-synthetic-data \\
    --reviewed-data-sha {preferences_sha} \\
    --model-dir /绝对路径/Qwen3-8B --out runs/corrected-rpo

不覆盖旧目录；输出存在则拒绝再启动。输出runs/rpo-corrected-results.zip。
返回结果后离线复算，再决定是否采用；不得只根据training loss认定进步。
"""


def build(old_dir, corrections_dir, out, approval, gpu="NVIDIA GeForce RTX 4090"):
    archive = out.with_suffix(".zip")
    if not approval:
        raise ValueError("explicit_sample_review_confirmation_required")
    if out.exists() or archive.exists():
        raise ValueError("new_corrected_output_required")
    if gpu not in runtime.GPUS:
        raise ValueError("unsupported_single_gpu")
    old = json.loads((old_dir / "rpo-bundle.json").read_text(encoding="utf-8"))
    if old["plan"] != runtime.PLAN or old.get("sample_rule_review", {}).get("status") != "approved_with_deterministic_corrections":
        raise ValueError("verified_reviewed_v1_source_required")
    protected = {}
    for name, digest in old["files"].items():
        p = old_dir / name
        if runtime.file_sha(p) != digest:
            raise ValueError("old_bundle_changed")
        protected[p] = digest
    manifest_path = corrections_dir / "manifest.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, digest in original["files_sha256"].items():
        p = corrections_dir / name
        if runtime.file_sha(p) != digest:
            raise ValueError("reviewed_corrections_changed")
        protected[p] = digest
    corrected = read_rows(corrections_dir / "train.oracle.jsonl")
    cases, pairs = mixed_training(read_rows(old_dir / "train.oracle.jsonl"),
                                  read_rows(old_dir / "preferences.jsonl"), corrected)
    source = Path(__file__).parent
    entries = {name: (source / name).read_bytes() for name in runtime.RUNTIME}
    for name in ("evaluation/validation.jsonl", "evaluation/blind_test.jsonl",
                 "v1-validation.predictions.jsonl", "v1-adapter/adapter_config.json",
                 "v1-adapter/adapter_model.safetensors"):
        entries[name] = (old_dir / name).read_bytes()
    entries["train.oracle.jsonl"] = rows_bytes(cases)
    entries["preferences.jsonl"] = rows_bytes(pairs)
    entries["evaluation/correction_probe.jsonl"] = (corrections_dir / "probe.oracle.jsonl").read_bytes()
    # Read IDs from public prompts, never interpret the held-out blind answers here.
    original_dataset = old_dir.parent / "tool-calling-v2"
    ids = {split: [row["id"] for row in read_rows(original_dataset / f"{split}.prompts.jsonl")]
           for split in ("validation", "blind_test")}
    ids["correction_probe"] = [row["id"] for row in read_rows(corrections_dir / "probe.prompts.jsonl")]
    entries["evaluation/ids.json"] = (canonical(ids) + "\n").encode("utf-8")
    entries["reviewed-source-manifest.json"] = manifest_path.read_bytes()
    review = {"status": "user_confirmed_sample_review_completed", "date": "2026-10-07",
              "authority": "direct_user_instruction", "user_statement": "审核完成",
              "scope": "correction_samples_and_rules", "sample_scenarios": 21,
              "all_examples_individually_certified": False,
              "source_manifest_sha256": sha(entries["reviewed-source-manifest.json"]),
              "source_training_sha256": original["files_sha256"]["train.oracle.jsonl"],
              "source_probe_sha256": original["files_sha256"]["probe.oracle.jsonl"]}
    entries["review.json"] = (canonical(review) + "\n").encode("utf-8")
    audit = {**runtime.COUNTS, "review": review, "old_families_preserved": 30,
             "replay_families": dict(Counter(c["family"] for c in cases if not c["id"].startswith("correction:"))),
             "rejected_codes_by_exposure": dict(Counter(p["rejected_code"] for p in pairs)),
             "raw_negative_bytes_preserved": True, "all_positive_checks": 2400,
             "all_negative_checks": 2400, "new_scorer": False, "gpu_loaded": False,
             "model_calls": 0, "blind_answers_used_for_preparation": False,
             "training_cuda_verified": False, "claimed_training_gain": None}
    entries["data-audit.json"] = (canonical(audit) + "\n").encode("utf-8")
    preferences_sha = sha(entries["preferences.jsonl"])
    entries["CLOUD_RPO_CORRECTED.txt"] = instructions(preferences_sha, gpu).encode("utf-8")
    manifest = {"schema": runtime.SCHEMA, "plan": runtime.PLAN, "counts": runtime.COUNTS,
                "review": review, "preferences_sha256": preferences_sha,
                "old_bundle_sha256": runtime.file_sha(old_dir / "rpo-bundle.json"),
                **{k: old[k] for k in ("versions", "model_files_sha256", "v1_adapter_sha256", "v1_validation")},
                "gpu": gpu, "gpu_capability": runtime.GPUS[gpu], "historical_gpu": old["gpu"],
                "baseline_policy": runtime.BASELINE_POLICY, "model_revision": runtime.MODEL_REVISION,
                "files": {name: sha(raw) for name, raw in sorted(entries.items())}}
    entries["corrected-bundle.json"] = (canonical(manifest) + "\n").encode("utf-8")
    out.mkdir(parents=True, exist_ok=False)
    for name, raw in entries.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    runtime.load_bundle(out)
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
        for name, raw in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 7, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, raw)
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None or any(z.read(name) != raw for name, raw in entries.items()):
            raise ValueError("corrected_archive_failed")
    if any(runtime.file_sha(path) != digest for path, digest in protected.items()):
        raise ValueError("source_modified")
    return {"status": "review_bound_and_prepared_not_executed", **runtime.COUNTS,
            "zip": str(archive.resolve()), "zip_sha256": runtime.file_sha(archive),
            "reviewed_data_sha256": preferences_sha, "gpu_loaded": False, "model_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-bundle", type=Path, required=True)
    parser.add_argument("--corrections", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--confirm-sample-review", action="store_true")
    parser.add_argument("--gpu", choices=sorted(runtime.GPUS), default="NVIDIA GeForce RTX 4090")
    args = parser.parse_args()
    print(canonical(build(args.old_bundle, args.corrections, args.out, args.confirm_sample_review, args.gpu)))
