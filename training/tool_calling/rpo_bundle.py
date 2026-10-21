"""CPU-only preference preparation. Oracle checks are not model evaluation."""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import zipfile

from contracts import canonical, parse_response, production_hashes, sha
import dataset
from grpo import read_rows
import grpo_once as once
import grpo_once_bundle as previous
from rewards import score
import rpo

CODE = ("rpo.py", "grpo_once.py", "grpo.py", "baseline.py", "contracts.py", "environment.py",
        "rewards.py", "prompt_v3.py", "requirements-grpo.txt", "run_rpo_once.sh", "CLOUD_RPO.txt")


def candidates(case, partner=None, alternative_query=None):
    """Context-grounded decision mistakes, not malformed JSON negatives."""
    gold = parse_response(case["reference"])
    if partner is not None:
        yield "opposite_context_answer", parse_response(partner["reference"])
    if gold["action"] == "call":
        tool, args = gold["tool"], gold["arguments"]
        if tool in ("read_paper", "resolve_paper"):
            key, field = (("paper_id", "paper_id") if tool == "read_paper" else ("identifier", "doi"))
            for paper in case["context"]["candidates"]:
                bad = deepcopy(gold)
                bad["arguments"][key] = paper[field]
                yield "wrong_candidate" if tool == "read_paper" else "wrong_doi_target", bad
        elif tool == "search_papers":
            for provider in case["context"]["allowed_providers"]:
                bad = deepcopy(gold)
                bad["arguments"]["providers"] = [provider]
                yield "wrong_provider_scope", bad
        elif tool == "search_library":
            for identity in case["context"]["allowed_document_ids"]:
                bad = deepcopy(gold)
                bad["arguments"]["document_ids"] = [identity]
                yield "wrong_document_scope", bad
        if tool in ("search_papers", "search_library"):
            bad = deepcopy(gold)
            key = "per_source" if tool == "search_papers" else "limit"
            bad["arguments"][key] = 1 if args[key] != 1 else 2
            if rpo.lowered_upper_bound(case, gold, bad):
                if not alternative_query or alternative_query == args["query"]:
                    raise ValueError("distinct_training_query_required")
                bad = deepcopy(gold)
                bad["arguments"]["query"] = alternative_query
                yield "wrong_query", bad
            else:
                yield "wrong_limit", bad
    else:
        for paper in case["context"]["candidates"]:
            yield "unnecessary_read", {"action": "call", "tool": "read_paper",
                                        "arguments": {"paper_id": paper["paper_id"]}}
        yield "unnecessary_search", {"action": "call", "tool": "search_papers",
            "arguments": {"query": case["topic"], "providers": ["openalex"], "per_source": 6}}
    for reason in ("ambiguous_paper", "permission_required", "missing_query", "missing_identifier"):
        yield "wrong_clarification", {"action": "clarify", "reason": reason}
    yield "premature_finish", {"action": "finish", "reason": "no_tool_needed"}


def preference_pairs(cases):
    # Rotate only training queries; never borrow a held-out topic or label.
    queries = sorted({a["arguments"]["query"] for c in cases
                      if (a := parse_response(c["reference"])).get("tool")
                      in ("search_papers", "search_library")})
    alternatives = {q: queries[(i + 1) % len(queries)] for i, q in enumerate(queries)}
    partners = {}
    for case in cases:
        if "pair_id" in case:
            partners.setdefault(case["pair_id"], []).append(case)
    result = []
    for case in sorted(cases, key=lambda c: c["id"]):
        if case["split"] != "train" or not score(case["reference"], case)["task_success"]:
            raise ValueError("training_reference_invalid")
        sides = partners.get(case.get("pair_id"), [])
        partner = next((c for c in sides if c["id"] != case["id"]), None)
        seen, selected_kinds, selected = set(), set(), []
        gold = parse_response(case["reference"])
        query = gold.get("arguments", {}).get("query")
        for kind, value in candidates(case, partner, alternatives.get(query)):
            raw = canonical(parse_response(canonical(value)))
            if raw in seen:
                continue
            seen.add(raw)
            scored = score(raw, case)
            if scored["task_success"] or kind in selected_kinds:
                continue
            selected.append((kind, raw, scored["code"]))
            selected_kinds.add(kind)
            if len(selected) == 2:
                break
        if len(selected) != 2:
            raise ValueError("two_distinct_preference_errors_required")
        for kind, rejected, code in selected:
            result.append({"id": "preference:" + sha(case["id"] + rejected)[:24],
                "case_id": case["id"], "family": case["family"], "chosen": case["reference"],
                "rejected": rejected, "negative_kind": kind, "rejected_code": code,
                "origin": "synthetic_rule_mutation", "human_reviewed": False})
    return result


