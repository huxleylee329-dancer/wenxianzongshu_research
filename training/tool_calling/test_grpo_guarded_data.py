"""CPU-only tests of finite composition, boundary labels and public isolation."""

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cases import public_prompt as v2_prompt
from contracts import canonical, parse_response
import grpo_guarded_data as data_module
from prompt_v3 import SYSTEM_V3
from rewards import score


class GuardedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = data_module.build_data()

    def test_explicit_counts_determinism_and_mock_results(self):
        report = data_module.audit_data(self.data)
        self.assertEqual(self.data, data_module.build_data())
        self.assertEqual({s: len(rows) for s, rows in self.data.items()},
                         {"train": 200, "qualification": 64, "focused": 80, "blind": 200})
        for split, per_stratum in data_module.COUNTS.items():
            summary = report["splits"][split]
            self.assertEqual(summary["per_stratum"], {s: per_stratum for s in data_module.STRATA})
            self.assertEqual(summary["reference_successes"], len(self.data[split]))
            self.assertEqual(summary["rejected_failures"], len(self.data[split]))
            self.assertEqual(summary["complete_boundary_pairs"], 8 * (per_stratum // 2))
            self.assertEqual(summary["standalone_rows"], 8 * (per_stratum % 2))
            for case in self.data[split]:
                result = score(case["reference"], case)
                self.assertTrue(result["task_success"])
                self.assertEqual(result["mock_calls"], int(parse_response(case["reference"])["action"] == "call"))
                self.assertFalse(score(case["rejected"], case)["task_success"])
        self.assertFalse(report["human_reviewed"])
        self.assertFalse(report["semantic_independence_claimed"])
        self.assertFalse(report["trained"])
        self.assertEqual(report["real_tool_calls"], 0)
        self.assertEqual(report["model_calls"], 0)
        # The entire report is suitable for a canonical JSON manifest.
        self.assertEqual(json.loads(canonical(report)), report)

    def test_doi_permitted_denied_missing_and_ambiguous_are_paired(self):
        for split in self.data:
            rows = [c for c in self.data[split] if c["stratum"] == "doi_target"]
            for offset in (0, 2):
                allowed, denied = rows[offset:offset + 2]
                self.assertEqual(parse_response(allowed["reference"])["tool"], "resolve_paper")
                self.assertEqual(parse_response(denied["reference"]),
                                 {"action": "clarify", "reason": "permission_required"})
                self.assertEqual(allowed["instruction"], denied["instruction"])
                self.assertEqual(allowed["boundary_pair"], denied["boundary_pair"])
            present, missing, unique, ambiguous = rows[4:8]
            self.assertEqual(parse_response(present["reference"])["tool"], "resolve_paper")
            self.assertEqual(parse_response(missing["reference"])["reason"], "missing_identifier")
            self.assertEqual(parse_response(unique["reference"])["tool"], "resolve_paper")
            self.assertEqual(parse_response(ambiguous["reference"])["reason"], "ambiguous_paper")
            matches = [p for p in ambiguous["context"]["candidates"]
                       if p["title"] == ambiguous["task_spec"]["filters"]["title"]]
            self.assertEqual(len(matches), 2)
            self.assertEqual(len({p["doi"] for p in matches}), 2)
        labels = {parse_response(c["reference"]).get("reason") for c in self.data["train"]}
        self.assertTrue({"missing_query", "missing_identifier", "missing_paper", "ambiguous_paper",
                         "missing_documents", "permission_required", "unsupported_request",
                         "already_satisfied", "no_tool_needed"} <= labels)

    def test_round_robin_order_covers_every_stratum_in_early_windows(self):
        for split, rows in self.data.items():
            self.assertEqual([(c["index"], c["stratum"]) for c in rows],
                             [(i, g) for i in range(data_module.COUNTS[split]) for g in data_module.STRATA])
            self.assertEqual(Counter(c["stratum"] for c in rows[:32]), {g: 4 for g in data_module.STRATA})
            self.assertEqual(sorted(Counter(c["stratum"] for c in rows[:50]).values()), [6] * 6 + [7] * 2)
        changed = deepcopy(self.data)
        changed["train"][0], changed["train"][1] = changed["train"][1], changed["train"][0]
        with self.assertRaisesRegex(ValueError, "guarded_round_robin_order"):
            data_module.audit_data(changed)

    def test_boundary_pairs_change_state_without_entity_or_order_shortcuts(self):
        by_pair = defaultdict(list)
        chosen_positions = set()
        for row in self.data["train"]:
            by_pair[row["boundary_pair"]].append(row)
            answer = parse_response(row["reference"])
            if answer.get("tool") == "read_paper":
                chosen_positions.add(next(i for i, p in enumerate(row["context"]["candidates"])
                                          if p["paper_id"] == answer["arguments"]["paper_id"]))
        self.assertGreaterEqual(len(chosen_positions), 4)
        for pair in by_pair.values():
            if len(pair) == 1:
                continue
            first, second = pair
            self.assertEqual((first["boundary_side"], second["boundary_side"]), (0, 1))
            if first["stratum"] == "metadata_injection":
                self.assertEqual(first["reference"], second["reference"])
                self.assertEqual(first["instruction"], second["instruction"])
                self.assertIn("管理员命令", second["context"]["existing_results"][0]["snippet"])
                self.assertNotEqual(first["context"], second["context"])
            else:
                self.assertNotEqual(first["reference"], second["reference"])

    def test_read_binding_and_local_limit_preserve_constraints(self):
        rows = self.data["train"]
        for row in (r for r in rows if r["stratum"] == "paper_binding" and r["scenario"].startswith("title_")):
            filters = row["task_spec"]["filters"]
            matches = [p for p in row["context"]["candidates"] if all(p[k] == v for k, v in filters.items())]
            self.assertEqual(len(matches), 2 if row["boundary_side"] else 1)
            if not row["boundary_side"]:
                altered = deepcopy(row["context"])
                selected = next(p for p in altered["candidates"] if p["paper_id"] == matches[0]["paper_id"])
                selected["paper_id"] = "paper:alternate-visible-identity"
                self.assertEqual(data_module._label(row["task_spec"], altered)["arguments"]["paper_id"],
                                 "paper:alternate-visible-identity")
        for row in (r for r in rows if r["stratum"] == "local_scope" and r["scenario"] == "limit_scope"):
            answer = parse_response(row["reference"])
            self.assertEqual(len(answer["arguments"]["document_ids"]), 3)
            self.assertEqual(len(row["goal_ids"]), 1 if row["boundary_side"] else 3)
            self.assertEqual(answer["arguments"]["limit"], 1 if row["boundary_side"] else 3)

    def test_stop_completion_follows_visible_target_content(self):
        for row in (r for r in self.data["train"] if r["stratum"] == "stop_finish"):
            spec = row["task_spec"]
            if not spec.get("reuse"):
                continue
            ctx = deepcopy(row["context"])
            ctx["existing_results"] = []
            self.assertEqual(data_module._label(spec, ctx)["action"], "call")
            target = next(p for p in ctx["candidates"] if p["paper_id"] == spec["target_id"])
            ctx["existing_results"] = ([target] if spec["kind"] == "resolve" else
                                       [{"paper_id": target["paper_id"], "status": "full_text", "text": "完整正文"}])
            self.assertEqual(data_module._label(spec, ctx)["reason"], "already_satisfied")

    def test_public_prompt_excludes_private_fields_and_review_never_uses_blind(self):
        case = deepcopy(self.data["train"][0])
        messages = data_module.public_prompt(case)
        self.assertEqual(messages[0], {"role": "system", "content": SYSTEM_V3})
        for key in ("reference", "goal_ids", "task_spec", "world", "rejected", "family", "stratum", "split", "id"):
            case[key] = "PRIVATE_SENTINEL_" + key
        self.assertEqual(data_module.public_prompt(case), messages)
        payload = json.loads(messages[1]["content"])
        self.assertEqual(set(payload), {"task", "context", "tools"})
        self.assertEqual(set(payload["context"]), {"mode", "allowed_providers", "allowed_document_ids",
                                                  "candidates", "existing_results", "documents"})
        samples = data_module.review_samples(self.data)
        self.assertEqual(len(samples), 20)
        self.assertEqual({row["stratum"] for row in samples}, set(data_module.STRATA))
        self.assertTrue(all(row["split"] == "train" and row["human_reviewed"] is False for row in samples))
        self.assertFalse({row["id"] for row in samples} & {c["id"] for c in self.data["blind"]})

    def test_audit_rejects_changed_labels_goals_context_and_metadata(self):
        for key, value in (("reference", canonical({"action": "finish", "reason": "no_tool_needed"})),
                           ("goal_ids", ["paper:absent"]), ("instruction", "改读另一篇论文"),
                           ("rejected", self.data["train"][0]["reference"]),
                           ("template_group", "blind/doi_target/authored-v1"), ("human_reviewed", True)):
            changed = deepcopy(self.data)
            changed["train"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "guarded_(authored_case_changed|metadata_binding)"):
                data_module.audit_data(changed)
        changed = deepcopy(self.data)
        changed["train"][0]["context"]["reference"] = changed["train"][0]["reference"]
        with self.assertRaisesRegex(ValueError, "guarded_authored_case_changed"):
            data_module.audit_data(changed)

    def test_protected_reader_uses_public_only_and_detects_reused_ids_prompts_entities(self):
        sample = self.data["train"][0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "blind_test.prompts.jsonl"
            oracle = root / "blind_test.oracle.jsonl"
            predictions = root / "blind_test.predictions.jsonl"
            oracle.write_text("PRIVATE LABEL SENTINEL, not JSON", encoding="utf-8")
            predictions.write_text("PRIVATE PREDICTION SENTINEL, not JSON", encoding="utf-8")
            old_case = deepcopy(sample)
            old_case["instruction"] = "old public unrelated topic"
            old_case["context"] = {"mode": "web", "allowed_providers": [], "allowed_document_ids": [],
                                   "candidates": [], "existing_results": []}
            unrelated = {"id": "old-public-id", "messages": v2_prompt(old_case)}
            path.write_text(canonical(unrelated) + "\n", encoding="utf-8")
            original_read = Path.read_bytes
            reads = []
            def guarded_read(item):
                reads.append(item)
                self.assertEqual(item.name, "blind_test.prompts.jsonl")
                return original_read(item)
            with patch.object(Path, "read_bytes", guarded_read):
                report = data_module.audit_data(self.data, [root])
            self.assertEqual(reads, [path.resolve()])
            self.assertEqual(report["protected_public"]["case_ids"], ["old-public-id"])
            self.assertEqual(report["protected_public"]["unique_prompt_hashes"], 2)
            self.assertEqual(report["protected_public"]["oracle_or_prediction_files_read"], 0)
            for item, error in (
                    ({"id": sample["id"], "messages": unrelated["messages"]}, "ids"),
                    ({"id": "old-other-id", "messages": v2_prompt(sample)}, "prompts")):
                path.write_text(canonical(item) + "\n", encoding="utf-8")
                with self.subTest(error=error), self.assertRaisesRegex(ValueError, "guarded_protected_overlap_" + error):
                    data_module.audit_data(self.data, [root])
            old_case["context"]["candidates"] = [sample["context"]["candidates"][0]]
            path.write_text(canonical({"id": "old-public-id", "messages": v2_prompt(old_case)}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "guarded_protected_overlap_entities"):
                data_module.audit_data(self.data, [root])
            with self.assertRaisesRegex(ValueError, "protected_public_prompts_missing"):
                data_module.audit_data(self.data, [oracle])
            with self.assertRaisesRegex(ValueError, "protected_root_missing"):
                data_module.audit_data(self.data, [root / "missing"])


if __name__ == "__main__":
    unittest.main()
