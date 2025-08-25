import importlib
from pathlib import Path
import sys
import pytest


@pytest.mark.asyncio
async def test_announce_close_uses_daily_stats(monkeypatch):
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = object.__new__(pari_xp.RouletteRefugeCog)

    async def _get_announce_channel():
        return None

    cog._get_announce_channel = _get_announce_channel

    called = {"count": 0}

    def fake_daily_stats():
        called["count"] += 1
        return {
            "day_txs": [{}, {}],
            "total_bet": 30,
            "total_payout": 10,
            "net": -20,
        }

    cog._daily_stats = fake_daily_stats

    sent = {}

    class DummyChannel:
        async def send(self, *, embed):
            sent["embed"] = embed

    channel = DummyChannel()

    await pari_xp.RouletteRefugeCog._announce_close(cog, channel)

    assert called["count"] == 1
    description = sent["embed"].description
    assert "Paris : 2" in description
    assert "Total misé : 30 XP" in description
    assert "Total redistribué : 10 XP" in description
    assert "Résultat net : -20 XP" in description


@pytest.mark.asyncio
async def test_post_daily_summary_uses_daily_stats(monkeypatch):
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    pari_xp = importlib.import_module("main.cogs.pari_xp")

    cog = object.__new__(pari_xp.RouletteRefugeCog)

    async def _get_announce_channel():
        return None

    cog._get_announce_channel = _get_announce_channel

    called = {"count": 0}

    def fake_daily_stats():
        called["count"] += 1
        return {
            "day_txs": [
                {"user_id": 1, "username": "A", "delta": 5, "bet": 10, "payout": 15},
                {"user_id": 2, "username": "B", "delta": -5, "bet": 10, "payout": 5},
            ],
            "total_bet": 20,
            "total_payout": 20,
            "net": 0,
        }

    cog._daily_stats = fake_daily_stats

    sent = {}

    class DummyChannel:
        async def send(self, *, embed):
            sent["embed"] = embed

    channel = DummyChannel()

    await pari_xp.RouletteRefugeCog._post_daily_summary(cog, channel)

    assert called["count"] == 1
    total_field = next(f for f in sent["embed"].fields if f.name == "📊 Total misé / redistribué")
    assert total_field.value == "20 XP / 20 XP"
    winners_field = next(f for f in sent["embed"].fields if f.name == "🏆 Top 3 gagnants")
    assert "A (+5 XP)" in winners_field.value
    losers_field = next(f for f in sent["embed"].fields if f.name == "💸 Top 3 perdants")
    assert "B (-5 XP)" in losers_field.value
