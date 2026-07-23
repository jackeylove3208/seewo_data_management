from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentGraphHumanGateView(BaseModel):
    id: UUID
    kind: str
    status: str
    item_count: int


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
