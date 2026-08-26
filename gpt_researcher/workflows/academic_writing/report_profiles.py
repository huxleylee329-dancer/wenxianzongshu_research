"""Immutable fixed report profiles for the academic-writing workflow."""

from __future__ import annotations

from typing import Literal, TypeAlias


ReportMode: TypeAlias = Literal[
    "freeform",
    "stem_literature_review",
    "technical_route_survey",
    "method_comparison",
    "equipment_material_selection",
    "proposal_research_status",
    "systematic_literature_review",
]

_Profile = tuple[tuple[str, str], ...]

_REPORT_PROFILES: tuple[tuple[ReportMode, _Profile], ...] = (
    (
        "stem_literature_review",
        (
            ("research_background", "研究背景"),
            ("literature_search_method", "文献检索方法"),
            ("technical_routes", "主要技术路线"),
            ("experimental_methods_and_metrics", "实验方法与评价指标"),
            ("results_comparison", "研究结果对比"),
            ("existing_problems", "现有问题"),
            ("future_research", "未来研究方向"),
            ("conclusion", "结论"),
        ),
    ),
    (
        "technical_route_survey",
        (
            ("requirements_and_scope", "需求与边界"),
            ("literature_search_method", "资料检索方法"),
            ("technical_routes", "技术路线分类"),
            ("principles_and_process", "核心原理与流程"),
            ("performance_maturity_cost_comparison", "性能成熟度与成本对比"),
            ("application_scenarios", "适用场景"),
            ("risks_and_challenges", "风险与难点"),
            ("recommended_route", "推荐路线"),
            ("conclusion", "结论"),
        ),
    ),
    (
        "method_comparison",
        (
            ("problem_definition", "问题定义"),
            ("comparison_framework", "比较框架"),
            ("candidate_methods", "候选方法"),
            ("experimental_conditions_and_data", "实验条件与数据"),
            ("performance_comparison", "性能对比"),
            ("robustness_and_scalability", "鲁棒性与扩展性"),
            ("cost_and_engineering_complexity", "成本与工程复杂度"),
            ("selection_guidance", "适用条件与选型建议"),
            ("conclusion", "结论"),
        ),
    ),
    (
        "equipment_material_selection",
        (
            ("requirements_and_constraints", "需求与约束"),
            ("candidate_options", "候选方案"),
            ("parameters_and_material_properties", "关键参数与材料性能"),
            ("testing_and_evidence", "测试与证据"),
            ("compatibility_and_reliability", "兼容性与可靠性"),
            ("cost_and_supply_risk", "成本与供应风险"),
            ("safety_environment_compliance", "安全环保与合规"),
            ("decision_matrix", "决策矩阵"),
            ("recommended_solution", "推荐方案"),
            ("conclusion", "结论"),
        ),
    ),
    (
        "proposal_research_status",
        (
            ("research_background_and_significance", "研究背景与意义"),
            ("literature_search_method", "检索范围与方法"),
            ("domestic_research_status", "国内研究现状"),
            ("international_research_status", "国外研究现状"),
            ("technical_routes", "主要学派或技术路线"),
            ("existing_problems", "现有不足"),
            ("proposed_problem", "拟解决问题"),
            ("research_content_and_innovation", "研究内容与创新点"),
            ("conclusion", "结论"),
        ),
    ),
    (
        "systematic_literature_review",
        (
            ("research_questions_and_protocol", "研究问题与协议"),
            ("databases_and_search_strategy", "数据库与检索式"),
            ("eligibility_criteria", "纳入排除标准"),
            ("quality_assessment", "质量评价"),
            ("study_selection_process", "文献筛选流程"),
            ("data_extraction_and_synthesis", "数据提取与综合"),
            ("results", "结果"),
            ("bias_and_limitations", "偏倚与局限"),
            ("discussion", "讨论"),
            ("conclusion", "结论"),
        ),
    ),
)

_FIXED_REPORT_MODES: tuple[ReportMode, ...] = tuple(
    mode for mode, _profile in _REPORT_PROFILES
)


def _get_report_profile(mode: ReportMode) -> _Profile | None:
    for candidate, profile in _REPORT_PROFILES:
        if candidate == mode:
            return profile
    return None


def _is_known_section_role(role: str) -> bool:
    if role == "freeform":
        return True
    for _mode, profile in _REPORT_PROFILES:
        for candidate, _title in profile:
            if candidate == role:
                return True
    return False


__all__ = ("ReportMode",)
