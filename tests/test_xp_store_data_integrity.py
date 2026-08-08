import json

import pytest

from storage.xp_store import XPStore


@pytest.mark.asyncio
async def test_cleanup_does_not_delete_persistent_users(tmp_path):
    path = tmp_path / "data.json"
    store = XPStore(path=str(path), cache_size=2)
    store.data = {
        "1": {"xp": 100, "level": 1, "last_accessed": "2026-01-01T00:00:00"},
        "2": {"xp": 200, "level": 1, "last_accessed": "2026-01-02T00:00:00"},
        "3": {"xp": 300, "level": 1, "last_accessed": "2026-01-03T00:00:00"},
    }

    await store._cleanup_cache()
    await store.flush()

    assert set(store.data) == {"1", "2", "3"}
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert set(persisted) == {"1", "2", "3"}
    assert persisted["1"]["xp"] == 100
    assert persisted["2"]["xp"] == 200
    assert persisted["3"]["xp"] == 300
