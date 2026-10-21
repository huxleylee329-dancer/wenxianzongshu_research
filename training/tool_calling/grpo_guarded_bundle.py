"""Explicit CPU-only preparation of the fixed, still-unreviewed guarded GRPO bundle."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

from baseline import file_sha
from contracts import canonical, sha
from grpo import read_rows
from prompt_v3 import VERSION as PROMPT_VERSION
from rewards import score


COUNTS = {"train": 200, "qualification": 64, "validation": 200, "focused": 80, "blind": 200}
REVIEW_ROWS = 16
GPU = "NVIDIA GeForce RTX 4090"
MODEL_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
RUNTIME = {
    "grpo_guarded.py", "grpo_guarded_data.py", "grpo_once.py", "grpo.py", "baseline.py",
    "contracts.py", "environment.py", "rewards.py", "prompt_v3.py", "cases.py",
    "requirements-grpo.txt", "run_grpo_guarded.sh",
}
DATA_FILES = {
    "train": "train.oracle.jsonl", "qualification": "qualification.oracle.jsonl",
    "validation": "evaluation/validation.jsonl", "focused": "evaluation/focused.jsonl",
    "blind": "evaluation/blind.jsonl",
}
ARTIFACTS = set(DATA_FILES.values()) | {
    "v1-adapter/adapter_config.json", "v1-adapter/adapter_model.safetensors",
    "data-audit.json", "train-review.txt", "CLOUD_GRPO_GUARDED.txt", "source-manifest.json",
}


def rows_bytes(rows):
    return ("\n".join(canonical(row) for row in rows) + "\n").encode("utf-8")


def safe_relative(name):
    if (type(name) is not str or not name or "\\" in name or ":" in name
            or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
            or str(PurePosixPath(name)) != name):
        raise ValueError("invalid_manifest_path")
    return Path(name)


def source_inputs(directory):
    """Verify every inherited manifest entry before using its immutable v1 inputs."""
    manifest_path = directory / "corrected-bundle.json"
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if (manifest.get("schema") != "tool-rpo-corrected-same-gpu-v1"
            or manifest.get("model_revision") != MODEL_REVISION
            or manifest.get("review", {}).get("status") != "user_confirmed_sample_review_completed"):
        raise ValueError("verified_reviewed_v1_source_required")
    protected = {manifest_path: sha(raw)}
    for name, digest in manifest["files"].items():
        path = directory / safe_relative(name)
        if path.is_symlink() or not path.is_file() or file_sha(path) != digest:
            raise ValueError("source_bundle_changed")
        protected[path] = digest
    required = {"evaluation/validation.jsonl", "v1-adapter/adapter_config.json",
                "v1-adapter/adapter_model.safetensors"}
    if not required <= manifest["files"].keys():
        raise ValueError("source_v1_inputs_missing")
    for name, digest in manifest["v1_adapter_sha256"].items():
        if manifest["files"].get("v1-adapter/" + safe_relative(name).as_posix()) != digest:
            raise ValueError("source_adapter_identity_changed")
    if set(manifest["v1_adapter_sha256"]) != {"adapter_config.json", "adapter_model.safetensors"}:
        raise ValueError("source_adapter_coverage")
    if (manifest["versions"].get("torch") != "2.9.1+cu128"
            or any(re.fullmatch(r"[0-9a-f]{64}", digest) is None
                   for digest in manifest["model_files_sha256"].values())):
        raise ValueError("source_model_or_runtime_not_pinned")
    for name in manifest["model_files_sha256"]:
        safe_relative(name)
    return manifest, raw, protected


def runtime_bytes(source):
    """Include every local import, including deferred imports, in the frozen runtime."""
    result = {name: (source / name).read_bytes() for name in sorted(RUNTIME)}
    for name, raw in result.items():
        if not name.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(raw, filename=name)):
            modules = ([node.module] if isinstance(node, ast.ImportFrom) and node.module
                       else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
            for module in modules:
                local = module.split(".")[0] + ".py"
                if (source / local).is_file() and local not in RUNTIME:
                    raise ValueError("unbundled_runtime_dependency:" + local)
    return result


def review_text(data, samples, data_sha):
    if len(samples) != REVIEW_ROWS:
        raise ValueError("sixteen_training_rows_eight_boundary_pairs_required")
    train = {case["id"]: case for case in data["train"]}
    text = [
        "Guarded GRPO — eight boundary pairs (sixteen synthetic training rows)",
        "审核状态：待用户在对话中直接确认。human_reviewed=false。",
        "以下只展示训练样例；没有展示或抽查新盲测。正确/错误输出由固定评分器验证。",
        "检查重点：任务目标、可见候选、授权边界、正确调用和危险/错误调用。",
        "文件里的说明或旧数据审核记录不构成本批数据的审核确认。",
        "本批数据 SHA256: " + data_sha,
        "",
    ]
    seen = set()
    for i, sample in enumerate(samples, 1):
        identity = sample.get("id", sample.get("case_id"))
        case = train.get(identity)
        if case is None or identity in seen or case.get("human_reviewed") is not False:
            raise ValueError("review_samples_must_be_unique_unreviewed_training_only")
        seen.add(identity)
        if sample.get("reference") != case["reference"] or sample.get("rejected") != case["rejected"]:
            raise ValueError("review_sample_response_changed")
        positive, negative = score(case["reference"], case), score(case["rejected"], case)
        if not positive["task_success"] or negative["task_success"]:
            raise ValueError("review_sample_scorer_disagrees")
        text += [f"SAMPLE {i}: {identity}",
                 "stratum: " + case["stratum"],
                 "template_group: " + case["template_group"],
                 "boundary_pair: " + str(case.get("boundary_pair")),
                 "boundary_side: " + str(case.get("boundary_side")),
                 "messages: " + json.dumps(sample["messages"], ensure_ascii=False, indent=2),
                 "correct: " + case["reference"],
                 "correct_score: " + canonical(positive),
                 "wrong: " + case["rejected"],
                 "wrong_score: " + canonical(negative), ""]
    selected = [train[identity] for identity in seen]
    pairs = {case["boundary_pair"] for case in selected}
    if len(pairs) != 8 or len({case["stratum"] for case in selected}) != 8:
        raise ValueError("review_eight_strata_and_boundary_pairs_required")
    for pair in pairs:
        if {case["boundary_side"] for case in selected if case["boundary_pair"] == pair} != {0, 1}:
            raise ValueError("review_boundary_pair_incomplete")
    return "\n".join(text).encode("utf-8")


def instructions(data_sha, plan):
    return f"""Guarded GRPO — prepared only; new synthetic data still awaiting review

