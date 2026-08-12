"""Handles queued channel rename operations with isolated per-channel workers."""

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
    """Coalesce channel renames without letting one channel stall the others.

    Discord rate limits can be scoped to a top-level ``channel_id``. discord.py
    can therefore legitimately wait inside ``channel.edit`` for one channel
    while another channel remains immediately available. The dispatcher only
    schedules work; each channel owns an independent task so such waits are
    isolated to the affected channel.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._pending: Dict[int, Tuple[discord.abc.GuildChannel, str]] = {}
        self._last_per_channel: Dict[int, float] = {}
        # TODO: consider periodic cleanup for IDs that are never reused
        self._last_global: float = 0.0
        self._global_dispatch_lock = asyncio.Lock()
        self._channel_tasks: Dict[int, asyncio.Task] = {}
        self._worker: asyncio.Task | None = None

    async def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="rename-manager")
            self._worker.add_done_callback(self._on_worker_done)

    async def aclose(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()

        channel_tasks = tuple(self._channel_tasks.values())
        for task in channel_tasks:
            if not task.done():
                task.cancel()

        if worker is not None:
            try:
                await worker
            except asyncio.CancelledError:
                pass

        if channel_tasks:
            await asyncio.gather(*channel_tasks, return_exceptions=True)

        self._channel_tasks.clear()
        self._pending.clear()

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

        cid = channel.id
        self._pending[cid] = (channel, new_name)

        task = self._channel_tasks.get(cid)
        if task is None or task.done():
            await self._queue.put(cid)
            logging.debug(
                "[rename_manager] queued rename %s -> %r", cid, new_name
            )
        else:
            logging.debug(
                "[rename_manager] coalesced rename %s -> %r", cid, new_name
            )

    async def _run(self) -> None:
        """Dispatch queued channel IDs without awaiting their network work."""

        while True:
            cid = await self._queue.get()
            task = self._channel_tasks.get(cid)
            if task is not None and not task.done():
                # A request can race with task cleanup. The active per-channel
                # task will consume the latest value from ``_pending``.
                self._queue.task_done()
                continue

            task = asyncio.create_task(
                self._process_channel_entry(cid),
                name=f"rename-channel-{cid}",
            )
            self._channel_tasks[cid] = task

    async def _process_channel_entry(self, cid: int) -> None:
        """Drain coalesced requests for one channel and finish one queue item."""

        try:
            await self._process_channel(cid)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception(
                "[rename_manager] channel worker %s encountered an error", cid
            )
        finally:
            current = asyncio.current_task()
            if self._channel_tasks.get(cid) is current:
                self._channel_tasks.pop(cid, None)

            # Mark the dispatcher item complete only when the channel task has
            # finished. Existing callers using ``queue.join`` therefore still
            # wait for the actual rename, not merely for scheduling.
            self._queue.task_done()

            # Close the small race where a request arrives after the drain loop
            # observed no pending work but before this task removed itself.
            if cid in self._pending and self._worker is not None:
                await self._queue.put(cid)

    async def _process_channel(self, cid: int) -> None:
        """Process only one channel; any rate-limit sleep stays local here."""

        while cid in self._pending:
            if CHANNEL_RENAME_DEBOUNCE_SECONDS > 0:
                await asyncio.sleep(CHANNEL_RENAME_DEBOUNCE_SECONDS)

            channel, name = self._pending.pop(cid, (None, None))
            if channel is None:
                return

            now = time.monotonic()
            last = self._last_per_channel.get(cid, 0.0)
            wait = CHANNEL_RENAME_MIN_INTERVAL_PER_CHANNEL - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)

            if channel.guild.get_channel(cid) is None:
                logging.debug(
                    "[rename_manager] channel %s deleted before rename; skipping",
                    cid,
                )
                self._last_per_channel.pop(cid, None)
                continue

            await self._edit_with_retries(channel, cid, name)

    async def _wait_for_global_dispatch_slot(self) -> None:
        """Space request starts without holding a lock during Discord I/O."""

        async with self._global_dispatch_lock:
            now = time.monotonic()
            wait = CHANNEL_RENAME_MIN_INTERVAL_GLOBAL - (now - self._last_global)
            if wait > 0:
                await asyncio.sleep(wait)
            # Reserve this slot before the network call starts. A subsequent
            # channel may therefore proceed after the configured spacing even
            # if this channel is sleeping inside discord.py for a route limit.
            self._last_global = time.monotonic()

    async def _edit_with_retries(
        self,
        channel: discord.abc.GuildChannel,
        cid: int,
        name: str,
    ) -> None:
        attempt = 0
        while True:
            await self._wait_for_global_dispatch_slot()
            start = time.monotonic()
            try:
                await channel.edit(name=name)
            except discord.NotFound:
                logging.warning("[rename_manager] channel %s not found", cid)
                return
            except discord.HTTPException as exc:
                if exc.status == 429 and attempt < CHANNEL_RENAME_MAX_RETRIES:
                    delay = CHANNEL_RENAME_BACKOFF_BASE ** attempt
                    logging.warning(
                        "[rename_manager] 429 on %s retry in %.1fs", cid, delay
                    )
                    # This sleep is intentionally local to this channel task.
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
                return
            else:
                latency = (time.monotonic() - start) * 1000
                logging.debug(
                    "[rename_manager] renamed %s to %r in %.1fms",
                    cid,
                    name,
                    latency,
                )
                self._last_per_channel[cid] = time.monotonic()
                return

    def _on_worker_done(self, task: asyncio.Task) -> None:
        """Log when the dispatcher stops unexpectedly."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logging.error("[rename_manager] worker crashed: %s", exc)
        else:
            logging.warning("[rename_manager] worker exited unexpectedly")


rename_manager = _RenameManager()
