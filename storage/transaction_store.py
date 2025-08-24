from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, List

from utils.storage import load_json, save_json


class TransactionStore:
    """Simple JSON-backed transaction ledger.

    Transactions are kept in memory and persisted to ``path``. To avoid race
    conditions when multiple coroutines modify the ledger concurrently, all
    mutating operations are protected by an :class:`asyncio.Lock`.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        # Load existing transactions or start with an empty list. ``load_json``
        # returns ``{}`` when the file is missing or corrupted; ensure we get a
        # list to avoid attribute errors.
        data = load_json(self.path, [])
        self.transactions: List[Any] = data if isinstance(data, list) else []
        self._lock = asyncio.Lock()

    async def add(self, transaction: Any) -> None:
        """Append ``transaction`` to the ledger and persist to disk."""
        async with self._lock:
            self.transactions.append(transaction)
            await save_json(self.path, self.transactions)

    async def clear(self) -> None:
        """Remove all transactions and persist the empty ledger."""
        async with self._lock:
            self.transactions.clear()
            await save_json(self.path, self.transactions)

    async def all(self) -> list[Any]:
        """Return a shallow copy of the current transactions list."""
        async with self._lock:
            return list(self.transactions)
