from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict

from storage.economy import TICKETS_FILE, transactions
from storage.roulette_store import RouletteStore
from utils.storage import load_json
from utils.persistence import atomic_write_json_async


async def consume_free_ticket(user_id: int) -> bool:
    """Consume one free ticket for ``user_id`` if available.

    Returns ``True`` if a ticket was consumed.
    Updates ``data/economy/tickets.json`` and logs the usage in
    ``transactions.json``.
    """
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
