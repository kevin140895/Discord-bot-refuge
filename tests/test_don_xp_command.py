import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path
import sys

import discord
from discord.app_commands import errors

sys.path.append(str(Path(__file__).resolve().parents[1]))
import cogs.xp as xp
from config import LEVEL_ROLE_REWARDS
from cogs.zz_economy_admin_guard import require_manage_guild

ARGENT_ROLE_ID = LEVEL_ROLE_REWARDS[10]


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
    interaction = SimpleNamespace(user=SimpleNamespace(id=1), guild=SimpleNamespace(id=1))
    await xp.XPCog.don_xp.callback(cog, interaction, member, 50)
    assert xp.xp_store.data[str(member.id)]["xp"] == 50
    respond.assert_awaited_once()


def test_don_xp_effective_check_requires_manage_guild_not_argent_role():
    manager = SimpleNamespace(
        user=_member_with_roles([]),
        guild=SimpleNamespace(id=1),
        permissions=discord.Permissions(manage_guild=True),
    )
    argent_member = SimpleNamespace(
        user=_member_with_roles([ARGENT_ROLE_ID]),
        guild=SimpleNamespace(id=1),
        permissions=discord.Permissions.none(),
    )

    assert require_manage_guild(manager)
    with pytest.raises(errors.MissingPermissions):
        require_manage_guild(argent_member)
