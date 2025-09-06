from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock

from cogs.daily_awards import DailyAwards
from config import MVP_ROLE_ID, WRITER_ROLE_ID, VOICE_ROLE_ID


class DummyRole:
    def __init__(self, rid: int):
        self.id = rid


class DummyMember:
    def __init__(self, mid: int, roles=None):
        self.id = mid
        self.roles = list(roles) if roles else []

    async def remove_roles(self, *roles, reason=None):
        for r in roles:
            if r in self.roles:
                self.roles.remove(r)

    async def add_roles(self, role, reason=None):
        if role not in self.roles:
            self.roles.append(role)


class DummyGuild:
    def __init__(self, members, roles):
        self.members = members
        self._roles = roles

    def get_role(self, rid: int):
        return self._roles.get(rid)

    def get_member(self, uid: int):
        for m in self.members:
            if m.id == uid:
                return m
        return None


@pytest.mark.asyncio
async def test_roles_reassigned():
    mvp = DummyRole(MVP_ROLE_ID)
    msg = DummyRole(WRITER_ROLE_ID)
    vc = DummyRole(VOICE_ROLE_ID)

    winner_mvp = DummyMember(1)
    winner_msg = DummyMember(2)
    winner_vc = DummyMember(3)
    other = DummyMember(4, roles=[mvp, msg, vc])

    guild = DummyGuild(
        [winner_mvp, winner_msg, winner_vc, other],
        {MVP_ROLE_ID: mvp, WRITER_ROLE_ID: msg, VOICE_ROLE_ID: vc},
    )

    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = SimpleNamespace(get_guild=lambda _id: guild)

    winners = {"mvp": winner_mvp.id, "msg": winner_msg.id, "vc": winner_vc.id}
    await DailyAwards._reset_and_assign(cog, winners)

    assert mvp in winner_mvp.roles
    assert msg in winner_msg.roles
    assert vc in winner_vc.roles

    assert mvp not in other.roles
    assert msg not in other.roles
    assert vc not in other.roles


@pytest.mark.asyncio
async def test_build_message_partial():
    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = SimpleNamespace()
    cog._mention_or_name = AsyncMock(side_effect=lambda uid: f"u{uid}")
    data = {
        "top3": {
            "mvp": [{"id": 1, "score": 10, "messages": 5, "voice": 30}]
        }
    }

    message = await DailyAwards._build_message(cog, data)
    assert "MVP du Refuge" in message
    assert "Écrivain du Refuge" in message and "Aucun gagnant" in message
    assert "Voix du Refuge" in message and message.count("Aucun gagnant") >= 2


@pytest.mark.asyncio
async def test_maybe_award_partial_publishes_and_assigns():
    channel = SimpleNamespace(send=AsyncMock())
    bot = SimpleNamespace(get_channel=lambda _id: channel)

    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = bot
    cog._read_state = lambda: {}
    cog._write_state = lambda state: None
    cog._reset_and_assign = AsyncMock()
    cog._build_message = AsyncMock(return_value="msg")

    data = {
        "date": "2024-01-01",
        "winners": {"mvp": 1, "msg": None, "vc": None},
    }

    await DailyAwards._maybe_award(cog, data)

    cog._reset_and_assign.assert_awaited_once_with(data["winners"])
    channel.send.assert_awaited_once_with("msg")


