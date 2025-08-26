import importlib
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_refresh_leaderboard_uses_in_memory_state(tmp_path, monkeypatch):
    import sys
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    state_path = tmp_path / "state.json"
    tx_path = tmp_path / "transactions.json"
    monkeypatch.setattr(pari_xp, "STATE_PATH", state_path)
    monkeypatch.setattr(pari_xp, "TX_PATH", tx_path)

    cog = object.__new__(pari_xp.RouletteRefugeCog)
    cog.bot = object()
    cog.state = {"leaderboard_message_id": 42}

    async def _ensure_leaderboard_message(channel):
        return None

    cog._ensure_leaderboard_message = _ensure_leaderboard_message
    cog._build_leaderboard_embed = lambda: None

    called = {}

    async def fake_safe_message_edit(message, **kwargs):
        called["edited"] = True
        return message

    monkeypatch.setattr(pari_xp, "safe_message_edit", fake_safe_message_edit)

    class DummyChannel:
        async def fetch_message(self, msg_id):
            called["fetched"] = msg_id
            return object()

    channel = DummyChannel()

    await pari_xp.RouletteRefugeCog._refresh_leaderboard(cog, channel)

    assert called.get("fetched") == 42
    assert called.get("edited")
