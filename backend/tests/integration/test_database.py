import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_sqlite_database_enforces_foreign_keys(database) -> None:
    async with database.engine.connect() as connection:
        enabled = await connection.scalar(text("PRAGMA foreign_keys"))

    assert enabled == 1
