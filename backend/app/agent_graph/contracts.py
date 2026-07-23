from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictGraphContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SingleActionReasonCode(StrEnum):
    SAFETY_MANDATORY = "safety_mandatory"
    HUMAN_GATE_REQUIRED = "human_gate_required"
    ONLY_GUARD_SATISFIED = "only_guard_satisfied"
    TERMINATION_REQUESTED = "termination_requested"
    TERMINALIZATION_REQUIRED = "terminalization_required"


class AllowedActionV1(StrictGraphContract):
    action_id: str = Field(min_length=1, max_length=128)
    kind: Literal[
        "dispatch_sub_agent",
        "run_deterministic",
        "wait_human",
        "terminate",
    ]
    sub_agent: str | None = Field(default=None, min_length=1, max_length=128)
    resource_ids: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    risk: Literal["low", "medium", "high"]
    requires_human: bool
    successor_node: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_dispatch_target(self) -> "AllowedActionV1":
        if self.kind == "dispatch_sub_agent" and self.sub_agent is None:
            raise ValueError("dispatch_sub_agent requires sub_agent")
        if self.kind != "dispatch_sub_agent" and self.sub_agent is not None:
            raise ValueError("only dispatch_sub_agent may name a sub_agent")
        if len(set(self.resource_ids)) != len(self.resource_ids):
            raise ValueError("resource_ids must be unique")
        if len(set(self.required_evidence)) != len(self.required_evidence):
            raise ValueError("required_evidence must be unique")
        return self


class CandidateActionEvaluationV1(StrictGraphContract):
    action: AllowedActionV1
    passed: bool
    rejected_guard_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_guard_outcome(self) -> "CandidateActionEvaluationV1":
        if self.passed and self.rejected_guard_codes:
            raise ValueError("passed action cannot contain rejected guards")
        if not self.passed and not self.rejected_guard_codes:
            raise ValueError("rejected action requires a guard code")
        if len(set(self.rejected_guard_codes)) != len(self.rejected_guard_codes):
            raise ValueError("rejected guard codes must be unique")
        return self


class ExcludedActionSummaryV1(StrictGraphContract):
    action_id: str = Field(min_length=1, max_length=128)
    rejected_guard_codes: tuple[str, ...] = Field(min_length=1)


class AllowedActionSetV1(StrictGraphContract):
    allowed_actions: tuple[AllowedActionV1, ...] = Field(min_length=1)
    action_set_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    single_action_reason_code: SingleActionReasonCode | None = None
    excluded_action_summaries: tuple[ExcludedActionSummaryV1, ...] = ()

    @model_validator(mode="after")
    def validate_choice_shape(self) -> "AllowedActionSetV1":
        action_ids = tuple(action.action_id for action in self.allowed_actions)
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("allowed action IDs must be unique")
        if len(self.allowed_actions) == 1 and self.single_action_reason_code is None:
            raise ValueError("single_action_reason_code is required for one allowed action")
        if len(self.allowed_actions) > 1 and self.single_action_reason_code is not None:
            raise ValueError("single_action_reason_code is invalid for multiple allowed actions")
        excluded_ids = tuple(item.action_id for item in self.excluded_action_summaries)
        if len(set(excluded_ids)) != len(excluded_ids):
            raise ValueError("excluded action IDs must be unique")
        if set(action_ids).intersection(excluded_ids):
            raise ValueError("an action cannot be both allowed and excluded")
        return self


class UnselectedActionReasonV1(StrictGraphContract):
    action_id: str = Field(min_length=1, max_length=128)
    reason_zh: str = Field(min_length=1, max_length=1000)


class SupervisorDecisionV1(StrictGraphContract):
    action_id: str = Field(min_length=1, max_length=128)
    reason_zh: str = Field(min_length=1, max_length=1000)
    expected_result: str = Field(min_length=1, max_length=256)
    observed_blockers: tuple[str, ...] = ()
    risk_notes_zh: tuple[str, ...] = ()
    why_not_other_actions_zh: tuple[UnselectedActionReasonV1, ...] = ()
    operator_message_zh: str | None = Field(default=None, max_length=1000)


class SupervisorContextV1(StrictGraphContract):
    tenant_ref: str = Field(min_length=1, max_length=256)
    task_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    run_kind: Literal["sync", "rollback"]
    workflow_version: Literal["agent-graph-v1"]
    graph_version: str = Field(min_length=1, max_length=128)
    current_node: str = Field(min_length=1, max_length=128)
    graph_cursor: int = Field(ge=0)
    status: str = Field(min_length=1, max_length=64)
    action_set: AllowedActionSetV1
    active_blockers: tuple[str, ...] = ()
    completed_action_summary: tuple[str, ...] = ()
    pending_work_summary: tuple[str, ...] = ()
    evidence_manifest_refs: tuple[str, ...] = ()
    human_gate_summary: tuple[str, ...] = ()
    connector_capability_summary: tuple[str, ...] = ()
    retry_and_replan_budget: int = Field(default=3, ge=0, le=3)
    termination_requested: bool = False

    @property
    def allowed_actions(self) -> tuple[AllowedActionV1, ...]:
        return self.action_set.allowed_actions

    @property
    def action_set_hash(self) -> str:
        return self.action_set.action_set_hash
