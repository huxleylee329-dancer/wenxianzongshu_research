"""Build/review/score the 50-task offline pilot. Never runs a model or tool API."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from cases import build_cases, public_prompt
from contracts import canonical, parse_response, production_hashes, sha, tools_schema
from rewards import score


ARTIFACTS = ("prompts.jsonl", "oracle.jsonl", "review.md", "tools.json", "self-check.json")


def mutations(case):
    """Wrong implementations must not earn a success reward. Not extra samples."""
    gold = json.loads(case["reference"])
    extra = deepcopy(gold)
    extra["override_permissions"] = True
    yield "extra_fields", canonical(extra)
    yield "unknown_tool", canonical({"action": "call", "tool": "shell", "arguments": {}})
    yield "two_actions", canonical([gold, gold])
    if gold["action"] != "call":
        yield "wrong_decision", canonical({"action": "finish" if gold["action"] == "clarify" else "clarify",
                                           "reason": "no_tool_needed" if gold["action"] == "clarify" else "missing_query"})
        return
    yield "always_finish", canonical({"action": "finish", "reason": "no_tool_needed"})
    args = gold["arguments"]
    tool = gold["tool"]
    if tool in ("search_papers", "search_library"):
        wrong = deepcopy(gold)
        wrong["arguments"]["query"] = "unrelated synthetic subject"
        yield "wrong_query", canonical(wrong)
        key = "per_source" if tool == "search_papers" else "limit"
        wrong = deepcopy(gold)
        wrong["arguments"][key] = 1 if args[key] != 1 else 2
        yield "wrong_valid_limit", canonical(wrong)
        wrong["arguments"][key] = True
        yield "bool_is_not_limit", canonical(wrong)
    elif tool == "resolve_paper":
        wrong = deepcopy(gold)
        wrong["arguments"]["identifier"] = "10.99999/missing"
        yield "wrong_doi", canonical(wrong)
    else:
        wrong = deepcopy(gold)
        wrong["arguments"]["paper_id"] = "paper:unseen"
        yield "invented_paper", canonical(wrong)
        wrong["arguments"]["paper_id"] = next(p["paper_id"] for p in case["context"]["candidates"]
                                                if p["paper_id"] != args["paper_id"])
        yield "wrong_known_paper", canonical(wrong)


def self_check(cases):
    ids, prompts = set(), set()
    categories, actions = Counter(), Counter()
    rejected = Counter()
    for case in cases:
        prompt = canonical(public_prompt(case))
        if case["id"] in ids or prompt in prompts or not case["synthetic"]:
            raise ValueError("invalid_pilot_identity")
        ids.add(case["id"])
        prompts.add(prompt)
        result = score(case["reference"], case)
        if not result["task_success"]:
            raise ValueError("reference_not_executable")
        categories[case["category"]] += 1
        action = parse_response(case["reference"])["action"]
        actions[action] += 1
        if result["mock_calls"] != int(action == "call"):
            raise ValueError("wrong_reference_call_count")
        for name, bad in mutations(case):
            if score(bad, case)["task_success"]:
                raise ValueError("mutation_survived")
            rejected[name] += 1
    return {"examples": len(cases), "categories": dict(categories), "actions": dict(actions),
            "reference_successes": len(ids), "negative_checks": sum(rejected.values()),
            "negative_checks_by_kind": dict(rejected), "real_tool_calls": 0,
            "llm_calls": 0, "trained": False, "model_accuracy": None,
            "note": "reference/negative checks only, NOT model performance or held-out evaluation"}


def review_text(cases):
    lines = ["# 工具调用试验包：50条待人工审核任务", "",
             "全部论文/DOI/文档均为离线虚构记录；不是论文事实或真实执行记录。",
             "本文件含答案，仅用于审核，不可拼入模型prompt。50条共享模拟环境，禁止随机拆分为泛化评估集。", ""]
    for case in cases:
        lines += [f"## {case['id']} — {case['category']}", "", case["instruction"], "",
                  "```json", canonical(case["context"]), "```", "",
                  "预期动作（允许等价JSON、默认数量、数组顺序、DOI规范化及query大小写/空白）：", "",
                  "```json", case["reference"], "```", "",
                  "预期模拟结果ID：" + canonical(case["goal_ids"]), "",
                  "人工审核：尚未完成。", ""]
    return "\n".join(lines)


def build(directory):
    cases = build_cases()
    report = self_check(cases)
    directory.mkdir(parents=True, exist_ok=False)
    content = {
        "prompts.jsonl": "".join(canonical({"id": c["id"], "prompt": public_prompt(c)}) + "\n" for c in cases),
        "oracle.jsonl": "".join(canonical(c) + "\n" for c in cases),
        "review.md": review_text(cases), "tools.json": canonical(tools_schema()) + "\n",
        "self-check.json": canonical(report) + "\n",
    }
    for name, value in content.items():
        with (directory / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
    manifest = {"schema": "academic-tool-pilot-v1", "count": 50,
                "status": "synthetic_calibration_pending_human_review",
                "training_started": False, "production_sha256": production_hashes(),
                "files": {name: sha((directory / name).read_bytes()) for name in ARTIFACTS}}
    (directory / "manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
    return report


def load_verified(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != "academic-tool-pilot-v1" or manifest.get("count") != 50
            or manifest.get("production_sha256") != production_hashes()
            or set(manifest.get("files", {})) != set(ARTIFACTS)):
        raise ValueError("production_or_manifest_changed")
    for name, expected in manifest["files"].items():
        if sha((directory / name).read_bytes()) != expected:
            raise ValueError("dataset_changed")
    cases = [json.loads(line) for line in (directory / "oracle.jsonl").read_text(encoding="utf-8").splitlines()]
    public = [json.loads(line) for line in (directory / "prompts.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(cases) != 50 or public != [{"id": c["id"], "prompt": public_prompt(c)} for c in cases]:
        raise ValueError("public_private_alignment")
    self_check(cases)
    return cases


def evaluate(directory, predictions):
    """Require all 50 predictions; never hide missing/duplicate rows in accuracy."""
    cases = load_verified(directory)
    rows = [json.loads(line) for line in predictions.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != len(cases) or any(type(r) is not dict or set(r) != {"id", "response"}
                                    or type(r["id"]) is not str or type(r["response"]) is not str for r in rows):
        raise ValueError("prediction_shape")
    answers = {row["id"]: row["response"] for row in rows}
    if len(answers) != len(rows) or set(answers) != {c["id"] for c in cases}:
        raise ValueError("prediction_coverage")
    outcomes = [{"id": c["id"], **score(answers[c["id"]], c)} for c in cases]
    return {"examples": len(cases), "task_successes": sum(o["task_success"] for o in outcomes),
            "mean_reward": sum(o["reward"] for o in outcomes) / len(outcomes),
            "mock_calls": sum(o["mock_calls"] for o in outcomes), "real_tool_calls": 0,
            "evaluation_scope": "seen calibration tasks, not held-out generalization",
            "codes": dict(Counter(o["code"] for o in outcomes)), "results": outcomes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build").add_argument("--out", type=Path, required=True)
    sub.add_parser("check").add_argument("--data", type=Path, required=True)
    evaluate_parser = sub.add_parser("score")
    evaluate_parser.add_argument("--data", type=Path, required=True)
    evaluate_parser.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.out)
    elif args.command == "check":
        result = self_check(load_verified(args.data))
    else:
        result = evaluate(args.data, args.predictions)
    print(canonical(result))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError):
        # Do not echo raw completions, paths, or arbitrary parser messages.
        print(canonical({"status": "blocked", "detail": "invalid_input_or_changed_contract"}))
        raise SystemExit(2) from None
