import asyncio
import json

import pytest

import storage.roulette_store as roulette_store
from storage.roulette_store import RouletteStore


def _existing_store(tmp_path) -> RouletteStore:
    (tmp_path / "roulette.json").write_text("{}", encoding="utf-8")
    return RouletteStore(str(tmp_path))


@pytest.mark.asyncio
async def test_mutation_does_not_use_sync_writer_inside_event_loop(
    monkeypatch, tmp_path
):
    store = _existing_store(tmp_path)
    writes = []

    def fail_sync_write(*args, **kwargs):
        raise AssertionError("synchronous disk write used inside event loop")

    async def fake_async_write(path, payload):
        writes.append((path, payload))

    monkeypatch.setattr(roulette_store, "atomic_write_json", fail_sync_write)
    monkeypatch.setattr(roulette_store, "atomic_write_json_async", fake_async_write)

    store.grant_ticket("42")
    assert store.has_ticket("42")

    await store.flush()

    assert writes
    assert writes[-1][1]["tickets"]["42"] == 1


@pytest.mark.asyncio
async def test_slow_write_cannot_leave_an_older_snapshot_on_disk(
    monkeypatch, tmp_path
):
    store = _existing_store(tmp_path)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    writes = []

    async def slow_async_write(path, payload):
        writes.append(json.loads(json.dumps(payload)))
        if len(writes) == 1:
            first_started.set()
            await release_first.wait()

    monkeypatch.setattr(roulette_store, "atomic_write_json_async", slow_async_write)

    store.grant_ticket("42")
    await first_started.wait()

    # The first snapshot is still being written. A newer mutation must return
    # immediately and be flushed afterwards, not be overwritten by the old one.
    store.grant_ticket("42")
    assert store.data["tickets"]["42"] == 2

    release_first.set()
    await store.flush()

    assert writes[0]["tickets"]["42"] == 1
    assert writes[-1]["tickets"]["42"] == 2


def test_mutation_keeps_sync_persistence_without_running_loop(monkeypatch, tmp_path):
    store = _existing_store(tmp_path)
    writes = []

    def fake_sync_write(path, payload):
        writes.append((path, json.loads(json.dumps(payload))))

    monkeypatch.setattr(roulette_store, "atomic_write_json", fake_sync_write)

    store.grant_ticket("7")

    assert writes == [(store.data_file, {"tickets": {"7": 1}})]
