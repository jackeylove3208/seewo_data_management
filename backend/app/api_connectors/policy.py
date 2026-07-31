from datetime import UTC, datetime, timedelta

TASK_SCOPED_CONVERSATION_PROVIDER_IDS = frozenset({"dingtalk"})
TASK_EPHEMERAL_CREDENTIAL_TTL = timedelta(hours=24)


def uses_task_scoped_conversation_credentials(provider_id: str) -> bool:
    return provider_id in TASK_SCOPED_CONVERSATION_PROVIDER_IDS


def task_ephemeral_credentials_expired(
    created_at: datetime,
    *,
    now: datetime | None = None,
) -> bool:
    normalized_created_at = (
        created_at.replace(tzinfo=UTC)
        if created_at.tzinfo is None
        else created_at
    )
    return normalized_created_at <= (
        now or datetime.now(UTC)
    ) - TASK_EPHEMERAL_CREDENTIAL_TTL
