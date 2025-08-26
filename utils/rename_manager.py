"""Handles queued channel rename operations with rate limiting and retries."""

import asyncio
import logging
import time
from typing import Dict, Tuple

import discord

from config import (
    CHANNEL_RENAME_BACKOFF_BASE,
    CHANNEL_RENAME_DEBOUNCE_SECONDS,
    CHANNEL_RENAME_MAX_RETRIES,
    CHANNEL_RENAME_MIN_INTERVAL_GLOBAL,
    CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL,
)

from utils.metrics import errors


class _RenameManager:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._pending: Dict[int, Tuple[discord.abc.GuildChannel, str]] = {}
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

    async def request(
        self, channel: discord.abc.GuildChannel, new_name: str
    ) -> None:
        if self._worker is None or self._worker.done():
            if self._worker is None:
                logging.warning("[rename_manager] worker absent; starting")
            else:
                logging.warning("[rename_manager] worker stopped; restarting")
            await self.start()
        if channel.name == new_name:
            logging.debug("[rename_manager] skip identical name for %s", channel.id)
            return
        if channel.id in self._pending:
            self._pending[channel.id] = (channel, new_name)
        else:
            self._pending[channel.id] = (channel, new_name)
            await self._queue.put(channel.id)
            logging.debug(
                "[rename_manager] queued rename %s -> %r", channel.id, new_name
            )

    async def _run(self) -> None:
        while True:
            cid: int | None = None
            try:
                cid = await self._queue.get()
                channel, name = self._pending.pop(cid, (None, None))
                if channel is None:
                    self._queue.task_done()
                    continue

                if CHANNEL_RENAME_DEBOUNCE_SECONDS > 0:
                    await asyncio.sleep(CHANNEL_RENAME_DEBOUNCE_SECONDS)

                now = time.monotonic()
                last = self._last_per_channel.get(cid, 0.0)
                wait = CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL - (now - last)
                if wait > 0:
                    await asyncio.sleep(wait)

                now = time.monotonic()
                gwait = CHANNEL_RENAME_MIN_INTERVAL_GLOBAL - (now - self._last_global)
                if gwait > 0:
                    await asyncio.sleep(gwait)

                if channel.guild.get_channel(cid) is None:
                    logging.debug(
                        "[rename_manager] channel %s deleted before rename; skipping",
                        cid,
                    )
                    self._queue.task_done()
                    continue

                attempt = 0
                while True:
                    start = time.monotonic()
                    try:
                        await channel.edit(name=name)
                    except discord.NotFound:
                        logging.warning("[rename_manager] channel %s not found", cid)
                        break
                    except discord.HTTPException as exc:
                        if exc.status == 429 and attempt < CHANNEL_RENAME_MAX_RETRIES:
                            delay = CHANNEL_RENAME_BACKOFF_BASE ** attempt
                            logging.warning(
                                "[rename_manager] 429 on %s retry in %.1fs", cid, delay
                            )
                            await asyncio.sleep(delay)
                            attempt += 1
                            continue
                        if exc.status == 403:
                            logging.warning(
                                "[rename_manager] permission insuffisante pour %s", cid
                            )
                        elif attempt:
                            logging.warning(
                                "[rename_manager] edit failed for %s after %d retries: %s",
                                cid,
                                attempt,
                                exc,
                            )
                        else:
                            logging.warning(
                                "[rename_manager] edit failed for %s: %s", cid, exc
                            )
                        errors["rename_failed"] += 1
                        break
                    else:
                        latency = (time.monotonic() - start) * 1000
                        logging.debug(
                            "[rename_manager] renamed %s to %r in %.1fms",
                            cid,
                            name,
                            latency,
                        )
                        now = time.monotonic()
                        self._last_per_channel[cid] = now
                        self._last_global = now
                        break

                self._queue.task_done()
            except Exception:
                logging.exception("[rename_manager] worker encountered an error")
                if cid is not None:
                    self._queue.task_done()


rename_manager = _RenameManager()

