import json
from pathlib import Path

import pytest

from storage.xp_store import XPStore


@pytest.mark.asyncio
async def test_get_user_data_returns_copy_for_cached_user(tmp_path: Path):
    store = XPStore(path=str(tmp_path / "xp.json"))
    store.data["42"] = {"xp": 125, "level": 1}

    payload = await store.get_user_data(42)
    payload["xp"] = 9999
    payload["level"] = 99

    assert store.data["42"]["xp"] == 125
    assert store.data["42"]["level"] == 1

    fresh_payload = await store.get_user_data(42)
    assert fresh_payload["xp"] == 125
    assert fresh_payload["level"] == 1


@pytest.mark.asyncio
async def test_get_user_data_returns_copy_after_boot_load(tmp_path: Path):
    path = tmp_path / "xp.json"
    path.write_text(
        json.dumps({"7": {"xp": 350, "level": 1}}),
        encoding="utf-8",
    )
    store = XPStore(path=str(path))
    await store.start()

    payload = await store.get_user_data(7)
    payload["xp"] = 0

    assert store.data["7"]["xp"] == 350
    assert (await store.get_user_data(7))["xp"] == 350

    await store.aclose()
