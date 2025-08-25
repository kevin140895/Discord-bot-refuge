import pytest

from storage import economy
from storage.transaction_store import TransactionStore


@pytest.mark.asyncio
async def test_economy_helpers(tmp_path, monkeypatch):
    monkeypatch.setattr(economy, 'BOOSTS_FILE', tmp_path / 'boosts.json')
    monkeypatch.setattr(economy, 'TICKETS_FILE', tmp_path / 'tickets.json')
    monkeypatch.setattr(economy, 'UI_FILE', tmp_path / 'ui.json')
    monkeypatch.setattr(economy, 'TRANSACTIONS_FILE', tmp_path / 'transactions.json')
    monkeypatch.setattr(economy, 'transactions', TransactionStore(tmp_path / 'transactions.json'))

    assert economy.load_boosts() == {}
    await economy.save_boosts({'user': 1})
    assert economy.load_boosts() == {'user': 1}

    assert economy.load_tickets() == {}
    await economy.save_tickets({'t': 2})
    assert economy.load_tickets() == {'t': 2}

    assert economy.load_ui() == {}
    await economy.save_ui({'a': 3})
    assert economy.load_ui() == {'a': 3}

    await economy.transactions.add({'id': 1})
    await economy.transactions.add({'id': 2})
    assert await economy.transactions.all() == [{'id': 1}, {'id': 2}]
