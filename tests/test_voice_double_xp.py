import asyncio
import os
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import cogs.voice_double_xp as dx
from utils.voice_bonus import get_voice_multiplier, set_voice_bonus


def test_random_sessions_limits(monkeypatch):
    with patch.object(dx.random, "randint", return_value=2), patch.object(
        dx.random, "sample", return_value=[600, 1320]
    ):
        sessions = dx._random_sessions()
    assert sessions == ["10:00", "22:00"]


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
