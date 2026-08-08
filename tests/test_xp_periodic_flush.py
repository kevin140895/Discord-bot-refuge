import asyncio
from unittest.mock import AsyncMock

import pytest

import storage.xp_store as xp_store_module
from storage.xp_store import XPStore


def test_new_store_has_no_unflushed_updates(tmp_path):
    store = XPStore(path=str(tmp_path / "xp.json"))

    assert store.stats["total_updates"] == 0
    assert store._last_flushed_update_count == 0
    assert store._has_unflushed_updates() is False


@pytest.mark.asyncio
async def test_successful_flush_marks_only_current_snapshot_clean(monkeypatch, tmp_path):
    store = XPStore(path=str(tmp_path / "xp.json"))
    store.data = {"1": {"xp": 100, "level": 1}}
    store.stats["total_updates"] = 100

    write = AsyncMock()
    monkeypatch.setattr(xp_store_module, "atomic_write_json_async", write)

    assert store._has_unflushed_updates() is True

    await store.flush()

    write.assert_awaited_once()
    assert store._last_flushed_update_count == 100
    assert store._has_unflushed_updates() is False

    # Une nouvelle mutation rend à nouveau l'état sale.
    store.stats["total_updates"] = 101
    assert store._has_unflushed_updates() is True


@pytest.mark.asyncio
async def test_failed_flush_keeps_updates_dirty(monkeypatch, tmp_path):
    store = XPStore(path=str(tmp_path / "xp.json"))
    store.data = {"1": {"xp": 100, "level": 1}}
    store.stats["total_updates"] = 100

    write = AsyncMock(side_effect=OSError("disk unavailable"))
    monkeypatch.setattr(xp_store_module, "atomic_write_json_async", write)

    with pytest.raises(OSError, match="disk unavailable"):
        await store.flush()

    assert store._last_flushed_update_count == 0
    assert store._has_unflushed_updates() is True


@pytest.mark.asyncio
async def test_older_flush_cannot_move_persisted_counter_backwards(monkeypatch, tmp_path):
    store = XPStore(path=str(tmp_path / "xp.json"))
    store.data = {"1": {"xp": 100, "level": 1}}
    store.stats["total_updates"] = 100

    write_started = asyncio.Event()
    release_write = asyncio.Event()

    async def slow_write(path, data):
        write_started.set()
        await release_write.wait()

    monkeypatch.setattr(xp_store_module, "atomic_write_json_async", slow_write)

    first_flush = asyncio.create_task(store.flush())
    await write_started.wait()

    # Simule qu'un flush plus récent a déjà persisté davantage d'updates.
    store._last_flushed_update_count = 101
    release_write.set()
    await first_flush

    assert store._last_flushed_update_count == 101
