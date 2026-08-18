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
