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
    ) -> AgentEvidenceManifestRecord:
        state = await self._require_state_at_cursor(graph_run_id, cursor)
        record = AgentEvidenceManifestRecord(
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
        )
        self.session.add(record)
        await self.session.flush()
        return record

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
