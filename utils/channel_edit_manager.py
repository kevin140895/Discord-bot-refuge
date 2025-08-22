"""Queues and throttles channel edits to respect Discord rate limits."""

import asyncio
import logging
import time
from typing import Dict, Tuple

import discord

from config import (
    CHANNEL_EDIT_MIN_INTERVAL_SECONDS,
    CHANNEL_EDIT_DEBOUNCE_SECONDS,
    CHANNEL_EDIT_GLOBAL_MIN_INTERVAL_SECONDS,
)

from utils.metrics import errors


class _ChannelEditManager:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._pending: Dict[int, Tuple[discord.abc.GuildChannel, dict]] = {}
        self._last_per_channel: Dict[int, float] = {}
        self._last_global: float = 0.0
        self._worker: asyncio.Task | None = None

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())

    async def aclose(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def request(self, channel: discord.abc.GuildChannel, **kwargs) -> None:
        if all(getattr(channel, k, None) == v for k, v in kwargs.items()):
            logging.debug("[channel_edit_manager] no-op for %s", channel.id)
            return
        if channel.id in self._pending:
            self._pending[channel.id] = (channel, kwargs)
        else:
            self._pending[channel.id] = (channel, kwargs)
            await self._queue.put(channel.id)

    async def _run(self) -> None:
        while True:
            cid = await self._queue.get()
            channel, params = self._pending.pop(cid, (None, None))
            if channel is None:
                self._queue.task_done()
                continue

            if CHANNEL_EDIT_DEBOUNCE_SECONDS > 0:
                await asyncio.sleep(CHANNEL_EDIT_DEBOUNCE_SECONDS)

            now = time.monotonic()
            last = self._last_per_channel.get(cid, 0.0)
            wait = CHANNEL_EDIT_MIN_INTERVAL_SECONDS - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)

            now = time.monotonic()
            gwait = CHANNEL_EDIT_GLOBAL_MIN_INTERVAL_SECONDS - (
                now - self._last_global
            )
            if gwait > 0:
                await asyncio.sleep(gwait)

            try:
                await channel.edit(**params)
            except discord.NotFound:
                logging.warning("[channel_edit_manager] channel %s not found", cid)
            except discord.HTTPException as exc:
                logging.warning("[channel_edit_manager] edit failed for %s: %s", cid, exc)
                errors["channel_edit_failed"] += 1
            else:
                now = time.monotonic()
                self._last_per_channel[cid] = now
                self._last_global = now

            self._queue.task_done()


channel_edit_manager = _ChannelEditManager()
