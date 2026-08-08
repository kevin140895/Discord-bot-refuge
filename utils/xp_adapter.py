"""XP adapter bridging Machine à sous with the global XP store."""
from __future__ import annotations

from storage.xp_store import xp_store
from utils.persistence import read_json_safe


class InsufficientXPError(ValueError):
    """Raised when an atomic XP debit cannot be fully funded."""


def get_balance(user_id: int) -> int:
    """Return current XP balance for ``user_id``.

    The underlying :mod:`storage.xp_store` lazily loads its data and may not
    be initialised when this function is called. If the requested user is not
    present in the in-memory cache we fallback to reading the JSON file from
    disk so that features relying on the XP store can operate even before it
    is fully started.
    """

    uid = str(user_id)
    if uid not in xp_store.data:
        # Load the on-disk data lazily. ``read_json_safe`` returns ``{}`` on
        # failure so this remains a best-effort operation.
        try:
            xp_store.data.update(read_json_safe(xp_store.path))
        except Exception:
            # If anything goes wrong we leave the cache untouched and return 0
            # below. This mirrors the previous behaviour while avoiding hard
            # failures during bot start-up.
            pass

    return int(xp_store.data.get(uid, {}).get("xp", 0))


async def add_xp(user_id: int, amount: int, guild_id: int, source: str) -> None:
    """Add or spend XP for ``user_id`` with event metadata.

    Positive amounts keep the historical :meth:`XPStore.add_xp` behaviour.
    Negative amounts are treated as purchases/spends and therefore use the
    atomic :meth:`XPStore.try_spend_xp` operation: an insufficient balance
    raises :class:`InsufficientXPError` instead of silently clamping to zero.
    """
    if amount < 0:
        spent = await xp_store.try_spend_xp(
            user_id,
            -amount,
            guild_id=guild_id,
            source=source,
        )
        if not spent:
            raise InsufficientXPError(
                f"user {user_id} cannot spend {-amount} XP"
            )
        return

    await xp_store.add_xp(user_id, amount, guild_id=guild_id, source=source)


__all__ = ["InsufficientXPError", "get_balance", "add_xp"]
