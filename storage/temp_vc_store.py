import logging
from pathlib import Path
from typing import Iterable, Set

from config import DATA_DIR
from utils.persistence import atomic_write_json, read_json_safe

DATA_FILE = Path(DATA_DIR) / "temp_vc_ids.json"


def load_temp_vc_ids() -> Set[int]:
    """Charge la liste des salons temporaires persistés."""
    data = read_json_safe(DATA_FILE)
    if isinstance(data, list):
        return set(int(x) for x in data)
    return set()


def save_temp_vc_ids(ids: Iterable[int]) -> None:
    """Persiste ``ids`` vers le fichier de stockage."""
    try:
        atomic_write_json(DATA_FILE, sorted(set(int(i) for i in ids)))
    except Exception as e:
        logging.error("[temp_vc_store] Écriture échouée pour %s: %s", DATA_FILE, e)
