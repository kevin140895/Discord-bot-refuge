import asyncio
import copy
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from utils.persist import atomic_write_json, atomic_write_json_async


class RouletteStore:
    def __init__(self, data_dir: str):
        self.data_file = Path(data_dir) / "roulette.json"
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._write_task: asyncio.Task | None = None
        self._dirty = False
        self._load()

    def _load(self):
        try:
            with self.data_file.open("r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
            self._save()

    def _save(self):
        """Persist the current state without blocking a running event loop.

        Mutators remain synchronous so callers observe the in-memory state
        immediately. When called from asyncio, disk I/O is delegated to one
        background writer that coalesces additional mutations. Outside an
        event loop, keep the historical synchronous persistence behaviour.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                atomic_write_json(self.data_file, copy.deepcopy(self.data))
            except Exception as e:
                logging.error(
                    "[RouletteStore] Écriture échouée pour %s: %s",
                    self.data_file,
                    e,
                )
            return

        self._dirty = True
        if self._write_task is None or self._write_task.done():
            self._write_task = loop.create_task(self._flush_async())

    async def _flush_async(self) -> None:
        """Write queued snapshots in order until the state is clean."""
        try:
            while self._dirty:
                self._dirty = False
                payload = copy.deepcopy(self.data)
                try:
                    await atomic_write_json_async(self.data_file, payload)
                except Exception as e:
                    logging.error(
                        "[RouletteStore] Écriture échouée pour %s: %s",
                        self.data_file,
                        e,
                    )
        finally:
            self._write_task = None
            # A mutation can arrive after the loop observed ``_dirty`` as
            # false but before this task finishes. Ensure it is not stranded.
            if self._dirty:
                self._write_task = asyncio.get_running_loop().create_task(
                    self._flush_async()
                )

    async def flush(self) -> None:
        """Wait until all currently queued writes have been persisted."""
        while self._write_task is not None:
            task = self._write_task
            await asyncio.shield(task)

    # ——— Poster principal ———
    def set_poster(self, channel_id: str, message_id: str):
        self.data["poster"] = {
            "channel_id": channel_id,
            "message_id": message_id,
        }
        self._save()

    def get_poster(self) -> Optional[dict]:
        return self.data.get("poster")

    def clear_poster(self):
        self.data.pop("poster", None)
        self._save()

    # ——— Message d’état ———
    def set_state_message(self, channel_id: str, message_id: str):
        self.data["state_message"] = {
            "channel_id": channel_id,
            "message_id": message_id,
        }
        self._save()

    def get_state_message(self) -> Optional[dict]:
        return self.data.get("state_message")

    def clear_state_message(self):
        self.data.pop("state_message", None)
        self._save()

    # ——— Claims journaliers ———
    def mark_claimed_today(self, user_id: str, tz: str):
        now = datetime.now(ZoneInfo(tz)).date().isoformat()
        self.data.setdefault("claims", {})[user_id] = now
        self._save()

    def has_claimed_today(self, user_id: str, tz: str) -> bool:
        claims = self.data.get("claims", {})
        today = datetime.now(ZoneInfo(tz)).date().isoformat()
        return claims.get(user_id) == today

    def unmark_claimed(self, user_id: str):
        self.data.get("claims", {}).pop(user_id, None)
        self._save()

    # —— Tickets machine à sous ——
    def grant_ticket(self, user_id: str):
        tickets = self.data.setdefault("tickets", {})
        tickets[user_id] = tickets.get(user_id, 0) + 1
        self._save()

    def has_ticket(self, user_id: str) -> bool:
        return self.data.get("tickets", {}).get(user_id, 0) > 0

    def use_ticket(self, user_id: str) -> bool:
        tickets = self.data.get("tickets", {})
        count = tickets.get(user_id, 0)
        if count > 0:
            tickets[user_id] = count - 1
            if tickets[user_id] <= 0:
                tickets.pop(user_id, None)
            self._save()
            return True
        return False

    # ——— Rôles 24h ———
    def upsert_role_assignment(
        self,
        user_id: str,
        guild_id: str,
        role_id: str,
        expires_at: str,
    ):
        self.data.setdefault("role_assignments", {})[user_id] = {
            "guild_id": guild_id,
            "role_id": role_id,
            "expires_at": expires_at,
        }
        self._save()

    def get_role_assignment(self, user_id: str) -> Optional[dict]:
        return self.data.get("role_assignments", {}).get(user_id)

    def get_all_role_assignments(self) -> dict:
        return self.data.get("role_assignments", {})

    def clear_role_assignment(self, user_id: str):
        self.data.get("role_assignments", {}).pop(user_id, None)
        self._save()
