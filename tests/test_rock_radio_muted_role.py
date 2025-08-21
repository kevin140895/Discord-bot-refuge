import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.rock_radio import RockRadioCog
from config import RADIO_MUTED_ROLE_ID, ROCK_RADIO_VC_ID


@pytest.mark.asyncio
async def test_member_with_muted_role_not_muted_when_joining_rock_radio():
    bot = SimpleNamespace(user=SimpleNamespace(id=1), loop=asyncio.get_event_loop())
    member = SimpleNamespace(
        id=2,
        roles=[SimpleNamespace(id=RADIO_MUTED_ROLE_ID)],
        edit=AsyncMock(),
    )
    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=SimpleNamespace(id=ROCK_RADIO_VC_ID))

    cog = RockRadioCog(bot)
    await cog.on_voice_state_update(member, before, after)

    member.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_with_muted_role_not_unmuted_when_leaving_rock_radio():
    bot = SimpleNamespace(user=SimpleNamespace(id=1), loop=asyncio.get_event_loop())
    member = SimpleNamespace(
        id=2,
        roles=[SimpleNamespace(id=RADIO_MUTED_ROLE_ID)],
        edit=AsyncMock(),
    )
    before = SimpleNamespace(channel=SimpleNamespace(id=ROCK_RADIO_VC_ID))
    after = SimpleNamespace(channel=None)

    cog = RockRadioCog(bot)
    await cog.on_voice_state_update(member, before, after)

    member.edit.assert_not_awaited()
