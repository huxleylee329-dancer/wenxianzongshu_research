"""CPU-only evidence for corrective labels, counterexamples and data separation."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import correction_data as correction
from contracts import ContractError, canonical, parse_response, sha
from prompt_v3 import upgrade
from cases import public_prompt
from rewards import equivalent, score


class CorrectionDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = correction.build_cases()
        cls.empty_protected = {"hashes": {}, "identifiers": set(), "prompts": set()}

    def test_counts_all_positive_negative_bindings_and_determinism(self):
        report = correction.audit(self.cases, self.empty_protected)
        self.assertEqual(self.cases, correction.build_cases())
        self.assertEqual([report["splits"][s]["reference_successes"] for s in ("train", "probe")], [320, 80])
        self.assertEqual(report["splits"]["train"]["positive_labels"]["already_satisfied"], 32)
        self.assertEqual(len(report["splits"]["train"]["scenarios"]), 21)
        self.assertFalse(report["human_reviewed"])
        self.assertFalse(report["old_rpo_runner_compatible"])
        self.assertFalse(report["probe_is_unbiased_blind_test"])
        self.assertEqual(report["real_tool_calls"], 0)
        for scenario in report["splits"]["train"]["scenarios"]:
            topics = {c["topic"] for c in self.cases["train"] if c["scenario"] == scenario}
            self.assertGreaterEqual(len(topics), 10)

    def test_stop_label_changes_when_existing_results_no_longer_satisfy_request(self):
        for c in self.cases["train"][:80]:
            ctx = deepcopy(c["context"])
            expected = json.loads(c["reference"])
            if c["scenario"] in ("metadata_present", "full_text_present"):
                self.assertEqual(expected["reason"], "already_satisfied")
                ctx["existing_results"] = []
                self.assertEqual(correction.label(c["task_spec"], ctx)["action"], "call")
                ctx = deepcopy(c["context"])
                key = "doi" if c["scenario"] == "metadata_present" else "text"
                ctx["existing_results"][0][key] = ""
                self.assertEqual(correction.label(c["task_spec"], ctx)["action"], "call")
            elif c["scenario"] == "wrong_result":
                self.assertEqual(expected["action"], "call")
                ctx["existing_results"][0]["paper_id"] = c["task_spec"]["paper_id"]
                self.assertEqual(correction.label(c["task_spec"], ctx)["reason"], "already_satisfied")
            else:
                self.assertEqual(expected["reason"], "no_tool_needed")

    def test_paper_intersection_not_first_candidate_year_only_or_single_filter(self):
        positions = set()
        for c in (c for c in self.cases["train"] if c["family"] == "paper_binding"):
            candidates = c["context"]["candidates"]
            filters = c["task_spec"]["filters"]
            matches = [p for p in candidates if all(p[k] == v for k, v in filters.items())]
            self.assertEqual(len(candidates), 12)
            self.assertEqual(len(matches), 2 if c["scenario"] == "still_ambiguous" else 1)
            if len(matches) == 1:
                chosen = matches[0]
                positions.add(candidates.index(chosen))
                self.assertTrue(any(p["title"] == chosen["title"] and p != chosen for p in candidates))
                self.assertTrue(any(p["title"] != chosen["title"] and all(p[k] == v for k, v in filters.items()
                    if k != "title") for p in candidates))
                # The answer must follow a changed identity, not a copied label.
                ctx = deepcopy(c["context"])
                ctx["candidates"][candidates.index(chosen)]["paper_id"] = "paper:changed_identity"
                self.assertEqual(correction.label(c["task_spec"], ctx)["arguments"]["paper_id"], "paper:changed_identity")
            else:
                self.assertEqual(json.loads(c["reference"])["reason"], "ambiguous_paper")
        self.assertGreater(len(positions), 5)

    def test_document_title_scope_and_limit_are_distinct(self):
        visible_positions = set()
        for c in (c for c in self.cases["train"] if c["family"] == "document_scope"):
            answer = json.loads(c["reference"])
            ids = answer["arguments"]["document_ids"]
            spec, ctx = c["task_spec"], c["context"]
            if "titles" in spec:
                selected_titles = {d["title"] for d in ctx["documents"] if d["document_id"] in ids}
                self.assertEqual(selected_titles, set(spec["titles"]))
                self.assertEqual(len(ids), 2)
                visible_positions.add(tuple(i for i, d in enumerate(ctx["documents"]) if d["document_id"] in ids))
            else:
                self.assertEqual(set(ids), set(ctx["allowed_document_ids"]) - {spec["exclude"]})
            if c["scenario"] == "keep_scope_at_limit_one":
                self.assertEqual(answer["arguments"]["limit"], 1)
                self.assertEqual(len(c["goal_ids"]), 1)
                self.assertEqual(len(ids), 2)
            shuffled = deepcopy(ctx)
            shuffled["documents"].reverse()
            self.assertTrue(equivalent(correction.label(spec, shuffled), answer))
            self.assertEqual(score(c["rejected"], c)["code"], "task_mismatch")
        self.assertGreater(len(visible_positions), 8)

    def test_format_negative_is_preserved_not_repaired_or_allowed_by_scorer(self):
        codes = Counter()
        for c in (c for c in self.cases["train"] if c["family"] == "json_schema"):
            result = score(c["rejected"], c)
            codes[result["code"]] += 1
            self.assertEqual(result["mock_calls"], 0)
            self.assertFalse(result["task_success"])
            self.assertEqual(score(c["reference"], c)["mock_calls"], 1)
            if c["scenario"] == "duplicate_paper_key":
                # A permissive last-key-wins decoder masks this exact failure.
                self.assertEqual(json.loads(c["rejected"]), json.loads(c["reference"]))
                with self.assertRaisesRegex(ContractError, "invalid_json"):
                    parse_response(c["rejected"])
        self.assertEqual(codes, {"invalid_fields": 40, "invalid_json": 10,
                                "invalid_string": 10, "paper_not_authorized": 20})

    def test_audit_rejects_bad_label_instruction_goal_and_leakage(self):
        for field, value, error in (
                ("reference", canonical({"action": "finish", "reason": "no_tool_needed"}), "label_binding"),
                ("instruction", "读取不在上下文里的另一篇", "instruction_binding"),
                ("goal_ids", ["paper:absent"], "reference_failed"),
                ("rejected", self.cases["train"][0]["reference"], "negative_succeeded"),
                ("topic", correction.TOPICS["probe"][0], "topic_or_split")):
            changed = deepcopy(self.cases)
            changed["train"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, error):
                correction.audit(changed, self.empty_protected)
        first = self.cases["train"][0]
        for key, value, error in (
                ("identifiers", first["id"], "identity_overlap"),
                ("prompts", sha(canonical(upgrade(public_prompt(first)))), "old_prompt")):
            protected = deepcopy(self.empty_protected)
            protected[key].add(value)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, error):
                correction.audit(self.cases, protected)

    def test_data_only_export_crc_hashes_prompt_isolation_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "corrections"
            with patch.object(correction, "read_protected_metadata", return_value=self.empty_protected):
                report = correction.export(Path(tmp), out)
                with self.assertRaisesRegex(ValueError, "new_correction_output_required"):
                    correction.export(Path(tmp), out)
            self.assertEqual(report["review_scenarios"], 21)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["training_integration_ready"])
            for name, digest in manifest["files_sha256"].items():
                self.assertEqual(sha((out / name).read_bytes()), digest)
            with zipfile.ZipFile(out.with_suffix(".zip")) as z:
                self.assertIsNone(z.testzip())
                self.assertFalse(any(n.endswith((".py", ".sh", ".safetensors")) for n in z.namelist()))
            for split in ("train", "probe"):
                for line in (out / f"{split}.prompts.jsonl").read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    self.assertEqual(set(row), {"id", "messages"})
                    self.assertEqual(len(row["messages"]), 2)
                    self.assertEqual(set(json.loads(row["messages"][1]["content"])), {"task", "context", "tools"})
            sft = [json.loads(s) for s in (out / "train.sft.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(sft), 320)
            self.assertTrue(all(r["messages"][-1]["role"] == "assistant" for r in sft))
            self.assertFalse({r["id"] for r in sft} & {c["id"] for c in self.cases["probe"]})


if __name__ == "__main__":
    unittest.main()
