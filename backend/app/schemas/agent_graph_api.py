from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentGraphHumanGateView(BaseModel):
    id: UUID
    kind: str
    status: str
    item_count: int
    entity_kind: str | None = None
    operation: str | None = None
    issue_kind: str | None = None
    summary_zh: str | None = None
    risk_reason_zh: str | None = None


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
    human_gates: tuple[AgentGraphHumanGateView, ...] = ()


class AgentGraphGateDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=1000)


class AgentGraphGateDecisionResponse(BaseModel):
    gate_id: UUID
    status: Literal["approved", "rejected"]
    graph_cursor: int
