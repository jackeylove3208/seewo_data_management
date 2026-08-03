from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_graph.contracts import (
    AllowedActionSetV1,
    CandidateActionEvaluationV1,
    SupervisorDecisionV1,
)
from app.models.agent_graph import (
    AgentEvidenceManifestRecord,
    AgentGraphCandidateSetRecord,
    AgentGraphRunRecord,
    AgentGraphTransitionRecord,
    AgentHumanGateRecord,
    AgentSubAgentInvocationRecord,
    AgentSupervisorDecisionRecord,
    AgentToolCallRecord,
)
from app.models.agent_runtime import AgentRunRecord


class GraphCursorConflict(RuntimeError):
    pass


class GraphFactConflict(RuntimeError):
    pass


class AgentGraphNotFound(LookupError):
    pass


class AgentGraphRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run_state(
        self,
        *,
        run_id: UUID,
        graph_version: str,
        initial_node: str,
    ) -> AgentGraphRunRecord:
        existing = await self.session.scalar(
            select(AgentGraphRunRecord).where(AgentGraphRunRecord.run_id == run_id)
        )
        if existing is not None:
            return existing
        run = await self.session.get(AgentRunRecord, run_id)
        if run is None or run.workflow_version != "agent-graph-v1":
            raise AgentGraphNotFound(f"agent-graph-v1 run not found: {run_id}")
        record = AgentGraphRunRecord(
            run_id=run.id,
            tenant_id=run.tenant_id,
            graph_version=graph_version,
            current_node=initial_node,
            cursor=0,
            status="running",
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_run_state(
        self,
        graph_run_id: UUID,
        *,
        for_update: bool = False,
    ) -> AgentGraphRunRecord | None:
        statement = select(AgentGraphRunRecord).where(
            AgentGraphRunRecord.id == graph_run_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(
            AgentGraphRunRecord | None,
            await self.session.scalar(statement),
        )

    async def get_run_state_for_agent_run(
        self,
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> AgentGraphRunRecord | None:
        statement = select(AgentGraphRunRecord).where(
            AgentGraphRunRecord.run_id == run_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(
            AgentGraphRunRecord | None,
            await self.session.scalar(statement),
        )

    async def record_candidate_set(
        self,
        *,
        graph_run_id: UUID,
        cursor: int,
        candidate_evaluations: Sequence[CandidateActionEvaluationV1],
        action_set: AllowedActionSetV1,
    ) -> AgentGraphCandidateSetRecord:
        state = await self._require_state(graph_run_id)
        if state.cursor != cursor:
            raise GraphCursorConflict("candidate set cursor is stale")
        existing = await self.session.scalar(
            select(AgentGraphCandidateSetRecord).where(
                AgentGraphCandidateSetRecord.graph_run_id == graph_run_id,
                AgentGraphCandidateSetRecord.cursor == cursor,
            )
        )
        if existing is not None:
            if existing.action_set_hash != action_set.action_set_hash:
                raise GraphFactConflict("candidate set already exists with different content")
            return existing
        record = AgentGraphCandidateSetRecord(
            graph_run_id=graph_run_id,
            tenant_id=state.tenant_id,
            cursor=cursor,
            action_set_hash=action_set.action_set_hash,
            candidate_evaluations=[
                item.model_dump(mode="json") for item in candidate_evaluations
            ],
            allowed_actions=[
                item.model_dump(mode="json") for item in action_set.allowed_actions
            ],
            single_action_reason_code=(
                action_set.single_action_reason_code.value
                if action_set.single_action_reason_code is not None
                else None
            ),
            excluded_action_summaries=[
                item.model_dump(mode="json")
                for item in action_set.excluded_action_summaries
            ],
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_candidate_set(
        self,
        *,
        graph_run_id: UUID,
        cursor: int,
    ) -> AgentGraphCandidateSetRecord | None:
        return cast(
            AgentGraphCandidateSetRecord | None,
            await self.session.scalar(
                select(AgentGraphCandidateSetRecord).where(
                    AgentGraphCandidateSetRecord.graph_run_id == graph_run_id,
                    AgentGraphCandidateSetRecord.cursor == cursor,
                )
            ),
        )

    async def get_supervisor_decision(
        self,
        *,
        candidate_set_id: UUID,
    ) -> AgentSupervisorDecisionRecord | None:
        return cast(
            AgentSupervisorDecisionRecord | None,
            await self.session.scalar(
                select(AgentSupervisorDecisionRecord).where(
                    AgentSupervisorDecisionRecord.candidate_set_id
                    == candidate_set_id
                )
            ),
        )

    async def record_decision(
        self,
        *,
        candidate_set_id: UUID,
        decision: SupervisorDecisionV1,
        model_provenance: dict[str, Any],
    ) -> AgentSupervisorDecisionRecord:
        candidate_set = await self.session.get(
            AgentGraphCandidateSetRecord,
            candidate_set_id,
        )
        if candidate_set is None:
            raise AgentGraphNotFound(f"candidate set not found: {candidate_set_id}")
        existing = await self.session.scalar(
            select(AgentSupervisorDecisionRecord).where(
                AgentSupervisorDecisionRecord.candidate_set_id == candidate_set_id
            )
        )
        if existing is not None:
            raise GraphFactConflict("Supervisor decision already exists")
        allowed_action_ids = {
            str(item["action_id"]) for item in candidate_set.allowed_actions
        }
        if decision.action_id not in allowed_action_ids:
            raise GraphFactConflict("Supervisor decision action is outside candidate set")
        record = AgentSupervisorDecisionRecord(
            candidate_set_id=candidate_set.id,
            graph_run_id=candidate_set.graph_run_id,
            tenant_id=candidate_set.tenant_id,
            cursor=candidate_set.cursor,
            selected_action_id=decision.action_id,
            decision=decision.model_dump(mode="json"),
            model_provenance=model_provenance,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def record_transition(
        self,
        graph_run_id: UUID,
        *,
        expected_cursor: int,
        from_node: str,
        to_node: str,
        action_id: str,
        guard_results: dict[str, Any],
        fencing_token: int,
    ) -> AgentGraphTransitionRecord:
        state = await self._require_state(graph_run_id, for_update=True)
        if state.cursor != expected_cursor or state.current_node != from_node:
            raise GraphCursorConflict("graph transition cursor or node is stale")
        next_cursor = expected_cursor + 1
        existing = await self.session.scalar(
            select(AgentGraphTransitionRecord.id).where(
                AgentGraphTransitionRecord.graph_run_id == graph_run_id,
                AgentGraphTransitionRecord.cursor == next_cursor,
            )
        )
        if existing is not None:
            raise GraphFactConflict("graph transition fact already exists")
        record = AgentGraphTransitionRecord(
            graph_run_id=state.id,
            tenant_id=state.tenant_id,
            cursor=next_cursor,
            from_node=from_node,
            to_node=to_node,
            action_id=action_id,
            guard_results=guard_results,
            fencing_token=fencing_token,
        )
        self.session.add(record)
        state.cursor = next_cursor
        state.current_node = to_node
        state.updated_at = datetime.now(UTC)
        await self.session.flush()
        return record

    async def list_transitions(
        self,
        graph_run_id: UUID,
    ) -> tuple[AgentGraphTransitionRecord, ...]:
        return tuple(
            await self.session.scalars(
                select(AgentGraphTransitionRecord)
                .where(AgentGraphTransitionRecord.graph_run_id == graph_run_id)
                .order_by(AgentGraphTransitionRecord.cursor)
            )
        )

    async def record_manifest(
        self,
        *,
        graph_run_id: UUID,
        cursor: int,
        graph_node: str,
        action_id: str,
        manifest: dict[str, Any],
        content_hash: str,
        record_id: UUID | None = None,
    ) -> AgentEvidenceManifestRecord:
        state = await self._require_state_at_cursor(graph_run_id, cursor)
        record = AgentEvidenceManifestRecord(
            id=record_id,
            graph_run_id=state.id,
            tenant_id=state.tenant_id,
            cursor=cursor,
            graph_node=graph_node,
            action_id=action_id,
            manifest=manifest,
            content_hash=content_hash,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def record_invocation(
        self,
        *,
        graph_run_id: UUID,
        cursor: int,
        action_id: str,
        evidence_manifest_id: UUID,
        execution_mode: str,
        skill_name: str,
        skill_version: str,
        schema_version: str,
        attempt: int,
        status: str,
        input_hash: str,
        output_hash: str,
        model_provenance: dict[str, Any],
    ) -> AgentSubAgentInvocationRecord:
        state = await self._require_state_at_cursor(graph_run_id, cursor)
        manifest = await self.session.get(
            AgentEvidenceManifestRecord,
            evidence_manifest_id,
        )
        if manifest is None or manifest.graph_run_id != state.id:
            raise GraphFactConflict("invocation manifest belongs to another graph run")
        existing = await self.session.scalar(
            select(AgentSubAgentInvocationRecord).where(
                AgentSubAgentInvocationRecord.graph_run_id == state.id,
                AgentSubAgentInvocationRecord.cursor == cursor,
                AgentSubAgentInvocationRecord.action_id == action_id,
                AgentSubAgentInvocationRecord.skill_name == skill_name,
                AgentSubAgentInvocationRecord.attempt == attempt,
            )
        )
        if existing is not None:
            if (
                existing.evidence_manifest_id != manifest.id
                or existing.execution_mode != execution_mode
                or existing.skill_version != skill_version
                or existing.schema_version != schema_version
                or existing.status != status
                or existing.input_hash != input_hash
                or existing.output_hash != output_hash
                or existing.model_provenance != model_provenance
            ):
                raise GraphFactConflict(
                    "sub-agent invocation replay changed frozen content"
                )
            return existing
        if attempt > 1:
            state.retry_count += 1
        record = AgentSubAgentInvocationRecord(
            graph_run_id=state.id,
            tenant_id=state.tenant_id,
            cursor=cursor,
            action_id=action_id,
            evidence_manifest_id=manifest.id,
            execution_mode=execution_mode,
            skill_name=skill_name,
            skill_version=skill_version,
            schema_version=schema_version,
            attempt=attempt,
            status=status,
            input_hash=input_hash,
            output_hash=output_hash,
            output_payload={},
            model_provenance=model_provenance,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def record_tool_call(
        self,
        *,
        invocation_id: UUID,
        tool_name: str,
        arguments_hash: str,
        result_hash: str,
        authorized: bool,
        status: str,
        trace_id: str,
        model_turn: int | None = None,
        replay_descriptor: dict[str, Any] | None = None,
    ) -> AgentToolCallRecord:
        invocation = await self.session.get(
            AgentSubAgentInvocationRecord,
            invocation_id,
        )
        if invocation is None:
            raise AgentGraphNotFound(f"sub-agent invocation not found: {invocation_id}")
        next_sequence = (
            await self.session.scalar(
                select(func.max(AgentToolCallRecord.sequence)).where(
                    AgentToolCallRecord.invocation_id == invocation_id
                )
            )
            or 0
        ) + 1
        record = AgentToolCallRecord(
            invocation_id=invocation.id,
            tenant_id=invocation.tenant_id,
            sequence=next_sequence,
            tool_name=tool_name,
            arguments_hash=arguments_hash,
            result_hash=result_hash,
            authorized=authorized,
            status=status,
            trace_id=trace_id,
            model_turn=model_turn,
            replay_descriptor=replay_descriptor,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_replayable_tool_calls(
        self,
        *,
        graph_run_id: UUID,
        cursor: int,
        action_id: str,
        skill_name: str,
        input_hash: str,
    ) -> tuple[AgentToolCallRecord, ...]:
        statement = (
            select(AgentToolCallRecord)
            .join(AgentSubAgentInvocationRecord)
            .where(
                AgentSubAgentInvocationRecord.graph_run_id == graph_run_id,
                AgentSubAgentInvocationRecord.cursor == cursor,
                AgentSubAgentInvocationRecord.action_id == action_id,
                AgentSubAgentInvocationRecord.skill_name == skill_name,
                AgentSubAgentInvocationRecord.input_hash == input_hash,
                AgentToolCallRecord.authorized.is_(True),
                AgentToolCallRecord.status == "completed",
                AgentToolCallRecord.replay_descriptor.is_not(None),
            )
            .order_by(
                AgentSubAgentInvocationRecord.attempt,
                AgentToolCallRecord.sequence,
            )
        )
        return tuple(await self.session.scalars(statement))

    async def finalize_invocation(
        self,
        invocation_id: UUID,
        *,
        status: str,
        output_hash: str,
        model_provenance: dict[str, Any],
        output_payload: dict[str, Any] | None = None,
    ) -> AgentSubAgentInvocationRecord:
        if status not in {"completed", "failed"}:
            raise ValueError("sub-agent invocation status must be terminal")
        record = await self.session.get(
            AgentSubAgentInvocationRecord,
            invocation_id,
        )
        if record is None:
            raise AgentGraphNotFound(f"sub-agent invocation not found: {invocation_id}")
        if record.status != "running":
            raise GraphFactConflict("sub-agent invocation is already terminal")
        record.status = status
        record.output_hash = output_hash
        record.output_payload = output_payload or {}
        record.model_provenance = model_provenance
        await self.session.flush()
        return record

    async def find_completed_invocation(
        self,
        *,
        graph_run_id: UUID,
        cursor: int,
        action_id: str,
        skill_name: str,
        input_hash: str,
    ) -> AgentSubAgentInvocationRecord | None:
        return cast(
            AgentSubAgentInvocationRecord | None,
            await self.session.scalar(
                select(AgentSubAgentInvocationRecord)
                .where(
                    AgentSubAgentInvocationRecord.graph_run_id == graph_run_id,
                    AgentSubAgentInvocationRecord.cursor == cursor,
                    AgentSubAgentInvocationRecord.action_id == action_id,
                    AgentSubAgentInvocationRecord.skill_name == skill_name,
                    AgentSubAgentInvocationRecord.input_hash == input_hash,
                    AgentSubAgentInvocationRecord.status == "completed",
                )
                .order_by(AgentSubAgentInvocationRecord.attempt.desc())
            )
        )

    async def prepare_invocation_resume(
        self,
        *,
        graph_run_id: UUID,
        cursor: int,
        action_id: str,
        skill_name: str,
        input_hash: str,
    ) -> tuple[
        int,
        tuple[dict[str, str], ...],
        tuple[str, ...],
    ]:
        records = tuple(
            await self.session.scalars(
                select(AgentSubAgentInvocationRecord)
                .where(
                    AgentSubAgentInvocationRecord.graph_run_id == graph_run_id,
                    AgentSubAgentInvocationRecord.cursor == cursor,
                    AgentSubAgentInvocationRecord.action_id == action_id,
                    AgentSubAgentInvocationRecord.skill_name == skill_name,
                    AgentSubAgentInvocationRecord.input_hash == input_hash,
                )
                .order_by(AgentSubAgentInvocationRecord.attempt)
            )
        )
        repair_feedback: tuple[dict[str, str], ...] = ()
        failure_categories: list[str] = []
        for record in records:
            candidate_feedback = _safe_repair_feedback(
                record.model_provenance.get("repair_feedback")
            )
            if candidate_feedback:
                repair_feedback = candidate_feedback
            safe_error_code = record.model_provenance.get("safe_error_code")
            if (
                isinstance(safe_error_code, str)
                and safe_error_code
                and safe_error_code not in failure_categories
            ):
                failure_categories.append(safe_error_code)
            if record.status != "running":
                continue
            record.status = "failed"
            record.model_provenance = {
                **record.model_provenance,
                "safe_error_code": "invocation_interrupted",
                "attempt": record.attempt,
                "request_ids": [],
            }
            if "invocation_interrupted" not in failure_categories:
                failure_categories.append("invocation_interrupted")
        if records:
            await self.session.flush()
        return (
            max((record.attempt for record in records), default=0) + 1,
            repair_feedback,
            tuple(failure_categories),
        )

    async def record_human_gate(
        self,
        *,
        graph_run_id: UUID,
        cursor: int,
        gate_kind: str,
        member_ids: Sequence[str],
        content_hash: str,
        status: str,
    ) -> AgentHumanGateRecord:
        state = await self._require_state_at_cursor(graph_run_id, cursor)
        existing = await self.session.scalar(
            select(AgentHumanGateRecord).where(
                AgentHumanGateRecord.graph_run_id == state.id,
                AgentHumanGateRecord.cursor == cursor,
                AgentHumanGateRecord.gate_kind == gate_kind,
                AgentHumanGateRecord.content_hash == content_hash,
            )
        )
        if existing is not None:
            if (
                existing.member_ids != list(member_ids)
                or existing.status != status
            ):
                raise GraphFactConflict("human gate replay changed frozen content")
            return existing
        record = AgentHumanGateRecord(
            graph_run_id=state.id,
            tenant_id=state.tenant_id,
            cursor=cursor,
            gate_kind=gate_kind,
            member_ids=list(member_ids),
            content_hash=content_hash,
            status=status,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def _require_state(
        self,
        graph_run_id: UUID,
        *,
        for_update: bool = False,
    ) -> AgentGraphRunRecord:
        state = await self.get_run_state(graph_run_id, for_update=for_update)
        if state is None:
            raise AgentGraphNotFound(f"Agent graph run not found: {graph_run_id}")
        return state

    async def _require_state_at_cursor(
        self,
        graph_run_id: UUID,
        cursor: int,
    ) -> AgentGraphRunRecord:
        state = await self._require_state(graph_run_id)
        if state.cursor != cursor:
            raise GraphCursorConflict("graph fact cursor is stale")
        return state


def _safe_repair_feedback(value: object) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        return ()
    feedback: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            return ()
        path = item.get("path")
        error_code = item.get("code")
        error_type = item.get("type")
        if not isinstance(path, str):
            return ()
        if isinstance(error_code, str):
            feedback.append({"path": path, "code": error_code})
        elif isinstance(error_type, str):
            feedback.append({"path": path, "type": error_type})
        else:
            return ()
    return tuple(feedback)
