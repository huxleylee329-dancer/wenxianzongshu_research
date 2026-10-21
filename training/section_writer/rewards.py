"""Conservative, closed-rubric reward for a SHORT evidence-writing pilot.

This is NOT an entailment model or a general academic-quality judge. Unknown
paraphrases receive zero rather than being declared false or supported. A human
must approve the sentence variants, attribution and evidence before real use.
"""

from __future__ import annotations

import re

from runtime import parse_response, production


MARKER = r"\[\[cite:([^\[\]]+)\]\]"
UNIT = re.compile(r"(.*?)(\[\[cite:[^\[\]]+\]\](?:\s*\[\[cite:[^\[\]]+\]\])*)", re.S)


def score(response: object, allowed_source_ids: list[str], rubric: dict) -> dict:
    content = parse_response(response, allowed_source_ids)
    if content is None:
        return {"reward": -1.0, "parser_passed": False,
                "rubric_coverage": 0.0, "unrecognized_units": 0}
    units = rubric["units"]
    recognized = set()
    unknown = 0
    position = 0
    for match in UNIT.finditer(content):
        if match.start() != position:
            unknown += 1
        text = match[1].strip()
        ids = set(re.findall(MARKER, match[2]))
        hit = next((unit["claim_id"] for unit in units
                    if text in unit["variants"] and ids == set(unit["source_ids"])), None)
        if hit is None:
            unknown += 1
        else:
            recognized.add(hit)
        position = match.end()
    if content[position:].strip():
        unknown += 1
    coverage = len(recognized) / len(units) if units else 0.0
    # No reward for citation stuffing, copying only IDs, duplicates, or adding
    # an unreviewed claim to an otherwise correct paragraph.
    reward = coverage if unknown == 0 else 0.0
    return {"reward": reward, "parser_passed": True,
            "rubric_coverage": coverage, "unrecognized_units": unknown}


def completion_text(completion: object) -> object:
    if type(completion) is str:
        return completion
    if (type(completion) is list and len(completion) == 1
            and type(completion[0]) is dict
            and completion[0].get("role") == "assistant"):
        return completion[0].get("content")
    return None


def grounded_reward(completions, allowed_source_ids, rubric, **kwargs):
    """TRL custom reward; no network, judge model, production retry or repair."""
    if not (len(completions) == len(allowed_source_ids) == len(rubric)):
        raise ValueError("reward batch alignment invalid")
    return [score(completion_text(answer), ids, rule)["reward"]
            for answer, ids, rule in zip(completions, allowed_source_ids, rubric, strict=True)]


def citation_ids(content: str, allowed_source_ids: list[str]) -> tuple[str, ...]:
    value = production("section_writer")._extract_citations(content, tuple(allowed_source_ids))
    if type(value) is not tuple:
        raise ValueError("production marker parsing failed")
    return value
