import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.economy_ui as economy_ui
import cogs.pari_xp as pari_xp
from storage.xp_store import XPStore
from utils import xp_adapter


@pytest.mark.asyncio
async def test_try_spend_xp_prevents_double_spend(tmp_path):
    store = XPStore(path=str(tmp_path / "xp.json"), cache_size=10)
    store.data["1"] = {"xp": 100, "level": 1}

    results = await asyncio.gather(
        store.try_spend_xp(1, 100),
        store.try_spend_xp(1, 100),
    )

    assert sorted(results) == [False, True]
    assert store.data["1"]["xp"] == 0
    await store.aclose()


@pytest.mark.asyncio
async def test_xp_adapter_rejects_unfunded_negative_debit(tmp_path, monkeypatch):
    store = XPStore(path=str(tmp_path / "xp.json"), cache_size=10)
    store.data["7"] = {"xp": 50, "level": 0}
    monkeypatch.setattr(xp_adapter, "xp_store", store)

    with pytest.raises(xp_adapter.InsufficientXPError):
        await xp_adapter.add_xp(7, -100, guild_id=123, source="test")

    assert store.data["7"]["xp"] == 50
    await store.aclose()


@pytest.mark.asyncio
async def test_shop_does_not_grant_item_when_atomic_debit_fails(monkeypatch):
    monkeypatch.setattr(
        economy_ui,
        "_load_shop",
        lambda: {"ticket_royal": {"name": "Ticket Royal", "price": 100}},
    )
    monkeypatch.setattr(economy_ui, "load_tickets", lambda: {})
    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 100)
    debit = AsyncMock(side_effect=xp_adapter.InsufficientXPError("insufficient"))
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", debit)
    save_tickets = AsyncMock()
    monkeypatch.setattr(economy_ui, "save_tickets", save_tickets)

    cog = economy_ui.EconomyUICog(object())
    send = AsyncMock()
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        guild_id=123,
        response=SimpleNamespace(send_message=send),
    )

    await cog._handle_shop_purchase(interaction, "ticket_royal")

    debit.assert_awaited_once()
    save_tickets.assert_not_awaited()
    assert "insuffisant" in send.call_args.args[0].lower()


@pytest.mark.asyncio
async def test_roulette_rejects_bet_when_atomic_debit_fails(monkeypatch):
    cog = object.__new__(pari_xp.PariXPCog)
    cog.is_open = True

    monkeypatch.setattr(
        pari_xp.xp_store,
        "get_user_data",
        AsyncMock(return_value={"xp": 100, "level": 1}),
    )
    monkeypatch.setattr(
        pari_xp.xp_adapter,
        "add_xp",
        AsyncMock(side_effect=xp_adapter.InsufficientXPError("insufficient")),
    )
    respond = AsyncMock()
    monkeypatch.setattr(pari_xp, "safe_respond", respond)

    interaction = SimpleNamespace(
        user=SimpleNamespace(id=42),
        guild_id=123,
        guild=None,
    )

    await pari_xp.PariXPCog._handle_bet(cog, interaction, "red", 100)

    respond.assert_awaited_once()
    assert "insuffisant" in respond.call_args.args[1].lower()
