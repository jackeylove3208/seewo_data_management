from uuid import uuid4

from app.models.remote_sources import RemoteSourceRecord


def test_remote_source_record_starts_registered_without_task_or_file() -> None:
    conversation_id = uuid4()
    record = RemoteSourceRecord(
        tenant_id="school-1",
        created_by="operator-1",
        conversation_id=conversation_id,
        original_url="https://data.example.test/roster.csv?private=value",
        display_origin="data.example.test",
    )

    assert record.conversation_id == conversation_id
    assert RemoteSourceRecord.__table__.c.state.default.arg == "registered"
    assert record.task_id is None
    assert record.source_file_id is None
    assert record.safe_problem_code is None
