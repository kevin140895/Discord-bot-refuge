"""XP adapter bridging Machine à sous with the global XP store."""
from __future__ import annotations

from storage.xp_store import xp_store


def get_balance(user_id: int) -> int:
    """Return current XP balance for ``user_id``."""
    return int(xp_store.data.get(str(user_id), {}).get("xp", 0))


async def add_xp(user_id: int, amount: int, guild_id: int, source: str) -> None:
    """Add (or remove) XP for ``user_id`` with event metadata."""
    await xp_store.add_xp(user_id, amount, guild_id=guild_id, source=source)
