TASK_SCOPED_CONVERSATION_PROVIDER_IDS = frozenset({"dingtalk"})


def uses_task_scoped_conversation_credentials(provider_id: str) -> bool:
    return provider_id in TASK_SCOPED_CONVERSATION_PROVIDER_IDS
