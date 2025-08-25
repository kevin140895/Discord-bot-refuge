import asyncio
import json
import os
import tempfile
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

__all__ = [
    "ensure_dir",
    "read_json_safe",
    "atomic_write_json",
    "atomic_write_json_async",
    "schedule_checkpoint",
]

_write_lock: asyncio.Lock | None = None
_write_lock_loop: asyncio.AbstractEventLoop | None = None


def ensure_dir(path: str | os.PathLike[str]) -> None:
    """Ensure that ``path`` exists as a directory."""
    Path(path).mkdir(parents=True, exist_ok=True)


def read_json_safe(path: str | os.PathLike[str]) -> dict:
    """Read JSON data from ``path``.

    If the file is missing or corrupted, attempt to read from ``path`` with
    ``.bak`` appended. Returns an empty dict on failure.
    """
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.warning("JSON file %s not found; trying backup", p)
    except json.JSONDecodeError:
        logging.warning("JSON file %s is corrupted; trying backup", p)
    except OSError as e:
        logging.warning("Error reading %s: %s", p, e)

    bak = p.with_suffix(p.suffix + ".bak")
    try:
        return json.loads(bak.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logging.warning("Backup file %s not found", bak)
        return {}
    except json.JSONDecodeError:
        logging.warning("Backup file %s is corrupted", bak)
        return {}
    except OSError as e:
        logging.warning("Error reading backup %s: %s", bak, e)
        return {}


def atomic_write_json(path: str | os.PathLike[str], data: Any) -> None:
    """Atomically write ``data`` to ``path`` and keep a ``.bak`` backup.

    This function blocks; use :func:`atomic_write_json_async` in async code.
    """
    dest = Path(path)
    ensure_dir(dest.parent)
    backup = dest.with_suffix(dest.suffix + ".bak")

    fd, tmp_path = tempfile.mkstemp(dir=str(dest.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        if dest.exists():
            try:
                os.replace(dest, backup)
            except OSError:
                logging.exception("Failed to rotate backup for %s", dest)
        os.replace(tmp_path, dest)
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


async def atomic_write_json_async(path: str | os.PathLike[str], data: Any) -> None:
    """Asynchronously write JSON data using :func:`atomic_write_json`.

    The write is executed in a thread and serialized with an event-loop-aware
    lock to avoid concurrent writes across different loops.
    """
    global _write_lock, _write_lock_loop
    loop = asyncio.get_running_loop()
    if _write_lock is None or _write_lock_loop is not loop:
        _write_lock = asyncio.Lock()
        _write_lock_loop = loop
    async with _write_lock:
        await asyncio.to_thread(atomic_write_json, path, data)


_checkpoint_lock = asyncio.Lock()
_checkpoint_task: asyncio.Task | None = None
# Interval (seconds) between automatic voice_times checkpoints.
# Defaults to 5 minutes to reduce disk writes.
VOICE_CP_DEBOUNCE_SECONDS = float(os.getenv("VOICE_CP_DEBOUNCE_SECONDS", "300"))


async def schedule_checkpoint(
    save_fn: Callable[[], Awaitable[None]],
    delay: float = VOICE_CP_DEBOUNCE_SECONDS,
) -> None:
    """Schedule ``save_fn`` to run after ``delay`` seconds.

    Unlike a debounce, once a checkpoint is scheduled it will not be
    cancelled by subsequent calls. This throttles saves so they happen at
    most once per ``delay`` interval.
    """
    global _checkpoint_task
    async with _checkpoint_lock:
        if _checkpoint_task and not _checkpoint_task.done():
            # A checkpoint is already scheduled; do nothing to avoid rescheduling
            return

        async def _run() -> None:
            global _checkpoint_task
            try:
                await asyncio.sleep(delay)
                await save_fn()
                logging.info("💾 voice_times checkpoint saved")
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logging.exception("voice_times checkpoint failed: %s", exc)
                raise
            finally:
                # Allow future checkpoints to be scheduled
                _checkpoint_task = None

        _checkpoint_task = asyncio.create_task(_run())

