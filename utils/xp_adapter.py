"""XP adapter bridging Machine à sous with the global XP store."""
from __future__ import annotations

from datetime import datetime
import logging

from storage.xp_store import xp_store
from utils.persistence import read_json_safe
from utils.refuge_casino_observer import observe_casino_xp_transaction

logger = logging.getLogger(__name__)


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


async def refund_xp_exact(
    user_id: int,
    amount: int,
    guild_id: int,
    source: str = "shop_refund",
) -> None:
    """Credit exactly ``amount`` XP without applying any XP multiplier.

    This is intentionally separate from :func:`add_xp`: compensation must
    restore the exact amount previously debited even if a legacy or personal
    Double XP flag is active. The mutation is applied under the XP store lock
    and flushed immediately because this function is only used on failure paths
    where durability matters more than batching.
    """
    if amount < 0:
        raise ValueError("refund amount must be non-negative")
    if amount == 0:
        return
    if amount > 10_000:
        raise ValueError("refund amount exceeds maximum XP transaction")

    uid = str(user_id)
    async with xp_store.lock:
        if uid not in xp_store.data:
            disk_data = read_json_safe(xp_store.path)
            disk_user = disk_data.get(uid) if isinstance(disk_data, dict) else None
            xp_store.data[uid] = (
                dict(disk_user)
                if isinstance(disk_user, dict)
                else {"xp": 0, "level": 0}
            )

        user = xp_store.data[uid]
        old_xp = int(user.get("xp", 0))
        old_level = int(user.get("level", xp_store._calc_level(old_xp)))
        new_xp = old_xp + amount
        new_level = xp_store._calc_level(new_xp)

        user["xp"] = new_xp
        user["level"] = new_level
        user["last_accessed"] = datetime.utcnow().isoformat()
        xp_store.stats["total_updates"] += 1

    # A compensation must survive a process crash immediately after the failed
    # purchase. Flush synchronously instead of relying on the normal delayed
    # batching path.
    await xp_store.flush()

    if new_level != old_level and guild_id:
        try:
            from utils.level_feed import LevelChange, emit

            emit(
                LevelChange(
                    user_id=user_id,
                    guild_id=guild_id,
                    old_level=old_level,
                    new_level=new_level,
                    old_xp=old_xp,
                    new_xp=new_xp,
                    source=source,
                )
            )
        except Exception:  # pragma: no cover - ancillary notification only
            logger.exception(
                "Impossible d'émettre le changement de niveau après remboursement XP"
            )

    try:
        observe_casino_xp_transaction(
            user_id=user_id,
            source=source,
            requested_amount=amount,
            applied_delta=amount,
        )
    except Exception:  # pragma: no cover - analytics must not invalidate refund
        logger.exception("Impossible d'observer le remboursement XP")


__all__ = [
    "InsufficientXPError",
    "get_balance",
    "add_xp",
    "refund_xp_exact",
]
