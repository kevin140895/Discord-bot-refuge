import asyncio
from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import cogs.xp as xp
import cogs.daily_leaderboard as dl
from cogs.daily_leaderboard import DailyLeaderboard
from config import MVP_ROLE_ID, TOP_MSG_ROLE_ID, TOP_VC_ROLE_ID


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
async def test_calculate_daily_winners(monkeypatch):
    date = "2024-01-01"
    xp.DAILY_STATS = {
        date: {
            "1": {"messages": 5, "voice": 60},
            "2": {"messages": 3, "voice": 120},
        }
    }
    dl.DAILY_STATS = xp.DAILY_STATS
    xp.DAILY_LOCK = asyncio.Lock()
    dl.DAILY_LOCK = xp.DAILY_LOCK

    async def dummy_save() -> None:
        return None

    monkeypatch.setattr(xp, "save_daily_stats_to_disk", dummy_save)
    cog = DailyLeaderboard.__new__(DailyLeaderboard)
    result = await DailyLeaderboard._calculate_daily_winners(cog, date)
    assert result["winners"] == {"msg": 1, "vc": 2, "mvp": 1}
    assert date not in xp.DAILY_STATS


@pytest.mark.asyncio
async def test_update_daily_roles_assigns():
    mvp = DummyRole(MVP_ROLE_ID)
    msg = DummyRole(TOP_MSG_ROLE_ID)
    vc = DummyRole(TOP_VC_ROLE_ID)

    winner_msg = DummyMember(1)
    winner_vc = DummyMember(2)
    winner_mvp = DummyMember(3)
    other = DummyMember(4, roles=[mvp, msg, vc])

    guild = DummyGuild(
        [winner_msg, winner_vc, winner_mvp, other],
        {MVP_ROLE_ID: mvp, TOP_MSG_ROLE_ID: msg, TOP_VC_ROLE_ID: vc},
    )

    cog = DailyLeaderboard.__new__(DailyLeaderboard)
    await DailyLeaderboard._update_daily_roles(
        cog,
        guild,
        {"msg": 1, "vc": 2, "mvp": 3},
    )

    assert msg in winner_msg.roles
    assert vc in winner_vc.roles
    assert mvp in winner_mvp.roles
    assert msg not in other.roles and vc not in other.roles and mvp not in other.roles
