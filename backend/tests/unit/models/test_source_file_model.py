from uuid import uuid4

from sqlalchemy import String

from app.models.snapshots import SourceFile


def test_source_file_storage_name_accepts_full_remote_filename_shape() -> None:
    storage_name = f"{uuid4().hex}-{'a' * 64}.csv"
    column_type = SourceFile.__table__.c.storage_name.type

    assert len(storage_name) == 101
    assert isinstance(column_type, String)
    assert column_type.length == 128
    assert SourceFile(storage_name=storage_name).storage_name == storage_name
