from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cogs.daily_awards import DailyAwards
from config import MVP_ROLE_ID, WRITER_ROLE_ID, VOICE_ROLE_ID, XP_VIEWER_ROLE_ID


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
    cog.bot = SimpleNamespace(guilds=[guild])

    winners = {"mvp": winner_mvp.id, "msg": winner_msg.id, "vc": winner_vc.id}
    await DailyAwards._reset_and_assign(cog, winners)

    assert mvp in winner_mvp.roles
    assert msg in winner_msg.roles
    assert vc in winner_vc.roles

    assert mvp not in other.roles
    assert msg not in other.roles
    assert vc not in other.roles


@pytest.mark.asyncio
async def test_test_classements_permission_and_send():
    cog = DailyAwards.__new__(DailyAwards)
    cog.bot = SimpleNamespace(get_channel=lambda _id: None)

    interaction = SimpleNamespace(user=SimpleNamespace(roles=[]))
    command = DailyAwards.test_classements
    with patch("cogs.daily_awards.safe_respond", new_callable=AsyncMock) as respond:
        await command.callback(cog, interaction)
    respond.assert_awaited_once_with(interaction, "Accès refusé.", ephemeral=True)

    viewer_role = SimpleNamespace(id=XP_VIEWER_ROLE_ID)
    interaction = SimpleNamespace(user=SimpleNamespace(roles=[viewer_role]))
    data = {
        "top3": {
            "mvp": [{"id": 1, "score": 1, "messages": 1, "voice": 1}],
            "msg": [{"id": 2, "count": 5}],
            "vc": [{"id": 3, "minutes": 10}],
        },
        "winners": {"mvp": 1, "msg": 2, "vc": 3},
        "date": "2024-01-01",
    }
    channel = SimpleNamespace(send=AsyncMock())
    cog.bot.get_channel = lambda _id: channel
    with patch("cogs.daily_awards.safe_respond", new_callable=AsyncMock) as respond, \
        patch("cogs.daily_awards.read_json_safe", return_value=data), \
        patch.object(DailyAwards, "_build_message", new=AsyncMock(return_value="msg")):
        await command.callback(cog, interaction)
    channel.send.assert_awaited_once_with("msg")
    respond.assert_awaited()
