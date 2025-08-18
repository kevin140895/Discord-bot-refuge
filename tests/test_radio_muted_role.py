import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.radio import RadioCog
from config import RADIO_MUTED_ROLE_ID, RADIO_VC_ID


@pytest.mark.asyncio
async def test_member_with_muted_role_gets_muted_when_joining_radio():
    bot = SimpleNamespace(user=SimpleNamespace(id=1), loop=asyncio.get_event_loop())
    member = SimpleNamespace(
        id=2,
        roles=[SimpleNamespace(id=RADIO_MUTED_ROLE_ID)],
        edit=AsyncMock(),
    )
    before = SimpleNamespace(channel=None)
    after = SimpleNamespace(channel=SimpleNamespace(id=RADIO_VC_ID))

    cog = RadioCog(bot)
    await cog.on_voice_state_update(member, before, after)

    member.edit.assert_awaited_once()
    assert member.edit.await_args.kwargs["mute"] is True

    # no cleanup needed


@pytest.mark.asyncio
async def test_member_unmuted_when_leaving_radio():
    bot = SimpleNamespace(user=SimpleNamespace(id=1), loop=asyncio.get_event_loop())
    member = SimpleNamespace(
        id=2,
        roles=[SimpleNamespace(id=RADIO_MUTED_ROLE_ID)],
        edit=AsyncMock(),
    )
    before = SimpleNamespace(channel=SimpleNamespace(id=RADIO_VC_ID))
    after = SimpleNamespace(channel=None)

    cog = RadioCog(bot)
    await cog.on_voice_state_update(member, before, after)

    member.edit.assert_awaited_once()
    assert member.edit.await_args.kwargs["mute"] is False

    # no cleanup needed

