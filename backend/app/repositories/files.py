from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snapshots import SourceFile
from app.schemas.canonical_entities import SourceRole


class FileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        source_role: SourceRole,
        original_name: str,
        storage_name: str,
        storage_path: Path,
        sha256: str,
        size_bytes: int,
        detected_encoding: str | None = None,
    ) -> SourceFile:
        source_file = SourceFile(
            id=uuid4(),
            source_role=source_role.value,
            original_name=original_name,
            storage_name=storage_name,
            storage_path=str(storage_path),
            sha256=sha256,
            size_bytes=size_bytes,
            detected_encoding=detected_encoding,
        )
        self.session.add(source_file)
        return source_file

    async def get(self, file_id: UUID) -> SourceFile | None:
        return await self.session.get(SourceFile, file_id)

    async def bind_to_task(self, file_id: UUID, task_id: UUID) -> SourceFile:
        source_file = await self.session.get(SourceFile, file_id)
        if source_file is None:
            raise LookupError(f"source file not found: {file_id}")
        if source_file.task_id is not None and source_file.task_id != task_id:
            raise ValueError("source file is already bound to another task")
        source_file.task_id = task_id
        return source_file
