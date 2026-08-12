import asyncio
import json
import os
import tempfile
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from utils.background_tasks import background_tasks

__all__ = [
    "ensure_dir",
    "read_json_safe",
    "atomic_write_json",
    "atomic_write_json_async",
    "schedule_checkpoint",
]

_write_lock: asyncio.Lock | None = None
_write_lock_loop: asyncio.AbstractEventLoop | None = None
_DEFAULT_JSON_FALLBACK = object()


def ensure_dir(path: str | os.PathLike[str]) -> None:
    """Ensure that ``path`` exists as a directory."""
    Path(path).mkdir(parents=True, exist_ok=True)


def read_json_safe(
    path: str | os.PathLike[str], default: Any = _DEFAULT_JSON_FALLBACK
) -> Any:
    """Read JSON data from ``path`` with backup fallback.

    If the primary file is missing or corrupted, ``path.bak`` is attempted.
    When neither file can be read, return ``default`` when explicitly
    provided; otherwise preserve the historical empty-dict fallback.
    """
    fallback = {} if default is _DEFAULT_JSON_FALLBACK else default
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
        return fallback
    except json.JSONDecodeError:
        logging.warning("Backup file %s is corrupted", bak)
        return fallback
    except OSError as e:
        logging.warning("Error reading backup %s: %s", bak, e)
        return fallback


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
_checkpoint_tasks: dict[Callable[[], Awaitable[None]], asyncio.Task[None]] = {}
# Interval (seconds) between automatic checkpoints.
# Defaults to 5 minutes to reduce disk writes.
VOICE_CP_DEBOUNCE_SECONDS = float(os.getenv("VOICE_CP_DEBOUNCE_SECONDS", "300"))


async def schedule_checkpoint(
    save_fn: Callable[[], Awaitable[None]],
    delay: float = VOICE_CP_DEBOUNCE_SECONDS,
) -> None:
    """Schedule ``save_fn`` to run after ``delay`` seconds.

    Each save function owns an independent checkpoint slot. Repeated calls for
    the same function are throttled while its checkpoint is pending, but a
    checkpoint for another file/function can be scheduled at the same time.

    The shared background-task registry owns the task lifetime and consumes any
    unhandled exception so checkpoint failures cannot become orphaned task
    exceptions.
    """
    async with _checkpoint_lock:
        existing_task = _checkpoint_tasks.get(save_fn)
        if existing_task and not existing_task.done():
            return

        save_name = getattr(
            save_fn,
            "__qualname__",
            getattr(save_fn, "__name__", repr(save_fn)),
        )

        async def _run() -> None:
            try:
                await asyncio.sleep(delay)
                await save_fn()
                logging.info("💾 checkpoint saved: %s", save_name)
            finally:
                async with _checkpoint_lock:
                    current_task = asyncio.current_task()
                    if _checkpoint_tasks.get(save_fn) is current_task:
                        _checkpoint_tasks.pop(save_fn, None)

        _checkpoint_tasks[save_fn] = background_tasks.create_task(
            _run(),
            name=f"checkpoint:{save_name}",
        )
