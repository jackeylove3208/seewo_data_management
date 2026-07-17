import csv
import io
from dataclasses import dataclass
from pathlib import Path

import polars as pl
from charset_normalizer import from_bytes


class CsvFormatError(ValueError):
    pass


@dataclass(frozen=True)
class CsvInspection:
    encoding: str
    delimiter: str
    headers: tuple[str, ...]


def inspect_csv(path: Path) -> CsvInspection:
    raw = path.read_bytes()
    if not raw:
        raise CsvFormatError("CSV file is empty")
    detected = from_bytes(raw[:65536]).best()
    encoding = _canonical_encoding(detected.encoding if detected else None)
    if encoding not in {"utf-8", "utf-8-sig", "gb18030"}:
        raise CsvFormatError(f"unsupported encoding: {encoding or 'unknown'}")
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as error:
        raise CsvFormatError(f"unsupported encoding: {encoding}") from error
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=",")
    headers = tuple(next(reader, ()))
    if not headers or all(not header.strip() for header in headers):
        raise CsvFormatError("CSV header is empty")
    normalized = tuple(header.strip() for header in headers)
    duplicates = sorted({header for header in normalized if normalized.count(header) > 1})
    if duplicates:
        raise CsvFormatError(f"duplicate header: {', '.join(duplicates)}")
    if any(not header for header in normalized):
        raise CsvFormatError("CSV header contains an empty column name")
    return CsvInspection(encoding=encoding, delimiter=",", headers=normalized)


def read_csv_frame(path: Path, inspection: CsvInspection) -> pl.DataFrame:
    text = path.read_bytes().decode(inspection.encoding)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=inspection.delimiter)
    next(reader, None)
    for row_number, row in enumerate(reader, start=2):
        if len(row) != len(inspection.headers):
            raise CsvFormatError(
                f"row shape mismatch at row {row_number}: "
                f"expected {len(inspection.headers)} columns, got {len(row)}"
            )
    try:
        frame = pl.read_csv(
            io.StringIO(text),
            infer_schema=False,
            missing_utf8_is_empty_string=True,
        )
    except pl.exceptions.PolarsError as error:
        raise CsvFormatError(f"CSV parse failed: {error}") from error
    return frame.with_row_index("_row_number", offset=2)


def _canonical_encoding(value: str | None) -> str:
    normalized = (value or "").lower().replace("_", "-")
    aliases = {
        "ascii": "utf-8",
        "utf-8": "utf-8",
        "utf-8-sig": "utf-8-sig",
        "utf-8-bom": "utf-8-sig",
        "gb18030": "gb18030",
        "gbk": "gb18030",
        "gb2312": "gb18030",
    }
    return aliases.get(normalized, normalized)
