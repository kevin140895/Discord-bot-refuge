from __future__ import annotations

import asyncio
import weakref


_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def refuge_world_mutation_lock() -> asyncio.Lock:
    """Return one loop-local lock shared by Refuge world orchestrators."""

    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _locks[loop] = lock
    return lock


__all__ = ["refuge_world_mutation_lock"]
