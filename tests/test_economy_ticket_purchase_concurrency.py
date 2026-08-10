import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cogs.economy_ui as economy_ui
from cogs.economy_ui import EconomyUICog
from storage import economy
from storage.transaction_store import TransactionStore
import utils.economy_tickets as economy_tickets
from utils.persistence import atomic_write_json_async
from utils.storage import load_json


def _setup_paths(tmp_path, monkeypatch):
    shop_file = tmp_path / "shop.json"
    tickets_file = tmp_path / "tickets.json"
    transactions_file = tmp_path / "transactions.json"

    shop_file.write_text(
        json.dumps({"ticket_royal": {"name": "Ticket Royal", "price": 100}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(economy, "SHOP_FILE", shop_file)
    monkeypatch.setattr(economy_ui, "SHOP_FILE", shop_file)
    monkeypatch.setattr(economy, "TICKETS_FILE", tickets_file)
    monkeypatch.setattr(economy_tickets, "TICKETS_FILE", tickets_file)

    transaction_store = TransactionStore(transactions_file)
    monkeypatch.setattr(economy, "transactions", transaction_store)
    monkeypatch.setattr(economy_ui, "transactions", transaction_store)
    monkeypatch.setattr(economy_tickets, "transactions", transaction_store)

    return tickets_file, transaction_store


def _interaction(user_id: int = 1):
    return SimpleNamespace(
        data={"custom_id": "shop_buy:ticket_royal"},
        user=SimpleNamespace(id=user_id, add_roles=AsyncMock()),
        guild=None,
        guild_id=123,
        response=SimpleNamespace(send_message=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_concurrent_ticket_purchases_share_limit_and_debit_boundary(
    tmp_path, monkeypatch
):
    tickets_file, transaction_store = _setup_paths(tmp_path, monkeypatch)
    await economy.save_tickets({"1": 2})

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)

    first_debit_started = asyncio.Event()
    release_first_debit = asyncio.Event()
    debit_calls = []

    async def blocked_debit(user_id, *, amount, guild_id, source):
        debit_calls.append((user_id, amount, guild_id, source))
        first_debit_started.set()
        await release_first_debit.wait()

    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", blocked_debit)

    cog = EconomyUICog(object())
    first = _interaction()
    second = _interaction()

    first_task = asyncio.create_task(
        cog._handle_shop_purchase(first, "ticket_royal")
    )
    await first_debit_started.wait()

    second_task = asyncio.create_task(
        cog._handle_shop_purchase(second, "ticket_royal")
    )
    await asyncio.sleep(0)

    # The second purchase must still be waiting on the shared ticket lock. If it
    # reached the debit concurrently, both purchases could charge XP at stock 2.
    assert len(debit_calls) == 1

    release_first_debit.set()
    await asyncio.gather(first_task, second_task)

    assert load_json(tickets_file, {}) == {"1": 3}
    assert len(debit_calls) == 1

    responses = [
        first.response.send_message.await_args.args[0],
        second.response.send_message.await_args.args[0],
    ]
    assert sum("effectu" in response.lower() for response in responses) == 1
    assert sum("stock" in response.lower() for response in responses) == 1

    entries = await transaction_store.all()
    purchases = [entry for entry in entries if entry.get("type") == "buy"]
    assert len(purchases) == 1
    assert purchases[0]["item"] == "ticket_royal"


@pytest.mark.asyncio
async def test_ticket_purchase_waits_for_concurrent_consumption(tmp_path, monkeypatch):
    tickets_file, transaction_store = _setup_paths(tmp_path, monkeypatch)
    await economy.save_tickets({"1": 1})

    write_started = asyncio.Event()
    release_write = asyncio.Event()

    async def blocked_ticket_write(path, data):
        write_started.set()
        await release_write.wait()
        await atomic_write_json_async(path, data)

    monkeypatch.setattr(
        economy_tickets,
        "atomic_write_json_async",
        blocked_ticket_write,
    )

    monkeypatch.setattr(economy_ui.xp_adapter, "get_balance", lambda _uid: 1000)
    debit = AsyncMock()
    monkeypatch.setattr(economy_ui.xp_adapter, "add_xp", debit)

    consume_task = asyncio.create_task(economy_tickets.consume_free_ticket(1))
    await write_started.wait()

    cog = EconomyUICog(object())
    interaction = _interaction()
    purchase_task = asyncio.create_task(
        cog._handle_shop_purchase(interaction, "ticket_royal")
    )
    await asyncio.sleep(0)

    # Consumption still owns the same shared lock, so purchase cannot debit XP
    # until the decrement has been durably written.
    debit.assert_not_awaited()

    release_write.set()
    consumed, _ = await asyncio.gather(consume_task, purchase_task)

    assert consumed is True
    debit.assert_awaited_once_with(1, amount=-100, guild_id=123, source="shop")
    assert load_json(tickets_file, {}) == {"1": 1}

    entries = await transaction_store.all()
    assert sorted(entry["type"] for entry in entries) == ["buy", "ticket_usage"]
