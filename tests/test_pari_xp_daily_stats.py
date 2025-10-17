import importlib
from pathlib import Path
import sys
import pytest


@pytest.mark.asyncio
async def test_announce_close_sends_simple_embed(monkeypatch):
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = object.__new__(pari_xp.RouletteRefugeCog)

    async def _get_announce_channel():
        return None

    cog._get_announce_channel = _get_announce_channel

    sent = {}

    class DummyChannel:
        async def send(self, *, embed):
            sent["embed"] = embed

    channel = DummyChannel()

    await pari_xp.RouletteRefugeCog._announce_close(cog, channel)

    embed = sent["embed"]
    assert embed.title == "🤑 Roulette Refuge — Fermeture"
    assert "Revenez demain" in (embed.description or "")


@pytest.mark.asyncio
async def test_announce_close_prefers_announce_channel(monkeypatch):
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = object.__new__(pari_xp.RouletteRefugeCog)

    sent = {"channel": None, "embed": None}

    class DummyChannel:
        async def send(self, *, embed):
            sent["channel"] = "announce"
            sent["embed"] = embed

    async def _get_announce_channel():
        return DummyChannel()

    cog._get_announce_channel = _get_announce_channel

    class FallbackChannel:
        async def send(self, *, embed):
            sent.setdefault("fallback", True)

    await pari_xp.RouletteRefugeCog._announce_close(cog, FallbackChannel())

    assert sent["channel"] == "announce"
    assert sent["embed"].title == "🤑 Roulette Refuge — Fermeture"
