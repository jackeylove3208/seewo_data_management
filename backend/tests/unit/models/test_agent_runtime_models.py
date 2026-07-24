from sqlalchemy import String

from app.models.agent_runtime import AgentCheckpointRecord


def test_checkpoint_input_hash_accepts_prefixed_sha256_contract() -> None:
    column_type = AgentCheckpointRecord.__table__.c.input_hash.type

    assert isinstance(column_type, String)
    assert column_type.length == 71
