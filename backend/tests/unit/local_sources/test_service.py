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
    service = LocalSourceService(
        Settings(agent_local_read_roots=(allowed,), _env_file=None)
    )

    with pytest.raises(LocalSourceAccessError, match="outside_allowed_roots"):
        service.read_page("../secret.csv", offset=0, limit=50)


def test_read_page_returns_at_most_fifty_records(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    _write_roster(allowed / "third-party" / "roster.csv", 51)
    service = LocalSourceService(
        Settings(agent_local_read_roots=(allowed,), _env_file=None)
    )

    page = service.read_page("third-party/roster.csv", offset=0, limit=99)

    assert len(page.records) == 50
    assert page.next_offset == 50
    assert page.source_ref == "third-party/roster.csv"


def test_local_source_summary_never_exposes_absolute_path(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    _write_roster(allowed / "seewo" / "roster.csv", 1)

    sources = LocalSourceService(
        Settings(agent_local_read_roots=(allowed,), _env_file=None)
    ).list_sources()

    assert [source.source_ref for source in sources] == ["seewo/roster.csv"]
    assert str(allowed) not in sources[0].model_dump_json()


def test_local_source_summary_marks_only_authorized_target_as_writable(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    _write_roster(allowed / "data" / "authority.csv", 1)
    _write_roster(allowed / "seewo" / "target.csv", 1)
    service = LocalSourceService(
        Settings(
            agent_local_read_roots=(allowed,),
            agent_local_write_roots=(allowed / "seewo",),
            _env_file=None,
        )
    )

    sources = {source.source_ref: source for source in service.list_sources()}

    assert sources["data/authority.csv"].writable_as_target is False
    assert sources["seewo/target.csv"].writable_as_target is True
    assert (
        service.describe_target_for_write("seewo/target.csv").source_ref
        == "seewo/target.csv"
    )
    with pytest.raises(LocalSourceAccessError, match="target_not_writable"):
        service.describe_target_for_write("data/authority.csv")


def test_local_target_write_resolution_rejects_symlink_substitution(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside.csv"
    _write_roster(outside, 1)
    link = allowed / "seewo" / "target.csv"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    service = LocalSourceService(
        Settings(
            agent_local_read_roots=(allowed,),
            agent_local_write_roots=(allowed / "seewo",),
            _env_file=None,
        )
    )

    with pytest.raises(LocalSourceAccessError, match="outside_allowed_roots"):
        service.describe_target_for_write("seewo/target.csv")
