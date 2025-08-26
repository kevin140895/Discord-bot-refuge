import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path
import os

import pytest

import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DISCORD_TOKEN", "dummy")

from cogs.machine_a_sous.machine_a_sous import MachineASousCog
from storage.roulette_store import RouletteStore
from discord.app_commands import errors
from config import XP_VIEWER_ROLE_ID

ROLE_ID = XP_VIEWER_ROLE_ID


def _member_with_roles(role_ids):
    roles = [SimpleNamespace(id=r) for r in role_ids]
    return SimpleNamespace(
        roles=roles,
        get_role=lambda i: next((r for r in roles if r.id == i), None),
    )


def test_ticket_check_requires_role():
    check = MachineASousCog.ticket.checks[0]
    with_role = SimpleNamespace(user=_member_with_roles([ROLE_ID]))
    without_role = SimpleNamespace(user=_member_with_roles([]))
    assert check(with_role)
    with pytest.raises(errors.MissingRole):
        check(without_role)


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

