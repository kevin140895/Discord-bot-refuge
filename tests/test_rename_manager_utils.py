import time
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from utils import rename_manager as rm
from utils.rename_manager import _RenameManager


class DummyChannel:
    def __init__(self, cid):
        self.id = cid
        self.name = ""
        self.calls: list[float] = []

    async def edit(self, name):
        self.name = name
        self.calls.append(time.monotonic())


@pytest.mark.asyncio
async def test_coalescing(monkeypatch):
    rm.CHANNEL_RENAME_DEBOUNCE_SECONDS = 0
    rm.CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL = 0
    rm.CHANNEL_RENAME_MIN_INTERVAL_GLOBAL = 0
    mgr = _RenameManager()

    ch = DummyChannel(1)
    await mgr.start()
    await mgr.request(ch, "a")
    await mgr.request(ch, "b")
    await mgr.request(ch, "c")
    await mgr._queue.join()

    assert ch.name == "c"
    assert len(ch.calls) == 1


@pytest.mark.asyncio
async def test_cooldowns(monkeypatch):
    rm.CHANNEL_RENAME_DEBOUNCE_SECONDS = 0
    rm.CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL = 0.05
    rm.CHANNEL_RENAME_MIN_INTERVAL_GLOBAL = 0.05
    mgr = _RenameManager()

    a = DummyChannel(1)
    b = DummyChannel(2)
    await mgr.start()
    await mgr.request(a, "a")
    await mgr.request(b, "b")
    await mgr._queue.join()

    assert b.calls[0] - a.calls[0] >= 0.05

