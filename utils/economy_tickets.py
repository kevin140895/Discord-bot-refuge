from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Dict

from storage.economy import TICKETS_FILE, transactions
from utils.storage import load_json
from utils.persist import atomic_write_json


def consume_free_ticket(user_id: int) -> bool:
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
    atomic_write_json(TICKETS_FILE, tickets)

    asyncio.create_task(
        transactions.add(
            {
                "type": "ticket_usage",
                "user_id": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    )
    return True
