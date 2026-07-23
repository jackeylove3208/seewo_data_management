"""Structured, bounded and privacy-safe Agent runtime telemetry."""

import json
import logging
import re
from collections import Counter
from collections.abc import Mapping
from threading import Lock
from typing import Literal
from uuid import UUID

logger = logging.getLogger(__name__)

AgentMetricName = Literal[
    "phase_started",
    "phase_completed",
    "phase_failed",
    "lock_observed",
    "connector_failed",
    "model_attempt",
    "analysis_batch",
    "approval_decided",
    "mutation_completed",
    "report_completed",
    "rollback_completed",
]
_PHONE_LIKE = re.compile(r"(?<!\d)1\d{10}(?!\d)")


class AgentObservability:
    """Emit only fixed telemetry dimensions; row contents cannot enter this API."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._lock = Lock()

    def observe(
        self,
        event: AgentMetricName,
        *,
        task_id: UUID | None = None,
        run_id: UUID | None = None,
        owner_task_id: UUID | None = None,
        phase: str | None = None,
        connector_kind: str | None = None,
        outcome: str | None = None,
        error_code: str | None = None,
        duration_ms: float | None = None,
        queue_age_ms: float | None = None,
        lock_age_ms: float | None = None,
        batch_size: int | None = None,
        retry_count: int | None = None,
        approval_count: int | None = None,
        mutation_count: int | None = None,
    ) -> None:
        payload: dict[str, object] = {"event": event}
        values: Mapping[str, object | None] = {
            "task_id": str(task_id) if task_id is not None else None,
            "run_id": str(run_id) if run_id is not None else None,
            "owner_task_id": str(owner_task_id) if owner_task_id is not None else None,
            "phase": phase,
            "connector_kind": connector_kind,
            "outcome": outcome,
            "error_code": error_code,
            "duration_ms": round(duration_ms, 3) if duration_ms is not None else None,
            "queue_age_ms": round(queue_age_ms, 3) if queue_age_ms is not None else None,
            "lock_age_ms": round(lock_age_ms, 3) if lock_age_ms is not None else None,
            "batch_size": batch_size,
            "retry_count": retry_count,
            "approval_count": approval_count,
            "mutation_count": mutation_count,
        }
        for key, value in values.items():
            if value is None:
                continue
            if (
                key not in {"task_id", "run_id", "owner_task_id"}
                and isinstance(value, str)
                and _PHONE_LIKE.search(value)
            ):
                raise ValueError("sensitive value rejected from Agent telemetry")
            payload[key] = value
        with self._lock:
            self._counts[event] += 1
        logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)


agent_observability = AgentObservability()
