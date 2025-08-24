from datetime import datetime, timedelta, timezone

import pytest

import cogs.xp as xp


@pytest.mark.asyncio
async def test_award_xp_with_boost(monkeypatch):
    xp.xp_store.data.clear()
    uid = 123
    xp.add_xp_boost(uid, 60)
    old, new, total = await xp.award_xp(uid, 10)
    assert total == 20


@pytest.mark.asyncio
async def test_boost_expiration(monkeypatch):
    xp.xp_store.data.clear()
    uid = 456
    xp.XP_BOOSTS[str(uid)] = datetime.now(timezone.utc) - timedelta(seconds=1)
    old, new, total = await xp.award_xp(uid, 10)
    assert total == 10
    assert str(uid) not in xp.XP_BOOSTS


@pytest.mark.asyncio
async def test_boost_persistence(tmp_path, monkeypatch):
    file_path = tmp_path / "xp_boosts.json"
    monkeypatch.setattr(xp, "XP_BOOSTS_FILE", str(file_path))
    xp.XP_BOOSTS.clear()

    uid = 789
    xp.add_xp_boost(uid, 60)
    await xp.save_xp_boosts_to_disk()

    xp.XP_BOOSTS.clear()
    await xp.xp_bootstrap_cache()

    assert str(uid) in xp.XP_BOOSTS
