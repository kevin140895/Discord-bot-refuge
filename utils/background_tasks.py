from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

__all__ = ["BackgroundTaskRegistry", "background_tasks"]

T = TypeVar("T")


class BackgroundTaskRegistry:
    """Own fire-and-forget asyncio tasks for the lifetime of the bot.

    ``asyncio`` only keeps weak references to scheduled tasks.  The registry
    therefore keeps a strong reference until completion, consumes task
    exceptions in one place, and provides a deterministic shutdown hook.
    """

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    @property
    def pending_count(self) -> int:
        """Return the number of tasks still owned by the registry."""

        return len(self._tasks)

    def create_task(
        self,
        coro: Coroutine[Any, Any, T],
        *,
        name: str,
    ) -> asyncio.Task[T]:
        """Schedule ``coro`` and retain it until its result is consumed."""

        if self._closed:
            coro.close()
            raise RuntimeError("background task registry is closed")

        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return

        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        except Exception:
            self._logger.exception(
                "failed to retrieve background task result: %s",
                task.get_name(),
            )
            return

        if exc is not None:
            self._logger.error(
                "background task failed: %s",
                task.get_name(),
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    async def aclose(self) -> None:
        """Cancel and await every task still owned by the registry."""

        if self._closed and not self._tasks:
            return

        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.difference_update(tasks)


background_tasks = BackgroundTaskRegistry()
