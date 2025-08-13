import json
from pathlib import Path
from typing import Any, Dict, Optional

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
    Squelette de persistance pour la Roulette.
    Étape 5 : on ajoutera les méthodes concrètes (claims, rôles, poster message).
    """

    def __init__(self, data_dir: str):
        base = Path(data_dir)
        self.claims_path = base / "roulette_claims.json"   # { user_id: true }
        self.roles_path  = base / "roulette_roles.json"    # { user_id: {...} }
        self.poster_path = base / "roulette_poster.json"   # { channel_id, message_id }

    # ───── Accès bas niveau (dict entier) — utiles pour l’étape 5 ─────
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

    # Étape 5 : on ajoutera ici des helpers plus pratiques (has_claimed, mark_claimed, upsert_role_assignment, etc.)
