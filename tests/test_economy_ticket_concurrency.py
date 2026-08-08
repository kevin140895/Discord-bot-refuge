import asyncio

import pytest

from storage.transaction_store import TransactionStore
from utils.persist import atomic_write_json
from utils.storage import load_json
import utils.economy_tickets as economy_tickets


@pytest.mark.asyncio
async def test_consume_free_ticket_is_atomic(tmp_path, monkeypatch):
    tickets_path = tmp_path / "tickets.json"
    transactions_path = tmp_path / "transactions.json"

    atomic_write_json(tickets_path, {"123": 1})
    transaction_store = TransactionStore(transactions_path)

    monkeypatch.setattr(economy_tickets, "TICKETS_FILE", tickets_path)
    monkeypatch.setattr(economy_tickets, "transactions", transaction_store)

    results = await asyncio.gather(
        economy_tickets.consume_free_ticket(123),
        economy_tickets.consume_free_ticket(123),
    )

    assert sorted(results) == [False, True]
    assert load_json(tickets_path, {}) == {}

    entries = await transaction_store.all()
    assert len(entries) == 1
    assert entries[0]["type"] == "ticket_usage"
    assert entries[0]["user_id"] == 123
