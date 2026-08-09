"""XP adapter bridging Machine à sous with the global XP store."""
from __future__ import annotations

from storage.xp_store import xp_store
from utils.persistence import read_json_safe
from utils.refuge_casino_observer import observe_casino_xp_transaction


class InsufficientXPError(ValueError):
    """Raised when an atomic XP debit cannot be fully funded."""


def get_balance(user_id: int) -> int:
    """Return current XP balance for ``user_id``.

    The in-memory XP store is authoritative once a user is cached. If the
    requested user is absent, read the on-disk snapshot only as a fallback for
    the returned balance. The fallback must never merge the whole disk snapshot
    into ``xp_store.data`` because that could overwrite newer, unflushed XP for
    other users with stale persisted values.
    """

    uid = str(user_id)
    cached = xp_store.data.get(uid)
    if cached is not None:
        return int(cached.get("xp", 0))

    try:
        disk_data = read_json_safe(xp_store.path)
    except Exception:
        return 0

    if not isinstance(disk_data, dict):
        return 0
    disk_user = disk_data.get(uid)
    if not isinstance(disk_user, dict):
        return 0
    return int(disk_user.get("xp", 0))


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
        observe_casino_xp_transaction(
            user_id=user_id,
            source=source,
            requested_amount=amount,
            applied_delta=amount,
        )
        return

    result = await xp_store.add_xp(
        user_id,
        amount,
        guild_id=guild_id,
        source=source,
    )
    _old_level, _new_level, old_xp, new_xp = result
    observe_casino_xp_transaction(
        user_id=user_id,
        source=source,
        requested_amount=amount,
        applied_delta=new_xp - old_xp,
    )


__all__ = ["InsufficientXPError", "get_balance", "add_xp"]
