import json
import logging
from pathlib import Path
from typing import Iterable, Set

from config import DATA_DIR

DATA_FILE = Path(DATA_DIR) / "temp_vc_ids.json"


def load_temp_vc_ids() -> Set[int]:
    """Charge la liste des salons temporaires persistés."""
    try:
        with DATA_FILE.open("r", encoding="utf-8") as fp:
            return set(json.load(fp))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_temp_vc_ids(ids: Iterable[int]) -> None:
    """Persiste ``ids`` vers le fichier de stockage."""
    try:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        with DATA_FILE.open("w", encoding="utf-8") as fp:
            json.dump(sorted(set(ids)), fp, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error("[temp_vc_store] Écriture échouée pour %s: %s", DATA_FILE, e)
