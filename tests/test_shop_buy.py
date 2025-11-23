import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.economy_ui as economy_ui
from cogs.economy_ui import EconomyUICog
from storage import economy
from storage.transaction_store import TransactionStore


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
async def test_shop_buy_ticket(tmp_path, monkeypatch):
    shop_file = _setup_paths(tmp_path, monkeypatch)
    shop_file.write_text(
        json.dumps({"ticket_royal": {"name": "Ticket", "price": 100}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 500)
    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)

    cog = EconomyUICog(object())

    send_mock = AsyncMock()
    interaction = SimpleNamespace(
        data={"custom_id": "shop_buy:ticket_royal"},
        user=SimpleNamespace(id=1, add_roles=AsyncMock()),
        guild=None,
        guild_id=123,
        response=SimpleNamespace(send_message=send_mock),
    )

    await cog.on_interaction(interaction)

    add_xp_mock.assert_awaited_once_with(1, amount=-100, guild_id=123, source="shop")
    tickets = economy.load_tickets()
    assert tickets["1"] == 1
    txs = await economy.transactions.all()
    assert txs[0]["item"] == "ticket_royal"
    send_mock.assert_awaited()
    assert send_mock.call_args.kwargs["ephemeral"] is True


def test_shop_text_includes_limits(tmp_path, monkeypatch):
    shop_file = _setup_paths(tmp_path, monkeypatch)
    shop_file.write_text(
        json.dumps(
            {
                "ticket_royal": {"name": "Ticket", "price": 100},
                "double_xp_1h": {"name": "Double XP", "price": 200},
            }
        ),
        encoding="utf-8",
    )

    cog = EconomyUICog.__new__(EconomyUICog)
    text = EconomyUICog._build_shop_text(cog)
    assert "max 3" in text.lower()
    assert "max 2" in text.lower()


@pytest.mark.asyncio
async def test_shop_buy_ticket_limit(tmp_path, monkeypatch):
    shop_file = _setup_paths(tmp_path, monkeypatch)
    shop_file.write_text(
        json.dumps({"ticket_royal": {"name": "Ticket", "price": 100}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)

    cog = EconomyUICog(object())

    async def buy():
        send_mock = AsyncMock()
        interaction = SimpleNamespace(
            data={"custom_id": "shop_buy:ticket_royal"},
            user=SimpleNamespace(id=1, add_roles=AsyncMock()),
            guild=None,
            guild_id=123,
            response=SimpleNamespace(send_message=send_mock),
        )
        await cog.on_interaction(interaction)
        return send_mock

    for _ in range(3):
        mock = await buy()
        assert "effectu" in mock.call_args.args[0].lower()
    mock = await buy()
    assert "stock" in mock.call_args.args[0].lower()
    tickets = economy.load_tickets()
    assert tickets["1"] == 3
    txs = await economy.transactions.all()
    assert sum(1 for tx in txs if tx["item"] == "ticket_royal") == 3


@pytest.mark.asyncio
async def test_shop_buy_double_xp_limit(tmp_path, monkeypatch):
    shop_file = _setup_paths(tmp_path, monkeypatch)
    shop_file.write_text(
        json.dumps({"double_xp_1h": {"name": "Double XP", "price": 200}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)

    cog = EconomyUICog(object())

    async def buy():
        send_mock = AsyncMock()
        interaction = SimpleNamespace(
            data={"custom_id": "shop_buy:double_xp_1h"},
            user=SimpleNamespace(id=1, add_roles=AsyncMock()),
            guild=None,
            guild_id=123,
            response=SimpleNamespace(send_message=send_mock),
        )
        await cog.on_interaction(interaction)
        return send_mock

    for _ in range(2):
        mock = await buy()
        assert "effectu" in mock.call_args.args[0].lower()
    mock = await buy()
    assert "limite" in mock.call_args.args[0].lower()
    boosts = economy.load_boosts()
    assert len(boosts["1"]) == 2
    txs = await economy.transactions.all()
    assert sum(1 for tx in txs if tx["item"] == "double_xp_1h") == 2
    assert add_xp_mock.await_count == 2


@pytest.mark.asyncio
async def test_ticket_limit_allows_repurchase_after_use(tmp_path, monkeypatch):
    shop_file = _setup_paths(tmp_path, monkeypatch)
    shop_file.write_text(
        json.dumps({"ticket_royal": {"name": "Ticket", "price": 100}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)

    cog = EconomyUICog(object())

    async def buy():
        send_mock = AsyncMock()
        interaction = SimpleNamespace(
            data={"custom_id": "shop_buy:ticket_royal"},
            user=SimpleNamespace(id=1, add_roles=AsyncMock()),
            guild=None,
            guild_id=123,
            response=SimpleNamespace(send_message=send_mock),
        )
        await cog.on_interaction(interaction)
        return send_mock

    for _ in range(3):
        await buy()

    tickets = economy.load_tickets()
    tickets["1"] = 1  # simulate using two tickets
    await economy.save_tickets(tickets)

    mock = await buy()
    assert "effectu" in mock.call_args.args[0].lower()
    tickets = economy.load_tickets()
    assert tickets["1"] == 2
    assert add_xp_mock.await_count == 4


@pytest.mark.asyncio
async def test_double_xp_limit_checks_active_boosts(tmp_path, monkeypatch):
    shop_file = _setup_paths(tmp_path, monkeypatch)
    shop_file.write_text(
        json.dumps({"double_xp_1h": {"name": "Double XP", "price": 200}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    add_xp_mock = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", add_xp_mock)

    cog = EconomyUICog(object())

    async def buy():
        send_mock = AsyncMock()
        interaction = SimpleNamespace(
            data={"custom_id": "shop_buy:double_xp_1h"},
            user=SimpleNamespace(id=1, add_roles=AsyncMock()),
            guild=None,
            guild_id=123,
            response=SimpleNamespace(send_message=send_mock),
        )
        await cog.on_interaction(interaction)
        return send_mock

    for _ in range(2):
        await buy()

    boosts = economy.load_boosts()
    boosts["1"][0]["until"] = "2000-01-01T00:00:00+00:00"  # expired
    await economy.save_boosts(boosts)

    mock = await buy()
    assert "effectu" in mock.call_args.args[0].lower()
    assert add_xp_mock.await_count == 3
