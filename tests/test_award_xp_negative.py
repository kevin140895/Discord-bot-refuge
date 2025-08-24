import pytest

import cogs.xp as xp


@pytest.mark.asyncio
async def test_award_xp_negative():
    xp.xp_store.data.clear()
    uid = 999
    await xp.award_xp(uid, 100, guild_id=0)
    old, new, oxp, total = await xp.award_xp(uid, -40, guild_id=0)
    assert total == 60
    old, new, oxp, total = await xp.award_xp(uid, -1000, guild_id=0)
    assert total == 0
