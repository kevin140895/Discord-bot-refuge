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
