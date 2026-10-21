"""Offline data preparation. Never discover private files or upload anything."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from rewards import score
from runtime import (
    HERE, SOURCE_DIR, SOURCE_NAMES, approved_view, canonical, digest,
    production, source_hashes, writer_prompt,
)


def read_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if not rows or any(type(row) is not dict for row in rows):
        raise ValueError("dataset must contain JSON objects")
    return rows


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical(row) + "\n")


def document_keys(sources: list[dict], evidence: dict) -> list[str]:
    # Local source IDs are recycled across workflows, so they cannot identify
    # a paper. URLs and exact evidence hashes prevent common split leakage.
    keys = {"url:" + digest(source["url"]) for source in sources}
    keys.update("block:" + digest(block) for blocks in evidence.values() for block in blocks)
    return sorted(keys)


def make_row(state, section_id: str, group: str, *, synthetic: bool = False) -> dict:
    prompt, ids = writer_prompt(state, section_id)
    evidence = {item.source_id: list(item.evidence_blocks)
                for item in state.research_evidence.provenance
                if item.source_id in ids}
    sources = json.loads(prompt[1]["content"])["evidence_sources"]
    return {
        "id": digest(canonical(prompt))[:24], "group": group,
        "synthetic": synthetic, "annotation_approved": False, "cloud_ok": False,
        "production_sha256": source_hashes(), "prompt": prompt,
        "allowed_source_ids": ids, "evidence": evidence,
        "document_keys": document_keys(sources, evidence), "rubric": {"units": []},
    }


def validate_row(row: dict, *, for_training: bool = False) -> None:
    expected = {"id", "group", "synthetic", "annotation_approved", "cloud_ok",
                "production_sha256", "prompt", "allowed_source_ids", "evidence",
                "document_keys", "rubric"}
    if set(row) != expected or row["production_sha256"] != source_hashes():
        raise ValueError("dataset fields or pinned production bytes differ")
    for name in ("synthetic", "annotation_approved", "cloud_ok"):
        if type(row[name]) is not bool:
            raise ValueError("approval flags must be exact booleans")
    if type(row["group"]) is not str or not row["group"].strip():
        raise ValueError("paper/topic group required")
    prompt = row["prompt"]
    writer = production("section_writer")
    if (type(prompt) is not list or len(prompt) != 2
            or any(type(m) is not dict or set(m) != {"role", "content"} for m in prompt)
            or prompt[0] != {"role": "system", "content": writer._SYSTEM_MESSAGE}
            or prompt[1]["role"] != "user" or type(prompt[1]["content"]) is not str):
        raise ValueError("exact production system/user prompt required")
    message = prompt[1]["content"]
    data = json.loads(message)
    if canonical(data) != message or len(message) > writer._USER_MESSAGE_MAX_CHARS:
        raise ValueError("user message is not canonical or exceeds production cap")
    if row["id"] != digest(canonical(prompt))[:24]:
        raise ValueError("prompt identity changed")
    ids = row["allowed_source_ids"]
    if (type(ids) is not list or not 1 <= len(ids) <= 64
            or any(type(s) is not str for s in ids) or len(set(ids)) != len(ids)):
        raise ValueError("allowed source IDs invalid")
    sources = data["evidence_sources"]
    if [s["source_id"] for s in sources] != ids or any(
        s["citation_marker"] != "[[cite:" + s["source_id"] + "]]" for s in sources
    ):
        raise ValueError("production marker plan mismatch")
    evidence = row["evidence"]
    if type(evidence) is not dict or any(sid not in ids for sid in evidence):
        raise ValueError("evidence source binding invalid")
    if any(type(blocks) is not list or not blocks
           or any(type(b) is not str or not b.strip() for b in blocks)
           for blocks in evidence.values()):
        raise ValueError("evidence blocks invalid")
    if row["document_keys"] != document_keys(sources, evidence):
        raise ValueError("paper split identity differs")
    if type(row["rubric"]) is not dict or set(row["rubric"]) != {"units"}:
        raise ValueError("rubric fields invalid")
    units = row["rubric"]["units"]
    if type(units) is not list or len(units) > 12:
        raise ValueError("short-section rubric invalid")
    claims, phrases = set(), set()
    for unit in units:
        if set(unit) != {"claim_id", "variants", "source_ids", "evidence_quotes"}:
            raise ValueError("rubric unit fields invalid")
        if type(unit["claim_id"]) is not str or not unit["claim_id"] or unit["claim_id"] in claims:
            raise ValueError("rubric claim identity invalid")
        claims.add(unit["claim_id"])
        variants, cited = unit["variants"], unit["source_ids"]
        if (type(variants) is not list or not 1 <= len(variants) <= 8
                or any(type(v) is not str or not v.strip() or v != v.strip()
                       or len(v) > 1024 for v in variants)
                or type(cited) is not list or not cited
                or any(type(s) is not str or s not in ids for s in cited)
                or len(set(cited)) != len(cited)):
            raise ValueError("rubric text/attribution invalid")
        if any(v in phrases for v in variants) or len(set(variants)) != len(variants):
            raise ValueError("ambiguous rubric variants")
        phrases.update(variants)
        quotes = unit["evidence_quotes"]
        if type(quotes) is not list or not quotes:
            raise ValueError("evidence anchors required")
        anchored = set()
        for anchor in quotes:
            if set(anchor) != {"source_id", "block_index", "quote"}:
                raise ValueError("evidence anchor fields invalid")
            sid, index, quote = anchor["source_id"], anchor["block_index"], anchor["quote"]
            if (type(sid) is not str or sid not in cited or sid not in evidence
                    or type(index) is not int or not 0 <= index < len(evidence[sid])
                    or type(quote) is not str or len(quote.strip()) < 8
                    or quote not in evidence[sid][index]
                    or not any(quote in block for block in data["context_blocks"])):
                raise ValueError("anchor absent from original AND writer-visible evidence")
            anchored.add(sid)
        if anchored != set(cited):
            raise ValueError("every attributed source needs an evidence anchor")
    if units:
        for unit in units:
            for variant in unit["variants"]:
                raw = canonical({"content": variant + "".join(
                    "[[cite:" + sid + "]]" for sid in unit["source_ids"])})
                if score(raw, ids, row["rubric"])["reward"] <= 0:
                    raise ValueError("approved variant fails actual production parser/reward")
    if for_training and (row["synthetic"] or not row["annotation_approved"]
                         or not row["cloud_ok"] or not units):
        raise ValueError("real data, human annotation and cloud permission required")


def split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep connected paper/evidence/topic groups together, never split sections."""
    seen_ids = set()
    components: list[tuple[set, list]] = []
    for row in rows:
        validate_row(row, for_training=True)
        if row["id"] in seen_ids:
            raise ValueError("duplicate training prompt")
        seen_ids.add(row["id"])
        keys = set(row["document_keys"]) | {"group:" + row["group"]}
        members = [row]
        remaining = []
        for previous_keys, previous in components:
            if keys & previous_keys:
                keys |= previous_keys
                members += previous
            else:
                remaining.append((previous_keys, previous))
        remaining.append((keys, members))
        components = remaining
    if len(components) < 2:
        raise ValueError("at least two disjoint paper/topic groups required")
    components.sort(key=lambda c: digest(canonical(sorted(c[0]))))
    count = max(1, round(len(components) * 0.25))
    evaluate = [r for _, group in components[:count] for r in group]
    train = [r for _, group in components[count:] for r in group]
    return train, evaluate