def audit(cases, pairs, held):
    lookup = {c["id"]: c for c in cases}
    if len(cases) != 1200 or len(lookup) != 1200 or len(pairs) != 2400:
        raise ValueError("preference_coverage")
    if Counter(p["case_id"] for p in pairs) != Counter({i: 2 for i in lookup}):
        raise ValueError("two_pairs_per_prompt_required")
    if len({p["id"] for p in pairs}) != len(pairs):
        raise ValueError("duplicate_preference")
    for p in pairs:
        c = lookup[p["case_id"]]
        parse_response(p["rejected"])
        if rpo.lowered_upper_bound(c, parse_response(p["chosen"]), parse_response(p["rejected"])):
            raise ValueError("ambiguous_lowered_upper_bound")
        if (p["chosen"] != c["reference"] or p["family"] != c["family"]
                or not score(p["chosen"], c)["task_success"] or score(p["rejected"], c)["task_success"]):
            raise ValueError("invalid_preference_direction")
    for getter in (lambda c: c["id"], lambda c: c["topic"], lambda c: c["family"],
                   lambda c: canonical(once.public_rows([c])[0]["prompt"])):
        if {getter(c) for c in cases} & {getter(c) for c in held}:
            raise ValueError("training_heldout_overlap")
    def entities(rows):
        return {p[k] for c in rows for p in c["world"]["papers"] for k in ("doi", "paper_id")} | {
            d["document_id"] for c in rows for d in c["world"]["documents"]}
    if entities(cases) & entities(held):
        raise ValueError("training_heldout_entity_overlap")
    return {"unique_train_prompts": 1200, "preference_pairs": 2400,
        "train_families": dict(sorted(Counter(c["family"] for c in cases).items())),
        "negative_kinds": dict(sorted(Counter(p["negative_kind"] for p in pairs).items())),
        "chosen_checks": 2400, "rejected_checks": 2400, "identity_overlap": 0,
        "real_trajectories": 0, "human_reviewed": False, "llm_calls": 0, "real_tool_calls": 0,
        "scope": "single_decision_only_not_retrieval_quality_or_writing",
        "limitation": "rule-generated labels; semantic leakage and oracle bias still require review"}


def correction_audit(cases, old_pairs, pairs):
    """Prove the approved edit changes only the 200 ambiguous negatives."""
    lookup = {c["id"]: c for c in cases}
    if len(old_pairs) != 2400 or len(pairs) != 2400:
        raise ValueError("correction_coverage_changed")
    changed = []
    for old, new in zip(old_pairs, pairs):
        case = lookup[old["case_id"]]
        chosen, rejected = parse_response(old["chosen"]), parse_response(old["rejected"])
        if not rpo.lowered_upper_bound(case, chosen, rejected):
            if old != new:
                raise ValueError("unapproved_preference_change")
            continue
        proposed = parse_response(new["rejected"])
        expected = deepcopy(chosen)
        expected["arguments"]["query"] = proposed["arguments"]["query"]
        if (proposed != expected or proposed["arguments"]["query"] == chosen["arguments"]["query"]
                or new["negative_kind"] != "wrong_query"
                or score(new["rejected"], case)["task_success"]
                or any(old[k] != new[k] for k in old
                       if k not in ("id", "rejected", "negative_kind", "rejected_code"))):
            raise ValueError("correction_must_change_only_query")
        changed.append({"case_id": case["id"], "family": case["family"],
                        "old_pair_id": old["id"], "new_pair_id": new["id"]})
    if len(changed) != 200:
        raise ValueError("expected_exactly_200_corrections")
    return {"corrected_pairs": 200, "unchanged_pairs": 2200,
            "by_family": dict(sorted(Counter(c["family"] for c in changed).items())),
            "changes": changed}


