"""Persistent XP storage with debounced and periodic disk flushes."""

import asyncio
import contextlib
import logging
import math
import os
from typing import Dict, TypedDict, cast

from config import DATA_DIR
from utils.persistence import ensure_dir, read_json_safe, atomic_write_json_async

# Legacy bots stored XP in ``data.json``. To maintain compatibility with
# existing deployments that expect this filename, we default to writing XP
# data into ``data.json`` instead of ``xp.json``.
XP_PATH = os.path.join(DATA_DIR, "data.json")


class XPUserData(TypedDict, total=False):
    xp: int
    level: int
    double_xp_until: str


class XPStore:
    """Simple XP store with debounced and periodic flush."""

    def __init__(self, path: str = XP_PATH) -> None:
        self.path = path
        self.data: Dict[str, XPUserData] = {}
        self.lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._periodic_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._periodic_task and not self._periodic_task.done():
            return
        ensure_dir(DATA_DIR)
        self.data = read_json_safe(self.path)
        logging.info("DATA_DIR=%s", DATA_DIR)
        logging.info("XP_PATH=%s", self.path)
        self._periodic_task = asyncio.create_task(self._periodic_flush())

    async def aclose(self) -> None:
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            with contextlib.suppress(Exception):
                await self._flush_task
        if self._periodic_task:
            self._periodic_task.cancel()
            with contextlib.suppress(Exception):
                await self._periodic_task
        await self.flush()

    def _schedule_flush(self) -> None:
        if self._flush_task and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        try:
            await asyncio.sleep(300)
            await self.flush()
        except asyncio.CancelledError:
            pass

    async def _periodic_flush(self) -> None:
        try:
            while True:
                await asyncio.sleep(600)
                await self.flush()
        except asyncio.CancelledError:
            pass

    async def flush(self) -> None:
        async with self.lock:
            await atomic_write_json_async(self.path, self.data)
            logging.info("XP sauvegardé (%d utilisateurs)", len(self.data))

    async def add_xp(self, user_id: int, amount: int) -> tuple[int, int, int]:
        uid = str(user_id)
        async with self.lock:
            user = self.data.setdefault(uid, cast(XPUserData, {"xp": 0, "level": 0}))
            old_level = int(user.get("level", 0))
            if amount != 0:
                current_xp = int(user.get("xp", 0))
                new_xp = max(0, current_xp + int(amount))
                user["xp"] = new_xp
                new_level = self._calc_level(new_xp)
                if new_level != old_level:
                    user["level"] = new_level
            else:
                new_level = old_level
            total_xp = int(user.get("xp", 0))
        if amount != 0:
            self._schedule_flush()
        return old_level, new_level, total_xp

    @staticmethod
    def _calc_level(xp: int) -> int:
        try:
            return int(math.isqrt(xp // 100))
        except Exception:
            level = 0
            while xp >= (level + 1) ** 2 * 100:
                level += 1
            return level


xp_store = XPStore()
