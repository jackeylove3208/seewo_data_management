import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.local_sources.publisher import (
    LocalCsvPublicationConflict,
    LocalCsvPublisher,
    copy_managed_initial_version,
)
from app.local_sources.service import LocalSourceService


def _hash(path: Path) -> str:
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def _publisher(tmp_path: Path, destination: Path) -> LocalCsvPublisher:
    read_root = tmp_path / "sources"
    assert destination.is_relative_to(read_root)
    return LocalCsvPublisher(
        LocalSourceService(
            Settings(
                agent_local_read_roots=(read_root,),
                agent_local_write_roots=(read_root / "seewo",),
                _env_file=None,
            )
        )
    )


def test_copy_managed_initial_version_does_not_point_at_mutable_destination(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "sources" / "seewo" / "target.csv"
    destination.parent.mkdir(parents=True)
    destination.write_text("编号,电话\n001,1000\n", encoding="utf-8")
    expected_hash = _hash(destination)

    managed = copy_managed_initial_version(
        destination,
        output_root=tmp_path / "managed",
        task_id=uuid4(),
        expected_sha256=expected_hash,
    )

    assert managed.path != destination
    assert managed.path.is_relative_to(tmp_path / "managed")
    assert managed.sha256 == expected_hash
    destination.write_text("编号,电话\n001,2000\n", encoding="utf-8")
    assert managed.path.read_text(encoding="utf-8") == "编号,电话\n001,1000\n"


def test_atomic_publication_replaces_destination_and_reads_back_hash(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "sources" / "seewo" / "target.csv"
    destination.parent.mkdir(parents=True)
    destination.write_text("编号,电话\n001,1000\n", encoding="utf-8")
    managed = tmp_path / "managed.csv"
    managed.write_text("编号,电话\n001,2000\n", encoding="utf-8")
    expected = _hash(destination)

    result = _publisher(tmp_path, destination).publish(
        source_ref="seewo/target.csv",
        managed_version_path=managed,
        expected_destination_sha256=expected,
        target_version_id=uuid4(),
    )

    assert result.status == "published"
    assert result.source_ref == "seewo/target.csv"
    assert result.published_sha256 == _hash(managed)
    assert _hash(destination) == _hash(managed)
    assert not tuple(destination.parent.glob(".agent-publish-*.tmp"))


def test_publication_rejects_external_hash_change_without_overwriting(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "sources" / "seewo" / "target.csv"
    destination.parent.mkdir(parents=True)
    destination.write_text("编号,电话\n001,1000\n", encoding="utf-8")
    expected = _hash(destination)
    destination.write_text("编号,电话\n001,外部修改\n", encoding="utf-8")
    external = destination.read_bytes()
    managed = tmp_path / "managed.csv"
    managed.write_text("编号,电话\n001,2000\n", encoding="utf-8")

    with pytest.raises(LocalCsvPublicationConflict, match="destination_hash_conflict"):
        _publisher(tmp_path, destination).publish(
            source_ref="seewo/target.csv",
            managed_version_path=managed,
            expected_destination_sha256=expected,
            target_version_id=uuid4(),
        )

    assert destination.read_bytes() == external


def test_repeating_same_publication_is_idempotent(tmp_path: Path) -> None:
    destination = tmp_path / "sources" / "seewo" / "target.csv"
    destination.parent.mkdir(parents=True)
    destination.write_text("编号,电话\n001,1000\n", encoding="utf-8")
    expected = _hash(destination)
    managed = tmp_path / "managed.csv"
    managed.write_text("编号,电话\n001,2000\n", encoding="utf-8")
    publisher = _publisher(tmp_path, destination)
    version_id = uuid4()

    first = publisher.publish(
        source_ref="seewo/target.csv",
        managed_version_path=managed,
        expected_destination_sha256=expected,
        target_version_id=version_id,
    )
    second = publisher.publish(
        source_ref="seewo/target.csv",
        managed_version_path=managed,
        expected_destination_sha256=expected,
        target_version_id=version_id,
    )

    assert first.status == "published"
    assert second.status == "already_published"
    assert first.published_sha256 == second.published_sha256
