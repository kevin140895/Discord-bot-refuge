from __future__ import annotations

import asyncio
import weakref
from pathlib import Path
from typing import Any, Dict

from config import DATA_DIR
from storage.transaction_store import TransactionStore
from utils.storage import load_json, save_json

# All persistent economy files live below the configured persistent root.
ECONOMY_DIR = Path(DATA_DIR) / "economy"

# Paths for various economy files
SHOP_FILE = ECONOMY_DIR / "shop.json"
TRANSACTIONS_FILE = ECONOMY_DIR / "transactions.json"
BOOSTS_FILE = ECONOMY_DIR / "boosts.json"
TICKETS_FILE = ECONOMY_DIR / "tickets.json"
UI_FILE = ECONOMY_DIR / "ui.json"

# Append-only transaction ledger
transactions = TransactionStore(TRANSACTIONS_FILE)


# Ticket stock uses read/check/mutate/write transactions from multiple modules.
# Keep one shared lock per live event loop so purchases and consumptions cannot
# overwrite each other while tests that create separate loops stay isolated.
_ticket_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def get_ticket_lock() -> asyncio.Lock:
    """Return the shared ticket transaction lock for the running event loop."""
    loop = asyncio.get_running_loop()
    lock = _ticket_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _ticket_locks[loop] = lock
    return lock


def load_boosts() -> Dict[str, Any]:
    """Load boosts from disk or return an empty dict."""
    return load_json(BOOSTS_FILE, {})


async def save_boosts(data: Dict[str, Any]) -> None:
    """Persist boosts to disk."""
    await save_json(BOOSTS_FILE, data)


def load_tickets() -> Dict[str, Any]:
    """Load tickets data from disk or return an empty dict."""
    return load_json(TICKETS_FILE, {})


async def save_tickets(data: Dict[str, Any]) -> None:
    """Persist tickets data to disk."""
    await save_json(TICKETS_FILE, data)


def load_ui() -> Dict[str, Any]:
    """Load UI configuration from disk or return an empty dict."""
    return load_json(UI_FILE, {})


async def save_ui(data: Dict[str, Any]) -> None:
    """Persist UI configuration to disk."""
    await save_json(UI_FILE, data)
