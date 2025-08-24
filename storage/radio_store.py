import json
import logging
from pathlib import Path
from typing import Optional

from utils.persist import atomic_write_json


class RadioStore:
    def __init__(self, data_dir: str):
        self.data_file = Path(data_dir) / "radio.json"
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._load()

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
            logging.error("[RadioStore] Écriture échouée pour %s: %s", self.data_file, e)

    def set_radio_message(self, channel_id: str, message_id: str) -> None:
        self.data["message"] = {"channel_id": channel_id, "message_id": message_id}
        self._save()

    def get_radio_message(self) -> Optional[dict]:
        return self.data.get("message")

    def clear_radio_message(self) -> None:
        self.data.pop("message", None)
        self._save()
