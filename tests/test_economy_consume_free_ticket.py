from utils.economy_tickets import consume_free_ticket
import utils.economy_tickets as et
from storage.transaction_store import TransactionStore
from utils.persist import atomic_write_json
from utils.storage import load_json


async def test_consume_free_ticket(tmp_path):
    ticket_path = tmp_path / "tickets.json"
    tx_path = tmp_path / "transactions.json"
    atomic_write_json(ticket_path, {"123": 1})
    # Monkeypatch paths and transactions store
    et.TICKETS_FILE = ticket_path
    et.transactions = TransactionStore(tx_path)

    assert await consume_free_ticket(123) is True
    assert await consume_free_ticket(123) is False
    assert load_json(ticket_path, {}) == {}
    txs = await et.transactions.all()
    assert txs and txs[0]["type"] == "ticket_usage"
