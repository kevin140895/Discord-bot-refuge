import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from utils.persist import atomic_write_json


class RouletteStore:
    def __init__(self, data_dir: str):
        self.data_file = Path(data_dir) / "roulette.json"
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        try:
            with self.data_file.open("r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
            self._save()

    def _save(self):
        try:
            atomic_write_json(self.data_file, self.data)
        except Exception as e:
            logging.error("[RouletteStore] Écriture échouée pour %s: %s", self.data_file, e)

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
            "expires_at": expires_at
        }
        self._save()

    def get_role_assignment(self, user_id: str) -> Optional[dict]:
        return self.data.get("role_assignments", {}).get(user_id)

    def get_all_role_assignments(self) -> dict:
        return self.data.get("role_assignments", {})

    def clear_role_assignment(self, user_id: str):
        self.data.get("role_assignments", {}).pop(user_id, None)
        self._save()
