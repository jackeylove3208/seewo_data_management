from io import BytesIO
from pathlib import Path

import pytest

from app.ingestion.upload_storage import UploadStorage, UploadTooLarge


def test_upload_uses_generated_name_and_preserves_safe_metadata(tmp_path: Path) -> None:
    storage = UploadStorage(tmp_path, max_bytes=1024)

    saved = storage.save(BytesIO(b"id,name\n1,A\n"), "../../teachers.csv")

    assert saved.original_name == "teachers.csv"
    assert saved.storage_name != "teachers.csv"
    assert saved.storage_name.endswith(".csv")
    assert saved.path.parent == tmp_path
    assert saved.size_bytes == 12
    assert saved.sha256 == "22fc3efab41885f53144a7e648f0dae6da504b2835a09fb4585be00b0ce37f92"


def test_oversized_upload_removes_partial_file(tmp_path: Path) -> None:
    storage = UploadStorage(tmp_path, max_bytes=5)

    with pytest.raises(UploadTooLarge, match="5 bytes"):
        storage.save(BytesIO(b"123456"), "large.csv")

    assert list(tmp_path.iterdir()) == []


def test_upload_rejects_non_csv_extension(tmp_path: Path) -> None:
    storage = UploadStorage(tmp_path, max_bytes=1024)

    with pytest.raises(ValueError, match="CSV"):
        storage.save(BytesIO(b"content"), "teachers.xlsx")