def review_packet(cases, pairs, approval=None):
    sample = []
    for family in sorted({c["family"] for c in cases}):
        bucket = sorted((c for c in cases if c["family"] == family), key=lambda c: c["id"])
        first = bucket[0]
        sample.extend([c for c in bucket if c.get("pair_id") == first["pair_id"]]
                      if "pair_id" in first else [first])
    lines = ["待人工审核：35个训练任务 / 70个偏好对；不含开发或盲测答案。",
             "全部为合成记录。请确认需求、授权范围、正确动作及错误动作的错误原因。",
             "审核通过前不要租GPU；审核不是运行单元测试能够代替的。", ""]
    if approval:
        lines = ["规则级抽样审核已确认：35个训练任务 / 70个偏好对；不含开发或盲测答案。",
                 "用户批准修正5个样本负例对应的200对歧义，认可其余65对所展示规则。",
                 "200对按授权确定性替换为错误查询；不是200对或全量2400对逐条人工审核。",
                 "数据仍为合成记录；本次批准不代表GPU执行授权，也不证明训练收益。", ""]
    for c in sample:
        lines += [c["id"] + " / " + c["family"], c["instruction"], canonical(c["context"]),
                  "chosen: " + c["reference"]]
        for p in pairs:
            if p["case_id"] == c["id"]:
                lines += ["rejected (" + p["negative_kind"] + "): " + p["rejected"],
                          "offline rejection: " + p["rejected_code"]]
        if approval:
            corrected = any(p["negative_kind"] == "wrong_query" for p in pairs if p["case_id"] == c["id"])
            lines += ["审核记录：已批准按规则修正歧义负例；新负例非逐条人工复核" if corrected
                      else "审核记录：用户认可本样本展示的规则", ""]
        else:
            lines += ["人工结论：待审核", ""]
    return "\n".join(lines) + "\n"


