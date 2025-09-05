import logging
from types import SimpleNamespace

import pytest

from utils.rename_manager import _RenameManager


@pytest.mark.asyncio
async def test_skip_when_channel_deleted(monkeypatch, caplog):
    monkeypatch.setattr(
        "utils.rename_manager.CHANNEL_RENAME_DEBOUNCE_SECONDS", 0
    )
    monkeypatch.setattr(
        "utils.rename_manager.CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL", 0
    )
    monkeypatch.setattr(
        "utils.rename_manager.CHANNEL_RENAME_MIN_INTERVAL_GLOBAL", 0
    )

    rm = _RenameManager()
    await rm.start()

    called = False

    async def edit(name):
        nonlocal called
        called = True

    guild = SimpleNamespace(get_channel=lambda cid: None)
    channel = SimpleNamespace(id=123, name="old", guild=guild, edit=edit)

    rm._last_per_channel[channel.id] = 0.0

    caplog.set_level(logging.DEBUG)

    await rm.request(channel, "new")
    await rm._queue.join()
    await rm.aclose()

    assert called is False
    assert channel.id not in rm._last_per_channel
    assert any(
        "deleted before rename" in record.getMessage() for record in caplog.records
    )
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)
