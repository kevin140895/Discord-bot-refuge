import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path
import os

import discord
import pytest

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from cogs.machine_a_sous.machine_a_sous import MachineASousCog
from storage.roulette_store import RouletteStore
from discord.app_commands import errors
from config import LEVEL_ROLE_REWARDS
from cogs.zz_economy_admin_guard import require_manage_guild

ARGENT_ROLE_ID = LEVEL_ROLE_REWARDS[10]


def _member_with_roles(role_ids):
    roles = [SimpleNamespace(id=r) for r in role_ids]
    return SimpleNamespace(
        roles=roles,
        get_role=lambda i: next((r for r in roles if r.id == i), None),
    )


def test_ticket_effective_check_requires_manage_guild_not_argent_role():
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


@pytest.mark.asyncio
async def test_ticket_command_grants_ticket(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "cogs.machine_a_sous.machine_a_sous.DATA_DIR", str(tmp_path)
    )
    bot = SimpleNamespace(wait_until_ready=asyncio.sleep)
    cog = MachineASousCog(bot)
    cog.store = RouletteStore(data_dir=str(tmp_path))

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    member = SimpleNamespace(id=123, mention="@user")

    await MachineASousCog.ticket.callback(cog, interaction, member)

    assert cog.store.has_ticket(str(member.id))
