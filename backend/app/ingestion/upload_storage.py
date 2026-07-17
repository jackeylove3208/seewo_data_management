from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4


class UploadTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class StoredUpload:
    path: Path
    storage_name: str
    original_name: str
    sha256: str
    size_bytes: int


class UploadStorage:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max_bytes

    def save(self, stream: BinaryIO, client_name: str) -> StoredUpload:
        original_name = Path(client_name).name
        if Path(original_name).suffix.casefold() != ".csv":
            raise ValueError("only CSV uploads are supported")
        self.root.mkdir(parents=True, exist_ok=True)
        storage_name = f"{uuid4().hex}.csv"
        destination = self.root / storage_name
        digest = sha256()
        size = 0
        try:
            with destination.open("xb") as target:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UploadTooLarge(
                            f"upload exceeds configured limit of {self.max_bytes} bytes"
                        )
                    digest.update(chunk)
                    target.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        if size == 0:
            destination.unlink(missing_ok=True)
            raise ValueError("CSV upload is empty")
        return StoredUpload(
            path=destination,
            storage_name=storage_name,
            original_name=original_name,
            sha256=digest.hexdigest(),
            size_bytes=size,
        )
