"""Offline corrective examples only: no trainer, model loading or tool dispatch.

The exposed development errors identify skill gaps, NOT reusable training rows.
These synthetic probes are targeted diagnostics, not a fresh blind benchmark.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import random
import zipfile

from cases import call, context, public_prompt, visible_papers
from contracts import PROVIDERS, SYSTEM, canonical, sha
from environment import OfflineEnvironment
from prompt_v3 import VERSION as PROMPT_VERSION, upgrade
from rewards import equivalent, score
from sft_refresh import expected_action


VERSION = "tool-corrections-v1-data-only"
GROUPS = ("stop_reason", "paper_binding", "json_schema", "document_scope")
COUNTS = {"train": 80, "probe": 20}  # Per group; not independent task templates.
TOPICS = {
    "train": ("热声制冷", "柔性压电器件", "城市雨洪调蓄", "海洋声学通信",
              "仿生黏附", "光催化制氨", "低温余热发电", "木质素转化",
              "自修复涂层", "流态化传热", "电渗析脱盐", "超声无损检测"),
    "probe": ("液滴微流控", "磁流变阻尼", "空间碎片跟踪", "植物表型测量"),
}
SOURCE_FILES = (
    "outputs/tool-calling-v2/train.prompts.jsonl",
    "outputs/tool-calling-v2/validation.prompts.jsonl",
    "outputs/tool-calling-v2/blind_test.prompts.jsonl",
    "outputs/tool-calling-rpo-v1-reviewed/train.oracle.jsonl",
)


def instruction(spec, split, index):
    """Render explicit constraints, without referring to a reference action/label."""
    if spec["kind"] == "stop":
        intent, pid = spec["intent"], spec["paper_id"]
        if intent == "cancel":
            return ("取消本轮任务，不再检索或读取。", "我已撤销这次请求，请结束工具阶段。")[split == "probe"]
        if intent == "explain":
            return ("只解释检索摘要与读取全文的区别，不实际操作工具。",
                    "这轮只想听工具用途说明，不需要查找任何论文。")[split == "probe"]
        need = "完整元数据" if intent == "metadata" else "全文"
        return (f"需要候选 {pid} 的{need}。检查已有结果；已完整取得则结束，否则读取目标。"
                if split == "train" else
                f"目标是 {pid} 的{need}；仅当 existing_results 尚未满足此要求时才读取。")
    if spec["kind"] == "read":
        labels = {"title": "标题", "year": "年份", "provider": "来源", "paper_id": "标识"}
        constraints = [f"{labels[k]}={v}" for k, v in spec["filters"].items()]
        if index % 2:
            constraints.reverse()
        return (("读取同时符合以下条件的论文：" if split == "train" else "请从候选中打开全文，须同时满足：")
                + "；".join(constraints) + "。没有指定其他区分条件。")
    if spec["kind"] == "library":
        scope = ("只使用标题为" + "、".join(f"《{t}》" for t in spec["titles"]) + "的文档"
                 if "titles" in spec else f"使用所有已授权文档，但排除标识为 {spec['exclude']} 的一个")
        return (("在本地库中" if split == "train" else "本轮仅处理本地资料，") + scope
                + f"，检索串保持为“{spec['query']}”，limit 参数指定为 {spec['limit']}。")
    raise ValueError("unsupported_correction_rule")


def label(spec, ctx):
    if spec["kind"] != "stop":
        return expected_action(spec, ctx)
    if spec["intent"] in ("cancel", "explain"):
        return {"action": "finish", "reason": "no_tool_needed"}
    satisfied = any(r["paper_id"] == spec["paper_id"] and (
        all(r.get(k) not in (None, "") for k in ("title", "provider", "year", "doi", "url"))
        if spec["intent"] == "metadata" else r.get("status") == "full_text" and bool(r.get("text")))
        for r in ctx["existing_results"])
    return ({"action": "finish", "reason": "already_satisfied"} if satisfied else
            call("read_paper", paper_id=spec["paper_id"]))


def make_case(split, group, index):
    if split not in COUNTS or group not in GROUPS or not 0 <= index < COUNTS[split]:
        raise ValueError("invalid_correction_index")
    seed = f"{VERSION}/{split}/{group}/{index}"
    rng = random.Random(int(sha(seed)[:16], 16))
    variants = {"stop_reason": 5, "paper_binding": 4, "json_schema": 8, "document_scope": 4}[group]
    # Rotate topics within each scenario, not in lockstep with the label.
    topic = TOPICS[split][(index // variants + 5 * (index % variants)) % len(TOPICS[split])]
    query = f"{topic} 的测量条件"
    providers = rng.sample(list(PROVIDERS), 3)
    year = 2020 + index % 6
    papers = []
    for number in range(12):
        key = sha(f"{seed}/paper/{number}")
        papers.append({"paper_id": "paper:" + key[:(16, 24, 32)[number % 3]],
                       "title": f"合成{topic}研究记录{number + 1}",
                       "provider": providers[number % 3], "year": year + number % 3,
                       "doi": "10.99999/correction." + key[:20],
                       "url": "https://papers.invalid/" + key,
                       "queries": [query], "text": "虚构离线记录，不构成论文证据。"})
    docs = [{"document_id": sha(f"{seed}/doc/{n}")[:32],
             "title": f"合成{topic}记录{n + 1}", "queries": [query], "text": "虚构本地段落。"}
            for n in range(6)]
    rng.shuffle(papers)
    rng.shuffle(docs)
    target = rng.choice(papers)
    others = [p for p in papers if p is not target]
    ctx = context("hybrid", providers, [d["document_id"] for d in docs])
    rng.shuffle(ctx["allowed_document_ids"])
    ctx["documents"] = [{k: d[k] for k in ("document_id", "title")} for d in docs]
    negative = None
    if group == "stop_reason":
        scenario = ("metadata_present", "full_text_present", "cancel", "explain", "wrong_result")[index % 5]
        intent = {"metadata_present": "metadata", "full_text_present": "full_text",
                  "wrong_result": "full_text"}.get(scenario, scenario)
        spec = {"kind": "stop", "intent": intent, "paper_id": target["paper_id"]}
        if scenario == "metadata_present":
            ctx["existing_results"] = visible_papers([target])
        elif scenario == "full_text_present":
            ctx["existing_results"] = [{"paper_id": target["paper_id"], "status": "full_text",
                                        "text": "所需全文已取得（离线合成标记）。"}]
        elif scenario == "wrong_result":
            ctx["existing_results"] = [{"paper_id": others[0]["paper_id"], "status": "full_text",
                                        "text": "仅有另一候选的全文。"}]
    elif group == "paper_binding":
        scenario = ("title_year", "title_provider", "title_year_provider", "still_ambiguous")[index % 4]
        filters = {"title": target["title"]}
        if scenario != "title_provider":
            filters["year"] = target["year"]
        if scenario != "title_year":
            filters["provider"] = target["provider"]
        # Same title but a wrong constraint, and right constraints but a wrong title.
        others[0].update(title=target["title"], year=target["year"] + 1,
                         provider=next(p for p in providers if p != target["provider"]))
        others[1].update(year=target["year"], provider=target["provider"])
        if scenario in ("title_year_provider", "still_ambiguous"):
            others[2].update(title=target["title"], year=target["year"], provider=others[0]["provider"])
            others[3].update(title=target["title"], year=target["year"] + 1, provider=target["provider"])
        if scenario == "still_ambiguous":
            others[4].update(**filters)
        spec = {"kind": "read", "filters": filters}
        negative = call("read_paper", paper_id=(target if scenario == "still_ambiguous" else others[1])["paper_id"])
    elif group == "document_scope":
        scenario = ("title_pair", "exclude_one", "title_near_miss", "keep_scope_at_limit_one")[index % 4]
        if scenario == "title_near_miss":
            docs[1]["title"] = docs[0]["title"] + "（补充稿）"
        ctx["documents"] = [{k: d[k] for k in ("document_id", "title")} for d in docs]
        spec = {"kind": "library", "query": query, "limit": 1 if scenario == "keep_scope_at_limit_one" else 12}
        if scenario == "exclude_one":
            spec["exclude"] = docs[2]["document_id"]
        else:
            spec["titles"] = [docs[0]["title"], docs[4]["title"]]
    else:
        scenario = ("extra_year", "extra_provider", "duplicate_paper_key", "wrong_action",
                    "id_truncated", "id_invented", "id_duplicate_fragment", "extra_top_level")[index % 8]
        spec = {"kind": "read", "filters": {"paper_id": target["paper_id"]}}
    ctx["candidates"] = visible_papers(papers)
    # Document selection must not reduce to taking fixed visible positions.
    rng.shuffle(ctx["documents"])
    expected = label(spec, ctx)
    if negative is None:
        negative = deepcopy(expected)
        if group == "stop_reason":
            negative = {"action": "finish", "reason": ("no_tool_needed" if
                        expected.get("reason") == "already_satisfied" else "already_satisfied")}
        elif group == "document_scope":
            ids = negative["arguments"]["document_ids"]
            if scenario == "keep_scope_at_limit_one":
                negative["arguments"]["document_ids"] = ids[:1]
            else:
                replacement = next(d["document_id"] for d in docs if d["document_id"] not in ids)
                negative["arguments"]["document_ids"] = [replacement, *ids[1:]]
        elif scenario in ("extra_year", "extra_provider"):
            key = scenario.removeprefix("extra_")
            negative["arguments"][key] = target[key]
        elif scenario == "wrong_action":
            negative["action"] = "read_paper"
            del negative["tool"]
        elif scenario == "id_truncated":
            negative["arguments"]["paper_id"] = target["paper_id"][:-1]
        elif scenario == "id_invented":
            negative["arguments"]["paper_id"] = "paper:" + sha(seed + "/absent")[:24]
        elif scenario == "id_duplicate_fragment":
            negative["arguments"]["paper_id"] = "paper:" + target["paper_id"][6:] * 5
        elif scenario == "extra_top_level":
            negative["provider"] = target["provider"]
    rejected = canonical(negative)
    if scenario == "duplicate_paper_key":
        # Deliberately retain both keys. Never json.loads/re-serialize this negative.
        rejected = ('{"action":"call","tool":"read_paper","arguments":{"paper_id":'
                    + canonical(others[0]["paper_id"]) + ',"paper_id":' + canonical(target["paper_id"]) + '}}')
    case = {"id": "correction:" + sha(seed)[:24], "split": split, "family": group,
            "scenario": scenario, "index": index, "topic": topic,
            "synthetic": True, "human_reviewed": False, "task_spec": spec,
            "instruction": instruction(spec, split, index), "context": ctx,
            "world": {"papers": papers, "documents": docs}, "reference": canonical(expected),
            "rejected": rejected}
    case["goal_ids"] = OfflineEnvironment(ctx, case["world"]).execute(expected)["ids"]
    return case


def build_cases():
    return {split: [make_case(split, group, i) for group in GROUPS for i in range(n)]
            for split, n in COUNTS.items()}


def identifiers(value):
    """Collect identity strings only; never use held-out answers to build examples."""
    found = set()
    if type(value) is dict:
        for key, item in value.items():
            if key in ("id", "paper_id", "document_id", "doi", "url") and type(item) is str:
                found.add(item)
            elif key in ("allowed_document_ids", "document_ids") and type(item) is list:
                found.update(x for x in item if type(x) is str)
            else:
                found.update(identifiers(item))
    elif type(value) is list:
        for item in value:
            found.update(identifiers(item))
    return found


def read_protected_metadata(repo):
    """Only public held-out prompts are opened; blind oracle/predictions stay unopened."""
    hashes, ids, prompts = {}, set(), set()
    for name in SOURCE_FILES:
        raw = (repo / name).read_bytes()
        hashes[name] = sha(raw)
        for line in raw.decode("utf-8").splitlines():
            row = json.loads(line)
            messages = row.get("messages", row.get("prompt"))
            if messages is not None:
                prompts.add(sha(canonical(messages)))
                if messages[0]["content"] == SYSTEM:
                    prompts.add(sha(canonical(upgrade(messages))))
                ids.add(row["id"])
                ids.update(identifiers(json.loads(messages[1]["content"])))
            else:
                ids.add(row["id"])
                ids.update(identifiers(row["context"]))
    # Hash sealed artifacts without interpreting their labels, predictions or weights.
    for name in ("outputs/tool-calling-v2/blind_test.oracle.jsonl",
                 "outputs/tool-calling-rpo-v1-reviewed.zip",
                 "outputs/tool-calling-rpo-v1-reviewed/preferences.jsonl",
                 "outputs/tool-calling-rpo-v1-reviewed/rpo-bundle.json"):
        hashes[name] = sha((repo / name).read_bytes())
    return {"hashes": hashes, "identifiers": ids, "prompts": prompts}


def audit(cases, protected):
    report = {"version": VERSION, "human_reviewed": False, "trained": False,
              "real_tool_calls": 0, "model_calls": 0, "blind_predictions_used": False,
              "dev_used_to_identify_skill_gaps": True, "probe_is_unbiased_blind_test": False,
              "old_rpo_runner_compatible": False, "splits": {}}
    split_ids, split_prompts = {}, {}
    for split, n in COUNTS.items():
        rows = cases[split]
        if Counter(c["family"] for c in rows) != Counter({g: n for g in GROUPS}):
            raise ValueError("correction_counts")
        split_ids[split], split_prompts[split] = set(), set()
        labels, rejected_codes, scenarios = Counter(), Counter(), Counter()
        case_ids = set()
        for case in rows:
            if case["id"] in case_ids:
                raise ValueError("duplicate_correction_id")
            case_ids.add(case["id"])
            if case["topic"] not in TOPICS[split] or case["split"] != split:
                raise ValueError("correction_topic_or_split")
            expected = label(case["task_spec"], case["context"])
            if canonical(expected) != case["reference"]:
                raise ValueError("correction_label_binding")
            if instruction(case["task_spec"], split, case["index"]) != case["instruction"]:
                raise ValueError("correction_instruction_binding")
            if not score(case["reference"], case)["task_success"]:
                raise ValueError("correction_reference_failed")
            negative = score(case["rejected"], case)
            if negative["task_success"]:
                raise ValueError("correction_negative_succeeded")
            rejected_codes[negative["code"]] += 1
            labels[expected.get("tool", expected.get("reason"))] += 1
            scenarios[case["scenario"]] += 1
            # Order is not the decision criterion, and private labels are not sent.
            reversed_ctx = deepcopy(case["context"])
            for key in ("candidates", "documents", "allowed_document_ids"):
                reversed_ctx[key].reverse()
            if not equivalent(label(case["task_spec"], reversed_ctx), expected):
                raise ValueError("correction_order_sensitive")
            messages = upgrade(public_prompt(case))
            user = json.loads(messages[1]["content"])
            if set(user) != {"task", "context", "tools"}:
                raise ValueError("private_prompt_leak")
            key = sha(canonical(messages))
            if key in split_prompts[split] or key in protected["prompts"]:
                raise ValueError("duplicate_or_old_prompt")
            split_prompts[split].add(key)
            split_ids[split].update(identifiers({"id": case["id"], "context": case["context"]}))
        if split_ids[split] & protected["identifiers"]:
            raise ValueError("old_identity_overlap")
        report["splits"][split] = {"rows": len(rows), "per_group": dict(Counter(c["family"] for c in rows)),
                                  "scenarios": dict(scenarios), "positive_labels": dict(labels),
                                  "rejected_codes": dict(rejected_codes), "reference_successes": len(rows),
                                  "rejected_failures": len(rows), "order_invariance_checks": len(rows)}
    if split_ids["train"] & split_ids["probe"] or split_prompts["train"] & split_prompts["probe"]:
        raise ValueError("train_probe_overlap")
    from dataset import TOPICS as OLD_TOPICS
    if set(TOPICS["train"]) & set(TOPICS["probe"]) or any(
            set(topics) & set(old) for topics in TOPICS.values() for old in OLD_TOPICS.values()):
        raise ValueError("topic_overlap")
    report["exact_prompt_identity_topic_overlap"] = 0
    report["semantic_independence_claimed"] = False
    return report


def jsonl(rows):
    return ("\n".join(canonical(r) for r in rows) + "\n").encode("utf-8")


def export(repo, out):
    archive = out.with_suffix(".zip")
    if out.exists() or archive.exists():
        raise ValueError("new_correction_output_required")
    protected = read_protected_metadata(repo)
    cases = build_cases()
    report = audit(cases, protected)
    files = {"audit.json": (canonical(report) + "\n").encode("utf-8")}
    for split, rows in cases.items():
        files[f"{split}.oracle.jsonl"] = jsonl(rows)
        files[f"{split}.prompts.jsonl"] = jsonl(
            {"id": c["id"], "messages": upgrade(public_prompt(c))} for c in rows)
    files["train.sft.jsonl"] = jsonl({"id": c["id"], "messages": [*upgrade(public_prompt(c)),
        {"role": "assistant", "content": c["reference"]}]} for c in cases["train"])
    files["train.preferences.jsonl"] = jsonl({"id": c["id"], "family": c["family"],
        "prompt": upgrade(public_prompt(c)), "chosen": c["reference"], "rejected": c["rejected"],
        "rejected_code": score(c["rejected"], c)["code"], "human_reviewed": False,
        "origin": "synthetic_rule_mutation"} for c in cases["train"])
    samples, seen = [], set()
    for c in cases["train"]:
        if c["scenario"] in seen:
            continue
        seen.add(c["scenario"])
        samples.extend([f"[{c['family']}/{c['scenario']}] {c['id']}", c["instruction"],
                        "可见上下文：" + canonical(c["context"]), "正确输出：" + c["reference"],
                        "对比错误：" + c["rejected"], "错误代码：" + score(c["rejected"], c)["code"], ""])
    files["review-sample.txt"] = "\n".join(samples).encode("utf-8")
    files["README.txt"] = ("本包仅用于数据审核，不开 GPU、不执行训练，也不调用真实工具。\n"
        "320 条合成训练题（每题一个正例和一个负例）；80 条隔离检查题。\n"
        "共 21 种纠错场景；实体/话题变化不能当作 400 种独立任务。\n"
        "所有新样本 human_reviewed=false；旧规则认可不等于新样本已经人工审核。\n"
        "开发集已用于发现能力缺口，不能再称为盲测；旧盲测标签/预测未读取。\n"
        "probe 是同类能力的定向检查题，不是无偏泛化证明；不能加入训练。\n"
        "正例可以用于后续 SFT；偏好记录包含故意无效 JSON/字段错误及 ID 错误。\n"
        "不可直接替换旧 RPO 包：旧 loader 拒绝格式错误的 rejected，本轮未改 runner。\n"
        "下一步先审核 review-sample.txt，之后再决定与旧训练样本的混合及离线集成。\n"
        "不覆盖 v1、不采用未达标候选，不承诺训练必然提升。\n").encode("utf-8")
    runtime_names = ("correction_data.py", "cases.py", "contracts.py", "environment.py", "rewards.py",
                     "prompt_v3.py", "sft_refresh.py")
    manifest = {"version": VERSION, "purpose": "data_review_only", "prompt_version": PROMPT_VERSION,
                "human_reviewed": False, "training_integration_ready": False,
                "files_sha256": {name: sha(raw) for name, raw in files.items()},
                "protected_files_sha256": protected["hashes"],
                "generator_dependencies_sha256": {name: sha((Path(__file__).parent / name).read_bytes())
                                                  for name in runtime_names}}
    files["manifest.json"] = (canonical(manifest) + "\n").encode("utf-8")
    out.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        (out / name).write_bytes(raw)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as z:
        for name, raw in sorted(files.items()):
            z.writestr(name, raw)
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None or any(z.read(name) != raw for name, raw in files.items()):
            raise ValueError("archive_verification_failed")
    if any(sha((repo / name).read_bytes()) != digest for name, digest in protected["hashes"].items()):
        raise ValueError("protected_data_changed")
    return {"status": "prepared_for_review_not_training", "train": 320, "probe": 80,
            "review_scenarios": len(seen), "model_calls": 0, "real_tool_calls": 0,
            "archive": str(archive.resolve()), "archive_sha256": sha(archive.read_bytes())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(canonical(export(Path(__file__).resolve().parents[2], args.out)))
