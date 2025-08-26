import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path
import sys
from discord.app_commands import errors

sys.path.append(str(Path(__file__).resolve().parents[1]))
import cogs.xp as xp
from config import XP_VIEWER_ROLE_ID

ROLE_ID = XP_VIEWER_ROLE_ID


def _member_with_roles(role_ids):
    roles = [SimpleNamespace(id=r) for r in role_ids]
    return SimpleNamespace(
        roles=roles, get_role=lambda i: next((r for r in roles if r.id == i), None)
    )


@pytest.mark.asyncio
async def test_don_xp_awards_xp(monkeypatch):
    xp.xp_store.data.clear()
    respond = AsyncMock()
    monkeypatch.setattr(xp, "safe_respond", respond)
    bot = SimpleNamespace(wait_until_ready=AsyncMock())
    cog = xp.XPCog(bot=bot)
    cog.auto_backup_xp.cancel()
    member = SimpleNamespace(id=123, display_name="User", bot=False)
    role = SimpleNamespace(id=ROLE_ID)
    interaction = SimpleNamespace(user=SimpleNamespace(id=1, roles=[role]), guild=SimpleNamespace(id=1))
    await xp.XPCog.don_xp.callback(cog, interaction, member, 50)
    assert xp.xp_store.data[str(member.id)]["xp"] == 50
    respond.assert_awaited_once()


def test_don_xp_check_requires_role():
    check = xp.XPCog.don_xp.checks[0]
    with_role = SimpleNamespace(user=_member_with_roles([ROLE_ID]))
    without_role = SimpleNamespace(user=_member_with_roles([]))
    assert check(with_role)
    with pytest.raises(errors.MissingRole):
        check(without_role)
