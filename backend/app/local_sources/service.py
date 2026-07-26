"""Safe discovery and bounded reading for deployment-approved local source files."""

import csv
import hashlib
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings

_ALLOWED_SUFFIXES = frozenset({".csv"})
_BLOCKED_NAMES = frozenset({".env", ".env.local", ".env.production"})
_BLOCKED_PARTS = frozenset({".git", "__pycache__", ".venv", "node_modules"})
_MAX_PAGE_SIZE = 50


class LocalSourceAccessError(ValueError):
    """A stable, public-safe local source access failure."""


@dataclass(frozen=True)
class LocalSourceMaterial:
    source_ref: str
    path: Path
    sha256: str
    size_bytes: int


class LocalSourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_ref: str = Field(min_length=1)
    kind: str = "csv"
    writable_as_target: bool = False


class LocalSourcePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_ref: str = Field(min_length=1)
    records: tuple[dict[str, str], ...]
    next_offset: int | None = Field(ge=0, default=None)


class LocalSourceService:
    def __init__(self, settings: Settings) -> None:
        self._roots = settings.agent_local_read_roots
        self._write_roots = settings.agent_local_write_roots

    def list_sources(self) -> tuple[LocalSourceSummary, ...]:
        summaries: list[LocalSourceSummary] = []
        for root in self._roots:
            if not root.is_dir():
                continue
            for candidate in sorted(root.rglob("*")):
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                try:
                    source_ref = self._source_ref(candidate)
                except LocalSourceAccessError:
                    continue
                resolved = candidate.resolve(strict=True)
                summaries.append(
                    LocalSourceSummary(
                        source_ref=source_ref,
                        writable_as_target=any(
                            _is_within(resolved, root) for root in self._write_roots
                        ),
                    )
                )
        return tuple(summaries)

    def read_page(
        self, source_ref: str, *, offset: int, limit: int = _MAX_PAGE_SIZE
    ) -> LocalSourcePage:
        if offset < 0:
            raise LocalSourceAccessError("invalid_page_offset")
        path = self.describe(source_ref).path
        page_size = min(max(limit, 1), _MAX_PAGE_SIZE)
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            window = tuple(islice(reader, offset, offset + page_size + 1))
            rows = tuple(
                {key: value or "" for key, value in row.items() if key is not None}
                for row in window[:page_size]
            )
            has_next = len(window) > page_size
        return LocalSourcePage(
            source_ref=self._source_ref(path),
            records=rows,
            next_offset=offset + len(rows) if has_next else None,
        )

    def describe(self, source_ref: str) -> LocalSourceMaterial:
        path = self._resolve(source_ref)
        return LocalSourceMaterial(
            source_ref=self._source_ref(path),
            path=path,
            sha256=_file_sha256(path),
            size_bytes=path.stat().st_size,
        )

    def describe_target_for_write(self, source_ref: str) -> LocalSourceMaterial:
        material = self.describe(source_ref)
        if not any(_is_within(material.path, root) for root in self._write_roots):
            raise LocalSourceAccessError("target_not_writable")
        return material

    def _resolve(self, source_ref: str) -> Path:
        if not source_ref or Path(source_ref).is_absolute():
            raise LocalSourceAccessError("outside_allowed_roots")
        candidate = Path(source_ref)
        if any(part in _BLOCKED_PARTS for part in candidate.parts):
            raise LocalSourceAccessError("outside_allowed_roots")
        for root in self._roots:
            if _contains_symlink(root, candidate):
                raise LocalSourceAccessError("outside_allowed_roots")
            path = (root / candidate).resolve(strict=False)
            if not _is_within(path, root):
                continue
            if not path.exists() or not path.is_file():
                raise LocalSourceAccessError("source_not_found")
            if path.is_symlink() or path.name in _BLOCKED_NAMES:
                raise LocalSourceAccessError("outside_allowed_roots")
            if path.suffix.casefold() not in _ALLOWED_SUFFIXES:
                raise LocalSourceAccessError("unsupported_source")
            return path
        raise LocalSourceAccessError("outside_allowed_roots")

    def _source_ref(self, path: Path) -> str:
        resolved = path.resolve(strict=True)
        if resolved.is_symlink() or resolved.name in _BLOCKED_NAMES:
            raise LocalSourceAccessError("outside_allowed_roots")
        if resolved.suffix.casefold() not in _ALLOWED_SUFFIXES:
            raise LocalSourceAccessError("unsupported_source")
        for root in self._roots:
            if _is_within(resolved, root):
                return resolved.relative_to(root).as_posix()
        raise LocalSourceAccessError("outside_allowed_roots")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _contains_symlink(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
