import threading
from pathlib import Path

import pytest

import storage.db as db_module
from storage.xp_store import XPStore
from utils.persistence import atomic_write_json


@pytest.mark.asyncio
async def test_start_offloads_legacy_import_from_event_loop(tmp_path, monkeypatch):
    path = tmp_path / "xp.json"
    event_loop_thread = threading.current_thread()
    read_threads = []

    def checked_read_json(read_path, default=None):
        assert Path(read_path) == path
        assert default is None
        read_threads.append(threading.current_thread())
        return {"1": {"xp": 100, "level": 1}}

    monkeypatch.setattr(db_module, "read_json_safe", checked_read_json)
    store = XPStore(path=str(path))

    await store.start()

    assert store.data["1"]["xp"] == 100
    assert len(read_threads) == 1
    assert read_threads[0] is not event_loop_thread

    await store.aclose()


@pytest.mark.asyncio
async def test_runtime_operations_use_memory_without_rereading_legacy_json(
    tmp_path, monkeypatch
):
    path = tmp_path / "xp.json"
    atomic_write_json(path, {"1": {"xp": 100, "level": 1}})
    store = XPStore(path=str(path))
    await store.start()

    def unexpected_disk_read(*_args, **_kwargs):
        pytest.fail("legacy JSON must not be read during normal XP runtime")

    monkeypatch.setattr(db_module, "read_json_safe", unexpected_disk_read)

    await store.add_xp(2, 50)
    assert await store.try_spend_xp(3, 10) is False
    assert (await store.get_user_data(4))["xp"] == 0

    leaderboard = await store.get_top_users(limit=10)
    stats = await store.get_stats()

    balances = {uid: payload["xp"] for uid, payload in leaderboard}
    assert balances["1"] == 100
    assert balances["2"] == 50
    assert balances["3"] == 0
    assert balances["4"] == 0
    assert stats["total_users"] == len(store.data)
    assert stats["cache_users"] == len(store.data)

    await store.aclose()
