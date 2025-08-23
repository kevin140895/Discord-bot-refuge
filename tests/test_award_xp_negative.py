import pytest

import cogs.xp as xp


@pytest.mark.asyncio
async def test_award_xp_negative():
    xp.xp_store.data.clear()
    uid = 999
    await xp.award_xp(uid, 100)
    old, new, total = await xp.award_xp(uid, -40)
    assert total == 60
    old, new, total = await xp.award_xp(uid, -1000)
    assert total == 0
