from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils import xp_adapter


@pytest.mark.asyncio
async def test_xp_adapter_observes_successful_roulette_spend_without_changing_amount(monkeypatch):
    calls = []

    async def fake_spend(user_id, amount, *, guild_id=None, source="spend"):
        assert user_id == 7
        assert amount == 125
        assert guild_id == 99
        assert source == "pari_xp"
        return True

    monkeypatch.setattr(xp_adapter.xp_store, "try_spend_xp", fake_spend)
    monkeypatch.setattr(
        xp_adapter,
        "observe_casino_xp_transaction",
        lambda **payload: calls.append(payload),
    )

    await xp_adapter.add_xp(7, -125, 99, "pari_xp")

    assert calls == [
        {
            "user_id": 7,
            "source": "pari_xp",
            "requested_amount": -125,
            "applied_delta": -125,
        }
    ]


@pytest.mark.asyncio
async def test_award_xp_preserves_nominal_machine_jackpot_when_double_xp_applies(monkeypatch):
    from cogs import xp as xp_cog

    calls = []
    store_amounts = []
    now = datetime.now(timezone.utc)

    async def fake_add_xp(user_id, amount, *, guild_id=None, source="manual"):
        store_amounts.append(amount)
        return 1, 2, 100, 100 + amount

    async def fake_season_record(*args, **kwargs):
        return None

    monkeypatch.setitem(xp_cog.XP_BOOSTS, "42", now + timedelta(hours=1))
    monkeypatch.setattr(xp_cog.xp_store, "add_xp", fake_add_xp)
    monkeypatch.setattr(xp_cog.season_store, "record", fake_season_record)
    monkeypatch.setattr(
        xp_cog,
        "observe_casino_xp_transaction",
        lambda **payload: calls.append(payload),
    )

    result = await xp_cog.award_xp(
        42,
        500,
        guild_id=99,
        source="machine_a_sous",
    )

    assert store_amounts == [1000]
    assert result == (1, 2, 100, 1100)
    assert len(calls) == 1
    assert calls[0]["user_id"] == 42
    assert calls[0]["source"] == "machine_a_sous"
    assert calls[0]["requested_amount"] == 500
    assert calls[0]["applied_delta"] == 1000
    assert calls[0]["at"].tzinfo is not None
