"""Injected adapter protocol for the offline academic-writing skeleton."""

from __future__ import annotations

from typing import Protocol

from .state import (
    AcademicWorkflowRequest,
    AdapterFailure,
    WorkflowOutline,
    WorkflowResearchEvidence,
    WorkflowTopicPlan,
)


class AcademicWritingAdapter(Protocol):
    """Provide deterministic workflow artifacts without live objects in state."""

    async def plan_topic(
        self,
        request: AcademicWorkflowRequest,
    ) -> WorkflowTopicPlan | AdapterFailure: ...

    async def collect_research_evidence(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
    ) -> WorkflowResearchEvidence | AdapterFailure: ...

    async def write_outline(
        self,
        request: AcademicWorkflowRequest,
        topic_plan: WorkflowTopicPlan,
        evidence: WorkflowResearchEvidence,
    ) -> WorkflowOutline | AdapterFailure: ...
