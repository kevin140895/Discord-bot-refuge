import asyncio
import json
import os
import tempfile
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Union

_write_lock = asyncio.Lock()

async def atomic_write_json(path: Union[str, os.PathLike[str]], data: Any) -> None:
    """Write ``data`` to ``path`` atomically without blocking the event loop."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    async with _write_lock:
        def _write() -> None:
            fd, tmp_path = tempfile.mkstemp(dir=str(dest.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, dest)
            finally:
                try:
                    os.remove(tmp_path)
                except FileNotFoundError:
                    pass
        await asyncio.to_thread(_write)


_checkpoint_lock = asyncio.Lock()
_checkpoint_task: asyncio.Task | None = None
VOICE_CP_DEBOUNCE_SECONDS = float(os.getenv("VOICE_CP_DEBOUNCE_SECONDS", "2"))


async def schedule_checkpoint(
    save_fn: Callable[[], Awaitable[None]],
    delay: float = VOICE_CP_DEBOUNCE_SECONDS,
) -> None:
    """Schedule ``save_fn`` after ``delay`` seconds, debouncing rapid calls."""
    global _checkpoint_task
    async with _checkpoint_lock:
        if _checkpoint_task and not _checkpoint_task.done():
            _checkpoint_task.cancel()

        async def _run() -> None:
            try:
                await asyncio.sleep(delay)
                await save_fn()
                logging.info("💾 voice_times checkpoint saved")
            except asyncio.CancelledError:
                pass
            except Exception:
                logging.exception("voice_times checkpoint failed")

        _checkpoint_task = asyncio.create_task(_run())
