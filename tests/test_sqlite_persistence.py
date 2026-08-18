import json
from datetime import datetime, timezone

import pytest

from storage.db import SQLiteDatabase
from storage.xp_store import XPStore


@pytest.mark.asyncio
async def test_legacy_xp_and_voice_migration_is_idempotent(tmp_path):
    xp_path = tmp_path / "data.json"
    voice_path = tmp_path / "voice_times.json"
    db = SQLiteDatabase(tmp_path / "refuge.db")

    xp_path.write_text(
        json.dumps(
            {
                "10": {"xp": 250, "level": 1},
                "20": {
                    "xp": 900,
                    "level": 3,
                    "double_xp_until": "2026-08-19T12:00:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )
    voice_path.write_text(
        json.dumps({"10": "2026-08-18T01:00:00+00:00"}),
        encoding="utf-8",
    )

    assert await db.migrate_legacy_xp(xp_path) == 2
    assert await db.migrate_legacy_voice_times(voice_path) == 1
    assert await db.migrate_legacy_xp(xp_path) == 0
    assert await db.migrate_legacy_voice_times(voice_path) == 0

    xp = await db.load_xp()
    voice = await db.load_voice_times()

    assert xp["10"]["xp"] == 250
    assert xp["20"]["xp"] == 900
    assert xp["20"]["double_xp_until"] == "2026-08-19T12:00:00+00:00"
    assert voice == {"10": "2026-08-18T01:00:00+00:00"}
    assert await db.quick_check() == "ok"


@pytest.mark.asyncio
async def test_legacy_daily_stats_and_boosts_migration_is_idempotent(tmp_path):
    daily_path = tmp_path / "daily_stats.json"
    boosts_path = tmp_path / "xp_boosts.json"
    db = SQLiteDatabase(tmp_path / "refuge.db")

    daily_path.write_text(
        json.dumps(
            {
                "2026-08-19": {
                    "10": {"messages": 7, "voice": 1800},
                    "20": {
                        "messages": 2,
                        "voice": 7200,
                        "voice_thanked": True,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    boosts_path.write_text(
        json.dumps(
            {
                "10": {
                    "started_at": "2026-08-19T08:00:00+00:00",
                    "expires_at": "2026-08-19T09:00:00+00:00",
                    "history": [
                        {
                            "start": "2026-08-18T08:00:00+00:00",
                            "end": "2026-08-18T09:00:00+00:00",
                        }
                    ],
                },
                "20": "2099-01-01T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    assert await db.migrate_legacy_daily_stats(daily_path) == 2
    assert await db.migrate_legacy_xp_boosts(boosts_path) == 2
    assert await db.migrate_legacy_daily_stats(daily_path) == 0
    assert await db.migrate_legacy_xp_boosts(boosts_path) == 0

    daily = await db.load_daily_stats()
    boosts = await db.load_xp_boosts()

    assert daily["2026-08-19"]["10"] == {"messages": 7, "voice": 1800}
    assert daily["2026-08-19"]["20"] == {
        "messages": 2,
        "voice": 7200,
        "voice_thanked": True,
    }
    assert boosts["10"]["started_at"] == "2026-08-19T08:00:00+00:00"
    assert boosts["10"]["expires_at"] == "2026-08-19T09:00:00+00:00"
    assert boosts["10"]["history"] == [
        {
            "start": "2026-08-18T08:00:00+00:00",
            "end": "2026-08-18T09:00:00+00:00",
        }
    ]
    assert boosts["20"]["expires_at"] == "2099-01-01T12:00:00+00:00"
    assert boosts["20"]["started_at"] is not None
    assert await db.quick_check() == "ok"


@pytest.mark.asyncio
async def test_daily_stats_and_boost_snapshots_replace_stale_rows(tmp_path):
    db = SQLiteDatabase(tmp_path / "refuge.db")

    await db.replace_daily_stats(
        {
            "2026-08-18": {
                "1": {"messages": 4, "voice": 30},
            }
        }
    )
    await db.replace_daily_stats(
        {
            "2026-08-19": {
                "2": {
                    "messages": 9,
                    "voice": 90,
                    "voice_thanked": True,
                }
            }
        }
    )
    assert await db.load_daily_stats() == {
        "2026-08-19": {
            "2": {
                "messages": 9,
                "voice": 90,
                "voice_thanked": True,
            }
        }
    }

    await db.replace_xp_boosts(
        {
            "1": {
                "started_at": "2026-08-19T08:00:00+00:00",
                "expires_at": "2026-08-19T09:00:00+00:00",
                "history": [
                    {
                        "start": "2026-08-18T08:00:00+00:00",
                        "end": "2026-08-18T09:00:00+00:00",
                    }
                ],
            }
        }
    )
    await db.replace_xp_boosts(
        {
            "2": {
                "started_at": "2026-08-19T10:00:00+00:00",
                "expires_at": "2026-08-19T11:00:00+00:00",
                "history": [],
            }
        }
    )
    assert await db.load_xp_boosts() == {
        "2": {
            "started_at": "2026-08-19T10:00:00+00:00",
            "expires_at": "2026-08-19T11:00:00+00:00",
            "history": [],
        }
    }
    assert await db.quick_check() == "ok"


@pytest.mark.asyncio
async def test_started_xp_store_persists_to_sqlite_without_rewriting_legacy_json(
    tmp_path,
):
    xp_path = tmp_path / "data.json"
    xp_path.write_text(
        json.dumps({"42": {"xp": 100, "level": 1}}),
        encoding="utf-8",
    )
    legacy_snapshot = xp_path.read_text(encoding="utf-8")

    store = XPStore(path=str(xp_path))
    await store.start()
    result = await store.add_xp(42, 50)
    await store.flush()

    assert result == (1, 1, 100, 150)
    assert xp_path.read_text(encoding="utf-8") == legacy_snapshot
    await store.aclose()

    reopened = XPStore(path=str(xp_path))
    await reopened.start()
    assert (await reopened.get_user_data(42))["xp"] == 150
    await reopened.aclose()


@pytest.mark.asyncio
async def test_replace_voice_times_removes_stale_active_checkpoints(tmp_path):
    db = SQLiteDatabase(tmp_path / "refuge.db")
    first = datetime(2026, 8, 18, 1, 0, tzinfo=timezone.utc).isoformat()
    second = datetime(2026, 8, 18, 2, 0, tzinfo=timezone.utc).isoformat()

    await db.replace_voice_times({"1": first, "2": second})
    assert await db.load_voice_times() == {"1": first, "2": second}

    await db.replace_voice_times({"2": second})
    assert await db.load_voice_times() == {"2": second}
    assert await db.quick_check() == "ok"
