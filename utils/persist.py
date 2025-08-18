import json
import os
import tempfile
import logging
from pathlib import Path
from typing import Any


def ensure_dir(path: str | os.PathLike[str]) -> None:
    """Ensure that ``path`` exists as a directory."""
    Path(path).mkdir(parents=True, exist_ok=True)


def read_json_safe(path: str | os.PathLike[str]) -> dict:
    """Read JSON data from ``path``.

    If the file is missing or corrupted, attempt to read from ``path``
    with ``.bak`` appended. Returns an empty dict on failure.
    """
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        bak = p.with_suffix(p.suffix + ".bak")
        try:
            return json.loads(bak.read_text(encoding="utf-8"))
        except Exception:
            return {}


def atomic_write_json(path: str | os.PathLike[str], data: Any) -> None:
    """Atomically write ``data`` to ``path`` and keep a ``.bak`` backup."""
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
            except Exception:
                logging.exception("Failed to rotate backup for %s", dest)
        os.replace(tmp_path, dest)
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass
