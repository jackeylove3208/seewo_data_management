import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_analysis import AgentInputRecord
from app.models.agent_runtime import AgentRunRecord
from app.models.api_connectors import (
    AgentExternalIdentityBindingRecord,
    ApiConnectionRecord,
)
from app.models.reconciliation import ReconciliationTask
from app.repositories.agent_external_identity import (
    AgentExternalIdentityRepository,
    ExternalIdentityRepositoryConflict,
)
from app.schemas.agent_ingestion import AgentEntityKind


class ExternalIdentityBindingConflict(ValueError):
    pass


class ExternalIdentityBindingNotFound(LookupError):
    pass


class ExternalIdentityBindingValidation(ValueError):
    pass


class AgentExternalIdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = AgentExternalIdentityRepository(session)

    async def list(
        self,
        *,
        tenant_id: str,
    ) -> tuple[AgentExternalIdentityBindingRecord, ...]:
        return await self._repository.list_for_tenant(tenant_id=tenant_id)

    async def confirm(
        self,
        *,
        tenant_id: str,
        operator_id: str,
        run_id: UUID,
        connection_id: UUID,
        entity_kind: str,
        authority_stable_locator: str,
        target_connector_id: str,
        target_stable_locator: str,
    ) -> AgentExternalIdentityBindingRecord:
        try:
            selected_kind = AgentEntityKind(entity_kind)
        except ValueError as error:
            raise ExternalIdentityBindingValidation(
                "external identity entity kind is invalid"
            ) from error
        run = await self._session.scalar(
            select(AgentRunRecord).where(
                AgentRunRecord.id == run_id,
                AgentRunRecord.tenant_id == tenant_id,
            )
        )
        if run is None:
            raise ExternalIdentityBindingValidation(
                "external identity run is unavailable"
            )
        task = await self._session.scalar(
            select(ReconciliationTask).where(
                ReconciliationTask.id == run.task_id,
                ReconciliationTask.tenant_id == tenant_id,
            )
        )
        connection = await self._session.scalar(
            select(ApiConnectionRecord).where(
                ApiConnectionRecord.id == connection_id,
                ApiConnectionRecord.tenant_id == tenant_id,
            )
        )
        if (
            task is None
            or connection is None
            or connection.state != "active"
            or run.ingestion_contract_version != "source-ingestion-v3"
        ):
            raise ExternalIdentityBindingValidation(
                "external identity scope is unavailable"
            )
        source_selection, target_selection = _task_selections(task)
        if (
            source_selection.get("kind") != "api"
            or source_selection.get("configuration_id") != str(connection_id)
            or target_selection.get("kind") != "database"
            or target_selection.get("configuration_id") != target_connector_id
        ):
            raise ExternalIdentityBindingValidation(
                "external identity scope does not match the run"
            )
        authority = await self._input(
            run=run,
            tenant_id=tenant_id,
            role="authoritative",
            locator=authority_stable_locator,
        )
        target = await self._input(
            run=run,
            tenant_id=tenant_id,
            role="target",
            locator=target_stable_locator,
        )
        expected_authority_prefix = (
            f"api:{connection_id}:{selected_kind.value}:"
        )
        expected_target_prefix = f"database:{target_connector_id}:"
        if (
            authority.entity_kind != selected_kind.value
            or target.entity_kind != selected_kind.value
            or not authority.stable_locator.startswith(expected_authority_prefix)
            or not target.stable_locator.startswith(expected_target_prefix)
        ):
            raise ExternalIdentityBindingValidation(
                "external identity locators do not match their scope"
            )
        evidence = {
            "run_id": str(run.id),
            "task_id": str(run.task_id),
            "connection_id": str(connection_id),
            "provider_id": connection.provider_id,
            "entity_kind": selected_kind.value,
            "authority_input_hash": authority.input_hash,
            "target_connector_id": target_connector_id,
            "target_input_hash": target.input_hash,
        }
        evidence_hash = hashlib.sha256(
            json.dumps(
                evidence,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        try:
            return await self._repository.create_active(
                tenant_id=tenant_id,
                provider_id=connection.provider_id,
                connection_id=connection_id,
                entity_kind=selected_kind.value,
                authority_stable_locator=authority.stable_locator,
                target_connector_id=target_connector_id,
                target_stable_locator=target.stable_locator,
                confirmed_by=operator_id,
                evidence_hash=evidence_hash,
            )
        except ExternalIdentityRepositoryConflict as error:
            raise ExternalIdentityBindingConflict(str(error)) from error

    async def revoke(
        self,
        *,
        tenant_id: str,
        operator_id: str,
        binding_id: UUID,
    ) -> AgentExternalIdentityBindingRecord:
        binding = await self._repository.revoke(
            tenant_id=tenant_id,
            binding_id=binding_id,
            revoked_by=operator_id,
        )
        if binding is None:
            raise ExternalIdentityBindingNotFound(
                "external identity binding was not found"
            )
        return binding

    async def _input(
        self,
        *,
        run: AgentRunRecord,
        tenant_id: str,
        role: str,
        locator: str,
    ) -> AgentInputRecord:
        record = await self._session.scalar(
            select(AgentInputRecord).where(
                AgentInputRecord.run_id == run.id,
                AgentInputRecord.task_id == run.task_id,
                AgentInputRecord.tenant_id == tenant_id,
                AgentInputRecord.source_role == role,
                AgentInputRecord.stable_locator == locator,
            )
        )
        if record is None:
            raise ExternalIdentityBindingValidation(
                f"external identity {role} locator is unavailable"
            )
        return record


def _task_selections(
    task: ReconciliationTask,
) -> tuple[dict[str, object], dict[str, object]]:
    intent = task.agent_intent
    if not isinstance(intent, dict):
        raise ExternalIdentityBindingValidation(
            "external identity task intent is unavailable"
        )
    source = intent.get("source")
    target = intent.get("target")
    if not isinstance(source, dict) or not isinstance(target, dict):
        raise ExternalIdentityBindingValidation(
            "external identity task selections are unavailable"
        )
    return source, target
