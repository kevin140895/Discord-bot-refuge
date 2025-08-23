import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
from cogs.radio import RadioCog
from utils.rename_manager import rename_manager


@pytest.mark.asyncio
async def test_radio_cog_does_not_start_rename_manager(monkeypatch):
    bot = SimpleNamespace(loop=asyncio.get_event_loop())
    rename_manager._worker = None
    start_mock = AsyncMock()
    monkeypatch.setattr("cogs.radio.rename_manager.start", start_mock)

    RadioCog(bot)
    await asyncio.sleep(0)

    start_mock.assert_not_called()
