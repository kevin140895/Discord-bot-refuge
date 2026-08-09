from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from storage.refuge_casino_activity_store import refuge_casino_activity_store


logger = logging.getLogger(__name__)
_CASINO_SOURCES = frozenset({"pari_xp", "machine_a_sous"})


async def _record(
    *,
    user_id: int,
    source: str,
    requested_amount: int,
    applied_delta: int,
    at: datetime | None,
) -> None:
    try:
        await refuge_casino_activity_store.record_transaction(
            user_id=user_id,
            source=source,
            requested_amount=requested_amount,
            applied_delta=applied_delta,
            at=at,
        )
    except Exception:
        logger.exception(
            "[refuge] casino XP observation failed for source=%s user=%s",
            source,
            user_id,
        )


def observe_casino_xp_transaction(
    *,
    user_id: int,
    source: str,
    requested_amount: int,
    applied_delta: int,
    at: datetime | None = None,
) -> None:
    """Schedule best-effort Refuge observation without blocking XP logic."""

    normalized = str(source).strip()
    if normalized not in _CASINO_SOURCES:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        _record(
            user_id=int(user_id),
            source=normalized,
            requested_amount=int(requested_amount),
            applied_delta=int(applied_delta),
            at=at,
        )
    )


__all__ = ["observe_casino_xp_transaction"]
