import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
import cogs.xp as xp

OWNER_ID = 541417878314942495


@pytest.mark.asyncio
async def test_don_xp_awards_xp(monkeypatch):
    xp.xp_store.data.clear()
    respond = AsyncMock()
    monkeypatch.setattr(xp, "safe_respond", respond)
    bot = SimpleNamespace(wait_until_ready=AsyncMock())
    cog = xp.XPCog(bot=bot)
    cog.auto_backup_xp.cancel()
    member = SimpleNamespace(id=123, display_name="User", bot=False)
    interaction = SimpleNamespace(user=SimpleNamespace(id=OWNER_ID), guild=SimpleNamespace(id=1))
    await xp.XPCog.don_xp.callback(cog, interaction, member, 50)
    assert xp.xp_store.data[str(member.id)]["xp"] == 50
    respond.assert_awaited_once()


def test_don_xp_check_owner_only():
    check = xp.XPCog.don_xp.checks[0]
    assert check(SimpleNamespace(user=SimpleNamespace(id=OWNER_ID)))
    assert not check(SimpleNamespace(user=SimpleNamespace(id=123)))
