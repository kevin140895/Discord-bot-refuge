"""SQLite persistence primitives for critical Refuge bot state.

The bot keeps its hot XP/voice state in memory and persists snapshots in the
background.  SQLite therefore only sits on the persistence path: blocking
``sqlite3`` work is moved off the Discord event loop with ``asyncio.to_thread``
and all write transactions are serialized by one asyncio lock.

This deliberately avoids adding an async SQLite dependency for the first
migration phase.  The standard-library driver is sufficient for the bot's
batched write pattern and keeps the production dependency lock unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from config import DATA_DIR
from utils.persistence import read_json_safe


logger = logging.getLogger(__name__)
DB_PATH = Path(DATA_DIR) / "refuge.db"


class SQLiteDatabase:
    """Small async facade around the standard-library SQLite driver."""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = Path(path)
        self._write_lock = asyncio.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            # WAL lets readers proceed while the short persistence transaction
            # is committed. Railway volumes are single-replica, so there is no
            # shared-network-filesystem writer topology to support here.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS xp (
                    user_id INTEGER PRIMARY KEY,
                    xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
                    level INTEGER NOT NULL DEFAULT 0 CHECK (level >= 0),
                    double_xp_until TEXT,
                    last_accessed TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_xp_score
                    ON xp (xp DESC);

                CREATE TABLE IF NOT EXISTS voice_times (
                    user_id INTEGER PRIMARY KEY,
                    joined_at TEXT NOT NULL
                );
                """
            )
            connection.commit()

    async def start(self) -> None:
        """Create the database and schema once for the current process."""
        if self._initialized:
            return
        async with self._write_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True
            logger.info("SQLite persistence ready: %s", self.path)

    async def aclose(self) -> None:
        """Lifecycle symmetry; connections are short-lived per operation."""
        self._initialized = False

    async def _run_write(self, func, *args):
        await self.start()
        async with self._write_lock:
            return await asyncio.to_thread(func, *args)

    async def load_xp(self) -> dict[str, dict[str, object]]:
        await self.start()

        def _load() -> dict[str, dict[str, object]]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT user_id, xp, level, double_xp_until, last_accessed
                    FROM xp
                    """
                ).fetchall()

            result: dict[str, dict[str, object]] = {}
            for row in rows:
                payload: dict[str, object] = {
                    "xp": int(row["xp"]),
                    "level": int(row["level"]),
                }
                if row["double_xp_until"] is not None:
                    payload["double_xp_until"] = str(row["double_xp_until"])
                if row["last_accessed"] is not None:
                    payload["last_accessed"] = str(row["last_accessed"])
                result[str(row["user_id"])] = payload
            return result

        return await asyncio.to_thread(_load)

    def _upsert_xp_sync(self, rows: list[tuple[object, ...]]) -> None:
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO xp (
                    user_id, xp, level, double_xp_until, last_accessed
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    xp = excluded.xp,
                    level = excluded.level,
                    double_xp_until = excluded.double_xp_until,
                    last_accessed = excluded.last_accessed
                """,
                rows,
            )
            connection.commit()

    async def upsert_xp(self, data: Mapping[str, Mapping[str, object]]) -> None:
        """Persist the supplied XP rows in one transaction."""
        rows: list[tuple[object, ...]] = []
        for uid, payload in data.items():
            rows.append(
                (
                    int(uid),
                    max(0, int(payload.get("xp", 0))),
                    max(0, int(payload.get("level", 0))),
                    payload.get("double_xp_until"),
                    payload.get("last_accessed"),
                )
            )
        await self._run_write(self._upsert_xp_sync, rows)

    async def load_voice_times(self) -> dict[str, str]:
        await self.start()

        def _load() -> dict[str, str]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT user_id, joined_at FROM voice_times"
                ).fetchall()
            return {str(row["user_id"]): str(row["joined_at"]) for row in rows}

        return await asyncio.to_thread(_load)

    def _replace_voice_times_sync(self, rows: list[tuple[int, str]]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM voice_times")
            if rows:
                connection.executemany(
                    "INSERT INTO voice_times (user_id, joined_at) VALUES (?, ?)",
                    rows,
                )
            connection.commit()

    async def replace_voice_times(self, data: Mapping[str, str]) -> None:
        """Atomically replace the active voice-session checkpoint."""
        rows = [(int(uid), str(joined_at)) for uid, joined_at in data.items()]
        await self._run_write(self._replace_voice_times_sync, rows)

    def _import_xp_sync(
        self,
        rows: list[tuple[object, ...]],
        source: str,
    ) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            marker = connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?",
                ("legacy_xp_json_v1",),
            ).fetchone()
            if marker is not None:
                connection.rollback()
                return 0

            existing = int(connection.execute("SELECT COUNT(*) FROM xp").fetchone()[0])
            imported = 0
            if existing == 0 and rows:
                connection.executemany(
                    """
                    INSERT INTO xp (
                        user_id, xp, level, double_xp_until, last_accessed
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                imported = len(rows)

            connection.execute(
                "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                (
                    "legacy_xp_json_v1",
                    f"{datetime.now(timezone.utc).isoformat()}|{source}|{imported}",
                ),
            )
            connection.commit()
            return imported

    async def migrate_legacy_xp(self, legacy_path: str | Path) -> int:
        """Import the historical ``data.json`` exactly once when available."""
        await self.start()
        source_path = Path(legacy_path)
        raw = await asyncio.to_thread(read_json_safe, source_path, None)
        if raw is None:
            return 0
        if not isinstance(raw, dict):
            raise ValueError(f"Legacy XP payload must be an object: {source_path}")

        rows: list[tuple[object, ...]] = []
        for uid, payload in raw.items():
            if not isinstance(payload, dict):
                raise ValueError(f"Invalid XP record for user {uid!r}")
            rows.append(
                (
                    int(uid),
                    max(0, int(payload.get("xp", 0))),
                    max(0, int(payload.get("level", 0))),
                    payload.get("double_xp_until"),
                    payload.get("last_accessed"),
                )
            )

        imported = await self._run_write(
            self._import_xp_sync,
            rows,
            str(source_path),
        )
        if imported:
            logger.info("Migrated %d XP rows from %s", imported, source_path)
        return imported

    def _import_voice_sync(
        self,
        rows: list[tuple[int, str]],
        source: str,
    ) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            marker = connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?",
                ("legacy_voice_times_json_v1",),
            ).fetchone()
            if marker is not None:
                connection.rollback()
                return 0

            existing = int(
                connection.execute("SELECT COUNT(*) FROM voice_times").fetchone()[0]
            )
            imported = 0
            if existing == 0 and rows:
                connection.executemany(
                    "INSERT INTO voice_times (user_id, joined_at) VALUES (?, ?)",
                    rows,
                )
                imported = len(rows)

            connection.execute(
                "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                (
                    "legacy_voice_times_json_v1",
                    f"{datetime.now(timezone.utc).isoformat()}|{source}|{imported}",
                ),
            )
            connection.commit()
            return imported

    async def migrate_legacy_voice_times(self, legacy_path: str | Path) -> int:
        """Import the historical active voice checkpoint exactly once."""
        await self.start()
        source_path = Path(legacy_path)
        raw = await asyncio.to_thread(read_json_safe, source_path, None)
        if raw is None:
            return 0
        if not isinstance(raw, dict):
            raise ValueError(f"Legacy voice payload must be an object: {source_path}")

        rows: list[tuple[int, str]] = []
        for uid, joined_at in raw.items():
            # Validate that the legacy timestamp is parseable before committing
            # anything. The original timezone offset is kept verbatim.
            datetime.fromisoformat(str(joined_at))
            rows.append((int(uid), str(joined_at)))

        imported = await self._run_write(
            self._import_voice_sync,
            rows,
            str(source_path),
        )
        if imported:
            logger.info("Migrated %d voice checkpoints from %s", imported, source_path)
        return imported

    async def quick_check(self) -> str:
        """Return SQLite's quick integrity-check result."""
        await self.start()

        def _check() -> str:
            with self._connect() as connection:
                row = connection.execute("PRAGMA quick_check").fetchone()
                return str(row[0]) if row else "unknown"

        return await asyncio.to_thread(_check)


# Shared process-wide database. Tests that need isolation can instantiate their
# own SQLiteDatabase with a temporary path.
database = SQLiteDatabase()


__all__ = ["DB_PATH", "SQLiteDatabase", "database"]
