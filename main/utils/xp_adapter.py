"""Adapter layer for XP operations used by ``pari_xp`` cog.

This module bridges the standalone ``pari_xp`` cog with the global
``xp_store`` used in the bot.  Functions are intentionally synchronous so
they can be called from synchronous contexts inside the cog.  Any
interaction with the asynchronous ``xp_store`` is scheduled in the
background using ``asyncio.create_task``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from typing import cast

from storage.xp_store import XPUserData, xp_store

DISCORD_EPOCH = 1420070400000


def get_user_xp(user_id: int) -> int:
    """Return the current XP balance for ``user_id``."""

    return int(xp_store.data.get(str(user_id), {}).get("xp", 0))


def add_user_xp(user_id: int, amount: int, guild_id: int, source: str = "pari_xp") -> None:
    """Schedule an XP change for ``user_id``.

    The underlying ``xp_store`` operation is asynchronous; we fire-and-forget
    the update so callers don't have to await it. ``source`` is recorded with
    the level change event.

    Any active double-XP buff is handled internally by ``xp_store``.
    """

    asyncio.create_task(
        xp_store.add_xp(user_id, amount, guild_id=guild_id, source=source)
    )


def get_user_account_age_days(user_id: int) -> int:
    """Return the account age in days based on the Discord snowflake."""

    timestamp = ((user_id >> 22) + DISCORD_EPOCH) / 1000
    created = datetime.utcfromtimestamp(timestamp)
    return (datetime.utcnow() - created).days


def apply_double_xp_buff(user_id: int, minutes: int = 60) -> None:
    """Grant a temporary double-XP buff to ``user_id``.

    The buff expires ``minutes`` after application and is persisted via the
    underlying ``xp_store``. Subsequent calls to :func:`add_user_xp` will honor
    the buff until it expires.
    """

    uid = str(user_id)
    expires_at = datetime.utcnow() + timedelta(minutes=minutes)
    user = xp_store.data.setdefault(uid, cast(XPUserData, {"xp": 0, "level": 0}))
    user["double_xp_until"] = expires_at.isoformat()
    # Persist buff information so it survives restarts.
    asyncio.create_task(xp_store.flush())


__all__ = [
    "get_user_xp",
    "add_user_xp",
    "get_user_account_age_days",
    "apply_double_xp_buff",
]

