"""Offline checks for refreshed training labels, isolation and v1/v2 comparisons."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cases import public_prompt
from contracts import canonical, sha
import dataset
import grpo
import grpo_bundle
from prompt_v3 import upgrade
from rewards import score
import sft
import sft_bundle
import sft_refresh as refresh


class RefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = dataset.build_cases()
        cls.pool = [refresh.make_case(f, i) for f in refresh.NEW_FAMILIES for i in range(24)]

    def test_pool_and_selection_have_references_but_no_held_out_overlap(self):
        snapshot = canonical(self.original)
        report = refresh.audit_pool(self.pool, self.original)
        self.assertEqual(report["reference_successes"], 240)
        self.assertGreater(report["negative_checks"], 1000)
        self.assertTrue(report["validation_used_to_identify_skill_gaps"])
        self.assertFalse(report["blind_predictions_used"])
        self.assertFalse(report["human_reviewed"])
        selected = refresh.select_new(self.pool)
        self.assertEqual(len(selected), 80)
        self.assertEqual(Counter(c["family"] for c in selected), Counter({f: 8 for f in refresh.NEW_FAMILIES}))
        self.assertEqual(Counter((c["family"], c["selection_stratum"]) for c in selected),
                         Counter({(f, s): 2 for f in refresh.NEW_FAMILIES for s in range(4)}))
        self.assertEqual(selected, refresh.select_new(list(reversed(self.pool))))
        self.assertEqual(canonical(self.original), snapshot)
        for key in report["held_out_overlap"]:
            self.assertTrue(all(n == 0 for n in report["held_out_overlap"][key].values()))

    def test_reading_decision_changes_with_unique_vs_ambiguous_metadata(self):
        for family in ("clarify_title_collision", "clarify_filter_still_ambiguous"):
            for index in range(24):
                c = refresh.make_case(family, index)
                self.assertEqual(json.loads(c["reference"]), {"action": "clarify", "reason": "ambiguous_paper"})
                matches = [p for p in c["context"]["candidates"]
                           if all(p[k] == v for k, v in c["task_spec"]["filters"].items())]
                chosen = matches[-1]
                spec = {"kind": "read", "filters": {"paper_id": chosen["paper_id"]}}
                self.assertEqual(refresh.expected_action(spec, c["context"]),
                                 {"action": "call", "tool": "read_paper", "arguments": {"paper_id": chosen["paper_id"]}})
        for index in range(24):
            c = refresh.make_case("read_unique_year_filter", index)
            answer = json.loads(c["reference"])
            chosen = next(p for p in c["context"]["candidates"] if p["paper_id"] == answer["arguments"]["paper_id"])
            self.assertEqual(chosen["year"], c["task_spec"]["filters"]["year"])
            self.assertEqual(chosen["title"], c["task_spec"]["filters"]["title"])
            # Looking only at the year finds two records; both year AND title are required.
            self.assertEqual(sum(p["year"] == chosen["year"] for p in c["context"]["candidates"]), 2)
            ambiguous = {"kind": "read", "filters": {"title": chosen["title"]}}
            self.assertEqual(refresh.expected_action(ambiguous, c["context"])["reason"], "ambiguous_paper")
            reverse = deepcopy(c["context"])
            reverse["candidates"].reverse()
            self.assertEqual(refresh.expected_action(c["task_spec"], reverse), answer)

    def test_limits_do_not_drop_documents_and_ids_are_copied_without_repair(self):
        for index in range(24):
            c = refresh.make_case("library_exclude_one_keep_all", index)
            answer = json.loads(c["reference"])
            ids = answer["arguments"]["document_ids"]
            self.assertEqual(len(ids), 3)
            self.assertNotIn(c["task_spec"]["exclude"], ids)
            self.assertLess(answer["arguments"]["limit"], len(ids))
            self.assertEqual(score(c["reference"], c)["reward"], 1)
            changed = deepcopy(answer)
            changed["arguments"]["document_ids"] = ids[:answer["arguments"]["limit"]]
            self.assertFalse(score(canonical(changed), c)["task_success"])
            paper = refresh.make_case("read_copy_id_exact", index)
            value = json.loads(paper["reference"])["arguments"]["paper_id"]
            self.assertEqual(value, paper["task_spec"]["filters"]["paper_id"])
            self.assertIn(value, {p["paper_id"] for p in paper["context"]["candidates"]})
            bad = json.loads(paper["reference"])
            bad["arguments"]["paper_id"] = value[:-1]
            self.assertEqual(score(canonical(bad), paper)["code"], "paper_not_authorized")

    def test_permission_and_untrusted_text_do_not_change_authorized_tool(self):
        for index in range(24):
            c = refresh.make_case("clarify_remote_permission", index)
            self.assertEqual(json.loads(c["reference"]), {"action": "clarify", "reason": "permission_required"})
            denied = {"action": "call", "tool": "search_papers", "arguments": {
                "query": c["task_spec"]["query"], "providers": [c["task_spec"]["provider"]], "per_source": 1}}
            self.assertEqual(score(canonical(denied), c)["code"], "network_not_authorized")
            c = refresh.make_case("search_ignore_embedded_request", index)
            answer = json.loads(c["reference"])
            self.assertEqual(answer["tool"], "search_papers")
            self.assertEqual(answer["arguments"]["providers"], [c["task_spec"]["provider"]])
            messages = upgrade(public_prompt(c))
            user = json.loads(messages[1]["content"])
            self.assertEqual(set(user), {"task", "context", "tools"})
            self.assertNotIn("task_spec", messages[1]["content"])
            self.assertNotIn("goal_ids", messages[1]["content"])

    def test_audit_rejects_changed_labels_and_identity_leakage(self):
        for field, value in (("reference", canonical({"action": "finish", "reason": "no_tool_needed"})),
                             ("id", self.original["validation"][0]["id"]),
                             ("topic", dataset.TOPICS["blind_test"][0])):
            changed = deepcopy(self.pool)
            changed[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                refresh.audit_pool(changed, self.original)

    def test_full_bundle_and_three_way_score_reuse_old_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "dataset"
            dataset.build(data)
            old_zip = root / "grpo.zip"
            grpo_bundle.export(data, old_zip)
            grpo_dir = root / "grpo"
            with zipfile.ZipFile(old_zip) as z:
                z.extractall(grpo_dir)
            _, prompts, _ = grpo.load_bundle(grpo_dir)
            predictions = [{"id": c["id"], "response": c["reference"]} for c in self.original["validation"]]
            before = root / "before"
            before.mkdir()
            (before / "predictions.jsonl").write_bytes(sft_bundle.rows_bytes(predictions))
            baseline = {**grpo.EVAL_SETTINGS, "status": "complete", "phase": "before", "prompt_version": grpo.VERSION,
                        "bundle_sha256": grpo.file_sha(grpo_dir / "grpo-bundle.json"), "selected": 200,
                        "completed": 200, "generation_calls": 200, "real_tool_calls": 0, "truncated_outputs": 0,
                        "predictions_sha256": grpo.file_sha(before / "predictions.jsonl"),
                        "gpu": "fake-test-only", "versions": {"torch": "fake-test-only"}, "model_files_sha256": {"weights": "fake"}}
            (before / "run.json").write_text(canonical(baseline), encoding="utf-8")
            v1 = root / "v1"
            sft_bundle.export(data, grpo_dir, before, v1, root / "v1.zip")
            old_results = root / "v1-results"
            self.make_results(old_results, v1, baseline, predictions)
            sft_bundle.score_results(data, v1, old_results / "runs/sft-after", old_results / "runs/sft-train",
                                     old_results / "comparison.json")
            protected = {str(p): grpo.file_sha(p) for folder in (data, v1, old_results)
                         for p in folder.rglob("*") if p.is_file()}
            new_data, new_bundle, archive = root / "new-data", root / "v2", root / "v2.zip"
            report = refresh.build(data, v1, old_results, new_data, new_bundle, archive)
            self.assertEqual(report["train"], 200)
            self.assertEqual((report["replay"], report["new_selected"], report["new_pool"]), (120, 80, 240))
            manifest, train, validation, _ = sft.load_bundle(new_bundle)
            self.assertEqual(validation, prompts["validation"])
            self.assertEqual(manifest["experiment_id"], refresh.VERSION)
            self.assertEqual(Counter(r["family"] for r in train), Counter({f: 8 for f in manifest["train_families"]}))
            for name in sft.RUNTIME_FILES - {"CLOUD_SFT.md"}:
                self.assertEqual((v1 / name).read_bytes(), (new_bundle / name).read_bytes())
            with zipfile.ZipFile(archive) as z:
                self.assertEqual(set(z.namelist()), sft.FILES | {"sft-bundle.json"})
                self.assertFalse(any("oracle" in name or "blind" in name for name in z.namelist()))
            self.assertEqual({name: grpo.file_sha(Path(name)) for name in protected}, protected)
            new_results = root / "v2-results"
            self.make_results(new_results, new_bundle, baseline, predictions)
            scored = refresh.compare(data, new_bundle, new_results, old_results, root / "comparison-v2.json")
            self.assertEqual((scored["base_correct"], scored["v1_correct"], scored["v2_correct"]), (200, 200, 200))
            self.assertFalse(scored["selection_guard"]["ambiguity_improved"])
            saved = json.loads((root / "comparison-v2.json").read_text(encoding="utf-8"))
            self.assertFalse(saved["blind_test_executed"])
            self.assertEqual(saved["paired_v1_to_v2"], {"correct_to_correct": 200})
            with self.assertRaisesRegex(ValueError, "new_refresh_output_paths_required"):
                refresh.build(data, v1, old_results, new_data, new_bundle, archive)
            # Default runner still cannot use GPU or execute a model during preflight.
            with patch("sys.argv", ["sft.py", "--bundle", str(new_bundle)]), patch.object(sft, "execute") as execute, patch("builtins.print") as printed:
                sft.main()
                execute.assert_not_called()
                self.assertEqual(json.loads(printed.call_args.args[0])["status"], "preflight_only")

    @staticmethod
    def make_results(root, bundle, baseline, predictions):
        train = root / "runs/sft-train"
        adapter = train / "adapter"
        adapter.mkdir(parents=True)
        (adapter / "adapter_model.safetensors").write_bytes(b"fake-test-weights-never-loaded")
        (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
        hashes = {p.name: grpo.file_sha(p) for p in adapter.iterdir()}
        report = {**baseline, "status": "complete", "phase": "train", "method": "sft-only",
                  "bundle_sha256": grpo.file_sha(bundle / "sft-bundle.json"), "generation_calls": 0,
                  "settings": sft.SFT_SETTINGS, "config": sft.training_config(train, "train"),
                  "optimizer_steps": 50, "examples_seen": 200, "losses": [0.5] * 200,
                  "grad_norms": [0.1] * 50, "adapter_changed": True, "adapter_files_sha256": hashes}
        (train / "run.json").write_text(canonical(report), encoding="utf-8")
        after = root / "runs/sft-after"
        after.mkdir()
        (after / "predictions.jsonl").write_bytes(sft_bundle.rows_bytes(predictions))
        result = {**baseline, "phase": "after", "method": "sft-only", "optimizer_steps": 0,
                  "bundle_sha256": grpo.file_sha(bundle / "sft-bundle.json"), "adapter_files_sha256": hashes,
                  "train_run_sha256": grpo.file_sha(train / "run.json"),
                  "predictions_sha256": grpo.file_sha(after / "predictions.jsonl")}
        (after / "run.json").write_text(canonical(result), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
