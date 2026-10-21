"""Offline behavioral checks; no model, application startup, or network."""

import ast
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from cases import build_cases, public_prompt
from contracts import (ContractError, ROOT, PROVIDERS, canonical, parse_response,
                       production_hashes, tools_schema)
from environment import OfflineEnvironment
from prepare import build, evaluate, load_verified, mutations, self_check
from rewards import score, tool_reward


class ToolPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = build_cases()

    def setUp(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("network forbidden")
        for name in ("create_connection", "getaddrinfo"):
            guard = patch.object(socket, name, forbidden)
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch.object(socket.socket, "connect", forbidden)
        guard.start()
        self.addCleanup(guard.stop)

    def selected(self, category):
        return next(c for c in self.cases if c["category"] == category)

    def test_all_50_gold_actions_execute_once_or_not_at_all(self):
        result = self_check(self.cases)
        self.assertEqual(result["examples"], 50)
        self.assertEqual(result["reference_successes"], 50)
        self.assertEqual(result["categories"], {"search_papers": 10, "resolve_paper": 8,
                         "read_paper": 10, "search_library": 10, "clarify": 8, "finish": 4})
        self.assertEqual(result["actions"], {"call": 38, "clarify": 8, "finish": 4})
        self.assertIsNone(result["model_accuracy"])
        self.assertEqual(build_cases(), self.cases)

    def test_public_prompt_never_receives_oracle_or_mock_world(self):
        for case in self.cases:
            messages = public_prompt(case)
            self.assertEqual([m["role"] for m in messages], ["system", "user"])
            body = json.loads(messages[1]["content"])
            self.assertEqual(set(body), {"task", "context", "tools"})
            self.assertEqual(body["context"], case["context"])
            changed = deepcopy(case)
            changed.update(reference="SECRET GOLD", goal_ids=["SECRET GOAL"], world={"SECRET": True})
            self.assertEqual(public_prompt(changed), messages)
        self.assertEqual(len({c["split_group"] for c in self.cases}), 1)
        self.assertTrue(all(c["synthetic"] and not c["human_reviewed"] for c in self.cases))

    def test_every_wrong_action_mutation_loses_reward(self):
        checked = 0
        for case in self.cases:
            for name, raw in mutations(case):
                with self.subTest(task=case["id"], mutation=name):
                    self.assertLessEqual(score(raw, case)["reward"], 0)
                    checked += 1
        self.assertGreaterEqual(checked, 250)

    def test_equivalent_defaults_order_whitespace_and_doi_are_accepted(self):
        case = self.selected("search_papers")
        action = json.loads(case["reference"])
        del action["arguments"]["per_source"]
        action["arguments"]["query"] = "  固态电池界面  "
        self.assertEqual(score(json.dumps(action, indent=2), case)["reward"], 1)
        case = self.cases[1]
        action = json.loads(case["reference"])
        action["arguments"]["providers"].reverse()
        action["arguments"]["query"] = "  SILICON   ANODE "
        self.assertEqual(score(canonical(action), case)["reward"], 1)
        case = self.selected("resolve_paper")
        action = json.loads(case["reference"])
        action["arguments"]["identifier"] = "HTTPS://DOI.ORG/" + action["arguments"]["identifier"].upper()
        self.assertEqual(score(canonical(action), case)["reward"], 1)
        case = self.selected("search_library")
        action = json.loads(case["reference"])
        del action["arguments"]["limit"]
        self.assertEqual(score(canonical(action), case)["reward"], 1)
        case = [c for c in self.cases if c["category"] == "search_library"][-1]
        action = json.loads(case["reference"])
        action["arguments"]["document_ids"].reverse()
        self.assertEqual(score(canonical(action), case)["reward"], 1)

    def test_unauthorized_actions_are_blocked_before_mock_execution(self):
        trials = []
        case = deepcopy(self.selected("search_papers"))
        action = json.loads(case["reference"])
        action["arguments"]["providers"] = ["zenodo"]
        trials.append((case, action))
        case = deepcopy(self.selected("search_papers"))
        case["context"]["mode"] = "local"
        trials.append((case, json.loads(case["reference"])))
        case = deepcopy(self.selected("resolve_paper"))
        case["context"]["allowed_providers"] = ["openalex"]
        trials.append((case, json.loads(case["reference"])))
        case = self.selected("search_library")
        action = json.loads(case["reference"])
        action["arguments"]["document_ids"] = ["f" * 32]
        trials.append((case, action))
        case = deepcopy(case)
        case["context"]["mode"] = "web"
        trials.append((case, json.loads(case["reference"])))
        case = self.selected("read_paper")
        action = json.loads(case["reference"])
        action["arguments"]["paper_id"] = "https://localhost/private"
        trials.append((case, action))
        for case, action in trials:
            with self.subTest(tool=action["tool"]):
                result = score(canonical(action), case)
                self.assertEqual(result["reward"], -1)
                self.assertEqual(result["mock_calls"], 0)

    def test_valid_arguments_need_correct_task_and_independent_result(self):
        case = self.selected("search_papers")
        altered = deepcopy(case)
        altered["world"]["papers"] = []
        self.assertEqual(score(case["reference"], altered)["reward"], 0)
        action = json.loads(case["reference"])
        # Still returns the same sole mock result, but violates explicit scope.
        action["arguments"]["per_source"] = 5
        self.assertEqual(score(canonical(action), case)["reward"], 0)
        case = self.selected("search_library")
        action = json.loads(case["reference"])
        action["arguments"]["document_ids"] = case["context"]["allowed_document_ids"]
        self.assertEqual(score(canonical(action), case)["reward"], 0)
        # A correct tool name with mismatched query cannot earn format-only points.
        action["arguments"]["query"] = "unrelated topic"
        self.assertEqual(score(canonical(action), case)["reward"], 0)

    def test_strict_json_and_argument_boundaries(self):
        case = self.selected("search_papers")
        invalid = [None, {}, "", "[]", "```json\n{}\n```", "{} trailing",
                   '{"action":"finish","action":"clarify","reason":"no_tool_needed"}',
                   '{"action":"finish","reason":NaN}', "[" * 2000 + "]" * 2000]
        for raw in invalid:
            self.assertEqual(score(raw, case)["reward"], -1)
        for value in (0, 21, True, "6", 6.0):
            action = json.loads(case["reference"])
            action["arguments"]["per_source"] = value
            self.assertEqual(score(canonical(action), case)["mock_calls"], 0)
        for providers in ([], ["openalex"] * 2, list(PROVIDERS[:7]), ["unknown"]):
            action = json.loads(case["reference"])
            action["arguments"]["providers"] = providers
            self.assertEqual(score(canonical(action), case)["reward"], -1)
        for query, allowed in (("a" * 4096, True), ("a" * 4097, False), (" ", False)):
            action = json.loads(case["reference"])
            action["arguments"]["query"] = query
            if allowed:
                self.assertEqual(parse_response(canonical(action)), action)
            else:
                with self.assertRaises(ContractError):
                    parse_response(canonical(action))
        for limit in (1, 64):
            action = json.loads(self.selected("search_library")["reference"])
            action["arguments"]["limit"] = limit
            self.assertEqual(parse_response(canonical(action)), action)
        action["arguments"]["limit"] = 65
        with self.assertRaises(ContractError):
            parse_response(canonical(action))

    def test_one_step_no_retry_and_input_is_unchanged(self):
        for case in (self.cases[0], self.cases[-1]):
            original = deepcopy(case)
            action = parse_response(case["reference"])
            env = OfflineEnvironment(case["context"], case["world"])
            result = env.execute(action)
            self.assertEqual(set(result["ids"]), set(case["goal_ids"]))
            with self.assertRaisesRegex(ContractError, "one_step_only"):
                env.execute(action)
            self.assertLessEqual(len(env.calls), 1)
            self.assertEqual(case, original)

    def test_metadata_instructions_do_not_expand_permissions(self):
        case = next(c for c in self.cases if c["family"] == "untrusted-result-instruction")
        self.assertTrue(any("忽略用户" in p["title"] for p in case["context"]["candidates"]))
        self.assertEqual(score(case["reference"], case)["reward"], 1)
        raw = canonical({"action": "call", "tool": "search_library", "arguments": {
            "query": "private", "document_ids": ["a" * 32]}})
        result = score(raw, case)
        self.assertEqual((result["reward"], result["mock_calls"]), (-1, 0))

    def test_reward_callback_alignment_and_failure_is_not_positive(self):
        chosen = [self.cases[0], self.cases[-1]]
        completions = [[{"role": "assistant", "content": c["reference"]}] for c in chosen]
        self.assertEqual(tool_reward(completions, chosen), [1, 1])
        with self.assertRaisesRegex(ValueError, "batch_alignment"):
            tool_reward(completions, chosen[:1])
        self.assertEqual(tool_reward([[{"role": "user", "content": chosen[0]["reference"]}]], chosen[:1]), [-1])

    def test_bundle_integrity_exclusive_output_and_complete_prediction_coverage(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "pilot"
            build(directory)
            self.assertEqual(load_verified(directory), self.cases)
            with self.assertRaises(FileExistsError):
                build(directory)
            predictions = Path(temporary) / "answers.jsonl"
            rows = [{"id": c["id"], "response": c["reference"]} for c in reversed(self.cases)]
            predictions.write_text("\n".join(canonical(r) for r in rows), encoding="utf-8")
            report = evaluate(directory, predictions)
            self.assertEqual((report["task_successes"], report["mock_calls"]), (50, 38))
            for wrong in (rows[:-1], rows[:-1] + [rows[0]]):
                predictions.write_text("\n".join(canonical(r) for r in wrong), encoding="utf-8")
                with self.assertRaises(ValueError):
                    evaluate(directory, predictions)
            (directory / "prompts.jsonl").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dataset_changed"):
                load_verified(directory)

    def test_production_contract_anchor_and_offline_interface_mapping(self):
        # Read syntax only: do not import the app, initialize providers or library.
        # This supplements runtime schema/authorization tests, not a symbol-only test.
        source = (ROOT / "gpt_researcher/academic_library/research.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        providers = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "PROVIDERS" for t in n.targets))
        self.assertEqual(providers, PROVIDERS)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PaperTools")
        methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
        expected = {"search_papers": ["self", "query", "providers"],
                    "resolve_paper": ["self", "identifier"], "read_paper": ["self", "paper"],
                    "search_library": ["self", "query", "document_ids", "limit"]}
        for name, arguments in expected.items():
            self.assertEqual([a.arg for a in methods[name].args.args], arguments)
        self.assertEqual(ast.literal_eval(methods["search_papers"].args.kw_defaults[0]), 6)
        self.assertEqual(ast.literal_eval(methods["search_library"].args.defaults[0]), 12)
        self.assertEqual({s["function"]["name"] for s in tools_schema()}, set(expected))
        self.assertEqual(len(production_hashes()), 5)
        self.assertEqual(score(self.cases[0]["reference"], self.cases[0])["mock_calls"], 1)


if __name__ == "__main__":
    unittest.main()
