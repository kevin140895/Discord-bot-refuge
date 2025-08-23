"""Minimal JSON storage helpers for Roulette Refuge."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.persistence import ensure_dir, read_json_safe, atomic_write_json_async


def load_json(path: Path, default: Any) -> Any:
    """Load JSON data from ``path`` or return ``default`` if missing."""
    ensure_dir(path.parent)
    data = read_json_safe(path)
    return data if data is not None else default


async def save_json(path: Path, data: Any) -> None:
    """Asynchronously write JSON data to ``path``."""
    ensure_dir(path.parent)
    await atomic_write_json_async(path, data)
