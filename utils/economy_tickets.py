from __future__ import annotations

import asyncio
import weakref
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict

from storage.economy import TICKETS_FILE, transactions
from storage.roulette_store import RouletteStore
from utils.storage import load_json
from utils.persistence import atomic_write_json_async


# ``asyncio.Lock`` instances are bound to the event loop that contends on
# them. Keep one lock per live loop so production has a single transaction
# boundary while tests that use separate event loops remain isolated.
_ticket_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _get_ticket_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _ticket_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _ticket_locks[loop] = lock
    return lock


async def consume_free_ticket(user_id: int) -> bool:
    """Consume one free ticket for ``user_id`` if available.

    The read/check/decrement/write sequence is serialized so two concurrent
    consumers cannot both spend the same ticket. Returns ``True`` only when a
    ticket was actually consumed, then records that successful usage in the
    transaction ledger.
    """
    async with _get_ticket_lock():
        tickets: Dict[str, int] = load_json(TICKETS_FILE, {})
        key = str(user_id)
        count = int(tickets.get(key, 0))
        if count <= 0:
            return False

        count -= 1
        if count:
            tickets[key] = count
        else:
            tickets.pop(key, None)
        await atomic_write_json_async(TICKETS_FILE, tickets)

        await transactions.add(
            {
                "type": "ticket_usage",
                "user_id": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return True


async def consume_any_ticket(
    user_id: int,
    store: RouletteStore | None = None,
    consume: Callable[[int], Awaitable[bool]] = consume_free_ticket,
) -> bool:
    """Consume a ticket from economy or the roulette store.

    Attempts to consume an economy ticket via ``consume`` first. If none are
    available and ``store`` is provided, a ticket from that store is used.
    Returns ``True`` if a ticket was consumed.
    """
    if await consume(user_id):
        return True
    if store and store.use_ticket(str(user_id)):
        return True
    return False
