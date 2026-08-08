import asyncio
import json
import threading
import time

import pytest

import storage.temp_vc_store as temp_vc_store
import utils.persistence as persistence


def _reset_async_write_lock(monkeypatch) -> None:
    monkeypatch.setattr(persistence, "_write_lock", None)
    monkeypatch.setattr(persistence, "_write_lock_loop", None)


@pytest.mark.asyncio
async def test_temp_vc_ids_newer_snapshot_wins(monkeypatch, tmp_path):
    _reset_async_write_lock(monkeypatch)
    path = tmp_path / "temp_vc_ids.json"
    monkeypatch.setattr(temp_vc_store, "DATA_FILE", path)

    original_write = persistence.atomic_write_json
    first_started = threading.Event()

    def controlled_write(dest, data):
        if data == [1]:
            first_started.set()
            time.sleep(0.05)
        original_write(dest, data)

    monkeypatch.setattr(persistence, "atomic_write_json", controlled_write)

    older = asyncio.create_task(temp_vc_store.save_temp_vc_ids_async({1}))
    assert await asyncio.to_thread(first_started.wait, 1.0)
    newer = asyncio.create_task(temp_vc_store.save_temp_vc_ids_async({1, 2}))

    await asyncio.gather(older, newer)

    assert json.loads(path.read_text(encoding="utf-8")) == [1, 2]


@pytest.mark.asyncio
async def test_temp_vc_names_newer_snapshot_wins(monkeypatch, tmp_path):
    _reset_async_write_lock(monkeypatch)
    path = tmp_path / "temp_vc_last_names.json"
    monkeypatch.setattr(temp_vc_store, "LAST_NAMES_FILE", path)

    original_write = persistence.atomic_write_json
    first_started = threading.Event()

    def controlled_write(dest, data):
        if data == {"1": "ancien"}:
            first_started.set()
            time.sleep(0.05)
        original_write(dest, data)

    monkeypatch.setattr(persistence, "atomic_write_json", controlled_write)

    older = asyncio.create_task(
        temp_vc_store.save_last_names_cache({1: "ancien"})
    )
    assert await asyncio.to_thread(first_started.wait, 1.0)
    newer = asyncio.create_task(
        temp_vc_store.save_last_names_cache({1: "nouveau", 2: "actif"})
    )

    await asyncio.gather(older, newer)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "1": "nouveau",
        "2": "actif",
    }
