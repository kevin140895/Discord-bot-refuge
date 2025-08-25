import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.economy_ui as economy_ui
from cogs.economy_ui import EconomyUICog
from storage import economy
from storage.transaction_store import TransactionStore
import config


def _setup_paths(tmp_path, monkeypatch):
    shop_file = tmp_path / "shop.json"
    monkeypatch.setattr(economy, "SHOP_FILE", shop_file)
    monkeypatch.setattr(economy_ui, "SHOP_FILE", shop_file)
    boosts_file = tmp_path / "boosts.json"
    tickets_file = tmp_path / "tickets.json"
    tx_file = tmp_path / "transactions.json"
    monkeypatch.setattr(economy, "BOOSTS_FILE", boosts_file)
    monkeypatch.setattr(economy, "TICKETS_FILE", tickets_file)
    monkeypatch.setattr(economy, "TRANSACTIONS_FILE", tx_file)
    store = TransactionStore(tx_file)
    monkeypatch.setattr(economy, "transactions", store)
    monkeypatch.setattr(economy_ui, "transactions", store)
    return shop_file


@pytest.mark.asyncio
async def test_shop_buy_insufficient_balance(tmp_path, monkeypatch):
    shop_file = _setup_paths(tmp_path, monkeypatch)
    shop_file.write_text(
        json.dumps({"double_xp_1h": {"name": "Double XP", "price": 500}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 100)
    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)

    cog = EconomyUICog(object())

    send_mock = AsyncMock()
    interaction = SimpleNamespace(
        data={"custom_id": "shop_buy:double_xp_1h"},
        user=SimpleNamespace(id=1, add_roles=AsyncMock()),
        guild=None,
        guild_id=123,
        response=SimpleNamespace(send_message=send_mock),
    )

    await cog.on_interaction(interaction)

    add_xp_mock.assert_not_called()
    send_mock.assert_awaited()
    assert send_mock.call_args.kwargs["ephemeral"] is True
    assert economy.load_boosts() == {}


@pytest.mark.asyncio
async def test_shop_buy_double_xp(tmp_path, monkeypatch):
    shop_file = _setup_paths(tmp_path, monkeypatch)
    shop_file.write_text(
        json.dumps({"double_xp_1h": {"name": "Double XP", "price": 500}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)

    cog = EconomyUICog(object())

    send_mock = AsyncMock()
    interaction = SimpleNamespace(
        data={"custom_id": "shop_buy:double_xp_1h"},
        user=SimpleNamespace(id=1, add_roles=AsyncMock()),
        guild=None,
        guild_id=123,
        response=SimpleNamespace(send_message=send_mock),
    )

    await cog.on_interaction(interaction)

    add_xp_mock.assert_awaited_once_with(1, amount=-500, guild_id=123, source="shop")
    boosts = economy.load_boosts()
    assert boosts["1"][0]["type"] == "double_xp"
    txs = await economy.transactions.all()
    assert txs[0]["item"] == "double_xp_1h"
    send_mock.assert_awaited()
    assert send_mock.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_shop_buy_vip(tmp_path, monkeypatch):
    shop_file = _setup_paths(tmp_path, monkeypatch)
    shop_file.write_text(
        json.dumps({"vip_24h": {"name": "VIP 24h", "price": 2000}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 3000)
    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)
    monkeypatch.setattr(config, "VIP_24H_ROLE_ID", 42)

    role = SimpleNamespace(id=42)
    guild = SimpleNamespace(id=321, get_role=lambda _id: role)

    cog = EconomyUICog(object())

    send_mock = AsyncMock()
    user = SimpleNamespace(id=1, add_roles=AsyncMock())
    interaction = SimpleNamespace(
        data={"custom_id": "shop_buy:vip_24h"},
        user=user,
        guild=guild,
        guild_id=321,
        response=SimpleNamespace(send_message=send_mock),
    )

    await cog.on_interaction(interaction)

    add_xp_mock.assert_awaited_once_with(1, amount=-2000, guild_id=321, source="shop")
    user.add_roles.assert_awaited_once_with(role, reason="Achat VIP 24h")
    boosts = economy.load_boosts()
    assert boosts["1"][0]["type"] == "vip"
    txs = await economy.transactions.all()
    assert txs[0]["item"] == "vip_24h"
    send_mock.assert_awaited()
    assert send_mock.call_args.kwargs["ephemeral"] is True

