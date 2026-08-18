import pytest

import cogs.xp as xp
from storage.db import SQLiteDatabase


@pytest.mark.asyncio
async def test_daily_stats_persist_to_sqlite_without_rewriting_legacy_json(
    tmp_path,
    monkeypatch,
):
    legacy_path = tmp_path / "daily_stats.json"
    legacy_path.write_text(
        '{"2026-08-18":{"1":{"messages":1,"voice":10}}}',
        encoding="utf-8",
    )
    legacy_snapshot = legacy_path.read_text(encoding="utf-8")
    db = SQLiteDatabase(tmp_path / "refuge.db")

    monkeypatch.setattr(xp, "DAILY_STATS_FILE", str(legacy_path))
    monkeypatch.setattr(xp, "database", db)

    xp.DAILY_STATS.clear()
    xp.DAILY_STATS["2026-08-19"] = {
        "42": {
            "messages": 3,
            "voice": 120,
            "voice_thanked": True,
        }
    }

    await xp.save_daily_stats_to_disk()

    assert legacy_path.read_text(encoding="utf-8") == legacy_snapshot
    assert await db.load_daily_stats() == {
        "2026-08-19": {
            "42": {
                "messages": 3,
                "voice": 120,
                "voice_thanked": True,
            }
        }
    }


@pytest.mark.asyncio
async def test_load_daily_stats_imports_legacy_once(tmp_path, monkeypatch):
    legacy_path = tmp_path / "daily_stats.json"
    legacy_path.write_text(
        '{"2026-08-19":{"42":{"messages":5,"voice":600}}}',
        encoding="utf-8",
    )
    db = SQLiteDatabase(tmp_path / "refuge.db")

    monkeypatch.setattr(xp, "DAILY_STATS_FILE", str(legacy_path))
    monkeypatch.setattr(xp, "database", db)

    first = await xp.load_daily_stats()
    legacy_path.write_text(
        '{"2026-08-19":{"42":{"messages":999,"voice":999}}}',
        encoding="utf-8",
    )
    second = await xp.load_daily_stats()

    expected = {
        "2026-08-19": {
            "42": {
                "messages": 5,
                "voice": 600,
            }
        }
    }
    assert first == expected
    assert second == expected
