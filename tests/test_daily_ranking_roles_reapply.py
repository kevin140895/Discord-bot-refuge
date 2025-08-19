import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from cogs.daily_ranking import DailyRankingAndRoles
from config import MVP_ROLE_ID, TOP_MSG_ROLE_ID, TOP_VC_ROLE_ID, XP_VIEWER_ROLE_ID


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
async def test_roles_reapplied_after_restart():
    mvp = DummyRole(MVP_ROLE_ID)
    msg = DummyRole(TOP_MSG_ROLE_ID)
    vc = DummyRole(TOP_VC_ROLE_ID)

    winner_mvp = DummyMember(1)
    winner_msg = DummyMember(2)
    winner_vc = DummyMember(3)
    other = DummyMember(4, roles=[mvp, msg, vc])

    guild = DummyGuild(
        [winner_mvp, winner_msg, winner_vc, other],
        {MVP_ROLE_ID: mvp, TOP_MSG_ROLE_ID: msg, TOP_VC_ROLE_ID: vc},
    )

    cog = DailyRankingAndRoles.__new__(DailyRankingAndRoles)
    cog.bot = SimpleNamespace(guilds=[guild])

    winners = {"mvp": winner_mvp.id, "msg": winner_msg.id, "vc": winner_vc.id}
    with patch.object(DailyRankingAndRoles, "_read_persistence", return_value={"winners": winners}):
        await DailyRankingAndRoles._apply_roles_from_file(cog)

    assert mvp in winner_mvp.roles
    assert msg in winner_msg.roles
    assert vc in winner_vc.roles

    assert mvp not in other.roles
    assert msg not in other.roles
    assert vc not in other.roles


@pytest.mark.asyncio
async def test_test_classement1_permission_and_ephemeral():
    cog = DailyRankingAndRoles.__new__(DailyRankingAndRoles)
    cog.bot = SimpleNamespace()

    interaction = SimpleNamespace(user=SimpleNamespace(roles=[]))
    command = DailyRankingAndRoles.test_classement1
    with patch("cogs.daily_ranking.safe_respond", new_callable=AsyncMock) as respond:
        await command.callback(cog, interaction)
    respond.assert_awaited_once_with(interaction, "Accès refusé.", ephemeral=True)

    viewer_role = SimpleNamespace(id=XP_VIEWER_ROLE_ID)
    interaction = SimpleNamespace(user=SimpleNamespace(roles=[viewer_role]))
    data = {"foo": "bar"}
    with patch("cogs.daily_ranking.safe_respond", new_callable=AsyncMock) as respond, \
         patch("cogs.daily_ranking.read_json_safe", return_value=data):
        await command.callback(cog, interaction)
    respond.assert_awaited()
    args, kwargs = respond.await_args
    assert kwargs["ephemeral"] is True
    assert json.dumps(data, indent=2, ensure_ascii=False) in args[1]