def demo_rows() -> list[dict]:
    """Invented facts for SOFTWARE checks only. Never eligible for real training."""
    s = production("state")
    profile = production("report_profiles")._get_report_profile("stem_literature_review")
    result = []
    for group in range(3):
        query = f"合成设备{group}的实验记录（非真实论文）"
        fact = f"合成实验{group}中，设备仅在25摄氏度下测试了100小时，未测试其他温度。"
        request = s.AcademicWorkflowRequest(
            workflow_mode="academic_langgraph", workflow_id="demo", thread_id="demo",
            run_id="demo", query=query, report_type="research_report", report_source="local",
            tone="Objective", language="zh-CN", source_urls=(), document_urls=(),
            query_domains=(), max_search_results=1, report_mode="stem_literature_review",
            report_locale="zh-CN")
        source = s.WorkflowEvidenceSource(source_id="evidence-source:000001", order=1,
                                         title=query, url=f"https://example.test/demo-{group}", candidate_id=None)
        plan = s.WorkflowTopicPlan(topic_plan_id="topic-plan:000001", workflow_id="demo",
                                  run_id="demo", attempt=1, research_topic=query,
                                  research_questions=("实验条件和结论边界是什么？",))
        evidence = s.WorkflowResearchEvidence(
            evidence_id="evidence:000001", topic_plan_id=plan.topic_plan_id, attempt=1,
            context_blocks=(fact,), sources=(source,),
            provenance=(s.WorkflowEvidenceProvenance(source_id=source.source_id, evidence_blocks=(fact,)),))
        outline = s.WorkflowOutline(
            outline_id="outline:000001", evidence_id=evidence.evidence_id, attempt=1,
            title=query, report_mode=request.report_mode, report_locale=request.report_locale,
            sections=tuple(s.WorkflowOutlineSection(
                section_id=f"section:{i:06d}", order=i, section_role=role, title=title,
                brief="只依据给定实验记录，简短说明条件与局限，不增加实验外结论。")
                for i, (role, title) in enumerate(profile, 1)))
        decision = s.WorkflowOutlineDecisionRecord(
            decision_id="outline-decision:000001", schema_version="1", workflow_id="demo",
            thread_id="demo", run_id="demo", outline_id=outline.outline_id,
            outline_digest=s._outline_digest(outline), decision="approve",
            actor_assertion="synthetic-software-fixture", attempt=1)
        event_plan = [(kind, node) for node in ("topic_planner", "research_evidence",
                                               "outline_writer", "outline_approval")
                      for kind in ("node_started", "node_completed")]
        event_plan.append(("workflow_completed", None))
        state = s.AcademicWorkflowState(
            schema_version="1", workflow_id="demo", thread_id="demo", run_id="demo",
            phase="outline_approved", status="completed", request=request, topic_plan=plan,
            research_evidence=evidence, outline=outline, outline_decision=decision, errors=(),
            events=tuple(s.WorkflowEvent(event_id=f"event:{i:06d}", order=i,
                                        event_type=kind, node_id=node, attempt=1)
                         for i, (kind, node) in enumerate(event_plan, 1)))
        for section_id in ("section:000004", "section:000006"):
            row = make_row(state, section_id, f"synthetic-{group}", synthetic=True)
            row["rubric"]["units"] = [{
                "claim_id": "test-conditions", "variants": [fact],
                "source_ids": [source.source_id], "evidence_quotes": [{
                    "source_id": source.source_id, "block_index": 0, "quote": fact}],
            }]
            result.append(row)
    return result


