from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentGraphApprovalChangeView(BaseModel):
    field: str
    field_zh: str
    before: str | None = None
    after: str | None = None


class AgentGraphApprovalItemView(BaseModel):
    finding_id: UUID
    entity_kind: str
    entity_name: str | None = None
    entity_number: str | None = None
    class_name: str | None = None
    source_locator: str
    source_row_number: int | None = None
    operation_zh: str
    issue_zh: str
    analysis_zh: str
    solution_zh: str
    changes: tuple[AgentGraphApprovalChangeView, ...] = ()


class AgentGraphHumanGateView(BaseModel):
    id: UUID
    kind: str
    status: str
    item_count: int
    entity_kind: str | None = None
    operation: str | None = None
    issue_kind: str | None = None
    risk: str | None = None
    cursor: int
    membership_hash: str | None = None
    member_decisions: dict[str, str] = Field(default_factory=dict)
    summary_zh: str | None = None
    risk_reason_zh: str | None = None
    actionable: bool = False
    unavailable_reason_zh: str | None = None
    items: tuple[AgentGraphApprovalItemView, ...] = ()


class AgentGraphProgressResponse(BaseModel):
    task_id: UUID
    workflow_version: Literal["agent-graph-v1"]
    graph_version: str
    graph_cursor: int
    current_node: str
    business_stage: Literal[
        "data_ingestion",
        "agent_analysis",
        "governance_execution",
        "report_and_rollback",
        "terminal",
    ]
    current_action_zh: str
    sub_agent_zh: str | None = None
    progress_completed: int | None = Field(default=None, ge=0)
    progress_total: int | None = Field(default=None, ge=0)
    status: str
    can_terminate: bool
    termination_requested: bool
    human_gates: tuple[AgentGraphHumanGateView, ...] = ()


class AgentGraphGateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=1000)
    approved_finding_ids: tuple[UUID, ...] = ()
    rejected_finding_ids: tuple[UUID, ...] = ()
    graph_cursor: int | None = Field(default=None, ge=0)
    membership_hash: str | None = Field(default=None, min_length=64, max_length=64)


class AgentGraphGateDecisionResponse(BaseModel):
    gate_id: UUID
    status: Literal["approved", "rejected"]
    graph_cursor: int


class AgentGraphGateBatchDecisionItem(AgentGraphGateDecisionRequest):
    gate_id: UUID


class AgentGraphGateBatchDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: tuple[AgentGraphGateBatchDecisionItem, ...] = Field(
        min_length=1,
        max_length=100,
    )


class AgentGraphGateBatchDecisionResponse(BaseModel):
    decisions: tuple[AgentGraphGateDecisionResponse, ...]
