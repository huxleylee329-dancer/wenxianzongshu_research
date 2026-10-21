"""Build the fixed 1200-case pool and a self-contained, v1-initialized cloud experiment."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import zipfile

from cases import call, context, public_prompt, visible_papers
from contracts import canonical, parse_response, sha
import dataset
from environment import OfflineEnvironment
import grpo
import grpo_once as once
from prepare import self_check
from prompt_v3 import upgrade
import sft
import sft_bundle


PAIR_KINDS = ("read_year", "read_provider", "doi_permission", "doi_target", "search_permission")
CODE = ("grpo_once.py", "grpo_once_setup.py", "baseline.py", "contracts.py", "environment.py",
        "rewards.py", "prompt_v3.py", "grpo.py", "requirements-grpo.txt", "run_grpo_once.sh",
        "CLOUD_GRPO_ONCE.txt")


def make_pair(kind, index):
    if kind not in PAIR_KINDS or not 0 <= index < 20:
        raise ValueError("invalid_pair")
    seed = f"grpo-once-pair/{kind}/{index}"
    rng = random.Random(int(sha(seed)[:16], 16))
    topic = dataset.TOPICS["train"][index % len(dataset.TOPICS["train"])]
    title, query = f"合成记录：{topic}约束试验", topic + " 机制"
    papers = [{"paper_id": "paper:" + sha(seed + str(i))[:16], "title": title,
               "provider": ("openalex", "arxiv")[i], "year": (2020, 2024)[i],
               "doi": "10.99999/once." + sha(seed + str(i))[:20],
               "url": "https://papers.invalid/" + sha(seed + str(i)),
               "queries": [query], "text": "离线虚构证据，不是真实论文。"} for i in range(2)]
    ctx = context("hybrid", ["openalex", "arxiv", "crossref"], [], visible_papers(papers))
    ctx["documents"] = []
    rng.shuffle(ctx["candidates"])
    rng.shuffle(ctx["allowed_providers"])
    chosen = papers[index % 2]
    result = []
    for side in (0, 1):
        view = deepcopy(ctx)
        rule = {"kind": kind, "side": side, "target": chosen["paper_id"]}
        if kind == "read_year":
            instruction = f"请阅读《{title}》全文。" + (f"只要{chosen['year']}年的记录。" if side else "")
            answer = call("read_paper", paper_id=chosen["paper_id"]) if side else {
                "action": "clarify", "reason": "ambiguous_paper"}
        elif kind == "read_provider":
            instruction = f"读取标题为《{title}》的论文。" + (f"限定来源{chosen['provider']}。" if side else "")
            answer = call("read_paper", paper_id=chosen["paper_id"]) if side else {
                "action": "clarify", "reason": "ambiguous_paper"}
        elif kind == "doi_permission":
            if not side:
                view["allowed_providers"].remove("crossref")
            instruction = f"请解析 DOI https://doi.org/{chosen['doi']}，获取元数据，不读全文。"
            answer = call("resolve_paper", identifier=chosen["doi"]) if side else {
                "action": "clarify", "reason": "permission_required"}
        elif kind == "doi_target":
            chosen = papers[side]
            rule["target"] = chosen["paper_id"]
            instruction = (f"记录甲的DOI是{papers[0]['doi']}；记录乙的DOI是{papers[1]['doi']}。"
                           f"本轮只解析记录{('甲', '乙')[side]}的元数据。")
            answer = call("resolve_paper", identifier=chosen["doi"])
        else:
            if not side:
                view["allowed_providers"].remove("openalex")
            instruction = f"只在openalex搜索“{query}”，最多2条，不切换其他源。"
            answer = call("search_papers", query=query, providers=["openalex"], per_source=2) if side else {
                "action": "clarify", "reason": "permission_required"}
        answer = parse_response(canonical(answer))
        world = {"papers": deepcopy(papers), "documents": []}
        goals = OfflineEnvironment(view, world).execute(answer)["ids"]
        result.append({"id": "once:" + sha(seed + f"/side{side}")[:24], "split": "train",
                       "family": "paired_" + kind, "split_group": "paired_" + kind,
                       "pair_id": sha(seed)[:24], "pair_index": index, "side": side,
                       "topic": topic, "synthetic": True, "human_reviewed": False,
                       "category": answer.get("tool", answer["action"]), "instruction": instruction,
                       "context": view, "world": world, "reference": canonical(answer),
                       "goal_ids": goals, "rule": rule})
    return result


def audit_pairs(pairs, original):
    if len(pairs) != 200 or Counter(c["family"] for c in pairs) != Counter({"paired_" + k: 40 for k in PAIR_KINDS}):
        raise ValueError("paired_coverage")
    held = original["validation"] + original["blind_test"]
    for getter in (lambda c: c["id"], lambda c: c["topic"], lambda c: c["family"],
                   lambda c: sha(canonical(upgrade(public_prompt(c))))):
        if {getter(c) for c in pairs} & {getter(c) for c in held}:
            raise ValueError("paired_held_out_overlap")
    held_ids = {p[key] for c in held for p in c["world"]["papers"] for key in ("paper_id", "doi")}
    if {p[key] for c in pairs for p in c["world"]["papers"] for key in ("paper_id", "doi")} & held_ids:
        raise ValueError("paired_entity_overlap")
    report = self_check(pairs)
    for left, right in zip(pairs[::2], pairs[1::2]):
        if left["pair_id"] != right["pair_id"] or [left["side"], right["side"]] != [0, 1]:
            raise ValueError("pair_alignment")
        kind = left["rule"]["kind"]
        if kind in ("doi_permission", "search_permission"):
            if left["instruction"] != right["instruction"]:
                raise ValueError("permission_pair_task_changed")
            lctx, rctx = deepcopy(left["context"]), deepcopy(right["context"])
            key = "crossref" if kind == "doi_permission" else "openalex"
            if set(rctx.pop("allowed_providers")) - set(lctx.pop("allowed_providers")) != {key} or lctx != rctx:
                raise ValueError("permission_pair_changes_more_than_authorization")
        elif left["context"] != right["context"]:
            raise ValueError("selection_pair_context_changed")
        for c in (left, right):
            gold = parse_response(c["reference"])
            target = next(p for p in c["context"]["candidates"] if p["paper_id"] == c["rule"]["target"])
            if kind.startswith("read_"):
                field = kind.split("_", 1)[1]
                matches = [p for p in c["context"]["candidates"] if p["title"] == target["title"]
                           and (not c["side"] or p[field] == target[field])]
                expected = call("read_paper", paper_id=matches[0]["paper_id"]) if len(matches) == 1 else {
                    "action": "clarify", "reason": "ambiguous_paper"}
                if gold != expected:
                    raise ValueError("selection_pair_label_changed")
            elif kind == "doi_target" and gold != call("resolve_paper", identifier=target["doi"]):
                raise ValueError("doi_target_label_changed")
    return report | {"pairs": 100, "held_out_identity_overlap": 0, "human_reviewed": False,
                     "validation_informed_design": True, "blind_predictions_used": False}


def select_ids(original, pairs):
    buckets = {f: sorted([c for c in original if c["family"] == f], key=lambda c: sha(c["id"]))
               for f in dataset.FAMILIES["train"]}
    train = [buckets[f][0] for f in dataset.FAMILIES["train"]]
    for family in ("doi_bare", "doi_url", "doi_prefix", "doi_case", "read_year_provider"):
        train.append(buckets[family][1])
    train += [c for c in pairs if c["pair_index"] in (0, 1)]
    # Fixed before any model generation: no selection using dev answers or probe rewards.
    signal_families = ("doi_bare", "doi_url", "doi_prefix", "doi_case", "read_id", "read_year_provider",
                       "read_title", "search_hybrid_remote", "library_subset", "clarify_provider",
                       "clarify_identifier", "finish_cancel")
    probe = [buckets[f][2] for f in signal_families] + [c for c in pairs if c["pair_index"] in (2, 3)]
    rng = random.Random(grpo.SEED)
    rng.shuffle(train)
    rng.shuffle(probe)
    if len(train) != 50 or len(probe) != 32 or {c["id"] for c in train} & {c["id"] for c in probe}:
        raise ValueError("fixed_quota_invalid")
    return [c["id"] for c in train], [c["id"] for c in probe]


def build(dataset_dir, v1_bundle, v1_results, out, archive):
    if out.exists() or archive.exists():
        raise ValueError("new_bundle_paths_required")
    data = dataset.load_verified(dataset_dir)
    _, _, _, before = sft.load_bundle(v1_bundle)
    with tempfile.TemporaryDirectory() as temp:
        sft_bundle.score_results(dataset_dir, v1_bundle, v1_results / "runs/sft-after",
                                 v1_results / "runs/sft-train", Path(temp) / "check.json")
    source = Path(__file__).parent
    predictions = grpo.read_rows(v1_results / "runs/sft-after/predictions.jsonl")
    old_score = once.evaluate(data["validation"], predictions)
    if (old_score["correct"], old_score["unsafe"], old_score["contract_errors"]) != (180, 3, 4):
        raise ValueError("verified_v1_90_percent_required")
    pairs = [c for kind in PAIR_KINDS for i in range(20) for c in make_pair(kind, i)]
    audit = audit_pairs(pairs, data)
    train_ids, signal_ids = select_ids(data["train"], pairs)
    pool = data["train"] + pairs
    entries = {name: (source / name).read_bytes() for name in CODE}
    entries["train.oracle.jsonl"] = sft_bundle.rows_bytes(pool)
    entries["evaluation/validation.jsonl"] = sft_bundle.rows_bytes(data["validation"])
    entries["evaluation/blind_test.jsonl"] = sft_bundle.rows_bytes(data["blind_test"])
    entries["v1-validation.predictions.jsonl"] = sft_bundle.rows_bytes(predictions)
    entries["audit.json"] = (canonical(audit) + "\n").encode()
    adapter_dir = v1_results / "runs/sft-train/adapter"
    adapter_hashes = {}
    for name in ("adapter_config.json", "adapter_model.safetensors"):
        entries["v1-adapter/" + name] = (adapter_dir / name).read_bytes()
        adapter_hashes[name] = sha(entries["v1-adapter/" + name])
    manifest = {"schema": once.SCHEMA, "plan": once.PLAN, "prompt_version": grpo.VERSION,
                "counts": {"train": 1200, "validation": 200, "blind_test": 200},
                "old_train_preserved": 1000, "new_pairs": 100, "human_reviewed": False,
                "train_ids": train_ids, "signal_ids": signal_ids,
                "train_quotas": {"original": 30, "paired": 20}, "probe_quotas": {"original": 12, "paired": 20},
                "source_dataset_sha256": grpo.file_sha(dataset_dir / "manifest.json"),
                "v1_validation": {k: old_score[k] for k in ("n", "correct", "unsafe", "contract_errors", "families")},
                "v1_adapter_sha256": adapter_hashes,
                **{k: before[k] for k in ("model_files_sha256", "versions", "gpu")},
                "evaluation_policy": "development-informed; blind read only after validation gate; never train on evaluation",
                "files": {name: sha(value) for name, value in entries.items()}}
    entries["once-bundle.json"] = (canonical(manifest) + "\n").encode()
    out.mkdir(parents=True, exist_ok=False)
    for name, value in entries.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(value)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
        for name, value in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, value)
    once.load_bundle(out)
    return {"status": "prepared_not_trained", "train_pool": 1200, "fixed_training_groups": 50,
            "probe_groups": 32, "reference_checks": audit["reference_successes"],
            "negative_checks": audit["negative_checks"], "gpu_loaded": False, "model_calls": 0,
            "zip": str(archive.resolve()), "zip_sha256": grpo.file_sha(archive)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "v1-bundle", "v1-results", "out", "zip"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    print(canonical(build(args.dataset, args.v1_bundle, args.v1_results, args.out, args.zip)))


if __name__ == "__main__":
    main()
