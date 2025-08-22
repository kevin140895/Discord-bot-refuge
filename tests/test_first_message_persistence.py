import asyncio
import json
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import cogs.first_message as fm
from cogs.first_message import FirstMessageCog


@pytest.mark.asyncio
async def test_reward_not_reissued_after_restart(tmp_path, monkeypatch):
    fixed_now = datetime.combine(date.today(), time(hour=10))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    file_path = tmp_path / "first_win.json"
    monkeypatch.setattr(fm, "FIRST_WIN_FILE", str(file_path))
    monkeypatch.setattr(fm, "datetime", FixedDateTime)

    data = {
        "date": fixed_now.date().isoformat(),
        "winner_id": 42,
        "claimed_at": fixed_now.isoformat(),
    }
    file_path.write_text(json.dumps(data))

    cog = FirstMessageCog.__new__(FirstMessageCog)
    cog.bot = SimpleNamespace()
    cog._lock = asyncio.Lock()
    cog.first_message_claimed = False
    cog.winner_id = None
    cog.claimed_at = None

    cog._load_state()

    assert cog.first_message_claimed is True
    assert cog.winner_id == 42
    assert cog.claimed_at == fixed_now


@pytest.mark.asyncio
async def test_state_resets_on_new_day(tmp_path, monkeypatch):
    today = date.today()
    fixed_now = datetime.combine(today, time(hour=10))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    file_path = tmp_path / "first_win.json"
    monkeypatch.setattr(fm, "FIRST_WIN_FILE", str(file_path))
    monkeypatch.setattr(fm, "datetime", FixedDateTime)

    yesterday = today - timedelta(days=1)
    data = {
        "date": yesterday.isoformat(),
        "winner_id": 99,
        "claimed_at": (fixed_now - timedelta(days=1)).isoformat(),
    }
    file_path.write_text(json.dumps(data))

    cog = FirstMessageCog.__new__(FirstMessageCog)
    cog.bot = SimpleNamespace()
    cog._lock = asyncio.Lock()
    cog.first_message_claimed = True
    cog.winner_id = 99
    cog.claimed_at = fixed_now - timedelta(days=1)

    with patch.object(FirstMessageCog, "_save_state", new_callable=AsyncMock):
        cog._load_state()
        await asyncio.sleep(0)

    assert cog.first_message_claimed is False
    assert cog.winner_id is None
    assert cog.claimed_at is None
