from sqlalchemy import String

from app.models.agent_runtime import AgentCheckpointRecord, AgentConversationRecord


def test_checkpoint_input_hash_accepts_prefixed_sha256_contract() -> None:
    column_type = AgentCheckpointRecord.__table__.c.input_hash.type

    assert isinstance(column_type, String)
    assert column_type.length == 71


def test_conversation_reset_key_and_single_active_operator_are_schema_invariants() -> None:
    table = AgentConversationRecord.__table__
    reset_key = table.c.reset_idempotency_key

    assert isinstance(reset_key.type, String)
    assert reset_key.type.length == 128
    assert reset_key.nullable is True
    assert any(
        constraint.name == "uq_agent_conversation_reset_key"
        for constraint in table.constraints
    )
    active_index = next(
        index
        for index in table.indexes
        if index.name == "uq_agent_conversations_active_operator"
    )
    assert active_index.unique is True
    assert [column.name for column in active_index.columns] == [
        "tenant_id",
        "created_by",
    ]