def build(dataset_dir, previous_bundle, out, archive, reviewed_bundle=None, approve_sample_rules=False):
    if out.exists() or archive.exists():
        raise ValueError("new_output_paths_required")
    original = dataset.load_verified(dataset_dir)
    previous_manifest, _ = once.load_bundle(previous_bundle)
    cases = read_rows(previous_bundle / "train.oracle.jsonl")
    if cases[:1000] != original["train"]:
        raise ValueError("original_train_changed")
    previous.audit_pairs(cases[1000:], original)
    for split in ("validation", "blind_test"):
        if read_rows(previous_bundle / "evaluation" / (split + ".jsonl")) != original[split]:
            raise ValueError("evaluation_changed")
    pairs = preference_pairs(cases)
    report = audit(cases, pairs, original["validation"] + original["blind_test"])
    source = Path(__file__).parent
    entries = {n: (source / n).read_bytes() for n in CODE}
    for name in ("train.oracle.jsonl", "evaluation/validation.jsonl", "evaluation/blind_test.jsonl",
                 "v1-validation.predictions.jsonl", "v1-adapter/adapter_config.json",
                 "v1-adapter/adapter_model.safetensors"):
        entries[name] = (previous_bundle / name).read_bytes()
    entries["preferences.jsonl"] = "".join(canonical(p) + "\n" for p in pairs).encode("utf-8")
    approval = None
    if (reviewed_bundle is None) != (not approve_sample_rules):
        raise ValueError("review_source_and_explicit_approval_required_together")
    if reviewed_bundle is not None:
        old = json.loads((reviewed_bundle / "rpo-bundle.json").read_text(encoding="utf-8"))
        if old["plan"] != rpo.PLAN:
            raise ValueError("approved_plan_changed")
        for name, digest in old["files"].items():
            if once.file_sha(reviewed_bundle / name) != digest:
                raise ValueError("reviewed_bundle_changed")
        old_sha = once.file_sha(reviewed_bundle / "preferences.jsonl")
        if old_sha != "ff7b444129d9e7e8e14601677b80574f12658883d862f4eeac6cc9570cc46aca":
            raise ValueError("reviewed_preferences_changed")
        for name in rpo.ARTIFACTS - {"preferences.jsonl", "data-audit.json", "review-sample.txt"}:
            if entries[name] != (reviewed_bundle / name).read_bytes():
                raise ValueError("protected_artifact_changed")
        report["correction_audit"] = correction_audit(cases, read_rows(reviewed_bundle / "preferences.jsonl"), pairs)
        approval = {"status": "approved_with_deterministic_corrections", "date": "2026-10-06",
            "authority": "direct_user_instruction", "instruction": "按建议修正，其余规则认可",
            "scope": "sampled_rules_and_correction_recipe_not_individual_label_certification",
            "sample_tasks": 35, "sample_pairs": 70, "sample_accepted_pairs": 65,
            "sample_corrected_pairs": 5, "corrected_pairs": 200, "unchanged_pairs": 2200,
            "all_examples_individually_reviewed": False,
            "reviewed_preferences_sha256": old_sha,
            "corrected_preferences_sha256": sha(entries["preferences.jsonl"])}
        report["sample_rule_review"] = approval
    entries["review-sample.txt"] = review_packet(cases, pairs, approval).encode("utf-8")
    entries["data-audit.json"] = (canonical(report) + "\n").encode("utf-8")
    manifest = {"schema": rpo.SCHEMA, "plan": rpo.PLAN, "human_reviewed": False,
        "counts": {"train_prompts": 1200, "preference_pairs": 2400, "validation": 200, "blind_test": 200},
        "preferences_sha256": sha(entries["preferences.jsonl"]), "production_sha256": production_hashes(),
        "source_bundle_sha256": once.file_sha(previous_bundle / "once-bundle.json"),
        "data_origin": "synthetic_rule_mutations_not_real_model_errors",
        "evaluation_policy": "dev_informed_design_blind_predictions_only_after_dev_gate",
        "sample_rule_review": approval,
        **{k: previous_manifest[k] for k in ("versions", "model_files_sha256", "gpu", "v1_adapter_sha256", "v1_validation")},
        "files": {n: sha(b) for n, b in sorted(entries.items())}}
    entries["rpo-bundle.json"] = (canonical(manifest) + "\n").encode("utf-8")
    out.mkdir(parents=True, exist_ok=False)
    for name, value in entries.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    rpo.load_bundle(out)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as z:
        for name, value in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, value)
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError("archive_crc_failed")
    return {"status": "prepared_pending_cuda_validation" if approval else "prepared_pending_human_review_and_cuda_validation",
            **manifest["counts"], "sample_rule_review": approval,
            "preferences_sha256": manifest["preferences_sha256"], "zip": str(archive.resolve()),
            "zip_sha256": once.file_sha(archive), "gpu_loaded": False, "real_tool_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "previous-bundle", "out", "zip"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--reviewed-bundle", type=Path)
    parser.add_argument("--approve-sample-rules", action="store_true")
    args = parser.parse_args()
    print(canonical(build(args.dataset, args.previous_bundle, args.out, args.zip,
                          args.reviewed_bundle, args.approve_sample_rules)))