def build_bundle(data_dir: Path | None, output: Path) -> None:
    if data_dir is not None:
        train, evaluate = (read_rows(data_dir / name) for name in ("train.jsonl", "eval.jsonl"))
        for row in train + evaluate:
            validate_row(row, for_training=True)
        check_split(train, evaluate)
    # Explicit allowlist: no .env, full repo, checkpoints, logs or local library.
    files = {name: HERE / name for name in (
        "runtime.py", "prepare.py", "rewards.py", "run.py", "README.md",
        "requirements-cloud.txt", "test_offline.py")}
    files.update({"production/" + name: SOURCE_DIR / name for name in SOURCE_NAMES})
    if data_dir is not None:
        files.update({name: data_dir / name for name in ("train.jsonl", "eval.jsonl")})
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            archive.write(path, name)
        if data_dir is None:
            archive.writestr("demo.jsonl", "".join(canonical(row) + "\n" for row in demo_rows()))
        archive.writestr("manifest.json", canonical({
            "files": {name: digest(path.read_bytes()) for name, path in files.items()},
            "production_sha256": source_hashes(), "model": "Qwen/Qwen3-8B",
            "real_training_data_included": data_dir is not None,
        }))


def check_split(train: list[dict], evaluate: list[dict]) -> None:
    if not train or not evaluate:
        raise ValueError("both splits required")
    ids = [r["id"] for r in train + evaluate]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate prompt across splits")
    a = {k for r in train for k in r["document_keys"] + ["group:" + r["group"]]}
    b = {k for r in evaluate for k in r["document_keys"] + ["group:" + r["group"]]}
    if a & b:
        raise ValueError("paper/topic/evidence leakage across splits")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("--out", required=True, type=Path)
    export = commands.add_parser("export")
    export.add_argument("--state", required=True, type=Path)
    export.add_argument("--group", required=True)
    export.add_argument("--out", required=True, type=Path)
    check = commands.add_parser("check")
    check.add_argument("--data", required=True, type=Path)
    split = commands.add_parser("split")
    split.add_argument("--data", required=True, type=Path)
    split.add_argument("--out", required=True, type=Path)
    bundle = commands.add_parser("bundle")
    bundle.add_argument("--data-dir", type=Path, help="omit for code + SYNTHETIC demos only")
    bundle.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "demo":
        rows = demo_rows()
        for row in rows:
            validate_row(row)
        write_rows(args.out, rows)
        print(canonical({"synthetic_examples": len(rows), "training_eligible": 0}))
    elif args.action == "export":
        state = approved_view(json.loads(args.state.read_text(encoding="utf-8-sig")))
        rows = [make_row(state, section.section_id, args.group) for section in state.outline.sections]
        for row in rows:
            validate_row(row)
        write_rows(args.out, rows)
        print(canonical({"exported_examples": len(rows), "training_eligible": 0,
                         "requires": "evidence annotation AND cloud permission"}))
    elif args.action == "check":
        rows = read_rows(args.data)
        eligible = 0
        for row in rows:
            validate_row(row)
            try:
                validate_row(row, for_training=True)
                eligible += 1
            except ValueError:
                pass
        print(canonical({"examples": len(rows), "training_eligible": eligible}))
    elif args.action == "split":
        train, evaluate = split_rows(read_rows(args.data))
        check_split(train, evaluate)
        args.out.mkdir(parents=True, exist_ok=False)
        write_rows(args.out / "train.jsonl", train)
        write_rows(args.out / "eval.jsonl", evaluate)
        print(canonical({"train": len(train), "eval": len(evaluate)}))
    else:
        build_bundle(args.data_dir, args.out)
        print(canonical({"archive_sha256": digest(args.out.read_bytes()), "uploaded": False}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError) as exc:
        # Dataset/parser errors can contain evidence. No exception text/trace.
        print(canonical({"status": "blocked", "error_type": type(exc).__name__}))
        raise SystemExit(2) from None
