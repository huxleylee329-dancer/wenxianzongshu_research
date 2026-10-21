"""Versioned clarification only; retain the V2 prompts and scorer unchanged."""

from contracts import SYSTEM


VERSION = "tool-decision-v3-scoped-missing-fields"
OLD_RULE = "query 缺失用 missing_query；没有可用 DOI 用 missing_identifier；工具不能实现用 unsupported_request。"
NEW_RULE = """先确定用户需要的工具，再只检查该工具的必填参数；不得要求其他工具的参数。
只有 search_papers / search_library 需要 query：用户未提供检索内容时用 missing_query。
只有 resolve_paper 需要 DOI identifier：用户要求 DOI 解析但未提供可用 DOI 时用 missing_identifier。
read_paper 只需从已有候选确定 paper_id，不要求 query 或 DOI；能用用户指定的 ID、标题、年份或来源唯一定位时直接读取。
reason 必须是上述精确英文标签，不得夹带中文解释或多个标签；工具不能实现用 unsupported_request。"""
if SYSTEM.count(OLD_RULE) != 1:
    raise ValueError("legacy_prompt_changed")
SYSTEM_V3 = SYSTEM.replace(OLD_RULE, NEW_RULE)


def upgrade(messages):
    """Make a new list; task/context/schema and the original list stay untouched."""
    if (type(messages) is not list or len(messages) != 2
            or messages[0] != {"role": "system", "content": SYSTEM}
            or type(messages[1]) is not dict
            or set(messages[1]) != {"role", "content"}
            or messages[1]["role"] != "user" or type(messages[1]["content"]) is not str):
        raise ValueError("expected_v2_messages")
    return [{"role": "system", "content": SYSTEM_V3}, dict(messages[1])]
