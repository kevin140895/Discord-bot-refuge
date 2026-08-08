"""Minimal JSON storage helpers for Machine à sous."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.persistence import ensure_dir, read_json_safe, atomic_write_json_async


def load_json(path: Path, default: Any) -> Any:
    """Load JSON data from ``path`` or return ``default`` if unreadable."""
    ensure_dir(path.parent)
    return read_json_safe(path, default=default)


async def save_json(path: Path, data: Any) -> None:
    """Asynchronously write JSON data to ``path``."""
    ensure_dir(path.parent)
    await atomic_write_json_async(path, data)
