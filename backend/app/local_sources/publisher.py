"""Atomic publication boundary for deployment-authorized local CSV targets."""

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from app.local_sources.service import LocalSourceService


class LocalCsvPublicationConflict(ValueError):
    """A safe, stable publication conflict that must not overwrite the target."""


@dataclass(frozen=True)
class ManagedInitialVersion:
    path: Path
    sha256: str


@dataclass(frozen=True)
class LocalCsvPublicationResult:
    source_ref: str
    target_version_id: UUID
    expected_destination_sha256: str
    published_sha256: str
    status: str


def copy_managed_initial_version(
    source: Path,
    *,
    output_root: Path,
    task_id: UUID,
    expected_sha256: str,
) -> ManagedInitialVersion:
    if source.is_symlink() or not source.is_file():
        raise LocalCsvPublicationConflict("managed_initial_source_unavailable")
    observed_sha256 = _file_sha256(source)
    if observed_sha256 != expected_sha256:
        raise LocalCsvPublicationConflict("managed_initial_source_hash_conflict")
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / f"{task_id}-initial.csv"
    if destination.exists():
        existing_sha256 = _file_sha256(destination)
        if existing_sha256 != expected_sha256:
            raise LocalCsvPublicationConflict("managed_initial_version_conflict")
        return ManagedInitialVersion(path=destination, sha256=existing_sha256)
    _atomic_copy(source, destination, prefix=".agent-initial-")
    published_sha256 = _file_sha256(destination)
    if published_sha256 != expected_sha256:
        destination.unlink(missing_ok=True)
        raise LocalCsvPublicationConflict("managed_initial_readback_mismatch")
    return ManagedInitialVersion(path=destination, sha256=published_sha256)


class LocalCsvPublisher:
    def __init__(self, sources: LocalSourceService) -> None:
        self._sources = sources

    def publish(
        self,
        *,
        source_ref: str,
        managed_version_path: Path,
        expected_destination_sha256: str,
        target_version_id: UUID,
    ) -> LocalCsvPublicationResult:
        material = self._sources.describe_target_for_write(source_ref)
        if managed_version_path.is_symlink() or not managed_version_path.is_file():
            raise LocalCsvPublicationConflict("managed_target_version_unavailable")
        managed_sha256 = _file_sha256(managed_version_path)
        if material.sha256 == managed_sha256:
            return _result(
                material.source_ref,
                target_version_id,
                expected_destination_sha256,
                managed_sha256,
                "already_published",
            )
        if material.sha256 != expected_destination_sha256:
            raise LocalCsvPublicationConflict("destination_hash_conflict")
        _atomic_copy(
            managed_version_path,
            material.path,
            prefix=".agent-publish-",
        )
        published_sha256 = _file_sha256(material.path)
        if published_sha256 != managed_sha256:
            raise LocalCsvPublicationConflict("destination_readback_mismatch")
        return _result(
            material.source_ref,
            target_version_id,
            expected_destination_sha256,
            published_sha256,
            "published",
        )


def _result(
    source_ref: str,
    target_version_id: UUID,
    expected_destination_sha256: str,
    published_sha256: str,
    status: str,
) -> LocalCsvPublicationResult:
    return LocalCsvPublicationResult(
        source_ref=source_ref,
        target_version_id=target_version_id,
        expected_destination_sha256=expected_destination_sha256,
        published_sha256=published_sha256,
        status=status,
    )


def _atomic_copy(source: Path, destination: Path, *, prefix: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with source.open("rb") as source_stream:
            with NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=prefix,
                suffix=".tmp",
                delete=False,
            ) as target_stream:
                temporary = Path(target_stream.name)
                shutil.copyfileobj(source_stream, target_stream)
                target_stream.flush()
                os.fsync(target_stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
