"""Resolve stable, privacy-safe target source identities for task history."""

import posixpath
import re
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal
from uuid import UUID

from app.models.reconciliation import ReconciliationTask
from app.schemas.agent_api import AgentHistoryTargetSource

_KEY_PREFIX = "target-source-v1"
_MAX_DISPLAY_NAME_LENGTH = 255
_SAFE_CONFIGURATION_ID = re.compile(r"^[\w.-]{1,128}$")
TargetSourceKind = Literal["database", "local", "upload", "unknown"]


def resolve_history_target_sources(
    tasks: Sequence[ReconciliationTask],
    *,
    upload_names: Mapping[UUID, str],
) -> dict[UUID, AgentHistoryTargetSource]:
    tasks_by_id = {task.id: task for task in tasks}
    resolved: dict[UUID, AgentHistoryTargetSource] = {}

    def resolve(
        task: ReconciliationTask,
        *,
        visiting: frozenset[UUID] = frozenset(),
    ) -> AgentHistoryTargetSource:
        if task.id in resolved:
            return resolved[task.id]
        if task.id in visiting:
            return _unknown(task.tenant_id)
        if task.task_kind == "rollback":
            parent = (
                tasks_by_id.get(task.parent_task_id)
                if task.parent_task_id is not None
                else None
            )
            target_source = (
                resolve(parent, visiting=visiting | {task.id})
                if parent is not None
                else _unknown(task.tenant_id)
            )
        else:
            try:
                target_source = _resolve_sync_target(
                    task,
                    upload_names=upload_names,
                )
            except (TypeError, ValueError):
                target_source = _unknown(task.tenant_id)
        resolved[task.id] = target_source
        return target_source

    for item in tasks:
        resolve(item)
    return resolved


def _resolve_sync_target(
    task: ReconciliationTask,
    *,
    upload_names: Mapping[UUID, str],
) -> AgentHistoryTargetSource:
    intent = task.agent_intent
    target = intent.get("target") if isinstance(intent, dict) else None
    if not isinstance(target, dict):
        return _unknown(task.tenant_id)
    kind = target.get("kind")
    if kind == "database":
        configuration_id = _non_empty_string(target.get("configuration_id"))
        if configuration_id is None:
            return _unknown(task.tenant_id)
        return _identified(
            tenant_id=task.tenant_id,
            kind="database",
            identity=configuration_id,
            name=_safe_database_name(configuration_id),
        )
    if kind == "local":
        source_ref = _non_empty_string(target.get("source_ref"))
        if source_ref is None:
            return _unknown(task.tenant_id)
        normalized_ref = _normalize_local_source_ref(source_ref)
        if normalized_ref is None:
            return _unknown(task.tenant_id)
        return _identified(
            tenant_id=task.tenant_id,
            kind="local",
            identity=normalized_ref,
            name=_safe_filename(normalized_ref, fallback="希沃 CSV"),
        )
    if kind == "csv":
        try:
            upload_id = UUID(str(target.get("upload_id")))
        except (TypeError, ValueError):
            return _unknown(task.tenant_id)
        original_name = _non_empty_string(upload_names.get(upload_id))
        if original_name is None:
            return _unknown(task.tenant_id)
        return _identified(
            tenant_id=task.tenant_id,
            kind="upload",
            identity=str(upload_id),
            name=(
                "临时上传 · "
                f"{_safe_filename(original_name, fallback='未命名 CSV')}"
            ),
        )
    return _unknown(task.tenant_id)


def _identified(
    *,
    tenant_id: str,
    kind: TargetSourceKind,
    identity: str,
    name: str,
) -> AgentHistoryTargetSource:
    return AgentHistoryTargetSource(
        key=_stable_key(tenant_id, kind, identity),
        name=_bounded_display_name(name),
        kind=kind,
        identified=True,
    )


def _unknown(tenant_id: str) -> AgentHistoryTargetSource:
    return AgentHistoryTargetSource(
        key=_stable_key(tenant_id, "unknown", "history"),
        name="其他历史任务",
        kind="unknown",
        identified=False,
    )


def _stable_key(tenant_id: str, kind: str, identity: str) -> str:
    digest = sha256(
        "\0".join((tenant_id, kind, identity)).encode("utf-8")
    ).hexdigest()
    return f"{_KEY_PREFIX}:{digest}"


def _normalize_local_source_ref(source_ref: str) -> str | None:
    if "\x00" in source_ref or "://" in source_ref:
        return None
    if (
        PurePosixPath(source_ref).is_absolute()
        or PureWindowsPath(source_ref).is_absolute()
    ):
        return None
    normalized = posixpath.normpath(source_ref.replace("\\", "/"))
    if (
        normalized in {"", ".", ".."}
        or normalized.startswith("../")
    ):
        return None
    return normalized


def _safe_database_name(configuration_id: str) -> str:
    if _SAFE_CONFIGURATION_ID.fullmatch(configuration_id):
        return configuration_id
    return "希沃数据库"


def _safe_filename(value: str, *, fallback: str) -> str:
    filename = PurePosixPath(value.replace("\\", "/")).name.strip()
    return filename or fallback


def _bounded_display_name(value: str) -> str:
    if len(value) <= _MAX_DISPLAY_NAME_LENGTH:
        return value
    return f"{value[: _MAX_DISPLAY_NAME_LENGTH - 1]}…"


def _non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
