import asyncio
import json

import pytest

from storage.transaction_store import TransactionStore


@pytest.mark.asyncio
async def test_concurrent_transaction_additions(tmp_path):
    path = tmp_path / "transactions.json"
    store = TransactionStore(path)

    async def worker(i: int):
        # Introduce a tiny delay to encourage task switching
        await asyncio.sleep(0)
        await store.add({"id": i})

    await asyncio.gather(*(worker(i) for i in range(50)))

    transactions = await store.all()
    assert len(transactions) == 50
    assert sorted(t["id"] for t in transactions) == list(range(50))

    with path.open() as f:
        data = json.load(f)
    assert len(data) == 50
