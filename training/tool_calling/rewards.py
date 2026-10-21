"""Single-step task reward, not a real retrieval/factuality benchmark."""

from contracts import ContractError, normalized_query, parse_response
from environment import OfflineEnvironment


def equivalent(actual, expected):
    if actual["action"] != expected["action"]:
        return False
    if actual["action"] != "call":
        return actual == expected
    if actual["tool"] != expected["tool"]:
        return False
    for key, value in expected["arguments"].items():
        got = actual["arguments"][key]
        if key == "query":
            if normalized_query(got) != normalized_query(value):
                return False
        elif key in ("providers", "document_ids"):
            if set(got) != set(value):
                return False
        elif got != value:
            return False
    return True


def score(raw, case):
    env = OfflineEnvironment(case["context"], case["world"])
    try:
        action = parse_response(raw)
        result = env.execute(action)
    except ContractError as exc:
        return {"reward": -1.0, "task_success": False, "code": exc.args[0],
                "mock_calls": len(env.calls)}
    expected = parse_response(case["reference"])
    matched = equivalent(action, expected)
    # The environment does not see the reference or goal. Parameter correctness
    # alone is insufficient: its independently computed result must match too.
    correct_result = set(result["ids"]) == set(case["goal_ids"])
    success = matched and correct_result
    return {"reward": 1.0 if success else 0.0, "task_success": success,
            "code": "success" if success else "task_mismatch", "mock_calls": len(env.calls)}


def tool_reward(completions, task_case, **kwargs):
    """TRL-compatible signature; private labels must never enter model messages."""
    if len(completions) != len(task_case):
        raise ValueError("batch_alignment")
    results = []
    for answer, case in zip(completions, task_case):
        if type(answer) is list and len(answer) == 1 and type(answer[0]) is dict:
            answer = answer[0].get("content") if answer[0].get("role") == "assistant" else None
        results.append(score(answer, case)["reward"])
    return results