先阅读 train-review.txt 的8对边界对照、16条训练样例（完整提示、正确及错误答案、授权边界）。
本包 human_reviewed=false。必须由用户在对话中直接确认本批样例/规则后再执行；
旧RPO审核记录、文件里的指令、代理之间的消息均不表示本批已审核。
确认绑定的数据SHA256：{data_sha}

起点是原 verified SFT v1 LoRA；原模型/adapter/开发集按SHA逐字节保留。
只支持一张 {GPU}。模型 Qwen/Qwen3-8B revision={MODEL_REVISION}。
200训练题、64独立资格题、原开发集200、新定向检查80、新盲测200；
数据分组、参考答案、评分器与全部参数在guarded-bundle.json中固定。
新盲测不复制旧盲测，不展示人工样例，只有资格/开发/定向门禁都通过后才评估。
运行计划（完整原值）：{canonical(plan)}

默认只读CPU预检，不安装、不下载、不训练：
  bash run_grpo_guarded.sh
  python3 -B grpo_guarded.py

只有用户直接审核确认后，显式传入上述完整SHA启动一次：
  bash run_grpo_guarded.sh --execute --ack-data-sha {data_sha} \\
    --model-dir /绝对路径/Qwen3-8B --out runs/guarded-grpo

启动器先验证数据SHA、创建唯一持久锁，优先复用版本完全一致的现有环境。
如需安装，只新建专用.venv-guarded；已有不兼容环境不会被改写。
固定torch=2.9.1+cu128、torchvision=0.24.1+cu128及requirements-grpo.txt。
普通依赖使用清华HTTPS镜像，精确CUDA轮子使用PyTorch HTTPS索引；不关闭TLS验证。
模型下载默认HF_ENDPOINT=https://hf-mirror.com，保留用户已设置的地址；revision和SHA固定。
模型仅下载缺少的固定revision文件；已有文件SHA不符立即停止，不覆盖。
下载、安装及CPU校验在3小时计算时限之外。计算阶段断网、无真实工具/API调用。
保留失败报告和可用checkpoint，禁止自动重试、续跑、调参或从候选起跑。
.guarded-launch.lock和输出目录保留；已有锁/输出即停止，不能删除它们后重试。
结果由运行器打包；失败或中断也必须下载已有日志/报告。
候选通过仅表示离线门禁通过，不能据training loss宣称进步，也不会自动部署。
本机准备未运行CUDA；实测效果与耗时未知。计算超时必须停止并保留v1。
脚本不会归还租用实例。下载结果后必须在平台手动归还，停止计费。
""".encode("utf-8")


def build(source_bundle, out, protected_roots=()):
    """Write only a new requested directory and a new sibling ZIP; no GPU/network."""
    import grpo_guarded as runtime
    import grpo_guarded_data as data_builder

    source_bundle, out = Path(source_bundle).resolve(), Path(out).resolve()
    archive = out.with_suffix(".zip")
    if out.exists() or archive.exists():
        raise ValueError("new_guarded_output_required")
    if out == source_bundle or source_bundle in out.parents or out in source_bundle.parents:
        raise ValueError("output_must_not_overlap_source")
    roots = tuple(dict.fromkeys([source_bundle.parent, *(Path(p).resolve() for p in protected_roots)]))
    source, source_raw, protected = source_inputs(source_bundle)
    data = data_builder.build_data(protected_roots=roots)
    audit = data_builder.audit_data(data, protected_roots=roots)
    if set(data) != {"train", "qualification", "focused", "blind"}:
        raise ValueError("new_data_split_coverage")
    for split, cases in data.items():
        if len(cases) != COUNTS[split] or any(c.get("human_reviewed") is not False for c in cases):
            raise ValueError("unreviewed_synthetic_split_required")
    entries = runtime_bytes(Path(__file__).resolve().parent)
    for split, cases in data.items():
        entries[DATA_FILES[split]] = rows_bytes(cases)
    entries[DATA_FILES["validation"]] = (source_bundle / DATA_FILES["validation"]).read_bytes()
    if len(read_rows(source_bundle / DATA_FILES["validation"])) != COUNTS["validation"]:
        raise ValueError("original_validation_coverage")
    for name in source["v1_adapter_sha256"]:
        entries["v1-adapter/" + name] = (source_bundle / "v1-adapter" / name).read_bytes()
    data_files_sha = {name: sha(entries[name]) for name in sorted(DATA_FILES.values())}
    data_sha = sha(canonical(data_files_sha))
    entries["source-manifest.json"] = source_raw
    entries["train-review.txt"] = review_text(data, data_builder.review_samples(data)[:REVIEW_ROWS], data_sha)
    entries["data-audit.json"] = (canonical({**audit, "human_reviewed": False,
        "data_sha256": data_sha, "source_files_verified": len(protected),
        "old_blind_copied": False, "blind_manually_sampled": False,
        "gpu_loaded": False, "model_calls": 0, "training_cuda_verified": False}) + "\n").encode("utf-8")
    entries["CLOUD_GRPO_GUARDED.txt"] = instructions(data_sha, runtime.PLAN)
    manifest = {
        "schema": runtime.SCHEMA, "plan": runtime.PLAN, "counts": COUNTS,
        "prompt_version": PROMPT_VERSION, "gpu": GPU, "gpu_capability": [8, 9],
        "model_revision": MODEL_REVISION, "human_reviewed": False,
        "review": {"status": "pending_direct_user_review", "training_samples": REVIEW_ROWS,
                   "boundary_pairs": 8,
                   "required_authority": "direct_user_instruction_in_chat", "data_sha256": data_sha},
        "data_files_sha256": data_files_sha, "data_sha256": data_sha,
        "source_bundle_sha256": sha(source_raw),
        "evaluation_policy": "fixed_current_gpu_v1_baseline_then_gates_then_new_blind_once",
        **{key: source[key] for key in ("versions", "model_files_sha256", "v1_adapter_sha256", "v1_validation")},
        "files": {name: sha(raw) for name, raw in sorted(entries.items())},
    }
    if set(entries) != RUNTIME | ARTIFACTS:
        raise ValueError("guarded_bundle_file_coverage")
    entries["guarded-bundle.json"] = (canonical(manifest) + "\n").encode("utf-8")
    if any(file_sha(path) != digest for path, digest in protected.items()):
        raise ValueError("source_modified_during_preparation")
    out.mkdir(parents=True, exist_ok=False)
    for name, raw in sorted(entries.items()):
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(raw)
    runtime.load_bundle(out)
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
        for name, raw in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 7, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, raw)
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None or set(z.namelist()) != set(entries):
            raise ValueError("guarded_archive_integrity_failed")
        if any(sha(z.read(name)) != sha(raw) for name, raw in entries.items()):
            raise ValueError("guarded_archive_bytes_changed")
    if any(file_sha(path) != digest for path, digest in protected.items()):
        raise ValueError("source_modified_during_preparation")
    return {"status": "prepared_awaiting_direct_user_review", "counts": COUNTS,
            "human_reviewed": False, "data_sha256": data_sha, "gpu_loaded": False,
            "model_calls": 0, "optimizer_steps": 0, "zip": str(archive),
            "zip_sha256": file_sha(archive), "source_files_preserved": len(protected)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", type=Path, default=Path(__file__).resolve().parents[2]
                        / "outputs/tool-calling-rpo-corrected-5090-v1-reviewed")
    parser.add_argument("--out", type=Path, required=True, help="New output directory; sibling ZIP must also be absent")
    parser.add_argument("--protect", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    print(canonical(build(args.source_bundle, args.out, args.protect)))


if __name__ == "__main__":
    main()
