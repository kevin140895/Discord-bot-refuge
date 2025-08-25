from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from storage.transaction_store import TransactionStore
from utils.storage import load_json, save_json

# Base directory for economy data files
ECONOMY_DIR = Path(__file__).resolve().parent.parent / "data" / "economy"

# Paths for various economy files
SHOP_FILE = ECONOMY_DIR / "shop.json"
TRANSACTIONS_FILE = ECONOMY_DIR / "transactions.json"
BOOSTS_FILE = ECONOMY_DIR / "boosts.json"
TICKETS_FILE = ECONOMY_DIR / "tickets.json"
UI_FILE = ECONOMY_DIR / "ui.json"

# Append-only transaction ledger
transactions = TransactionStore(TRANSACTIONS_FILE)


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
