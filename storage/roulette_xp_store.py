import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from utils.persist import atomic_write_json


class RouletteXPStore:
    def __init__(self, data_dir: str, filename: str = "roulette_xp") -> None:
        self.data_file = Path(data_dir) / f"{filename}.json"
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._load()

    # ----- basic persistence -----
    def _load(self) -> None:
        try:
            with self.data_file.open("r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}
            self._save()

    def _save(self) -> None:
        try:
            atomic_write_json(self.data_file, self.data)
        except Exception as e:
            logging.error("[RouletteXPStore] Écriture échouée pour %s: %s", self.data_file, e)

    # ----- helpers -----
    def _month_key(self) -> str:
        return datetime.now(ZoneInfo("Europe/Paris")).strftime("%Y-%m")

    def _month_data(self) -> dict:
        months = self.data.setdefault("months", {})
        key = self._month_key()
        return months.setdefault(
            key,
            {
                "players": {},
                "total_bet": 0,
                "house_gain": 0,
                "max_gain": {"user_id": None, "amount": 0},
                "max_loss": {"user_id": None, "amount": 0},
            },
        )

    # ----- public API -----
    def record_bet(self, amount: int) -> None:
        m = self._month_data()
        m["total_bet"] += amount
        self._save()

    def record_result(self, user_id: str, bet: int, delta: int) -> None:
        m = self._month_data()
        player = m["players"].setdefault(user_id, {"net": 0})
        player["net"] += delta
        if delta < 0:
            m["house_gain"] += -delta
            if -delta > m["max_loss"]["amount"]:
                m["max_loss"] = {"user_id": user_id, "amount": -delta}
        else:
            if delta > m["max_gain"]["amount"]:
                m["max_gain"] = {"user_id": user_id, "amount": delta}
        self._save()

    def get_month(self) -> dict:
        return self._month_data()

    def set_score_message(self, channel_id: str, message_id: str) -> None:
        self.data["score_message"] = {
            "channel_id": channel_id,
            "message_id": message_id,
        }
        self._save()

    def get_score_message(self) -> Optional[dict]:
        return self.data.get("score_message")

