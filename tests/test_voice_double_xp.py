import asyncio
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import cogs.voice_double_xp as dx
from utils.voice_bonus import get_voice_multiplier, set_voice_bonus


@pytest.mark.parametrize("count, expected", [
    (0, []),
    (1, ["10:00"]),
    (2, ["10:00", "22:00"]),
])
def test_random_sessions_limits(count, expected):
    samples = [600, 1320][:count]
    with patch.object(dx.random, "randint", return_value=count), patch.object(
        dx.random, "sample", return_value=samples
    ):
        assert dx._random_sessions() == expected


def test_multiplier_application():
    set_voice_bonus(False)
    assert get_voice_multiplier(1.0) == 1.0
    set_voice_bonus(True)
    assert get_voice_multiplier(1.0) == 2.0
    assert get_voice_multiplier(3.0) == 3.0
    set_voice_bonus(False)


@pytest.mark.asyncio
async def test_persistence_no_redraw(tmp_path, monkeypatch):
    today = datetime.now(dx.PARIS_TZ).date().isoformat()
    state_file = tmp_path / "double_voice_xp.json"
    monkeypatch.setattr(dx, "STATE_FILE", str(state_file))
    await dx._write_state({"date": today, "sessions": ["10:00"]})

    bot = SimpleNamespace(loop=asyncio.get_event_loop(), get_channel=lambda cid: None)
    with patch.object(dx.tasks.Loop, "start", lambda self, *a, **k: None):
        with patch.object(dx.DoubleVoiceXP, "_run_session", AsyncMock()):
            with patch.object(dx.random, "randint", side_effect=AssertionError("no redraw")):
                cog = dx.DoubleVoiceXP(bot)
                await cog._prepare_today()
                await asyncio.sleep(0)
    assert len(cog._tasks) == 1


@pytest.mark.asyncio
async def test_announcements_and_duration(monkeypatch):
    session = {"hm": "00:00", "started": False, "end": None, "ended": False}
    channel = SimpleNamespace(send=AsyncMock())

    async def wait():
        return None

    bot = SimpleNamespace(
        loop=asyncio.get_event_loop(),
        get_channel=lambda cid: channel,
        wait_until_ready=wait,
    )
    monkeypatch.setattr(dx, "XP_DOUBLE_VOICE_DURATION_MINUTES", 0)
    with patch.object(dx, "_write_state", AsyncMock()):
        cog = dx.DoubleVoiceXP(bot)
        cog.state = {"date": "", "sessions": [session]}
        await cog._run_session(session, 0)
    assert channel.send.call_count == 2
    assert session["started"] and session["ended"]


@pytest.mark.asyncio
async def test_resume_after_restart(tmp_path, monkeypatch):
    now = datetime.now(dx.PARIS_TZ)
    end = now + timedelta(seconds=0.1)
    state = {
        "date": now.date().isoformat(),
        "sessions": [
            {
                "hm": now.strftime("%H:%M"),
                "started": True,
                "end": end.isoformat(),
                "ended": False,
            }
        ],
    }
    state_file = tmp_path / "double_voice_xp.json"
    monkeypatch.setattr(dx, "STATE_FILE", str(state_file))
    await dx._write_state(state)
    async def wait():
        return None

    bot = SimpleNamespace(
        loop=asyncio.get_event_loop(),
        get_channel=lambda cid: None,
        wait_until_ready=wait,
    )
    with patch.object(dx.tasks.Loop, "start", lambda self, *a, **k: None):
        with patch.object(dx, "set_voice_bonus") as set_bonus:
            with patch.object(dx.DoubleVoiceXP, "_end_session", AsyncMock()) as end_mock:
                dx.DoubleVoiceXP(bot)
                await asyncio.sleep(0.2)
    set_bonus.assert_called_with(True)
    assert end_mock.await_count == 1
