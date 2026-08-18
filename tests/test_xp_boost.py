from datetime import datetime, timedelta, timezone

import pytest

import cogs.xp as xp
from storage.db import SQLiteDatabase


@pytest.mark.asyncio
async def test_award_xp_with_boost(monkeypatch):
    async def _noop_save() -> None:
        return None

    monkeypatch.setattr(xp, "save_xp_boosts_to_disk", _noop_save)
    xp.xp_store.data.clear()
    xp.XP_BOOSTS.clear()
    xp.XP_BOOST_STARTS.clear()
    xp.XP_BOOST_HISTORY.clear()
    uid = 123
    xp.add_xp_boost(uid, 60)
    old, new, oxp, total = await xp.award_xp(uid, 10, guild_id=0)
    assert total == 20


@pytest.mark.asyncio
async def test_boost_expiration(monkeypatch):
    xp.xp_store.data.clear()
    xp.XP_BOOSTS.clear()
    xp.XP_BOOST_STARTS.clear()
    xp.XP_BOOST_HISTORY.clear()
    uid = 456
    xp.XP_BOOSTS[str(uid)] = datetime.now(timezone.utc) - timedelta(seconds=1)
    old, new, oxp, total = await xp.award_xp(uid, 10, guild_id=0)
    assert total == 10
    # L'expiration reste disponible comme borne temporelle jusqu'au prochain
    # boost afin qu'une session vocale commencée avant l'expiration puisse être
    # comptabilisée correctement à sa sortie.
    assert str(uid) in xp.XP_BOOSTS


@pytest.mark.asyncio
async def test_boost_persistence(tmp_path, monkeypatch):
    file_path = tmp_path / "xp_boosts.json"
    db = SQLiteDatabase(tmp_path / "refuge.db")
    monkeypatch.setattr(xp, "XP_BOOSTS_FILE", str(file_path))
    monkeypatch.setattr(xp, "database", db)
    xp.XP_BOOSTS.clear()
    xp.XP_BOOST_STARTS.clear()
    xp.XP_BOOST_HISTORY.clear()

    uid = 789
    start = datetime.now(timezone.utc)
    expiry = start + timedelta(hours=1)
    history_start = start - timedelta(hours=2)
    history_end = start - timedelta(hours=1)
    xp.XP_BOOSTS[str(uid)] = expiry
    xp.XP_BOOST_STARTS[str(uid)] = start
    xp.XP_BOOST_HISTORY[str(uid)] = [(history_start, history_end)]

    await xp.save_xp_boosts_to_disk()

    xp.XP_BOOSTS.clear()
    xp.XP_BOOST_STARTS.clear()
    xp.XP_BOOST_HISTORY.clear()
    expiries, starts, history = await xp.load_xp_boosts()

    assert expiries[str(uid)] == expiry
    assert starts[str(uid)] == start
    assert history[str(uid)] == [(history_start, history_end)]
    assert not file_path.exists()
