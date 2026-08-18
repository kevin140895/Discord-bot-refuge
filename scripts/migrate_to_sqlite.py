"""Explicit one-shot migration/verification for critical JSON persistence."""

from __future__ import annotations

import asyncio
from pathlib import Path

from config import DATA_DIR
from storage.db import DB_PATH, SQLiteDatabase


async def main() -> int:
    data_dir = Path(DATA_DIR)
    db = SQLiteDatabase(DB_PATH)
    await db.start()

    xp_imported = await db.migrate_legacy_xp(data_dir / "data.json")
    voice_imported = await db.migrate_legacy_voice_times(
        data_dir / "voice_times.json"
    )
    integrity = await db.quick_check()

    print(f"SQLite: {DB_PATH}")
    print(f"XP importés: {xp_imported}")
    print(f"Checkpoints vocaux importés: {voice_imported}")
    print(f"PRAGMA quick_check: {integrity}")

    return 0 if integrity == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
