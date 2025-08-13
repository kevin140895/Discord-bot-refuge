import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

def _safe_read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def _safe_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

class RouletteStore:
    """
    Persistance JSON pour la Roulette.

    - claims_path : { "user_id": "YYYY-MM-DD", ... }
        -> la date (Europe/Paris) du dernier tirage du joueur (donc 1 fois / jour)

    - roles_path  : {
         "user_id": {"guild_id": "...", "role_id": "...", "expires_at": "iso"},
         ...
      }

    - poster_path : {"channel_id": "...", "message_id": "..."}
    """

    def __init__(self, data_dir: str):
        base = Path(data_dir)
        self.claims_path = base / "roulette_claims.json"
        self.roles_path  = base / "roulette_roles.json"
        self.poster_path = base / "roulette_poster.json"

    # ——— low-level ———
    def read_claims(self) -> Dict[str, Any]:
        return _safe_read_json(self.claims_path)

    def write_claims(self, data: Dict[str, Any]) -> None:
        _safe_write_json(self.claims_path, data)

    def read_roles(self) -> Dict[str, Any]:
        return _safe_read_json(self.roles_path)

    def write_roles(self, data: Dict[str, Any]) -> None:
        _safe_write_json(self.roles_path, data)

    def read_poster(self) -> Dict[str, Any]:
        return _safe_read_json(self.poster_path)

    def write_poster(self, data: Dict[str, Any]) -> None:
        _safe_write_json(self.poster_path, data)

    # ——— high-level helpers ———
    # Limite : 1 tirage par jour (Europe/Paris)
    def get_last_claim_date(self, user_id: str) -> Optional[str]:
        return self.read_claims().get(user_id)

    def has_claimed_today(self, user_id: str, tz: str = "Europe/Paris") -> bool:
        last = self.get_last_claim_date(user_id)
        if not last:
            return False
        today = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
        return last == today

    def mark_claimed_today(self, user_id: str, tz: str = "Europe/Paris") -> None:
        data = self.read_claims()
        data[user_id] = datetime.now(ZoneInfo(tz)).strftime("%Y-%m-%d")
        self.write_claims(data)

    def unmark_claimed(self, user_id: str) -> None:
        data = self.read_claims()
        if user_id in data:
            data.pop(user_id)
            self.write_claims(data)

    # Rôles temporaires 24h
    def upsert_role_assignment(self, *, user_id: str, guild_id: str, role_id: str, expires_at: str) -> None:
        data = self.read_roles()
        data[user_id] = {
            "guild_id": guild_id,
            "role_id": role_id,
            "expires_at": expires_at
        }
        self.write_roles(data)

    def get_role_assignment(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self.read_roles().get(user_id)

    def clear_role_assignment(self, user_id: str) -> None:
        data = self.read_roles()
        if user_id in data:
            data.pop(user_id)
            self.write_roles(data)

    def get_all_role_assignments(self) -> Dict[str, Dict[str, Any]]:
        return self.read_roles()

    # Poster message
    def set_poster(self, *, channel_id: str, message_id: str) -> None:
        self.write_poster({"channel_id": channel_id, "message_id": message_id})

    def get_poster(self) -> Optional[Dict[str, Any]]:
        p = self.read_poster()
        if "channel_id" in p and "message_id" in p:
            return p
        return None
