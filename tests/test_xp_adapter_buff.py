from datetime import datetime, timedelta

import asyncio
import importlib.util
from pathlib import Path

import pytest
from storage.xp_store import xp_store
from utils.persistence import read_json_safe

# Load ``main/utils/xp_adapter.py`` explicitly to avoid clashing with the root ``utils`` package.
_XP_ADAPTER_PATH = Path(__file__).resolve().parent.parent / "main" / "utils" / "xp_adapter.py"
spec = importlib.util.spec_from_file_location("pari_xp_adapter", _XP_ADAPTER_PATH)
xp_adapter = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(xp_adapter)  # type: ignore[union-attr]

add_user_xp = xp_adapter.add_user_xp
apply_double_xp_buff = xp_adapter.apply_double_xp_buff
get_user_xp = xp_adapter.get_user_xp


@pytest.mark.asyncio
async def test_double_xp_buff_applies():
    xp_store.data.clear()
    xp_store.lock = asyncio.Lock()
    uid = 100
    apply_double_xp_buff(uid, minutes=60)
    add_user_xp(uid, 10, guild_id=0)
    await asyncio.sleep(0.05)
    assert get_user_xp(uid) == 20


@pytest.mark.asyncio
async def test_double_xp_buff_expires():
    xp_store.data.clear()
    xp_store.lock = asyncio.Lock()
    uid = 200
    apply_double_xp_buff(uid, minutes=60)
    xp_store.data[str(uid)]["double_xp_until"] = (
        datetime.utcnow() - timedelta(seconds=1)
    ).isoformat()
    add_user_xp(uid, 10, guild_id=0)
    await asyncio.sleep(0.05)
    assert get_user_xp(uid) == 10
    assert "double_xp_until" not in xp_store.data[str(uid)]


@pytest.mark.asyncio
async def test_double_xp_buff_persisted(tmp_path, monkeypatch):
    xp_store.data.clear()
    xp_store.lock = asyncio.Lock()
    file_path = tmp_path / "data.json"
    monkeypatch.setattr(xp_store, "path", str(file_path))
    uid = 300
    apply_double_xp_buff(uid, minutes=60)
    await xp_store.flush()
    data = read_json_safe(str(file_path))
    assert "double_xp_until" in data[str(uid)]
