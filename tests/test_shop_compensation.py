from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import cogs.economy_ui as economy_ui
from storage import transaction_store
from storage.transaction_store import TransactionStore
from storage.xp_store import XPStore
from utils import xp_adapter


def _interaction(user_id: int = 42, guild_id: int = 123):
    send = AsyncMock()
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=guild_id,
        response=SimpleNamespace(send_message=send),
    ), send


@pytest.mark.asyncio
async def test_exact_refund_ignores_double_xp_and_flushes(tmp_path, monkeypatch):
    store = XPStore(path=str(tmp_path / "xp.json"), cache_size=10)
    store.data["7"] = {
        "xp": 500,
        "level": store._calc_level(500),
        "double_xp_until": (datetime.utcnow() + timedelta(hours=1)).isoformat(),
    }
    monkeypatch.setattr(xp_adapter, "xp_store", store)

    await xp_adapter.refund_xp_exact(
        7,
        100,
        guild_id=0,
        source="shop_refund",
    )

    assert store.data["7"]["xp"] == 600
    persisted = xp_adapter.read_json_safe(store.path)
    assert persisted["7"]["xp"] == 600
    await store.aclose()


@pytest.mark.asyncio
async def test_ticket_delivery_failure_refunds_exact_debit(monkeypatch):
    monkeypatch.setattr(
        economy_ui,
        "_load_shop",
        lambda: {"ticket_royal": {"name": "Ticket Royal", "price": 100}},
    )
    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 500)
    debit = AsyncMock()
    refund = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", debit)
    monkeypatch.setattr(economy_ui.xp_adapter, "refund_xp_exact", refund)
    monkeypatch.setattr(economy_ui, "load_tickets", lambda: {})
    monkeypatch.setattr(
        economy_ui,
        "save_tickets",
        AsyncMock(side_effect=OSError("disk full")),
    )
    ledger_add = AsyncMock()
    monkeypatch.setattr(
        economy_ui,
        "transactions",
        SimpleNamespace(add=ledger_add, all=AsyncMock(return_value=[])),
    )

    interaction, send = _interaction()
    cog = economy_ui.EconomyUICog(object())

    await cog._handle_shop_purchase(interaction, "ticket_royal")

    debit.assert_awaited_once_with(42, amount=-100, guild_id=123, source="shop")
    refund.assert_awaited_once_with(
        42,
        100,
        123,
        source="shop_refund",
    )
    ledger_add.assert_not_awaited()
    assert "intégralement remboursés" in send.call_args.args[0]


@pytest.mark.asyncio
async def test_boost_delivery_failure_restores_state_and_refunds(monkeypatch):
    monkeypatch.setattr(
        economy_ui,
        "_load_shop",
        lambda: {"double_xp_1h": {"name": "Double XP", "price": 300}},
    )
    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 500)
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", AsyncMock())
    refund = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "refund_xp_exact", refund)

    snapshot = {"marker": "before"}
    monkeypatch.setattr(
        economy_ui,
        "_snapshot_personal_double_xp",
        lambda _uid: snapshot,
    )
    restore = Mock()
    monkeypatch.setattr(economy_ui, "_restore_personal_double_xp", restore)
    monkeypatch.setattr(
        economy_ui,
        "_activate_personal_double_xp",
        lambda _uid, _minutes: datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(economy_ui, "load_boosts", lambda: {})
    monkeypatch.setattr(
        economy_ui,
        "save_boosts",
        AsyncMock(side_effect=OSError("disk full")),
    )
    persist_boost = AsyncMock()
    monkeypatch.setattr(
        economy_ui,
        "_persist_personal_double_xp",
        persist_boost,
    )
    ledger_add = AsyncMock()
    monkeypatch.setattr(
        economy_ui,
        "transactions",
        SimpleNamespace(add=ledger_add, all=AsyncMock(return_value=[])),
    )

    interaction, send = _interaction()
    cog = economy_ui.EconomyUICog(object())

    await cog._handle_shop_purchase(interaction, "double_xp_1h")

    restore.assert_called_once_with(42, snapshot)
    refund.assert_awaited_once_with(42, 300, 123, source="shop_refund")
    persist_boost.assert_not_awaited()
    ledger_add.assert_not_awaited()
    assert "intégralement remboursés" in send.call_args.args[0]


@pytest.mark.asyncio
async def test_ledger_failure_after_delivery_does_not_refund(monkeypatch):
    monkeypatch.setattr(
        economy_ui,
        "_load_shop",
        lambda: {"ticket_royal": {"name": "Ticket Royal", "price": 100}},
    )
    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 500)
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", AsyncMock())
    refund = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "refund_xp_exact", refund)
    monkeypatch.setattr(economy_ui, "load_tickets", lambda: {})
    save_tickets = AsyncMock()
    monkeypatch.setattr(economy_ui, "save_tickets", save_tickets)
    monkeypatch.setattr(
        economy_ui,
        "transactions",
        SimpleNamespace(
            add=AsyncMock(side_effect=OSError("ledger unavailable")),
            all=AsyncMock(return_value=[]),
        ),
    )

    interaction, send = _interaction()
    cog = economy_ui.EconomyUICog(object())

    await cog._handle_shop_purchase(interaction, "ticket_royal")

    save_tickets.assert_awaited_once()
    refund.assert_not_awaited()
    assert "effectué" in send.call_args.args[0]


@pytest.mark.asyncio
async def test_transaction_store_rolls_back_memory_when_save_fails(
    tmp_path, monkeypatch
):
    store = TransactionStore(tmp_path / "transactions.json")
    monkeypatch.setattr(
        transaction_store,
        "save_json",
        AsyncMock(side_effect=OSError("disk full")),
    )

    with pytest.raises(OSError):
        await store.add({"type": "buy", "item": "ticket_royal"})

    assert await store.all() == []
