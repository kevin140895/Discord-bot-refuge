import pytest

from storage.xp_store import XPStore
from utils.persistence import atomic_write_json


@pytest.mark.asyncio
async def test_get_top_users_prefers_fresh_memory_over_stale_disk(tmp_path):
    path = tmp_path / "xp.json"
    atomic_write_json(
        path,
        {
            "1": {"xp": 100, "level": 1},
            "2": {"xp": 200, "level": 1},
        },
    )
    store = XPStore(path=str(path))
    store.data["1"] = {"xp": 300, "level": 1}

    leaderboard = await store.get_top_users(limit=2)

    assert [(uid, payload["xp"]) for uid, payload in leaderboard] == [
        ("1", 300),
        ("2", 200),
    ]


@pytest.mark.asyncio
async def test_get_top_users_keeps_disk_only_users_when_memory_is_partial(tmp_path):
    path = tmp_path / "xp.json"
    atomic_write_json(
        path,
        {
            "10": {"xp": 500, "level": 2},
            "20": {"xp": 50, "level": 0},
        },
    )
    store = XPStore(path=str(path))
    store.data["30"] = {"xp": 250, "level": 1}

    leaderboard = await store.get_top_users(limit=3)

    assert [(uid, payload["xp"]) for uid, payload in leaderboard] == [
        ("10", 500),
        ("30", 250),
        ("20", 50),
    ]


@pytest.mark.asyncio
async def test_get_top_users_returns_copies_not_mutable_store_entries(tmp_path):
    path = tmp_path / "xp.json"
    store = XPStore(path=str(path))
    store.data["1"] = {"xp": 300, "level": 1}

    leaderboard = await store.get_top_users(limit=1)
    leaderboard[0][1]["xp"] = 0

    assert store.data["1"]["xp"] == 300
