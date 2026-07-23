from pathlib import Path

import pytest

from app.core.config import Settings
from app.local_sources.service import LocalSourceAccessError, LocalSourceService


def _write_roster(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = ["编号,姓名,类别"]
    rows.extend(f"{index:03d},测试{index},学生" for index in range(count))
    path.write_text("\n".join(rows), encoding="utf-8")


def test_read_page_rejects_parent_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (tmp_path / "secret.csv").write_text("编号,姓名\n001,不应读取", encoding="utf-8")
    service = LocalSourceService(Settings(agent_local_read_roots=(allowed,)))

    with pytest.raises(LocalSourceAccessError, match="outside_allowed_roots"):
        service.read_page("../secret.csv", offset=0, limit=50)


def test_read_page_returns_at_most_fifty_records(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    _write_roster(allowed / "third-party" / "roster.csv", 51)
    service = LocalSourceService(Settings(agent_local_read_roots=(allowed,)))

    page = service.read_page("third-party/roster.csv", offset=0, limit=99)

    assert len(page.records) == 50
    assert page.next_offset == 50
    assert page.source_ref == "third-party/roster.csv"


def test_local_source_summary_never_exposes_absolute_path(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    _write_roster(allowed / "seewo" / "roster.csv", 1)

    sources = LocalSourceService(Settings(agent_local_read_roots=(allowed,))).list_sources()

    assert [source.source_ref for source in sources] == ["seewo/roster.csv"]
    assert str(allowed) not in sources[0].model_dump_json()
